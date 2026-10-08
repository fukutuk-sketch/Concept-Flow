# SPDX-License-Identifier: GPL-3.0-or-later
# ConceptFlow for Blender
"""Capture and restore the light-weight scene state used by the Branch Tree.

Only cameras, lights, the world, visibility / color management and object
transforms are stored. Meshes, materials and modifiers are never touched, so
states stay small and restoring is fast. Other modules can add their own
parts of the state with :func:`register_provider`.
"""

import base64
import json
import traceback
import zlib

import bpy
from mathutils import Matrix

from .common import view_layer_of

STATE_VERSION = 1
ND = 5  # decimals kept for floats

_providers = []  # (order, key, capture(scene) -> data, restore(scene, data, report))


class RestoreReport:
    def __init__(self):
        self.missing = set()
        self.errors = 0


def register_provider(key, capture_fn, restore_fn, order=50):
    unregister_provider(key)
    _providers.append((order, key, capture_fn, restore_fn))
    _providers.sort(key=lambda p: p[0])


def unregister_provider(key):
    _providers[:] = [p for p in _providers if p[1] != key]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def capture(scene, options=None):
    """Return the current state of ``scene`` as plain JSON-able data."""
    options = options or {}
    state = {"v": STATE_VERSION}
    for _order, key, cap, _res in _providers:
        if options.get(key, True) is False:
            continue
        try:
            state[key] = cap(scene)
        except Exception:
            traceback.print_exc()
    return state


def restore(scene, state):
    """Apply a captured state to ``scene``. Missing objects are skipped."""
    report = RestoreReport()
    for _order, key, _cap, res in _providers:
        if key not in state:
            continue
        try:
            res(scene, state[key], report)
        except Exception:
            traceback.print_exc()
            report.errors += 1
    return report


