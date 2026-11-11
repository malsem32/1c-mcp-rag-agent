# -*- coding: utf-8 -*-
"""Сравнение поиска по коллекции процедур (onec_procs) с onec_code на тех же «человеческих» вопросах.

  python scripts/_procs_eval.py [--k 10]
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _stress_vector_eval import Q  # noqa: E402


def _hit(blob: str, subs: list[str]) -> bool:
    b = blob.replace("\\", "/").lower()
    return any(s.lower() in b for s in subs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=10)
    a = ap.parse_args()
    from config import load_config
    from vector.procs import search_procs
    from vector.search import search_code_vectors

    cfg = load_config()
    qs = [q for q in Q if q[2] in ("nl", "id")]
    agg = {"procs": [0, 0, 0.0, 0], "code": [0, 0, 0.0, 0]}
    for q, subs, typ in qs:
        row = []
        for name in ("procs", "code"):
            t = time.time()
            if name == "procs":
                hits = [" ".join(str(h.get(k) or "") for k in ("source_path", "object_name", "proc_name")) for h in search_procs(q, limit=a.k, cfg=cfg)]
            else:
                r = search_code_vectors(q, limit=a.k, match_mode="hybrid", cfg=cfg)
                hits = [" ".join(str(m.get(k) or "") for k in ("path", "object_name")) + " " + " ".join(map(str, m.get("proc_names") or [])) for m in r.get("matches") or []]
            ms = int((time.time() - t) * 1000)
            rank = next((i + 1 for i, h in enumerate(hits) if _hit(h, subs)), 0)
            g = agg[name]
            g[0] += rank == 1
            g[1] += bool(rank)
            g[2] += 1 / rank if rank else 0
            g[3] += ms
            row.append(f"{name}={rank}/{ms}ms")
        print(q[:58].ljust(60), *row, flush=True)
    n = len(qs)
    print(f"\nn={n} k={a.k}")
    for name, g in agg.items():
        print(f"{name:<6} hit@1={g[0]/n:.2f} hit@{a.k}={g[1]/n:.2f} MRR={g[2]/n:.3f} avg={g[3]//n}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
