# SPDX-License-Identifier: GPL-3.0-or-later
# ConceptFlow for Blender
"""Branch Tree: save states, jump back to any of them and branch from there.

Snapshot, branch and history in one place. A state holds cameras, lights,
the world, visibility / color management and (optionally) object transforms.

Note: never keep a Python reference to a tree node across ``nodes.add()`` or
``nodes.remove()``; always look nodes up again by ``uid``.
"""

import time

import bpy
from bpy.props import IntProperty, StringProperty
from bpy.types import Operator, UIList

from . import state as cf_state
from .common import guarded, icon, rpt

ORIGINAL_NAME = "Original"
AUTO_NAME = "Auto-save"


# ---------------------------------------------------------------------------
# Tree helpers (also used by other modules)
# ---------------------------------------------------------------------------

def tree_of(scene):
    return scene.conceptflow.tree


def node_by_uid(tree, uid):
    for i, node in enumerate(tree.nodes):
        if node.uid == uid:
            return i, node
    return -1, None


def selected_node(tree):
    if 0 <= tree.index < len(tree.nodes):
        return tree.nodes[tree.index]
    return None


def current_node(tree):
    return node_by_uid(tree, tree.current_uid)[1]


def recompute_depths(tree):
    parents = {n.uid: n.parent_uid for n in tree.nodes}
    for node in tree.nodes:
        depth, parent, seen = 0, node.parent_uid, set()
        while parent in parents and parent not in seen and depth < 1000:
            seen.add(parent)
            depth += 1
            parent = parents[parent]
        if node.depth != depth:
            node.depth = depth


def display_order(tree):
    """Indices of the nodes in depth-first order (parents before children)."""
    uids = {n.uid for n in tree.nodes}
    children, roots = {}, []
    for i, node in enumerate(tree.nodes):
        if node.parent_uid in uids and node.parent_uid != node.uid:
            children.setdefault(node.parent_uid, []).append(i)
        else:
            roots.append(i)
    order, seen = [], set()
    stack = list(reversed(roots))
    while stack:
        i = stack.pop()
        if i in seen:
            continue
        seen.add(i)
        order.append(i)
        stack.extend(reversed(children.get(tree.nodes[i].uid, [])))
    order.extend(i for i in range(len(tree.nodes)) if i not in seen)
    return order


def capture_scene(scene):
    tree = tree_of(scene)
    return cf_state.capture(scene, {"xforms": tree.save_transforms})


def _default_name(tree):
    if len(tree.nodes) == 0:
        return ORIGINAL_NAME
    manual = sum(1 for n in tree.nodes if n.origin == 'MANUAL')
    return "State {}".format(manual)


def add_node(scene, name, packed, parent_uid, origin='MANUAL', note=""):
    tree = tree_of(scene)
    uid = tree.next_uid
    tree.next_uid += 1
    node = tree.nodes.add()
    node.uid = uid
    node.name = name
    node.parent_uid = parent_uid
    node.origin = origin
    node.note = note
    node.created = time.strftime("%Y-%m-%d %H:%M")
    node.data = packed
    recompute_depths(tree)
    tree.current_uid = uid
    tree.index = len(tree.nodes) - 1
    return uid


def save_state(scene, name=None, origin='MANUAL', note=""):
    """Save the present state as a child of the current node. Returns the new uid."""
    tree = tree_of(scene)
    name = name or _default_name(tree)
    packed = cf_state.pack(capture_scene(scene))
    cur = current_node(tree)
    parent = cur.uid if cur is not None else -1
    return add_node(scene, name, packed, parent, origin, note)


def ensure_original(scene):
    """Save "Original" when the tree is still empty. Returns True when it saved."""
    if len(tree_of(scene).nodes) == 0:
        save_state(scene, ORIGINAL_NAME, 'MANUAL')
        return True
    return False


def has_unsaved_changes(scene):
    tree = tree_of(scene)
    cur = current_node(tree)
    if cur is None:
        return len(tree.nodes) > 0
    return not cf_state.equal(capture_scene(scene), cf_state.unpack(cur.data))


def checkpoint(scene, name=AUTO_NAME, origin='AUTO'):
    """Make sure the present state is kept before something replaces it.

    Saves "Original" when the tree is empty, or an automatic state when there are
    unsaved changes. Returns the uid of the saved node, or -1 when nothing was needed.
    """
    tree = tree_of(scene)
    if len(tree.nodes) == 0:
        return save_state(scene, ORIGINAL_NAME, 'MANUAL')
    if has_unsaved_changes(scene):
        return save_state(scene, name, origin)
    return -1


def restore_uid(scene, uid):
    """Restore the node ``uid`` and make it current. Returns a RestoreReport or None."""
    tree = tree_of(scene)
    index, node = node_by_uid(tree, uid)
    if node is None:
        return None
    report = cf_state.restore(scene, cf_state.unpack(node.data))
    tree.current_uid = uid
    tree.index = index
    return report


def delete_uid(tree, uid):
    index, node = node_by_uid(tree, uid)
    if node is None:
        return False
    parent = node.parent_uid
    for other in tree.nodes:
        if other.parent_uid == uid:
            other.parent_uid = parent
    if tree.current_uid == uid:
        tree.current_uid = parent
    tree.nodes.remove(index)
    if tree.index >= index:
        tree.index = max(0, tree.index - 1)
    tree.index = min(tree.index, max(0, len(tree.nodes) - 1))
    recompute_depths(tree)
    return True


def _target_uid(tree, uid):
    if uid >= 0:
        return uid
    node = selected_node(tree)
    return node.uid if node is not None else -1


# ---------------------------------------------------------------------------
# UI list
# ---------------------------------------------------------------------------

