# ZyenLang Standard Library

v0.1.47 keeps the v0.1.45 serious stdlib baseline and adds Round 3 collection helpers.

## Core modules

- `std/string`
- `std/fs`
- `std/path`
- `std/text`
- `std/log`
- `std/test`
- `std/config`
- `std/csv`
- `std/time`
- `std/cmd`
- `std/term`
- `std/mem`

## Collections

- `std/list`
- `std/stack`
- `std/queue`
- `std/map`
- `std/set`

## Tests

```powershell
zy run tests\test_test.zy
zy run tests\path_test.zy
zy run tests\text_test.zy
zy run tests\config_test.zy
zy run tests\csv_test.zy
zy run tests\log_test.zy
zy run tests\list_test.zy
zy run tests\stack_test.zy
zy run tests\queue_test.zy
zy run tests\map_test.zy
zy run tests\set_test.zy
zy run tests\regression_claude_probe_test.zy
```

## v0.1.47 additions

- `std/units`: unit conversion helpers for deg/rad, rpm/rad/s, mm/m, g/kg, ms/s.
- `std/filter`: low-pass, high-pass, moving average, deadband, slew-rate, threshold, hysteresis.
- `std/trajectory`: linear/smoothstep interpolation and time-based ramp helpers.
- `std/robot`: Pose2D, JointLimit, joint limits, pose math, differential-drive helpers.

## v0.1.48 additions

- `std/thread`: `cpu_count`, `sleep_ms`, `yield_now`, `run_cmd`, `spawn_cmd`.
- `std/coroutine`: cooperative `Coro` state helper.
- `std/gpu`: CSV vector compute helpers: `vector_add_csv`, `vector_scale_csv`, `dot_csv`.

