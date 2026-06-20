#!/usr/bin/env python3
"""Install ZyenLang as an editable Python module and CLI.

Run from the project root:
    python tools/install_zy_module.py

After install:
    zy check examples/add.zy
    zy run examples/add.zy
    zy build examples/add.zy -o main.c
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        print(f"ERROR: pyproject.toml not found at {pyproject}", file=sys.stderr)
        print("Please run this script from inside the ZyenLang project folder.", file=sys.stderr)
        return 1

    cmd = [sys.executable, "-m", "pip", "install", "-e", str(root)]
    print("Installing ZyenLang editable module:")
    print(" ".join(cmd))
    subprocess.check_call(cmd)

    print("\nInstalled. Try:")
    print("  zy --help")
    print("  zy run examples/add.zy")
    print("  zy build examples/add.zy -o main.c")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
