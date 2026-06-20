from pathlib import Path
root = Path(__file__).resolve().parents[1]
required = ["pyproject.toml", "zyen.py", "zyenlang/transpiler.py", "tools/install_vscode_extension.py", "examples/add.zy"]
print("ZyenLang folder:", root)
missing = []
for item in required:
    p = root / item
    print(("OK      " if p.exists() else "MISSING ") + item)
    if not p.exists():
        missing.append(item)
if missing:
    raise SystemExit(1)
print("Layout OK")
