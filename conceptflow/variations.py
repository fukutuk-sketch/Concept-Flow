# SPDX-License-Identifier: GPL-3.0-or-later
# ConceptFlow for Blender
"""Auto Variations: 8 (single axis) or 16 (combined axes) quick renders of one picture.

Axes: Angle, Lens, Time of day, Atmosphere. A variation is a small dictionary of
changes relative to the *base* (the scene as it was when you pressed Generate):

    {"label": str, "cam": {"yaw", "elev", "dist", "lens"} or None,
     "time": hour or None, "atmos": index or None}

* Generate renders every variation small with EEVEE, then puts the scene back
  exactly as it was (the captured base state is restored and compared).
* Apply keeps the scene before the change in the Branch Tree ("Before Apply"),
  applies the variation and saves it as a new state.
"""

import copy
import json
import math
import os
import random
import time

import bpy
from bpy.props import IntProperty
from bpy.types import Operator
from mathutils import Matrix, Vector

from . import branch_tree, common, look_presets
from . import state as cf_state
from .common import guarded, iface, rpt, tip

RENDER_WIDTH = 480
RENDER_SAMPLES = 16
SHEET_COLUMNS = 4
SHEET_GAP = 4
SINGLE_COUNT = 8
COMBO_COUNT = 16
MIN_ELEVATION = -10.0
MAX_ELEVATION = 80.0

# ---------------------------------------------------------------------------
# The values of each axis. Combined mode uses every second value ([::2]),
# so each list is ordered to keep that subset varied.
# ---------------------------------------------------------------------------

# (yaw degrees, elevation change degrees, distance scale)
ANGLES = (
    (-45, 5, 1.0), (-20, 18, 0.85), (40, 5, 1.0), (15, -10, 0.9),
    (0, 28, 1.15), (-35, -5, 0.7), (60, 10, 1.2), (25, 12, 0.75),
)
LENSES = (14, 20, 28, 35, 50, 70, 85, 120)
HOURS = (5.5, 7.5, 10.0, 12.5, 15.5, 17.5, 19.5, 22.5)
# (name, haze (color, density, anisotropy) or None)
ATMOSPHERES = (
    ("Clear", None),
    ("Mist", ((0.75, 0.85, 1.0), 0.010, 0.3)),
    ("Light Haze", ((0.85, 0.88, 0.95), 0.015, 0.4)),
    ("Dust", ((0.90, 0.75, 0.55), 0.025, 0.4)),
    ("Haze", ((0.80, 0.85, 0.95), 0.030, 0.4)),
    ("Smoke", ((0.25, 0.25, 0.27), 0.050, 0.2)),
    ("Heavy Fog", ((0.85, 0.88, 0.90), 0.075, 0.5)),
    ("Warm Fog", ((1.0, 0.80, 0.60), 0.060, 0.5)),
)

AXIS_KEYS = ('ANGLE', 'LENS', 'TIME', 'ATMOS')
AXIS_LEN = {'ANGLE': len(ANGLES), 'LENS': len(LENSES), 'TIME': len(HOURS), 'ATMOS': len(ATMOSPHERES)}

# Time of day: hour -> (zenith, horizon, ground, sun color, sun strength, sun elevation,
#                       window color, window strength, exposure, haze density, haze color)
_NIGHT = ((0.004, 0.010, 0.030), (0.016, 0.034, 0.075), (0.002, 0.003, 0.005),
          (0.6, 0.72, 1.0), 0.7, 35.0, (0.5, 0.65, 1.0), 1.2, 0.2, 0.010, (0.5, 0.62, 0.85))
