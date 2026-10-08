# Run inside Blender (Text Editor > Run Script, or exec the file), then call from the console:
#
#   import sys, bpy
#   from types import SimpleNamespace
#   exec(open(r"<path>/tools/mock_ui_check.py", encoding="utf-8").read())
#   sc = bpy.data.scenes["CF_Test"]
#   mk = lambda mode, act, sel: SimpleNamespace(scene=sc, view_layer=sc.view_layers[0], mode=mode,
#                                                active_object=act, selected_objects=sel)
#   probs, calls, panels = run(sc, [mk('OBJECT', None, []), mk('EDIT_MESH', None, [])])
#   print(len(set(probs)), "problems"); [print(" -", p) for p in sorted(set(probs))]
#
# Also run it with a look active and with a scene that has an empty Branch Tree.

# Runs inside Blender: draws every ConceptFlow panel / UI list / operator draw() with a
# mock layout and reports unknown properties, operators, operator properties,
# layout parameters and icons.
import bpy
from types import SimpleNamespace

ICONS = set(bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items.keys())
FUNCS = bpy.types.UILayout.bl_rna.functions
LAYOUT_PROPS = set(bpy.types.UILayout.bl_rna.properties.keys())
problems = []
calls = {"prop": 0, "operator": 0, "label": 0, "menu_enum": 0}


def _chk(fname, kw):
    func = FUNCS.get(fname)
    if func is not None:
        params = set(func.parameters.keys())
        for k in kw:
            if k not in params:
                problems.append(f"{fname}(): unknown parameter '{k}'")
    ic = kw.get("icon")
    if ic and ic not in ICONS:
        problems.append(f"{fname}(): unknown icon '{ic}'")


class OpProps:
    def __init__(self, idname, rna):
        object.__setattr__(self, "_idname", idname)
        object.__setattr__(self, "_rna", rna)

    def __setattr__(self, key, value):
        if key not in self._rna.properties.keys():
            problems.append(f"operator {self._idname}: no property '{key}'")


class Mock:
    def __setattr__(self, key, value):
        if key not in LAYOUT_PROPS:
            problems.append(f"layout attribute '{key}' does not exist")

    def _sub(self, fname, kw):
        _chk(fname, kw)
        return Mock()

    def row(self, **kw): return self._sub("row", kw)
    def column(self, **kw): return self._sub("column", kw)
    def box(self, **kw): return self._sub("box", kw)
    def split(self, **kw): return self._sub("split", kw)
    def grid_flow(self, **kw): return self._sub("grid_flow", kw)
    def column_flow(self, **kw): return self._sub("column_flow", kw)

    def label(self, **kw):
        calls["label"] += 1
        _chk("label", kw)

    def separator(self, **kw): _chk("separator", kw)
    def separator_spacer(self): pass

    def prop(self, data, propname, **kw):
        calls["prop"] += 1
        _chk("prop", kw)
        rna = getattr(data, "bl_rna", None)
        if rna is None or propname not in rna.properties.keys():
            problems.append(f"prop(): '{propname}' not found on {type(data).__name__}")

    def prop_enum(self, data, propname, value, **kw):
        self.prop(data, propname)
        prop = data.bl_rna.properties.get(propname)
        if prop is not None and value not in prop.enum_items.keys():
            problems.append(f"prop_enum(): '{value}' not in {propname}")

    def operator(self, idname, **kw):
        calls["operator"] += 1
        _chk("operator", kw)
        try:
            mod, name = idname.split(".")
            rna = getattr(getattr(bpy.ops, mod), name).get_rna_type()
        except Exception:
            problems.append(f"operator '{idname}' does not exist")
            return SimpleNamespace()
        return OpProps(idname, rna)

    def operator_menu_enum(self, idname, prop, **kw):
        calls["menu_enum"] += 1
        _chk("operator_menu_enum", kw)
        try:
            mod, name = idname.split(".")
            rna = getattr(getattr(bpy.ops, mod), name).get_rna_type()
            if prop not in rna.properties.keys() or rna.properties[prop].type != 'ENUM':
                problems.append(f"operator_menu_enum: '{prop}' is not an enum of {idname}")
        except Exception:
            problems.append(f"operator '{idname}' does not exist")

    def template_list(self, listtype, list_id, data, propname, active_data, active_propname, **kw):
        _chk("template_list", kw)
        if not hasattr(bpy.types, listtype):
            problems.append(f"UIList '{listtype}' not registered")
        if propname not in data.bl_rna.properties.keys():
            problems.append(f"template_list: '{propname}' missing")
        if active_propname not in active_data.bl_rna.properties.keys():
            problems.append(f"template_list: '{active_propname}' missing")

    def menu(self, menu, **kw):
        _chk("menu", kw)
        if not hasattr(bpy.types, menu):
            problems.append(f"menu '{menu}' not registered")

    def popover(self, panel, **kw):
        _chk("popover", kw)
        if not hasattr(bpy.types, panel):
            problems.append(f"popover panel '{panel}' not registered")

    def template_icon_view(self, data, propname, **kw):
        _chk("template_icon_view", kw)
        self.prop(data, propname)

    def template_icon(self, **kw): _chk("template_icon", kw)

    def __getattr__(self, name):
        problems.append(f"layout method '{name}' not handled by mock")
        return lambda *a, **k: Mock()