def pack(state):
    raw = json.dumps(state, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return "z1:" + base64.b64encode(zlib.compress(raw, 6)).decode("ascii")


def unpack(text):
    if not text:
        return {}
    try:
        if text.startswith("z1:"):
            raw = zlib.decompress(base64.b64decode(text[3:].encode("ascii")))
            return json.loads(raw.decode("utf-8"))
        return json.loads(text)
    except Exception:
        traceback.print_exc()
        return {}


# Object tables where objects that exist on one side only are ignored when comparing:
# new objects are not part of a saved state, and deleted ones cannot be restored.
_LENIENT = {("camera", "objs"), ("view", "objs"), ("view", "colls"), ("xforms",)}


def _light_is_off(entry):
    vis = entry.get("vis", [False, False, False]) if isinstance(entry, dict) else []
    return len(vis) == 3 and vis[1] and vis[2]


def equal(a, b, tol=1e-4, _path=()):
    """Compare a present state ``a`` with a saved state ``b``, ignoring tiny float
    differences.

    Only the parts both states contain are compared (a state saved without
    transforms still matches). A light added after ``b`` was saved counts as a
    change only while it is visible, because restoring ``b`` hides it.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        if _path and _path not in _LENIENT:
            extra_a = a.keys() - b.keys()
            if _path == ("lights", "objs"):
                # Lights deleted since ``b`` cannot come back, so only new ones matter.
                if any(not _light_is_off(a[k]) for k in extra_a):
                    return False
            elif extra_a or (b.keys() - a.keys()):
                return False
        return all(equal(a[k], b[k], tol, _path + (k,) if len(_path) < 2 else _path + ("*",))
                   for k in a.keys() & b.keys())
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x, y, tol, _path + ("*",)) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= tol
    return a == b


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _round(v):
    return round(v, ND) if isinstance(v, float) else v


def mat_to_list(m):
    return [round(x, ND) for row in m for x in row]


def list_to_mat(values):
    return Matrix((values[0:4], values[4:8], values[8:12], values[12:16]))


def _same_matrix(m, values, tol=1e-5):
    flat = [x for row in m for x in row]
    return all(abs(a - b) <= tol for a, b in zip(flat, values))


_SKIP = {
    "rna_type", "name", "name_full", "is_evaluated", "original", "users", "use_fake_user",
    "use_extra_user", "is_embedded_data", "is_missing", "is_runtime_data", "library",
    "library_weak_reference", "override_library", "preview", "session_uid", "tag",
    "is_library_indirect", "asset_data", "id_type", "is_editmode", "animation_data",
    "cycles", "node_tree", "stereo", "background_images",
}
_CAMERA_SKIP = frozenset({"angle", "angle_x", "angle_y"})
_LIGHT_SKIP = frozenset({"type"})  # restored first, separately


def rna_dump(struct, skip=frozenset(), depth=0):
    """Dump the simple editable RNA properties of ``struct`` (one level of sub-structs)."""
    out = {}
    for prop in struct.bl_rna.properties:
        pid = prop.identifier
        if pid in _SKIP or pid in skip:
            continue
        try:
            if prop.type in {'BOOLEAN', 'INT', 'FLOAT', 'ENUM'}:
                if prop.is_readonly:
                    continue
                value = getattr(struct, pid)
                if prop.type == 'ENUM':
                    out[pid] = sorted(value) if prop.is_enum_flag else value
                elif getattr(prop, "array_length", 0):
                    dims = [d for d in getattr(prop, "array_dimensions", (prop.array_length,)) if d]
                    if len(dims) > 1:
                        continue
                    out[pid] = [_round(x) for x in value]
                else:
                    out[pid] = _round(value)
            elif prop.type == 'POINTER':
                value = getattr(struct, pid)
                if isinstance(value, bpy.types.ID) or (value is None and not prop.is_readonly):
                    if not prop.is_readonly and prop.fixed_type.identifier == "Object":
                        out[pid] = {"__ob__": value.name if value else None}
                elif value is not None and depth == 0:
                    sub = rna_dump(value, skip, depth + 1)
                    if sub:
                        out[pid] = sub
        except Exception:
            continue
    return out


def rna_load(struct, data, depth=0):
    """Apply data produced by :func:`rna_dump`. Unknown or invalid values are ignored."""
    props = struct.bl_rna.properties
    for pid, value in data.items():
        prop = props.get(pid)
        if prop is None:
            continue
        try:
            if prop.type == 'POINTER':
                if isinstance(value, dict) and "__ob__" in value:
                    name = value["__ob__"]
                    setattr(struct, pid, bpy.data.objects.get(name) if name else None)
                elif isinstance(value, dict) and depth == 0:
                    sub = getattr(struct, pid)
                    if sub is not None:
                        rna_load(sub, value, depth + 1)
                continue
            if prop.type == 'ENUM' and prop.is_enum_flag:
                value = set(value)
            current = getattr(struct, pid)
            if isinstance(value, list):
                if all(abs(a - b) <= 1e-6 for a, b in zip(current, value)):
                    continue
            elif isinstance(value, float) and isinstance(current, float):
                if abs(current - value) <= 1e-6:
                    continue
            elif current == value:
                continue
            setattr(struct, pid, value)
        except Exception:
            continue


# ---------------------------------------------------------------------------
# Built-in providers
# ---------------------------------------------------------------------------

def _set_basis(ob, values):
    if ob.library is not None:
        return
    if not _same_matrix(ob.matrix_basis, values):
        ob.matrix_basis = list_to_mat(values)


def _capture_camera(scene):
    objs = {}
    for ob in scene.objects:
        if ob.type == 'CAMERA':
            objs[ob.name] = {"m": mat_to_list(ob.matrix_basis), "d": rna_dump(ob.data, _CAMERA_SKIP)}
    return {"active": scene.camera.name if scene.camera else None, "objs": objs}


def _restore_camera(scene, data, report):
    for name, entry in data.get("objs", {}).items():
        ob = scene.objects.get(name)
        if ob is None or ob.type != 'CAMERA':
            report.missing.add(name)
            continue
        _set_basis(ob, entry["m"])
        if ob.data.library is None:
            rna_load(ob.data, entry.get("d", {}))
    active = data.get("active")
    if active:
        ob = scene.objects.get(active)
        if ob is not None and ob.type == 'CAMERA' and scene.camera != ob:
            scene.camera = ob


def _vis_get(ob, view_layer):
    try:
        hidden = ob.hide_get(view_layer=view_layer)
    except RuntimeError:
        hidden = False
    return [bool(ob.hide_viewport), bool(ob.hide_render), bool(hidden)]


def _vis_set(ob, values, view_layer):
    hv, hr, hs = values
    if ob.hide_viewport != hv:
        ob.hide_viewport = hv
    if ob.hide_render != hr:
        ob.hide_render = hr
    try:
        if ob.hide_get(view_layer=view_layer) != hs:
            ob.hide_set(hs, view_layer=view_layer)
    except RuntimeError:
        pass


def _capture_lights(scene):
    vl = view_layer_of(scene)
    objs = {}
    for ob in scene.objects:
        if ob.type == 'LIGHT':
            light = ob.data
            objs[ob.name] = {
                "m": mat_to_list(ob.matrix_basis),
                "t": light.type,
                "d": rna_dump(light, _LIGHT_SKIP),
                "vis": _vis_get(ob, vl),
            }
    return {"objs": objs}


def _restore_lights(scene, data, report):
    vl = view_layer_of(scene)
    objs = data.get("objs", {})
    for name, entry in objs.items():
        ob = scene.objects.get(name)
        if ob is None or ob.type != 'LIGHT':
            report.missing.add(name)
            continue
        _set_basis(ob, entry["m"])
        if ob.data.library is None:
            if ob.data.type != entry.get("t", ob.data.type):
                ob.data.type = entry["t"]
            rna_load(ob.data, entry.get("d", {}))  # re-read ob.data: the type may have changed
        _vis_set(ob, entry.get("vis", [False, False, False]), vl)
    # Lights added after this state was saved are hidden (never deleted).
    for ob in scene.objects:
        if ob.type == 'LIGHT' and ob.name not in objs:
            _vis_set(ob, [ob.hide_viewport, True, True], vl)


def _dump_nodes(node_tree):
    out = {}
    for node in node_tree.nodes:
        entry = {}
        inputs = {}
        for sock in node.inputs:
            if sock.is_linked or not hasattr(sock, "default_value"):
                continue
            value = sock.default_value
            if isinstance(value, str):
                continue
            try:
                value = [_round(float(x)) for x in value]
            except TypeError:
                value = _round(value)
            inputs[sock.identifier] = value
        if inputs:
            entry["i"] = inputs
        image = getattr(node, "image", None)
        if image is not None:
            entry["img"] = image.name
        if node.mute:
            entry["mute"] = True
        if entry:
            out[node.name] = entry
    return out


def _load_nodes(node_tree, data):
    for node_name, entry in data.items():
        node = node_tree.nodes.get(node_name)
        if node is None:
            continue
        for ident, value in entry.get("i", {}).items():
            sock = next((s for s in node.inputs if s.identifier == ident), None)
            if sock is None or sock.is_linked or not hasattr(sock, "default_value"):
                continue
            try:
                sock.default_value = value
            except Exception:
                pass
        if "img" in entry and hasattr(node, "image"):
            image = bpy.data.images.get(entry["img"])
            if image is not None and node.image != image:
                node.image = image
        mute = entry.get("mute", False)
        if node.mute != mute:
            node.mute = mute


def _capture_world(scene):
    world = scene.world
    if world is None:
        return {"name": None}
    data = {"name": world.name, "color": [_round(c) for c in world.color]}
    if world.node_tree is not None:
        data["nodes"] = _dump_nodes(world.node_tree)
    return data


def _restore_world(scene, data, report):
    name = data.get("name")
    if name is None:
        if scene.world is not None:
            scene.world = None
        return
    world = bpy.data.worlds.get(name)
    if world is None:
        report.missing.add(name)
        return
    if scene.world != world:
        scene.world = world
    if world.library is not None:
        return
    if "color" in data:
        world.color = data["color"]
    if world.node_tree is not None and "nodes" in data:
        _load_nodes(world.node_tree, data["nodes"])


def _walk_layer_collections(layer_coll):
    yield layer_coll
    for child in layer_coll.children:
        yield from _walk_layer_collections(child)


def _capture_view(scene):
    vl = view_layer_of(scene)
    vs = scene.view_settings
    data = {
        "cm": {"view_transform": vs.view_transform, "look": vs.look,
               "exposure": _round(vs.exposure), "gamma": _round(vs.gamma)},
        "objs": {},
        "colls": {},
    }
    for ob in scene.objects:
        if ob.type in {'LIGHT', 'CAMERA'}:
            continue
        data["objs"][ob.name] = _vis_get(ob, vl)
    if vl is not None:
        for lc in _walk_layer_collections(vl.layer_collection):
            coll = lc.collection
            if coll == scene.collection:
                continue
            data["colls"][coll.name] = [bool(lc.exclude), bool(lc.hide_viewport),
                                        bool(coll.hide_viewport), bool(coll.hide_render)]
    return data


def _restore_view(scene, data, report):
    vl = view_layer_of(scene)
    cm = data.get("cm", {})
    vs = scene.view_settings
    for key in ("view_transform", "look", "exposure", "gamma"):
        if key in cm:
            try:
                if getattr(vs, key) != cm[key]:
                    setattr(vs, key, cm[key])
            except Exception:
                pass
    if vl is not None:
        colls = data.get("colls", {})
        for lc in _walk_layer_collections(vl.layer_collection):
            entry = colls.get(lc.collection.name)
            if entry is None or lc.collection == scene.collection:
                continue
            exclude, lhide, chv, chr_ = entry
            try:
                if lc.exclude != exclude:
                    lc.exclude = exclude
                if not lc.exclude and lc.hide_viewport != lhide:
                    lc.hide_viewport = lhide
                coll = lc.collection
                if coll.library is None:
                    if coll.hide_viewport != chv:
                        coll.hide_viewport = chv
                    if coll.hide_render != chr_:
                        coll.hide_render = chr_
            except Exception:
                pass
    for name, values in data.get("objs", {}).items():
        ob = scene.objects.get(name)
        if ob is None:
            continue  # objects deleted on purpose are not reported here
        _vis_set(ob, values, vl)


def _capture_xforms(scene):
    return {ob.name: mat_to_list(ob.matrix_basis)
            for ob in scene.objects if ob.type not in {'LIGHT', 'CAMERA'}}


def _restore_xforms(scene, data, report):
    # Objects deleted since the state was saved (for example walls split by an
    # opening) are skipped silently; only cameras, lights and worlds are reported.
    for name, values in data.items():
        ob = scene.objects.get(name)
        if ob is not None:
            _set_basis(ob, values)


def register():
    register_provider("world", _capture_world, _restore_world, order=10)
    register_provider("camera", _capture_camera, _restore_camera, order=20)
    register_provider("lights", _capture_lights, _restore_lights, order=30)
    register_provider("view", _capture_view, _restore_view, order=40)
    register_provider("xforms", _capture_xforms, _restore_xforms, order=60)


def unregister():
    for key in ("world", "camera", "lights", "view", "xforms"):
        unregister_provider(key)
