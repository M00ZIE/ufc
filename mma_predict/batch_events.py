"""
Processa múltiplas URLs de eventos UFC (uma por linha) e grava JSONL.

Uso (na raiz do projeto):
  python -m mma_predict.batch_events --urls-file eventos.txt --out ufc_batch.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch: análise UFC por lista de URLs.")
    ap.add_argument("--urls-file", type=Path, required=True, help="Arquivo texto, uma URL por linha.")
    ap.add_argument("--out", type=Path, default=Path("ufc_batch.jsonl"), help="Saída JSONL.")
    ap.add_argument("--cache-dir", type=Path, default=None, help="Cache HTML (padrão: .ufc_html_cache na raiz).")
    ap.add_argument("--cache-hours", type=float, default=24.0)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    if not args.urls_file.is_file():
        print(f"Arquivo não encontrado: {args.urls_file}", file=sys.stderr)
        return 1

    root = Path(__file__).resolve().parent.parent
    cache = args.cache_dir or (root / ".ufc_html_cache")

    from ufc_event_analysis import analyze_event_json

    lines = [
        ln.strip()
        for ln in args.urls_file.read_text(encoding="utf-8", errors="replace").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for url in lines:
            data = analyze_event_json(
                url,
                cache_dir=cache,
                cache_hours=args.cache_hours,
                refresh=args.refresh,
            )
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

    print(f"Gravado: {args.out} ({len(lines)} evento(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
