# SPDX-License-Identifier: GPL-3.0-or-later
# ConceptFlow for Blender
"""Building tool extras: space styles, ruin slider, doors and windows by clicking on walls,
furnishing rooms automatically, the scale figure, trace underlays, and extra parts
(canopy, arch wall, hip roof, dome)."""

import math
import random
import traceback
import bmesh
import bpy
from bpy.props import EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper
from mathutils import Matrix, Vector, geometry

from . import common, graybox, graybox_tools
from .common import guarded, iface, rpt, tip
from .graybox import FRAME_GAP, cut_openings, is_box, is_part, new_custom_part, new_part

# ---------------------------------------------------------------------------
# Space styles
# ---------------------------------------------------------------------------

# Everything a style sets on the Draw Walls / Build Building settings.
STYLES = {
    'MODERN': dict(height=2.7, thickness=0.15, slab=0.15, ceiling=True, windows=True, window_spacing=3.0,
                   window_width=1.8, window_height=1.4, sill_height=0.9, doors=1, door_width=0.9,
                   door_height=2.1, roof_type='FLAT', roof_pitch=30.0, roof_overhang=0.2, ruin=0.0),
    'WASHITSU': dict(height=2.4, thickness=0.12, slab=0.1, ceiling=True, windows=True, window_spacing=1.8,
                     window_width=1.6, window_height=1.6, sill_height=0.4, doors=1, door_width=1.7,
                     door_height=1.8, roof_type='HIP', roof_pitch=22.0, roof_overhang=0.9, ruin=0.0),
    'RUIN': dict(height=3.2, thickness=0.35, slab=0.2, ceiling=False, windows=True, window_spacing=3.2,
                 window_width=1.0, window_height=1.8, sill_height=0.8, doors=1, door_width=1.1,
                 door_height=2.2, roof_type='GABLE', roof_pitch=32.0, roof_overhang=0.2, ruin=0.55),
    'SCIFI': dict(height=3.0, thickness=0.4, slab=0.3, ceiling=True, windows=False, window_spacing=4.0,
                  window_width=1.0, window_height=1.0, sill_height=1.0, doors=2, door_width=1.6,
                  door_height=2.5, roof_type='FLAT', roof_pitch=30.0, roof_overhang=0.0, ruin=0.0),
    'STONE': dict(height=3.2, thickness=0.55, slab=0.25, ceiling=True, windows=True, window_spacing=3.6,
                  window_width=0.6, window_height=1.1, sill_height=1.5, doors=1, door_width=1.2,
                  door_height=2.2, roof_type='GABLE', roof_pitch=45.0, roof_overhang=0.4, ruin=0.0),
    'WAREHOUSE': dict(height=6.0, thickness=0.25, slab=0.25, ceiling=False, windows=True, window_spacing=4.0,
                      window_width=1.6, window_height=0.9, sill_height=4.2, doors=1, door_width=3.0,
                      door_height=3.5, roof_type='SHED', roof_pitch=8.0, roof_overhang=0.3, ruin=0.0),
    'SHOP': dict(height=3.4, thickness=0.25, slab=0.2, ceiling=True, windows=True, window_spacing=3.2,
                 window_width=2.6, window_height=2.2, sill_height=0.3, doors=1, door_width=1.2,
                 door_height=2.3, roof_type='FLAT', roof_pitch=30.0, roof_overhang=0.3, ruin=0.0),
}


def apply_style(draw):
    """Update callback of the style menu: copy the style's numbers into the settings."""
    style = STYLES.get(draw.style)
    if style is None:
        return
    for key, value in style.items():
        if key == 'ruin':
            continue
        setattr(draw, key, value)
    scene = draw.id_data
    if isinstance(scene, bpy.types.Scene):
        scene.conceptflow.extras.ruin_amount = style["ruin"]


# ---------------------------------------------------------------------------
# Picking points in the 3D view
# ---------------------------------------------------------------------------

def window_region(context):
    area = context.area
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    return region, area.spaces.active.region_3d if area.spaces.active else None


def mouse_ray(context, event):
    """(origin, direction) of the mouse ray in the 3D view, or None if the mouse is outside it."""
    from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
    region, rv3d = window_region(context)
    if region is None or rv3d is None:
        return None
    if not (region.x <= event.mouse_x < region.x + region.width
            and region.y <= event.mouse_y < region.y + region.height):
        return None
    coord = (event.mouse_x - region.x, event.mouse_y - region.y)
    return region_2d_to_origin_3d(region, rv3d, coord), region_2d_to_vector_3d(region, rv3d, coord)


