# -*- coding: utf-8 -*-
"""
Подбор настроек индексации под КОНКРЕТНУЮ выгрузку: модель эмбеддингов × размер чанка.

  python scripts/tune_embedding.py --source D:\\dumps\\new_cf
  python scripts/tune_embedding.py --source D:\\dumps\\new_cf --models minilm,mpnet --chunk-sizes 800,1500,2800 --budget-hours 6

Берёт выборку модулей, режет их тем же кодом, что prepare, строит запросы из реальных процедур
(имя словами и описание из комментария), эмбеддит выборку каждой моделью и меряет, находится ли
нужный чанк (recall@1/5, MRR; чисто векторный поиск). Замеряет скорость и оценивает время полной индексации.
Печатает рекомендацию для config.yaml; полный отчёт — exports/tune_embedding.json.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

MODELS = {
    "minilm": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",  # ~0.22 ГБ, быстрая
    "mpnet": "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",  # ~1.0 ГБ
    "e5": "intfloat/multilingual-e5-large",  # ~2.2 ГБ, медленная
}
_PROC = re.compile(r"^[ \t]*(?:Процедура|Функция)[ \t]+([A-Za-zА-Яа-яЁё_]\w{9,})\s*\(", re.M)
_CAMEL = re.compile(r"[А-ЯЁA-Z]?[а-яёa-z0-9]+|[А-ЯЁA-Z]+(?![а-яё])")


def words(name: str) -> str:
    return " ".join(_CAMEL.findall(name)).lower()


def describe(text: str, pos: int) -> str:
    """Комментарий-описание непосредственно над процедурой."""
    lines = text[:pos].rstrip().split("\n")
    out: list[str] = []
    for ln in reversed(lines):
        s = ln.strip()
        if s.startswith("&"):
            continue
        if s.startswith("//"):
            out.append(s.lstrip("/ ").strip())
        else:
            break
    d = " ".join(reversed(out))
    return d if len(d) >= 25 else ""


def main() -> int:
    from config import load_config

    cfg = load_config()
    prep = cfg.get("prepare") or {}
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=Path(cfg["paths"]["code_dir"]))
    ap.add_argument("--models", default="minilm,mpnet,e5", help="ключи minilm|mpnet|e5 или полные имена через запятую")
    ap.add_argument("--chunk-sizes", default="800,1500,2800")
    ap.add_argument("--files", type=int, default=250, help="сколько модулей в выборке")
    ap.add_argument("--queries", type=int, default=120)
    ap.add_argument("--budget-hours", type=float, default=8.0, help="допустимое время полной индексации")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=ROOT / "exports" / "tune_embedding.json")
    a = ap.parse_args()

    import numpy as np
    import prepare_config_for_qdrant as P
    from vector.embed import Embedder
    from vector.index import _procs_of

    src = a.source if a.source.is_absolute() else ROOT / a.source
    if not src.is_dir():
        print(f"Нет папки выгрузки: {src}", file=sys.stderr)
        return 1
    rnd = random.Random(a.seed)

    print(f"Источник: {src}", flush=True)
    all_files = [p for p in P.iter_files(src, include_bsl=True, max_file_bytes=2_000_000) if p.suffix.lower() == ".bsl"]
    if len(all_files) < 20:
        print("В выгрузке слишком мало .bsl для замера", file=sys.stderr)
        return 1
    total_bytes = sum(p.stat().st_size for p in all_files)
    sample = rnd.sample(all_files, min(a.files, len(all_files)))
    sample_bytes = sum(p.stat().st_size for p in sample)
    scale = total_bytes / max(1, sample_bytes)
    print(f".bsl всего: {len(all_files)}, в выборке: {len(sample)} (масштаб ×{scale:.1f})", flush=True)

    # запросы строим по файлам — они не зависят от размера чанка
    cands: list[tuple[Path, str, str, str]] = []  # (файл, имя, q_name, q_desc)
    name_count: dict[str, int] = {}
    texts: dict[Path, str] = {}
    for p in sample:
        t = P.clean_code_text(p.read_text(encoding="utf-8", errors="replace"))
        texts[p] = t
        for m in _PROC.finditer(t):
            name_count[m.group(1)] = name_count.get(m.group(1), 0) + 1
    for p, t in texts.items():
        for m in _PROC.finditer(t):
            n = m.group(1)
            if name_count[n] == 1 and len(words(n).split()) >= 2:
                cands.append((p, n, words(n), describe(t, m.start())))
    rnd.shuffle(cands)
    qs: list[tuple[str, str, str]] = []  # (имя процедуры, тип, текст запроса)
    for _p, n, qn, qd in cands:
        qs.append((n, "имя", qn))
        if qd:
            qs.append((n, "описание", qd))
        if len(qs) >= a.queries:
            break
    print(f"Запросов: {len(qs)} (имя процедуры словами / описание из комментария)", flush=True)
    if len(qs) < 20:
        print("Мало подходящих процедур для замера", file=sys.stderr)
        return 1

    models = [MODELS.get(m.strip(), m.strip()) for m in a.models.split(",") if m.strip()]
    sizes = [int(x) for x in a.chunk_sizes.split(",") if x.strip()]
    results = []
    for size in sizes:
        chunks = []
        for p in sample:
            chunks.extend(P.process_file(p, src, size, chunk_overlap=int(prep.get("chunk_overlap") or 150)))
        defs = {}
        for i, c in enumerate(chunks):
            for m in _PROC.finditer(c.text):
                defs.setdefault(m.group(1), []).append(i)
        keep = [(n, t, q) for n, t, q in qs if n in defs]
        full = int(len(chunks) * scale)
        print(f"\n=== чанк {size}: чанков в выборке {len(chunks)}, оценка по всей выгрузке ≈ {full} ===", flush=True)
        for model in models:
            c2 = deepcopy(cfg)
            c2["embedding"] = {**(cfg.get("embedding") or {}), "provider": "fastembed", "model": model,
                               "passage_max_chars": size, "vector_size": None}
            try:
                e = Embedder(c2)
                e.embed_query("прогрев")
                t0 = time.time()
                V = np.array(e.embed_batch([e.prepare_passage(c.text, procs=_procs_of({"text": c.text})) for c in chunks]))
                dt = time.time() - t0
                V /= np.linalg.norm(V, axis=1, keepdims=True)
                Q = np.array([e.embed_query(q) for _n, _t, q in keep])
                Q /= np.linalg.norm(Q, axis=1, keepdims=True)
            except Exception as ex:  # noqa: BLE001
                print(f"  {model}: ПРОПУСК ({type(ex).__name__}: {ex})", flush=True)
                continue
            S = Q @ V.T
            stat = {"имя": [0, 0, 0.0, 0], "описание": [0, 0, 0.0, 0]}
            for k, (n, typ, _q) in enumerate(keep):
                best = min(int((S[k] > S[k][i]).sum()) + 1 for i in defs[n])
                s = stat[typ]
                s[0] += best == 1
                s[1] += best <= 5
                s[2] += 1 / best
                s[3] += 1
            tot = [sum(v[i] for v in stat.values()) for i in range(4)]
            n = max(1, tot[3])
            rate = len(chunks) / dt
            hours = full / rate / 3600
            r = {"model": model, "chunk": size, "recall1": tot[0] / n, "recall5": tot[1] / n, "mrr": tot[2] / n,
                 "mrr_name": stat["имя"][2] / max(1, stat["имя"][3]),
                 "mrr_desc": stat["описание"][2] / max(1, stat["описание"][3]),
                 "chunks_per_sec": rate, "est_hours": hours, "est_chunks": full, "queries": n}
            results.append(r)
            print(f"  {model.split('/')[-1]:<42} recall@5={r['recall5']:.2f} MRR={r['mrr']:.3f} "
                  f"(имя {r['mrr_name']:.2f} / описание {r['mrr_desc']:.2f})  {rate:.1f} чанк/с  ≈{hours:.1f} ч", flush=True)

    if not results:
        return 1
    ok = [r for r in results if r["est_hours"] <= a.budget_hours] or results
    top = max(r["mrr"] for r in ok)
    # самая быстрая комбинация, не уступающая лучшей по качеству больше чем на 5% (разница в пределах шума замера)
    best = min((r for r in ok if r["mrr"] >= 0.95 * top), key=lambda r: (r["est_hours"], -r["mrr"]))
    rec = {"embedding.model": best["model"], "prepare.max_chunk_chars": best["chunk"],
           "embedding.passage_max_chars": min(best["chunk"], 1800)}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"source": str(src), "results": results, "recommended": rec,
                                 "within_budget": best["est_hours"] <= a.budget_hours}, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    print("\n=== РЕКОМЕНДАЦИЯ ===", flush=True)
    note = "" if best["est_hours"] <= a.budget_hours else f"  (!) ни одна комбинация не укладывается в {a.budget_hours} ч"
    print(f"модель: {best['model']}\nmax_chunk_chars: {best['chunk']}\nMRR={best['mrr']:.3f}, ≈{best['est_hours']:.1f} ч на всю выгрузку{note}", flush=True)
    print("Применить: панель → «Запуск и проверки» → «Применить рекомендацию» или внести значения в config.yaml, затем «Пересоздать коллекцию».", flush=True)
    print("@@RECOMMENDED@@" + json.dumps(rec, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    raise SystemExit(main())
