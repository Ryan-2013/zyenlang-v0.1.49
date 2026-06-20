from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def has_git() -> bool:
    return shutil.which("git") is not None


def git_output(cmd: list[str], cwd: Path) -> str:
    return run(cmd, cwd=cwd).stdout.strip()


def ensure_git_repo(grammar_dir: Path) -> str:
    if not has_git():
        print("WARNING: git was not found. Zed may not load the local Tree-sitter grammar until this folder is a git repo.")
        return "HEAD"

    if not (grammar_dir / ".git").exists():
        run(["git", "init"], cwd=grammar_dir)

    run(["git", "add", "-A"], cwd=grammar_dir)

    status = git_output(["git", "status", "--porcelain"], cwd=grammar_dir)
    if status:
        commit = [
            "git",
            "-c", "user.name=ZyenLang",
            "-c", "user.email=zyenlang@example.local",
            "commit",
            "-m", "Prepare local tree-sitter-zyenlang grammar",
        ]
        result = run(commit, cwd=grammar_dir, check=False)
        if result.returncode != 0:
            # If commit failed because there is nothing to commit or local policy blocks it,
            # continue and try to read an existing HEAD.
            msg = (result.stderr or result.stdout).strip()
            if msg:
                print("WARNING: git commit did not complete:")
                print(msg)

    rev = git_output(["git", "rev-parse", "HEAD"], cwd=grammar_dir)
    return rev or "HEAD"


def write_zed_tasks(root: Path) -> None:
    zed_dir = root / ".zed"
    zed_dir.mkdir(exist_ok=True)
    tasks = [
        {
            "label": "Zyen: check current file",
            "command": "python",
            "args": ["zyen.py", "check", "$ZED_FILE"],
            "cwd": "$ZED_WORKTREE_ROOT",
            "save": "current",
            "reveal": "always",
            "hide": "never",
            "shell": "system",
        },
        {
            "label": "Zyen: run current file",
            "command": "python",
            "args": ["zyen.py", "run", "$ZED_FILE"],
            "cwd": "$ZED_WORKTREE_ROOT",
            "save": "current",
            "reveal": "always",
            "hide": "never",
            "shell": "system",
        },
        {
            "label": "Zyen: build C from current file",
            "command": "python",
            "args": ["zyen.py", "build", "$ZED_FILE", "-o", "build/$ZED_STEM.c"],
            "cwd": "$ZED_WORKTREE_ROOT",
            "save": "current",
            "reveal": "always",
            "hide": "never",
            "shell": "system",
        },
        {
            "label": "Zyen: build exe from current file",
            "command": "python",
            "args": ["zyen.py", "build", "$ZED_FILE", "-o", "build/$ZED_STEM.exe"],
            "cwd": "$ZED_WORKTREE_ROOT",
            "save": "current",
            "reveal": "always",
            "hide": "never",
            "shell": "system",
        },
    ]
    (zed_dir / "tasks.json").write_text(json.dumps(tasks, indent=2), encoding="utf-8")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    extension_dir = root / "ide" / "zed" / "zyenlang"
    grammar_dir = root / "ide" / "zed" / "tree-sitter-zyenlang"
    template = extension_dir / "extension.toml.template"
    output = extension_dir / "extension.toml"

    if not template.exists():
        print(f"ERROR: Missing {template}")
        return 1
    if not grammar_dir.exists():
        print(f"ERROR: Missing {grammar_dir}")
        return 1

    write_zed_tasks(root)
    rev = ensure_git_repo(grammar_dir)

    repo_uri = grammar_dir.resolve().as_uri()
    text = template.read_text(encoding="utf-8")
    text = text.replace("__TREE_SITTER_ZYENLANG_REPOSITORY__", repo_uri)
    text = text.replace("__TREE_SITTER_ZYENLANG_REV__", rev)
    output.write_text(text, encoding="utf-8")

    print("ZyenLang Zed support prepared.")
    print(f"Project tasks: {root / '.zed' / 'tasks.json'}")
    print(f"Dev extension: {extension_dir}")
    print(f"Grammar repo:   {repo_uri}")
    print(f"Grammar rev:    {rev}")
    print("")
    print("In Zed:")
    print("  1. Open Command Palette")
    print("  2. Run: zed: install dev extension")
    print(f"  3. Select: {extension_dir}")
    print("  4. Open a .zy file")
    print("")
    print("For running code:")
    print("  Ctrl+Shift+P -> task: spawn -> Zyen: run current file")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
