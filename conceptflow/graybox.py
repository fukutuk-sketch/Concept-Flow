# SPDX-License-Identifier: GPL-3.0-or-later
# ConceptFlow for Blender
"""Graybox: quick parts (walls, pillars, stairs, doors, windows, floors, blocks),
a one-click building generator and wall openings."""

import math
import uuid

import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy.types import Operator
from mathutils import Euler, Matrix, Vector

from . import common
from .common import guarded, rpt, tip
from .props import PART_ITEMS

ADDABLE_PARTS = [item for item in PART_ITEMS if item[0] != 'NONE']

# Default size (width X, depth Y, height Z) in metres.
PART_DEFAULTS = {
    'WALL': (4.0, 0.2, 3.0),
    'PILLAR': (0.4, 0.4, 3.0),
    'STAIRS': (1.2, 3.0, 1.8),
    'DOOR': (0.9, 0.24, 2.1),
    'WINDOW': (1.2, 0.24, 1.2),
    'FLOOR': (4.0, 4.0, 0.15),
    'BLOCK': (1.0, 1.0, 1.0),
}

PART_TIPS = {
    'WALL': "Add a wall at the 3D cursor. Doors and windows can cut openings into it",
    'PILLAR': "Add a pillar at the 3D cursor",
    'STAIRS': "Add straight stairs at the 3D cursor. They go up towards +Y",
    'DOOR': "Add a door. With a ConceptFlow wall active, the door is placed in the wall "
            "and the opening is cut",
    'WINDOW': "Add a window. With a ConceptFlow wall active, the window is placed in the wall "
              "and the opening is cut",
    'FLOOR': "Add a floor slab. Its top is at the 3D cursor",
    'BLOCK': "Add a block for furniture, props or large masses",
}

# Graybox material tones: key -> (color, roughness)
MAT_TONES = {
    'WALL': ((0.60, 0.60, 0.60), 0.85),
    'PILLAR': ((0.55, 0.55, 0.55), 0.85),
    'STAIRS': ((0.50, 0.50, 0.50), 0.85),
    'DOOR': ((0.36, 0.36, 0.36), 0.70),
    'FRAME': ((0.26, 0.26, 0.26), 0.60),
    'GLASS': ((0.16, 0.20, 0.24), 0.08),
    'FLOOR': ((0.46, 0.46, 0.46), 0.90),
    'BLOCK': ((0.68, 0.68, 0.68), 0.85),
}

FRAME_GAP = 0.02  # door/window frames stick out of the wall by this much on each side
STEP_RISE = 0.18


# ---------------------------------------------------------------------------
# Materials and collections
# ---------------------------------------------------------------------------

