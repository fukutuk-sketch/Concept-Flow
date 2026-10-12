# SPDX-License-Identifier: GPL-3.0-or-later
# ConceptFlow for Blender
"""ConceptFlow - Blender for thinking about pictures, not just making 3D.

3D Viewport > Sidebar (N) > ConceptFlow
"""

bl_info = {
    "name": "ConceptFlow",
    "author": "ConceptFlow",
    "version": (0, 3, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar > ConceptFlow",
    "description": "Graybox, branch tree, looks and variations for concept art",
    "category": "3D View",
}

import traceback

from . import translations, props, state, graybox, graybox_tools, branch_tree, look_presets, asset_swap, variations, read_views, ui

_MODULES = (translations, props, state, graybox, graybox_tools, branch_tree, look_presets, asset_swap, variations, read_views, ui)


def register():
    done = []
    try:
        for module in _MODULES:
            module.register()
            done.append(module)
    except Exception:
        traceback.print_exc()
        for module in reversed(done):
            try:
                module.unregister()
            except Exception:
                traceback.print_exc()
        raise


def unregister():
    for module in reversed(_MODULES):
        try:
            module.unregister()
        except Exception:
            traceback.print_exc()
