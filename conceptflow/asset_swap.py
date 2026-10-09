# SPDX-License-Identifier: GPL-3.0-or-later
# ConceptFlow for Blender
"""Asset Swap: replace graybox parts with real assets, one button.

* Assets are registered per part type as a collection. Each top-level object
  (or each child collection) in it is one candidate.
* A candidate is picked per part with a seed, fitted to the part's box
  (stretch, repeat along the length, or keep proportions) and added as
  instances: linked duplicates that share mesh data, or collection instances.
* The instances are parented to their graybox part, so moving a graybox part
  moves its final asset too. Graybox / Final display can be switched any time.
"""

import math
import random
import zlib

import bpy
from bpy.props import EnumProperty
from bpy.types import Operator
from mathutils import Matrix, Vector

from . import common
from . import state as cf_state
from .common import guarded, rpt
from .props import PART_ITEMS

SWAP_PARTS = [item[0] for item in PART_ITEMS if item[0] != 'NONE']
AUTO_FIT = {'WALL': 'TILE', 'FLOOR': 'TILE', 'BLOCK': 'UNIFORM'}
MAX_TILES = 400
_GEOMETRY_TYPES = {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META', 'CURVES', 'POINTCLOUD', 'VOLUME',
                   'GREASEPENCIL'}


def slot_of(scene, part):
    return getattr(scene.conceptflow.swap, part.lower())


def final_collection(scene):
    return common.ensure_child_collection(scene.collection, "CF_Final", "FINAL")


def is_final(ob):
    return ob is not None and bool(ob.get("cf_final"))


def _in_asset_collection(ob):
    return any(str(c.get("cf_role", "")).startswith("ASSETS") for c in ob.users_collection)


def swappable_parts(scene):
    return [ob for ob in scene.objects
            if ob.type == 'MESH' and ob.conceptflow.part in SWAP_PARTS and not is_final(ob)
            and not _in_asset_collection(ob)]


def finals_map(scene, orphans=None):
    """{graybox part: [final objects]} built in one pass over the scene.

    Finals whose graybox part was deleted are appended to ``orphans`` when it is given.
    """
    result = {}
    for ob in scene.objects:
        if not is_final(ob):
            continue
        root = ob
        while root is not None and is_final(root):
            root = root.parent
        if root is not None:
            result.setdefault(root, []).append(ob)
        elif orphans is not None:
            orphans.append(ob)
    return result


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

def _basis_world(ob, cache):
    """World matrix from the parent chain (works for objects never evaluated)."""
    key = ob.name
    if key in cache:
        return cache[key]
    m = ob.matrix_parent_inverse @ ob.matrix_basis if ob.parent is not None else ob.matrix_basis.copy()
    if ob.parent is not None:
        m = _basis_world(ob.parent, cache) @ m
    cache[key] = m
    return m


def _corners(ob):
    pts = [Vector(c) for c in ob.bound_box]
    if ob.type == 'MESH' and (max(p.x for p in pts) - min(p.x for p in pts) < 1e-9) \
            and (max(p.z for p in pts) - min(p.z for p in pts) < 1e-9) and len(ob.data.vertices):
        xs = [v.co for v in ob.data.vertices]
        mn = Vector((min(v.x for v in xs), min(v.y for v in xs), min(v.z for v in xs)))
        mx = Vector((max(v.x for v in xs), max(v.y for v in xs), max(v.z for v in xs)))
        pts = [Vector((x, y, z)) for x in (mn.x, mx.x) for y in (mn.y, mx.y) for z in (mn.z, mx.z)]
    return pts


def _bounds(points):
    if not points:
        return Vector((-0.5, -0.5, 0.0)), Vector((0.5, 0.5, 1.0))
    mn = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    mx = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return mn, mx


class Candidate:
    """One asset choice: an object hierarchy or a collection."""

    def __init__(self, kind, ref, objects, mn, mx):
        self.kind = kind          # 'OBJECT' or 'COLLECTION'
        self.ref = ref
        self.objects = objects    # OBJECT: [(object, parent index)], parents first
        self.mn, self.mx = mn, mx


