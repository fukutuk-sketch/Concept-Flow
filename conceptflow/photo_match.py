# SPDX-License-Identifier: GPL-3.0-or-later
# ConceptFlow for Blender
"""Photo Match: set the camera to the perspective of a photo (for photobashing).

How it works (the same idea as fSpy):

1. Load a photo. It becomes the background image of the scene camera.
2. Draw two lines along one horizontal direction of the photo (for example the edges of a
   street) and two lines along the other horizontal direction (a cross street, a facade).
   Each pair meets in a vanishing point.
3. Click the point on the ground that should become the world origin.
4. From the two vanishing points the focal length and the camera rotation follow
   (the image centre is taken as the optical centre). The camera height you give fixes the
   scale, so the ground is at z = 0 and the two directions are the X and Y axes.

Graybox parts you add afterwards line up with the photo.
"""

import math
import traceback

import bpy
import gpu
from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Vector

from . import common, shot_maker
from .common import guarded, iface, rpt, tip

POINTS_NEEDED = 9  # 2 lines for X (4 points), 2 lines for Y (4 points), 1 origin point
LINE_COLORS = ((1.0, 0.25, 0.25, 1.0), (0.3, 0.9, 0.3, 1.0), (1.0, 1.0, 1.0, 1.0))
PHOTO_TAG = "cf_photo"


class MatchError(Exception):
    """A problem with the lines that the user can fix; the text is shown to them."""


# ---------------------------------------------------------------------------
# Maths (pure)
# ---------------------------------------------------------------------------

def vanishing_point(p1, p2, p3, p4):
    """Intersection of the lines p1-p2 and p3-p4 (2D points). Raises MatchError if parallel."""
    def line(a, b):
        return Vector((a[0], a[1], 1.0)).cross(Vector((b[0], b[1], 1.0)))
    v = line(p1, p2).cross(line(p3, p4))
    if abs(v.z) < 1e-9 * max(1.0, v.length):
        raise MatchError("The two lines are parallel in the photo. Draw lines that really run towards a vanishing point")
    return Vector((v.x / v.z, v.y / v.z))


def solve_camera(lines_x, lines_y, origin, camera_height, flip=False):
    """Solve focal length (pixels), rotation and location of the camera.

    Points are in pixels from the image centre, y up. ``lines_x`` / ``lines_y`` are two lines
    each, a line being a pair of points. ``origin`` is where the world origin appears (on the
    ground). The ground is z = 0 and the camera is ``camera_height`` above it.
    Returns (focal length in pixels, 3x3 matrix of the camera axes in the world, location).
    """
    v1 = vanishing_point(lines_x[0][0], lines_x[0][1], lines_x[1][0], lines_x[1][1])
    v2 = vanishing_point(lines_y[0][0], lines_y[0][1], lines_y[1][0], lines_y[1][1])
    f2 = -(v1.dot(v2))
    if f2 <= 1e-6:
        raise MatchError("These lines do not give a usable perspective. The two directions should be "
                         "about 90 degrees apart on the ground")
    f = math.sqrt(f2)
    d1 = Vector((v1.x, v1.y, -f)).normalized()
    d2 = Vector((v2.x, v2.y, -f)).normalized()
    # the world X axis should point to the right of the picture
    if d1.x < 0.0:
        d1 = -d1
    z = d1.cross(d2)
    if z.y < 0.0:
        d2 = -d2
        z = d1.cross(d2)
    # make the three axes exactly perpendicular
    x_axis = d1.normalized()
    z_axis = z.normalized()
    y_axis = z_axis.cross(x_axis).normalized()
    if flip:
        x_axis, y_axis = -x_axis, -y_axis
    # x_axis, y_axis, z_axis are the world axes seen from the camera. As rows they form the matrix
    # whose columns are the camera axes in the world, which is what the camera object needs.
    rotation = Matrix((x_axis, y_axis, z_axis))
    direction_cam = Vector((origin[0], origin[1], -f)).normalized()
    direction_world = rotation @ direction_cam
    if direction_world.z > -1e-3:
        raise MatchError("The origin point is not below the horizon. Click a point on the ground")
    location = direction_world * (camera_height / direction_world.z)
    return f, rotation, location


def lens_from_pixels(f_pixels, width, height, sensor_width):
    """Focal length in mm for a camera with sensor fit AUTO."""
    return f_pixels * sensor_width / max(width, height)


# ---------------------------------------------------------------------------
# Photo as camera background
# ---------------------------------------------------------------------------

def photo_background(cam):
    """The ConceptFlow photo entry among the camera's background images, or None."""
    for bg in cam.data.background_images:
        if bg.image is not None and bg.image.get(PHOTO_TAG):
            return bg
    return None