TIME_KEYS = (
    (0.0, _NIGHT),
    (5.5, ((0.08, 0.13, 0.30), (0.90, 0.50, 0.40), (0.03, 0.025, 0.03),
           (1.0, 0.55, 0.40), 1.5, 5.0, (1.0, 0.60, 0.50), 2.0, 0.1, 0.012, (1.0, 0.75, 0.65))),
    (8.0, ((0.15, 0.30, 0.65), (0.70, 0.75, 0.85), (0.12, 0.10, 0.09),
           (1.0, 0.85, 0.70), 3.5, 25.0, (1.0, 0.90, 0.80), 3.5, 0.0, 0.004, (0.9, 0.9, 1.0))),
    (12.0, ((0.12, 0.28, 0.70), (0.60, 0.75, 0.95), (0.15, 0.14, 0.12),
            (1.0, 0.97, 0.92), 5.0, 65.0, (0.95, 0.97, 1.0), 5.0, -0.2, 0.002, (0.9, 0.93, 1.0))),
    (16.0, ((0.14, 0.28, 0.62), (0.85, 0.78, 0.70), (0.13, 0.11, 0.09),
            (1.0, 0.90, 0.75), 4.0, 38.0, (1.0, 0.90, 0.75), 4.0, 0.0, 0.004, (1.0, 0.9, 0.8))),
    (18.0, ((0.16, 0.26, 0.50), (1.0, 0.55, 0.28), (0.06, 0.045, 0.04),
            (1.0, 0.58, 0.30), 4.0, 9.0, (1.0, 0.64, 0.34), 4.0, 0.3, 0.005, (1.0, 0.78, 0.55))),
    (19.5, ((0.05, 0.07, 0.20), (0.55, 0.25, 0.30), (0.02, 0.015, 0.02),
            (0.9, 0.40, 0.40), 1.0, 2.0, (0.7, 0.45, 0.6), 1.5, 0.2, 0.010, (0.8, 0.6, 0.7))),
    (22.0, _NIGHT),
    (24.0, _NIGHT),
)


# ---------------------------------------------------------------------------
# Pure logic (no scene access)
# ---------------------------------------------------------------------------

def _mix(a, b, t):
    if isinstance(a, (tuple, list)):
        return tuple(_mix(x, y, t) for x, y in zip(a, b))
    return a + (b - a) * t


def time_label(hour):
    return "{:02d}:{:02d}".format(int(hour) % 24, int(round((hour % 1.0) * 60)) % 60)


def time_spec(hour):
    """A look definition (see look_presets.PRESETS) for ``hour`` o'clock."""
    hour = hour % 24.0
    for (h0, k0), (h1, k1) in zip(TIME_KEYS, TIME_KEYS[1:]):
        if h0 <= hour <= h1:
            t = 0.0 if h1 == h0 else (hour - h0) / (h1 - h0)
            vals = _mix(k0, k1, t)
            break
    else:
        vals = TIME_KEYS[0][1]
    zen, hor, gnd, sun_c, sun_b, sun_el, win_c, win_b, exp, fog_d, fog_c = vals
    daylight = 5.5 <= hour <= 19.5
    az = 170.0 - (hour - 5.5) / 14.0 * 160.0 if daylight else 120.0
    return {
        "sky": [list(zen), list(hor), list(gnd)],
        "fog": [list(fog_c), fog_d, 0.4],
        "cm": ['AgX', 'AgX - Base Contrast', exp],
        "lights": [
            dict(role="sun", type='SUN', color=list(sun_c), bright=sun_b, az=az, el=sun_el, size=1.5),
            dict(role="window", type='AREA', color=list(win_c), bright=win_b,
                 pos=[-0.3, 0.3, 0.25], size=0.8),
        ],
    }


def atmosphere_fog(index):
    fog = ATMOSPHERES[index][1]
    return None if fog is None else [list(fog[0]), fog[1], fog[2]]


def _values(axis, subset):
    """Variation fragments for one axis (all 8, or the varied 4 for combined mode)."""
    idx = range(AXIS_LEN[axis])
    idx = idx[::2] if subset else idx
    return [(axis, i) for i in idx]


def _fragment(axis, i, rng):
    """(label, cam changes, time, atmos) for value ``i`` of ``axis``."""
    if axis == 'ANGLE':
        yaw, elev, dist = ANGLES[i]
        yaw += rng.uniform(-4, 4)
        elev += rng.uniform(-3, 3)
        return ("{:+.0f}°/{:+.0f}°".format(yaw, elev),
                {"yaw": round(yaw, 1), "elev": round(elev, 1), "dist": dist}, None, None)
    if axis == 'LENS':
        return ("{}mm".format(LENSES[i]), {"lens": LENSES[i]}, None, None)
    if axis == 'TIME':
        return (time_label(HOURS[i]), None, HOURS[i], None)
    return (ATMOSPHERES[i][0], None, None, i)


