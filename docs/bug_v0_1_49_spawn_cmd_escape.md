# Bug: zl_thread_spawn_cmd emits unescaped inner double quotes (P0)

## Title
Every generated C file fails to compile on Windows: `zl_thread_spawn_cmd`'s `snprintf` format string has unescaped inner double quotes.

## Minimal repro
```zy
fn main()->int{
    print("hello world");
    return 0;
}
```
Then:
```powershell
python -m zyenlang build main.zy
gcc main.c -o main.exe
```

## Expected
`main.c` compiles; running `main.exe` prints `hello world`.

## Actual
gcc errors out on the runtime helper `zl_thread_spawn_cmd`, which every transpiled file embeds:
```
main.c: In function 'zl_thread_spawn_cmd':
main.c:82:54: error: 's' undeclared (first use in this function)
   82 |     snprintf(cmd, sizeof(cmd), "start "" /B cmd /C "%s"", command);
      |                                                      ^
main.c:82:55: error: expected ')' before string constant
```

## Error message (verbatim)
```
main.c:82:54: error: 's' undeclared (first use in this function)
main.c:82:55: error: expected ')' before string constant
```

## Produced C code problem
Line 82 of every emitted file under `#ifdef _WIN32` is:
```c
snprintf(cmd, sizeof(cmd), "start "" /B cmd /C "%s"", command);
```
The C tokenizer sees adjacent string literals (`"start "` then `""` then bare ` /B cmd /C ` then `"%s"` then `""`) which is not valid syntax. The intent is one C string literal `"start \"\" /B cmd /C \"%s\""` so that the runtime command becomes `start "" /B cmd /C "<arg>"` (Windows `start` needs an empty title in quotes, and the wrapped command needs quoting in case it contains spaces).

## Probable cause
`transpiler.py` line 2112 uses a Python **single-quoted** string with `\"` to try to emit an escaped quote in C:
```python
out.append('    snprintf(cmd, sizeof(cmd), "start \"\" /B cmd /C \"%s\"", command);')
```
Inside a single-quoted Python literal, `\"` collapses to a bare `"`. To emit `\"` into the C output you need `\\"` (one backslash to escape the backslash, then the quote). All other `out.append(...)` lines in this region of `transpiler.py` use Python **double-quoted** strings where `\"` is a legitimate way to put a literal `"` into the output — those lines emit valid C. This one line is the lone Windows-only outlier that needs the double escape because the inner quotes must appear in the C source.

## Workaround
Patch line 2112 of `zyenlang/transpiler.py`:
```python
# Before
out.append('    snprintf(cmd, sizeof(cmd), "start \"\" /B cmd /C \"%s\"", command);')
# After
out.append('    snprintf(cmd, sizeof(cmd), "start \\"\\" /B cmd /C \\"%s\\"", command);')
```
After the patch, line 82 of the generated C reads:
```c
snprintf(cmd, sizeof(cmd), "start \"\" /B cmd /C \"%s\"", command);
```
which compiles, and `thread.spawn_cmd("foo")` then issues `start "" /B cmd /C "foo"` to the Windows shell as intended.

## Severity
P0 — blocks every `zy build` / `zy run` on Windows; no user program can compile without the patch.

## Notes
- POSIX branch (`#else`) is unaffected because it has no inner quotes.
- I swept the rest of `transpiler.py` for the same single-quoted-`\"` pattern; only this one line is broken.
- After patching, `python -m zyenlang run main.zy` prints `hello world`; `examples/add.zy` and `examples/cmd_basic.zy` also run.
