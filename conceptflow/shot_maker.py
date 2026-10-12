# SPDX-License-Identifier: GPL-3.0-or-later
# ConceptFlow for Blender
"""Shot Maker: camera sliders and shot presets for concept shots.

* Eye height presets (child, adult, drone, worm's eye ...) and a height slider.
* Lens presets and a lens slider with an optional dolly (the subject keeps its size).
* Aspect ratio presets (16:9, 2.39:1, 9:16 ...).
* Shot presets (establishing, hero, close-up, bird's eye, dutch ...).
* A horizon guide drawn over the camera view, and click-to-focus.

Everything works on the scene camera. The subject is the first surface the camera looks at,
so heights and lenses are changed *around* it and it stays in frame.
"""

import math
import traceback

import blf
import bpy
import gpu
from bpy.props import EnumProperty, FloatProperty, IntProperty
from bpy.types import Operator
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Vector

from . import branch_tree, common, look_presets, variations
from .common import guarded, iface, rpt, tip

MIN_LENS = 8.0
MAX_LENS = 400.0

# key: (label, eye height in metres, tooltip)
HEIGHTS = {
    'WORM': ("Worm's Eye", 0.15, "Camera on the ground, looking up at things"),
    'CHILD': ("Child", 1.0, "The view of a child, about 1 m"),
    'ADULT': ("Adult", 1.6, "Standing adult eye height, 1.6 m"),
    'UPPER': ("Balcony", 5.0, "Looking down from a first floor, 5 m"),
    'DRONE': ("Drone", 30.0, "Looking down from 30 m"),
}
HEIGHT_ORDER = ('WORM', 'CHILD', 'ADULT', 'UPPER', 'DRONE')
HEIGHT_ITEMS = [(k, HEIGHTS[k][0], HEIGHTS[k][2]) for k in HEIGHT_ORDER]

LENS_PRESETS = (14, 24, 35, 50, 85, 135)

# key: (label, width, height, tooltip)
ASPECTS = {
    'R16_9': ("16:9", 16, 9, "Wide screen"),
    'R239': ("2.39:1 Scope", 239, 100, "Cinemascope"),
    'R185': ("1.85:1", 185, 100, "Cinema flat"),
    'R3_2': ("3:2", 3, 2, "Photo"),
    'R4_3': ("4:3", 4, 3, "Classic screen"),
    'R1_1': ("1:1", 1, 1, "Square"),
    'R9_16': ("9:16", 9, 16, "Vertical, for phones"),
}
ASPECT_ORDER = ('R16_9', 'R239', 'R185', 'R3_2', 'R4_3', 'R1_1', 'R9_16')
ASPECT_ITEMS = [(k, ASPECTS[k][0], ASPECTS[k][3]) for k in ASPECT_ORDER]

# key: label, lens, how high (height = metres, height_rel = metres above the subject, elev = degrees),
#      distance factor (1 = same subject size as now), roll degrees, tooltip
SHOTS = {
    'ESTABLISH': dict(label="Establishing", lens=24, height=1.7, dist=1.5, roll=0.0,
                      tip="Wide shot that shows the whole place from standing height"),
    'EYE': dict(label="Eye Level", lens=35, height=1.6, dist=1.0, roll=0.0,
                tip="Natural eye level shot"),
    'HERO': dict(label="Hero (Low)", lens=28, height=0.35, dist=0.9, roll=0.0,
                 tip="Camera near the ground looking up: makes things powerful"),
    'CLOSE': dict(label="Close-up", lens=85, height_rel=0.1, dist=0.55, roll=0.0,
                  tip="Long lens, close to the subject"),
    'WIDE_ROOM': dict(label="Wide Interior", lens=16, height=1.4, dist=0.85, roll=0.0,
                      tip="Very wide lens for small rooms"),
    'BIRD': dict(label="Bird's Eye", lens=35, elev=65.0, dist=1.2, roll=0.0,
                 tip="High above, looking down steeply"),
    'DRONE': dict(label="Drone", lens=24, height=30.0, dist=1.5, roll=0.0,
                  tip="Aerial view from 30 m"),
    'WORM': dict(label="Worm's Eye", lens=16, height=0.12, dist=0.8, roll=0.0,
                 tip="Camera on the ground, looking steeply up"),
    'DUTCH': dict(label="Dutch Angle", lens=35, height=1.6, dist=1.0, roll=14.0,
                  tip="Tilted camera for tension"),
}
SHOT_ORDER = ('ESTABLISH', 'EYE', 'HERO', 'CLOSE', 'WIDE_ROOM', 'BIRD', 'DRONE', 'WORM', 'DUTCH')
SHOT_ITEMS = [(k, SHOTS[k]["label"], SHOTS[k]["tip"]) for k in SHOT_ORDER]


