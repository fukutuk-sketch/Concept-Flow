# SPDX-License-Identifier: GPL-3.0-or-later
# ConceptFlow for Blender
"""More graybox tools: draw walls by clicking, props and furniture, stair variants and streets.

Everything uses the same part types as the Graybox panel (WALL, FLOOR, STAIRS, BLOCK), so
Asset Swap and the Branch Tree work on these parts too.
"""

import math
import random
import uuid
from types import SimpleNamespace

import bmesh
import bpy
import gpu
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy.types import Operator
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Vector, geometry

from . import common, graybox
from .common import guarded, iface, rpt, tip
from .graybox import new_custom_part, new_part


# ---------------------------------------------------------------------------
# Draw walls with the mouse
# ---------------------------------------------------------------------------

def opening_params(draw):
    """Draw settings in the form graybox.wall_openings() expects."""
    return SimpleNamespace(
        wall_thickness=draw.thickness, window_spacing=draw.window_spacing, doors=draw.doors,
        door_width=draw.door_width, door_height=draw.door_height, windows=draw.windows,
        window_width=draw.window_width, window_height=draw.window_height, sill_height=draw.sill_height,
        open_side='NONE', balcony=False)


def snap_value(value, step):
    return round(value / step) * step if step > 0.0 else value


def lock_angle(origin, point, degrees=15.0):
    """Turn ``point`` around ``origin`` to the nearest multiple of ``degrees``."""
    d = point - origin
    length = d.length
    if length < 1e-6:
        return point
    step = math.radians(degrees)
    ang = round(math.atan2(d.y, d.x) / step) * step
    return origin + Vector((math.cos(ang) * length, math.sin(ang) * length, 0.0))


def polygon_area(points):
    return 0.5 * sum(a.x * b.y - b.x * a.y for a, b in zip(points, points[1:] + points[:1]))


