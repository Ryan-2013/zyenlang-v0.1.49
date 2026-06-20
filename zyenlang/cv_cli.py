from __future__ import annotations

import sys
from pathlib import Path


def _load_cv2():
    try:
        import cv2  # type: ignore
        return cv2
    except Exception as exc:  # pragma: no cover - depends on user env
        print("OpenCV is not installed for this Python.", file=sys.stderr)
        print("Install it with: python -m pip install opencv-python", file=sys.stderr)
        print(f"Detail: {exc}", file=sys.stderr)
        return None


def _read_image(cv2, path: str):
    img = cv2.imread(path)
    if img is None:
        print(f"cannot read image: {path}", file=sys.stderr)
        return None
    return img


def _write_image(cv2, path: str, img) -> int:
    out = Path(path)
    if out.parent and str(out.parent) not in {"", "."}:
        out.parent.mkdir(parents=True, exist_ok=True)
    ok = cv2.imwrite(str(out), img)
    if not ok:
        print(f"cannot write image: {path}", file=sys.stderr)
        return 1
    print(str(out))
    return 0


def info(argv: list[str]) -> int:
    cv2 = _load_cv2()
    if cv2 is None:
        return 1
    print(f"OpenCV: {cv2.__version__}")
    cuda_count = 0
    try:
        cuda_count = cv2.cuda.getCudaEnabledDeviceCount()
    except Exception:
        cuda_count = 0
    print(f"OpenCV CUDA devices: {cuda_count}")
    return 0


def readable(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: readable <input>", file=sys.stderr)
        return 2
    cv2 = _load_cv2()
    if cv2 is None:
        return 1
    img = _read_image(cv2, argv[0])
    if img is None:
        return 1
    h, w = img.shape[:2]
    print(f"readable: {argv[0]} ({w}x{h})")
    return 0


def gray(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: gray <input> <output>", file=sys.stderr)
        return 2
    cv2 = _load_cv2()
    if cv2 is None:
        return 1
    img = _read_image(cv2, argv[0])
    if img is None:
        return 1
    out = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return _write_image(cv2, argv[1], out)


def gpu_gray(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: gpu-gray <input> <output>", file=sys.stderr)
        return 2
    cv2 = _load_cv2()
    if cv2 is None:
        return 1
    img = _read_image(cv2, argv[0])
    if img is None:
        return 1
    try:
        if cv2.cuda.getCudaEnabledDeviceCount() > 0:
            gpu = cv2.cuda_GpuMat()
            gpu.upload(img)
            gout = cv2.cuda.cvtColor(gpu, cv2.COLOR_BGR2GRAY)
            out = gout.download()
            return _write_image(cv2, argv[1], out)
    except Exception as exc:
        print(f"OpenCV CUDA path unavailable, falling back to CPU: {exc}", file=sys.stderr)
    out = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return _write_image(cv2, argv[1], out)


def resize(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: resize <input> <output> <width> <height>", file=sys.stderr)
        return 2
    cv2 = _load_cv2()
    if cv2 is None:
        return 1
    img = _read_image(cv2, argv[0])
    if img is None:
        return 1
    try:
        width = int(argv[2])
        height = int(argv[3])
    except ValueError:
        print("width/height must be int", file=sys.stderr)
        return 2
    out = cv2.resize(img, (width, height))
    return _write_image(cv2, argv[1], out)


def blur(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: blur <input> <output> <ksize>", file=sys.stderr)
        return 2
    cv2 = _load_cv2()
    if cv2 is None:
        return 1
    img = _read_image(cv2, argv[0])
    if img is None:
        return 1
    try:
        k = int(argv[2])
    except ValueError:
        print("ksize must be int", file=sys.stderr)
        return 2
    if k < 1:
        k = 1
    if k % 2 == 0:
        k += 1
    out = cv2.GaussianBlur(img, (k, k), 0)
    return _write_image(cv2, argv[1], out)


def canny(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: canny <input> <output> <low> <high>", file=sys.stderr)
        return 2
    cv2 = _load_cv2()
    if cv2 is None:
        return 1
    img = _read_image(cv2, argv[0])
    if img is None:
        return 1
    try:
        low = int(argv[2])
        high = int(argv[3])
    except ValueError:
        print("low/high must be int", file=sys.stderr)
        return 2
    gray_img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    out = cv2.Canny(gray_img, low, high)
    return _write_image(cv2, argv[1], out)


def threshold(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: threshold <input> <output> <thresh>", file=sys.stderr)
        return 2
    cv2 = _load_cv2()
    if cv2 is None:
        return 1
    img = _read_image(cv2, argv[0])
    if img is None:
        return 1
    try:
        t = int(argv[2])
    except ValueError:
        print("thresh must be int", file=sys.stderr)
        return 2
    gray_img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, out = cv2.threshold(gray_img, t, 255, cv2.THRESH_BINARY)
    return _write_image(cv2, argv[1], out)


COMMANDS = {
    "info": info,
    "readable": readable,
    "gray": gray,
    "gpu-gray": gpu_gray,
    "resize": resize,
    "blur": blur,
    "canny": canny,
    "threshold": threshold,
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in COMMANDS:
        print("usage: python -m zyenlang.cv_cli <info|readable|gray|gpu-gray|resize|blur|canny|threshold> ...", file=sys.stderr)
        return 2
    cmd = argv.pop(0)
    return COMMANDS[cmd](argv)


if __name__ == "__main__":
    raise SystemExit(main())