def ground_hit(context, event, z):
    ray = mouse_ray(context, event)
    if ray is None:
        return None
    origin, direction = ray
    hit = geometry.intersect_line_plane(origin, origin + direction * 1000.0,
                                        Vector((0.0, 0.0, z)), Vector((0.0, 0.0, 1.0)))
    if hit is None or (hit - origin).dot(direction) < 0.0:
        return None
    return Vector((hit.x, hit.y, z))


# ---------------------------------------------------------------------------
# Doors and windows by clicking on a wall
# ---------------------------------------------------------------------------

def place_opening(scene, wall, kind, point, draw):
    """Add a door or window to ``wall`` at the world ``point`` and cut the wall."""
    rmat, mn, mx = common.frame_of(wall)
    local = rmat.inverted_safe() @ point
    if kind == 'DOOR':
        w, h, sill = draw.door_width, min(draw.door_height, mx.z - mn.z - 0.05), 0.0
    else:
        w, sill = draw.window_width, draw.sill_height
        h = min(draw.window_height, mx.z - mn.z - sill - 0.1)
    if h < 0.2 or (mx.x - mn.x) < 0.3:
        return None
    w = min(w, mx.x - mn.x - 0.1)
    half = w / 2.0
    x = min(max(local.x, mn.x + half), mx.x - half) if (mx.x - mn.x) > w else (mn.x + mx.x) / 2.0
    centre_y = (mn.y + mx.y) / 2.0
    size = (w, (mx.y - mn.y) + 2.0 * FRAME_GAP, h)
    matrix = rmat @ Matrix.Translation((x, centre_y, mn.z + sill))
    obj = new_part(kind, size, matrix, wall.users_collection[0], parent=wall.parent,
                   building=wall.conceptflow.building, sill=sill if kind == 'WINDOW' else 0.0)
    walls = [o for o in scene.objects if is_part(o, 'WALL')]
    cut_openings(walls, [obj])
    return obj


class CONCEPTFLOW_OT_click_opening(Operator):
    bl_idname = "conceptflow.click_opening"
    bl_label = "Add by Clicking"
    bl_description = ("Click on a wall in the 3D view to add a door or window there and cut the wall. "
                      "Click again for more. Right click or Esc to finish")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.scene is not None and context.mode == 'OBJECT'
                and context.area is not None and context.area.type == 'VIEW_3D')

    def invoke(self, context, event):
        context.window_manager.modal_handler_add(self)
        kind = context.scene.conceptflow.extras.opening_kind
        context.workspace.status_text_set(iface("Click on a wall to add a {kind}. Right click / Esc = finish").format(
            kind=iface("door") if kind == 'DOOR' else iface("window")))
        self._count = 0
        return {'RUNNING_MODAL'}

    def _finish(self, context):
        context.workspace.status_text_set(None)
        return {'FINISHED'} if self._count else {'CANCELLED'}

    def modal(self, context, event):
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            return {'PASS_THROUGH'}
        if event.type in {'ESC', 'RIGHTMOUSE', 'RET'} and event.value == 'PRESS':
            return self._finish(context)
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            try:
                self._click(context, event)
            except Exception:
                common.last_error = traceback.format_exc()
                traceback.print_exc()
                self.report({'ERROR'}, rpt("Adding the opening stopped because of an error"))
                return self._finish(context)
        return {'RUNNING_MODAL'}

    def _click(self, context, event):
        ray = mouse_ray(context, event)
        if ray is None:
            return
        scene = context.scene
        origin, direction = ray
        depsgraph = context.evaluated_depsgraph_get()
        p = origin.copy()
        for _ in range(8):
            hit, loc, _nor, _idx, ob, _mw = scene.ray_cast(depsgraph, p, direction, distance=10000.0)
            if not hit:
                return
            src = ob.original if ob is not None else None
            if src is not None and src.hide_render:
                p = loc + direction * 1e-3
                continue
            break
        else:
            return
        if not is_part(src, 'WALL'):
            self.report({'INFO'}, rpt("That is not a wall"))
            return
        extras = scene.conceptflow.extras
        obj = place_opening(scene, src, extras.opening_kind, loc, scene.conceptflow.draw)
        if obj is None:
            self.report({'WARNING'}, rpt("This wall is too small for that"))
            return
        self._count += 1


# ---------------------------------------------------------------------------
# Ruin: break walls, floors and roofs
# ---------------------------------------------------------------------------

