# SPDX-License-Identifier: GPL-3.0-or-later
# ConceptFlow for Blender
"""Look Presets: one-click, non-destructive lighting moods.

* The look's lights live in their own collection (CF_Look / CF_Look_<PRESET>).
* The world is duplicated once per look (CF_World_<PRESET>) and swapped in;
  the original world is never edited.
* Your own lights are kept, dimmed or hidden, and come back on Reset.
* Lights and worlds of a look are kept (hidden) after Reset so that Branch Tree
  states that used them can still be restored. "Remove Look Data" deletes them.
"""

import json
import math

import bpy
from bpy.props import EnumProperty
from bpy.types import Operator
from mathutils import Matrix, Vector

from . import branch_tree, common
from . import state as cf_state
from .common import guarded, rpt, tip
from .props import LOOK_ITEMS

LOOK_ORDER = ('HORROR', 'NOIR', 'MOONLIGHT', 'GOLDEN_HOUR', 'NEON')
LOOK_ICONS = {
    'HORROR': 'GHOST_ENABLED',
    'NOIR': 'IMAGE_ZDEPTH',
    'MOONLIGHT': 'LIGHT_HEMI',
    'GOLDEN_HOUR': 'LIGHT_SUN',
    'NEON': 'LIGHT_POINT',
}
DIM_FACTOR = 0.15

# Light fields:
#   role, type, color, bright: light at the target (sun: strength)
#   pos: (forward, right, up) from the target, in units of the camera-target distance
#        (negative forward = towards the camera)
#   az, el: sun direction in degrees; az 0 = from the camera side, 180 = from behind the subject
#   size: radius in metres (point / spot), side length (area) or angle in degrees (sun)
#   A sun cannot reach into a closed room, so every look that uses one also has a
#   soft "window" area light on the camera side: the mood works indoors and outdoors.
#   spot, blend: spot cone angle in degrees and edge softness
PRESETS = {
    'HORROR': {
        'sky': ((0.004, 0.007, 0.006), (0.012, 0.020, 0.017), (0.002, 0.002, 0.002)),
        'fog': ((0.55, 0.68, 0.62), 0.035, 0.3),
        'cm': ('AgX', 'AgX - High Contrast', -0.4),
        'lights': (
            dict(role="moon", type='SUN', color=(0.5, 0.75, 0.68), bright=0.3, az=160, el=40, size=2.0),
            dict(role="window", type='AREA', color=(0.40, 0.78, 0.62), bright=0.9, pos=(-0.35, -0.3, 0.28),
                 size=0.7),
            dict(role="under", type='POINT', color=(1.0, 0.4, 0.15), bright=1.2, pos=(-0.3, 0.12, -0.12),
                 size=0.05),
            dict(role="rim", type='SPOT', color=(0.35, 0.7, 0.58), bright=7.0, pos=(0.6, -0.45, 0.35),
                 size=0.1, spot=40, blend=0.5),
        ),
    },
    'NOIR': {
        'sky': ((0.003, 0.003, 0.003), (0.006, 0.006, 0.006), (0.001, 0.001, 0.001)),
        'fog': ((0.9, 0.9, 0.9), 0.02, 0.5),
        'cm': ('AgX', 'AgX - Greyscale', 0.0),
        'lights': (
            dict(role="key", type='SPOT', color=(1.0, 0.97, 0.92), bright=14.0, pos=(-0.15, -0.55, 0.55),
                 size=0.02, spot=36, blend=0.25),
            dict(role="rim", type='SPOT', color=(0.85, 0.9, 1.0), bright=6.0, pos=(0.7, 0.45, 0.4),
                 size=0.05, spot=30, blend=0.3),
            dict(role="fill", type='POINT', color=(0.9, 0.92, 1.0), bright=0.35, pos=(-0.55, 0.0, 0.12),
                 size=0.3),
        ),
    },
    'MOONLIGHT': {
        'sky': ((0.004, 0.010, 0.032), (0.016, 0.034, 0.075), (0.002, 0.003, 0.005)),
        'fog': ((0.5, 0.62, 0.85), 0.012, 0.2),
        'cm': ('AgX', 'AgX - Base Contrast', 0.2),
        'lights': (
            dict(role="moon", type='SUN', color=(0.6, 0.72, 1.0), bright=0.9, az=120, el=32, size=1.0),
            dict(role="window", type='AREA', color=(0.5, 0.65, 1.0), bright=1.5, pos=(-0.3, 0.3, 0.26),
                 size=0.7),
            dict(role="practical", type='POINT', color=(1.0, 0.55, 0.25), bright=1.0, pos=(0.1, 0.3, 0.05),
                 size=0.08),
        ),
    },
    'GOLDEN_HOUR': {
        'sky': ((0.16, 0.26, 0.5), (1.0, 0.55, 0.28), (0.06, 0.045, 0.04)),
        'fog': ((1.0, 0.78, 0.55), 0.005, 0.5),
        'cm': ('AgX', 'AgX - Punchy', 0.3),
        'lights': (
            dict(role="sun", type='SUN', color=(1.0, 0.58, 0.3), bright=4.0, az=150, el=9, size=1.5),
            dict(role="window", type='AREA', color=(1.0, 0.64, 0.34), bright=4.0, pos=(-0.3, 0.32, 0.24),
                 size=1.0),
        ),
    },
    'NEON': {
        'sky': ((0.004, 0.002, 0.010), (0.025, 0.010, 0.040), (0.002, 0.002, 0.003)),
        'fog': ((0.8, 0.75, 1.0), 0.025, 0.3),
        'cm': ('AgX', 'AgX - Punchy', 0.3),
        'lights': (
            dict(role="magenta", type='POINT', color=(1.0, 0.08, 0.55), bright=5.0, pos=(-0.15, -0.4, 0.12),
                 size=0.15),
            dict(role="cyan", type='POINT', color=(0.05, 0.75, 1.0), bright=5.0, pos=(0.05, 0.42, 0.18),
                 size=0.15),
            dict(role="back", type='SPOT', color=(0.55, 0.25, 1.0), bright=6.0, pos=(0.65, 0.1, 0.55),
                 size=0.2, spot=60, blend=0.5),
        ),
    },
}

