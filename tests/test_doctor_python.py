"""Python-level tests for `zy doctor` (ZEP-0007).

Smoke-level: spawn doctor as a subprocess and assert the JSON output has
the expected shape and that the exit code matches the worst severity.
We don't assert specific pass/fail per check because the host environment
varies (CI may not have tkinter, gcc, etc.).

To run:
    python tests/test_doctor_python.py
"""

from __future__ import annotations

import json
import subprocess
import sys


REQUIRED_CHECK_IDS = {
    # Environment
    "python_version", "tkinter", "cc", "cc_compiles", "zy_on_path",
    # Installation
    "package_installed", "package_version", "transpiler_importable",
    "std_modules_count", "std_mirror_sync",
    # Configuration
    "vscode_ext", "zed_support", "tmpdir_writable",
}

REQUIRED_RUNTIME_IDS = {"smoke_transpile", "smoke_compile", "smoke_run"}

VALID_CATEGORIES = {"environment", "installation", "configuration", "runtime"}
VALID_STATUSES = {"pass", "warning", "error", "info"}


def _run_doctor_json(*extra: str) -> dict:
    r = subprocess.run(
        [sys.executable, "-m", "zyenlang", "doctor", "--json", *extra],
        capture_output=True, text=True, timeout=60,
    )
    # Doctor's exit code may be 0/1/2; that's tested separately below.
    return json.loads(r.stdout), r.returncode


def test_json_envelope() -> None:
    payload, _rc = _run_doctor_json("--no-runtime")
    assert payload["doctor_version"] == 1, "doctor_version must be 1"
    assert isinstance(payload["zyenlang_version"], str)
    assert "checks" in payload and isinstance(payload["checks"], list)
    assert "summary" in payload
    for key in ("pass", "warning", "error"):
        assert key in payload["summary"], f"summary missing key {key}"
        assert isinstance(payload["summary"][key], int)


def test_all_required_checks_present_without_runtime() -> None:
    payload, _rc = _run_doctor_json("--no-runtime")
    got = {c["id"] for c in payload["checks"]}
    missing = REQUIRED_CHECK_IDS - got
    assert not missing, f"checks missing without --no-runtime allowed: {missing}"
    # Runtime checks must NOT appear when skipped.
    assert not (REQUIRED_RUNTIME_IDS & got), \
        "runtime checks present despite --no-runtime"


def test_runtime_checks_present_by_default() -> None:
    payload, _rc = _run_doctor_json()
    got = {c["id"] for c in payload["checks"]}
    missing = (REQUIRED_CHECK_IDS | REQUIRED_RUNTIME_IDS) - got
    assert not missing, f"missing checks in default run: {missing}"


def test_each_check_well_formed() -> None:
    payload, _rc = _run_doctor_json()
    for c in payload["checks"]:
        assert c["category"] in VALID_CATEGORIES, c
        assert c["status"] in VALID_STATUSES, c
        assert isinstance(c["id"], str) and c["id"]
        assert isinstance(c["detail"], str)
        # fix is optional; if present must be str
        if "fix" in c:
            assert isinstance(c["fix"], str)


def test_exit_code_matches_summary() -> None:
    payload, rc = _run_doctor_json("--no-runtime")
    s = payload["summary"]
    if s["error"] > 0:
        expected = 2
    elif s["warning"] > 0:
        expected = 1
    else:
        expected = 0
    assert rc == expected, f"exit {rc} does not match summary {s} (expected {expected})"


def main() -> int:
    tests = [
        test_json_envelope,
        test_all_required_checks_present_without_runtime,
        test_runtime_checks_present_by_default,
        test_each_check_well_formed,
        test_exit_code_matches_summary,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
        else:
            print(f"OK    {t.__name__}")
            passed += 1
    print(f"---- zy doctor python tests ----")
    print(f"pass={passed} fail={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
