# tk session IPC protocol

A file-based polling protocol that lets a long-running ZyenLang program drive a Tk window owned by `python -m zyenlang.tk_cli interactive <session_dir>`.

## Why file polling
ZyenLang has no pipe / socket primitives. `thread.spawn_cmd` can launch a background process but cannot read/write its stdio. Files in a session directory are the lowest-common-denominator transport that works today.

## Session directory layout
```
<session_dir>/
  state.txt     # small key=value file, atomic full-rewrites only
  scene.ztk     # current scene script (same format as one-shot mode)
  events.txt    # append-only log of events from Python to ZyenLang
```

## state.txt
Each side over-writes the entire file when bumping any field. Keys (one per line, `key=value`):
- `ready=1`           — written by Python once the Tk window is up
- `scene_version=N`   — written by ZyenLang after writing scene.ztk; tells Python to reload
- `event_version=M`   — written by Python after appending to events.txt; tells ZyenLang to drain
- `close=1`           — written by ZyenLang to request shutdown
- `closed=1`          — written by Python just before `root.destroy()`

A torn read (file mid-write) is treated as "no change" and retried after 10ms.

## events.txt
Append-only stream from Python to ZyenLang. One event per line, tab-separated:
```
ready
key\t<keysym>
keychar\t<char>
mouse\t<x>\t<y>
resize\t<w>\t<h>
quit
```
ZyenLang remembers the byte offset it last read; on each event_version bump it re-reads from that offset and splits on newlines.

## Roundtrip
1. ZyenLang spawns Python with `python -m zyenlang.tk_cli interactive <session_dir>`.
2. Python creates Tk window, writes `state.txt` with `ready=1`, appends `ready` event.
3. ZyenLang polls until it sees `ready=1` (timeout: 5s). Then begins the event loop:
   - bump local scene_version
   - write `scene.ztk` (same format as `tk.show()` mode)
   - rewrite `state.txt` with the new scene_version
   - poll for `event_version` bump or timeout (default 50ms)
   - if events arrived, drain events.txt from offset, return next event to caller
4. ZyenLang program processes the event, possibly modifies scene, loops.
5. To close: ZyenLang writes `close=1` and waits for `closed=1` (timeout: 2s) then exits.

## Why version counters rather than file mtimes
mtime resolution on Windows is often 1s; version bumps let us tick faster. They also work over network filesystems where mtime is unreliable.