def floor_polygon_mesh(name, points, thickness):
    """A slab from the polygon ``points`` (world XY). Its top is at z = 0."""
    pts = list(points)
    if polygon_area(pts) < 0.0:
        pts.reverse()
    bm = bmesh.new()
    top = [bm.verts.new((p.x, p.y, 0.0)) for p in pts]
    bottom = [bm.verts.new((p.x, p.y, -thickness)) for p in pts]
    bm.faces.new(top)
    bm.faces.new(list(reversed(bottom)))
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((bottom[i], bottom[j], top[j], top[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return common.mesh_from_bm(name, bm)


class CONCEPTFLOW_OT_draw_walls(Operator):
    bl_idname = "conceptflow.draw_walls"
    bl_label = "Draw Walls"
    bl_description = ("Click in the 3D Viewport to draw walls point by point. Enter or right click "
                      "finishes, C closes the room, Backspace removes the last wall, Esc cancels")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.scene is not None and context.mode == 'OBJECT'
                and context.area is not None and context.area.type == 'VIEW_3D')

    # -- helpers ----------------------------------------------------------

    def _window_region(self, context):
        area = context.area
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        return region, area.spaces.active.region_3d if area.spaces.active else None

    def _inside(self, region, event):
        return (region.x <= event.mouse_x < region.x + region.width
                and region.y <= event.mouse_y < region.y + region.height)

    def _world_point(self, context, event):
        from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
        region, rv3d = self._window_region(context)
        if region is None or rv3d is None or not self._inside(region, event):
            return None
        coord = (event.mouse_x - region.x, event.mouse_y - region.y)
        origin = region_2d_to_origin_3d(region, rv3d, coord)
        direction = region_2d_to_vector_3d(region, rv3d, coord)
        hit = geometry.intersect_line_plane(origin, origin + direction * 1000.0,
                                            Vector((0.0, 0.0, self._z)), Vector((0.0, 0.0, 1.0)))
        if hit is None:
            return None
        # a point behind the camera is no use
        if (hit - origin).dot(direction) < 0.0:
            return None
        return Vector((hit.x, hit.y, self._z))

    def _snapped(self, point, event):
        settings = self._settings
        if self._points and event.shift:
            return lock_angle(self._points[-1], point)
        if not event.ctrl:
            step = settings.snap
            return Vector((snap_value(point.x, step), snap_value(point.y, step), point.z))
        return point

    def _ensure_building(self, context):
        if self._root is not None:
            return
        scene = context.scene
        self._building = uuid.uuid4().hex[:8]
        gray = graybox.graybox_collection(scene)
        self._coll = bpy.data.collections.new("CF_Building")
        self._coll["cf_role"] = "BUILDING"
        gray.children.link(self._coll)
        self._root = bpy.data.objects.new("CF_Building", None)
        self._root.empty_display_type = 'PLAIN_AXES'
        self._root.conceptflow.building = self._building
        self._coll.objects.link(self._root)
        self._root.matrix_world = Matrix.Translation((self._points[0].x, self._points[0].y, self._z))

    def _add_wall(self, context, a, b):
        length = (b - a).length
        if length < 0.05:
            return None
        self._ensure_building(context)
        s = self._settings
        d = b - a
        mid = (a + b) / 2.0
        m = Matrix.Translation((mid.x, mid.y, self._z)) @ Matrix.Rotation(math.atan2(d.y, d.x), 4, 'Z')
        return new_part('WALL', (length + s.thickness, s.thickness, s.height), m, self._coll,
                        parent=self._root, building=self._building)

    def _add_floor(self, context):
        s = self._settings
        pts = self._points
        if len(pts) < 3 or not (s.floor_slab or s.ceiling):
            return
        xs, ys = [p.x for p in pts], [p.y for p in pts]
        size = (max(xs) - min(xs), max(ys) - min(ys), s.slab)
        flat = []
        for p in pts:
            flat += [p.x, p.y]
        for enabled, name, z in ((s.floor_slab, "CF_Floor", self._z),
                                 (s.ceiling, "CF_Ceiling", self._z + s.height + s.slab)):
            if not enabled:
                continue
            mesh = floor_polygon_mesh(name, pts, s.slab)
            ob = new_custom_part('FLOOR', mesh, size, Matrix.Translation((0.0, 0.0, z)), self._coll,
                                 slots=['FLOOR'], parent=self._root, building=self._building, name=name)
            ob["cf_poly"] = flat
            self._extra.append(ob)

    def _add_openings(self, context):
        """Windows along the walls and a door on the longest wall, from the style settings."""
        s = self._settings
        p = opening_params(s)
        if not self._walls:
            return
        longest = max(self._walls, key=lambda w: w.conceptflow.size[0])
        result = []
        for wall in self._walls:
            length = wall.conceptflow.size[0] - s.thickness
            if length < 1.0:
                result.append(wall)
                continue
            side = 'FRONT' if wall is longest else 'RIGHT'
            openings = graybox.wall_openings(p, wall.matrix_world, length, s.height, 0, side,
                                             wall.users_collection[0], self._building)
            for op in openings:
                world = op.matrix_world.copy()
                op.parent = self._root
                op.matrix_world = world
            if openings:
                _cut, pieces, _skipped = graybox.cut_openings([wall], openings)
                result += pieces
            else:
                result.append(wall)
        self._walls = result

    def _remove_all(self):
        for ob in list(self._walls) + list(self._extra):
            if ob is not None and ob.name in bpy.data.objects:
                common.remove_object(ob)
        if self._root is not None and self._root.name in bpy.data.objects:
            bpy.data.objects.remove(self._root, do_unlink=True)
        if self._coll is not None and self._coll.name in bpy.data.collections:
            bpy.data.collections.remove(self._coll)
        self._walls, self._extra, self._root, self._coll = [], [], None, None

    def _status(self, context, point=None):
        text = iface("Draw Walls: click = point, Enter / right click = finish, C = close, "
                     "Backspace = undo, Shift = 15° angles, Ctrl = no snap, Esc = cancel")
        if point is not None and self._points:
            text += "   {:.2f} m".format((point - self._points[-1]).length)
        context.workspace.status_text_set(text)

    # -- preview ----------------------------------------------------------

    def _draw_preview(self):
        try:
            pts = list(self._points)
            if self._mouse is not None:
                pts.append(self._mouse)
            if not pts:
                return
            shader = gpu.shader.from_builtin('UNIFORM_COLOR')
            gpu.state.blend_set('ALPHA')
            gpu.state.depth_test_set('NONE')
            lift = Vector((0.0, 0.0, 0.02))
            coords = [tuple(p + lift) for p in pts]
            shader.bind()
            if len(coords) > 1:
                gpu.state.line_width_set(3.0)
                shader.uniform_float("color", (1.0, 0.55, 0.15, 1.0))
                batch_for_shader(shader, 'LINE_STRIP', {"pos": coords}).draw(shader)
            gpu.state.point_size_set(9.0)
            shader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
            batch_for_shader(shader, 'POINTS', {"pos": coords}).draw(shader)
            gpu.state.line_width_set(1.0)
            gpu.state.blend_set('NONE')
        except Exception:
            pass

    def _finish(self, context, cancelled):
        if self._handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        context.workspace.status_text_set(None)
        for area in context.screen.areas:
            area.tag_redraw()
        if cancelled:
            self._remove_all()
            return {'CANCELLED'}
        if not self._walls:
            self._remove_all()
            return {'CANCELLED'}
        if self._settings.auto_openings:
            self._add_openings(context)
        common.select_only(context, [self._root], self._root)
        self.report({'INFO'}, rpt("Drew {count} walls").format(count=len(self._walls)))
        return {'FINISHED'}

    # -- operator ---------------------------------------------------------

    def invoke(self, context, event):
        scene = context.scene
        self._settings = scene.conceptflow.draw
        self._z = scene.cursor.location.z
        self._points = []
        self._walls = []
        self._extra = []
        self._root = None
        self._coll = None
        self._building = ""
        self._mouse = None
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self._draw_preview, (), 'WINDOW', 'POST_VIEW')
        context.window_manager.modal_handler_add(self)
        self._status(context)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except Exception:
            import traceback
            common.last_error = traceback.format_exc()
            traceback.print_exc()
            self.report({'ERROR'}, rpt("Draw Walls stopped because of an error"))
            return self._finish(context, True)

    def _modal(self, context, event):
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'WHEELINMOUSE', 'WHEELOUTMOUSE'} \
                or event.type.startswith(('NUMPAD_', 'TRACKPAD', 'NDOF')) and event.type != 'NUMPAD_ENTER':
            return {'PASS_THROUGH'}
        if event.type == 'ESC' and event.value == 'PRESS':
            return self._finish(context, True)
        if event.type in {'RET', 'NUMPAD_ENTER', 'RIGHTMOUSE'} and event.value == 'PRESS':
            return self._finish(context, False)
        if event.type == 'BACK_SPACE' and event.value == 'PRESS':
            if self._walls:
                common.remove_object(self._walls.pop())
            if self._points:
                self._points.pop()
            if not self._walls and self._extra:
                for ob in self._extra:
                    common.remove_object(ob)
                self._extra = []
            self._status(context)
            return {'RUNNING_MODAL'}
        if event.type == 'C' and event.value == 'PRESS':
            if len(self._points) >= 3:
                wall = self._add_wall(context, self._points[-1], self._points[0])
                if wall is not None:
                    self._walls.append(wall)
                self._add_floor(context)
                return self._finish(context, False)
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE':
            point = self._world_point(context, event)
            self._mouse = self._snapped(point, event) if point is not None else None
            self._status(context, self._mouse)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            point = self._world_point(context, event)
            if point is None:
                return {'PASS_THROUGH'}
            point = self._snapped(point, event)
            if len(self._points) >= 3 and (point - self._points[0]).length < max(self._settings.snap, 0.25) * 0.6:
                wall = self._add_wall(context, self._points[-1], self._points[0])
                if wall is not None:
                    self._walls.append(wall)
                self._add_floor(context)
                return self._finish(context, False)
            if self._points and (point - self._points[-1]).length < 0.05:
                return {'RUNNING_MODAL'}
            self._points.append(point)
            if len(self._points) >= 2:
                wall = self._add_wall(context, self._points[-2], self._points[-1])
                if wall is not None:
                    self._walls.append(wall)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        return {'RUNNING_MODAL'}


