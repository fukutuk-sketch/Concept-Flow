# Usage (plain Python, no Blender needed):
#   python3 tools/i18n_check.py conceptflow
# Lists UI strings in the add-on sources that have no Japanese entry in translations.py.
"""Find UI strings in the ConceptFlow sources that have no Japanese entry."""
import ast, sys, pathlib, importlib.util
pkg = pathlib.Path(sys.argv[1])
src = (pkg / "translations.py").read_text(encoding="utf-8")
tree = ast.parse(src)
ja = None
for node in tree.body:
    if isinstance(node, ast.Assign) and node.targets[0].id == "JA":
        ja = {ast.literal_eval(e.elts[0]): ast.literal_eval(e.elts[1]) for e in node.value.elts}
assert ja is not None
SKIP_FILES = {"translations.py"}
found = {}
def add(s, where):
    if isinstance(s, str) and s.strip() and not s.isupper() and s not in ("CF_", ):
        found.setdefault(s, where)
for f in sorted(pkg.glob("*.py")):
    if f.name in SKIP_FILES: continue
    t = ast.parse(f.read_text(encoding="utf-8"))
    for node in ast.walk(t):
        if isinstance(node, ast.Call):
            fn = node.func
            fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if fname in ("rpt", "iface", "tip") and node.args and isinstance(node.args[0], ast.Constant):
                add(node.args[0].value, f"{f.name}:{node.lineno}")
            translate_false = any(k.arg == "translate" and isinstance(k.value, ast.Constant) and k.value.value is False for k in node.keywords)
            for k in node.keywords:
                if k.arg in ("name", "description", "text", "heading", "placeholder") and not translate_false:
                    v = k.value
                    if isinstance(v, ast.Constant): add(v.value, f"{f.name}:{node.lineno}")
                    elif isinstance(v, ast.IfExp):
                        for vv in (v.body, v.orelse):
                            if isinstance(vv, ast.Constant): add(vv.value, f"{f.name}:{node.lineno}")
                    elif isinstance(v, ast.BinOp) or isinstance(v, ast.JoinedStr):
                        pass
        if isinstance(node, ast.Assign):
            for tg in node.targets:
                if isinstance(tg, ast.Name) and tg.id in ("bl_label", "bl_description", "ORIGINAL_NAME", "AUTO_NAME"):
                    try: add(ast.literal_eval(node.value), f"{f.name}:{node.lineno}")
                    except Exception: pass
                if isinstance(tg, ast.Name) and tg.id.endswith("_ITEMS") or (isinstance(tg, ast.Name) and tg.id in ("PART_TIPS",)):
                    try: val = ast.literal_eval(node.value)
                    except Exception: continue
                    if isinstance(val, dict):
                        for v in val.values(): add(v, f"{f.name}:{node.lineno}")
                    else:
                        for item in val:
                            add(item[1], f"{f.name}:{node.lineno}"); add(item[2], f"{f.name}:{node.lineno}")
                if isinstance(tg, ast.Name) and tg.id == "PART_BUTTONS":
                    for item in ast.literal_eval(node.value): add(item[1], f"{f.name}:{node.lineno}")
missing = {s: w for s, w in found.items() if s not in ja}
# names that are user data, not UI
ignore = {"Original", "Auto-save", "CF_Graybox", "CF_Building", "CF_Roof", "CF_Wall", "CF_Part", "ConceptFlow"}
for s, w in sorted(missing.items(), key=lambda x: x[1]):
    if s in ignore: continue
    print(f"MISSING  {w:28s} {s!r}")
unused = [k for k in ja if k not in found]
print("unused entries:", len(unused))
for u in unused: print("  unused:", repr(u))
print("checked", len(found), "strings;", len(ja), "translations")
