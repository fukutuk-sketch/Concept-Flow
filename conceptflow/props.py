# SPDX-License-Identifier: GPL-3.0-or-later
# ConceptFlow for Blender
"""Property groups stored on scenes and objects."""

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import PropertyGroup

PART_ITEMS = [
    ('NONE', "None", "Not a ConceptFlow part"),
    ('WALL', "Wall", "Wall section. Doors and windows can cut openings into it"),
    ('PILLAR', "Pillar", "Column or pillar"),
    ('STAIRS', "Stairs", "Straight staircase"),
    ('DOOR', "Door", "Door with a frame"),
    ('WINDOW', "Window", "Window with a frame and glass"),
    ('FLOOR', "Floor", "Floor, ceiling or roof slab"),
    ('BLOCK', "Block", "Generic block for furniture, props or large masses"),
]

PIECE_ITEMS = [
    ('FULL', "Full", "Whole part"),
    ('SOLID', "Solid", "Wall section between openings"),
    ('HEADER', "Header", "Wall section above an opening"),
    ('SILL', "Sill", "Wall section below an opening"),
]

NODE_ORIGIN_ITEMS = [
    ('MANUAL', "Manual", "Saved by you"),
    ('AUTO', "Auto-save", "Saved automatically so unsaved changes are never lost"),
    ('APPLY', "Before Apply", "Saved automatically before a variation was applied"),
]


class CONCEPTFLOW_PG_object(PropertyGroup):
    part: EnumProperty(
        name="Part Type",
        description="ConceptFlow part type. Used to swap graybox parts for final assets",
        items=PART_ITEMS,
        default='NONE',
    )
    piece: EnumProperty(name="Piece", items=PIECE_ITEMS, default='FULL', options={'HIDDEN'})
    building: StringProperty(name="Building", default="", options={'HIDDEN'})
    size: FloatVectorProperty(name="Size", size=3, default=(0.0, 0.0, 0.0), options={'HIDDEN'})
    sill: FloatProperty(name="Sill Height", default=0.0, options={'HIDDEN'})


class CONCEPTFLOW_PG_tree_node(PropertyGroup):
    # "name" is provided by PropertyGroup.
    uid: IntProperty(default=0, options={'HIDDEN'})
    parent_uid: IntProperty(default=-1, options={'HIDDEN'})
    depth: IntProperty(default=0, options={'HIDDEN'})
    created: StringProperty(name="Created", default="", options={'HIDDEN'})
    origin: EnumProperty(name="Origin", items=NODE_ORIGIN_ITEMS, default='MANUAL')
    note: StringProperty(name="Note", description="Free note for this state", default="")
    data: StringProperty(default="", options={'HIDDEN'})


class CONCEPTFLOW_PG_tree(PropertyGroup):
    nodes: CollectionProperty(type=CONCEPTFLOW_PG_tree_node)
    index: IntProperty(name="Selected State", default=0)
    current_uid: IntProperty(default=-1, options={'HIDDEN'})
    next_uid: IntProperty(default=1, options={'HIDDEN'})
    save_transforms: BoolProperty(
        name="Include Object Transforms",
        description="Also save the location, rotation and scale of every object. "
                    "Turn this off for very large scenes",
        default=True,
    )


# ---------------------------------------------------------------------------
# Look presets
# ---------------------------------------------------------------------------

LOOK_ITEMS = [
    ('NONE', "None", "No look applied"),
    ('HORROR', "HORROR", "Dark, sickly under-light, cold rim light and thick haze"),
    ('NOIR', "NOIR", "Black and white, hard key light and deep shadows"),
    ('MOONLIGHT', "MOONLIGHT", "Cold blue moon with a small warm practical light"),
    ('GOLDEN_HOUR', "GOLDEN HOUR", "Low warm sun from behind, long shadows and soft haze"),
    ('NEON', "NEON", "Magenta and cyan lights in a dark, hazy night"),
    ('CUSTOM', "Custom", "A look made by Auto Variations (time of day or atmosphere)"),
]

EXISTING_LIGHT_ITEMS = [
    ('KEEP', "Keep", "Leave your own lights as they are"),
    ('DIM', "Dim", "Turn your own lights down while the look is on"),
    ('HIDE', "Hide", "Hide your own lights while the look is on"),
]


def _look_update(kind):
    def update(self, context):
        from . import look_presets
        look_presets.on_setting_changed(self, context, kind)
    return update


