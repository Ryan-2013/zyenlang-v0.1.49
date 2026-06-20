from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk
except Exception as exc:  # pragma: no cover
    print(f"Zyen IDE requires tkinter: {exc}", file=sys.stderr)
    raise

KEYWORDS = {
    "import", "fn", "return", "let", "const", "set", "if", "else", "for", "break", "pass", "stop",
    "struct", "this", "true", "false", "None", "ptr", "List", "int", "float", "bool", "str", "void",
}
BUILTINS = {"print", "mem", "cmd", "fs", "term", "time", "math", "string", "tk", "gpu", "cv"}
SNIPPETS = {
    "fn main": "fn main() -> int {\n    return 0;\n}\n",
    "fn void": "fn name() -> void {\n    pass;\n}\n",
    "if": "if (condition) {\n    pass;\n}\n",
    "if else": "if (condition) {\n    pass;\n} else {\n    pass;\n}\n",
    "for": "for (let i = 0; i < 10; i++) {\n    pass;\n}\n",
    "struct": "struct Name {\n    let this.value: int;\n\n    fn method() -> void {\n        pass;\n    }\n}\n",
    "ptr": "let p: ptr<int>;\nset *p = 0;\n",
    "List": "let xs: List;\nxs.append(1);\nprint(xs.len());\n",
    "import mem": "import <std/mem>;\n",
    "import string": "import <std/string>;\n",
}

THEMES = {
    "dark": {
        "bg": "#111827", "fg": "#d1d5db", "insert": "#f9fafb", "line_bg": "#0b1020",
        "keyword": "#f59e0b", "type": "#60a5fa", "string": "#34d399", "comment": "#6b7280",
        "number": "#f472b6", "builtin": "#a78bfa", "current": "#1f2937", "select": "#374151",
    },
    "light": {
        "bg": "#ffffff", "fg": "#111827", "insert": "#111827", "line_bg": "#f3f4f6",
        "keyword": "#b45309", "type": "#1d4ed8", "string": "#047857", "comment": "#6b7280",
        "number": "#be185d", "builtin": "#7c3aed", "current": "#e5e7eb", "select": "#bfdbfe",
    },
}

@dataclass
class Doc:
    frame: ttk.Frame
    text: tk.Text
    path: Path | None = None
    dirty: bool = False

    @property
    def title(self) -> str:
        name = self.path.name if self.path else "Untitled.zy"
        return name + (" *" if self.dirty else "")


