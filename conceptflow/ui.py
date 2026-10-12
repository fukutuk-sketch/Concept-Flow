# SPDX-License-Identifier: GPL-3.0-or-later
# ConceptFlow for Blender
"""Sidebar panels (3D Viewport > Sidebar > ConceptFlow)."""

import bpy
from bpy.types import Panel

from . import branch_tree, graybox_tools, look_presets, read_views, shot_maker, variations
from .common import icon, iface

PART_BUTTONS = (
    ('WALL', "Wall", 'MOD_SOLIDIFY'),
    ('PILLAR', "Pillar", 'MESH_CYLINDER'),
    ('STAIRS', "Stairs", 'IPO_CONSTANT'),
    ('FLOOR', "Floor", 'MESH_PLANE'),
    ('DOOR', "Door", 'MOD_LATTICE'),
    ('WINDOW', "Window", 'MESH_GRID'),
    ('BLOCK', "Block", 'MESH_CUBE'),
)


class _ConceptFlowPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "ConceptFlow"


class CONCEPTFLOW_PT_graybox(_ConceptFlowPanel, Panel):
    bl_idname = "CONCEPTFLOW_PT_graybox"
    bl_label = "Graybox"
    bl_order = 0

    def draw_header(self, context):
        self.layout.label(text="", icon=icon('HOME'))

    def draw(self, context):
        layout = self.layout
        in_object_mode = context.mode == 'OBJECT'

        col = layout.column(align=True)
        col.enabled = in_object_mode
        col.scale_y = 1.4
        col.operator("conceptflow.build_building", icon=icon('HOME'))

        layout.label(text="Add Part")
        grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=True,
                                align=True)
        grid.enabled = in_object_mode
        for part, label, ic in PART_BUTTONS:
            op = grid.operator("conceptflow.add_part", text=label, icon=icon(ic))
            op.part = part

        layout.label(text="Openings")
        row = layout.row(align=True)
        row.operator("conceptflow.cut_openings", icon=icon('MOD_BOOLEAN'))
        row.operator("conceptflow.merge_walls", icon=icon('AUTOMERGE_ON'))

        obj = context.active_object
        if obj is not None and obj.type in {'MESH', 'EMPTY'}:
            box = layout.box()
            box.label(text=obj.name, icon=icon('OBJECT_DATA'), translate=False)
            if obj.type == 'MESH':
                box.prop(obj.conceptflow, "part")
        if not in_object_mode:
            layout.label(text="Switch to Object Mode to add parts", icon=icon('INFO'))


class CONCEPTFLOW_PT_draw_walls(_ConceptFlowPanel, Panel):
    bl_idname = "CONCEPTFLOW_PT_draw_walls"
    bl_label = "Draw Walls"
    bl_parent_id = "CONCEPTFLOW_PT_graybox"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 1

    def draw_header(self, context):
        self.layout.label(text="", icon=icon('GREASEPENCIL'))

    def draw(self, context):
        layout = self.layout
        s = context.scene.conceptflow.draw
        col = layout.column()
        col.enabled = context.mode == 'OBJECT'
        col.scale_y = 1.4
        col.operator("conceptflow.draw_walls", icon=icon('GREASEPENCIL'))
        col = layout.column(align=True)
        col.prop(s, "height")
        col.prop(s, "thickness")
        col.prop(s, "snap")
        row = layout.row(align=True)
        row.prop(s, "floor_slab")
        sub = row.row(align=True)
        sub.active = s.floor_slab
        sub.prop(s, "slab", text="")
        layout.label(text="Click points on the ground plane at the 3D cursor height", icon=icon('INFO'))


class CONCEPTFLOW_PT_props(_ConceptFlowPanel, Panel):
    bl_idname = "CONCEPTFLOW_PT_props"
    bl_label = "Props & Street"
    bl_parent_id = "CONCEPTFLOW_PT_graybox"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 2

    def draw_header(self, context):
        self.layout.label(text="", icon=icon('OUTLINER_OB_GROUP_INSTANCE'))

    def draw(self, context):
        layout = self.layout
        in_object_mode = context.mode == 'OBJECT'
        grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=True, align=True)
        grid.enabled = in_object_mode
        for key in graybox_tools.PROP_ORDER:
            op = grid.operator("conceptflow.add_prop", text=iface(graybox_tools.PROPS[key][0]),
                               icon=icon(graybox_tools.PROP_ICONS.get(key, 'MESH_CUBE')), translate=False)
            op.kind = key
        col = layout.column(align=True)
        col.enabled = in_object_mode
        col.operator_menu_enum("conceptflow.add_stairs", "kind", text="Add Stairs...", icon=icon('IPO_CONSTANT'))
        col.operator("conceptflow.build_street", icon=icon('HOME'))