class CONCEPTFLOW_PG_saved_light(PropertyGroup):
    # "name" holds the light object name.
    hide_viewport: BoolProperty(default=False, options={'HIDDEN'})
    hide_render: BoolProperty(default=False, options={'HIDDEN'})
    hidden: BoolProperty(default=False, options={'HIDDEN'})
    energy: FloatProperty(default=0.0, options={'HIDDEN'})


class CONCEPTFLOW_PG_look(PropertyGroup):
    active: EnumProperty(name="Active Look", items=LOOK_ITEMS, default='NONE')
    intensity: FloatProperty(
        name="Light Intensity", description="Brightness of the look's lights",
        min=0.0, soft_max=3.0, default=1.0, update=_look_update('INTENSITY'))
    fog: BoolProperty(
        name="Haze", description="Fill the air with haze so light shafts and depth read better",
        default=True, update=_look_update('FOG'))
    fog_amount: FloatProperty(
        name="Haze Amount", description="Thickness of the haze",
        min=0.0, soft_max=3.0, default=1.0, update=_look_update('FOG'))
    existing: EnumProperty(
        name="Your Lights", description="What happens to the lights that were already in the scene",
        items=EXISTING_LIGHT_ITEMS, default='HIDE', update=_look_update('EXISTING'))
    # Values from before the first look, used by Reset.
    original_world: StringProperty(default="", options={'HIDDEN'})
    cm_view_transform: StringProperty(default="", options={'HIDDEN'})
    cm_look: StringProperty(default="", options={'HIDDEN'})
    cm_exposure: FloatProperty(default=0.0, options={'HIDDEN'})
    cm_gamma: FloatProperty(default=1.0, options={'HIDDEN'})
    saved_lights: CollectionProperty(type=CONCEPTFLOW_PG_saved_light)
    # JSON of the look made by Auto Variations (used while active == 'CUSTOM').
    custom_spec: StringProperty(default="", options={'HIDDEN'})


# ---------------------------------------------------------------------------
# Asset swap
# ---------------------------------------------------------------------------

FIT_ITEMS = [
    ('AUTO', "Auto", "Walls repeat along their length, floors tile, blocks keep their proportions "
                     "and everything else stretches to fit"),
    ('STRETCH', "Stretch", "Scale the asset to fill the part exactly"),
    ('TILE', "Repeat", "Repeat the asset along the length (and across, for floors) and fit the rest"),
    ('UNIFORM', "Keep Proportions", "Scale evenly to fit inside the part, standing on its bottom"),
]

ROTATE_ITEMS = [
    ('0', "0°", "The asset faces -Y with its length along X (Blender front view)"),
    ('90', "90°", "Turn the asset 90° around Z"),
    ('180', "180°", "Turn the asset 180° around Z"),
    ('270', "270°", "Turn the asset 270° around Z"),
]

DISPLAY_ITEMS = [
    ('GRAYBOX', "Graybox", "Show the graybox parts"),
    ('FINAL', "Final", "Show the swapped assets instead of the graybox parts"),
]


def _swap_display_update(self, context):
    from . import asset_swap
    asset_swap.on_display_changed(self, context)


class CONCEPTFLOW_PG_swap_slot(PropertyGroup):
    source: PointerProperty(
        type=bpy.types.Collection, name="Assets",
        description="Collection holding the assets for this part type. Each object (or child "
                    "collection) in it is one candidate")
    fit: EnumProperty(name="Fit", items=FIT_ITEMS, default='AUTO')
    rotate: EnumProperty(name="Rotate", items=ROTATE_ITEMS, default='0',
                         description="Turn the assets if they were modelled facing another way")


class CONCEPTFLOW_PG_swap(PropertyGroup):
    wall: PointerProperty(type=CONCEPTFLOW_PG_swap_slot)
    pillar: PointerProperty(type=CONCEPTFLOW_PG_swap_slot)
    stairs: PointerProperty(type=CONCEPTFLOW_PG_swap_slot)
    door: PointerProperty(type=CONCEPTFLOW_PG_swap_slot)
    window: PointerProperty(type=CONCEPTFLOW_PG_swap_slot)
    floor: PointerProperty(type=CONCEPTFLOW_PG_swap_slot)
    block: PointerProperty(type=CONCEPTFLOW_PG_swap_slot)
    seed: IntProperty(name="Seed", description="Changes which candidate each part gets",
                      min=0, default=0)
    display: EnumProperty(name="Display", items=DISPLAY_ITEMS, default='GRAYBOX',
                          update=_swap_display_update)
    show_options: BoolProperty(name="Fit Options", description="Show fit and rotation per part type",
                               default=False)