def load_photo(scene, filepath, rv3d=None):
    cam = scene.camera
    if cam is None or cam.type != 'CAMERA':
        cam = shot_maker.camera_from_view(scene, rv3d)
    img = bpy.data.images.load(filepath, check_existing=True)
    img[PHOTO_TAG] = True
    width, height = img.size
    if width <= 0 or height <= 0:
        raise MatchError("The photo could not be read")
    data = cam.data
    bg = photo_background(cam)
    if bg is None:
        bg = data.background_images.new()
    bg.image = img
    bg.frame_method = 'FIT'
    bg.alpha = scene.conceptflow.photo.opacity
    bg.display_depth = 'FRONT'
    data.show_background_images = True
    data.sensor_fit = 'AUTO'
    r = scene.render
    base = max(r.resolution_x, r.resolution_y)
    if width >= height:
        r.resolution_x, r.resolution_y = base, max(1, int(round(base * height / width)))
    else:
        r.resolution_y, r.resolution_x = base, max(1, int(round(base * width / height)))
    return cam, img


def remove_photo(scene):
    cam = scene.camera
    if cam is None or cam.type != 'CAMERA':
        return False
    bg = photo_background(cam)
    if bg is None:
        return False
    cam.data.background_images.remove(bg)
    return True


def get_opacity(self):
    cam = self.id_data.camera
    bg = photo_background(cam) if cam is not None and cam.type == 'CAMERA' else None
    return bg.alpha if bg is not None else self.get("_opacity", 0.6)


def set_opacity(self, value):
    self["_opacity"] = value
    cam = self.id_data.camera
    bg = photo_background(cam) if cam is not None and cam.type == 'CAMERA' else None
    if bg is not None:
        bg.alpha = value


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

def _has_photo(context):
    scene = context.scene
    cam = scene.camera if scene is not None else None
    return cam is not None and cam.type == 'CAMERA' and photo_background(cam) is not None