def make_variations(mode, axes, seed):
    """List of variation dictionaries.

    ``mode`` SINGLE: ``axes`` is one axis name, 8 variations. COMBO: ``axes`` is a list of
    2+ axis names, 16 variations (all pairs for two axes, a seeded spread for more).
    """
    rng = random.Random(seed)
    combos = []
    if mode == 'SINGLE':
        axis = axes if isinstance(axes, str) else axes[0]
        combos = [[(axis, i)] for i in range(AXIS_LEN[axis])]
    else:
        axes = [a for a in AXIS_KEYS if a in axes]
        if len(axes) < 2:
            return []
        per_axis = [_values(a, True) for a in axes]
        if len(axes) == 2:
            combos = [[a, b] for a in per_axis[0] for b in per_axis[1]]
        else:
            columns = []
            for vals in per_axis:
                col = list(vals) * (COMBO_COUNT // len(vals))
                rng.shuffle(col)
                columns.append(col)
            combos = [list(row) for row in zip(*columns)]
    out = []
    for combo in combos:
        label, cam, hour, atmos = [], {}, None, None
        for axis, i in combo:
            text, c, h, a = _fragment(axis, i, rng)
            label.append(text)
            if c:
                cam.update(c)
            if h is not None:
                hour = h
            if a is not None:
                atmos = a
        out.append({"label": " · ".join(label), "cam": cam or None, "time": hour, "atmos": atmos})
    return out


def neutral_spec():
    """A look that changes nothing but the haze: the world's own sky and your own lights stay."""
    return {"sky": None, "fog": None, "cm": None, "lights": [], "keep_lights": True}


def look_spec_for(var, base_spec):
    """Look definition of a variation, or None when it does not touch lighting or haze."""
    if var.get("time") is None and var.get("atmos") is None:
        return None
    if var.get("time") is not None:
        spec = time_spec(var["time"])
    else:
        spec = copy.deepcopy(base_spec) if base_spec else neutral_spec()
    if var.get("atmos") is not None:
        spec["fog"] = atmosphere_fog(var["atmos"])
    return spec


def orbit_position(offset, yaw_deg, elev_deg, scale):
    """New (x, y, z) of the camera relative to the target."""
    x, y, z = offset
    radius = math.sqrt(x * x + y * y + z * z) * scale
    horiz = math.hypot(x, y)
    yaw = math.atan2(y, x) + math.radians(yaw_deg)
    elev = math.degrees(math.atan2(z, horiz)) + elev_deg
    elev = math.radians(min(max(elev, MIN_ELEVATION), MAX_ELEVATION))
    return (radius * math.cos(elev) * math.cos(yaw),
            radius * math.cos(elev) * math.sin(yaw),
            radius * math.sin(elev))


# ---------------------------------------------------------------------------
# Scene access
# ---------------------------------------------------------------------------

def capture_base(scene, context):
    """Everything needed to rebuild the base and to place variations relative to it."""
    view = look_presets.find_target(scene, context)
    origin, forward, right, up, target, dist = view
    cam = scene.camera
    return {
        "view": [list(origin), list(forward), list(right), list(up), list(target), dist],
        "lens": cam.data.lens,
        "ortho": cam.data.type == 'ORTHO',
        "look": look_presets.get_preset(scene.conceptflow.look, scene.conceptflow.look.active)
        if scene.conceptflow.look.active != 'NONE' else None,
    }


def _view_tuple(base):
    origin, forward, right, up, target, dist = base["view"]
    return (Vector(origin), Vector(forward), Vector(right), Vector(up), Vector(target), dist)


def _clear_distance(scene, context, target, direction, wanted):
    """How far from ``target`` along ``direction`` is free, at most ``wanted``."""
    depsgraph = look_presets._depsgraph(scene, context)
    if depsgraph is None:
        return wanted
    p = target + direction * 0.05
    travelled = 0.05
    for _ in range(8):
        try:
            hit, loc, _nor, _idx, ob, _mw = scene.ray_cast(depsgraph, p, direction, distance=wanted - travelled)
        except Exception:
            return wanted
        if not hit:
            return wanted
        src = ob.original if ob is not None else None
        if src is not None and (src.hide_render or look_presets.is_look_object(src)):
            p = loc + direction * 1e-3
            travelled = (p - target).length
            continue
        return max((loc - target).length * 0.92, 0.2)
    return wanted


# Camera search in small rooms: the wanted spot is tried first, then turned around the subject
# and pulled in until the way from the subject to the camera is free.
YAW_STEPS = (0, 20, 40, 60, 90, 130, 180)
SCALE_STEPS = (1.0, 0.8, 0.65, 0.5)
ELEV_STEPS = (0, -8, -16)
GOOD_CLEARANCE = 0.9


def find_camera_spot(scene, context, target, offset, yaw, elev, scale):
    """Position of the camera (relative to nothing: world space) that is free of walls.

    Returns the best spot found; the wanted spot itself when the room is big enough.
    """
    sign = 1.0 if yaw >= 0 else -1.0
    turns = [0.0]
    for step in YAW_STEPS[1:]:
        turns += [sign * step, -sign * step]
    best, best_score = None, -1.0
    for de in ELEV_STEPS:
        for sc in SCALE_STEPS:
            for dy in turns:
                pos = Vector(orbit_position(tuple(offset), yaw + dy, elev + de, scale * sc))
                wanted = pos.length
                if wanted < 1e-4:
                    continue
                direction = pos / wanted
                clear = _clear_distance(scene, context, target, direction, wanted)
                ratio = clear / wanted
                # prefer free spots, then small turns, then keeping the distance
                score = min(ratio, 1.0) - 0.0015 * abs(dy) - 0.05 * (1.0 - sc) - 0.01 * abs(de) / 8.0
                if ratio >= GOOD_CLEARANCE:
                    return target + direction * min(clear, wanted)
                if score > best_score:
                    best, best_score = target + direction * max(clear, 0.3), score
    return best


def _apply_camera(scene, context, base, cam_change):
    cam = scene.camera
    origin, _f, _r, _u, target, _dist = _view_tuple(base)
    offset = origin - target
    lens = cam_change.get("lens", base["lens"])
    scale = cam_change.get("dist", 1.0)
    if not base["ortho"] and "lens" in cam_change:
        scale *= lens / max(base["lens"], 1e-3)  # dolly so the subject keeps its size
    loc = find_camera_spot(scene, context, target, offset, cam_change.get("yaw", 0.0),
                           cam_change.get("elev", 0.0), scale)
    if loc is None:
        return
    quat = (target - loc).to_track_quat('-Z', 'Y')
    cam.matrix_world = Matrix.LocRotScale(loc, quat, None)
    if not base["ortho"] and cam.data.library is None:
        cam.data.lens = lens


def apply_variation(scene, context, base, var):
    """Apply ``var`` to a scene that is in its base state."""
    if var.get("cam"):
        _apply_camera(scene, context, base, var["cam"])
    spec = look_spec_for(var, base.get("look"))
    if spec is not None:
        look_presets.apply_look(scene, 'CUSTOM', context, spec=spec, view=_view_tuple(base))


def _restore_base(scene, packed):
    cf_state.restore(scene, cf_state.unpack(packed))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _eevee_engine(scene):
    names = {i.identifier for i in scene.render.bl_rna.properties["engine"].enum_items}
    for name in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'):
        if name in names:
            return name
    return scene.render.engine


def _render_settings(scene):
    r = scene.render
    return {
        "engine": r.engine, "x": r.resolution_x, "y": r.resolution_y, "pct": r.resolution_percentage,
        "path": r.filepath, "fmt": r.image_settings.file_format, "color": r.image_settings.color_mode,
        "samples": scene.eevee.taa_render_samples,
    }


def _set_render_settings(scene, s):
    r = scene.render
    r.engine = s["engine"]
    r.resolution_x, r.resolution_y, r.resolution_percentage = s["x"], s["y"], s["pct"]
    r.filepath = s["path"]
    try:
        r.image_settings.file_format = s["fmt"]
        r.image_settings.color_mode = s["color"]
    except TypeError:
        pass
    scene.eevee.taa_render_samples = s["samples"]


def output_folder():
    blend = bpy.data.filepath
    stamp = time.strftime("%Y%m%d_%H%M%S")
    if blend:
        name = os.path.splitext(os.path.basename(blend))[0]
        root = os.path.join(os.path.dirname(blend), "ConceptFlow_Renders")
    else:
        name = "untitled"
        root = os.path.join(bpy.app.tempdir, "ConceptFlow_Renders")
    folder = os.path.join(root, "{}_{}".format(name, stamp))
    os.makedirs(folder, exist_ok=True)
    return folder


def make_contact_sheet(paths, out_path, columns=SHEET_COLUMNS, gap=SHEET_GAP):
    """Put the rendered PNGs in a grid. Returns out_path, or None when nothing could be read."""
    import numpy as np

    tiles = []
    for path in paths:
        img = bpy.data.images.load(path, check_existing=False)
        try:
            w, h = img.size
            arr = np.empty(w * h * 4, dtype=np.float32)
            img.pixels.foreach_get(arr)
            tiles.append(arr.reshape(h, w, 4))
        finally:
            bpy.data.images.remove(img)
    if not tiles:
        return None
    h, w = tiles[0].shape[:2]
    tiles = [t for t in tiles if t.shape[:2] == (h, w)]
    cols = min(columns, len(tiles))
    rows = -(-len(tiles) // cols)
    sheet = np.full((rows * h + (rows + 1) * gap, cols * w + (cols + 1) * gap, 4), 0.12, dtype=np.float32)
    sheet[..., 3] = 1.0
    for n, tile in enumerate(tiles):
        r, c = divmod(n, cols)
        # Blender images start at the bottom row, so the first row of tiles goes on top.
        y0 = (rows - 1 - r) * h + (rows - r) * gap
        x0 = c * w + (c + 1) * gap
        sheet[y0:y0 + h, x0:x0 + w] = tile
    out = bpy.data.images.new("CF_ContactSheet", sheet.shape[1], sheet.shape[0], alpha=False)
    try:
        out.pixels.foreach_set(sheet.ravel())
        out.filepath_raw = out_path
        out.file_format = 'PNG'
        out.save()
    finally:
        bpy.data.images.remove(out)
    return out_path


def render_variations(scene, context, variations, base, base_packed, folder):
    """Generator: renders one variation per step and yields (done, total).

    The scene is always put back to its base state, even when the generator is closed early.
    The third value is the (growing) list of image paths, one per finished variation.
    """
    paths = []
    saved = _render_settings(scene)
    try:
        r = scene.render
        aspect = (r.resolution_y / max(r.resolution_x, 1))
        r.engine = _eevee_engine(scene)
        r.resolution_x = RENDER_WIDTH
        r.resolution_y = max(1, int(round(RENDER_WIDTH * aspect)))
        r.resolution_percentage = 100
        r.image_settings.file_format = 'PNG'
        r.image_settings.color_mode = 'RGB'
        scene.eevee.taa_render_samples = RENDER_SAMPLES
        total = len(variations)
        for n, var in enumerate(variations):
            _restore_base(scene, base_packed)
            apply_variation(scene, context, base, var)
            path = os.path.join(folder, "v{:02d}.png".format(n + 1))
            r.filepath = path
            bpy.ops.render.render(write_still=True, scene=scene.name)
            paths.append(path if os.path.exists(path) else "")
            yield n + 1, total, paths
    finally:
        try:
            _restore_base(scene, base_packed)
        finally:
            _set_render_settings(scene, saved)


# ---------------------------------------------------------------------------
# Thumbnails
# ---------------------------------------------------------------------------

_previews = None


def thumb_icon(path):
    """Icon id of the rendered image at ``path`` (0 when it cannot be shown)."""
    global _previews
    if not path or not os.path.exists(path):
        return 0
    try:
        if _previews is None:
            import bpy.utils.previews
            _previews = bpy.utils.previews.new()
        if path not in _previews:
            _previews.load(path, path, 'IMAGE')
        return _previews[path].icon_id
    except Exception:
        return 0


def _free_previews():
    global _previews
    if _previews is not None:
        try:
            bpy.utils.previews.remove(_previews)
        except Exception:
            pass
        _previews = None


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

_running = False


def selected_axes(var):
    return [a for a, flag in (('ANGLE', var.use_angle), ('LENS', var.use_lens),
                              ('TIME', var.use_time), ('ATMOS', var.use_atmos)) if flag]


def planned_count(var):
    return SINGLE_COUNT if var.mode == 'SINGLE' else COMBO_COUNT


class CONCEPTFLOW_OT_variations_generate(Operator):
    bl_idname = "conceptflow.variations_generate"
    bl_label = "Generate Variations"
    bl_description = ("Render small previews of the scene while changing the angle, lens, time of day or "
                      "atmosphere. Your scene is put back as it was. Press Esc to stop")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return scene is not None and scene.camera is not None and not _running and context.window is not None

    def invoke(self, context, event):
        global _running
        scene = context.scene
        var = scene.conceptflow.variations
        if var.mode == 'SINGLE':
            specs = make_variations('SINGLE', var.axis, var.seed)
        else:
            axes = selected_axes(var)
            if len(axes) < 2:
                self.report({'WARNING'}, rpt("Turn on at least two of Angle, Lens, Time and Atmosphere"))
                return {'CANCELLED'}
            specs = make_variations('COMBO', axes, var.seed)
        try:
            branch_tree.ensure_original(scene)
            self._packed = cf_state.pack(cf_state.capture(scene, {"xforms": False}))
            self._base = capture_base(scene, context)
            self._folder = output_folder()
        except Exception as exc:  # noqa: BLE001
            common.last_error = repr(exc)
            self.report({'ERROR'}, rpt("ConceptFlow: unexpected error ({error}). You can undo with Ctrl+Z")
                        .format(error=exc))
            return {'CANCELLED'}
        self._specs = specs
        self._scene = scene
        self._gen = render_variations(scene, context, specs, self._base, self._packed, self._folder)
        self._paths = []
        _running = True
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)
        context.workspace.status_text_set(iface("Rendering variations... Esc to stop"))
        return {'RUNNING_MODAL'}

    def _finish(self, context, cancelled):
        global _running
        _running = False
        wm = context.window_manager
        try:
            wm.event_timer_remove(self._timer)
        except Exception:
            pass
        try:
            self._gen.close()  # runs the restore even when stopped early
        except Exception:
            import traceback
            traceback.print_exc()
        context.workspace.status_text_set(None)
        scene = self._scene
        ok = cf_state.equal(cf_state.capture(scene, {"xforms": False}), cf_state.unpack(self._packed))
        if not ok:
            self.report({'WARNING'}, rpt("The scene is not exactly as before. Use Back to Base"))
        done = [p for p in self._paths if p]
        if cancelled and not done:
            self.report({'INFO'}, rpt("Variations stopped"))
            return {'CANCELLED'}
        var = scene.conceptflow.variations
        _free_previews()
        var.items.clear()
        for spec, path in zip(self._specs, self._paths):
            if not path:
                continue
            item = var.items.add()
            item.name = spec["label"]
            item.spec = json.dumps(spec)
            item.image = path
        var.applied_index = -1
        var.before_uid = -1
        var.folder = self._folder
        var.base_state = self._packed
        var.base_view = json.dumps(self._base)
        try:
            var.sheet = make_contact_sheet([i.image for i in var.items], os.path.join(self._folder, "contact_sheet.png")) or ""
        except Exception:
            import traceback
            traceback.print_exc()
            var.sheet = ""
        self.report({'INFO'}, rpt("Made {count} variations. Saved in {folder}").format(
            count=len(var.items), folder=self._folder))
        for area in context.screen.areas:
            area.tag_redraw()
        return {'FINISHED'}

    def modal(self, context, event):
        if event.type == 'ESC' and event.value == 'PRESS':
            return self._finish(context, True)
        if event.type != 'TIMER':
            return {'RUNNING_MODAL'}
        try:
            n, total, self._paths = next(self._gen)
            context.window_manager.progress_update(n / max(total, 1))
            context.workspace.status_text_set(
                iface("Rendering variations... {done}/{total}  (Esc to stop)").format(done=n, total=total))
        except StopIteration:
            return self._finish(context, False)
        except Exception:
            import traceback
            common.last_error = traceback.format_exc()
            traceback.print_exc()
            self.report({'ERROR'}, rpt("Variations stopped because of an error. The scene was put back"))
            self._finish(context, True)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}