MAX_RUIN_PIECES = 500


def _subtract(intervals, cut_lo, cut_hi):
    out = []
    for lo, hi in intervals:
        if cut_hi <= lo or cut_lo >= hi:
            out.append((lo, hi))
            continue
        if cut_lo > lo:
            out.append((lo, cut_lo))
        if cut_hi < hi:
            out.append((cut_hi, hi))
    return out


def ruin_boxes(length, height, amount, rng):
    """Boxes (x0, x1, z0, z1) of a wall of ``length`` x ``height`` after it broke (local, from 0)."""
    cols, x = [], 0.0
    while x < length - 1e-3:
        w = min(rng.uniform(0.35, 1.1), length - x)
        if length - x - w < 0.2:
            w = length - x
        cols.append([x, x + w, height])
        x += w
    for col in cols:
        roll = rng.random()
        if roll < amount * 0.22:
            col[2] = height * rng.uniform(0.0, 0.18)           # collapsed down to a stub
        elif roll < amount * 0.95:
            col[2] = height * rng.uniform(max(0.12, 1.0 - amount), 1.0)  # broken top
    holes = []
    for _ in range(int(round(amount * length / 2.2 * rng.uniform(0.6, 1.4)))):
        hw, hh = rng.uniform(0.5, 1.6), rng.uniform(0.5, 1.8)
        if hw >= length or hh >= height:
            continue
        hx = rng.uniform(0.0, length - hw)
        hz = 0.0 if rng.random() < 0.4 else rng.uniform(0.0, height - hh)
        holes.append((hx, hx + hw, hz, hz + hh))
    xs = sorted({0.0, length, *[c[0] for c in cols], *[c[1] for c in cols],
                 *[h[0] for h in holes], *[h[1] for h in holes]})
    boxes = []
    for a, b in zip(xs, xs[1:]):
        if b - a < 0.04:
            continue
        mid = (a + b) / 2.0
        col = next((c for c in cols if c[0] <= mid <= c[1]), None)
        if col is None or col[2] < 0.05:
            continue
        free = [(0.0, col[2])]
        for hx0, hx1, hz0, hz1 in holes:
            if hx0 <= mid <= hx1:
                free = _subtract(free, hz0, hz1)
        boxes += [(a, b, lo, hi) for lo, hi in free if hi - lo > 0.04]
    return boxes


def ruin_cells(sx, sy, amount, rng):
    """Cells (x0, x1, y0, y1) of a slab broken into tiles with some tiles missing."""
    cell = max(0.8, math.sqrt(sx * sy / 500.0))
    nx, ny = max(1, int(round(sx / cell))), max(1, int(round(sy / cell)))
    cells = []
    for i in range(nx):
        for j in range(ny):
            edge = i in (0, nx - 1) or j in (0, ny - 1)
            if rng.random() < amount * (0.3 if edge else 0.45):
                continue
            cells.append((sx * i / nx, sx * (i + 1) / nx, sy * j / ny, sy * (j + 1) / ny))
    return cells


def _materials_of(ob):
    return [m for m in ob.data.materials if m is not None] or None


def ruin_wall(wall, amount, rng):
    rmat, mn, mx = common.frame_of(wall)
    length, height = mx.x - mn.x, mx.z - mn.z
    boxes = ruin_boxes(length, height, amount, rng)
    if len(boxes) > MAX_RUIN_PIECES:
        return 0
    cy, depth = (mn.y + mx.y) / 2.0, mx.y - mn.y
    mats = _materials_of(wall)
    made = 0
    for x0, x1, z0, z1 in boxes:
        matrix = rmat @ Matrix.Translation((mn.x + (x0 + x1) / 2.0, cy, mn.z + z0))
        piece = new_part('WALL', (x1 - x0, depth, z1 - z0), matrix, wall.users_collection[0],
                         parent=wall.parent, building=wall.conceptflow.building, name="CF_Wall",
                         materials=mats)
        for coll in wall.users_collection[1:]:
            coll.objects.link(piece)
        made += 1
    common.remove_object(wall)
    return made


def ruin_slab(slab, amount, rng):
    rmat, mn, mx = common.frame_of(slab)
    cells = ruin_cells(mx.x - mn.x, mx.y - mn.y, amount, rng)
    thick = mx.z - mn.z
    mats = _materials_of(slab)
    for x0, x1, y0, y1 in cells:
        matrix = rmat @ Matrix.Translation((mn.x + (x0 + x1) / 2.0, mn.y + (y0 + y1) / 2.0, mx.z))
        piece = new_part('FLOOR', (x1 - x0, y1 - y0, thick), matrix, slab.users_collection[0],
                         parent=slab.parent, building=slab.conceptflow.building, name=slab.name,
                         materials=mats)
        for coll in slab.users_collection[1:]:
            coll.objects.link(piece)
    common.remove_object(slab)
    return len(cells)


