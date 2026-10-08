# Run inside Blender (Text Editor > Run Script) with the ConceptFlow add-on enabled.
# Development helper, not part of the add-on.
#
# Builds the test scene "CF_Test" used for all ConceptFlow checks: a 6 x 5 m graybox room with
# a door and windows, a camera inside (CF_Cam), a world (CF_World) and a sun (CF_Sun).
# Optionally creates simple asset objects and registers them for Asset Swap.
#
# Nothing outside CF_Test is changed and the window scene is never switched.
#
#   exec(open(r"<path>/tools/make_test_scene.py", encoding="utf-8").read())
#   make_test_scene()
#   make_test_assets()      # optional: wall / door / window / pillar / floor assets for Asset Swap

import math

import bmesh
import bpy
from mathutils import Euler


def make_test_scene():
    sc = bpy.data.scenes.get("CF_Test") or bpy.data.scenes.new("CF_Test")
    vl = sc.view_layers[0]
    engines = [i.identifier for i in sc.render.bl_rna.properties['engine'].enum_items]
    sc.render.engine = 'BLENDER_EEVEE_NEXT' if 'BLENDER_EEVEE_NEXT' in engines else 'BLENDER_EEVEE'
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = 960, 540, 100

    world = bpy.data.worlds.get("CF_World") or bpy.data.worlds.new("CF_World")
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.type == 'BACKGROUND')
    bg.inputs['Color'].default_value = (0.42, 0.5, 0.62, 1)
    bg.inputs['Strength'].default_value = 0.8
    sc.world = world

    with bpy.context.temp_override(scene=sc, view_layer=vl, selected_objects=[]):
        bpy.ops.conceptflow.build_building(width=6, depth=5, floors=1, doors=1, open_side='NONE')

    cam_data = bpy.data.cameras.get("CF_Cam") or bpy.data.cameras.new("CF_Cam")
    cam_data.lens = 24
    cam = bpy.data.objects.get("CF_Cam") or bpy.data.objects.new("CF_Cam", cam_data)
    if cam.name not in sc.collection.objects:
        sc.collection.objects.link(cam)
    cam.location = (2.3, 1.9, 1.6)
    cam.rotation_euler = Euler((math.radians(86), 0, math.radians(145)))
    sc.camera = cam

    light = bpy.data.lights.get("CF_Sun") or bpy.data.lights.new("CF_Sun", 'SUN')
    light.energy = 3.0
    sun = bpy.data.objects.get("CF_Sun") or bpy.data.objects.new("CF_Sun", light)
    if sun.name not in sc.collection.objects:
        sc.collection.objects.link(sun)
    sun.rotation_euler = Euler((math.radians(50), math.radians(10), math.radians(-30)))
    return sc


def _box(bm, mn, mx):
    x0, y0, z0 = mn
    x1, y1, z1 = mx
    vs = [bm.verts.new(v) for v in [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                                    (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]]
    for f in [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]:
        bm.faces.new([vs[i] for i in f])


def _make(name, boxes, color, collection):
    bm = bmesh.new()
    for mn, mx in boxes:
        _box(bm, mn, mx)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mat = bpy.data.materials.get("CFT_" + name) or bpy.data.materials.new("CFT_" + name)
    mat.diffuse_color = color
    mesh.materials.append(mat)
    ob = bpy.data.objects.new(name, mesh)
    collection.objects.link(ob)
    return ob


def make_test_assets(scene_name="CF_Test"):
    """Assets face -Y, length along X, base at z = 0, origin at the lower-left-front corner."""
    sc = bpy.data.scenes[scene_name]
    vl = sc.view_layers[0]
    tmp = bpy.data.collections.new("CF_TmpAssets")
    sc.collection.children.link(tmp)
    assets = {
        'wall_a': _make("T_WallBrick", [((0, -0.15, 0), (2, 0.15, 3)), ((0, -0.18, 0), (2, -0.15, 0.3))],
                        (0.55, 0.25, 0.2, 1), tmp),
        'wall_b': _make("T_WallPlaster", [((0, -0.15, 0), (1.5, 0.15, 3)), ((0, -0.18, 2.8), (1.5, -0.15, 3))],
                        (0.85, 0.8, 0.7, 1), tmp),
        'door': _make("T_Door", [((0, -0.05, 0), (1, 0.05, 2.2)), ((0.1, -0.08, 0.1), (0.9, -0.05, 2.1))],
                      (0.35, 0.2, 0.1, 1), tmp),
        'window': _make("T_Window", [((0, -0.08, 0), (1, 0.08, 0.1)), ((0, -0.08, 1.4), (1, 0.08, 1.5)),
                                     ((0, -0.08, 0), (0.1, 0.08, 1.5)), ((0.9, -0.08, 0), (1, 0.08, 1.5))],
                        (0.2, 0.2, 0.25, 1), tmp),
        'pillar': _make("T_Pillar", [((-0.25, -0.25, 0), (0.25, 0.25, 3)), ((-0.35, -0.35, 0), (0.35, 0.35, 0.2))],
                        (0.7, 0.7, 0.65, 1), tmp),
        'floor': _make("T_FloorTile", [((0, 0, 0), (1, 1, 0.1))], (0.4, 0.35, 0.3, 1), tmp),
    }
    for part, names in (('WALL', ['wall_a', 'wall_b']), ('DOOR', ['door']), ('WINDOW', ['window']),
                        ('PILLAR', ['pillar']), ('FLOOR', ['floor'])):
        objs = [assets[n] for n in names]
        with bpy.context.temp_override(scene=sc, view_layer=vl, selected_objects=objs, active_object=objs[0]):
            bpy.ops.conceptflow.swap_register(part=part)
    if not tmp.all_objects:
        bpy.data.collections.remove(tmp)
    return assets
