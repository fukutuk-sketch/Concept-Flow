# SPDX-License-Identifier: GPL-3.0-or-later
# ConceptFlow for Blender
"""Read Views: one-touch ways to read a picture (values, silhouette, thirds, blur).

Everything is drawn as an overlay on the 3D viewport. The scene is never changed and
nothing is scored: the views only show what is there, the judgement stays with you.

* Grayscale / Silhouette / Blur: the viewport is drawn into an off-screen buffer
  (GPUOffScreen.draw_view3d) and shown again through a small shader.
* Thirds: guide lines over the camera frame (or over the whole view when not looking
  through the camera).
* The settings are not saved in the .blend, so a file always opens with the views off.
"""

import traceback

import bpy
import gpu
from bpy.props import EnumProperty
from bpy.types import Operator
from gpu_extras.batch import batch_for_shader

from . import common
from .common import guarded, tip

VIEW_ITEMS = (
    ('GRAYSCALE', "Grayscale", "Show only the values (light and dark) to check the picture reads without color"),
    ('SILHOUETTE', "Silhouette", "Turn the picture into black and white shapes to check the main shapes"),
    ('THIRDS', "Thirds", "Show rule-of-thirds guide lines on the camera frame"),
    ('BLUR', "Blur", "Blur the picture to check the big masses of light and dark"),
)
VIEW_ICONS = {
    'GRAYSCALE': 'IMAGE_ALPHA',
    'SILHOUETTE': 'MATSHADERBALL',
    'THIRDS': 'MESH_GRID',
    'BLUR': 'SHADING_RENDERED',
}
FILTER_MODE = {'GRAYSCALE': 1, 'SILHOUETTE': 2, 'BLUR': 3}
RENDER_SCALE = {'GRAYSCALE': 1.0, 'SILHOUETTE': 1.0, 'BLUR': 0.5}

_VERT = """
void main() {
    uv = texCoord;
    gl_Position = vec4(pos, 0.0, 1.0);
}
"""

_FRAG = """
float luma(vec3 c) { return dot(c, vec3(0.2126, 0.7152, 0.0722)); }

void main() {
    vec3 c;
    if (mode == 3) {
        vec3 sum = vec3(0.0);
        float total = 0.0;
        for (int x = -3; x <= 3; x++) {
            for (int y = -3; y <= 3; y++) {
                float w = exp(-float(x * x + y * y) / 6.0);
                sum += texture(image, uv + vec2(float(x), float(y)) * texel * radius).rgb * w;
                total += w;
            }
        }
        c = sum / total;
    } else {
        c = texture(image, uv).rgb;
    }
    if (mode == 1) {
        c = vec3(luma(c));
    } else if (mode == 2) {
        c = vec3(luma(c) > threshold ? 1.0 : 0.0);
    }
    fragColor = vec4(c, 1.0);
}
"""

_handle = None
_shader = None
_offscreen = None
_offscreen_size = (0, 0)
error = ""  # last drawing problem, shown in the panel


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def _get_shader():
    global _shader
    if _shader is None:
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant('INT', "mode")
        info.push_constant('FLOAT', "threshold")
        info.push_constant('FLOAT', "radius")
        info.push_constant('VEC2', "texel")
        info.sampler(0, 'FLOAT_2D', "image")
        info.vertex_in(0, 'VEC2', "pos")
        info.vertex_in(1, 'VEC2', "texCoord")
        iface = gpu.types.GPUStageInterfaceInfo("cf_read_iface")
        iface.smooth('VEC2', "uv")
        info.vertex_out(iface)
        info.fragment_out(0, 'VEC4', "fragColor")
        info.vertex_source(_VERT)
        info.fragment_source(_FRAG)
        _shader = gpu.shader.create_from_info(info)
    return _shader


def _free_offscreen():
    global _offscreen, _offscreen_size
    if _offscreen is not None:
        try:
            _offscreen.free()
        except Exception:
            pass
    _offscreen = None
    _offscreen_size = (0, 0)


def _draw_filter(context, rv, region, space, rv3d):
    global _offscreen, _offscreen_size
    scale = RENDER_SCALE[rv.filter]
    size = (max(int(region.width * scale), 16), max(int(region.height * scale), 16))
    if _offscreen is None or _offscreen_size != size:
        _free_offscreen()
        _offscreen = gpu.types.GPUOffScreen(*size)
        _offscreen_size = size
    _offscreen.draw_view3d(context.scene, context.view_layer, space, region,
                           rv3d.view_matrix, rv3d.window_matrix,
                           do_color_management=True, draw_background=True)
    shader = _get_shader()
    batch = batch_for_shader(shader, 'TRI_FAN', {
        "pos": ((-1, -1), (1, -1), (1, 1), (-1, 1)),
        "texCoord": ((0, 0), (1, 0), (1, 1), (0, 1)),
    })
    gpu.state.blend_set('NONE')
    gpu.state.depth_test_set('NONE')
    shader.bind()
    shader.uniform_sampler("image", _offscreen.texture_color)
    shader.uniform_int("mode", FILTER_MODE[rv.filter])
    shader.uniform_float("threshold", rv.threshold)
    shader.uniform_float("radius", rv.blur_amount)
    shader.uniform_float("texel", (1.0 / size[0], 1.0 / size[1]))
    batch.draw(shader)