class CONCEPTFLOW_PT_asset_swap(_ConceptFlowPanel, Panel):
    bl_idname = "CONCEPTFLOW_PT_asset_swap"
    bl_label = "Asset Swap"
    bl_parent_id = "CONCEPTFLOW_PT_graybox"
    bl_order = 0

    def draw_header(self, context):
        self.layout.label(text="", icon=icon('FILE_REFRESH'))

    def draw(self, context):
        layout = self.layout
        swap = context.scene.conceptflow.swap
        in_object_mode = context.mode == 'OBJECT'

        row = layout.row(align=True)
        row.enabled = in_object_mode
        row.scale_y = 1.4
        op = row.operator("conceptflow.swap_assets", text="Swap All", icon=icon('FILE_REFRESH'))
        op.scope = 'ALL'
        op = row.operator("conceptflow.swap_assets", text="Selected")
        op.scope = 'SELECTED'

        layout.row(align=True).prop(swap, "display", expand=True)

        row = layout.row(align=True)
        row.enabled = in_object_mode
        row.prop(swap, "seed")
        row.operator("conceptflow.swap_shuffle", text="", icon=icon('MOD_NOISE'))
        op = row.operator("conceptflow.swap_clear", text="", icon=icon('X'))
        op.scope = 'ALL'

        box = layout.box()
        header = box.row(align=True)
        header.label(text="Assets by Part Type")
        header.prop(swap, "show_options", text="", icon=icon('PREFERENCES'), emboss=False)
        for part, label, ic in PART_BUTTONS:
            slot = getattr(swap, part.lower())
            split = box.split(factor=0.34, align=True)
            split.label(text=label, icon=icon(ic))
            split.prop(slot, "source", text="")
            if swap.show_options:
                sub = box.row(align=True)
                sub.active = slot.source is not None
                sub.prop(slot, "fit", text="")
                sub.prop(slot, "rotate", text="")
        col = box.column()
        col.enabled = in_object_mode
        col.operator_menu_enum("conceptflow.swap_register", "part", text="Register Selected as",
                               icon=icon('IMPORT'))
        box.label(text="Assets face -Y with their length along X", icon=icon('INFO'))


class CONCEPTFLOW_PT_branch_tree(_ConceptFlowPanel, Panel):
    bl_idname = "CONCEPTFLOW_PT_branch_tree"
    bl_label = "Branch Tree"
    bl_order = 1

    def draw_header(self, context):
        self.layout.label(text="", icon=icon('OUTLINER'))

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        tree = scene.conceptflow.tree

        row = layout.row(align=True)
        row.scale_y = 1.3
        save_text = "Save Original" if len(tree.nodes) == 0 else "Save State"
        row.operator("conceptflow.tree_save", text=save_text, icon=icon('ADD'))
        row.operator("conceptflow.tree_restore", text="Go to", icon=icon('LOOP_BACK'))

        if len(tree.nodes) == 0:
            box = layout.box()
            col = box.column(align=True)
            col.label(text="Save the present scene as Original,", icon=icon('INFO'))
            col.label(text="then try ideas and branch freely.")
            return

        layout.template_list("CONCEPTFLOW_UL_tree", "", tree, "nodes", tree, "index",
                             rows=6, maxrows=12)

        row = layout.row(align=True)
        row.operator("conceptflow.tree_overwrite", text="Overwrite", icon=icon('FILE_TICK'))
        row.operator("conceptflow.tree_delete", text="Delete", icon=icon('TRASH'))
        row.operator("conceptflow.tree_clear", text="", icon=icon('X'))

        node = branch_tree.selected_node(tree)
        if node is not None:
            col = layout.column(align=True)
            col.prop(node, "note", text="", icon=icon('GREASEPENCIL'), placeholder="Note")
            info = "{}  {}".format(node.created, iface(node.bl_rna.properties["origin"].enum_items[node.origin].name))
            col.label(text=info, translate=False)

        cur = branch_tree.current_node(tree)
        if cur is not None:
            layout.label(text=iface("Current: {name}").format(name=cur.name), icon=icon('RADIOBUT_ON'),
                         translate=False)

        layout.prop(tree, "save_transforms")


