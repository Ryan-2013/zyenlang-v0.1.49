# ZyenLang v0.1.49

[English](README.md) | **繁體中文**

> **相關 repo**
> - **規範與慣例(ZEPs)**:[zyenlang-zeps](https://github.com/Ryan-2013/zyenlang-zeps)
> - **IDE(dogfood)**:[zyenlang-ide](https://github.com/Ryan-2013/zyenlang-ide)

ZyenLang 是一個實驗性的 C-like 腳本語言,搭配 Python 風格的工具體驗。
`.zy` 原始碼會被轉譯成 C,再交給 gcc/clang 編譯。

定位:機器人、電腦視覺、控制系統、嵌入式風格實驗,以及小型引擎原型。

這包是 **Windows flat direct** 版:壓縮檔第一層直接是 `pyproject.toml`、
`zyen.py`、`zyenlang/`、`std/`、`examples/`、`tests/`、`tools/`。

## 安裝

```powershell
cd <repo-root>
python tools\check_layout.py
python -m pip uninstall zyenlang -y
python -m pip install -e .
python tools\install_vscode_extension.py
```

## CLI 指令

```powershell
zy check main.zy             # 只做語法 + 型別檢查
zy run main.zy               # 轉譯 + 編譯 + 執行
zy build main.zy -o main.c   # 只輸出 C
zy build main.zy -o main.exe # 輸出 .exe
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

## 語言表面(v0.1.49 凍結)

- **變數**:`let a = v;`、`let a: T = v;`、`const a = v;`
- **修改一律寫 `set`**:`set a = v;`、`set a += v;`、`set *p = v;`
- **流程控制**:`if (...) {}`、`else { ... }`、`for (init; cond; step) {}`,無限迴圈 `for (;;) {}`。**沒有 `while`**。
- **函式**:`fn name(args) -> T { ... }`。可有尾端預設參數 `fn f(a: int, b: int = 1) -> int`,可前置宣告 `fn f(a: int) -> int;`。
- **結構**:`struct S { let this.field: T; fn method() -> T { ... } }`
- **型別**:`int`、`float`、`bool`、`str`、`List`、`ptr<T>`、struct、`void`、`None`。(`Any` 只用在 `List` 內部,使用者拿不到。)
- **f-string**:`f"i={i}"`
- **import**:`import <std/math>;`、`import <std/math> as m;`、`import "lib.zy" as lib;`。相對路徑以「寫 import 的那個 `.zy` 檔案所在資料夾」為基準,不是終端機目前目錄。
- **限定型別**:`let car: test.Car = test.Car { model: "Honda" };` —— 被 import 的 user struct 仍在全域命名空間,前綴只是被 strip 掉。
- **多行語句**:函式簽章、函式呼叫、`if` 條件、`for` 表頭、list literal 可在它們的 `(...)`、`[...]`、`{...}` 內換行。

## 檔案頂層規則

頂層只允許 `import`、`struct`、`fn`。**不能寫頂層 `let` 或 `const`**。
若需要常數,用零參數函式包起來:

```zy
fn max_pwm() -> int {
    return 1000;
}
```

## 修改規則(mutation)

```zy
let x: int = 1;
set x += 1;     // 正確
set x = x + 1;  // 正確

x += 1;         // 錯誤 —— 任何修改都要 `set`
```

`for` 表頭第三段支援兩種寫法:

```zy
for (let i: int = 0; i < 10; i += 1) {
}

for (let i: int = 0; i < 10; set i += 1) {
}
```

## 預設參數與前置宣告(v0.1.49 新增)

### 尾端預設參數

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

規則:

- 預設參數**必須**在尾端。
- `fn bad(a: int = 1, b: int) -> int` 會被拒絕。
- 預設值由轉譯器在呼叫端展開,C 簽章不會有預設值。
- 預設值應該是字面值或呼叫端看得到的單純表達式。

### 前置函式宣告

ZyenLang 本來就會自動生 C prototype,v0.1.49 也接受顯式宣告:

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

規則:

- 每個宣告**必須**有對應的定義。
- 定義可省略已經在宣告寫過的預設值。
- 若宣告與定義都給同一個參數預設值,必須完全相同。
- 簽章不對就直接拒絕。

### 方法的預設參數

struct 的 method 一樣可以用預設參數:

```zy
struct Counter {
    let this.base: int;

    fn add(x: int = 1) -> int {
        return this.base + x;
    }
}
```

## 多行語句範例

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

## 標準函式庫

### Round 3:集合(collections)
`std/list`、`std/stack`、`std/queue`、`std/map`、`std/set`

### Round 2:工具(tooling)
`std/path`、`std/text`、`std/log`、`std/test`、`std/config`、`std/csv`

### 核心 / runtime
`std/string`、`std/char`、`std/fs`、`std/cmd`、`std/term`、`std/time`、
`std/math`、`std/mem`、`std/ptr`、`std/random`、`std/stats`

### 領域模組
`std/tk`(Tk 風格 GUI bridge)、`std/cv`、`std/gpu`、`std/units`、
`std/filter`、`std/trajectory`、`std/robot`、`std/pid`、`std/motor`、
`std/control`、`std/thread`、`std/coroutine`、`std/geometry`、
`std/buffer`、`std/bit`、`std/range`、`std/ease`、`std/check`

### 集合範例

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

### 測試 harness 範例

```zy
import <std/test>;

fn main() -> int {
    let t: TestStats = test.new_stats("demo");
    t.eq_int("math", 1 + 1, 2);
    t.eq_str("name", "Zyen", "Zyen");
    return t.summary();
}
```

## 範例與測試

```powershell
zy run examples\add.zy
zy run tests\text_test.zy
```

- `examples/` —— 91 支單檔範例
- `tests/` —— 26 個測試
- `apps/` —— 完整應用程式(`apps/zyide.zy`、`apps/zyide_gui.zy`、`apps/zytk_demo.zy`)

## 專案結構

```
zyenlang/             # Python 轉譯器 + 內附 std
zyenlang/std/         # 標準函式庫正本(import <std/...> 會載這份)
std/                  # 標準函式庫的源樹鏡像(方便瀏覽)
examples/             # 91 支 .zy 範例
tests/                # 26 個測試
docs/                 # 規格與筆記
apps/                 # 完整應用程式
tools/                # 安裝 / 修補腳本
ide/                  # VSCode + Zed 編輯器設定
```

## 狀態

v0.1.49 是實驗性語言。語法表面(語法 + stdlib API)**故意保持小,並且已凍結** ——
不打算加 lambda、spread / destructuring、`**kwargs`、comprehension,
或其他 JS / Python 風格的語法糖。後續精力會花在修小毛病,不會擴張表面。

## 授權

MIT。見 [`LICENSE`](LICENSE)。
