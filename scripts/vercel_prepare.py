"""Copia static/ para public/static/ (Vercel serve assets em public/, nao via Flask static_folder)."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "static"
DST = ROOT / "public" / "static"


def main() -> int:
    if not SRC.is_dir():
        print("static/ nao encontrado", file=sys.stderr)
        return 1
    if DST.exists():
        shutil.rmtree(DST)
    DST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SRC, DST)
    print(f"OK: copiado {SRC} -> {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