def collect_ruin_targets(context):
    scene = context.scene
    selected = list(context.selected_objects)
    if selected:
        pool = []
        for ob in selected:
            pool.append(ob)
            pool += list(ob.children_recursive)
    else:
        pool = list(scene.objects)
    seen, parts = set(), []
    for ob in pool:
        if ob.name in seen or ob.type != 'MESH':
            continue
        seen.add(ob.name)
        if ob.conceptflow.part in {'WALL', 'FLOOR'} and is_box(ob):
            parts.append(ob)
    return parts


class CONCEPTFLOW_OT_ruin(Operator):
    bl_idname = "conceptflow.ruin"
    bl_label = "Ruin"
    bl_description = ("Break the selected buildings (or all walls and floors when nothing is selected): "
                      "chipped wall tops, holes, collapsed parts and missing floor tiles. Undo with Ctrl+Z")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.mode == 'OBJECT'

    @guarded
    def execute(self, context):
        extras = context.scene.conceptflow.extras
        amount = extras.ruin_amount
        if amount <= 0.0:
            self.report({'INFO'}, rpt("Ruin Amount is 0: nothing to break"))
            return {'CANCELLED'}
        rng = random.Random(extras.ruin_seed)
        parts = collect_ruin_targets(context)
        broken = 0
        for ob in sorted(parts, key=lambda o: o.name):
            if ob.conceptflow.part == 'WALL':
                broken += 1 if ruin_wall(ob, amount, rng) else 0
            else:
                broken += 1 if ruin_slab(ob, amount * 0.8, rng) else 0
        if broken == 0:
            self.report({'WARNING'}, rpt("No walls or floors to break"))
            return {'CANCELLED'}
        self.report({'INFO'}, rpt("Broke {count} parts").format(count=broken))
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Furnish rooms automatically
# ---------------------------------------------------------------------------

# kit: list of (prop kind, weight, wants a wall behind it)
KITS = {
    'ROOM': [('TABLE', 1.0, False), ('CHAIR', 2.0, False), ('BED', 0.8, True), ('SOFA', 0.8, True),
             ('SHELF', 1.5, True), ('COUNTER', 0.4, True), ('BENCH', 0.3, True), ('CRATE', 0.5, False),
             ('BARREL', 0.3, False)],
    'STORAGE': [('CRATE', 3.0, False), ('BARREL', 2.0, False), ('SHELF', 2.0, True)],
    'RUINS': [('CRATE', 1.5, False), ('BARREL', 1.5, False), ('CHAIR', 1.0, False), ('BENCH', 0.6, False),
              ('TABLE', 0.5, False)],
}
SCATTER_TAG = "cf_scatter"
SKIP_FLOORS = ("CF_Roof", "CF_Ceiling", "CF_Balcony", "CF_Road")


def point_in_polygon(point, poly):
    inside = False
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if (a.y > point.y) != (b.y > point.y):
            x = (b.x - a.x) * (point.y - a.y) / (b.y - a.y) + a.x
            if point.x < x:
                inside = not inside
    return inside


def distance_to_polygon_edges(point, poly):
    best = math.inf
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        ab = b - a
        t = 0.0 if ab.length_squared < 1e-12 else max(0.0, min(1.0, (point - a).dot(ab) / ab.length_squared))
        best = min(best, (point - (a + ab * t)).length)
    return best


def floor_footprint(floor):
    """(polygon of world XY points, z of the top surface)."""
    poly = floor.get("cf_poly")
    if poly:
        pts = [Vector((poly[i], poly[i + 1], 0.0)) for i in range(0, len(poly) - 1, 2)]
        return pts, floor.matrix_world.translation.z
    rmat, mn, mx = common.frame_of(floor)
    pts = [rmat @ Vector((x, y, mx.z)) for x, y in ((mn.x, mn.y), (mx.x, mn.y), (mx.x, mx.y), (mn.x, mx.y))]
    return [Vector((p.x, p.y, 0.0)) for p in pts], pts[0].z