def _frame_rect(context, region, rv3d):
    """(x0, y0, x1, y1) of the camera frame in region pixels, or the whole region."""
    scene = context.scene
    cam = scene.camera
    if rv3d.view_perspective == 'CAMERA' and cam is not None and cam.type == 'CAMERA':
        from bpy_extras.view3d_utils import location_3d_to_region_2d
        pts = [location_3d_to_region_2d(region, rv3d, cam.matrix_world @ c) for c in cam.data.view_frame(scene=scene)]
        if all(p is not None for p in pts):
            xs, ys = [p.x for p in pts], [p.y for p in pts]
            return min(xs), min(ys), max(xs), max(ys)
    return 0.0, 0.0, float(region.width), float(region.height)


def _draw_thirds(context, region, rv3d):
    x0, y0, x1, y1 = _frame_rect(context, region, rv3d)
    w, h = x1 - x0, y1 - y0
    lines = []
    for k in (1, 2):
        lines += [(x0 + w * k / 3, y0, 0), (x0 + w * k / 3, y1, 0), (x0, y0 + h * k / 3, 0), (x1, y0 + h * k / 3, 0)]
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'LINES', {"pos": lines})
    gpu.state.blend_set('ALPHA')
    shader.bind()
    for width, color in ((3.0, (0.0, 0.0, 0.0, 0.55)), (1.2, (1.0, 1.0, 1.0, 0.85))):
        gpu.state.line_width_set(width)
        shader.uniform_float("color", color)
        batch.draw(shader)
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')


def _draw():
    global error
    context = bpy.context
    region, rv3d, space = context.region, context.region_data, context.space_data
    scene = context.scene
    if scene is None or region is None or rv3d is None or space is None or space.type != 'VIEW_3D':
        return
    rv = scene.conceptflow.read_views
    if rv.filter == 'OFF' and not rv.thirds:
        return
    try:
        if rv.filter != 'OFF':
            _draw_filter(context, rv, region, space, rv3d)
        if rv.thirds:
            _draw_thirds(context, region, rv3d)
        error = ""
    except Exception:
        error = traceback.format_exc()
        common.last_error = error
        traceback.print_exc()
        _remove_handler()


# ---------------------------------------------------------------------------
# Handler management
# ---------------------------------------------------------------------------

def _add_handler():
    global _handle
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')


def _remove_handler():
    global _handle
    if _handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        except Exception:
            pass
        _handle = None
    _free_offscreen()


def _any_active():
    return any(s.conceptflow.read_views.filter != 'OFF' or s.conceptflow.read_views.thirds
               for s in bpy.data.scenes)


def redraw_all():
    wm = bpy.context.window_manager
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def on_changed():
    """Update callback of the settings: keep the overlay in step with them."""
    global error
    if _any_active():
        if _handle is None:
            error = ""
        _add_handler()
    else:
        _remove_handler()
    redraw_all()


def active_label(rv):
    names = {ident: name for ident, name, _d in VIEW_ITEMS}
    parts = []
    if rv.filter != 'OFF':
        parts.append(names[rv.filter])
    if rv.thirds:
        parts.append(names['THIRDS'])
    return " + ".join(parts)


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class CONCEPTFLOW_OT_read_view(Operator):
    bl_idname = "conceptflow.read_view"
    bl_label = "Read View"
    bl_description = "Show or hide a read view on the 3D viewport. Nothing in the scene is changed"
    bl_options = {'REGISTER'}

    view: EnumProperty(name="View", items=VIEW_ITEMS + (('OFF', "Off", "Turn all read views off"),),
                       default='OFF')

    @classmethod
    def description(cls, context, properties):
        if properties is not None:
            if properties.view == 'OFF':
                return tip("Turn all read views off")
            for ident, _name, desc in VIEW_ITEMS:
                if ident == properties.view:
                    return tip(desc) + "\n" + tip(cls.bl_description)
        return tip(cls.bl_description)

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    @guarded
    def execute(self, context):
        rv = context.scene.conceptflow.read_views
        if self.view == 'OFF':
            rv.thirds = False
            rv.filter = 'OFF'
        elif self.view == 'THIRDS':
            rv.thirds = not rv.thirds
        else:
            rv.filter = 'OFF' if rv.filter == self.view else self.view
        on_changed()
        return {'FINISHED'}


classes = (CONCEPTFLOW_OT_read_view,)


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