# ---------------------------------------------------------------------------
# Props and furniture (all BLOCK parts: origin at the bottom centre, X width, Y depth)
# ---------------------------------------------------------------------------

def _box(bm, cx, cy, z0, sx, sy, sz):
    common.bm_box(bm, (cx - sx / 2.0, cy - sy / 2.0, z0), (cx + sx / 2.0, cy + sy / 2.0, z0 + sz))


def _cylinder(bm, cx, cy, z0, radius, height, segments=16, radius2=None):
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segments,
                          radius1=radius, radius2=radius if radius2 is None else radius2, depth=height,
                          matrix=Matrix.Translation((cx, cy, z0 + height / 2.0)))


def _legs(bm, sx, sy, height, leg, inset=0.0):
    for ix in (-1.0, 1.0):
        for iy in (-1.0, 1.0):
            _box(bm, ix * (sx / 2.0 - leg / 2.0 - inset), iy * (sy / 2.0 - leg / 2.0 - inset), 0.0, leg, leg, height)


def _table(bm, sx, sy, sz):
    top = min(0.06, sz * 0.12)
    _legs(bm, sx, sy, sz - top, min(0.07, sx * 0.08, sy * 0.1), 0.03)
    _box(bm, 0.0, 0.0, sz - top, sx, sy, top)


def _chair(bm, sx, sy, sz):
    seat_h = sz * 0.5
    _legs(bm, sx, sy, seat_h, min(0.05, sx * 0.12))
    _box(bm, 0.0, 0.0, seat_h, sx, sy, 0.05)
    _box(bm, 0.0, sy / 2.0 - 0.02, seat_h + 0.05, sx, 0.04, sz - seat_h - 0.05)


def _bed(bm, sx, sy, sz):
    _box(bm, 0.0, 0.0, 0.1, sx, sy, sz * 0.35)
    _box(bm, 0.0, -0.02, 0.1 + sz * 0.35, sx - 0.06, sy - 0.1, sz * 0.2)
    _legs(bm, sx, sy, 0.1, 0.08)
    _box(bm, 0.0, sy / 2.0 - 0.04, 0.1, sx, 0.08, sz * 1.5)


