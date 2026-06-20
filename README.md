# ZyenLang v0.1.49 Windows Flat Direct

ZyenLang 是一個實驗性 C-like scripting language：`let / const / set` 明確、`fn` 函式、`struct + this`、`List`、安全 `ptr<T>` / `mem`、標準庫、Tk-like GUI bridge，並轉譯成 C 執行。

這包是 **Windows flat direct** 版：ZIP 第一層直接有 `pyproject.toml`、`zyen.py`、`zyenlang/`、`std/`、`examples/`、`tests/`、`tools/`。

## 安裝

```powershell
cd D:\python_project\N_code\zyenlang_v0_1_47_windows_flat_direct
python tools\check_layout.py
python -m pip uninstall zyenlang -y
python -m pip install -e .
python tools\install_vscode_extension.py
```

## v0.1.49 重點

### 標準庫 Round 3：Collections

新增並鏡像到 `std/` 與 `zyenlang/std/`：

- `std/list`：List helper，包括 `join_str`、`contains_str`、`index_of_str`、`copy`、`reverse`、`slice`、`without_at`、`append_all`
- `std/stack`：`Stack`，支援 typed push/pop/peek、`len`、`is_empty`、`clear`
- `std/queue`：`Queue`，FIFO，支援 typed push/pop/peek、`len`、`is_empty`、`clear`
- `std/map`：`StringMap`，string key / string value map
- `std/set`：`StringSet`，string set

新增測試：

```powershell
zy run tests\list_test.zy
zy run tests\stack_test.zy
zy run tests\queue_test.zy
zy run tests\map_test.zy
zy run tests\set_test.zy
```

新增範例：

```powershell
zy run examples\list_helpers.zy
zy run examples\collections_basic.zy
```

### v0.1.45 保留重點

- `std/path`：`basename`、`dirname`、`extension`、`stem`、`join`、`normalize`、`is_absolute`、`with_extension`、`parent`
- `std/text`：多行文字工具，給 IDE / formatter / config parser 使用
- `std/log`：簡單 logger，支援 level 與 file output
- `std/test`：`TestStats` 測試 harness
- `std/config`：簡單 `key=value` parser
- `std/csv`：簡單 CSV split/join，支援 quoted comma 與 doubled quote

## mutation 規則

一般區塊內，修改變數必須寫 `set`：

```zy
let x: int = 1;
set x += 1;
set x = x + 1;
```

禁止：

```zy
x += 1;
x = x + 1;
```

`for` 第三段是 loop control syntax，支援兩種：

```zy
for (let i: int = 0; i < 10; i += 1) {
}

for (let i: int = 0; i < 10; set i += 1) {
}
```

## top-level 規則

目前 top-level 只允許：

```text
import
struct
fn
```

不支援 top-level 變數：

```zy
let g: int = 1; // error in v0.1.49
```

如果需要常數，先用零參數函式：

```zy
fn max_pwm() -> int {
    return 1000;
}
```

## std/list 範例

```zy
import <std/list>;

fn main() -> int {
    let xs: List;
    xs.append("robot");
    xs.append("cv");
    xs.append("control");
    print(list.join_str(xs, " | "));
    return 0;
}
```

## collections 範例

```zy
import <std/stack>;
import <std/queue>;
import <std/map>;
import <std/set>;

fn main() -> int {
    let s: Stack = stack.new();
    s.push_str("first");
    s.push_str("second");
    print(s.pop_str());

    let q: Queue = queue.new();
    q.push_str("first");
    q.push_str("second");
    print(q.pop_str());

    let m: StringMap = map.new();
    m.put("name", "ZyenLang");
    print(m.get("name", "none"));

    let tags: StringSet = set.new();
    tags.add("robotics");
    tags.add("cv");
    print(tags.len());

    return 0;
}
```

## std/test 範例

```zy
import <std/test>;

fn main() -> int {
    let t: TestStats = test.new_stats("demo");
    t.eq_int("math", 1 + 1, 2);
    t.eq_str("name", "Zyen", "Zyen");
    return t.summary();
}
```

## import 路徑規則

```zy
import <std/string>;
import "../hi.zy";
import "../lib/hi.zy" as hi;
```

相對路徑永遠以「寫 import 的那個 `.zy` 檔案所在資料夾」為基準，不是 terminal 目前目錄。

## CLI

```powershell
zy check main.zy
zy run main.zy
zy build main.zy -o main.c
zy build main.zy -o main.exe
```

## v0.1.49 highlights

- Upgraded `clean_lines()` into a tokenizer-aware logical line assembler.
- Multiline function signatures, function calls, method calls, `if` conditions, `for` headers, and list literals are now supported.
- Added robotics/control-oriented stdlib modules: `std/units`, `std/filter`, `std/trajectory`, and `std/robot`.
- Added tests: `multiline_syntax_test`, `units_test`, `filter_test`, `trajectory_test`, and `robot_test`.

Example multiline syntax:

```zy
fn add(
    a: int,
    b: int
) -> int {
    return a + b;
}

fn main() -> int {
    let n: int = add(
        10,
        20
    );

    for (
        let i: int = 0;
        i < 10;
        i += 1
    ) {
        print(i);
    }

    return 0;
}
```


## v0.1.49 — default parameters and explicit function declarations

v0.1.49 adds two function-system features:

### Default function parameters

```zy
fn add(a: int, b: int = 10) -> int {
    return a + b;
}

fn main() -> int {
    print(add(5));     // 15
    print(add(5, 2));  // 7
    return 0;
}
```

Rules:

- Default parameters must be trailing.
- `fn bad(a: int = 1, b: int) -> int` is rejected.
- Defaults are filled at call sites by the transpiler.
- Default values should be simple expressions such as literals or normal expressions visible at the call site.

### Explicit function declaration before definition

ZyenLang already emits C prototypes automatically, but v0.1.49 also accepts explicit declarations:

```zy
fn scale(a: int, factor: int = 2) -> int;

fn main() -> int {
    print(scale(7));    // 14
    print(scale(7, 3)); // 21
    return 0;
}

fn scale(a: int, factor: int) -> int {
    return a * factor;
}
```

Rules:

- A declaration must eventually have a matching definition.
- The definition may omit defaults declared in the prototype.
- If both declaration and definition provide a default for the same parameter, they must match exactly.
- Signature mismatch is rejected.

### Method default parameters

Struct methods can also use default parameters:

```zy
struct Counter {
    let this.base: int;

    fn add(x: int = 1) -> int {
        return this.base + x;
    }
}
```