class ZyenIde:
    def __init__(self, root: tk.Tk, start_file: str | None = None):
        self.root = root
        self.root.title("ZyenLang IDE")
        self.theme_name = "dark"
        self.theme = THEMES[self.theme_name]
        self.docs: list[Doc] = []
        self._highlight_after: str | None = None

        self._build_ui()
        self._bind_shortcuts()
        if start_file:
            self.open_path(Path(start_file))
        else:
            self.new_file()

    def _build_ui(self) -> None:
        self.root.geometry("1120x760")
        self.root.minsize(900, 560)

        self.menu = tk.Menu(self.root)
        self.root.config(menu=self.menu)
        file_menu = tk.Menu(self.menu, tearoff=False)
        file_menu.add_command(label="New", accelerator="Ctrl+N", command=self.new_file)
        file_menu.add_command(label="Open...", accelerator="Ctrl+O", command=self.open_dialog)
        file_menu.add_command(label="Save", accelerator="Ctrl+S", command=self.save_current)
        file_menu.add_command(label="Save As...", accelerator="Ctrl+Shift+S", command=self.save_as_current)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.destroy)
        self.menu.add_cascade(label="File", menu=file_menu)

        run_menu = tk.Menu(self.menu, tearoff=False)
        run_menu.add_command(label="Check", accelerator="F6", command=self.check_current)
        run_menu.add_command(label="Run", accelerator="F5", command=self.run_current)
        run_menu.add_command(label="Build C", command=self.build_c_current)
        run_menu.add_command(label="Build EXE", command=self.build_exe_current)
        self.menu.add_cascade(label="Run", menu=run_menu)

        edit_menu = tk.Menu(self.menu, tearoff=False)
        edit_menu.add_command(label="Find / Replace", accelerator="Ctrl+F", command=self.find_replace_dialog)
        snippets_menu = tk.Menu(edit_menu, tearoff=False)
        for name in SNIPPETS:
            snippets_menu.add_command(label=name, command=lambda n=name: self.insert_snippet(n))
        edit_menu.add_cascade(label="Snippets", menu=snippets_menu)
        edit_menu.add_separator()
        edit_menu.add_command(label="Toggle Theme", command=self.toggle_theme)
        self.menu.add_cascade(label="Edit", menu=edit_menu)

        toolbar = ttk.Frame(self.root)
        toolbar.pack(side=tk.TOP, fill=tk.X)
        for label, command in [
            ("Open", self.open_dialog), ("Save", self.save_current), ("Check", self.check_current),
            ("Run", self.run_current), ("Build C", self.build_c_current), ("Build EXE", self.build_exe_current),
            ("Find", self.find_replace_dialog), ("Theme", self.toggle_theme),
        ]:
            ttk.Button(toolbar, text=label, command=command).pack(side=tk.LEFT, padx=2, pady=2)

        main = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(main, width=220)
        ttk.Label(left, text="Outline").pack(anchor="w", padx=6, pady=(6, 2))
        self.outline = tk.Listbox(left, height=20)
        self.outline.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        self.outline.bind("<<ListboxSelect>>", self.goto_outline)
        main.add(left, weight=0)

        right = ttk.PanedWindow(main, orient=tk.VERTICAL)
        main.add(right, weight=1)

        self.tabs = ttk.Notebook(right)
        self.tabs.bind("<<NotebookTabChanged>>", lambda _e: self.refresh_all())
        right.add(self.tabs, weight=4)

        bottom = ttk.Frame(right)
        ttk.Label(bottom, text="Output").pack(anchor="w", padx=6, pady=(4, 0))
        self.output = tk.Text(bottom, height=10, wrap=tk.WORD)
        self.output.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        right.add(bottom, weight=1)

        self.status = tk.StringVar(value="Ready")
        ttk.Label(self.root, textvariable=self.status, anchor="w").pack(side=tk.BOTTOM, fill=tk.X)
        self.apply_theme()

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-n>", lambda _e: self.new_file())
        self.root.bind("<Control-o>", lambda _e: self.open_dialog())
        self.root.bind("<Control-s>", lambda _e: self.save_current())
        self.root.bind("<Control-S>", lambda _e: self.save_as_current())
        self.root.bind("<F5>", lambda _e: self.run_current())
        self.root.bind("<F6>", lambda _e: self.check_current())
        self.root.bind("<Control-f>", lambda _e: self.find_replace_dialog())

    def current_doc(self) -> Doc | None:
        if not self.docs:
            return None
        tab_id = self.tabs.select()
        for doc in self.docs:
            if str(doc.frame) == tab_id:
                return doc
        return self.docs[0]

    def new_file(self) -> None:
        frame = ttk.Frame(self.tabs)
        text = tk.Text(frame, undo=True, wrap=tk.NONE, font=("Consolas", 12), padx=10, pady=8)
        xscroll = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=text.xview)
        yscroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        text.configure(xscrollcommand=xscroll.set, yscrollcommand=yscroll.set)
        text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        doc = Doc(frame=frame, text=text)
        self.docs.append(doc)
        self.tabs.add(frame, text=doc.title)
        self.tabs.select(frame)
        text.bind("<<Modified>>", lambda _e, d=doc: self.on_modified(d))
        text.bind("<KeyRelease>", lambda _e: self.schedule_highlight())
        text.insert("1.0", "fn main() -> int {\n    return 0;\n}\n")
        text.edit_modified(False)
        self.apply_text_theme(text)
        self.highlight_current()
        self.refresh_all()

    def open_dialog(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Zyen files", "*.zy"), ("All files", "*.*")])
        if path:
            self.open_path(Path(path))

    def open_path(self, path: Path) -> None:
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("Open failed", str(exc))
            return
        self.new_file()
        doc = self.current_doc()
        if doc is None:
            return
        doc.text.delete("1.0", tk.END)
        doc.text.insert("1.0", content)
        doc.path = path
        doc.dirty = False
        doc.text.edit_modified(False)
        self.update_tab_title(doc)
        self.highlight_current()
        self.refresh_all()
        self.status.set(f"Opened {path}")

    def save_current(self) -> bool:
        doc = self.current_doc()
        if doc is None:
            return False
        if doc.path is None:
            return self.save_as_current()
        try:
            doc.path.write_text(doc.text.get("1.0", "end-1c"), encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            return False
        doc.dirty = False
        doc.text.edit_modified(False)
        self.update_tab_title(doc)
        self.status.set(f"Saved {doc.path}")
        return True

    def save_as_current(self) -> bool:
        doc = self.current_doc()
        if doc is None:
            return False
        path = filedialog.asksaveasfilename(defaultextension=".zy", filetypes=[("Zyen files", "*.zy"), ("All files", "*.*")])
        if not path:
            return False
        doc.path = Path(path)
        return self.save_current()

    def on_modified(self, doc: Doc) -> None:
        if doc.text.edit_modified():
            doc.dirty = True
            doc.text.edit_modified(False)
            self.update_tab_title(doc)
            self.schedule_highlight()
            self.refresh_outline()

    def update_tab_title(self, doc: Doc) -> None:
        try:
            self.tabs.tab(doc.frame, text=doc.title)
        except Exception:
            pass

    def _ensure_saved_or_temp(self) -> Path | None:
        doc = self.current_doc()
        if doc is None:
            return None
        if doc.path is not None:
            self.save_current()
            return doc.path
        tmp = Path(tempfile.gettempdir()) / "zyen_ide_unsaved.zy"
        tmp.write_text(doc.text.get("1.0", "end-1c"), encoding="utf-8")
        return tmp

    def run_zy(self, args: list[str]) -> None:
        path = self._ensure_saved_or_temp()
        if path is None:
            return
        cmd = [sys.executable, "-m", "zyenlang", *args, str(path)]
        self.write_output(f"$ {' '.join(cmd)}\n")
        try:
            p = subprocess.run(cmd, text=True, capture_output=True, cwd=str(path.parent))
            if p.stdout:
                self.write_output(p.stdout)
            if p.stderr:
                self.write_output(p.stderr)
            self.write_output(f"[exit {p.returncode}]\n")
            self.status.set(f"Command exit {p.returncode}")
        except Exception as exc:
            self.write_output(f"ERROR: {exc}\n")

    def check_current(self) -> None:
        self.run_zy(["check"])

    def run_current(self) -> None:
        self.run_zy(["run"])

    def build_c_current(self) -> None:
        path = self._ensure_saved_or_temp()
        if path is None:
            return
        out = path.with_suffix(".c")
        cmd = [sys.executable, "-m", "zyenlang", "build", str(path), "-o", str(out)]
        self.run_subprocess(cmd, cwd=path.parent)

    def build_exe_current(self) -> None:
        path = self._ensure_saved_or_temp()
        if path is None:
            return
        out = path.with_suffix(".exe" if os.name == "nt" else "")
        if str(out) == str(path):
            out = path.parent / (path.stem + "_app")
        cmd = [sys.executable, "-m", "zyenlang", "build", str(path), "-o", str(out)]
        self.run_subprocess(cmd, cwd=path.parent)

    def run_subprocess(self, cmd: list[str], cwd: Path | None = None) -> None:
        self.write_output(f"$ {' '.join(map(str, cmd))}\n")
        try:
            p = subprocess.run(cmd, text=True, capture_output=True, cwd=str(cwd) if cwd else None)
            if p.stdout:
                self.write_output(p.stdout)
            if p.stderr:
                self.write_output(p.stderr)
            self.write_output(f"[exit {p.returncode}]\n")
            self.status.set(f"Command exit {p.returncode}")
        except Exception as exc:
            self.write_output(f"ERROR: {exc}\n")

    def write_output(self, text: str) -> None:
        self.output.configure(state=tk.NORMAL)
        self.output.insert(tk.END, text)
        self.output.see(tk.END)
        self.output.configure(state=tk.NORMAL)

    def refresh_all(self) -> None:
        self.refresh_outline()
        self.highlight_current()

    def refresh_outline(self) -> None:
        doc = self.current_doc()
        self.outline.delete(0, tk.END)
        if doc is None:
            return
        text = doc.text.get("1.0", "end-1c")
        for no, line in enumerate(text.splitlines(), start=1):
            m = re.match(r"\s*(fn|struct)\s+([A-Za-z_]\w*)", line)
            if m:
                self.outline.insert(tk.END, f"{no}: {m.group(1)} {m.group(2)}")

    def goto_outline(self, _event=None) -> None:
        doc = self.current_doc()
        if doc is None:
            return
        sel = self.outline.curselection()
        if not sel:
            return
        item = self.outline.get(sel[0])
        line = item.split(":", 1)[0]
        doc.text.mark_set(tk.INSERT, f"{line}.0")
        doc.text.see(tk.INSERT)
        doc.text.focus_set()

    def schedule_highlight(self) -> None:
        if self._highlight_after:
            self.root.after_cancel(self._highlight_after)
        self._highlight_after = self.root.after(120, self.highlight_current)

    def highlight_current(self) -> None:
        doc = self.current_doc()
        if doc is None:
            return
        text = doc.text
        content = text.get("1.0", "end-1c")
        for tag in ["keyword", "type", "string", "comment", "number", "builtin"]:
            text.tag_remove(tag, "1.0", tk.END)
        self._tag_regex(text, r"//.*$", "comment", flags=re.MULTILINE)
        self._tag_regex(text, r"f?\"([^\"\\]|\\.)*\"", "string")
        self._tag_regex(text, r"\b\d+(?:\.\d+)?\b", "number")
        self._tag_words(text, KEYWORDS, "keyword")
        self._tag_words(text, {"int", "float", "bool", "str", "ptr", "List", "void"}, "type")
        self._tag_words(text, BUILTINS, "builtin")
        self._highlight_after = None

    def _tag_words(self, text: tk.Text, words: set[str], tag: str) -> None:
        pattern = r"\b(" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r")\b"
        self._tag_regex(text, pattern, tag)

    def _tag_regex(self, text: tk.Text, pattern: str, tag: str, flags: int = 0) -> None:
        content = text.get("1.0", "end-1c")
        for m in re.finditer(pattern, content, flags):
            start = f"1.0+{m.start()}c"
            end = f"1.0+{m.end()}c"
            text.tag_add(tag, start, end)

    def find_replace_dialog(self) -> None:
        doc = self.current_doc()
        if doc is None:
            return
        win = tk.Toplevel(self.root)
        win.title("Find / Replace")
        ttk.Label(win, text="Find").grid(row=0, column=0, padx=6, pady=6)
        find_var = tk.StringVar()
        rep_var = tk.StringVar()
        ttk.Entry(win, textvariable=find_var, width=40).grid(row=0, column=1, padx=6, pady=6)
        ttk.Label(win, text="Replace").grid(row=1, column=0, padx=6, pady=6)
        ttk.Entry(win, textvariable=rep_var, width=40).grid(row=1, column=1, padx=6, pady=6)

        def do_find():
            needle = find_var.get()
            if not needle:
                return
            start = doc.text.search(needle, tk.INSERT, tk.END)
            if not start:
                start = doc.text.search(needle, "1.0", tk.END)
            if start:
                end = f"{start}+{len(needle)}c"
                doc.text.tag_remove("sel", "1.0", tk.END)
                doc.text.tag_add("sel", start, end)
                doc.text.mark_set(tk.INSERT, end)
                doc.text.see(start)

        def do_replace():
            try:
                start = doc.text.index("sel.first")
                end = doc.text.index("sel.last")
            except tk.TclError:
                do_find()
                return
            doc.text.delete(start, end)
            doc.text.insert(start, rep_var.get())

        def do_replace_all():
            content = doc.text.get("1.0", "end-1c")
            doc.text.delete("1.0", tk.END)
            doc.text.insert("1.0", content.replace(find_var.get(), rep_var.get()))

        ttk.Button(win, text="Find", command=do_find).grid(row=2, column=0, padx=6, pady=6)
        ttk.Button(win, text="Replace", command=do_replace).grid(row=2, column=1, sticky="w", padx=6, pady=6)
        ttk.Button(win, text="Replace All", command=do_replace_all).grid(row=2, column=1, sticky="e", padx=6, pady=6)

    def insert_snippet(self, name: str) -> None:
        doc = self.current_doc()
        if doc is None:
            return
        doc.text.insert(tk.INSERT, SNIPPETS[name])
        doc.text.focus_set()

    def toggle_theme(self) -> None:
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        self.theme = THEMES[self.theme_name]
        self.apply_theme()

    def apply_theme(self) -> None:
        self.root.configure(bg=self.theme["bg"])
        self.output.configure(bg=self.theme["line_bg"], fg=self.theme["fg"], insertbackground=self.theme["insert"])
        self.outline.configure(bg=self.theme["line_bg"], fg=self.theme["fg"], selectbackground=self.theme["select"])
        for doc in self.docs:
            self.apply_text_theme(doc.text)

    def apply_text_theme(self, text: tk.Text) -> None:
        text.configure(bg=self.theme["bg"], fg=self.theme["fg"], insertbackground=self.theme["insert"], selectbackground=self.theme["select"])
        text.tag_configure("keyword", foreground=self.theme["keyword"])
        text.tag_configure("type", foreground=self.theme["type"])
        text.tag_configure("string", foreground=self.theme["string"])
        text.tag_configure("comment", foreground=self.theme["comment"])
        text.tag_configure("number", foreground=self.theme["number"])
        text.tag_configure("builtin", foreground=self.theme["builtin"])


def self_test() -> int:
    # Does not open a GUI. Verifies module-level syntax and key constants.
    assert "fn main" in SNIPPETS
    assert "dark" in THEMES and "light" in THEMES
    print("Zyen IDE GUI self-test OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m zyenlang.ide_gui", description="ZyenLang Tk GUI IDE")
    parser.add_argument("file", nargs="?", help="optional .zy file to open")
    parser.add_argument("--self-test", action="store_true", help="run non-GUI self test")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    root = tk.Tk()
    ZyenIde(root, args.file)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