def candidates_for(slot):
    coll = slot.source
    if coll is None:
        return []
    cache = {}
    result = []
    for child in coll.children:
        objs = [o for o in child.all_objects if o.type in _GEOMETRY_TYPES or o.instance_type == 'COLLECTION']
        if not objs:
            continue
        offset = Vector(child.instance_offset)
        pts = []
        for o in objs:
            m = _basis_world(o, cache)
            pts += [m @ c - offset for c in _corners(o)]
        mn, mx = _bounds(pts)
        result.append(Candidate('COLLECTION', child, [], mn, mx))
    in_coll = set(coll.objects)
    children = {}
    for o in coll.objects:
        if o.parent is not None and o.parent in in_coll:
            children.setdefault(o.parent, []).append(o)
    for root in coll.objects:
        if root.parent is not None and root.parent in in_coll:
            continue
        ordered = [(root, -1)]
        i = 0
        while i < len(ordered):
            for kid in children.get(ordered[i][0], []):
                ordered.append((kid, i))
            i += 1
        root_inv = _basis_world(root, cache).inverted_safe()
        pts = []
        for o, _p in ordered:
            if o.type in _GEOMETRY_TYPES:
                m = root_inv @ _basis_world(o, cache)
                pts += [m @ c for c in _corners(o)]
        if not pts:
            continue
        mn, mx = _bounds(pts)
        result.append(Candidate('OBJECT', root, ordered, mn, mx))
    return result


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------

def fit_mode(slot, part):
    return slot.fit if slot.fit != 'AUTO' else AUTO_FIT.get(part, 'STRETCH')


