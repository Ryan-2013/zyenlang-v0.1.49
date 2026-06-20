# ZyenLang Stdlib Round 2 Report

## Version

Target: v0.1.45

## Added modules

- `std/path`
- `std/text`
- `std/log`
- `std/test`
- `std/config`
- `std/csv`

All modules are mirrored under both `std/` and `zyenlang/std/`.

## Compiler/runtime fixes included

- B1 fixed: `zl_str_join` now returns heap-allocated strings instead of rotating static buffers.
- B2 fixed: nested transformed List calls such as `test.eq_str(..., text.join_lines(xs), ...)` no longer mis-wrap `&xs` as a ptr error.
- B3 fixed as diagnostic: top-level `let` / `const` now errors clearly instead of being silently dropped.
- `for (...; set i += 1)` now emits valid C.
- Bare compound assignment detection no longer scans string literals or argument text too broadly.
- Std module struct methods are no longer namespace-renamed; `TestStats.eq_int(...)` works.

## Tests

Manual test set:

```powershell
zy run tests\test_test.zy
zy run tests\path_test.zy
zy run tests\text_test.zy
zy run tests\config_test.zy
zy run tests\csv_test.zy
zy run tests\log_test.zy
zy run tests\regression_claude_probe_test.zy
zy run examples\for_set_step.zy
python tests\smoke_test.py
```

`python tests\smoke_test.py` completed with `ALL PASS`.