class CONCEPTFLOW_OT_photo_load(Operator, ImportHelper):
    bl_idname = "conceptflow.photo_load"
    bl_label = "Load Photo"
    bl_description = "Choose a photo and show it behind the scene camera. The render shape follows the photo"
    bl_options = {'REGISTER', 'UNDO'}

    filter_glob: StringProperty(default="*.jpg;*.jpeg;*.png;*.tif;*.tiff;*.bmp;*.webp;*.exr;*.hdr",
                                options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    @guarded
    def execute(self, context):
        space = context.area.spaces.active if context.area and context.area.type == 'VIEW_3D' else None
        rv3d = space.region_3d if space is not None else None
        try:
            load_photo(context.scene, self.filepath, rv3d)
        except MatchError as exc:
            self.report({'ERROR'}, rpt(str(exc)))
            return {'CANCELLED'}
        self.report({'INFO'}, rpt("Photo loaded. Look through the camera and press Match Camera"))
        return {'FINISHED'}


class CONCEPTFLOW_OT_photo_remove(Operator):
    bl_idname = "conceptflow.photo_remove"
    bl_label = "Remove Photo"
    bl_description = "Take the photo off the camera background. The camera stays where it is"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _has_photo(context)

    @guarded
    def execute(self, context):
        remove_photo(context.scene)
        return {'FINISHED'}


class CONCEPTFLOW_OT_photo_match(Operator):
    bl_idname = "conceptflow.photo_match"
    bl_label = "Match Camera"
    bl_description = ("Set the camera to the perspective of the photo. Draw two lines along one ground "
                      "direction (red), two along the other (green), then click the world origin on the ground")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (_has_photo(context) and context.area is not None and context.area.type == 'VIEW_3D'
                and context.mode == 'OBJECT')

    # -- helpers ----------------------------------------------------------

    def _region(self, context):
        area = context.area
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        return region, area.spaces.active.region_3d if area.spaces.active else None

    def _frame(self, context):
        from .read_views import _frame_rect
        region, rv3d = self._region(context)
        return _frame_rect(context, region, rv3d)

    def _uv(self, context, event):
        """Mouse position as (u, v) in the photo, 0..1, v up. None outside the photo."""
        region, rv3d = self._region(context)
        if region is None or rv3d is None:
            return None
        if not (region.x <= event.mouse_x < region.x + region.width
                and region.y <= event.mouse_y < region.y + region.height):
            return None
        x0, y0, x1, y1 = self._frame(context)
        mx, my = event.mouse_x - region.x, event.mouse_y - region.y
        if x1 - x0 < 1.0 or y1 - y0 < 1.0:
            return None
        u, v = (mx - x0) / (x1 - x0), (my - y0) / (y1 - y0)
        if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
            return None
        return (u, v)

    def _prompt(self, n):
        if n < 2:
            return iface("Red line 1: click two points along a ground direction ({n}/2)").format(n=n)
        if n < 4:
            return iface("Red line 2: click two points on another line of the same direction ({n}/2)").format(n=n - 2)
        if n < 6:
            return iface("Green line 1: click two points along the other ground direction ({n}/2)").format(n=n - 4)
        if n < 8:
            return iface("Green line 2: click two points on another line of that direction ({n}/2)").format(n=n - 6)
        return iface("Click the point on the ground that is the world origin")

    def _status(self, context):
        context.workspace.status_text_set(self._prompt(len(self._points)) + "   " +
                                          iface("(Backspace = undo, Esc = cancel)"))

    # -- overlay ----------------------------------------------------------

    def _draw(self):
        try:
            context = bpy.context
            region, rv3d = context.region, context.region_data
            if region is None or rv3d is None or context.space_data is None:
                return
            x0, y0, x1, y1 = self._frame(context)
            shader = gpu.shader.from_builtin('UNIFORM_COLOR')
            gpu.state.blend_set('ALPHA')
            gpu.state.depth_test_set('NONE')
            shader.bind()

            def px(p):
                return (x0 + p[0] * (x1 - x0), y0 + p[1] * (y1 - y0), 0.0)

            pts = self._points
            groups = [(0, pts[0:4]), (1, pts[4:8])]
            for color_index, group in groups:
                for i in range(0, len(group) - 1, 2):
                    seg = [px(group[i]), px(group[i + 1])]
                    shader.uniform_float("color", LINE_COLORS[color_index])
                    gpu.state.line_width_set(2.0)
                    batch_for_shader(shader, 'LINES', {"pos": seg}).draw(shader)
            if len(pts) % 2 == 1 and len(pts) < 8 and self._mouse is not None:
                shader.uniform_float("color", LINE_COLORS[0 if len(pts) < 4 else 1])
                gpu.state.line_width_set(1.0)
                batch_for_shader(shader, 'LINES', {"pos": [px(pts[-1]), px(self._mouse)]}).draw(shader)
            gpu.state.point_size_set(8.0)
            for index, p in enumerate(pts):
                color = LINE_COLORS[2] if index == 8 else LINE_COLORS[0 if index < 4 else 1]
                shader.uniform_float("color", color)
                batch_for_shader(shader, 'POINTS', {"pos": [px(p)]}).draw(shader)
            gpu.state.line_width_set(1.0)
            gpu.state.blend_set('NONE')
        except Exception:
            pass

    # -- operator ---------------------------------------------------------

    def invoke(self, context, event):
        region, rv3d = self._region(context)
        if rv3d is None:
            return {'CANCELLED'}
        if rv3d.view_perspective != 'CAMERA':
            rv3d.view_perspective = 'CAMERA'
        self._points = []
        self._mouse = None
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self._draw, (), 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        self._status(context)
        return {'RUNNING_MODAL'}

    def _finish(self, context, result):
        if self._handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        context.workspace.status_text_set(None)
        for area in context.screen.areas:
            area.tag_redraw()
        return result

    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except MatchError as exc:
            self.report({'ERROR'}, rpt(str(exc)))
            return self._finish(context, {'CANCELLED'})
        except Exception:
            common.last_error = traceback.format_exc()
            traceback.print_exc()
            self.report({'ERROR'}, rpt("Match Camera stopped because of an error"))
            return self._finish(context, {'CANCELLED'})

    def _modal(self, context, event):
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'} or (
                event.type.startswith('NUMPAD_') and event.type != 'NUMPAD_ENTER'):
            return {'PASS_THROUGH'}
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            return self._finish(context, {'CANCELLED'})
        if event.type == 'BACK_SPACE' and event.value == 'PRESS':
            if self._points:
                self._points.pop()
            self._status(context)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE':
            self._mouse = self._uv(context, event)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            uv = self._uv(context, event)
            if uv is None:
                return {'PASS_THROUGH'}
            self._points.append(uv)
            self._status(context)
            context.area.tag_redraw()
            if len(self._points) == POINTS_NEEDED:
                self._solve(context)
                return self._finish(context, {'FINISHED'})
        return {'RUNNING_MODAL'}

    def _solve(self, context):
        scene = context.scene
        cam = scene.camera
        bg = photo_background(cam)
        width, height = bg.image.size

        def centred(uv):
            return ((uv[0] - 0.5) * width, (uv[1] - 0.5) * height)

        p = [centred(uv) for uv in self._points]
        shot = scene.conceptflow.photo
        f, rotation, location = solve_camera(
            [(p[0], p[1]), (p[2], p[3])], [(p[4], p[5]), (p[6], p[7])], p[8], shot.camera_height, shot.flip)
        lens = lens_from_pixels(f, width, height, cam.data.sensor_width)
        from . import branch_tree
        branch_tree.checkpoint(scene)  # keep the camera as it was
        cam.matrix_world = Matrix.Translation(location) @ rotation.to_4x4()
        if cam.data.library is None:
            cam.data.type = 'PERSP'
            cam.data.shift_x = 0.0
            cam.data.shift_y = 0.0
            cam.data.lens = min(max(lens, 1.0), 5000.0)
        fov = math.degrees(2.0 * math.atan(cam.data.sensor_width / (2.0 * max(cam.data.lens, 1e-3))))
        self.report({'INFO'}, rpt("Camera matched: {lens} mm lens, field of view {fov} degrees, "
                                  "{height} m high").format(lens="{:.1f}".format(cam.data.lens),
                                                            fov="{:.0f}".format(fov),
                                                            height="{:.2f}".format(location.z)))


classes = (
    CONCEPTFLOW_OT_photo_load,
    CONCEPTFLOW_OT_photo_remove,
    CONCEPTFLOW_OT_photo_match,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