def graybox_material(key):
    name = "CF_Graybox_" + key.title()
    mat = bpy.data.materials.get(name)
    if mat is not None and mat.library is None:
        return mat
    color, rough = MAT_TONES.get(key, MAT_TONES['BLOCK'])
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1.0)
    mat.roughness = rough
    try:
        mat.use_nodes = True
    except Exception:
        pass
    node_tree = mat.node_tree
    if node_tree is not None:
        bsdf = next((n for n in node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = (*color, 1.0)
            bsdf.inputs["Roughness"].default_value = rough
    return mat


def graybox_collection(scene):
    return common.ensure_child_collection(scene.collection, "CF_Graybox", "GRAYBOX")


# ---------------------------------------------------------------------------
# Mesh builders (local space: X = width, Y = depth, Z = up, origin bottom centre)
# ---------------------------------------------------------------------------

def _stairs(bm, sx, sy, sz, steps):
    n = steps if steps > 0 else max(2, int(round(sz / STEP_RISE)))
    rise, run = sz / n, sy / n
    y0 = -sy / 2.0
    profile = [(y0, 0.0)]
    for i in range(n):
        profile.append((y0 + i * run, (i + 1) * rise))
        profile.append((y0 + (i + 1) * run, (i + 1) * rise))
    profile.append((sy / 2.0, 0.0))
    left = [bm.verts.new((-sx / 2.0, y, z)) for y, z in profile]
    right = [bm.verts.new((sx / 2.0, y, z)) for y, z in profile]
    bm.faces.new(left)
    bm.faces.new(list(reversed(right)))
    count = len(profile)
    for i in range(count):
        j = (i + 1) % count
        bm.faces.new((left[i], left[j], right[j], right[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])


def _door(bm, sx, sy, sz):
    hx, hy = sx / 2.0, sy / 2.0
    ft = min(0.08, sx * 0.15, sz * 0.15)
    common.bm_box(bm, (-hx, -hy, 0.0), (-hx + ft, hy, sz), 0)
    common.bm_box(bm, (hx - ft, -hy, 0.0), (hx, hy, sz), 0)
    common.bm_box(bm, (-hx + ft, -hy, sz - ft), (hx - ft, hy, sz), 0)
    slab = min(0.025, hy * 0.5)
    common.bm_box(bm, (-hx + ft, -slab, 0.0), (hx - ft, slab, sz - ft), 1)


def _window(bm, sx, sy, sz):
    hx, hy = sx / 2.0, sy / 2.0
    ft = min(0.07, sx * 0.15, sz * 0.15)
    common.bm_box(bm, (-hx, -hy, 0.0), (-hx + ft, hy, sz), 0)
    common.bm_box(bm, (hx - ft, -hy, 0.0), (hx, hy, sz), 0)
    common.bm_box(bm, (-hx + ft, -hy, sz - ft), (hx - ft, hy, sz), 0)
    common.bm_box(bm, (-hx + ft, -hy, 0.0), (hx - ft, hy, ft), 0)
    mull = 0.02
    md = min(0.03, hy)
    common.bm_box(bm, (-mull, -md, ft), (mull, md, sz - ft), 0)
    common.bm_box(bm, (-hx + ft, -md, sz / 2.0 - mull), (-mull, md, sz / 2.0 + mull), 0)
    common.bm_box(bm, (mull, -md, sz / 2.0 - mull), (hx - ft, md, sz / 2.0 + mull), 0)
    glass = min(0.006, hy * 0.5)
    common.bm_box(bm, (-hx + ft, -glass, ft), (hx - ft, glass, sz - ft), 1)


def build_part_mesh(part, size, *, round_pillar=False, steps=0, name="CF_Part"):
    """Return (mesh, material keys) for a graybox part."""
    sx, sy, sz = (max(0.001, float(v)) for v in size)
    hx, hy = sx / 2.0, sy / 2.0
    bm = bmesh.new()
    if part == 'PILLAR' and round_pillar:
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=24,
                              radius1=0.5, radius2=0.5, depth=1.0,
                              matrix=Matrix.Translation((0.0, 0.0, 0.5)))
        bmesh.ops.scale(bm, vec=(sx, sy, sz), verts=bm.verts[:])
        slots = ['PILLAR']
    elif part == 'STAIRS':
        _stairs(bm, sx, sy, sz, steps)
        slots = ['STAIRS']
    elif part == 'DOOR':
        _door(bm, sx, sy, sz)
        slots = ['FRAME', 'DOOR']
    elif part == 'WINDOW':
        _window(bm, sx, sy, sz)
        slots = ['FRAME', 'GLASS']
    elif part == 'FLOOR':
        common.bm_box(bm, (-hx, -hy, -sz), (hx, hy, 0.0))
        slots = ['FLOOR']
    else:
        common.bm_box(bm, (-hx, -hy, 0.0), (hx, hy, sz))
        slots = [part if part in MAT_TONES else 'BLOCK']
    return common.mesh_from_bm(name, bm), slots


def new_part(part, size, matrix, collection, *, parent=None, round_pillar=False, steps=0,
             piece='FULL', building="", sill=0.0, name=None, materials=None):
    """Create a graybox part object and link it to ``collection``."""
    name = name or "CF_" + part.title()
    mesh, slots = build_part_mesh(part, size, round_pillar=round_pillar, steps=steps, name=name)
    if materials:
        for mat in materials:
            mesh.materials.append(mat)
    else:
        for key in slots:
            mesh.materials.append(graybox_material(key))
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    if parent is not None:
        obj.parent = parent
    obj.matrix_world = matrix
    props = obj.conceptflow
    props.part = part
    props.size = size
    props.piece = piece
    props.building = building
    props.sill = sill
    return obj


def new_custom_part(part, mesh, size, matrix, collection, *, slots, parent=None, building="",
                    piece='FULL', name=None):
    """Like new_part, but with a mesh made by the caller (``slots`` = graybox material keys)."""
    for key in slots:
        mesh.materials.append(graybox_material(key))
    obj = bpy.data.objects.new(name or mesh.name, mesh)
    collection.objects.link(obj)
    if parent is not None:
        obj.parent = parent
    obj.matrix_world = matrix
    props = obj.conceptflow
    props.part = part
    props.size = size
    props.piece = piece
    props.building = building
    return obj


def prism_mesh(name, profile, thickness):
    """Prism from a polygon ``profile`` of (x, z) points, extruded along Y (centred)."""
    bm = bmesh.new()
    h = thickness / 2.0
    front = [bm.verts.new((x, -h, z)) for x, z in profile]
    back = [bm.verts.new((x, h, z)) for x, z in profile]
    bm.faces.new(list(reversed(front)))
    bm.faces.new(back)
    n = len(profile)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((front[i], front[j], back[j], back[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return common.mesh_from_bm(name, bm)


def is_part(obj, *parts):
    return obj is not None and obj.type == 'MESH' and obj.conceptflow.part in parts


def is_box(obj):
    mesh = obj.data
    return len(mesh.vertices) == 8 and len(mesh.polygons) == 6


# ---------------------------------------------------------------------------
# Openings
# ---------------------------------------------------------------------------

def _merge_holes(holes):
    holes = sorted(holes, key=lambda h: h[0])
    merged = []
    for hole in holes:
        if merged and hole[0] < merged[-1][1] - common.EPS:
            last = merged[-1]
            last[1] = max(last[1], hole[1])
            last[2] = min(last[2], hole[2])
            last[3] = max(last[3], hole[3])
        else:
            merged.append(list(hole))
    return merged


def _wall_pieces(mn, mx, holes):
    xs = sorted({mn.x, mx.x, *[h[0] for h in holes], *[h[1] for h in holes]})
    pieces = []
    for a, b in zip(xs, xs[1:]):
        if b - a < 1e-3:
            continue
        mid = (a + b) / 2.0
        hole = next((h for h in holes if h[0] - common.EPS <= mid <= h[1] + common.EPS), None)
        if hole is None:
            pieces.append(('SOLID', a, b, mn.z, mx.z))
            continue
        if hole[2] - mn.z > 1e-3:
            pieces.append(('SILL', a, b, mn.z, hole[2]))
        if mx.z - hole[3] > 1e-3:
            pieces.append(('HEADER', a, b, hole[3], mx.z))
    return pieces


def _holes_for(wall, openings, tol=0.05):
    rmat, mn, mx = common.frame_of(wall)
    rinv = rmat.inverted_safe()
    x_axis = (rmat.to_3x3() @ Vector((1.0, 0.0, 0.0))).normalized()
    holes = []
    for op in openings:
        if op == wall:
            continue
        op_x = (op.matrix_world.to_3x3() @ Vector((1.0, 0.0, 0.0)))
        if op_x.length < 1e-6 or abs(op_x.normalized().dot(x_axis)) < 0.98:
            continue
        omn, omx = common.bounds_in(rinv, op)
        if omx.x <= mn.x + common.EPS or omn.x >= mx.x - common.EPS:
            continue
        if omx.z <= mn.z + common.EPS or omn.z >= mx.z - common.EPS:
            continue
        cy = (omn.y + omx.y) / 2.0
        if not (mn.y - tol <= cy <= mx.y + tol):
            continue
        holes.append([max(omn.x, mn.x), min(omx.x, mx.x), max(omn.z, mn.z), min(omx.z, mx.z)])
    return rmat, mn, mx, holes


def cut_openings(walls, openings):
    """Split box walls around door/window parts that pass through them.

    Returns (number of walls cut, new wall pieces, number of walls skipped because
    they are not simple boxes).
    """
    cut, skipped = 0, 0
    new_pieces = []
    for wall in list(walls):
        if not is_part(wall, 'WALL'):
            continue
        rmat, mn, mx, holes = _holes_for(wall, openings)
        if not holes:
            continue
        if not is_box(wall):
            skipped += 1
            continue
        holes = _merge_holes(holes)
        materials = [m for m in wall.data.materials if m is not None] or None
        cy = (mn.y + mx.y) / 2.0
        depth = mx.y - mn.y
        for kind, a, b, z0, z1 in _wall_pieces(mn, mx, holes):
            size = (b - a, depth, z1 - z0)
            matrix = rmat @ Matrix.Translation(((a + b) / 2.0, cy, z0))
            piece = new_part('WALL', size, matrix, wall.users_collection[0], parent=wall.parent,
                             piece=kind, building=wall.conceptflow.building,
                             name="CF_Wall", materials=materials)
            for coll in wall.users_collection[1:]:
                coll.objects.link(piece)
            piece.hide_render = wall.hide_render
            piece.display_type = wall.display_type
            new_pieces.append(piece)
        common.remove_object(wall)
        cut += 1
    return cut, new_pieces, skipped


def merge_walls(walls, reference):
    """Replace parallel box walls by one box covering all of them. Returns the new wall."""
    rmat, _mn, _mx = common.frame_of(reference)
    rinv = rmat.inverted_safe()
    x_axis = (rmat.to_3x3() @ Vector((1.0, 0.0, 0.0))).normalized()
    z_axis = (rmat.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
    usable = []
    for wall in walls:
        m3 = wall.matrix_world.to_3x3()
        wx = (m3 @ Vector((1.0, 0.0, 0.0))).normalized()
        wz = (m3 @ Vector((0.0, 0.0, 1.0))).normalized()
        if abs(wx.dot(x_axis)) > 0.999 and abs(wz.dot(z_axis)) > 0.999:
            usable.append(wall)
    if len(usable) < 2:
        return None, len(walls) - len(usable)
    lo = Vector((math.inf, math.inf, math.inf))
    hi = Vector((-math.inf, -math.inf, -math.inf))
    for wall in usable:
        mn, mx = common.bounds_in(rinv, wall)
        lo = Vector((min(lo.x, mn.x), min(lo.y, mn.y), min(lo.z, mn.z)))
        hi = Vector((max(hi.x, mx.x), max(hi.y, mx.y), max(hi.z, mx.z)))
    size = tuple(hi - lo)
    matrix = rmat @ Matrix.Translation(((lo.x + hi.x) / 2.0, (lo.y + hi.y) / 2.0, lo.z))
    materials = [m for m in reference.data.materials if m is not None] or None
    merged = new_part('WALL', size, matrix, reference.users_collection[0], parent=reference.parent,
                      building=reference.conceptflow.building, name="CF_Wall", materials=materials)
    for coll in reference.users_collection[1:]:
        coll.objects.link(merged)
    for wall in usable:
        common.remove_object(wall)
    return merged, len(walls) - len(usable)


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

ROOF_ITEMS = [
    ('FLAT', "Flat", "A flat roof slab"),
    ('SHED', "Shed", "One sloping roof surface"),
    ('GABLE', "Gable", "Two roof surfaces meeting at a ridge, with triangular ends"),
]


def wall_openings(p, wall_matrix, length, wall_height, level, side, collection, building):
    t = p.wall_thickness
    slots_n = max(1, int(length // p.window_spacing))
    door_side = 'BACK' if p.open_side == 'FRONT' else 'FRONT'
    want_doors = p.doors if (level == 0 and side == door_side) else 0
    if level > 0 and side == door_side and getattr(p, "balcony", False):
        want_doors = 1  # a door out to the balcony
    slots_n = max(slots_n, want_doors)
    if want_doors and (slots_n - want_doors) % 2 == 1:
        # Keep the doors centred: same number of windows on both sides.
        if length / (slots_n + 1) >= p.door_width + 0.3:
            slots_n += 1
        elif slots_n - 1 >= want_doors:
            slots_n -= 1
    slot_w = length / slots_n
    centres = [-length / 2.0 + slot_w * (j + 0.5) for j in range(slots_n)]
    by_centre = sorted(range(slots_n), key=lambda j: abs(centres[j]))
    door_slots = set(by_centre[:want_doors])
    openings = []
    for j, x in enumerate(centres):
        if j in door_slots:
            w = min(p.door_width, slot_w - 0.3)
            h = min(p.door_height, wall_height - 0.1)
            if w < 0.4 or h < 0.5:
                continue
            m = wall_matrix @ Matrix.Translation((x, 0.0, 0.0))
            openings.append(new_part('DOOR', (w, t + 2 * FRAME_GAP, h), m, collection,
                                     building=building))
        elif p.windows:
            w = min(p.window_width, slot_w - 0.3)
            h = min(p.window_height, wall_height - p.sill_height - 0.15)
            if w < 0.2 or h < 0.2:
                continue
            m = wall_matrix @ Matrix.Translation((x, 0.0, p.sill_height))
            openings.append(new_part('WINDOW', (w, t + 2 * FRAME_GAP, h), m, collection,
                                     building=building, sill=p.sill_height))
    return openings


def build_balcony(coll, building, outward, centre_x, level_z, wall_outer, width, depth, slab, rail=1.0):
    """A slab with railings on a wall. ``outward`` is the rotation (radians about Z) of the wall frame.

    The wall frame is the front wall's: X along the wall, the balcony sticks out towards -Y.
    """
    frame = Matrix.Rotation(outward, 4, 'Z')
    rt = 0.08
    y_out = wall_outer - depth / 2.0
    new_part('FLOOR', (width, depth + 0.1, slab), frame @ Matrix.Translation((centre_x, y_out + 0.05, level_z)),
             coll, building=building, name="CF_Balcony")
    z = level_z
    new_part('WALL', (width, rt, rail), frame @ Matrix.Translation((centre_x, wall_outer - depth + rt / 2.0, z)),
             coll, building=building, name="CF_Railing")
    for sx in (-1.0, 1.0):
        m = frame @ Matrix.Translation((centre_x + sx * (width / 2.0 - rt / 2.0), y_out, z)) @ Matrix.Rotation(math.pi / 2.0, 4, 'Z')
        new_part('WALL', (depth - rt, rt, rail), m, coll, building=building, name="CF_Railing")


def build_roof(coll, building, W, D, top, st, kind, pitch, overhang):
    """Roof on top of walls that end at height ``top``."""
    if kind == 'FLAT':
        new_part('FLOOR', (W, D, st), Matrix.Translation((0.0, 0.0, top + st)), coll,
                 building=building, name="CF_Roof")
        return
    along_x = W >= D
    ridge_len, span = (W, D) if along_x else (D, W)
    rot = Matrix.Rotation(0.0 if along_x else math.pi / 2.0, 4, 'Z')
    theta = math.radians(min(max(pitch, 5.0), 70.0))
    ov = max(overhang, 0.0)
    if kind == 'GABLE':
        half = span / 2.0 + ov
        rise = math.tan(theta) * span / 2.0
        length = half / math.cos(theta)
        z_ridge = top + rise
        for sgn in (-1.0, 1.0):
            zc = z_ridge - math.tan(theta) * half / 2.0
            m = rot @ Matrix.Translation((0.0, sgn * half / 2.0, zc)) @ Matrix.Rotation(-sgn * theta, 4, 'X')
            new_part('FLOOR', (ridge_len + 2 * ov, length + st, st), m, coll, building=building, name="CF_Roof")
        profile = [(-span / 2.0, 0.0), (span / 2.0, 0.0), (0.0, rise)]
        height = rise
    else:  # SHED
        rise = math.tan(theta) * span
        length = (span + 2 * ov) / math.cos(theta)
        zc = top + rise / 2.0
        m = rot @ Matrix.Translation((0.0, 0.0, zc)) @ Matrix.Rotation(theta, 4, 'X')
        new_part('FLOOR', (ridge_len + 2 * ov, length, st), m, coll, building=building, name="CF_Roof")
        profile = [(-span / 2.0, 0.0), (span / 2.0, 0.0), (span / 2.0, rise)]
        height = rise
    t = 0.2
    for sgn in (-1.0, 1.0):
        mesh = prism_mesh("CF_GableEnd", profile, t)
        m = rot @ Matrix.Translation((sgn * (ridge_len / 2.0 - t / 2.0), 0.0, top)) @ Matrix.Rotation(math.pi / 2.0, 4, 'Z')
        new_custom_part('WALL', mesh, (span, t, max(height, 0.01)), m, coll, slots=['WALL'],
                        building=building, name="CF_GableEnd")


def build_building(scene, p, matrix, parent=None):
    """Build a graybox building from the settings ``p`` and return its root empty.

    ``p`` has the properties of the Build Building operator. ``matrix`` places the root.
    """
    building = uuid.uuid4().hex[:8]
    gray = graybox_collection(scene)
    coll = bpy.data.collections.new("CF_Building")
    coll["cf_role"] = "BUILDING"
    gray.children.link(coll)

    W, D, H = p.width, p.depth, p.floor_height
    t, st = p.wall_thickness, min(p.slab_thickness, p.floor_height * 0.5)
    n = p.floors
    sides = (
        ('FRONT', (0.0, -D / 2.0 + t / 2.0), 0.0, W),
        ('RIGHT', (W / 2.0 - t / 2.0, 0.0), math.pi / 2.0, D - 2.0 * t),
        ('BACK', (0.0, D / 2.0 - t / 2.0), math.pi, W),
        ('LEFT', (-W / 2.0 + t / 2.0, 0.0), -math.pi / 2.0, D - 2.0 * t),
    )
    # Everything is built around the world origin first, then parented to a root
    # empty that is moved to ``matrix``.
    for level in range(n):
        z0 = level * H
        new_part('FLOOR', (W, D, st), Matrix.Translation((0.0, 0.0, z0 + st)), coll,
                 building=building)
        wall_z = z0 + st
        wall_h = H - st
        for side, (cx, cy), rot_z, length in sides:
            if side == p.open_side or length <= 0.05:
                continue
            m = Matrix.Translation((cx, cy, wall_z)) @ Matrix.Rotation(rot_z, 4, 'Z')
            wall = new_part('WALL', (length, t, wall_h), m, coll, building=building)
            openings = wall_openings(p, m, length, wall_h, level, side, coll, building)
            if openings:
                cut_openings([wall], openings)
        if getattr(p, "balcony", False) and level >= 1:
            door_side = 'BACK' if p.open_side == 'FRONT' else 'FRONT'
            if door_side != p.open_side:
                depth = getattr(p, "balcony_depth", 1.2)
                width = min(max(W * 0.5, 1.8), 3.5, W - 0.6)
                if width > 0.8:
                    outward = 0.0 if door_side == 'FRONT' else math.pi
                    build_balcony(coll, building, outward, 0.0, z0 + st, -D / 2.0, width, depth, st)
    top = n * H
    if p.roof:
        build_roof(coll, building, W, D, top, st, getattr(p, "roof_type", 'FLAT'),
                   getattr(p, "roof_pitch", 30.0), getattr(p, "roof_overhang", 0.3))
    if p.pillars:
        ps = max(t * 2.0, 0.2)
        ph = top + (st if p.roof else 0.0)
        for sx in (-1.0, 1.0):
            for sy in (-1.0, 1.0):
                m = Matrix.Translation((sx * (W / 2.0 - ps / 2.0), sy * (D / 2.0 - ps / 2.0), 0.0))
                new_part('PILLAR', (ps, ps, ph), m, coll, building=building)

    root = bpy.data.objects.new("CF_Building", None)
    root.empty_display_type = 'PLAIN_AXES'
    root.empty_display_size = 1.0
    root.conceptflow.building = building
    coll.objects.link(root)
    for obj in coll.objects:
        if obj is not root and obj.parent is None:
            obj.parent = root
    if parent is not None:
        root.parent = parent
        root.matrix_parent_inverse = parent.matrix_world.inverted_safe()
    root.matrix_world = matrix
    return root


def _cursor_matrix(scene, z_offset=0.0):
    cursor = scene.cursor
    yaw = cursor.matrix.to_euler('XYZ').z
    loc = cursor.location.copy()
    loc.z += z_offset
    return Matrix.LocRotScale(loc, Euler((0.0, 0.0, yaw)), None)


class CONCEPTFLOW_OT_add_part(Operator):
    bl_idname = "conceptflow.add_part"
    bl_label = "Add Graybox Part"
    bl_description = "Add a graybox part at the 3D cursor"
    bl_options = {'REGISTER', 'UNDO'}

    part: EnumProperty(name="Part", items=ADDABLE_PARTS, default='WALL')
    size_x: FloatProperty(name="Width", min=0.0, soft_max=100.0, unit='LENGTH', default=0.0,
                          options={'SKIP_SAVE'})
    size_y: FloatProperty(name="Depth", min=0.0, soft_max=100.0, unit='LENGTH', default=0.0,
                          options={'SKIP_SAVE'})
    size_z: FloatProperty(name="Height", min=0.0, soft_max=100.0, unit='LENGTH', default=0.0,
                          options={'SKIP_SAVE'})
    round_shape: BoolProperty(name="Round", description="Make the pillar round", default=False,
                              options={'SKIP_SAVE'})
    steps: IntProperty(name="Steps", description="Number of steps. 0 = automatic (about 18 cm each)",
                       min=0, max=200, default=0, options={'SKIP_SAVE'})
    sill: FloatProperty(name="Sill Height", description="Height of the window bottom above the floor",
                        min=0.0, soft_max=10.0, unit='LENGTH', default=0.9, options={'SKIP_SAVE'})
    insert: BoolProperty(name="Insert into Active Wall",
                         description="When a ConceptFlow wall is selected and active, place the door "
                                     "or window in it and cut the opening",
                         default=True, options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        if properties is None:
            return tip(cls.bl_description)
        return tip(PART_TIPS.get(properties.part, cls.bl_description))

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.mode == 'OBJECT'

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "part")
        col = layout.column(align=True)
        col.prop(self, "size_x")
        col.prop(self, "size_y", text="Thickness" if self.part in {'WALL', 'DOOR', 'WINDOW'} else "Depth")
        col.prop(self, "size_z")
        if self.part == 'PILLAR':
            layout.prop(self, "round_shape")
        elif self.part == 'STAIRS':
            layout.prop(self, "steps")
        elif self.part in {'DOOR', 'WINDOW'}:
            if self.part == 'WINDOW':
                layout.prop(self, "sill")
            layout.prop(self, "insert")

    def _target_wall(self, context):
        obj = context.active_object
        if not (self.insert and self.part in {'DOOR', 'WINDOW'}):
            return None
        if is_part(obj, 'WALL') and obj.select_get():
            return obj
        return None

    @guarded
    def execute(self, context):
        scene = context.scene
        defaults = PART_DEFAULTS[self.part]
        if self.size_x <= 0.0:
            self.size_x = defaults[0]
        if self.size_y <= 0.0:
            self.size_y = defaults[1]
        if self.size_z <= 0.0:
            self.size_z = defaults[2]

        wall = self._target_wall(context)
        if wall is not None:
            rmat, mn, mx = common.frame_of(wall)
            cur = rmat.inverted_safe() @ scene.cursor.location
            half = self.size_x / 2.0
            centre_y = (mn.y + mx.y) / 2.0
            if mn.x <= cur.x <= mx.x and abs(cur.y - centre_y) < 2.0:
                x = min(max(cur.x, mn.x + half), mx.x - half) if (mx.x - mn.x) > self.size_x else (mn.x + mx.x) / 2.0
            else:
                x = (mn.x + mx.x) / 2.0
            z0 = mn.z + (self.sill if self.part == 'WINDOW' else 0.0)
            self.size_y = (mx.y - mn.y) + 2.0 * FRAME_GAP
            matrix = rmat @ Matrix.Translation((x, centre_y, z0))
            collection = wall.users_collection[0]
            parent = wall.parent
            building = wall.conceptflow.building
        else:
            matrix = _cursor_matrix(scene, self.sill if self.part == 'WINDOW' else 0.0)
            collection = graybox_collection(scene)
            parent = None
            building = ""

        size = (self.size_x, self.size_y, self.size_z)
        obj = new_part(self.part, size, matrix, collection, parent=parent,
                       round_pillar=self.round_shape, steps=self.steps, building=building,
                       sill=self.sill if self.part == 'WINDOW' else 0.0)
        if wall is not None:
            # Neighbouring wall pieces are cut too when the opening is wider than the active one.
            walls = [o for o in scene.objects if is_part(o, 'WALL')]
            cut_openings(walls, [obj])
        common.select_only(context, [obj], obj)
        return {'FINISHED'}


OPEN_SIDE_ITEMS = [
    ('NONE', "None", "All four walls"),
    ('FRONT', "Front", "Leave out the front wall (-Y), handy for looking into a room"),
    ('BACK', "Back", "Leave out the back wall (+Y)"),
    ('LEFT', "Left", "Leave out the left wall (-X)"),
    ('RIGHT', "Right", "Leave out the right wall (+X)"),
]


class CONCEPTFLOW_OT_build_building(Operator):
    bl_idname = "conceptflow.build_building"
    bl_label = "Build Building"
    bl_description = ("Build a graybox room or building at the 3D cursor: floors, walls with door "
                      "and window openings, and a roof. Every piece stays a separate part")
    bl_options = {'REGISTER', 'UNDO'}

    width: FloatProperty(name="Width", description="Size along X", min=1.0, soft_max=100.0,
                         default=6.0, unit='LENGTH')
    depth: FloatProperty(name="Depth", description="Size along Y", min=1.0, soft_max=100.0,
                         default=5.0, unit='LENGTH')
    floors: IntProperty(name="Floors", description="Number of storeys", min=1, max=30, default=1)
    floor_height: FloatProperty(name="Floor Height", description="Height of one storey, slab included",
                                min=1.5, soft_max=10.0, default=3.2, unit='LENGTH')
    wall_thickness: FloatProperty(name="Wall Thickness", min=0.05, soft_max=1.0, default=0.2,
                                  unit='LENGTH')
    slab_thickness: FloatProperty(name="Slab Thickness", min=0.02, soft_max=1.0, default=0.15,
                                  unit='LENGTH')
    doors: IntProperty(name="Doors",
                       description="Doors in the front wall of the ground floor "
                                   "(in the back wall when the front is open)",
                       min=0, max=10, default=1)
    door_width: FloatProperty(name="Door Width", min=0.4, soft_max=5.0, default=1.0, unit='LENGTH')
    door_height: FloatProperty(name="Door Height", min=1.0, soft_max=6.0, default=2.1, unit='LENGTH')
    windows: BoolProperty(name="Windows", description="Add windows to the walls", default=True)
    window_spacing: FloatProperty(name="Window Spacing",
                                  description="About one window (or door) for this much wall length",
                                  min=0.8, soft_max=20.0, default=2.5, unit='LENGTH')
    window_width: FloatProperty(name="Window Width", min=0.2, soft_max=10.0, default=1.2,
                                unit='LENGTH')
    window_height: FloatProperty(name="Window Height", min=0.2, soft_max=10.0, default=1.3,
                                 unit='LENGTH')
    sill_height: FloatProperty(name="Sill Height", min=0.0, soft_max=5.0, default=0.9, unit='LENGTH')
    roof: BoolProperty(name="Roof", description="Add a roof", default=True)
    roof_type: EnumProperty(name="Roof Type", items=ROOF_ITEMS, default='FLAT')
    roof_pitch: FloatProperty(name="Roof Pitch", description="Slope of a shed or gable roof, in degrees",
                              min=5.0, max=70.0, default=30.0)
    roof_overhang: FloatProperty(name="Overhang", description="How far a sloping roof sticks out of the walls",
                                 min=0.0, soft_max=2.0, default=0.3, unit='LENGTH')
    balcony: BoolProperty(name="Balcony",
                          description="Add a balcony with a railing and a door on every upper floor "
                                      "(on the door side)", default=False)
    balcony_depth: FloatProperty(name="Balcony Depth", min=0.5, soft_max=4.0, default=1.2, unit='LENGTH')
    pillars: BoolProperty(name="Corner Pillars", description="Add pillars at the four corners",
                          default=False)
    open_side: EnumProperty(name="Open Side", items=OPEN_SIDE_ITEMS, default='NONE')

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.mode == 'OBJECT'

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=340)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column(align=True)
        col.prop(self, "width")
        col.prop(self, "depth")
        col = layout.column(align=True)
        col.prop(self, "floors")
        col.prop(self, "floor_height")
        col = layout.column(align=True)
        col.prop(self, "wall_thickness")
        col.prop(self, "slab_thickness")
        layout.prop(self, "open_side")
        col = layout.column(align=True)
        col.prop(self, "doors")
        sub = col.column(align=True)
        sub.active = self.doors > 0
        sub.prop(self, "door_width")
        sub.prop(self, "door_height")
        layout.prop(self, "windows")
        col = layout.column(align=True)
        col.active = self.windows
        col.prop(self, "window_spacing")
        col.prop(self, "window_width")
        col.prop(self, "window_height")
        col.prop(self, "sill_height")
        col = layout.column(heading="Extras")
        col.prop(self, "roof")
        sub = col.column(align=True)
        sub.active = self.roof
        sub.prop(self, "roof_type", text="")
        if self.roof_type != 'FLAT':
            sub.prop(self, "roof_pitch")
            sub.prop(self, "roof_overhang")
        col.prop(self, "pillars")
        col.prop(self, "balcony")
        if self.balcony:
            sub = col.column()
            sub.active = self.floors > 1
            sub.prop(self, "balcony_depth")

    @guarded
    def execute(self, context):
        scene = context.scene
        root = build_building(scene, self, _cursor_matrix(scene))
        common.select_only(context, [root], root)
        count = sum(1 for c in root.children_recursive)
        self.report({'INFO'}, rpt("Built a building with {count} parts").format(count=count))
        return {'FINISHED'}


class CONCEPTFLOW_OT_cut_openings(Operator):
    bl_idname = "conceptflow.cut_openings"
    bl_label = "Cut Openings"
    bl_description = ("Cut openings in the walls that the selected doors and windows pass through. "
                      "The walls are split into separate pieces")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and any(
            is_part(o, 'DOOR', 'WINDOW') for o in context.selected_objects)

    @guarded
    def execute(self, context):
        openings = [o for o in context.selected_objects if is_part(o, 'DOOR', 'WINDOW')]
        walls = [o for o in context.scene.objects if is_part(o, 'WALL') and o not in openings]
        cut, _pieces, skipped = cut_openings(walls, openings)
        if cut == 0 and skipped == 0:
            self.report({'WARNING'}, rpt("No wall overlaps the selected doors or windows"))
            return {'CANCELLED'}
        msg = rpt("Cut openings in {count} walls").format(count=cut)
        if skipped:
            msg += " " + rpt("({count} walls skipped: not simple boxes)").format(count=skipped)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class CONCEPTFLOW_OT_merge_walls(Operator):
    bl_idname = "conceptflow.merge_walls"
    bl_label = "Merge Walls"
    bl_description = ("Join the selected parallel wall pieces back into one solid wall "
                      "(for example after deleting a door)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and sum(
            1 for o in context.selected_objects if is_part(o, 'WALL')) >= 2

    @guarded
    def execute(self, context):
        walls = [o for o in context.selected_objects if is_part(o, 'WALL')]
        reference = context.active_object if context.active_object in walls else walls[0]
        merged, skipped = merge_walls(walls, reference)
        if merged is None:
            self.report({'WARNING'}, rpt("Select at least two parallel walls"))
            return {'CANCELLED'}
        common.select_only(context, [merged], merged)
        msg = rpt("Merged walls")
        if skipped:
            msg += " " + rpt("({count} walls skipped: not parallel)").format(count=skipped)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


classes = (
    CONCEPTFLOW_OT_add_part,
    CONCEPTFLOW_OT_build_building,
    CONCEPTFLOW_OT_cut_openings,
    CONCEPTFLOW_OT_merge_walls,
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