# ---------------------------------------------------------------------------
# Camera maths (no UI)
# ---------------------------------------------------------------------------

def aim_matrix(loc, target, roll_degrees=0.0):
    """Camera matrix at ``loc`` looking at ``target`` with the horizon level, then rolled."""
    direction = target - loc
    if direction.length < 1e-6:
        direction = Vector((0.0, 1.0, 0.0))
    quat = direction.to_track_quat('-Z', 'Y')
    return Matrix.LocRotScale(loc, quat, None) @ Matrix.Rotation(math.radians(roll_degrees), 4, 'Z')


def camera_roll(cam):
    """Roll of the camera around its viewing direction, in degrees (0 = level horizon)."""
    m = cam.matrix_world.to_3x3()
    forward = -(m @ Vector((0.0, 0.0, 1.0)))
    up = m @ Vector((0.0, 1.0, 0.0))
    z = Vector((0.0, 0.0, 1.0))
    right0 = forward.cross(z)
    if right0.length < 1e-4:
        return 0.0
    up0 = right0.cross(forward).normalized()
    up = up.normalized()
    return -math.degrees(math.atan2(up0.cross(up).dot(forward), up0.dot(up)))


def horizontal_distance(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


def shot_elevation(target, radius, spec):
    """Elevation (degrees above the subject) a shot preset asks for at distance ``radius``."""
    if "elev" in spec:
        return spec["elev"]
    height = spec["height"] if "height" in spec else target.z + spec["height_rel"]
    dz = height - target.z
    return math.degrees(math.asin(max(-1.0, min(1.0, dz / max(radius, 1e-3)))))


def scene_target(scene, context=None):
    """(camera location, subject point, distance to it) for the scene camera."""
    cam = scene.camera
    origin, _forward, _right, _up, target, dist = look_presets.find_target(scene, context)
    return cam.matrix_world.translation.copy(), target, dist


# ---------------------------------------------------------------------------
# Applying changes to the scene camera
# ---------------------------------------------------------------------------

def set_roll(scene, roll_degrees):
    cam = scene.camera
    if cam is None:
        return
    loc = cam.matrix_world.translation.copy()
    forward = -(cam.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0)))
    cam.matrix_world = aim_matrix(loc, loc + forward, roll_degrees)


def set_height(scene, height, keep_aim=True, context=None):
    """Move the camera straight up or down to ``height`` metres (z)."""
    cam = scene.camera
    if cam is None:
        return
    loc, target, _dist = scene_target(scene, context)
    roll = camera_roll(cam)
    new_loc = Vector((loc.x, loc.y, height))
    if keep_aim:
        cam.matrix_world = aim_matrix(new_loc, target, roll)
    else:
        m = cam.matrix_world.copy()
        m.translation = new_loc
        cam.matrix_world = m


def set_lens(scene, lens, dolly=True, context=None):
    """Change the focal length; with ``dolly`` the camera moves so the subject keeps its size."""
    cam = scene.camera
    if cam is None or cam.data.library is not None:
        return
    lens = min(max(lens, MIN_LENS), MAX_LENS)
    old = max(cam.data.lens, 1e-3)
    if dolly and cam.data.type != 'ORTHO' and abs(lens - old) > 1e-4:
        loc, target, _dist = scene_target(scene, context)
        offset = loc - target
        wanted = offset.length * lens / old
        if offset.length > 1e-4:
            direction = offset / offset.length
            clear = variations._clear_distance(scene, context, target, direction, wanted)
            new_loc = target + direction * clear
            m = cam.matrix_world.copy()
            m.translation = new_loc
            cam.matrix_world = m
    cam.data.lens = lens


