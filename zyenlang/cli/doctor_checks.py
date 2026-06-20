"""Individual health checks for `zy doctor`. See ZEP-0007.

Each check function takes no arguments and returns a CheckResult. They are
deliberately small and pure-ish so the doctor entry point can compose them
in any order, format them in any output, or skip them via flags.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


# Status values follow ZEP-0007 §"Severity levels".
STATUS_PASS = "pass"
STATUS_WARNING = "warning"
STATUS_ERROR = "error"
STATUS_INFO = "info"


@dataclass
class CheckResult:
    id: str
    category: str           # "environment" | "installation" | "configuration" | "runtime"
    status: str
    detail: str
    fix: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        # Drop empty fix to keep JSON tight.
        if not d["fix"]:
            d.pop("fix")
        return d


# ----------------------------------------------------------------------------
# Environment
# ----------------------------------------------------------------------------

def check_python_version() -> CheckResult:
    major, minor = sys.version_info[:2]
    detail = f"{major}.{minor}.{sys.version_info.micro}"
    if (major, minor) < (3, 8):
        return CheckResult(
            "python_version", "environment", STATUS_ERROR,
            f"Python {detail} is below the minimum 3.8",
            "Install Python 3.8 or newer; ZyenLang transpiler uses 3.8+ syntax.",
        )
    return CheckResult("python_version", "environment", STATUS_PASS, detail)


def check_tkinter() -> CheckResult:
    try:
        import tkinter  # noqa: F401
        return CheckResult("tkinter", "environment", STATUS_PASS, "import tkinter OK")
    except Exception as exc:
        return CheckResult(
            "tkinter", "environment", STATUS_WARNING,
            f"import tkinter failed: {exc}",
            "Install tkinter (Windows/macOS: bundled with standard Python; Linux: `apt install python3-tk`). Only needed for the GUI IDE (ide_gui.zy).",
        )


def check_cc() -> CheckResult:
    for name in ("gcc", "clang"):
        path = shutil.which(name)
        if path:
            return CheckResult("cc", "environment", STATUS_PASS, f"{name} at {path}")
    return CheckResult(
        "cc", "environment", STATUS_ERROR,
        "neither gcc nor clang found on PATH",
        "Install gcc (Windows: MSYS2 / MinGW-w64; macOS: `xcode-select --install`; Linux: `apt install gcc`).",
    )


def check_cc_compiles() -> CheckResult:
    cc = shutil.which("gcc") or shutil.which("clang")
    if not cc:
        return CheckResult(
            "cc_compiles", "environment", STATUS_ERROR,
            "skipped: no C compiler on PATH",
            "Fix `cc` first.",
        )
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "hi.c"
        exe = Path(tmp) / ("hi.exe" if os.name == "nt" else "hi")
        src.write_text("int main(void){return 0;}\n", encoding="utf-8")
        try:
            r = subprocess.run(
                [cc, "-O0", "-o", str(exe), str(src)],
                capture_output=True, text=True, timeout=20,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            return CheckResult(
                "cc_compiles", "environment", STATUS_ERROR,
                f"C compiler launch failed: {exc}",
                "Reinstall the C compiler.",
            )
        if r.returncode != 0:
            return CheckResult(
                "cc_compiles", "environment", STATUS_ERROR,
                "C compiler refused trivial program",
                r.stderr.strip() or "(no diagnostic)",
            )
        if not exe.exists():
            return CheckResult(
                "cc_compiles", "environment", STATUS_ERROR,
                "C compiler returned 0 but produced no executable",
            )
        return CheckResult(
            "cc_compiles", "environment", STATUS_PASS,
            f"{Path(cc).name} compiled hello.c",
        )


def check_zy_on_path() -> CheckResult:
    path = shutil.which("zy")
    if path:
        return CheckResult("zy_on_path", "environment", STATUS_PASS, path)
    return CheckResult(
        "zy_on_path", "environment", STATUS_WARNING,
        "`zy` not on PATH",
        "Use `python -m zyenlang <cmd>` or run `python tools/repair_install.py` to add the shim.",
    )


# ----------------------------------------------------------------------------
# Installation
# ----------------------------------------------------------------------------

def check_package_installed() -> CheckResult:
    try:
        import zyenlang  # noqa: F401
        return CheckResult("package_installed", "installation", STATUS_PASS, "import zyenlang OK")
    except Exception as exc:
        return CheckResult(
            "package_installed", "installation", STATUS_ERROR,
            f"import zyenlang failed: {exc}",
            "Run `pip install -e .` from the repo root.",
        )


def check_package_version() -> CheckResult:
    try:
        import zyenlang
        v = getattr(zyenlang, "__version__", "(unknown)")
        return CheckResult("package_version", "installation", STATUS_INFO, v)
    except Exception as exc:
        return CheckResult("package_version", "installation", STATUS_INFO, f"(unavailable: {exc})")


def check_transpiler_importable() -> CheckResult:
    try:
        from zyenlang import transpiler  # noqa: F401
        return CheckResult("transpiler_importable", "installation", STATUS_PASS,
                           "from zyenlang import transpiler OK")
    except Exception as exc:
        return CheckResult(
            "transpiler_importable", "installation", STATUS_ERROR,
            f"transpiler import failed: {exc}",
            "Reinstall: `pip install -e .` and check for syntax errors near recent edits.",
        )


def _inner_std_dir() -> Optional[Path]:
    try:
        import zyenlang
        d = Path(zyenlang.__file__).parent / "std"
        return d if d.is_dir() else None
    except Exception:
        return None


def check_std_modules_count() -> CheckResult:
    d = _inner_std_dir()
    if d is None:
        return CheckResult(
            "std_modules_count", "installation", STATUS_ERROR,
            "zyenlang/std/ not found",
            "Reinstall the package.",
        )
    modules = sorted(p.name for p in d.glob("*.zy"))
    count = len(modules)
    if count < 40:
        return CheckResult(
            "std_modules_count", "installation", STATUS_ERROR,
            f"{count} stdlib modules in {d} (expected >= 40)",
            "stdlib is incomplete — reinstall from a clean tree.",
        )
    return CheckResult("std_modules_count", "installation", STATUS_PASS,
                       f"{count} stdlib modules")


def check_std_mirror_sync() -> CheckResult:
    inner = _inner_std_dir()
    if inner is None:
        return CheckResult(
            "std_mirror_sync", "installation", STATUS_ERROR,
            "inner zyenlang/std/ missing",
        )
    # Outer std/ lives one directory above the zyenlang package, only in
    # source-tree (editable) installs.
    outer = inner.parent.parent / "std"
    if not outer.is_dir():
        return CheckResult(
            "std_mirror_sync", "installation", STATUS_INFO,
            "source-tree std/ not present (wheel install)",
        )
    diffs = []
    inner_names = {p.name for p in inner.glob("*.zy")}
    outer_names = {p.name for p in outer.glob("*.zy")}
    for name in sorted(inner_names | outer_names):
        if name not in inner_names:
            diffs.append(f"{name} only in source std/")
            continue
        if name not in outer_names:
            diffs.append(f"{name} only in zyenlang/std/")
            continue
        if (inner / name).read_bytes() != (outer / name).read_bytes():
            diffs.append(f"{name} differs")
    if not diffs:
        return CheckResult("std_mirror_sync", "installation", STATUS_PASS,
                           "std/ and zyenlang/std/ are byte-identical")
    return CheckResult(
        "std_mirror_sync", "installation", STATUS_WARNING,
        f"{len(diffs)} file(s) drift: " + "; ".join(diffs[:3]) + ("…" if len(diffs) > 3 else ""),
        "From the repo root: copy from zyenlang/std/ to std/ (the inner copy is canonical).",
    )


# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

def _vscode_ext_dirs():
    home = Path.home()
    if os.name == "nt":
        yield home / ".vscode" / "extensions"
    else:
        yield home / ".vscode" / "extensions"
        yield home / ".vscode-server" / "extensions"


def check_vscode_ext() -> CheckResult:
    for d in _vscode_ext_dirs():
        if not d.is_dir():
            continue
        for child in d.iterdir():
            if "zyenlang" in child.name.lower():
                return CheckResult("vscode_ext", "configuration", STATUS_INFO,
                                   f"installed at {child}")
    return CheckResult(
        "vscode_ext", "configuration", STATUS_INFO,
        "not installed (optional)",
        "Run `python tools/install_vscode_extension.py` from the repo root.",
    )


def _zed_ext_dirs():
    home = Path.home()
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            yield Path(appdata) / "Zed" / "extensions"
    elif sys.platform == "darwin":
        yield home / "Library" / "Application Support" / "Zed" / "extensions"
    else:
        yield home / ".config" / "zed" / "extensions"
        yield home / ".local" / "share" / "zed" / "extensions"


def check_zed_support() -> CheckResult:
    for d in _zed_ext_dirs():
        if not d.is_dir():
            continue
        for child in d.iterdir():
            if "zyenlang" in child.name.lower():
                return CheckResult("zed_support", "configuration", STATUS_INFO,
                                   f"installed at {child}")
    return CheckResult(
        "zed_support", "configuration", STATUS_INFO,
        "not installed (optional)",
        "Run `python tools/install_zed_support.py` from the repo root.",
    )


def check_tmpdir_writable() -> CheckResult:
    target = Path("ide_session")
    try:
        target.mkdir(exist_ok=True)
        probe = target / ".doctor_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return CheckResult("tmpdir_writable", "configuration", STATUS_PASS,
                           f"{target.resolve()} writable")
    except OSError as exc:
        return CheckResult(
            "tmpdir_writable", "configuration", STATUS_WARNING,
            f"cannot write to ./{target}/: {exc}",
            "Run doctor from a directory you have write access to (the GUI IDE creates ide_session/ here).",
        )


# ----------------------------------------------------------------------------
# Runtime (smoke trio) — `--no-runtime` skips all three.
# ----------------------------------------------------------------------------

SMOKE_SOURCE = 'fn main() -> int {\n    print("doctor_ok");\n    return 0;\n}\n'


def _smoke_paths(tmp: Path):
    return tmp / "doctor_smoke.zy", tmp / "doctor_smoke.c", tmp / (
        "doctor_smoke.exe" if os.name == "nt" else "doctor_smoke"
    )


def run_smoke_trio() -> tuple[CheckResult, CheckResult, CheckResult]:
    """Run the three Runtime checks together; they share a tempdir."""
    tmp = Path(tempfile.mkdtemp(prefix="zyen_doctor_"))
    try:
        zy, c, exe = _smoke_paths(tmp)
        zy.write_text(SMOKE_SOURCE, encoding="utf-8")

        # 1. transpile
        try:
            from zyenlang import transpiler
            transpiler.build_file(zy, c)
            t = CheckResult("smoke_transpile", "runtime", STATUS_PASS,
                            "minimal .zy transpiled")
        except Exception as exc:
            t = CheckResult(
                "smoke_transpile", "runtime", STATUS_ERROR,
                f"build_file raised: {exc}",
                "Check transpiler.py for recent edits.",
            )
            err = CheckResult("smoke_compile", "runtime", STATUS_ERROR,
                              "skipped (transpile failed)")
            run = CheckResult("smoke_run", "runtime", STATUS_ERROR,
                              "skipped (transpile failed)")
            return t, err, run

        # 2. compile
        cc = shutil.which("gcc") or shutil.which("clang")
        if not cc:
            comp = CheckResult("smoke_compile", "runtime", STATUS_ERROR,
                               "no C compiler on PATH")
            run = CheckResult("smoke_run", "runtime", STATUS_ERROR,
                              "skipped (no C compiler)")
            return t, comp, run
        try:
            r = subprocess.run(
                [cc, "-O0", "-o", str(exe), str(c)],
                capture_output=True, text=True, timeout=30,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            comp = CheckResult("smoke_compile", "runtime", STATUS_ERROR,
                               f"compiler launch failed: {exc}")
            run = CheckResult("smoke_run", "runtime", STATUS_ERROR,
                              "skipped (compile failed)")
            return t, comp, run
        if r.returncode != 0 or not exe.exists():
            comp = CheckResult(
                "smoke_compile", "runtime", STATUS_ERROR,
                "C compile failed",
                r.stderr.strip()[:200] or "(no diagnostic)",
            )
            run = CheckResult("smoke_run", "runtime", STATUS_ERROR,
                              "skipped (compile failed)")
            return t, comp, run
        comp = CheckResult("smoke_compile", "runtime", STATUS_PASS,
                           "smoke .c compiled")

        # 3. run
        try:
            r2 = subprocess.run(
                [str(exe)], capture_output=True, text=True, timeout=10,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            run = CheckResult("smoke_run", "runtime", STATUS_ERROR,
                              f"launch failed: {exc}")
            return t, comp, run
        out = (r2.stdout or "").strip()
        if r2.returncode != 0:
            run = CheckResult(
                "smoke_run", "runtime", STATUS_ERROR,
                f"exit code {r2.returncode}", r2.stderr.strip()[:200],
            )
        elif "doctor_ok" not in out:
            run = CheckResult(
                "smoke_run", "runtime", STATUS_ERROR,
                f"unexpected output: {out!r}",
                "Smoke program ran but didn't print the expected token. Check runtime stdout/print handling.",
            )
        else:
            run = CheckResult("smoke_run", "runtime", STATUS_PASS,
                              "smoke program printed doctor_ok")
        return t, comp, run
    finally:
        # Best-effort cleanup; tempdir might hold output files.
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except OSError:
            pass


# ----------------------------------------------------------------------------
# Top-level orchestration
# ----------------------------------------------------------------------------

def run_all(with_runtime: bool = True) -> list[CheckResult]:
    results: list[CheckResult] = []
    # Environment first — nothing else matters if it fails.
    results.append(check_python_version())
    results.append(check_tkinter())
    results.append(check_cc())
    results.append(check_cc_compiles())
    results.append(check_zy_on_path())
    # Installation second — depends on Python but not on cc.
    results.append(check_package_installed())
    results.append(check_package_version())
    results.append(check_transpiler_importable())
    results.append(check_std_modules_count())
    results.append(check_std_mirror_sync())
    # Configuration third — pure informational mostly.
    results.append(check_vscode_ext())
    results.append(check_zed_support())
    results.append(check_tmpdir_writable())
    # Runtime last — exercises the full pipeline.
    if with_runtime:
        results.extend(run_smoke_trio())
    return results