class CONCEPTFLOW_PT_look_presets(_ConceptFlowPanel, Panel):
    bl_idname = "CONCEPTFLOW_PT_look_presets"
    bl_label = "Look Presets"
    bl_order = 2

    def draw_header(self, context):
        self.layout.label(text="", icon=icon('LIGHT'))

    def draw(self, context):
        layout = self.layout
        look = context.scene.conceptflow.look

        grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=True, align=True)
        grid.scale_y = 1.3
        for key in look_presets.LOOK_ORDER:
            op = grid.operator("conceptflow.look_apply", text=look_presets.label_of(key),
                               icon=icon(look_presets.LOOK_ICONS.get(key, 'LIGHT')),
                               depress=(look.active == key), translate=False)
            op.preset = key
        grid.operator("conceptflow.look_reset", text="Reset", icon=icon('LOOP_BACK'))

        col = layout.column(align=True)
        col.prop(look, "intensity", slider=True)
        row = col.row(align=True)
        row.prop(look, "fog", text="")
        sub = row.row(align=True)
        sub.active = look.fog
        sub.prop(look, "fog_amount", slider=True)

        layout.label(text="Your Lights")
        layout.row(align=True).prop(look, "existing", expand=True)

        if look.active != 'NONE':
            layout.label(text=iface("Active: {look}").format(look=look_presets.label_of(look.active)),
                         icon=icon('RADIOBUT_ON'), translate=False)
        layout.operator("conceptflow.look_remove_data", icon=icon('TRASH'))


class CONCEPTFLOW_PT_variations(_ConceptFlowPanel, Panel):
    bl_idname = "CONCEPTFLOW_PT_variations"
    bl_label = "Auto Variations"
    bl_order = 3

    def draw_header(self, context):
        self.layout.label(text="", icon=icon('RENDER_RESULT'))

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        var = scene.conceptflow.variations

        layout.row(align=True).prop(var, "mode", expand=True)
        if var.mode == 'SINGLE':
            layout.row(align=True).prop(var, "axis", expand=True)
        else:
            grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
            grid.prop(var, "use_angle", toggle=True)
            grid.prop(var, "use_lens", toggle=True)
            grid.prop(var, "use_time", toggle=True)
            grid.prop(var, "use_atmos", toggle=True)
        layout.prop(var, "seed")

        col = layout.column()
        col.scale_y = 1.5
        text = iface("Generate {count} Variations").format(count=variations.planned_count(var))
        col.operator("conceptflow.variations_generate", text=text, icon=icon('RENDER_RESULT'),
                     translate=False)
        if scene.camera is None:
            layout.label(text="Set a scene camera first", icon=icon('ERROR'))

        if len(var.items) == 0:
            return

        layout.prop(var, "columns", slider=True)
        scale = {1: 9.0, 2: 5.0, 3: 3.4, 4: 2.5}.get(var.columns, 5.0)
        grid = layout.grid_flow(row_major=True, columns=var.columns, even_columns=True,
                                even_rows=False, align=False)
        for n, item in enumerate(var.items):
            cell = grid.column(align=True)
            icon_id = variations.thumb_icon(item.image)
            if icon_id:
                cell.template_icon(icon_value=icon_id, scale=scale)
            op = cell.operator("conceptflow.variations_apply", text="{:02d} {}".format(n + 1, item.name),
                               depress=(n == var.applied_index), translate=False)
            op.index = n

        row = layout.row(align=True)
        row.operator("conceptflow.variations_undo", icon=icon('LOOP_BACK'))
        row.operator("conceptflow.variations_base", text="Base", icon=icon('HOME'))
        row = layout.row(align=True)
        row.operator("conceptflow.variations_open_folder", icon=icon('FILE_FOLDER'))
        row.operator("conceptflow.variations_clear", text="", icon=icon('X'))