def apply_shot(scene, key, keep_lens_size=True, context=None):
    """Place the camera for shot preset ``key`` around the subject."""
    cam = scene.camera
    spec = SHOTS[key]
    loc, target, _dist = scene_target(scene, context)
    offset = loc - target
    r0 = max(offset.length, 0.3)
    lens0 = max(cam.data.lens, 1e-3)
    scale = spec["dist"] * (spec["lens"] / lens0 if keep_lens_size and cam.data.type != 'ORTHO' else 1.0)
    radius = r0 * scale
    if "elev" not in spec:
        # a very high or low camera needs room: keep the angle below the limit
        height = spec["height"] if "height" in spec else target.z + spec["height_rel"]
        radius = max(radius, abs(height - target.z) / math.sin(math.radians(75.0)))
        scale = radius / r0
    elev0 = math.degrees(math.atan2(offset.z, max(math.hypot(offset.x, offset.y), 1e-6)))
    elev = shot_elevation(target, radius, spec)
    new_loc = variations.find_camera_spot(scene, context, target, offset, 0.0, elev - elev0, scale)
    if new_loc is None:
        return False
    cam.matrix_world = aim_matrix(new_loc, target, spec["roll"])
    if cam.data.library is None:
        cam.data.lens = spec["lens"]
    return True


def apply_aspect(scene, key):
    _label, w, h, _tip = ASPECTS[key]
    r = scene.render
    base = max(r.resolution_x, r.resolution_y)
    if w >= h:
        r.resolution_x = base
        r.resolution_y = max(1, int(round(base * h / w)))
    else:
        r.resolution_y = base
        r.resolution_x = max(1, int(round(base * w / h)))
    cam = scene.camera
    if cam is not None and cam.data.library is None:
        cam.data.sensor_fit = 'AUTO'


def camera_from_view(scene, rv3d):
    """Create the scene camera if needed and put it where the 3D view is."""
    cam = scene.camera
    if cam is None or cam.type != 'CAMERA':
        data = bpy.data.cameras.new("CF_Cam")
        cam = bpy.data.objects.new("CF_Cam", data)
        scene.collection.objects.link(cam)
        scene.camera = cam
    if rv3d is not None:
        cam.matrix_world = rv3d.view_matrix.inverted()
    return cam


# Properties with get/set functions: the sliders show the camera itself.

def _scene_of(self):
    return self.id_data


def get_height(self):
    cam = _scene_of(self).camera
    return cam.matrix_world.translation.z if cam is not None else 0.0


def set_height_prop(self, value):
    scene = _scene_of(self)
    try:
        set_height(scene, value, self.keep_aim)
    except Exception:
        traceback.print_exc()


def get_lens(self):
    cam = _scene_of(self).camera
    return cam.data.lens if cam is not None else 35.0


def set_lens_prop(self, value):
    try:
        set_lens(_scene_of(self), value, self.dolly)
    except Exception:
        traceback.print_exc()


def get_roll(self):
    cam = _scene_of(self).camera
    return camera_roll(cam) if cam is not None else 0.0


def set_roll_prop(self, value):
    try:
        set_roll(_scene_of(self), value)
    except Exception:
        traceback.print_exc()


def on_horizon_changed(self, context):
    if self.show_horizon:
        _add_handler()
    elif not any(s.conceptflow.shot.show_horizon for s in bpy.data.scenes):
        _remove_handler()
    redraw_all()


# ---------------------------------------------------------------------------
# Horizon guide (overlay in the 3D view)
# ---------------------------------------------------------------------------

_handle = None


