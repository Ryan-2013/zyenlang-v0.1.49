from __future__ import annotations
import subprocess, sys, os
from pathlib import Path

root = Path(__file__).resolve().parents[1]
print(f"ZyenLang root: {root}")
needed = [root / "pyproject.toml", root / "zyen.py", root / "zyenlang" / "transpiler.py"]
missing = [str(p) for p in needed if not p.exists()]
if missing:
    print("Missing required files:")
    for m in missing:
        print("  -", m)
    sys.exit(1)

cmds = [
    [sys.executable, "-m", "pip", "uninstall", "zyenlang", "-y"],
    [sys.executable, "-m", "pip", "install", "-e", str(root)],
]
for cmd in cmds:
    print("\n> " + " ".join(cmd))
    subprocess.run(cmd, check=False)

print("\nTesting zy command...")
subprocess.run(["zy", "--help"], check=False)
print("\nDone. Try: zy run examples/add.zy")
