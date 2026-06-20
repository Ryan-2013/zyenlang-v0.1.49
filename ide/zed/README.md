# ZyenLang Zed Support v0.1.10

This folder contains two levels of Zed support.

## 1. Project tasks

The project includes `.zed/tasks.json`, so in Zed you can run:

```text
Ctrl+Shift+P -> task: spawn
```

Available tasks:

- `Zyen: check current file`
- `Zyen: run current file`
- `Zyen: build C from current file`
- `Zyen: build exe from current file`

## 2. Experimental language extension

The extension scaffold is here:

```text
ide/zed/zyenlang/
```

It provides `.zy` file association, `//` comments, brackets, and Tree-sitter highlight queries.

Prepare it:

```bash
python tools/install_zed_support.py
```

Then in Zed:

```text
Ctrl+Shift+P -> zed: install dev extension
```

Select:

```text
ide/zed/zyenlang
```

Note: Zed language extensions use Tree-sitter. For local development, the grammar path in `extension.toml` must point to a local git repo. The installer script creates/updates that local grammar repo and manifest automatically.