# ---------------------------------------------------------------------------
# Shot maker (the sliders show and change the scene camera)
# ---------------------------------------------------------------------------

def _shot(name):
    from . import shot_maker
    return getattr(shot_maker, name)


class CONCEPTFLOW_PG_shot(PropertyGroup):
    height: FloatProperty(
        name="Eye Height", description="Height of the camera above the ground (z). It keeps looking at the subject",
        min=-10.0, soft_min=0.0, soft_max=40.0, unit='LENGTH',
        get=lambda self: _shot("get_height")(self), set=lambda self, v: _shot("set_height_prop")(self, v))
    lens: FloatProperty(
        name="Lens", description="Focal length in millimetres", min=8.0, max=400.0, soft_max=200.0,
        get=lambda self: _shot("get_lens")(self), set=lambda self, v: _shot("set_lens_prop")(self, v))
    roll: FloatProperty(
        name="Roll", description="Tilt of the camera around its viewing direction, in degrees",
        min=-90.0, max=90.0,
        get=lambda self: _shot("get_roll")(self), set=lambda self, v: _shot("set_roll_prop")(self, v))
    dolly: BoolProperty(
        name="Dolly", description="When the lens or shot changes, move the camera so the subject keeps its size",
        default=True)
    keep_aim: BoolProperty(
        name="Keep Aim", description="When the height changes, keep looking at the same subject", default=True)
    show_horizon: BoolProperty(
        name="Horizon Guide", description="Draw the horizon line and the camera height over the camera view",
        default=False, options={'SKIP_SAVE'},
        update=lambda self, context: _shot("on_horizon_changed")(self, context))


# ---------------------------------------------------------------------------
# Photo match
# ---------------------------------------------------------------------------

class CONCEPTFLOW_PG_photo(PropertyGroup):
    camera_height: FloatProperty(
        name="Camera Height", description="How high above the ground the photo was taken. It sets the scale: "
                                          "the ground is z = 0", min=0.05, soft_max=50.0, default=1.6, unit='LENGTH')
    flip: BoolProperty(
        name="Flip Directions", description="Turn the world axes by 180 degrees around the vertical axis. "
                                            "Use it when X and Y point the wrong way", default=False)
    opacity: FloatProperty(
        name="Photo Opacity", description="How strongly the photo shows behind the 3D view",
        min=0.0, max=1.0, default=0.6,
        get=lambda self: _photo("get_opacity")(self), set=lambda self, v: _photo("set_opacity")(self, v))


def _photo(name):
    from . import photo_match
    return getattr(photo_match, name)


# ---------------------------------------------------------------------------
# Draw walls settings
# ---------------------------------------------------------------------------

class CONCEPTFLOW_PG_draw(PropertyGroup):
    height: FloatProperty(name="Wall Height", min=0.3, soft_max=20.0, default=3.0, unit='LENGTH')
    thickness: FloatProperty(name="Wall Thickness", min=0.03, soft_max=2.0, default=0.2, unit='LENGTH')
    snap: FloatProperty(name="Grid Snap", description="Snap points to this grid. 0 = no snapping. "
                                                       "Hold Ctrl while drawing to switch snapping off",
                        min=0.0, soft_max=5.0, default=0.5, unit='LENGTH')
    floor_slab: BoolProperty(name="Floor When Closed",
                             description="Add a floor slab when the walls are closed into a room", default=True)
    slab: FloatProperty(name="Floor Thickness", min=0.02, soft_max=1.0, default=0.15, unit='LENGTH')


# ---------------------------------------------------------------------------
# Auto variations
# ---------------------------------------------------------------------------

VARIATION_MODE_ITEMS = [
    ('SINGLE', "Single", "Change one thing at a time: 8 variations"),
    ('COMBO', "Combine", "Mix two or more things: 16 variations"),
]

VARIATION_AXIS_ITEMS = [
    ('ANGLE', "Angle", "Move the camera around the subject"),
    ('LENS', "Lens", "Change the focal length and keep the subject size"),
    ('TIME', "Time of Day", "Change sun, sky and light from dawn to night"),
    ('ATMOS', "Atmosphere", "Change the haze: mist, dust, smoke, fog"),
]


class CONCEPTFLOW_PG_variation_item(PropertyGroup):
    # "name" holds the label.
    spec: StringProperty(default="", options={'HIDDEN'})
    image: StringProperty(default="", subtype='FILE_PATH', options={'HIDDEN'})