def _item_and_base(context, index):
    var = context.scene.conceptflow.variations
    if not (0 <= index < len(var.items)) or not var.base_state:
        return var, None, None
    try:
        return var, json.loads(var.items[index].spec), json.loads(var.base_view)
    except ValueError:
        return var, None, None


class CONCEPTFLOW_OT_variations_apply(Operator):
    bl_idname = "conceptflow.variations_apply"
    bl_label = "Apply"
    bl_description = ("Use this variation in the scene. The scene before is kept in the Branch Tree as "
                      "'Before Apply', so you can always go back")
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(default=0, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        return tip(cls.bl_description)

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.scene.camera is not None and not _running

    @guarded
    def execute(self, context):
        scene = context.scene
        var, spec, base = _item_and_base(context, self.index)
        if spec is None:
            self.report({'WARNING'}, rpt("This variation is no longer available"))
            return {'CANCELLED'}
        before = branch_tree.checkpoint(scene, "Before Apply", 'APPLY')
        if before < 0:
            before = branch_tree.tree_of(scene).current_uid
        _restore_base(scene, var.base_state)
        apply_variation(scene, context, base, spec)
        branch_tree.save_state(scene, "Var {:02d}: {}".format(self.index + 1, spec["label"]), 'MANUAL')
        var.applied_index = self.index
        var.before_uid = before
        self.report({'INFO'}, rpt("Applied variation {label}").format(label=spec["label"]))
        return {'FINISHED'}


class CONCEPTFLOW_OT_variations_base(Operator):
    bl_idname = "conceptflow.variations_base"
    bl_label = "Back to Base"
    bl_description = "Bring back the scene as it was when the variations were generated"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and bool(context.scene.conceptflow.variations.base_state) and not _running

    @guarded
    def execute(self, context):
        scene = context.scene
        branch_tree.checkpoint(scene, "Before Apply", 'APPLY')
        _restore_base(scene, scene.conceptflow.variations.base_state)
        scene.conceptflow.variations.applied_index = -1
        self.report({'INFO'}, rpt("Back to the base scene"))
        return {'FINISHED'}


class CONCEPTFLOW_OT_variations_undo(Operator):
    bl_idname = "conceptflow.variations_undo"
    bl_label = "Undo Apply"
    bl_description = "Go back to the scene as it was before the last variation was applied"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        scene = context.scene
        if scene is None or _running:
            return False
        var = scene.conceptflow.variations
        return var.applied_index >= 0 and var.before_uid >= 0

    @guarded
    def execute(self, context):
        scene = context.scene
        var = scene.conceptflow.variations
        if branch_tree.restore_uid(scene, var.before_uid) is None:
            var.applied_index = -1
            var.before_uid = -1
            self.report({'WARNING'}, rpt("The saved state is gone"))
            return {'CANCELLED'}
        var.applied_index = -1
        var.before_uid = -1
        self.report({'INFO'}, rpt("Back to the scene before Apply"))
        return {'FINISHED'}


class CONCEPTFLOW_OT_variations_clear(Operator):
    bl_idname = "conceptflow.variations_clear"
    bl_label = "Clear Results"
    bl_description = "Forget the variations shown here. The rendered files stay on disk"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and len(context.scene.conceptflow.variations.items) > 0 and not _running

    @guarded
    def execute(self, context):
        var = context.scene.conceptflow.variations
        var.items.clear()
        var.base_state = ""
        var.base_view = ""
        var.sheet = ""
        var.applied_index = -1
        var.before_uid = -1
        _free_previews()
        return {'FINISHED'}


class CONCEPTFLOW_OT_variations_open_folder(Operator):
    bl_idname = "conceptflow.variations_open_folder"
    bl_label = "Open Folder"
    bl_description = "Open the folder with the rendered variations and the contact sheet"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        folder = context.scene.conceptflow.variations.folder if context.scene else ""
        return bool(folder) and os.path.isdir(folder)

    @guarded
    def execute(self, context):
        bpy.ops.wm.path_open(filepath=context.scene.conceptflow.variations.folder)
        return {'FINISHED'}


classes = (
    CONCEPTFLOW_OT_variations_generate,
    CONCEPTFLOW_OT_variations_apply,
    CONCEPTFLOW_OT_variations_base,
    CONCEPTFLOW_OT_variations_undo,
    CONCEPTFLOW_OT_variations_clear,
    CONCEPTFLOW_OT_variations_open_folder,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    _free_previews()
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