_suspend_updates = False


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def label_of(key):
    for ident, name, _desc in LOOK_ITEMS:
        if ident == key:
            return name
    return key


def get_preset(look, key):
    """Definition of look ``key``. CUSTOM comes from the JSON kept in ``look.custom_spec``."""
    if key == 'CUSTOM':
        try:
            return json.loads(look.custom_spec)
        except ValueError:
            return {"sky": None, "fog": None, "cm": None, "lights": []}
    return PRESETS[key]


def is_look_object(ob):
    return ob is not None and bool(ob.get("cf_look"))


def is_look_world(world):
    return world is not None and bool(world.get("cf_look"))


def look_collection(scene):
    return common.ensure_child_collection(scene.collection, "CF_Look", "LOOK")


def preset_collection(scene, key):
    return common.ensure_child_collection(look_collection(scene), "CF_Look_" + key, "LOOK_" + key)


def _show(ob, view_layer):
    if ob.hide_viewport:
        ob.hide_viewport = False
    if ob.hide_render:
        ob.hide_render = False
    try:
        if ob.hide_get(view_layer=view_layer):
            ob.hide_set(False, view_layer=view_layer)
    except RuntimeError:
        pass


def _hide(ob, view_layer):
    if not ob.hide_render:
        ob.hide_render = True
    try:
        if not ob.hide_get(view_layer=view_layer):
            ob.hide_set(True, view_layer=view_layer)
    except RuntimeError:
        pass


def _energy(spec, distance):
    """Blender power for a light ``distance`` metres from the target."""
    bright = spec["bright"]
    if spec["type"] == 'SUN':
        return bright
    d2 = max(0.04, distance * distance)
    if spec["type"] == 'AREA':
        return bright * math.pi * d2
    return bright * 4.0 * math.pi * d2


# ---------------------------------------------------------------------------
# Where is the picture looking?
# ---------------------------------------------------------------------------

def _view_frame(scene, context):
    """Return (origin, forward, right, up) of the scene camera or the 3D view."""
    x_axis, y_axis, z_axis = Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))
    cam = scene.camera
    if cam is not None:
        m = cam.matrix_world
        m3 = m.to_3x3().normalized()
        return m.translation.copy(), -(m3 @ z_axis), m3 @ x_axis, m3 @ y_axis
    rv3d = getattr(context, "region_data", None) if context is not None else None
    if rv3d is not None:
        vm = rv3d.view_matrix.inverted()
        m3 = vm.to_3x3().normalized()
        return vm.translation.copy(), -(m3 @ z_axis), m3 @ x_axis, m3 @ y_axis
    return Vector((0.0, -10.0, 2.0)), y_axis.copy(), x_axis.copy(), z_axis.copy()


