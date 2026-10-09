# Run inside Blender (Text Editor > Run Script). Development helper, not part of the add-on.
#
# Renders the CF_Test scene once per look at a small size (EEVEE) and writes one contact
# sheet PNG, so the looks can be compared at a glance. Nothing outside CF_Test is touched and
# the window scene is never switched.
#
#   exec(open(r"<path>/tools/render_contact_sheet.py", encoding="utf-8").read())
#   path, stats = render_looks(['ORIGINAL', 'HORROR', 'NOIR', 'MOONLIGHT', 'GOLDEN_HOUR', 'NEON'],
#                              "looks", res=(640, 360), cols=3, samples=48)
#   print(path, stats)   # stats = mean brightness per tile (0 = black picture)

import os
import tempfile

import bpy
import numpy as np


def render_looks(keys, tag, res=(480, 270), cols=3, samples=24, scene_name="CF_Test"):
    sc = bpy.data.scenes[scene_name]
    vl = sc.view_layers[0]
    out = os.path.join(tempfile.gettempdir(), "cf_diag")
    os.makedirs(out, exist_ok=True)
    r = sc.render
    old = (r.resolution_x, r.resolution_y, r.resolution_percentage, r.filepath)
    r.resolution_x, r.resolution_y, r.resolution_percentage = res[0], res[1], 100
    old_samples = getattr(sc.eevee, "taa_render_samples", None)
    if old_samples is not None:
        sc.eevee.taa_render_samples = samples
    tiles = []
    try:
        for key in keys:
            with bpy.context.temp_override(scene=sc, view_layer=vl):
                if key == 'ORIGINAL':
                    if sc.conceptflow.look.active != 'NONE':
                        bpy.ops.conceptflow.look_reset()
                else:
                    bpy.ops.conceptflow.look_apply(preset=key)
            r.filepath = os.path.join(out, "t_%s.png" % key)
            bpy.ops.render.render(scene=scene_name, write_still=True)
            img = bpy.data.images.load(r.filepath)
            arr = np.array(img.pixels[:], dtype=np.float32).reshape(res[1], res[0], 4)
            bpy.data.images.remove(img)
            tiles.append(arr)
    finally:
        r.resolution_x, r.resolution_y, r.resolution_percentage, r.filepath = old
        if old_samples is not None:
            sc.eevee.taa_render_samples = old_samples
    rows = []
    for i in range(0, len(tiles), cols):
        row = tiles[i:i + cols]
        while len(row) < cols:
            row.append(np.zeros_like(tiles[0]))
        rows.append(np.concatenate(row, axis=1))
    sheet = np.concatenate(rows[::-1], axis=0)  # Blender pixel rows run bottom-up
    h, w = sheet.shape[:2]
    img = bpy.data.images.new("cf_sheet_tmp", w, h, alpha=True, float_buffer=False)
    img.pixels = sheet.ravel().tolist()
    path = os.path.join(out, "sheet_%s.png" % tag)
    img.filepath_raw = path
    img.file_format = 'PNG'
    img.save()
    bpy.data.images.remove(img)
    stats = {k: round(float(t[..., :3].mean()), 3) for k, t in zip(keys, tiles)}
    return path, stats
