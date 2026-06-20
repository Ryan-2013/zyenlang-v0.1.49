# ZyenLang v0.1.49

**English** | [繁體中文](README.zh-TW.md)

ZyenLang is an experimental C-like scripting language with Python-like
tooling. `.zy` source is transpiled to C, then compiled with gcc/clang.

Aimed at robotics, computer vision, control systems, embedded-style
experiments, and small engine prototyping.

This package is the **Windows flat direct** distribution: the top level
contains `pyproject.toml`, `zyen.py`, `zyenlang/`, `std/`, `examples/`,
`tests/`, `tools/`.

## Install

```powershell
cd <repo-root>
python tools\check_layout.py
python -m pip uninstall zyenlang -y
python -m pip install -e .
python tools\install_vscode_extension.py
```

## CLI

```powershell
zy check main.zy             # parse + type-check only
zy run main.zy               # transpile, compile, execute
zy build main.zy -o main.c   # emit C
zy build main.zy -o main.exe # emit .exe
```

## Hello world

```zy
fn add(a: int, b: int) -> int {
    return a + b;
}

fn main() -> int {
    let x: int = 10;
    let y: int = 20;
    print(add(x, y));
    return 0;
}
```

## Language surface (locked at v0.1.49)

- **Variables**: `let a = v;`, `let a: T = v;`, `const a = v;`
- **Mutation requires `set`**: `set a = v;`, `set a += v;`, `set *p = v;`
- **Control flow**: `if (...) {}`, `else { ... }`, `for (init; cond; step) {}`, infinite loop `for (;;) {}`. There is no `while`.
- **Functions**: `fn name(args) -> T { ... }`. Trailing default params: `fn f(a: int, b: int = 1) -> int`. Explicit forward declarations: `fn f(a: int) -> int;`.
- **Structs**: `struct S { let this.field: T; fn method() -> T { ... } }`
- **Types**: `int`, `float`, `bool`, `str`, `List`, `ptr<T>`, struct types, `void`, `None`. (`Any` is internal to `List`.)
- **f-strings**: `f"i={i}"`
- **Imports**: `import <std/math>;`, `import <std/math> as m;`, `import "lib.zy" as lib;`. Relative paths are resolved against the importing file's folder.
- **Qualified types**: `let car: test.Car = test.Car { model: "Honda" };` — imported user structs live in the global namespace, the alias is just stripped.
- **Multi-line statements**: function signatures, calls, `if` conditions, `for` headers, and list literals can span lines inside their `(...)`, `[...]`, `{...}` delimiters.

## File-scope rules

Only `import`, `struct`, and `fn` are allowed at file scope. There is no
top-level `let` or `const`. If you need a constant, expose it as a zero-arg
function:

```zy
fn max_pwm() -> int {
    return 1000;
}
```

## Mutation

```zy
let x: int = 1;
set x += 1;     // OK
set x = x + 1;  // OK

x += 1;         // ERROR — every mutation needs `set`
```

## Standard library

### Round 3: collections
`std/list`, `std/stack`, `std/queue`, `std/map`, `std/set`

### Round 2: tooling
`std/path`, `std/text`, `std/log`, `std/test`, `std/config`, `std/csv`

### Core / runtime
`std/string`, `std/char`, `std/fs`, `std/cmd`, `std/term`, `std/time`,
`std/math`, `std/mem`, `std/ptr`, `std/random`, `std/stats`

### Domain modules
`std/tk` (Tk-like GUI bridge), `std/cv`, `std/gpu`, `std/units`,
`std/filter`, `std/trajectory`, `std/robot`, `std/pid`, `std/motor`,
`std/control`, `std/thread`, `std/coroutine`, `std/geometry`,
`std/buffer`, `std/bit`, `std/range`, `std/ease`, `std/check`

```zy
import <std/stack>;
import <std/map>;

fn main() -> int {
    let s: Stack = stack.new();
    s.push_str("first");
    print(s.pop_str());

    let m: StringMap = map.new();
    m.put("name", "ZyenLang");
    print(m.get("name", "none"));
    return 0;
}
```

## Examples and tests

```powershell
zy run examples\add.zy
zy run tests\text_test.zy
```

- `examples/` — 91 single-file demos
- `tests/` — 26 test suites
- `apps/` — full programs (`apps/zyide.zy`, `apps/zyide_gui.zy`, `apps/zytk_demo.zy`)

## Layout

```
zyenlang/             # Python transpiler + installed std
zyenlang/std/         # canonical stdlib (loaded by `import <std/...>`)
std/                  # source-tree mirror of stdlib (browse-friendly)
examples/             # 91 .zy demos
tests/                # 26 test suites
docs/                 # specs and notes
apps/                 # full programs
tools/                # install / repair scripts
ide/                  # VSCode + Zed editor configs
```

## Status

v0.1.49 is an experimental language. The surface (syntax + stdlib API) is
intentionally small and **locked** — there are no plans to add lambdas,
spread / destructuring, `**kwargs`, comprehensions, or other JS / Python-
style sugar. Effort goes into fixing rough edges, not adding surface area.

## License

MIT. See [`LICENSE`](LICENSE).
