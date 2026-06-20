# ZyenLang v0.1.48 std/thread, std/coroutine, std/gpu

## std/thread

`std/thread` is a minimal low-level helper module. It is intentionally conservative.

```zy
import <std/thread>;

thread.cpu_count();
thread.sleep_ms(10);
thread.yield_now();
thread.run_cmd("echo hello");
thread.spawn_cmd("echo background");
```

Notes:

- `sleep_ms()` is implemented in the C runtime.
- `spawn_cmd()` launches a detached OS process command.
- v0.1.48 does **not** expose joinable ZyenLang threads yet.

## std/coroutine

`std/coroutine` is cooperative. It does not create OS threads.

```zy
import <std/coroutine>;

let c: Coro = coroutine.new();
print(c.next());
print(c.next());
c.finish();
print(c.is_done());
```

Use `Coro` as a small state-machine state holder.

## std/gpu compute

`std/gpu` now includes CSV vector compute helpers:

```zy
import <std/gpu>;

gpu.vector_add_csv("a.csv", "b.csv", "out.csv");
gpu.vector_scale_csv("a.csv", 2.0, "out.csv");
gpu.dot_csv("a.csv", "b.csv", "dot.txt");
```

The backend calls `python -m zyenlang.gpu_cli`. If PyTorch CUDA is available, it uses CUDA tensors. Otherwise it falls back to Python CPU so tests can run anywhere.
