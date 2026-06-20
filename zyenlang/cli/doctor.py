"""`zy doctor` entry point and output formatters. See ZEP-0007."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List

from . import doctor_checks as dc


SYMBOLS = {
    dc.STATUS_PASS: "+",      # ✓
    dc.STATUS_WARNING: "!",
    dc.STATUS_ERROR: "x",     # ✗
    dc.STATUS_INFO: "-",
}

CATEGORY_TITLES = [
    ("environment", "Environment"),
    ("installation", "Installation"),
    ("configuration", "Configuration"),
    ("runtime", "Runtime"),
]


def _exit_code(results: List[dc.CheckResult]) -> int:
    if any(r.status == dc.STATUS_ERROR for r in results):
        return 2
    if any(r.status == dc.STATUS_WARNING for r in results):
        return 1
    return 0


def _summary_counts(results: List[dc.CheckResult]) -> tuple[int, int, int]:
    p = sum(1 for r in results if r.status == dc.STATUS_PASS)
    w = sum(1 for r in results if r.status == dc.STATUS_WARNING)
    e = sum(1 for r in results if r.status == dc.STATUS_ERROR)
    return p, w, e


def _print_human(results: List[dc.CheckResult], verbose: bool) -> None:
    try:
        import zyenlang
        v = getattr(zyenlang, "__version__", "?")
    except Exception:
        v = "?"
    print(f"ZyenLang doctor - v{v}")
    print()
    for cat_key, cat_title in CATEGORY_TITLES:
        cat_results = [r for r in results if r.category == cat_key]
        if not cat_results:
            continue
        print(cat_title)
        for r in cat_results:
            sym = SYMBOLS.get(r.status, "?")
            line = f"  {sym}  {r.id}"
            if verbose or r.status != dc.STATUS_PASS:
                line += f"  {r.detail}"
            print(line)
            if r.fix and r.status != dc.STATUS_PASS:
                # Indent fix on its own line so it's easy to copy.
                print(f"     Fix: {r.fix}")
        print()
    p, w, e = _summary_counts(results)
    print(f"Summary: {p} passed, {w} warnings, {e} errors")


def _print_json(results: List[dc.CheckResult]) -> None:
    try:
        import zyenlang
        v = getattr(zyenlang, "__version__", "")
    except Exception:
        v = ""
    p, w, e = _summary_counts(results)
    payload = {
        "doctor_version": 1,
        "zyenlang_version": v,
        "checks": [r.to_dict() for r in results],
        "summary": {"pass": p, "warning": w, "error": e},
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def run_doctor(verbose: bool = False, json_out: bool = False,
               no_runtime: bool = False) -> int:
    results = dc.run_all(with_runtime=not no_runtime)
    if json_out:
        _print_json(results)
    else:
        _print_human(results, verbose=verbose)
    return _exit_code(results)


def add_subparser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("doctor", help="diagnose installation / environment issues (ZEP-0007)")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="show details for every check, not just failures")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON instead of human output")
    p.add_argument("--no-runtime", action="store_true",
                   help="skip the smoke transpile/compile/run trio (~3s faster)")


def handle(args: argparse.Namespace) -> int:
    return run_doctor(
        verbose=args.verbose,
        json_out=args.json,
        no_runtime=args.no_runtime,
    )


def main(argv=None) -> int:
    """Allow `python -m zyenlang.cli.doctor` as a debugging shortcut."""
    parser = argparse.ArgumentParser(prog="zy doctor")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-runtime", action="store_true")
    args = parser.parse_args(argv)
    return run_doctor(args.verbose, args.json, args.no_runtime)


if __name__ == "__main__":
    sys.exit(main())