def _sofa(bm, sx, sy, sz):
    arm = min(0.18, sx * 0.1)
    _box(bm, 0.0, 0.0, 0.08, sx, sy, sz * 0.4)
    _box(bm, 0.0, sy / 2.0 - 0.12, 0.08 + sz * 0.4, sx, 0.24, sz * 0.5 - 0.08)
    for side in (-1.0, 1.0):
        _box(bm, side * (sx / 2.0 - arm / 2.0), -0.04, 0.08 + sz * 0.4, arm, sy - 0.1, sz * 0.2)
    _box(bm, 0.0, -0.1, 0.08 + sz * 0.4, sx - 2 * arm, sy - 0.3, 0.1)


def _shelf(bm, sx, sy, sz):
    t = 0.03
    for side in (-1.0, 1.0):
        _box(bm, side * (sx / 2.0 - t / 2.0), 0.0, 0.0, t, sy, sz)
    _box(bm, 0.0, sy / 2.0 - 0.01, 0.0, sx - 2 * t, 0.02, sz)
    levels = 5
    for i in range(levels + 1):
        _box(bm, 0.0, 0.0, i * (sz - t) / levels, sx - 2 * t, sy - 0.02, t)


def _counter(bm, sx, sy, sz):
    _box(bm, 0.0, 0.0, 0.0, sx - 0.06, sy - 0.08, sz - 0.05)
    _box(bm, 0.0, -0.02, sz - 0.05, sx, sy, 0.05)


def _barrel(bm, sx, sy, sz):
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=20, radius1=0.5, radius2=0.5,
                          depth=1.0, matrix=Matrix.Translation((0.0, 0.0, 0.5)))
    bmesh.ops.scale(bm, vec=(sx, sy, sz), verts=bm.verts[:])


def _crate(bm, sx, sy, sz):
    _box(bm, 0.0, 0.0, 0.0, sx, sy, sz)


def _lamp_post(bm, sx, sy, sz):
    _cylinder(bm, 0.0, 0.0, 0.0, 0.12, 0.2, 12)
    _cylinder(bm, 0.0, 0.0, 0.2, 0.05, sz - 0.5, 10)
    _box(bm, 0.0, 0.0, sz - 0.3, 0.35, 0.35, 0.3)
    _box(bm, 0.0, 0.0, sz, 0.45, 0.45, 0.05)


def _tree(bm, sx, sy, sz):
    trunk = sz * 0.35
    _cylinder(bm, 0.0, 0.0, 0.0, min(sx, sy) * 0.07, trunk, 10)
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=14, radius1=min(sx, sy) / 2.0,
                          radius2=0.0, depth=sz - trunk * 0.7,
                          matrix=Matrix.Translation((0.0, 0.0, trunk * 0.7 + (sz - trunk * 0.7) / 2.0)))


def _fence(bm, sx, sy, sz):
    post = 0.1
    for side in (-1.0, 1.0):
        _box(bm, side * (sx / 2.0 - post / 2.0), 0.0, 0.0, post, sy, sz)
    for z in (sz * 0.3, sz * 0.7):
        _box(bm, 0.0, 0.0, z, sx - 2 * post, sy * 0.6, 0.08)


def _bench(bm, sx, sy, sz):
    seat_h = sz * 0.5
    for side in (-1.0, 1.0):
        _box(bm, side * (sx / 2.0 - 0.06), 0.0, 0.0, 0.08, sy, seat_h)
    _box(bm, 0.0, 0.0, seat_h, sx, sy, 0.06)
    _box(bm, 0.0, sy / 2.0 - 0.03, seat_h + 0.06, sx, 0.05, sz - seat_h - 0.06)


def _fountain(bm, sx, sy, sz):
    r = min(sx, sy) / 2.0
    _cylinder(bm, 0.0, 0.0, 0.0, r, sz * 0.35, 24)
    _cylinder(bm, 0.0, 0.0, sz * 0.35, r * 0.2, sz * 0.4, 12)
    _cylinder(bm, 0.0, 0.0, sz * 0.75, r * 0.5, sz * 0.12, 20, radius2=r * 0.6)


def _car(bm, sx, sy, sz):
    wheel = sz * 0.28
    _box(bm, 0.0, 0.0, wheel * 0.6, sx, sy, sz * 0.42)
    _box(bm, -sx * 0.05, 0.0, wheel * 0.6 + sz * 0.42, sx * 0.55, sy * 0.92, sz * 0.36)
    for ix in (-1.0, 1.0):
        for iy in (-1.0, 1.0):
            bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=12, radius1=wheel,
                                  radius2=wheel, depth=0.22,
                                  matrix=Matrix.Translation((ix * sx * 0.3, iy * (sy / 2.0 - 0.05), wheel))
                                  @ Matrix.Rotation(math.pi / 2.0, 4, 'X'))