class CONCEPTFLOW_PT_read_views(_ConceptFlowPanel, Panel):
    bl_idname = "CONCEPTFLOW_PT_read_views"
    bl_label = "Read Views"
    bl_order = 4

    def draw_header(self, context):
        self.layout.label(text="", icon=icon('HIDE_OFF'))

    def draw(self, context):
        layout = self.layout
        rv = context.scene.conceptflow.read_views

        grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=True, align=True)
        grid.scale_y = 1.3
        for key, name, _desc in read_views.VIEW_ITEMS:
            on = rv.thirds if key == 'THIRDS' else rv.filter == key
            op = grid.operator("conceptflow.read_view", text=name, depress=on,
                               icon=icon(read_views.VIEW_ICONS.get(key, 'DOT')))
            op.view = key
        op = grid.operator("conceptflow.read_view", text="Off", icon=icon('X'))
        op.view = 'OFF'

        if rv.filter == 'SILHOUETTE':
            layout.prop(rv, "threshold", slider=True)
        elif rv.filter == 'BLUR':
            layout.prop(rv, "blur_amount", slider=True)
        if read_views.error:
            layout.label(text="This view could not be drawn", icon=icon('ERROR'))
            layout.label(text="Your scene was not changed")
        layout.label(text="Views only show the picture. Nothing is scored.", icon=icon('INFO'))


class CONCEPTFLOW_PT_shot_maker(_ConceptFlowPanel, Panel):
    bl_idname = "CONCEPTFLOW_PT_shot_maker"
    bl_label = "Shot Maker"
    bl_order = 5

    def draw_header(self, context):
        self.layout.label(text="", icon=icon('CAMERA_DATA'))

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        shot = scene.conceptflow.shot
        cam = scene.camera
        row = layout.row(align=True)
        row.operator("conceptflow.shot_camera", icon=icon('VIEW_CAMERA'))
        if cam is None or cam.type != 'CAMERA':
            layout.label(text="Set a scene camera first", icon=icon('ERROR'))
            return
        row.operator("view3d.view_camera", text="", icon=icon('CAMERA_DATA'))

        layout.label(text="Shots")
        grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=True, align=True)
        for key in shot_maker.SHOT_ORDER:
            op = grid.operator("conceptflow.shot_preset", text=iface(shot_maker.SHOTS[key]["label"]),
                               translate=False)
            op.kind = key

        col = layout.column(align=True)
        col.prop(shot, "height")
        grid = col.grid_flow(row_major=True, columns=3, even_columns=True, align=True)
        for key in shot_maker.HEIGHT_ORDER:
            op = grid.operator("conceptflow.shot_height", text=iface(shot_maker.HEIGHTS[key][0]),
                               translate=False)
            op.kind = key
        col.prop(shot, "keep_aim")

        col = layout.column(align=True)
        col.prop(shot, "lens", slider=True)
        row = col.row(align=True)
        for mm in shot_maker.LENS_PRESETS:
            op = row.operator("conceptflow.shot_lens", text=str(mm), translate=False)
            op.mm = mm
        col.prop(shot, "dolly")
        col = layout.column(align=True)
        col.prop(shot, "roll", slider=True)
        col.prop(cam.data, "shift_y", slider=True)

        layout.label(text="Picture Shape")
        grid = layout.grid_flow(row_major=True, columns=3, even_columns=True, align=True)
        for key in shot_maker.ASPECT_ORDER:
            op = grid.operator("conceptflow.shot_aspect", text=shot_maker.ASPECTS[key][0], translate=False)
            op.kind = key

        layout.label(text="Focus")
        row = layout.row(align=True)
        row.operator("conceptflow.shot_focus", icon=icon('RESTRICT_SELECT_OFF'))
        row.prop(cam.data.dof, "use_dof", text="")
        sub = layout.column(align=True)
        sub.active = cam.data.dof.use_dof
        sub.prop(cam.data.dof, "aperture_fstop")
        sub.prop(cam.data.dof, "focus_distance")
        layout.prop(shot, "show_horizon", toggle=True, icon=icon('NORMALIZE_FCURVES'))


classes = (
    CONCEPTFLOW_PT_graybox,
    CONCEPTFLOW_PT_draw_walls,
    CONCEPTFLOW_PT_props,
    CONCEPTFLOW_PT_asset_swap,
    CONCEPTFLOW_PT_branch_tree,
    CONCEPTFLOW_PT_look_presets,
    CONCEPTFLOW_PT_variations,
    CONCEPTFLOW_PT_read_views,
    CONCEPTFLOW_PT_shot_maker,
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
