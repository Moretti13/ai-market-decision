from __future__ import annotations

import compileall
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    ok = compileall.compile_dir(str(ROOT), quiet=1)
    print(f"python compile: {'OK' if ok else 'FAIL'}")
    print(f"project root: {ROOT}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