# key: (label, default size (X, Y, Z), builder, description)
PROPS = {
    'TABLE': ("Table", (1.4, 0.8, 0.75), _table, "A table"),
    'CHAIR': ("Chair", (0.45, 0.45, 0.9), _chair, "A chair. Its back is on the +Y side"),
    'BED': ("Bed", (1.6, 2.0, 0.6), _bed, "A bed. The headboard is on the +Y side"),
    'SOFA': ("Sofa", (2.0, 0.9, 0.85), _sofa, "A sofa. Its back is on the +Y side"),
    'SHELF': ("Shelf", (1.2, 0.35, 1.9), _shelf, "A bookshelf"),
    'COUNTER': ("Counter", (2.0, 0.6, 0.95), _counter, "A counter or kitchen block"),
    'BARREL': ("Barrel", (0.6, 0.6, 0.9), _barrel, "A barrel"),
    'CRATE': ("Crate", (0.8, 0.8, 0.8), _crate, "A crate"),
    'BENCH': ("Bench", (1.6, 0.5, 0.85), _bench, "A bench. Its back is on the +Y side"),
    'LAMP_POST': ("Lamp Post", (0.45, 0.45, 4.0), _lamp_post, "A street lamp"),
    'TREE': ("Tree", (2.0, 2.0, 4.5), _tree, "A simple tree"),
    'FENCE': ("Fence", (2.0, 0.1, 1.2), _fence, "A fence section"),
    'CAR': ("Car", (4.2, 1.8, 1.5), _car, "A car block. The front is on the +X side"),
    'FOUNTAIN': ("Fountain", (3.0, 3.0, 1.6), _fountain, "A round fountain for a plaza"),
}
PROP_ORDER = ('TABLE', 'CHAIR', 'BED', 'SOFA', 'SHELF', 'COUNTER', 'BENCH', 'BARREL', 'CRATE',
              'FENCE', 'LAMP_POST', 'TREE', 'FOUNTAIN', 'CAR')
PROP_ICONS = {
    'TABLE': 'MESH_PLANE', 'CHAIR': 'MESH_CUBE', 'BED': 'MESH_PLANE', 'SOFA': 'MESH_CUBE',
    'SHELF': 'MESH_GRID', 'COUNTER': 'MESH_CUBE', 'BENCH': 'MESH_PLANE', 'BARREL': 'MESH_CYLINDER',
    'CRATE': 'MESH_CUBE', 'FENCE': 'MESH_GRID', 'LAMP_POST': 'LIGHT_POINT', 'TREE': 'MESH_CONE',
    'CAR': 'AUTO', 'FOUNTAIN': 'MESH_CIRCLE',
}
PROP_ITEMS = [(key, PROPS[key][0], PROPS[key][3]) for key in PROP_ORDER]


def prop_mesh(kind, size, name):
    bm = bmesh.new()
    PROPS[kind][2](bm, *size)
    return common.mesh_from_bm(name, bm)


class CONCEPTFLOW_OT_add_prop(Operator):
    bl_idname = "conceptflow.add_prop"
    bl_label = "Add Prop"
    bl_description = "Add a furniture or street prop block at the 3D cursor"
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(name="Prop", items=PROP_ITEMS, default='TABLE')
    scale: FloatProperty(name="Scale", description="Size of the prop compared to the normal size",
                         min=0.1, soft_max=5.0, default=1.0)

    @classmethod
    def description(cls, context, properties):
        if properties is not None and properties.kind in PROPS:
            return tip(PROPS[properties.kind][3]) + "\n" + tip(cls.bl_description)
        return tip(cls.bl_description)

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.mode == 'OBJECT'

    @guarded
    def execute(self, context):
        scene = context.scene
        label, base, _builder, _desc = PROPS[self.kind]
        size = tuple(v * self.scale for v in base)
        mesh = prop_mesh(self.kind, size, "CF_" + label.replace(" ", ""))
        obj = new_custom_part('BLOCK', mesh, size, graybox._cursor_matrix(scene),
                              graybox.graybox_collection(scene), slots=['BLOCK'],
                              name="CF_" + label.replace(" ", ""))
        obj["cf_prop"] = self.kind
        common.select_only(context, [obj], obj)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Stair variants
# ---------------------------------------------------------------------------

STAIR_ITEMS = [
    ('STRAIGHT', "Straight", "A straight flight of stairs"),
    ('L_SHAPE', "L-Shaped", "Two flights with a landing and a 90° turn"),
    ('U_SHAPE', "U-Shaped", "Two flights with a landing and a 180° turn"),
    ('SPIRAL', "Spiral", "Round stairs around a pole"),
]
STEP_RUN = 0.28
STEP_RISE = graybox.STEP_RISE
LANDING_THICKNESS = 0.15


