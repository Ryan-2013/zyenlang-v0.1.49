# ZyenLang v0.1.49 Spec Snapshot

## 語言定位

ZyenLang 是 C-like 語法、Python-like 工具體驗的實驗語言。`.zy` 目前轉譯成 C，再交給 gcc/clang 編譯。

核心定位：robotics / CV / control systems / embedded-style experiments / engine prototyping。

## File scope

Top-level 目前只允許：

```text
import
struct
fn
```

不支援 top-level `let` / `const` / `set`。v0.1.49 會明確報錯，不再靜默丟掉。

## 基本規則

- 函式：`fn name(args) -> type { ... }`
- 變數：`let a = value;` 或 `let a: type = value;`
- 常數：`const a = value;`
- 修改：`set a = value;`
- compound 修改：`set a += value;`
- 指針寫入：`set *p = value;`
- 大部分執行語句需要 `;`
- `if` 和 `for` 都要括號：`if (...) {}`、`for (...; ...; ...) {}`
- 沒有 `while`，用 `for (;;)` 表示無限迴圈

## 型別

```text
int
float
bool
str
List
ptr<T>
struct type
void
None
```

`Any` 不是 user-facing 型別，只保留在 `List` runtime 內部。

## mutation

正確：

```zy
let x: int = 1;
set x += 1;
set x = x + 1;
```

錯誤：

```zy
x += 1;
x = x + 1;
```

`for` 第三段可用：

```zy
for (let i: int = 0; i < 10; i += 1) {
}

for (let i: int = 0; i < 10; set i += 1) {
}
```

## f-string

```zy
let i: int = 12;
print(f"i={i}");
```

普通字串中的 `"f"` 不應觸發 f-string scanner。

## import

```zy
import <std/math>;
import <std/math> as m;
import "list.zy";
import "list.zy" as list;
import "../hi.zy";
import "../lib/hi.zy" as hi;
```

相對路徑以目前 `.zy` 檔案所在資料夾為基準。

## struct / this

```zy
struct Counter {
    let this.value: int;

    fn inc() -> void {
        set this.value = this.value + 1;
    }
}
```

struct 欄位必須寫 `let this.name: type;`。

## List

```zy
let list: List;
list.append(1);
list.append("hello");
print(list.len());
print(list.get(0));
list.set(0, 99);
print(list.pop());
list.clear();
```

`append()` 是 `void`，不能寫 `let x = list.append(1);`。

List 作為函式參數時以 reference 傳遞，因此 mutation 會 propagate。

## ptr / mem

```zy
import <std/mem>;

let p: ptr<int>;
set *p = 5;
print(*p);

print(mem.is_valid(p));
mem.free(p);
```

`None` 只代表 ptr 沒有有效位置。

## std Round 2 modules

```zy
import <std/path>;
import <std/text>;
import <std/log>;
import <std/test>;
import <std/config>;
import <std/csv>;
```

## std Round 3 collections

```zy
import <std/list>;
import <std/stack>;
import <std/queue>;
import <std/map>;
import <std/set>;
```

### std/list

```zy
let xs: List;
xs.append("a");
xs.append("b");
print(list.join_str(xs, ","));
```

### std/stack

```zy
let s: Stack = stack.new();
s.push_str("a");
print(s.pop_str());
```

### std/queue

```zy
let q: Queue = queue.new();
q.push_str("a");
print(q.pop_str());
```

### std/map

```zy
let m: StringMap = map.new();
m.put("name", "ZyenLang");
print(m.get("name", "none"));
```

### std/set

```zy
let s: StringSet = set.new();
s.add("robotics");
print(s.has("robotics"));
```

## Runtime notes

- `zl_str_join` 在 v0.1.45 改為 heap allocation，避免 8-buffer rotation 覆蓋長生命週期字串。
- `term.input` 回傳獨立字串。
- `List` 在 C runtime 內部用 `Any` array 表示。
- `ptr<T>` 在 C runtime 內部用 `{ addr, type_name, mem_id, owned }` 表示。

## CLI

```powershell
zy check main.zy
zy run main.zy
zy build main.zy -o main.c
zy build main.zy -o main.exe
```

## v0.1.49 parser and stdlib update

### Multiline logical statements

The compiler now joins physical lines into logical statements while tracking strings, escapes, parentheses, brackets, and expression braces.

Supported multiline forms:

```zy
fn add(
    a: int,
    b: int
) -> int {
    return a + b;
}

let n: int = add(
    10,
    20
);

if (
    n > 10
) {
    print("large");
}

for (
    let i: int = 0;
    i < 10;
    i += 1
) {
    print(i);
}
```

ZyenLang still uses one logical statement at a time. Two independent statements on one physical line are not part of the style or parser guarantee.

### New standard modules

- `std/units`: angle, speed, length, mass, and time unit conversion.
- `std/filter`: low-pass, high-pass, moving average, deadband, slew-rate, threshold, hysteresis.
- `std/trajectory`: linear/smoothstep interpolation and ramp helpers.
- `std/robot`: `Pose2D`, `JointLimit`, joint limit helpers, pose helpers, differential drive helpers.


## v0.1.49 Function defaults and declarations

### Default parameters

Function parameters may provide trailing default values:

```zy
fn f(a: int, b: int = 1, label: str = "x") -> int {
    return a + b;
}
```

A call may omit only trailing defaulted parameters:

```zy
f(10);
f(10, 2);
f(10, 2, "custom");
```

A non-default parameter may not follow a default parameter.

### Function declarations

A top-level function may be declared before it is defined:

```zy
fn f(a: int, b: int = 1) -> int;

fn f(a: int, b: int) -> int {
    return a + b;
}
```

The declaration contributes to the compiler signature table and to call-site default completion. Every declaration must have a matching definition. Return type and parameter names/types must match.

### Implementation notes

The compiler stores each function as a `FunctionDef` with ordered `params`, a `defaults` map, and declaration/definition state. During expression transformation, direct calls and struct-method calls are completed with missing default expressions before argument type wrapping. C output remains plain C: defaults are not emitted into C signatures; they are expanded at ZyenLang call sites.
