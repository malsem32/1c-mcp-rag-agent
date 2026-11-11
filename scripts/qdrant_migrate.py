# -*- coding: utf-8 -*-
"""
Перенос коллекции между двумя Qdrant (векторы и payload копируются как есть, без пересчёта эмбеддингов).

  python scripts/qdrant_migrate.py --from http://localhost:6333 --to http://localhost:6343 \\
      --collection onec_code_v2 --to-collection onec_code

Нулевые векторы не переносятся (их считает --repair-vectors уже на целевом сервере).
Целевая коллекция создаётся с теми же payload-индексами, что и при индексации.
После копирования выполняется проверка: число точек и доля нулевых векторов.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--to", dest="dst", required=True)
    ap.add_argument("--collection", required=True)
    ap.add_argument("--to-collection", default="")
    ap.add_argument("--recreate", action="store_true", help="пересоздать целевую коллекцию")
    ap.add_argument("--batch", type=int, default=256)
    a = ap.parse_args()

    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qm

    from vector.index import _norm, _retry, ensure_collection

    s = QdrantClient(url=a.src, timeout=120, check_compatibility=False)
    d = QdrantClient(url=a.dst, timeout=120, check_compatibility=False)
    tgt = a.to_collection or a.collection
    info = s.get_collection(a.collection)
    dim = info.config.params.vectors.size
    total = info.points_count
    ensure_collection(d, tgt, dim, recreate=a.recreate)
    print(f"{a.src}/{a.collection} ({total}) → {a.dst}/{tgt}, dim={dim}", flush=True)

    off, copied, skipped, t0 = None, 0, 0, time.time()
    while True:
        pts, off = _retry(lambda: s.scroll(collection_name=a.collection, limit=a.batch, offset=off,
                                           with_vectors=True, with_payload=True), log=print)
        good = [p for p in pts if _norm(p.vector) > 1e-6]
        skipped += len(pts) - len(good)
        if good:
            _retry(lambda: d.upsert(collection_name=tgt, points=[
                qm.PointStruct(id=p.id, vector=p.vector, payload=p.payload) for p in good], wait=True), log=print)
            copied += len(good)
        if copied and copied % (a.batch * 20) < a.batch:
            print(f"  … {copied}/{total}  ({copied / (time.time() - t0):.0f} т/с)", flush=True)
        if off is None:
            break

    z = n = 0
    off = None
    while True:
        pts, off = d.scroll(collection_name=tgt, limit=2000, offset=off, with_vectors=True, with_payload=False)
        for p in pts:
            n += 1
            z += _norm(p.vector) <= 1e-6
        if off is None:
            break
    print(f"Готово: скопировано {copied}, нулевых пропущено {skipped}; в целевой {n} точек, нулевых {z}")
    return 0 if z == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
