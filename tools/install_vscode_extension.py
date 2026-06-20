from __future__ import annotations

import shutil
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    src = root / "ide" / "vscode" / "zyenlang"
    if not src.exists():
        print(f"missing extension folder: {src}")
        return 1

    dst = Path.home() / ".vscode" / "extensions" / "zyenlang-vscode-0.1.47"
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)

    print("Installed ZyenLang VS Code extension:")
    print(dst)
    print("Restart VS Code, then open a .zy file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