def _depsgraph(scene, context):
    try:
        if context is not None and context.scene == scene:
            return context.evaluated_depsgraph_get()
    except Exception:
        pass
    vl = common.view_layer_of(scene)
    return vl.depsgraph if vl is not None else None


def find_target(scene, context):
    """Point the picture looks at (first surface hit) and its distance from the eye."""
    origin, forward, right, up = _view_frame(scene, context)
    depsgraph = _depsgraph(scene, context)
    if depsgraph is not None:
        p = origin.copy()
        for _ in range(12):
            try:
                hit, loc, _nor, _idx, ob, _mw = scene.ray_cast(depsgraph, p, forward, distance=1000.0)
            except Exception:
                break
            if not hit:
                break
            src = ob.original if ob is not None else None
            if (loc - origin).length < 0.3 or (src is not None and src.hide_render):
                p = loc + forward * 1e-3
                continue
            return origin, forward, right, up, loc.copy(), (loc - origin).length
    return origin, forward, right, up, origin + forward * 8.0, 8.0


def _horizontal(forward, up):
    fh = Vector((forward.x, forward.y, 0.0))
    if fh.length < 1e-3:
        fh = Vector((up.x, up.y, 0.0))
    if fh.length < 1e-3:
        fh = Vector((0.0, 1.0, 0.0))
    fh.normalize()
    rh = fh.cross(Vector((0.0, 0.0, 1.0))).normalized()
    return fh, rh


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

def _look_light(scene, key, spec):
    coll = preset_collection(scene, key)
    ob = next((o for o in coll.objects if o.get("cf_look_role") == spec["role"]), None)
    if ob is None or ob.type != 'LIGHT':
        name = "CF_{}_{}".format(key.title().replace("_", ""), spec["role"].title())
        data = bpy.data.lights.new(name, spec["type"])
        ob = bpy.data.objects.new(name, data)
        coll.objects.link(ob)
        ob["cf_look"] = key
        ob["cf_look_role"] = spec["role"]
    elif ob.data.type != spec["type"]:
        ob.data.type = spec["type"]
    return ob


def _place_light(ob, spec, frame, target, dist):
    _origin, forward, _right, up = frame
    fh, rh = _horizontal(forward, up)
    z = Vector((0.0, 0.0, 1.0))
    if spec["type"] == 'SUN':
        az, el = math.radians(spec["az"]), math.radians(spec["el"])
        back = -fh  # from the target towards the camera
        horiz = Matrix.Rotation(az, 3, 'Z') @ back
        from_dir = (horiz * math.cos(el) + z * math.sin(el)).normalized()
        loc = target + from_dir * min(dist, 20.0)
        quat = from_dir.to_track_quat('Z', 'Y')
        ob.matrix_world = Matrix.LocRotScale(loc, quat, None)
        return 0.0
    f, r, u = spec["pos"]
    loc = target + fh * (f * dist) + rh * (r * dist) + z * (u * dist)
    to_light = loc - target
    if to_light.length < 1e-4:
        to_light = Vector((0.0, 0.0, 1.0))
    quat = to_light.normalized().to_track_quat('Z', 'Y')
    ob.matrix_world = Matrix.LocRotScale(loc, quat, None)
    return to_light.length


def _set_light_data(ob, spec, distance, intensity):
    light = ob.data
    base = _energy(spec, distance)
    ob["cf_base_energy"] = base
    light.color = spec["color"]
    light.energy = base * intensity
    light.use_shadow = True
    if spec["type"] == 'SUN':
        light.angle = math.radians(spec.get("size", 1.0))
    else:
        light.shadow_soft_size = spec.get("size", 0.1)
    if spec["type"] == 'SPOT':
        light.spot_size = math.radians(spec.get("spot", 45))
        light.spot_blend = spec.get("blend", 0.3)


def _set_fog(world, key, look):
    node_tree = world.node_tree
    if node_tree is None:
        return
    nodes, links = node_tree.nodes, node_tree.links
    for node in [n for n in nodes if n.get("cf_fog")]:
        nodes.remove(node)
    out = next((n for n in nodes if n.type == 'OUTPUT_WORLD'), None)
    if out is None or not look.fog or look.fog_amount <= 0.0:
        return
    fog = get_preset(look, key).get("fog")
    if not fog:
        return
    color, density, aniso = fog
    vol = nodes.new('ShaderNodeVolumeScatter')
    vol["cf_fog"] = True
    vol.location = (out.location.x - 220, out.location.y - 200)
    vol.inputs["Color"].default_value = (*color, 1.0)
    vol.inputs["Density"].default_value = density * look.fog_amount
    vol.inputs["Anisotropy"].default_value = aniso
    links.new(vol.outputs[0], out.inputs["Volume"])