def fit_matrices(part_mn, part_mx, cand, mode, rotate_deg, part):
    """Matrices (in the part's local space) for each instance of ``cand``."""
    rot = Matrix.Rotation(math.radians(rotate_deg), 4, 'Z')
    pts = [rot @ Vector((x, y, z)) for x in (cand.mn.x, cand.mx.x)
           for y in (cand.mn.y, cand.mx.y) for z in (cand.mn.z, cand.mx.z)]
    r_mn, r_mx = _bounds(pts)
    size = Vector((max(r_mx.x - r_mn.x, 1e-6), max(r_mx.y - r_mn.y, 1e-6), max(r_mx.z - r_mn.z, 1e-6)))
    box = Vector((max(part_mx.x - part_mn.x, 1e-4), max(part_mx.y - part_mn.y, 1e-4),
                  max(part_mx.z - part_mn.z, 1e-4)))
    if mode == 'UNIFORM':
        k = min(box.x / size.x, box.y / size.y, box.z / size.z)
        base = Vector(((part_mn.x + part_mx.x) / 2.0, (part_mn.y + part_mx.y) / 2.0, part_mn.z))
        a_base = Vector(((r_mn.x + r_mx.x) / 2.0, (r_mn.y + r_mx.y) / 2.0, r_mn.z))
        return [Matrix.Translation(base) @ Matrix.Diagonal((k, k, k, 1.0))
                @ Matrix.Translation(-a_base) @ rot]
    nx = ny = 1
    if mode == 'TILE':
        nx = max(1, round(box.x / size.x))
        if part == 'FLOOR':
            ny = max(1, round(box.y / size.y))
        while nx * ny > MAX_TILES:  # keep huge floors light: use bigger tiles
            if nx >= ny:
                nx = max(1, nx // 2)
            else:
                ny = max(1, ny // 2)
    sx, sy, sz = box.x / (nx * size.x), box.y / (ny * size.y), box.z / size.z
    scale = Matrix.Diagonal((sx, sy, sz, 1.0))
    tail = scale @ Matrix.Translation(-r_mn) @ rot
    mats = []
    for i in range(nx):
        for j in range(ny):
            origin = Vector((part_mn.x + i * box.x / nx, part_mn.y + j * box.y / ny, part_mn.z))
            mats.append(Matrix.Translation(origin) @ tail)
    return mats


def _instantiate(cand, collection):
    """Create one instance of ``cand``. Returns (root, all new objects)."""
    if cand.kind == 'COLLECTION':
        empty = bpy.data.objects.new("CF_Final_" + cand.ref.name, None)
        empty.instance_type = 'COLLECTION'
        empty.instance_collection = cand.ref
        empty.empty_display_size = 0.25
        empty["cf_final"] = True
        collection.objects.link(empty)
        return empty, [empty]
    made = []
    for src, parent_index in cand.objects:
        new = src.copy()  # linked duplicate: mesh data is shared
        new["cf_final"] = True
        if hasattr(new, "conceptflow"):
            new.conceptflow.part = 'NONE'
        new.hide_viewport = False
        new.hide_render = False
        new.hide_select = False
        collection.objects.link(new)
        if parent_index >= 0:
            new.parent = made[parent_index]
        made.append(new)
    return made[0], made


# ---------------------------------------------------------------------------
# Swap / clear / display
# ---------------------------------------------------------------------------

def _remove_finals(objects):
    for ob in objects:
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
        except ReferenceError:
            pass


def _set_visible(ob, visible, view_layer):
    if ob.hide_render == visible:
        ob.hide_render = not visible
    try:
        if ob.hide_get(view_layer=view_layer) == visible:
            ob.hide_set(not visible, view_layer=view_layer)
    except RuntimeError:
        pass


def swap_parts(scene, parts):
    """Swap ``parts`` for assets. Returns (swapped, parts without assets, instances)."""
    swap = scene.conceptflow.swap
    coll = final_collection(scene)
    existing = finals_map(scene)
    cands_by_part = {}
    swapped = missing = instances = 0
    for part_ob in parts:
        part = part_ob.conceptflow.part
        if part not in cands_by_part:
            cands_by_part[part] = candidates_for(slot_of(scene, part))
        cands = cands_by_part[part]
        _remove_finals(existing.pop(part_ob, []))
        if not cands:
            missing += 1
            continue
        slot = slot_of(scene, part)
        rng = random.Random(zlib.crc32("{}:{}".format(swap.seed, part_ob.name).encode("utf-8")))
        cand = cands[rng.randrange(len(cands))]
        p_mn, p_mx = _bounds([Vector(c) for c in part_ob.bound_box])
        for matrix in fit_matrices(p_mn, p_mx, cand, fit_mode(slot, part), int(slot.rotate), part):
            root, made = _instantiate(cand, coll)
            root.parent = part_ob
            root.matrix_parent_inverse = Matrix.Identity(4)
            root.matrix_basis = matrix
            instances += len(made)
        swapped += 1
    return swapped, missing, instances


def clear_parts(scene, parts, include_orphans=False):
    view_layer = common.view_layer_of(scene)
    orphans = [] if include_orphans else None
    existing = finals_map(scene, orphans)
    if orphans:
        _remove_finals(orphans)  # the graybox part they belonged to is gone
    cleared = 0
    for part_ob in parts:
        finals = existing.get(part_ob)
        if finals:
            _remove_finals(finals)
            cleared += 1
        _set_visible(part_ob, True, view_layer)
    return cleared


def apply_display(scene, mode):
    view_layer = common.view_layer_of(scene)
    show_graybox = mode == 'GRAYBOX'
    for part_ob, finals in finals_map(scene).items():
        _set_visible(part_ob, show_graybox, view_layer)
        for ob in finals:
            _set_visible(ob, not show_graybox, view_layer)


def on_display_changed(swap, context):
    scene = swap.id_data
    if isinstance(scene, bpy.types.Scene):
        try:
            apply_display(scene, swap.display)
        except Exception:
            import traceback
            traceback.print_exc()


def resolve_parts(context, scope):
    scene = context.scene
    if scope == 'ALL':
        return swappable_parts(scene)
    parts = []
    for ob in context.selected_objects:
        root = ob
        while root is not None and is_final(root):
            root = root.parent
        if root is not None and root.type == 'MESH' and root.conceptflow.part in SWAP_PARTS \
                and root not in parts:
            parts.append(root)
    return parts


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

SCOPE_ITEMS = [
    ('ALL', "All", "Every graybox part in the scene"),
    ('SELECTED', "Selected", "Only the selected parts (or the parts of the selected assets)"),
]


class CONCEPTFLOW_OT_swap_assets(Operator):
    bl_idname = "conceptflow.swap_assets"
    bl_label = "Swap to Assets"
    bl_description = ("Replace graybox parts with the assets registered for their type. "
                      "The graybox stays, so you can switch back at any time")
    bl_options = {'REGISTER', 'UNDO'}

    scope: EnumProperty(name="Scope", items=SCOPE_ITEMS, default='ALL')

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.mode == 'OBJECT'

    @guarded
    def execute(self, context):
        scene = context.scene
        parts = resolve_parts(context, self.scope)
        if not parts:
            self.report({'WARNING'}, rpt("No graybox parts to swap"))
            return {'CANCELLED'}
        swapped, missing, instances = swap_parts(scene, parts)
        if swapped == 0:
            self.report({'WARNING'}, rpt("No assets registered for these part types yet"))
            return {'CANCELLED'}
        swap = scene.conceptflow.swap
        if swap.display != 'FINAL':
            swap.display = 'FINAL'  # update callback hides the graybox
        else:
            apply_display(scene, 'FINAL')
        msg = rpt("Swapped {count} parts ({instances} instances)").format(count=swapped, instances=instances)
        if missing:
            msg += " " + rpt("{count} parts have no assets and stay graybox").format(count=missing)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class CONCEPTFLOW_OT_swap_shuffle(Operator):
    bl_idname = "conceptflow.swap_shuffle"
    bl_label = "Shuffle"
    bl_description = "Pick other candidates: change the seed and swap the swapped parts again"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.mode == 'OBJECT'

    @guarded
    def execute(self, context):
        scene = context.scene
        parts = list(finals_map(scene).keys())
        if not parts:
            self.report({'WARNING'}, rpt("Nothing has been swapped yet"))
            return {'CANCELLED'}
        scene.conceptflow.swap.seed += 1
        swap_parts(scene, parts)
        apply_display(scene, scene.conceptflow.swap.display)
        self.report({'INFO'}, rpt("Seed {seed}").format(seed=scene.conceptflow.swap.seed))
        return {'FINISHED'}


class CONCEPTFLOW_OT_swap_clear(Operator):
    bl_idname = "conceptflow.swap_clear"
    bl_label = "Clear Swapped Assets"
    bl_description = "Delete the swapped assets and show the graybox again"
    bl_options = {'REGISTER', 'UNDO'}

    scope: EnumProperty(name="Scope", items=SCOPE_ITEMS, default='ALL')

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.mode == 'OBJECT'

    @guarded
    def execute(self, context):
        scene = context.scene
        if self.scope == 'ALL':
            parts = list(finals_map(scene).keys())
        else:
            parts = resolve_parts(context, 'SELECTED')
        cleared = clear_parts(scene, parts, include_orphans=(self.scope == 'ALL'))
        if self.scope == 'ALL':
            swap = scene.conceptflow.swap
            if swap.display != 'GRAYBOX':
                swap.display = 'GRAYBOX'
        self.report({'INFO'}, rpt("Cleared {count} parts").format(count=cleared))
        return {'FINISHED'}


class CONCEPTFLOW_OT_swap_register(Operator):
    bl_idname = "conceptflow.swap_register"
    bl_label = "Register Selected as Assets"
    bl_description = ("Move the selected objects (with their children) into a hidden asset collection "
                      "for this part type and use it for swapping")
    bl_options = {'REGISTER', 'UNDO'}

    part: EnumProperty(name="Part Type", items=[i for i in PART_ITEMS if i[0] != 'NONE'], default='WALL')

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.mode == 'OBJECT' and bool(context.selected_objects)

    @guarded
    def execute(self, context):
        scene = context.scene
        roots = [o for o in context.selected_objects if o.parent not in context.selected_objects]
        roots = [o for o in roots if not is_final(o)]
        if not roots:
            self.report({'WARNING'}, rpt("Select the asset objects first"))
            return {'CANCELLED'}
        root_coll = common.ensure_child_collection(scene.collection, "CF_Assets", "ASSETS")
        name = "CF_Assets_" + self.part.title()
        coll = common.ensure_child_collection(root_coll, name, "ASSETS_" + self.part)
        moved = 0
        for root in roots:
            for ob in [root, *root.children_recursive]:
                for other in list(ob.users_collection):
                    if other != coll:
                        other.objects.unlink(ob)
                if coll not in ob.users_collection:
                    coll.objects.link(ob)
                if ob.type == 'MESH':
                    ob.conceptflow.part = 'NONE'
                moved += 1
        for view_layer in scene.view_layers:
            lc = _find_layer_collection(view_layer.layer_collection, root_coll)
            if lc is not None and not lc.exclude:
                lc.exclude = True
        slot = slot_of(scene, self.part)
        slot.source = coll
        self.report({'INFO'}, rpt("Registered {count} objects as {part} assets").format(
            count=moved, part=self.part.title()))
        return {'FINISHED'}


def _find_layer_collection(layer_coll, coll):
    if layer_coll.collection == coll:
        return layer_coll
    for child in layer_coll.children:
        found = _find_layer_collection(child, coll)
        if found is not None:
            return found
    return None


# ---------------------------------------------------------------------------
# Branch Tree integration
# ---------------------------------------------------------------------------

def _capture(scene):
    swap = scene.conceptflow.swap
    return {"display": swap.display, "seed": swap.seed}


def _restore(scene, data, report):
    swap = scene.conceptflow.swap
    if swap.seed != data.get("seed", swap.seed):
        swap.seed = data.get("seed", swap.seed)
    display = data.get("display", swap.display)
    if swap.display != display:
        swap.display = display  # update callback applies it
    else:
        apply_display(scene, display)


classes = (
    CONCEPTFLOW_OT_swap_assets,
    CONCEPTFLOW_OT_swap_shuffle,
    CONCEPTFLOW_OT_swap_clear,
    CONCEPTFLOW_OT_swap_register,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    cf_state.register_provider("swap", _capture, _restore, order=95)


def unregister():
    cf_state.unregister_provider("swap")
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
