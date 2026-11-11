# -*- coding: utf-8 -*-
"""Индексация процедур в коллекцию onec_procs (1 точка = 1 процедура).

  python scripts/index_procs.py --recreate     # с нуля
  python scripts/index_procs.py                # докатка (пропускает неизменённые)
  python scripts/index_procs.py --limit 500    # проба
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import load_config  # noqa: E402
from vector.procs import index_procs  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recreate", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--chunks", type=Path, help="JSONL чанков (по умолчанию qdrant.chunks_jsonl)")
    ap.add_argument("--collection", help="коллекция процедур (по умолчанию qdrant.collection_procs)")
    a = ap.parse_args()
    cfg = load_config()
    if a.collection:
        cfg = {**cfg, "qdrant": {**(cfg.get("qdrant") or {}), "collection_procs": a.collection}}
    src = a.chunks or ROOT / (cfg.get("qdrant", {}).get("chunks_jsonl") or "knowledge/qdrant_ready/config_chunks.jsonl")
    r = index_procs(cfg, src, recreate=a.recreate, limit=a.limit, log=lambda m: print(m, flush=True))
    print(r)
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