def scatter_floor(scene, floor, kit, density, rng, wall_margin):
    poly, top = floor_footprint(floor)
    if len(poly) < 3:
        return 0
    area = abs(graybox_tools.polygon_area(poly))
    count = min(200, int(round(area * density / 4.0)))
    if count <= 0:
        return 0
    doors = [o.matrix_world.translation for o in scene.objects
             if is_part(o, 'DOOR') and o.conceptflow.building == floor.conceptflow.building]
    weights = [w for _k, w, _wall in KITS[kit]]
    placed = []  # (centre, radius)
    made = 0
    edges = [(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))]
    sign = 1.0 if graybox_tools.polygon_area(poly) > 0 else -1.0
    xs, ys = [p.x for p in poly], [p.y for p in poly]
    for _ in range(count):
        kind, _w, wants_wall = rng.choices(KITS[kit], weights=weights)[0]
        label, base, _builder, _desc = graybox_tools.PROPS[kind]
        scale = rng.uniform(0.9, 1.15)
        size = tuple(v * scale for v in base)
        radius = max(size[0], size[1]) / 2.0 * 0.85 + 0.12
        for _attempt in range(40):
            if wants_wall:
                a, b = rng.choices(edges, weights=[(q - p).length for p, q in edges])[0]
                ab = b - a
                if ab.length < size[0] + 0.3:
                    continue
                direction = ab.normalized()
                outward = Vector((direction.y, -direction.x, 0.0)) * sign  # points out of the room
                t = rng.uniform(size[0] / 2.0 + 0.1, ab.length - size[0] / 2.0 - 0.1)
                centre = a + direction * t - outward * (wall_margin + size[1] / 2.0 + 0.02)
                yaw = math.atan2(outward.y, outward.x) - math.pi / 2.0
            else:
                centre = Vector((rng.uniform(min(xs), max(xs)), rng.uniform(min(ys), max(ys)), 0.0))
                yaw = rng.choice((0.0, math.pi / 2.0, math.pi, -math.pi / 2.0)) + rng.uniform(-0.35, 0.35)
            if not point_in_polygon(centre, poly) or distance_to_polygon_edges(centre, poly) < wall_margin + radius * 0.6:
                continue
            if any((centre - d).length < 1.2 for d in doors):
                continue
            if any((centre - c).length < radius + r for c, r in placed):
                continue
            mesh = graybox_tools.prop_mesh(kind, size, "CF_" + label.replace(" ", ""))
            matrix = Matrix.Translation((centre.x, centre.y, top)) @ Matrix.Rotation(yaw, 4, 'Z')
            obj = new_custom_part('BLOCK', mesh, size, matrix, floor.users_collection[0], slots=['BLOCK'],
                                  parent=floor.parent, building=floor.conceptflow.building,
                                  name="CF_" + label.replace(" ", ""))
            obj["cf_prop"] = kind
            obj[SCATTER_TAG] = floor.name
            placed.append((centre, radius))
            made += 1
            break
    return made


def collect_floors(context):
    pool = []
    selected = list(context.selected_objects)
    for ob in selected:
        pool.append(ob)
        pool += list(ob.children_recursive)
    floors, seen = [], set()
    for ob in pool:
        if ob.name in seen or not is_part(ob, 'FLOOR'):
            continue
        seen.add(ob.name)
        if ob.name.startswith(SKIP_FLOORS) or ob.get(SCATTER_TAG):
            continue
        floors.append(ob)
    return floors


class CONCEPTFLOW_OT_scatter_props(Operator):
    bl_idname = "conceptflow.scatter_props"
    bl_label = "Furnish Rooms"
    bl_description = ("Fill the selected rooms or buildings with props, against the walls and in the open, "
                      "away from doors. Pressing it again with another seed replaces them")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.mode == 'OBJECT' and bool(context.selected_objects)

    @guarded
    def execute(self, context):
        scene = context.scene
        extras = scene.conceptflow.extras
        floors = collect_floors(context)
        if not floors:
            self.report({'WARNING'}, rpt("Select a building, room or floor first"))
            return {'CANCELLED'}
        names = {f.name for f in floors}
        for ob in [o for o in scene.objects if o.get(SCATTER_TAG) in names]:
            common.remove_object(ob)
        wall_margin = scene.conceptflow.draw.thickness / 2.0 + 0.1
        total = 0
        for index, floor in enumerate(sorted(floors, key=lambda o: o.name)):
            rng = random.Random(extras.scatter_seed * 1009 + index)
            total += scatter_floor(scene, floor, extras.scatter_kit, extras.scatter_density, rng, wall_margin)
        self.report({'INFO'}, rpt("Placed {count} props").format(count=total))
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Scale figure
# ---------------------------------------------------------------------------