def _build_world(world, key, look):
    preset = get_preset(look, key)
    if not preset.get("sky"):  # keep the world's own sky, only the haze is added
        _set_fog(world, key, look)
        return
    zenith, horizon, ground = preset["sky"]
    try:
        world.use_nodes = True
    except Exception:
        pass
    world.color = horizon
    node_tree = world.node_tree
    if node_tree is None:
        return
    nodes, links = node_tree.nodes, node_tree.links
    nodes.clear()
    out = nodes.new('ShaderNodeOutputWorld')
    out.location = (700, 0)
    bg = nodes.new('ShaderNodeBackground')
    bg.location = (450, 0)
    coord = nodes.new('ShaderNodeTexCoord')
    coord.location = (-500, 0)
    sep = nodes.new('ShaderNodeSeparateXYZ')
    sep.location = (-300, 0)
    remap = nodes.new('ShaderNodeMapRange')
    remap.location = (-120, 0)
    remap.inputs["From Min"].default_value = -1.0
    remap.inputs["From Max"].default_value = 1.0
    ramp = nodes.new('ShaderNodeValToRGB')
    ramp.location = (100, 0)
    elements = ramp.color_ramp.elements
    elements[0].position = 0.0
    elements[0].color = (*ground, 1.0)
    elements[1].position = 1.0
    elements[1].color = (*zenith, 1.0)
    e = elements.new(0.495)
    e.color = (*ground, 1.0)
    e = elements.new(0.5)
    e.color = (*horizon, 1.0)
    links.new(coord.outputs["Generated"], sep.inputs[0])
    links.new(sep.outputs["Z"], remap.inputs["Value"])
    links.new(remap.outputs["Result"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 1.0
    links.new(bg.outputs["Background"], out.inputs["Surface"])
    _set_fog(world, key, look)


def _look_world(key, base, look):
    keep_sky = not get_preset(look, key).get("sky")
    world = next((w for w in bpy.data.worlds
                  if w.get("cf_look") == key and w.library is None and bool(w.get("cf_keep_sky")) == keep_sky),
                 None)
    if world is None:
        world = base.copy() if base is not None else bpy.data.worlds.new("CF_World_" + key)
        world.name = "CF_World_" + key.title().replace("_", "") + ("Haze" if keep_sky else "")
        world["cf_look"] = key
        world["cf_keep_sky"] = keep_sky
        world.use_fake_user = True  # kept so saved states can switch back to it
    _build_world(world, key, look)
    return world


def _apply_cm(scene, cm):
    if not cm:
        return
    view_transform, look_name, exposure = cm
    vs = scene.view_settings
    try:
        vs.view_transform = view_transform
    except Exception:
        pass
    try:
        vs.look = look_name
    except Exception:
        try:
            vs.look = 'None'
        except Exception:
            pass
    vs.exposure = exposure
    vs.gamma = 1.0


# ---------------------------------------------------------------------------
# Your own lights
# ---------------------------------------------------------------------------

def _save_originals(scene, look, view_layer):
    world = scene.world
    look.original_world = world.name if (world is not None and not is_look_world(world)) else ""
    vs = scene.view_settings
    look.cm_view_transform = vs.view_transform
    look.cm_look = vs.look
    look.cm_exposure = vs.exposure
    look.cm_gamma = vs.gamma
    look.saved_lights.clear()
    for ob in scene.objects:
        if ob.type != 'LIGHT' or is_look_object(ob):
            continue
        item = look.saved_lights.add()
        item.name = ob.name
        item.hide_viewport = ob.hide_viewport
        item.hide_render = ob.hide_render
        try:
            item.hidden = ob.hide_get(view_layer=view_layer)
        except RuntimeError:
            item.hidden = False
        item.energy = ob.data.energy


def _restore_own_lights(scene, look, view_layer):
    for item in look.saved_lights:
        ob = scene.objects.get(item.name)
        if ob is None or ob.type != 'LIGHT':
            continue
        if ob.hide_viewport != item.hide_viewport:
            ob.hide_viewport = item.hide_viewport
        if ob.hide_render != item.hide_render:
            ob.hide_render = item.hide_render
        try:
            if ob.hide_get(view_layer=view_layer) != item.hidden:
                ob.hide_set(item.hidden, view_layer=view_layer)
        except RuntimeError:
            pass
        if ob.data.library is None and abs(ob.data.energy - item.energy) > 1e-6:
            ob.data.energy = item.energy


def _handle_own_lights(scene, look, view_layer):
    for item in look.saved_lights:
        ob = scene.objects.get(item.name)
        if ob is None or ob.type != 'LIGHT':
            continue
        if look.existing == 'HIDE':
            _hide(ob, view_layer)
        elif look.existing == 'DIM' and ob.data.library is None:
            ob.data.energy = item.energy * DIM_FACTOR


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_look(scene, key, context=None, spec=None, view=None):
    """Apply look ``key`` to ``scene``. Returns the number of look lights.

    ``spec`` (a look definition, see PRESETS) is only used with key 'CUSTOM'.
    ``view`` is a result of find_target(); pass it to place the lights for another view.
    """
    look = scene.conceptflow.look
    if key == 'CUSTOM':
        look.custom_spec = json.dumps(spec if spec is not None else get_preset(look, key))
    view_layer = common.view_layer_of(scene)
    if look.active == 'NONE':
        _save_originals(scene, look, view_layer)
    else:
        _restore_own_lights(scene, look, view_layer)
    for ob in scene.objects:
        if is_look_object(ob):
            _hide(ob, view_layer)

    preset = get_preset(look, key)
    origin, forward, right, up, target, dist = view or find_target(scene, context)
    dist = min(max(dist, 1.5), 60.0)
    frame = (origin, forward, right, up)
    count = 0
    for spec in preset["lights"]:
        ob = _look_light(scene, key, spec)
        light_dist = _place_light(ob, spec, frame, target, dist)
        _set_light_data(ob, spec, light_dist, look.intensity)
        _show(ob, view_layer)
        count += 1

    base = bpy.data.worlds.get(look.original_world) if look.original_world else None
    world = _look_world(key, base, look)
    if scene.world != world:
        scene.world = world
    _apply_cm(scene, preset.get("cm"))
    if not preset.get("keep_lights"):
        _handle_own_lights(scene, look, view_layer)
    look.active = key
    return count


def reset_look(scene):
    """Put back the world, lights and color management from before the first look."""
    look = scene.conceptflow.look
    if look.active == 'NONE':
        return False
    view_layer = common.view_layer_of(scene)
    _restore_own_lights(scene, look, view_layer)
    for ob in scene.objects:
        if is_look_object(ob):
            _hide(ob, view_layer)
    world = bpy.data.worlds.get(look.original_world) if look.original_world else None
    if scene.world != world:
        scene.world = world
    vs = scene.view_settings
    try:
        if look.cm_view_transform:
            vs.view_transform = look.cm_view_transform
        if look.cm_look:
            vs.look = look.cm_look
    except Exception:
        pass
    vs.exposure = look.cm_exposure
    vs.gamma = look.cm_gamma
    look.active = 'NONE'
    look.saved_lights.clear()
    return True


def remove_look_data(scene):
    """Reset, then delete this scene's look lights and unused look worlds."""
    reset_look(scene)
    removed = 0
    for ob in [o for o in scene.objects if is_look_object(o)]:
        data = ob.data
        bpy.data.objects.remove(ob, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.lights.remove(data)
        removed += 1
    root = next((c for c in scene.collection.children if c.get("cf_role") == "LOOK"), None)
    if root is not None:
        for child in list(root.children):
            if not child.all_objects:
                bpy.data.collections.remove(child)
        if not root.all_objects and not root.children:
            bpy.data.collections.remove(root)
    for world in [w for w in bpy.data.worlds if is_look_world(w) and w.library is None]:
        if world.users <= (1 if world.use_fake_user else 0):
            bpy.data.worlds.remove(world)
    return removed


def on_setting_changed(look, context, kind):
    """Update callback for the look settings in the panel."""
    if _suspend_updates or look.active == 'NONE':
        return
    scene = look.id_data
    if not isinstance(scene, bpy.types.Scene):
        return
    try:
        view_layer = common.view_layer_of(scene)
        if kind == 'INTENSITY':
            for ob in scene.objects:
                if ob.get("cf_look") == look.active and ob.type == 'LIGHT' and "cf_base_energy" in ob:
                    ob.data.energy = float(ob["cf_base_energy"]) * look.intensity
        elif kind == 'FOG':
            if is_look_world(scene.world):
                _set_fog(scene.world, look.active, look)
        elif kind == 'EXISTING':
            _restore_own_lights(scene, look, view_layer)
            _handle_own_lights(scene, look, view_layer)
    except Exception:
        import traceback
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Branch Tree integration: remember the look bookkeeping with each state
# ---------------------------------------------------------------------------

def _capture(scene):
    look = scene.conceptflow.look
    return {
        "active": look.active,
        "orig_world": look.original_world,
        "cm": [look.cm_view_transform, look.cm_look, round(look.cm_exposure, 5), round(look.cm_gamma, 5)],
        "saved": {i.name: [i.hide_viewport, i.hide_render, i.hidden, round(i.energy, 5)]
                  for i in look.saved_lights},
        "intensity": round(look.intensity, 5),
        "fog": look.fog,
        "fog_amount": round(look.fog_amount, 5),
        "existing": look.existing,
        "custom": look.custom_spec,
    }


def _restore(scene, data, report):
    global _suspend_updates
    look = scene.conceptflow.look
    _suspend_updates = True
    try:
        look.active = data.get("active", 'NONE')
        look.original_world = data.get("orig_world", "")
        cm = data.get("cm")
        if cm:
            look.cm_view_transform, look.cm_look, look.cm_exposure, look.cm_gamma = cm
        look.saved_lights.clear()
        for name, (hv, hr, hidden, energy) in data.get("saved", {}).items():
            item = look.saved_lights.add()
            item.name = name
            item.hide_viewport, item.hide_render, item.hidden, item.energy = hv, hr, hidden, energy
        look.intensity = data.get("intensity", look.intensity)
        look.fog = data.get("fog", look.fog)
        look.fog_amount = data.get("fog_amount", look.fog_amount)
        look.existing = data.get("existing", look.existing)
        look.custom_spec = data.get("custom", "")
    finally:
        _suspend_updates = False


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

PRESET_ITEMS = [item for item in LOOK_ITEMS if item[0] not in {'NONE', 'CUSTOM'}]


class CONCEPTFLOW_OT_look_apply(Operator):
    bl_idname = "conceptflow.look_apply"
    bl_label = "Apply Look"
    bl_description = "Apply a lighting look. Nothing is destroyed: Reset brings everything back"
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(name="Look", items=PRESET_ITEMS, default='HORROR')

    @classmethod
    def description(cls, context, properties):
        if properties is None:
            return tip(cls.bl_description)
        for ident, _name, desc in PRESET_ITEMS:
            if ident == properties.preset:
                return tip(desc) + "\n" + tip(cls.bl_description)
        return tip(cls.bl_description)

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    @guarded
    def execute(self, context):
        scene = context.scene
        saved_original = branch_tree.ensure_original(scene)
        count = apply_look(scene, self.preset, context)
        msg = rpt("Applied {look} ({count} lights)").format(look=label_of(self.preset), count=count)
        if saved_original:
            msg += " " + rpt("The scene before the look was saved as Original")
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class CONCEPTFLOW_OT_look_reset(Operator):
    bl_idname = "conceptflow.look_reset"
    bl_label = "Reset Look"
    bl_description = "Remove the look and bring back your world, lights and color settings"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.scene.conceptflow.look.active != 'NONE'

    @guarded
    def execute(self, context):
        reset_look(context.scene)
        self.report({'INFO'}, rpt("Look reset"))
        return {'FINISHED'}


class CONCEPTFLOW_OT_look_remove_data(Operator):
    bl_idname = "conceptflow.look_remove_data"
    bl_label = "Remove Look Data"
    bl_description = ("Reset the look, then delete the look lights and look worlds of this scene. "
                      "Saved states that use a look can no longer bring it back")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return scene is not None and any(c.get("cf_role") == "LOOK" for c in scene.collection.children)

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    @guarded
    def execute(self, context):
        removed = remove_look_data(context.scene)
        self.report({'INFO'}, rpt("Removed {count} look lights").format(count=removed))
        return {'FINISHED'}


classes = (
    CONCEPTFLOW_OT_look_apply,
    CONCEPTFLOW_OT_look_reset,
    CONCEPTFLOW_OT_look_remove_data,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    cf_state.register_provider("look", _capture, _restore, order=90)


def unregister():
    cf_state.unregister_provider("look")
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