class CONCEPTFLOW_PG_variations(PropertyGroup):
    mode: EnumProperty(name="Mode", items=VARIATION_MODE_ITEMS, default='SINGLE')
    axis: EnumProperty(name="Change", items=VARIATION_AXIS_ITEMS, default='ANGLE',
                       description="What to change in Single mode")
    use_angle: BoolProperty(name="Angle", description=VARIATION_AXIS_ITEMS[0][2], default=True)
    use_lens: BoolProperty(name="Lens", description=VARIATION_AXIS_ITEMS[1][2], default=False)
    use_time: BoolProperty(name="Time of Day", description=VARIATION_AXIS_ITEMS[2][2], default=True)
    use_atmos: BoolProperty(name="Atmosphere", description=VARIATION_AXIS_ITEMS[3][2], default=False)
    seed: IntProperty(name="Seed", description="Changes the small random differences between variations",
                      min=0, default=0)
    items: CollectionProperty(type=CONCEPTFLOW_PG_variation_item)
    folder: StringProperty(default="", subtype='DIR_PATH', options={'HIDDEN'})
    sheet: StringProperty(default="", subtype='FILE_PATH', options={'HIDDEN'})
    base_state: StringProperty(default="", options={'HIDDEN'})
    base_view: StringProperty(default="", options={'HIDDEN'})
    applied_index: IntProperty(default=-1, options={'HIDDEN'})
    before_uid: IntProperty(default=-1, options={'HIDDEN'})
    columns: IntProperty(name="Columns", description="How many thumbnails per row",
                         min=1, max=4, default=2)


# ---------------------------------------------------------------------------
# Read views (viewport overlays; not saved in the .blend)
# ---------------------------------------------------------------------------

READ_FILTER_ITEMS = [
    ('OFF', "Off", "No filter"),
    ('GRAYSCALE', "Grayscale", "Values only"),
    ('SILHOUETTE', "Silhouette", "Black and white shapes"),
    ('BLUR', "Blur", "Blurred masses"),
]


def _read_update(self, context):
    from . import read_views
    read_views.on_changed()


class CONCEPTFLOW_PG_read_views(PropertyGroup):
    filter: EnumProperty(name="Filter", items=READ_FILTER_ITEMS, default='OFF',
                         options={'SKIP_SAVE'}, update=_read_update)
    thirds: BoolProperty(name="Thirds", default=False, options={'SKIP_SAVE'}, update=_read_update)
    threshold: FloatProperty(
        name="Threshold", description="Brightness where the silhouette turns from black to white",
        min=0.0, max=1.0, default=0.35, options={'SKIP_SAVE'}, update=_read_update)
    blur_amount: FloatProperty(
        name="Blur Amount", description="How strongly the picture is blurred",
        min=0.5, soft_max=12.0, default=4.0, options={'SKIP_SAVE'}, update=_read_update)


class CONCEPTFLOW_PG_scene(PropertyGroup):
    tree: PointerProperty(type=CONCEPTFLOW_PG_tree)
    look: PointerProperty(type=CONCEPTFLOW_PG_look)
    swap: PointerProperty(type=CONCEPTFLOW_PG_swap)
    variations: PointerProperty(type=CONCEPTFLOW_PG_variations)
    read_views: PointerProperty(type=CONCEPTFLOW_PG_read_views)
    draw: PointerProperty(type=CONCEPTFLOW_PG_draw)
    shot: PointerProperty(type=CONCEPTFLOW_PG_shot)
    photo: PointerProperty(type=CONCEPTFLOW_PG_photo)


classes = (
    CONCEPTFLOW_PG_object,
    CONCEPTFLOW_PG_tree_node,
    CONCEPTFLOW_PG_tree,
    CONCEPTFLOW_PG_saved_light,
    CONCEPTFLOW_PG_look,
    CONCEPTFLOW_PG_swap_slot,
    CONCEPTFLOW_PG_swap,
    CONCEPTFLOW_PG_variation_item,
    CONCEPTFLOW_PG_variations,
    CONCEPTFLOW_PG_read_views,
    CONCEPTFLOW_PG_draw,
    CONCEPTFLOW_PG_shot,
    CONCEPTFLOW_PG_photo,
    CONCEPTFLOW_PG_scene,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.conceptflow = PointerProperty(type=CONCEPTFLOW_PG_scene)
    bpy.types.Object.conceptflow = PointerProperty(type=CONCEPTFLOW_PG_object)


def unregister():
    for attr_owner in (bpy.types.Object, bpy.types.Scene):
        if hasattr(attr_owner, "conceptflow"):
            del attr_owner.conceptflow
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
