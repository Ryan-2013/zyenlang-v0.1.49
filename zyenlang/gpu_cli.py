from __future__ import annotations

import csv
import math
import shutil
import subprocess
import sys
from pathlib import Path


def has_nvidia(argv: list[str]) -> int:
    exe = shutil.which("nvidia-smi")
    if not exe:
        print("nvidia-smi not found")
        return 1
    try:
        result = subprocess.run([exe], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
    except Exception as exc:
        print(f"nvidia-smi failed: {exc}")
        return 1
    if result.returncode == 0:
        first = result.stdout.splitlines()[0] if result.stdout.splitlines() else "nvidia-smi ok"
        print(first)
    else:
        print(result.stderr.strip() or "nvidia-smi returned error")
    return result.returncode


def has_torch_cuda(argv: list[str]) -> int:
    try:
        import torch  # type: ignore
    except Exception as exc:
        print(f"torch not installed: {exc}")
        return 1
    ok = bool(torch.cuda.is_available())
    print(f"torch CUDA: {ok}")
    if ok:
        try:
            print(f"device: {torch.cuda.get_device_name(0)}")
        except Exception:
            pass
    return 0 if ok else 1


def has_opencv_cuda(argv: list[str]) -> int:
    try:
        import cv2  # type: ignore
    except Exception as exc:
        print(f"opencv not installed: {exc}")
        return 1
    try:
        count = cv2.cuda.getCudaEnabledDeviceCount()
    except Exception:
        count = 0
    print(f"OpenCV CUDA devices: {count}")
    return 0 if count > 0 else 1


def info(argv: list[str]) -> int:
    print("ZyenLang GPU info")
    n = has_nvidia([])
    t = has_torch_cuda([])
    c = has_opencv_cuda([])
    return 0 if (n == 0 or t == 0 or c == 0) else 1


def _read_vector(path: str) -> list[float]:
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        return []
    # Accept comma-separated, newline-separated, or mixed numeric values.
    values: list[float] = []
    for row in csv.reader(text.splitlines()):
        for cell in row:
            cell = cell.strip()
            if cell:
                values.append(float(cell))
    return values


def _write_vector(path: str, values) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True) if Path(path).parent != Path('.') else None
    with Path(path).open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([float(v) for v in values])


def _write_scalar(path: str, value: float) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True) if Path(path).parent != Path('.') else None
    Path(path).write_text(f"{value:.17g}\n", encoding="utf-8")


def _torch_backend():
    try:
        import torch  # type: ignore
    except Exception:
        return None, "cpu-python"
    if not torch.cuda.is_available():
        return None, "cpu-python"
    return torch, "torch-cuda"


def vector_add_csv(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: vector-add-csv <a.csv> <b.csv> <out.csv>", file=sys.stderr)
        return 2
    a = _read_vector(argv[0])
    b = _read_vector(argv[1])
    if len(a) != len(b):
        print(f"vector length mismatch: {len(a)} vs {len(b)}", file=sys.stderr)
        return 1
    torch, backend = _torch_backend()
    if torch is not None:
        ta = torch.tensor(a, dtype=torch.float64, device="cuda")
        tb = torch.tensor(b, dtype=torch.float64, device="cuda")
        out = (ta + tb).cpu().tolist()
    else:
        out = [x + y for x, y in zip(a, b)]
    _write_vector(argv[2], out)
    print(f"vector-add-csv ok backend={backend} n={len(out)}")
    return 0


def vector_scale_csv(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: vector-scale-csv <input.csv> <scale> <out.csv>", file=sys.stderr)
        return 2
    a = _read_vector(argv[0])
    scale = float(argv[1])
    torch, backend = _torch_backend()
    if torch is not None:
        ta = torch.tensor(a, dtype=torch.float64, device="cuda")
        out = (ta * scale).cpu().tolist()
    else:
        out = [x * scale for x in a]
    _write_vector(argv[2], out)
    print(f"vector-scale-csv ok backend={backend} n={len(out)}")
    return 0


def dot_csv(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: dot-csv <a.csv> <b.csv> <out.txt>", file=sys.stderr)
        return 2
    a = _read_vector(argv[0])
    b = _read_vector(argv[1])
    if len(a) != len(b):
        print(f"vector length mismatch: {len(a)} vs {len(b)}", file=sys.stderr)
        return 1
    torch, backend = _torch_backend()
    if torch is not None:
        ta = torch.tensor(a, dtype=torch.float64, device="cuda")
        tb = torch.tensor(b, dtype=torch.float64, device="cuda")
        value = float(torch.dot(ta, tb).cpu().item())
    else:
        value = sum(x * y for x, y in zip(a, b))
    _write_scalar(argv[2], value)
    print(f"dot-csv ok backend={backend} n={len(a)} value={value:.17g}")
    return 0


COMMANDS = {
    "info": info,
    "has-nvidia": has_nvidia,
    "has-torch-cuda": has_torch_cuda,
    "has-opencv-cuda": has_opencv_cuda,
    "vector-add-csv": vector_add_csv,
    "vector-scale-csv": vector_scale_csv,
    "dot-csv": dot_csv,
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in COMMANDS:
        print("usage: python -m zyenlang.gpu_cli <info|has-nvidia|has-torch-cuda|has-opencv-cuda|vector-add-csv|vector-scale-csv|dot-csv>", file=sys.stderr)
        return 2
    cmd = argv.pop(0)
    return COMMANDS[cmd](argv)


if __name__ == "__main__":
    raise SystemExit(main())