def _draw_horizon():
    context = bpy.context
    scene, region, rv3d = context.scene, context.region, context.region_data
    if scene is None or region is None or rv3d is None or context.space_data is None:
        return
    if context.space_data.type != 'VIEW_3D' or not scene.conceptflow.shot.show_horizon:
        return
    cam = scene.camera
    if cam is None or cam.type != 'CAMERA':
        return
    try:
        from bpy_extras.view3d_utils import location_3d_to_region_2d
        from .read_views import _frame_rect
        loc = cam.matrix_world.translation
        forward = -(cam.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0)))
        flat = Vector((forward.x, forward.y, 0.0))
        if flat.length < 1e-3:
            return  # looking straight up or down: no horizon
        flat.normalize()
        point = location_3d_to_region_2d(region, rv3d, loc + flat * 10000.0)
        if point is None:
            return
        x0, y0, x1, y1 = _frame_rect(context, region, rv3d)
        if not (y0 - 40.0 <= point.y <= y1 + 40.0):
            return
        y = min(max(point.y, y0), y1)
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        batch = batch_for_shader(shader, 'LINES', {"pos": [(x0, y, 0.0), (x1, y, 0.0)]})
        gpu.state.blend_set('ALPHA')
        shader.bind()
        for width, color in ((4.0, (0.0, 0.0, 0.0, 0.5)), (1.5, (1.0, 0.75, 0.2, 0.95))):
            gpu.state.line_width_set(width)
            shader.uniform_float("color", color)
            batch.draw(shader)
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set('NONE')
        blf.size(0, 14)
        blf.color(0, 1.0, 0.8, 0.3, 1.0)
        blf.position(0, x0 + 10.0, y + 6.0, 0)
        blf.draw(0, "{:.2f} m".format(loc.z))
    except Exception:
        traceback.print_exc()
        _remove_handler()


def _add_handler():
    global _handle
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(_draw_horizon, (), 'WINDOW', 'POST_PIXEL')


def _remove_handler():
    global _handle
    if _handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        except Exception:
            pass
        _handle = None


def redraw_all():
    wm = bpy.context.window_manager
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class _NeedsCamera:
    @classmethod
    def poll(cls, context):
        scene = context.scene
        return scene is not None and scene.camera is not None and scene.camera.type == 'CAMERA'


class CONCEPTFLOW_OT_shot_camera(Operator):
    bl_idname = "conceptflow.shot_camera"
    bl_label = "Camera from View"
    bl_description = ("Put the scene camera where the 3D view is. A camera named CF_Cam is made "
                      "when the scene has none")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.scene is not None and context.area is not None
                and context.area.type == 'VIEW_3D' and context.mode == 'OBJECT')

    @guarded
    def execute(self, context):
        space = context.area.spaces.active
        rv3d = space.region_3d if space is not None else None
        if rv3d is not None and rv3d.view_perspective == 'CAMERA':
            rv3d = None  # already looking through the camera: keep it where it is
        camera_from_view(context.scene, rv3d)
        return {'FINISHED'}


class CONCEPTFLOW_OT_shot_height(_NeedsCamera, Operator):
    bl_idname = "conceptflow.shot_height"
    bl_label = "Eye Height"
    bl_description = "Move the camera to an eye height. It keeps looking at the same subject"
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(name="Height", items=HEIGHT_ITEMS, default='ADULT')

    @classmethod
    def description(cls, context, properties):
        if properties is not None and properties.kind in HEIGHTS:
            return tip(HEIGHTS[properties.kind][2]) + "\n" + tip(cls.bl_description)
        return tip(cls.bl_description)

    @guarded
    def execute(self, context):
        scene = context.scene
        set_height(scene, HEIGHTS[self.kind][1], scene.conceptflow.shot.keep_aim, context)
        return {'FINISHED'}


class CONCEPTFLOW_OT_shot_lens(_NeedsCamera, Operator):
    bl_idname = "conceptflow.shot_lens"
    bl_label = "Lens"
    bl_description = "Set the focal length. With Dolly on, the camera moves so the subject keeps its size"
    bl_options = {'REGISTER', 'UNDO'}

    mm: IntProperty(name="Focal Length", min=8, max=400, default=35)

    @classmethod
    def description(cls, context, properties):
        return tip(cls.bl_description)

    @guarded
    def execute(self, context):
        scene = context.scene
        set_lens(scene, float(self.mm), scene.conceptflow.shot.dolly, context)
        return {'FINISHED'}


class CONCEPTFLOW_OT_shot_aspect(Operator):
    bl_idname = "conceptflow.shot_aspect"
    bl_label = "Aspect Ratio"
    bl_description = "Set the picture shape of the render (the long side keeps its size)"
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(name="Aspect Ratio", items=ASPECT_ITEMS, default='R16_9')

    @classmethod
    def description(cls, context, properties):
        if properties is not None and properties.kind in ASPECTS:
            return tip(ASPECTS[properties.kind][3]) + "\n" + tip(cls.bl_description)
        return tip(cls.bl_description)

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    @guarded
    def execute(self, context):
        apply_aspect(context.scene, self.kind)
        return {'FINISHED'}