def figure_mesh(name, h):
    bm = bmesh.new()
    box = common.bm_box
    for side in (-1.0, 1.0):
        box(bm, (side * 0.06 * h - 0.05 * h, -0.045 * h, 0.0), (side * 0.06 * h + 0.05 * h, 0.05 * h, 0.46 * h))
        box(bm, (side * 0.165 * h - 0.03 * h, -0.035 * h, 0.46 * h), (side * 0.165 * h + 0.03 * h, 0.035 * h, 0.80 * h))
    box(bm, (-0.135 * h, -0.075 * h, 0.46 * h), (0.135 * h, 0.075 * h, 0.80 * h))
    box(bm, (-0.03 * h, -0.03 * h, 0.80 * h), (0.03 * h, 0.03 * h, 0.84 * h))
    box(bm, (-0.065 * h, -0.07 * h, 0.84 * h), (0.065 * h, 0.07 * h, h))
    return common.mesh_from_bm(name, bm)


FIGURE_ITEMS = [
    ('ADULT', "Adult", "A person 1.7 m tall"),
    ('CHILD', "Child", "A child 1.2 m tall"),
    ('TALL', "Tall", "A tall person 1.95 m tall"),
    ('CUSTOM', "Custom", "A person with the Figure Height set in the panel"),
]
FIGURE_HEIGHTS = {'ADULT': 1.7, 'CHILD': 1.2, 'TALL': 1.95}


class CONCEPTFLOW_OT_add_figure(Operator):
    bl_idname = "conceptflow.add_figure"
    bl_label = "Scale Figure"
    bl_description = "Place a simple person at the 3D cursor to check sizes"
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(name="Figure", items=FIGURE_ITEMS, default='ADULT')

    @classmethod
    def description(cls, context, properties):
        if properties is not None:
            for ident, _name, desc in FIGURE_ITEMS:
                if ident == properties.kind:
                    return tip(desc) + "\n" + tip(cls.bl_description)
        return tip(cls.bl_description)

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.mode == 'OBJECT'

    @guarded
    def execute(self, context):
        scene = context.scene
        height = FIGURE_HEIGHTS.get(self.kind, scene.conceptflow.extras.figure_height)
        mesh = figure_mesh("CF_Figure", height)
        mesh.materials.append(graybox.graybox_material('FIGURE'))
        obj = bpy.data.objects.new("CF_Figure", mesh)
        graybox.graybox_collection(scene).objects.link(obj)
        obj.matrix_world = graybox._cursor_matrix(scene)
        obj["cf_figure"] = height
        common.select_only(context, [obj], obj)
        self.report({'INFO'}, rpt("Placed a figure {height} m tall").format(height="{:.2f}".format(height)))
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Extra parts: canopy, arch wall, hip roof, dome
# ---------------------------------------------------------------------------

def canopy_mesh(name, width, depth, thickness=0.08, tilt_degrees=12.0):
    """A sloping canopy over a door. The wall is at y = 0, the canopy sticks out towards -Y."""
    bm = bmesh.new()
    before = set(bm.verts)
    common.bm_box(bm, (-width / 2.0, -depth, -thickness), (width / 2.0, 0.05, 0.0))
    slab = [v for v in bm.verts if v not in before]
    bmesh.ops.rotate(bm, cent=(0.0, 0.0, 0.0), matrix=Matrix.Rotation(math.radians(tilt_degrees), 3, 'X'), verts=slab)
    reach = depth * 0.75
    for side in (-1.0, 1.0):
        before = set(bm.verts)
        length = reach * math.sqrt(2.0)
        common.bm_box(bm, (side * (width / 2.0 - 0.15) - 0.03, -length / 2.0, -0.03),
                      (side * (width / 2.0 - 0.15) + 0.03, length / 2.0, 0.03))
        brace = [v for v in bm.verts if v not in before]
        bmesh.ops.rotate(bm, cent=(0.0, 0.0, 0.0), matrix=Matrix.Rotation(math.radians(135.0), 3, 'X'), verts=brace)
        bmesh.ops.translate(bm, vec=(0.0, -reach / 2.0, -thickness - reach / 2.0), verts=brace)
    return common.mesh_from_bm(name, bm)


