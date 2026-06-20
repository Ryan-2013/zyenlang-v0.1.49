# ZyenLang VS Code Extension v0.1.49

This unpacked VS Code extension adds basic editor support for `.zy` files.

## Features

- `.zy` file association
- ZyenLang syntax highlighting
- line comment support: `//`
- bracket pairing and indentation
- snippets for `fn`, `main`, `let`, `const`, `for`, `if`, `struct`, `import`, `pass`, `break`, `cmd.run`

## Install manually

Copy this folder into your VS Code extensions folder:

Windows:

```text
%USERPROFILE%\.vscode\extensions\zyenlang-vscode-0.1.49
```

macOS / Linux:

```text
~/.vscode/extensions/zyenlang-vscode-0.1.49
```

Then restart VS Code.

## Install from the ZyenLang project root

From the ZyenLang project root, run:

```bash
python tools/install_vscode_extension.py
```

Then restart VS Code.

## v0.1.49

`if` snippets now use `if (condition) {}` to match C-style `for`.

## v0.1.49

Adds local file namespace import highlighting and snippets:

```zy
import "list.zy" as list;
list.say_hi();
```


## v0.1.49

Adds `std/mem` snippets and highlighting for explicit memory-control workflows.

```zy
import <std/mem>;
let p: ptr<int> = mem.alloc_int(0);
set *p = 5;
mem.free(p);
```

## v0.1.49

Adds f-string highlighting/snippets and CLI performance monitor examples.

```zy
print(f"score={score}, ok={ok}");
```


## v0.1.49

Adds highlighting/snippets for `std/fs`, `std/term`, and the Zy-written CLI IDE workflow.

```zy
import <std/term>;
import <std/fs>;
let file: str = term.input("file> ");
print(fs.read(file));
```


## v0.1.49

Adds `std/tk` snippets for Tk-like GUI/canvas experiments.

```zy
import <std/tk>;
tk.open("Demo", 800, 480);
tk.rect(40, 40, 120, 80, "#ff8800");
tk.show();
```