def _flight_size(width, height):
    n = max(2, int(round(height / STEP_RISE)))
    return n, (width, n * STEP_RUN, height)


def spiral_mesh(name, radius, height, steps, degrees_per_step=22.5):
    bm = bmesh.new()
    rise = height / steps
    thick = min(0.16, rise * 1.2)
    r_in = min(0.15, radius * 0.3)
    for i in range(steps):
        a0 = math.radians(degrees_per_step * i)
        a1 = math.radians(degrees_per_step * (i + 1)) + math.radians(1.5)
        z1 = (i + 1) * rise
        z0 = z1 - thick
        corners = []
        for z in (z0, z1):
            ring = [bm.verts.new((math.cos(a) * r, math.sin(a) * r, z)) for r, a in
                    ((r_in, a0), (radius, a0), (radius, a1), (r_in, a1))]
            corners.append(ring)
        lo, hi = corners
        bm.faces.new(list(reversed(lo)))
        bm.faces.new(hi)
        for k in range(4):
            j = (k + 1) % 4
            bm.faces.new((lo[k], lo[j], hi[j], hi[k]))
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=12, radius1=r_in, radius2=r_in,
                          depth=height, matrix=Matrix.Translation((0.0, 0.0, height / 2.0)))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return common.mesh_from_bm(name, bm)


class CONCEPTFLOW_OT_add_stairs(Operator):
    bl_idname = "conceptflow.add_stairs"
    bl_label = "Add Stairs"
    bl_description = "Add straight, L-shaped, U-shaped or spiral stairs at the 3D cursor"
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(name="Shape", items=STAIR_ITEMS, default='L_SHAPE')
    width: FloatProperty(name="Width", min=0.5, soft_max=5.0, default=1.2, unit='LENGTH')
    height: FloatProperty(name="Height", description="Total height climbed", min=0.5, soft_max=20.0,
                          default=3.2, unit='LENGTH')

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.mode == 'OBJECT'

    def _part(self, scene, coll, root, kind, size, matrix, **kw):
        return new_part(kind, size, matrix, coll, parent=root, **kw)

    @guarded
    def execute(self, context):
        scene = context.scene
        coll = graybox.graybox_collection(scene)
        root = bpy.data.objects.new("CF_Stairs", None)
        root.empty_display_type = 'PLAIN_AXES'
        coll.objects.link(root)
        w, h = self.width, self.height
        made = []
        if self.kind == 'STRAIGHT':
            _n, size = _flight_size(w, h)
            made.append(self._part(scene, coll, root, 'STAIRS', size,
                                   Matrix.Translation((0.0, size[1] / 2.0, 0.0))))
        elif self.kind == 'L_SHAPE':
            h1 = h / 2.0
            n1, s1 = _flight_size(w, h1)
            n2, s2 = _flight_size(w, h - h1)
            made.append(self._part(scene, coll, root, 'STAIRS', s1,
                                   Matrix.Translation((0.0, s1[1] / 2.0, 0.0))))
            made.append(self._part(scene, coll, root, 'FLOOR', (w, w, LANDING_THICKNESS),
                                   Matrix.Translation((0.0, s1[1] + w / 2.0, h1)), name="CF_Landing"))
            made.append(self._part(scene, coll, root, 'STAIRS', s2,
                                   Matrix.Translation((-w / 2.0 - s2[1] / 2.0, s1[1] + w / 2.0, h1))
                                   @ Matrix.Rotation(math.pi / 2.0, 4, 'Z')))
        elif self.kind == 'U_SHAPE':
            h1 = h / 2.0
            n1, s1 = _flight_size(w, h1)
            n2, s2 = _flight_size(w, h - h1)
            gap = 0.05
            x = w / 2.0 + gap / 2.0
            made.append(self._part(scene, coll, root, 'STAIRS', s1,
                                   Matrix.Translation((-x, s1[1] / 2.0, 0.0))))
            made.append(self._part(scene, coll, root, 'FLOOR', (2 * w + gap, w, LANDING_THICKNESS),
                                   Matrix.Translation((0.0, s1[1] + w / 2.0, h1)), name="CF_Landing"))
            made.append(self._part(scene, coll, root, 'STAIRS', s2,
                                   Matrix.Translation((x, s1[1] - s2[1] / 2.0, h1))
                                   @ Matrix.Rotation(math.pi, 4, 'Z')))
        else:
            steps = max(6, int(round(h / STEP_RISE)))
            radius = max(w, 0.8)
            mesh = spiral_mesh("CF_Spiral", radius, h, steps)
            made.append(new_custom_part('STAIRS', mesh, (radius * 2, radius * 2, h), Matrix.Identity(4),
                                        coll, slots=['STAIRS'], parent=root, name="CF_Spiral"))
        root.matrix_world = graybox._cursor_matrix(scene)
        common.select_only(context, [root], root)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Streets: rows of buildings