def arch_wall_mesh(name, width, height, thickness, arch_width, segments=16):
    """A wall with a round-topped gateway in the middle. Origin at the bottom centre."""
    arch_width = min(arch_width, width - 0.4)
    radius = arch_width / 2.0
    spring = min(height * 0.45, height - radius - 0.25)
    if spring < 0.3:
        spring = 0.3
        radius = max(0.2, min(radius, height - spring - 0.2))
        arch_width = radius * 2.0
    bm = bmesh.new()
    h = thickness / 2.0
    common.bm_box(bm, (-width / 2.0, -h, 0.0), (-radius, h, height))
    common.bm_box(bm, (radius, -h, 0.0), (width / 2.0, h, height))
    xs = [-radius + 2.0 * radius * i / segments for i in range(segments + 1)]
    zs = [spring + math.sqrt(max(radius * radius - x * x, 0.0)) for x in xs]
    for i in range(segments):
        x0, x1, z0, z1 = xs[i], xs[i + 1], zs[i], zs[i + 1]
        v = [bm.verts.new(c) for c in (
            (x0, -h, z0), (x1, -h, z1), (x1, -h, height), (x0, -h, height),
            (x0, h, z0), (x1, h, z1), (x1, h, height), (x0, h, height))]
        for f in ((0, 1, 2, 3), (7, 6, 5, 4), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)):
            bm.faces.new([v[k] for k in f])
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-5)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return common.mesh_from_bm(name, bm)


FEATURE_ITEMS = [
    ('CANOPY', "Canopy", "A sloping canopy with braces over a door. Place it with the wall on its +Y side"),
    ('ARCH_WALL', "Arch Wall", "A wall with a round-topped gateway"),
    ('HIP_ROOF', "Hip Roof", "A hip roof on its own, for placing on top of any box"),
    ('DOME', "Dome", "A rounded dome on its own"),
]
FEATURE_SIZES = {'CANOPY': (2.0, 1.0, 0.0), 'ARCH_WALL': (6.0, 0.4, 4.0), 'HIP_ROOF': (6.0, 5.0, 0.0),
                 'DOME': (5.0, 5.0, 0.0)}