class CONCEPTFLOW_OT_shot_preset(_NeedsCamera, Operator):
    bl_idname = "conceptflow.shot_preset"
    bl_label = "Shot"
    bl_description = "Place the camera for a typical concept shot around the subject"
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(name="Shot", items=SHOT_ITEMS, default='ESTABLISH')

    @classmethod
    def description(cls, context, properties):
        if properties is not None and properties.kind in SHOTS:
            return tip(SHOTS[properties.kind]["tip"]) + "\n" + tip(cls.bl_description)
        return tip(cls.bl_description)

    @guarded
    def execute(self, context):
        scene = context.scene
        branch_tree.checkpoint(scene)  # keep the present camera in the Branch Tree
        if not apply_shot(scene, self.kind, scene.conceptflow.shot.dolly, context):
            self.report({'WARNING'}, rpt("No place for the camera was found"))
            return {'CANCELLED'}
        return {'FINISHED'}


class CONCEPTFLOW_OT_shot_focus(_NeedsCamera, Operator):
    bl_idname = "conceptflow.shot_focus"
    bl_label = "Click to Focus"
    bl_description = ("Click on something in the 3D view to focus the camera on it. "
                      "Depth of field is switched on. Right click or Esc to finish")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return super().poll(context) and context.area is not None and context.area.type == 'VIEW_3D'

    def _region(self, context):
        area = context.area
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        return region, area.spaces.active.region_3d if area.spaces.active else None

    def invoke(self, context, event):
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(iface("Click to Focus: click on the subject, right click / Esc = finish"))
        return {'RUNNING_MODAL'}

    def _finish(self, context):
        context.workspace.status_text_set(None)
        return {'FINISHED'}

    def modal(self, context, event):
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            return {'PASS_THROUGH'}
        if (event.type in {'ESC', 'RIGHTMOUSE', 'RET'}) and event.value == 'PRESS':
            return self._finish(context)
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            try:
                self._click(context, event)
            except Exception:
                common.last_error = traceback.format_exc()
                traceback.print_exc()
                self.report({'ERROR'}, rpt("Click to Focus stopped because of an error"))
                return self._finish(context)
            return {'RUNNING_MODAL'}
        return {'RUNNING_MODAL'}

    def _click(self, context, event):
        from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
        scene = context.scene
        cam = scene.camera
        region, rv3d = self._region(context)
        if region is None or rv3d is None:
            return
        if not (region.x <= event.mouse_x < region.x + region.width
                and region.y <= event.mouse_y < region.y + region.height):
            return
        coord = (event.mouse_x - region.x, event.mouse_y - region.y)
        origin = region_2d_to_origin_3d(region, rv3d, coord)
        direction = region_2d_to_vector_3d(region, rv3d, coord)
        depsgraph = context.evaluated_depsgraph_get()
        p = origin.copy()
        for _ in range(8):
            hit, loc, _nor, _idx, ob, _mw = scene.ray_cast(depsgraph, p, direction, distance=10000.0)
            if not hit:
                return
            src = ob.original if ob is not None else None
            if src is not None and (src.hide_render or look_presets.is_look_object(src)):
                p = loc + direction * 1e-3
                continue
            break
        else:
            return
        forward = -(cam.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0)))
        distance = (loc - cam.matrix_world.translation).dot(forward.normalized())
        if distance <= 0.0 or cam.data.library is not None:
            return
        dof = cam.data.dof
        dof.use_dof = True
        dof.focus_object = None
        dof.focus_distance = distance
        self.report({'INFO'}, rpt("Focus distance {distance} m").format(distance="{:.2f}".format(distance)))


classes = (
    CONCEPTFLOW_OT_shot_camera,
    CONCEPTFLOW_OT_shot_height,
    CONCEPTFLOW_OT_shot_lens,
    CONCEPTFLOW_OT_shot_aspect,
    CONCEPTFLOW_OT_shot_preset,
    CONCEPTFLOW_OT_shot_focus,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    _remove_handler()
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