class CONCEPTFLOW_UL_tree(UIList):
    bl_idname = "CONCEPTFLOW_UL_tree"

    def draw_item(self, context, layout, data, item, icon_value, active_data, active_propname,
                  index=0, flt_flag=0):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            if item.depth > 0 and not self.filter_name:
                row.separator(factor=1.4 * min(item.depth, 10))
            if item.uid == data.current_uid:
                ic = 'RADIOBUT_ON'
            elif item.origin != 'MANUAL':
                ic = 'TIME'
            else:
                ic = 'RADIOBUT_OFF'
            row.prop(item, "name", text="", emboss=False, icon=icon(ic))
            op = row.operator("conceptflow.tree_restore", text="", icon=icon('LOOP_BACK'),
                              emboss=False)
            op.uid = item.uid
        else:
            layout.alignment = 'CENTER'
            layout.label(text="", icon=icon('RADIOBUT_OFF'))

    def filter_items(self, context, data, propname):
        nodes = getattr(data, propname)
        flags = [self.bitflag_filter_item] * len(nodes)
        if self.filter_name:
            flags = bpy.types.UI_UL_list.filter_items_by_name(
                self.filter_name, self.bitflag_filter_item, nodes, "name")
        order = display_order(data)
        new_order = [0] * len(nodes)
        for position, i in enumerate(order):
            new_order[i] = position
        return flags, new_order


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class CONCEPTFLOW_OT_tree_save(Operator):
    bl_idname = "conceptflow.tree_save"
    bl_label = "Save State"
    bl_description = ("Save the cameras, lights, world, visibility and transforms as a new state "
                      "under the current one. The first state is called Original")
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty(name="Name", description="Name of the new state. Empty = automatic",
                         default="", options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    @guarded
    def execute(self, context):
        scene = context.scene
        uid = save_state(scene, self.name.strip() or None)
        _i, node = node_by_uid(tree_of(scene), uid)
        self.report({'INFO'}, rpt('Saved state "{name}"').format(name=node.name))
        return {'FINISHED'}


class CONCEPTFLOW_OT_tree_restore(Operator):
    bl_idname = "conceptflow.tree_restore"
    bl_label = "Go to State"
    bl_description = ("Restore this state. Unsaved changes are kept automatically, and the next "
                      "save branches from here")
    bl_options = {'REGISTER', 'UNDO'}

    uid: IntProperty(default=-1, options={'SKIP_SAVE', 'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.scene is not None and len(context.scene.conceptflow.tree.nodes) > 0

    @guarded
    def execute(self, context):
        scene = context.scene
        tree = tree_of(scene)
        uid = _target_uid(tree, self.uid)
        if node_by_uid(tree, uid)[1] is None:
            self.report({'WARNING'}, rpt("Select a state first"))
            return {'CANCELLED'}
        auto_uid = checkpoint(scene)
        report = restore_uid(scene, uid)
        _i, node = node_by_uid(tree, uid)
        msg = rpt('Restored "{name}"').format(name=node.name)
        if report is not None and report.missing:
            msg += " " + rpt("({count} missing items skipped)").format(count=len(report.missing))
        if auto_uid >= 0:
            msg += " " + rpt("Unsaved changes were kept as a new state")
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class CONCEPTFLOW_OT_tree_overwrite(Operator):
    bl_idname = "conceptflow.tree_overwrite"
    bl_label = "Overwrite State"
    bl_description = "Replace the selected state with the present scene"
    bl_options = {'REGISTER', 'UNDO'}

    uid: IntProperty(default=-1, options={'SKIP_SAVE', 'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.scene is not None and selected_node(context.scene.conceptflow.tree) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    @guarded
    def execute(self, context):
        scene = context.scene
        tree = tree_of(scene)
        uid = _target_uid(tree, self.uid)
        index, node = node_by_uid(tree, uid)
        if node is None:
            return {'CANCELLED'}
        node.data = cf_state.pack(capture_scene(scene))
        node.created = time.strftime("%Y-%m-%d %H:%M")
        tree.current_uid = uid
        tree.index = index
        self.report({'INFO'}, rpt('Overwrote "{name}"').format(name=node.name))
        return {'FINISHED'}


class CONCEPTFLOW_OT_tree_delete(Operator):
    bl_idname = "conceptflow.tree_delete"
    bl_label = "Delete State"
    bl_description = "Delete the selected state. Its branches stay and move up one level"
    bl_options = {'REGISTER', 'UNDO'}

    uid: IntProperty(default=-1, options={'SKIP_SAVE', 'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.scene is not None and selected_node(context.scene.conceptflow.tree) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    @guarded
    def execute(self, context):
        tree = tree_of(context.scene)
        uid = _target_uid(tree, self.uid)
        if not delete_uid(tree, uid):
            return {'CANCELLED'}
        self.report({'INFO'}, rpt("Deleted the state"))
        return {'FINISHED'}


class CONCEPTFLOW_OT_tree_clear(Operator):
    bl_idname = "conceptflow.tree_clear"
    bl_label = "Clear Tree"
    bl_description = "Delete every saved state of this scene. The scene itself does not change"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and len(context.scene.conceptflow.tree.nodes) > 0

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    @guarded
    def execute(self, context):
        tree = tree_of(context.scene)
        tree.nodes.clear()
        tree.current_uid = -1
        tree.index = 0
        self.report({'INFO'}, rpt("Cleared the tree"))
        return {'FINISHED'}


classes = (
    CONCEPTFLOW_UL_tree,
    CONCEPTFLOW_OT_tree_save,
    CONCEPTFLOW_OT_tree_restore,
    CONCEPTFLOW_OT_tree_overwrite,
    CONCEPTFLOW_OT_tree_delete,
    CONCEPTFLOW_OT_tree_clear,
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