# ---------------------------------------------------------------------------

STREET_LAYOUTS = [
    ('STREET', "Street", "Two rows of buildings along a road"),
    ('ALLEY', "Alley", "A narrow lane between tall buildings that sit at uneven depths"),
    ('PLAZA', "Plaza", "A square with buildings on all four sides facing a fountain"),
]


class CONCEPTFLOW_OT_build_street(Operator):
    bl_idname = "conceptflow.build_street"
    bl_label = "Build Street"
    bl_description = ("Build a street, alley or plaza of different buildings at the 3D cursor. "
                      "Heights and widths are random. Every building keeps its own parts")
    bl_options = {'REGISTER', 'UNDO'}

    layout: EnumProperty(name="Layout", items=STREET_LAYOUTS, default='STREET')
    count: IntProperty(name="Buildings", description="Buildings in each row", min=1, max=30, default=5)
    both_sides: BoolProperty(name="Both Sides", description="Add a second row across the road", default=True)
    road: BoolProperty(name="Road", description="Add a road slab between the rows", default=True)
    road_width: FloatProperty(name="Road Width", min=2.0, soft_max=30.0, default=8.0, unit='LENGTH')
    alley_width: FloatProperty(name="Alley Width", min=1.2, soft_max=10.0, default=3.0, unit='LENGTH')
    plaza_size: FloatProperty(name="Plaza Size", min=8.0, soft_max=80.0, default=20.0, unit='LENGTH')
    min_width: FloatProperty(name="Min Width", min=2.0, soft_max=30.0, default=5.0, unit='LENGTH')
    max_width: FloatProperty(name="Max Width", min=2.0, soft_max=40.0, default=9.0, unit='LENGTH')
    depth: FloatProperty(name="Depth", min=3.0, soft_max=40.0, default=8.0, unit='LENGTH')
    min_floors: IntProperty(name="Min Floors", min=1, max=20, default=1)
    max_floors: IntProperty(name="Max Floors", min=1, max=20, default=4)
    gap: FloatProperty(name="Gap", description="Space between buildings. 0 = a continuous row",
                       min=0.0, soft_max=10.0, default=0.0, unit='LENGTH')
    seed: IntProperty(name="Seed", description="Changes the mix of buildings", min=0, default=0)

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.mode == 'OBJECT'

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=340)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "layout")
        col = layout.column(align=True)
        if self.layout != 'PLAZA':
            col.prop(self, "count")
        col.prop(self, "seed")
        col = layout.column(align=True)
        col.prop(self, "min_width")
        col.prop(self, "max_width")
        col.prop(self, "depth")
        col = layout.column(align=True)
        col.prop(self, "min_floors")
        col.prop(self, "max_floors")
        layout.prop(self, "gap")
        if self.layout == 'STREET':
            layout.prop(self, "both_sides")
            row = layout.row()
            row.prop(self, "road")
            sub = row.row()
            sub.active = self.road
            sub.prop(self, "road_width", text="")
        elif self.layout == 'ALLEY':
            layout.prop(self, "alley_width")
        else:
            layout.prop(self, "plaza_size")

    def _params(self, rng, width, depth, floors, style):
        roof_type = rng.choice(('FLAT', 'GABLE', 'GABLE', 'SHED', 'HIP'))
        p = SimpleNamespace(
            width=width, depth=depth, floors=floors, floor_height=3.2, wall_thickness=0.2,
            slab_thickness=0.15, doors=1, door_width=1.0, door_height=2.1, windows=True,
            window_spacing=rng.uniform(2.2, 3.4), window_width=rng.uniform(1.0, 1.4), window_height=1.3,
            sill_height=0.9, roof=True, roof_type=roof_type, roof_pitch=rng.uniform(22.0, 40.0),
            roof_overhang=0.3, balcony=floors >= 2 and rng.random() < 0.4, balcony_depth=1.2,
            pillars=False, open_side='NONE')
        if style.style != 'NONE':
            p.floor_height = max(1.5, style.height + style.slab)
            p.wall_thickness, p.slab_thickness = style.thickness, style.slab
            p.doors, p.door_width, p.door_height = style.doors, style.door_width, style.door_height
            p.windows, p.window_spacing = style.windows, style.window_spacing
            p.window_width, p.window_height, p.sill_height = style.window_width, style.window_height, style.sill_height
            p.roof_type, p.roof_pitch, p.roof_overhang = style.roof_type, style.roof_pitch, style.roof_overhang
        return p

    def _row(self, scene, street, base, rng, to_matrix, style, limit=None, floors_bonus=0, vary_depth=False):
        """Build one row. ``to_matrix(x_centre, width, depth)`` places a building. Returns its length."""
        lo, hi = sorted((self.min_width, self.max_width))
        x = 0.0
        built = 0
        while True:
            width = round(rng.uniform(lo, hi) * 2.0) / 2.0
            if limit is None:
                if built >= self.count:
                    break
            elif x + width > limit + 0.01:
                width = limit - x
                if width < lo * 0.6:
                    break
            floors = rng.randint(min(self.min_floors, self.max_floors), max(self.min_floors, self.max_floors))
            floors += floors_bonus
            depth = self.depth * (rng.uniform(0.6, 1.0) if vary_depth else 1.0)
            p = self._params(rng, width, depth, floors, style)
            graybox.build_building(scene, p, base @ to_matrix(x + width / 2.0, width, depth), parent=street)
            x += width + (self.gap if not vary_depth else rng.uniform(0.0, 0.4))
            built += 1
        return x - (self.gap if not vary_depth else 0.0)

    @guarded
    def execute(self, context):
        scene = context.scene
        style = scene.conceptflow.draw
        rng = random.Random(self.seed)
        base = graybox._cursor_matrix(scene)
        coll = graybox.graybox_collection(scene)
        street = bpy.data.objects.new("CF_" + self.layout.title(), None)
        street.empty_display_type = 'PLAIN_AXES'
        street.empty_display_size = 2.0
        coll.objects.link(street)
        street.matrix_world = base
        D = self.depth
        total = 0
        if self.layout == 'PLAZA':
            S = self.plaza_size
            sides = (
                (lambda xc, w, d: Matrix.Translation((xc - S / 2.0, -S / 2.0 - d / 2.0, 0.0)) @ Matrix.Rotation(math.pi, 4, 'Z')),
                (lambda xc, w, d: Matrix.Translation((xc - S / 2.0, S / 2.0 + d / 2.0, 0.0))),
                (lambda xc, w, d: Matrix.Translation((-S / 2.0 - d / 2.0, xc - S / 2.0, 0.0)) @ Matrix.Rotation(math.pi / 2.0, 4, 'Z')),
                (lambda xc, w, d: Matrix.Translation((S / 2.0 + d / 2.0, xc - S / 2.0, 0.0)) @ Matrix.Rotation(-math.pi / 2.0, 4, 'Z')),
            )
            for to_matrix in sides:
                self._row(scene, street, base, rng, to_matrix, style, limit=S)
            new_part('FLOOR', (S, S, 0.12), base @ Matrix.Translation((0.0, 0.0, 0.0)), coll,
                     parent=street, name="CF_Plaza")
            size = PROPS['FOUNTAIN'][1]
            mesh = prop_mesh('FOUNTAIN', size, "CF_Fountain")
            fountain = new_custom_part('BLOCK', mesh, size, base, coll, slots=['BLOCK'], parent=street,
                                       name="CF_Fountain")
            fountain["cf_prop"] = 'FOUNTAIN'
            for sx in (-1.0, 1.0):
                for sy in (-1.0, 1.0):
                    lsize = PROPS['LAMP_POST'][1]
                    lamp = new_custom_part('BLOCK', prop_mesh('LAMP_POST', lsize, "CF_LampPost"), lsize,
                                           base @ Matrix.Translation((sx * (S / 2.0 - 0.6), sy * (S / 2.0 - 0.6), 0.0)),
                                           coll, slots=['BLOCK'], parent=street, name="CF_LampPost")
                    lamp["cf_prop"] = 'LAMP_POST'
            total = len(street.children_recursive)
        else:
            if self.layout == 'ALLEY':
                rw, both, vary, bonus = self.alley_width, True, True, 1
            else:
                rw, both, vary, bonus = (self.road_width if self.road else 0.0), self.both_sides, False, 0

            def front(xc, w, d):
                return Matrix.Translation((xc, -D / 2.0 + d / 2.0, 0.0))

            def back(xc, w, d):
                return Matrix.Translation((xc, -D / 2.0 - rw - d / 2.0, 0.0)) @ Matrix.Rotation(math.pi, 4, 'Z')

            length = self._row(scene, street, base, rng, front, style, floors_bonus=bonus, vary_depth=vary)
            if both:
                length = max(length, self._row(scene, street, base, rng, back, style, floors_bonus=bonus,
                                               vary_depth=vary))
            if rw > 0.0:
                new_part('FLOOR', (length + 2.0, rw, 0.2),
                         base @ Matrix.Translation((length / 2.0, -D / 2.0 - rw / 2.0, 0.0)), coll,
                         parent=street, name="CF_Road")
            total = self.count * (2 if both else 1)
        common.select_only(context, [street], street)
        self.report({'INFO'}, rpt("Built a street with {count} buildings").format(count=total))
        return {'FINISHED'}


classes = (
    CONCEPTFLOW_OT_draw_walls,
    CONCEPTFLOW_OT_add_prop,
    CONCEPTFLOW_OT_add_stairs,
    CONCEPTFLOW_OT_build_street,
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
