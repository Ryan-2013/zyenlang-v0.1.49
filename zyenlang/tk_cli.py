from __future__ import annotations

import sys
from pathlib import Path


def _to_int(value: str, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _read_commands(path: Path) -> list[list[str]]:
    commands: list[list[str]] = []
    if not path.exists():
        print(f"tk script not found: {path}", file=sys.stderr)
        return commands
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip("\n\r")
        if not line or line.lstrip().startswith("#"):
            continue
        commands.append(line.split("\t"))
    return commands


def show(script_path: str, close_ms: int = 0) -> int:
    commands = _read_commands(Path(script_path))
    try:
        import tkinter as tk
    except Exception as exc:
        print(f"tkinter is not available: {exc}", file=sys.stderr)
        return 2

    width = 800
    height = 600
    title = "Zyen TK"
    bg = "#202124"

    for cmd in commands:
        op = cmd[0] if cmd else ""
        if op == "window" and len(cmd) >= 4:
            title = cmd[1]
            width = _to_int(cmd[2], width)
            height = _to_int(cmd[3], height)
        elif op in {"bg", "clear"} and len(cmd) >= 2:
            bg = cmd[1]

    try:
        root = tk.Tk()
        root.title(title)
        canvas = tk.Canvas(root, width=width, height=height, bg=bg, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        images: list[object] = []

        for cmd in commands:
            if not cmd:
                continue
            op = cmd[0]
            try:
                if op == "window":
                    continue
                if op == "bg":
                    canvas.configure(bg=cmd[1])
                elif op == "clear":
                    canvas.delete("all")
                    canvas.configure(bg=cmd[1])
                elif op == "line" and len(cmd) >= 7:
                    canvas.create_line(_to_int(cmd[1]), _to_int(cmd[2]), _to_int(cmd[3]), _to_int(cmd[4]), fill=cmd[5], width=_to_int(cmd[6], 1))
                elif op == "rect" and len(cmd) >= 6:
                    x, y, w, h = map(_to_int, cmd[1:5])
                    canvas.create_rectangle(x, y, x + w, y + h, fill=cmd[5], outline="")
                elif op == "rect_outline" and len(cmd) >= 7:
                    x, y, w, h = map(_to_int, cmd[1:5])
                    canvas.create_rectangle(x, y, x + w, y + h, outline=cmd[5], width=_to_int(cmd[6], 1))
                elif op == "circle" and len(cmd) >= 5:
                    x, y, r = map(_to_int, cmd[1:4])
                    canvas.create_oval(x - r, y - r, x + r, y + r, fill=cmd[4], outline="")
                elif op == "circle_outline" and len(cmd) >= 6:
                    x, y, r = map(_to_int, cmd[1:4])
                    canvas.create_oval(x - r, y - r, x + r, y + r, outline=cmd[4], width=_to_int(cmd[5], 1))
                elif op == "text" and len(cmd) >= 6:
                    canvas.create_text(_to_int(cmd[1]), _to_int(cmd[2]), text=cmd[3], fill=cmd[4], font=("Consolas", _to_int(cmd[5], 16)), anchor="nw")
                elif op == "image" and len(cmd) >= 4:
                    img = tk.PhotoImage(file=cmd[1])
                    images.append(img)
                    canvas.create_image(_to_int(cmd[2]), _to_int(cmd[3]), image=img, anchor="nw")
            except Exception as exc:
                canvas.create_text(12, height - 28, text=f"tk command error: {op}: {exc}", fill="#ff5555", anchor="nw")

        if close_ms > 0:
            root.after(close_ms, root.destroy)
        root.mainloop()
        return 0
    except Exception as exc:
        print(f"tk failed: {exc}", file=sys.stderr)
        return 3


def _read_state(state_path: Path) -> dict[str, str]:
    if not state_path.exists():
        return {}
    try:
        body = state_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in body.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _write_state(state_path: Path, fields: dict[str, str]) -> None:
    body = "\n".join(f"{k}={v}" for k, v in fields.items()) + "\n"
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    try:
        tmp.replace(state_path)
    except OSError:
        # Fallback: best-effort overwrite. Brief torn-read window is OK
        # because the reader retries.
        state_path.write_text(body, encoding="utf-8")


def interactive(session_dir: str) -> int:
    """Run a Tk window driven by file-IPC. Documented in docs/tk_session_ipc.md."""
    try:
        import tkinter as tk
    except Exception as exc:
        print(f"tkinter is not available: {exc}", file=sys.stderr)
        return 2

    sd = Path(session_dir)
    sd.mkdir(parents=True, exist_ok=True)
    state_path = sd / "state.txt"
    scene_path = sd / "scene.ztk"
    events_path = sd / "events.txt"
    # Reset events log so ZyenLang's offset of 0 is well-defined.
    events_path.write_text("", encoding="utf-8")

    state = {"scene_version": "0", "event_version": "0", "close": "0", "closed": "0"}
    last_scene_version = -1
    event_version = 0

    title = "ZyenLang TK"
    width = 800
    height = 600
    bg = "#202124"

    import tkinter.font as tkfont

    root = tk.Tk()
    root.title(title)
    canvas = tk.Canvas(root, width=width, height=height, bg=bg, highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    images: list[object] = []

    # Measure the editor font once so ZyenLang knows real glyph advance / row
    # height. Editors that want pixel-aligned segments should request font size
    # -16 (Tk treats negative sizes as pixels) which is what we measure here.
    editor_font = tkfont.Font(family="Consolas", size=-16)
    measured_char_w = max(1, int(editor_font.measure("M")))
    measured_line_h = max(1, int(editor_font.metrics("linespace")))

    def append_event(line: str) -> None:
        nonlocal event_version
        with events_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
        event_version += 1
        nonlocal_state = _read_state(state_path)
        nonlocal_state["event_version"] = str(event_version)
        _write_state(state_path, nonlocal_state)

    font_cache: dict[int, object] = {-16: editor_font}

    def pick_font(size: int) -> object:
        if size in font_cache:
            return font_cache[size]
        f = tkfont.Font(family="Consolas", size=size)
        font_cache[size] = f
        return f

    def redraw_from_scene() -> None:
        canvas.delete("all")
        images.clear()
        if not scene_path.exists():
            return
        for raw in scene_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip("\n\r")
            if not line or line.lstrip().startswith("#"):
                continue
            cmd = line.split("\t")
            op = cmd[0]
            try:
                if op == "window" and len(cmd) >= 4:
                    nonlocal_title = cmd[1]
                    root.title(nonlocal_title)
                    try:
                        new_w = _to_int(cmd[2], width)
                        new_h = _to_int(cmd[3], height)
                        canvas.configure(width=new_w, height=new_h)
                    except Exception:
                        pass
                elif op == "bg":
                    canvas.configure(bg=cmd[1])
                elif op == "clear" and len(cmd) >= 2:
                    canvas.configure(bg=cmd[1])
                elif op == "line" and len(cmd) >= 7:
                    canvas.create_line(_to_int(cmd[1]), _to_int(cmd[2]), _to_int(cmd[3]), _to_int(cmd[4]), fill=cmd[5], width=_to_int(cmd[6], 1))
                elif op == "rect" and len(cmd) >= 6:
                    x, y, w, h = map(_to_int, cmd[1:5])
                    canvas.create_rectangle(x, y, x + w, y + h, fill=cmd[5], outline="")
                elif op == "rect_outline" and len(cmd) >= 7:
                    x, y, w, h = map(_to_int, cmd[1:5])
                    canvas.create_rectangle(x, y, x + w, y + h, outline=cmd[5], width=_to_int(cmd[6], 1))
                elif op == "circle" and len(cmd) >= 5:
                    x, y, r = map(_to_int, cmd[1:4])
                    canvas.create_oval(x - r, y - r, x + r, y + r, fill=cmd[4], outline="")
                elif op == "circle_outline" and len(cmd) >= 6:
                    x, y, r = map(_to_int, cmd[1:4])
                    canvas.create_oval(x - r, y - r, x + r, y + r, outline=cmd[4], width=_to_int(cmd[5], 1))
                elif op == "text" and len(cmd) >= 6:
                    size_val = _to_int(cmd[5], 16)
                    canvas.create_text(_to_int(cmd[1]), _to_int(cmd[2]), text=cmd[3], fill=cmd[4], font=pick_font(size_val), anchor="nw")
                elif op == "image" and len(cmd) >= 4:
                    import tkinter as _tk
                    img = _tk.PhotoImage(file=cmd[1])
                    images.append(img)
                    canvas.create_image(_to_int(cmd[2]), _to_int(cmd[3]), image=img, anchor="nw")
            except Exception as exc:
                canvas.create_text(12, height - 28, text=f"tk command error: {op}: {exc}", fill="#ff5555", anchor="nw")

    request_path = sd / "request.txt"

    def handle_requests() -> None:
        """Handle one-shot UI requests from ZyenLang (file/dir pickers, etc.)."""
        if not request_path.exists():
            return
        try:
            body = request_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return
        # Consume the request first so a failure doesn't loop forever.
        try:
            request_path.unlink()
        except OSError:
            pass
        if not body:
            return
        if body == "pickdir":
            from tkinter import filedialog
            d = filedialog.askdirectory(parent=root, title="Open folder")
            append_event(f"pickdir\t{d}" if d else "pickdir\t")
        elif body.startswith("pickfile"):
            from tkinter import filedialog
            f = filedialog.askopenfilename(parent=root, title="Open file")
            append_event(f"pickfile\t{f}" if f else "pickfile\t")

    def tick() -> None:
        nonlocal last_scene_version
        st = _read_state(state_path)
        if st.get("close") == "1":
            current = _read_state(state_path)
            current["closed"] = "1"
            _write_state(state_path, current)
            root.destroy()
            return
        try:
            sv = int(st.get("scene_version", "-1"))
        except ValueError:
            sv = -1
        if sv > last_scene_version:
            redraw_from_scene()
            last_scene_version = sv
        handle_requests()
        root.after(20, tick)

    NAV_KEYS = {"Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next"}

    def on_key(event: object) -> None:
        keysym = getattr(event, "keysym", "")
        ch = getattr(event, "char", "") or ""
        state = getattr(event, "state", 0)
        try:
            state_int = int(state)
        except (TypeError, ValueError):
            state_int = 0
        # Tk modifier masks on Windows: Shift=0x1, Control=0x4, Alt=0x20000.
        shift = bool(state_int & 0x1)
        ctrl = bool(state_int & 0x4)
        alt = bool(state_int & 0x20000)
        # Suppress noisy modifier-only events.
        if keysym in {"Control_L", "Control_R", "Shift_L", "Shift_R",
                      "Alt_L", "Alt_R", "Caps_Lock", "Num_Lock"}:
            return
        if ctrl and keysym:
            append_event(f"ctrl\t{keysym}")
            return
        if alt and keysym:
            append_event(f"alt\t{keysym}")
            return
        if shift and keysym in NAV_KEYS:
            append_event(f"shift\t{keysym}")
            return
        if ch and ch.isprintable():
            ch_safe = ch.replace("\t", " ").replace("\n", " ")
            append_event(f"keychar\t{ch_safe}")
        if keysym:
            append_event(f"key\t{keysym}")

    def on_click(event: object) -> None:
        try:
            state_int = int(getattr(event, "state", 0))
        except (TypeError, ValueError):
            state_int = 0
        x = int(getattr(event, "x", 0))
        y = int(getattr(event, "y", 0))
        if state_int & 0x4:
            append_event(f"ctrl_mouse\t{x}\t{y}")
        else:
            append_event(f"mouse\t{x}\t{y}")

    def on_release(event: object) -> None:
        append_event(f"release\t{int(getattr(event, 'x', 0))}\t{int(getattr(event, 'y', 0))}")

    motion_last_ms = [0]
    motion_last_xy = [(-1, -1)]
    drag_last_ms = [0]
    drag_last_xy = [(-1, -1)]

    def on_motion(event: object) -> None:
        import time as _t
        now_ms = int(_t.monotonic() * 1000)
        # Throttle motion events to ~12/s so we don't flood the IPC channel.
        if now_ms - motion_last_ms[0] < 80:
            return
        x = int(getattr(event, "x", 0))
        y = int(getattr(event, "y", 0))
        if (x, y) == motion_last_xy[0]:
            return
        motion_last_ms[0] = now_ms
        motion_last_xy[0] = (x, y)
        append_event(f"motion\t{x}\t{y}")

    def on_drag(event: object) -> None:
        import time as _t
        now_ms = int(_t.monotonic() * 1000)
        # Drag is more responsive than motion — throttle to ~30/s.
        if now_ms - drag_last_ms[0] < 30:
            return
        x = int(getattr(event, "x", 0))
        y = int(getattr(event, "y", 0))
        if (x, y) == drag_last_xy[0]:
            return
        drag_last_ms[0] = now_ms
        drag_last_xy[0] = (x, y)
        append_event(f"drag\t{x}\t{y}")

    def on_wheel(event: object) -> None:
        try:
            delta = int(getattr(event, "delta", 0))
        except (TypeError, ValueError):
            delta = 0
        # Windows reports ±120 per notch. Normalize to a small signed
        # integer the editor multiplies into a line count.
        notches = 0
        if delta > 0:
            notches = max(1, delta // 120)
        elif delta < 0:
            notches = -max(1, (-delta) // 120)
        x = int(getattr(event, "x", 0))
        y = int(getattr(event, "y", 0))
        append_event(f"wheel\t{notches}\t{x}\t{y}")

    def on_leave(_event: object) -> None:
        append_event("leave")

    def on_quit() -> None:
        append_event("quit")
        # Don't destroy yet; wait for ZyenLang to signal close.
        # Mark as if ZyenLang asked: a hard close avoids zombie waits.
        current = _read_state(state_path)
        current["close"] = "1"
        _write_state(state_path, current)

    def emit_tick() -> None:
        append_event("tick")
        root.after(500, emit_tick)

    root.bind("<Key>", on_key)
    root.bind("<Button-1>", on_click)
    root.bind("<ButtonRelease-1>", on_release)
    root.bind("<B1-Motion>", on_drag)
    root.bind("<Motion>", on_motion)
    # Use bind_all so wheel events are caught regardless of which widget
    # currently holds focus (on Windows MouseWheel only reaches the focused
    # widget by default, which is the canvas — and we never bound it there).
    root.bind_all("<MouseWheel>", on_wheel)
    # Linux/X11 wheel arrives as Button-4 / Button-5.
    root.bind_all("<Button-4>", lambda e: append_event(f"wheel\t1\t{int(getattr(e,'x',0))}\t{int(getattr(e,'y',0))}"))
    root.bind_all("<Button-5>", lambda e: append_event(f"wheel\t-1\t{int(getattr(e,'x',0))}\t{int(getattr(e,'y',0))}"))
    root.bind("<Leave>", on_leave)
    root.protocol("WM_DELETE_WINDOW", on_quit)

    # Tell the world we're alive. Include the measured editor metrics so
    # ZyenLang can compute pixel-accurate layouts.
    _write_state(state_path, {
        "ready": "1",
        "scene_version": "-1",
        "event_version": "0",
        "close": "0",
        "closed": "0",
        "char_w": str(measured_char_w),
        "line_h": str(measured_line_h),
        "font_px": "16",
    })
    append_event("ready")
    root.after(20, tick)
    root.after(500, emit_tick)

    try:
        root.mainloop()
    except Exception as exc:
        print(f"tk interactive failed: {exc}", file=sys.stderr)
        return 3
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print("Usage:")
        print("  python -m zyenlang.tk_cli show <script.ztk> [close_ms]")
        print("  python -m zyenlang.tk_cli interactive <session_dir>")
        return 0
    op = args.pop(0)
    if op == "show" and args:
        script = args[0]
        close_ms = _to_int(args[1], 0) if len(args) >= 2 else 0
        return show(script, close_ms)
    if op == "interactive" and args:
        return interactive(args[0])
    print("unknown tk_cli command", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