class CONCEPTFLOW_OT_add_feature(Operator):
    bl_idname = "conceptflow.add_feature"
    bl_label = "Add Feature"
    bl_description = "Add a canopy, arch wall, hip roof or dome at the 3D cursor"
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(name="Feature", items=FEATURE_ITEMS, default='CANOPY')
    size_x: FloatProperty(name="Width", min=0.0, soft_max=100.0, unit='LENGTH', default=0.0, options={'SKIP_SAVE'})
    size_y: FloatProperty(name="Depth", min=0.0, soft_max=100.0, unit='LENGTH', default=0.0, options={'SKIP_SAVE'})
    size_z: FloatProperty(name="Height", min=0.0, soft_max=100.0, unit='LENGTH', default=0.0, options={'SKIP_SAVE'})
    pitch: FloatProperty(name="Roof Pitch", min=5.0, max=70.0, default=30.0, options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        if properties is not None:
            for ident, _name, desc in FEATURE_ITEMS:
                if ident == properties.kind:
                    return tip(desc) + "\n" + tip(cls.bl_description)
        return tip(cls.bl_description)

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.mode == 'OBJECT'

    @guarded
    def execute(self, context):
        scene = context.scene
        dx, dy, dz = FEATURE_SIZES[self.kind]
        sx, sy, sz = (self.size_x or dx), (self.size_y or dy), (self.size_z or dz)
        part, slots, name = 'BLOCK', ['BLOCK'], "CF_" + self.kind.title().replace("_", "")
        if self.kind == 'CANOPY':
            mesh = canopy_mesh(name, sx, sy)
        elif self.kind == 'ARCH_WALL':
            mesh = arch_wall_mesh(name, sx, sz, sy, min(2.2, sx * 0.4))
            part, slots = 'WALL', ['WALL']
        elif self.kind == 'HIP_ROOF':
            mesh, sz = graybox.hip_roof_mesh(name, sx, sy, self.pitch)
            part, slots = 'FLOOR', ['FLOOR']
        else:
            sz = sz or min(sx, sy) / 2.0 * 0.9
            mesh = graybox.dome_mesh(name, sx / 2.0, sy / 2.0, sz)
            part, slots = 'FLOOR', ['FLOOR']
        obj = new_custom_part(part, mesh, (sx, sy, sz), graybox._cursor_matrix(scene),
                              graybox.graybox_collection(scene), slots=slots, name=name)
        common.select_only(context, [obj], obj)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Trace an underlay image
# ---------------------------------------------------------------------------

def underlay_object(scene):
    return next((o for o in scene.objects if o.get("cf_underlay")), None)


def on_underlay_changed(extras):
    scene = extras.id_data
    if not isinstance(scene, bpy.types.Scene):
        return
    ob = underlay_object(scene)
    if ob is None:
        return
    ob.empty_display_size = extras.underlay_size
    ob.color = (1.0, 1.0, 1.0, extras.underlay_opacity)


class CONCEPTFLOW_OT_underlay_load(Operator, ImportHelper):
    bl_idname = "conceptflow.underlay_load"
    bl_label = "Load Underlay"
    bl_description = ("Lay a reference image (a photo, floor plan or sketch) flat on the ground at the "
                      "3D cursor so that you can trace over it with Draw Walls")
    bl_options = {'REGISTER', 'UNDO'}

    filter_glob: StringProperty(default="*.jpg;*.jpeg;*.png;*.tif;*.tiff;*.bmp;*.webp", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.mode == 'OBJECT'

    @guarded
    def execute(self, context):
        scene = context.scene
        extras = scene.conceptflow.extras
        img = bpy.data.images.load(self.filepath, check_existing=True)
        old = underlay_object(scene)
        if old is not None:
            bpy.data.objects.remove(old, do_unlink=True)
        ob = bpy.data.objects.new("CF_Underlay", None)
        ob.empty_display_type = 'IMAGE'
        ob.data = img
        ob.use_empty_image_alpha = True
        ob.empty_image_depth = 'BACK'
        ob.show_in_front = False
        ob["cf_underlay"] = True
        graybox.graybox_collection(scene).objects.link(ob)
        ob.location = scene.cursor.location
        on_underlay_changed(extras)
        common.select_only(context, [ob], ob)
        self.report({'INFO'}, rpt("Underlay placed. Set its real size, then trace it with Draw Walls"))
        return {'FINISHED'}


class CONCEPTFLOW_OT_underlay_remove(Operator):
    bl_idname = "conceptflow.underlay_remove"
    bl_label = "Remove Underlay"
    bl_description = "Remove the underlay image from the scene"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and underlay_object(context.scene) is not None

    @guarded
    def execute(self, context):
        bpy.data.objects.remove(underlay_object(context.scene), do_unlink=True)
        return {'FINISHED'}


class CONCEPTFLOW_OT_underlay_scale(Operator):
    bl_idname = "conceptflow.underlay_scale"
    bl_label = "Set Scale from Two Points"
    bl_description = ("Click two points on the underlay whose real distance you know (set Known Length first) "
                      "and the underlay is scaled to match")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.scene is not None and underlay_object(context.scene) is not None
                and context.mode == 'OBJECT' and context.area is not None and context.area.type == 'VIEW_3D')

    def invoke(self, context, event):
        self._points = []
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(iface("Click the first point (Esc = cancel)"))
        return {'RUNNING_MODAL'}

    def _finish(self, context, result):
        context.workspace.status_text_set(None)
        return result

    def modal(self, context, event):
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            return {'PASS_THROUGH'}
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            return self._finish(context, {'CANCELLED'})
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            scene = context.scene
            ob = underlay_object(scene)
            point = ground_hit(context, event, ob.matrix_world.translation.z)
            if point is None:
                return {'RUNNING_MODAL'}
            self._points.append(point)
            if len(self._points) == 1:
                context.workspace.status_text_set(iface("Click the second point"))
                return {'RUNNING_MODAL'}
            a, b = self._points
            measured = (b - a).length
            extras = scene.conceptflow.extras
            if measured < 1e-4:
                self.report({'WARNING'}, rpt("The two points are the same"))
                return self._finish(context, {'CANCELLED'})
            factor = extras.known_length / measured
            ob.location = a + (ob.location - a) * factor  # scale around the first point
            extras.underlay_size = extras.underlay_size * factor
            self.report({'INFO'}, rpt("Underlay scaled by {factor}").format(factor="{:.3f}".format(factor)))
            return self._finish(context, {'FINISHED'})
        return {'RUNNING_MODAL'}


classes = (
    CONCEPTFLOW_OT_click_opening,
    CONCEPTFLOW_OT_ruin,
    CONCEPTFLOW_OT_scatter_props,
    CONCEPTFLOW_OT_add_figure,
    CONCEPTFLOW_OT_add_feature,
    CONCEPTFLOW_OT_underlay_load,
    CONCEPTFLOW_OT_underlay_remove,
    CONCEPTFLOW_OT_underlay_scale,
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
