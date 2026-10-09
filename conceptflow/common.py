# SPDX-License-Identifier: GPL-3.0-or-later
# ConceptFlow for Blender
"""Shared helpers: translation shortcuts, safe operators, icons, collections, meshes."""

import functools
import traceback

import bpy
from mathutils import Matrix, Vector

ADDON_ID = __package__
PREFIX = "CF_"
EPS = 1e-4


# ---------------------------------------------------------------------------
# Translation shortcuts
# ---------------------------------------------------------------------------

def iface(msg):
    """Translate a UI string (labels and buttons built at run time)."""
    return bpy.app.translations.pgettext_iface(msg)


def tip(msg):
    """Translate a tooltip string."""
    return bpy.app.translations.pgettext_tip(msg)


def rpt(msg):
    """Translate a report (status bar) message."""
    fn = getattr(bpy.app.translations, "pgettext_rpt", None) or bpy.app.translations.pgettext_iface
    return fn(msg)


# ---------------------------------------------------------------------------
# Icons (only names that exist in the running Blender are used)
# ---------------------------------------------------------------------------

_ICONS = None


def icon(name, fallback="DOT"):
    global _ICONS
    if _ICONS is None:
        try:
            enum = bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items
            _ICONS = set(enum.keys())
        except Exception:
            _ICONS = set()
    if name in _ICONS:
        return name
    if fallback in _ICONS:
        return fallback
    return "NONE"


# ---------------------------------------------------------------------------
# Safe operators
# ---------------------------------------------------------------------------

last_error = ""  # full traceback of the last caught error (for support)


def guarded(fn):
    """Wrap Operator.execute so an unexpected error never stops the user.

    The error is printed to the console and shown in the status bar. For
    undoable operators the result is FINISHED so that an undo step exists and
    any partial change can be reverted with Ctrl+Z.
    """

    @functools.wraps(fn)
    def wrapper(self, context, *args, **kwargs):
        try:
            return fn(self, context, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - last line of defence
            global last_error
            last_error = traceback.format_exc()
            traceback.print_exc()
            msg = rpt("ConceptFlow: unexpected error ({error}). You can undo with Ctrl+Z")
            self.report({'ERROR'}, msg.format(error=exc))
            if 'UNDO' in getattr(self, "bl_options", set()):
                return {'FINISHED'}
            return {'CANCELLED'}

    return wrapper


# ---------------------------------------------------------------------------
# Scene helpers
# ---------------------------------------------------------------------------

def view_layer_of(scene):
    """The view layer to use for visibility queries on ``scene``."""
    ctx = bpy.context
    if getattr(ctx, "scene", None) == scene and getattr(ctx, "view_layer", None) is not None:
        return ctx.view_layer
    return scene.view_layers[0] if scene.view_layers else None


def ensure_child_collection(parent, name, role):
    """Return the child collection of ``parent`` tagged with ``role``; create it if needed."""
    for child in parent.children:
        if child.get("cf_role") == role:
            return child
    coll = bpy.data.collections.new(name)
    coll["cf_role"] = role
    parent.children.link(coll)
    return coll


def remove_object(obj):
    """Delete an object and its mesh when nothing else uses the mesh."""
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if data is not None and data.users == 0 and isinstance(data, bpy.types.Mesh):
        bpy.data.meshes.remove(data)


def select_only(context, objects, active=None):
    """Select ``objects`` (and make ``active`` active) in the context view layer."""
    view_layer = context.view_layer
    keep = set(objects)
    for ob in view_layer.objects:
        # Objects removed earlier in the same operator can show up as None.
        if ob is None or ob in keep:
            continue
        try:
            if ob.select_get(view_layer=view_layer):
                ob.select_set(False, view_layer=view_layer)
        except (RuntimeError, ReferenceError):
            pass
    for ob in objects:
        try:
            ob.select_set(True, view_layer=view_layer)
        except (RuntimeError, ReferenceError):
            pass
    if active is not None:
        try:
            view_layer.objects.active = active
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Mesh helpers
# ---------------------------------------------------------------------------

_BOX_FACES = ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7))


def bm_box(bm, mn, mx, mat_index=0):
    """Add an axis aligned box from ``mn`` to ``mx`` with outward normals."""
    x0, y0, z0 = mn
    x1, y1, z1 = mx
    co = ((x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
          (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))
    verts = [bm.verts.new(c) for c in co]
    for face in _BOX_FACES:
        f = bm.faces.new([verts[i] for i in face])
        f.material_index = mat_index


def mesh_from_bm(name, bm):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return mesh


def frame_of(obj):
    """Return (R, mn, mx): rotation+location matrix of ``obj`` and its scaled local bounds."""
    loc, rot, sca = obj.matrix_world.decompose()
    rmat = Matrix.LocRotScale(loc, rot, Vector((1.0, 1.0, 1.0)))
    pts = [Vector((c[0] * sca.x, c[1] * sca.y, c[2] * sca.z)) for c in obj.bound_box]
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return rmat, mn, mx


def bounds_in(frame_inv, obj):
    """Bounds of ``obj`` (world bounding box corners) expressed in another frame."""
    mw = obj.matrix_world
    pts = [frame_inv @ (mw @ Vector(c)) for c in obj.bound_box]
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return mn, mx