class FakeOp:
    def __init__(self, rna):
        object.__setattr__(self, "bl_rna", rna)
        object.__setattr__(self, "layout", Mock())
        for p in rna.properties:
            if p.identifier == "rna_type":
                continue
            default = getattr(p, "default", None)
            if getattr(p, "is_array", False) or getattr(p, "array_length", 0):
                default = tuple(getattr(p, "default_array", ()))
            object.__setattr__(self, p.identifier, default)


def run(scene, contexts):
    """contexts: list of SimpleNamespace contexts to draw with."""
    panels = [c for c in bpy.types.Panel.__subclasses__() if c.__name__.startswith("CONCEPTFLOW_PT")]
    lists = [c for c in bpy.types.UIList.__subclasses__() if c.__name__.startswith("CONCEPTFLOW_UL")]
    for i, ctx in enumerate(contexts):
        for cls in panels:
            try:
                if hasattr(cls, "poll") and not cls.poll(ctx):
                    continue
                fake = SimpleNamespace(layout=Mock(), bl_idname=cls.bl_idname)
                if hasattr(cls, "draw_header"):
                    cls.draw_header(fake, ctx)
                cls.draw(fake, ctx)
            except Exception as exc:
                import traceback
                problems.append(f"[ctx{i}] {cls.__name__}.draw raised {exc!r}\n{traceback.format_exc()}")
    tree = scene.conceptflow.tree
    for cls in lists:
        fake = SimpleNamespace(layout_type="DEFAULT", filter_name="", bitflag_filter_item=1 << 30)
        try:
            for idx, item in enumerate(tree.nodes):
                cls.draw_item(fake, contexts[0], Mock(), tree, item, 0, tree, "index", idx)
            flags, order = cls.filter_items(fake, contexts[0], tree, "nodes")
            if len(flags) != len(tree.nodes) or sorted(order) != list(range(len(tree.nodes))):
                problems.append(f"{cls.__name__}.filter_items returned bad lists")
        except Exception as exc:
            problems.append(f"{cls.__name__} raised {exc!r}")
    for op_cls in [c for c in bpy.types.Operator.__subclasses__() if c.__name__.startswith("CONCEPTFLOW_OT")]:
        if "draw" not in op_cls.__dict__:
            continue
        mod, name = op_cls.bl_idname.split(".")
        rna = getattr(getattr(bpy.ops, mod), name).get_rna_type()
        variants = [None]
        part_prop = rna.properties.get("part")
        if part_prop is not None:
            variants = [e.identifier for e in part_prop.enum_items]
        for v in variants:
            fake = FakeOp(rna)
            if v is not None:
                object.__setattr__(fake, "part", v)
            try:
                op_cls.draw(fake, contexts[0])
            except Exception as exc:
                problems.append(f"{op_cls.__name__}.draw({v}) raised {exc!r}")
    # descriptions callbacks
    for op_cls in [c for c in bpy.types.Operator.__subclasses__() if c.__name__.startswith("CONCEPTFLOW_OT")]:
        if "description" in op_cls.__dict__:
            try:
                op_cls.description(contexts[0], None)
            except Exception as exc:
                problems.append(f"{op_cls.__name__}.description raised {exc!r}")
    return problems, calls, [c.__name__ for c in panels]
