# -*- coding: utf-8 -*-
"""
Оценка качества поиска по ЖИВОЙ коллекции Qdrant (боевой путь search_code_vectors, весь корпус).

  python scripts/eval_search.py                    # config.yaml, ~40 вопросов каждого типа
  python scripts/eval_search.py --n 100 --modes hybrid,semantic,exact
  python scripts/eval_search.py --source D:\\dumps\\other_cf --collection onec_code_other

Вопросы строятся из самой выгрузки (не из индекса) и не зависят от конфигурации:
  имя        — имя процедуры словами («получить курс валюты на дату»)
  описание   — комментарий над процедурой
  литерал    — уникальная строка из ТЕЛА процедуры (проверяет, что поиск видит не только начало чанка)
  структура  — синоним реквизита/ТЧ → искомый объект (карточки структуры из XML)
Попадание строгое: тот же файл И та же процедура (для «структуры» — тот же объект).
Результат: таблица + exports/eval_search.json (с списком промахов для разбора).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_PROC = re.compile(r"^[ \t]*(?:Процедура|Функция)[ \t]+([A-Za-zА-Яа-яЁё_]\w+)\s*\([^\n]*\n", re.M)
_END = re.compile(r"^[ \t]*(?:КонецПроцедуры|КонецФункции)", re.M)
_LIT = re.compile(r'"([^"\n|]{22,90})"')
_CAMEL = re.compile(r"[А-ЯЁA-Z]?[а-яёa-z0-9]+|[А-ЯЁA-Z]+(?![а-яё])")
_CYR = re.compile(r"[А-Яа-яЁё]{4,}")


def words(n: str) -> str:
    return " ".join(_CAMEL.findall(n)).lower()


def description(text: str, pos: int) -> str:
    out: list[str] = []
    for ln in reversed(text[:pos].rstrip().split("\n")):
        s = ln.strip()
        if s.startswith("&"):
            continue
        if s.startswith("//"):
            out.append(s.lstrip("/ ").strip())
        else:
            break
    d = " ".join(reversed(out))
    return d if len(d) >= 25 else ""


def collect_procs(src: Path):
    """Все процедуры дампа: (rel_path, имя, описание, тело). Плюс счётчики уникальности имён и литералов."""
    procs: list[tuple[str, str, str, str]] = []
    name_n: Counter = Counter()
    lit_n: Counter = Counter()
    for dp, _d, fs in os.walk(src):
        for f in fs:
            if not f.lower().endswith(".bsl"):
                continue
            p = Path(dp) / f
            try:
                t = p.read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n")
            except OSError:
                continue
            rel = p.relative_to(src).as_posix()
            for m in _PROC.finditer(t):
                e = _END.search(t, m.end())
                body = t[m.end(): e.start() if e else m.end() + 3000]
                procs.append((rel, m.group(1), description(t, m.start()), body))
                name_n[m.group(1)] += 1
                for lit in set(_LIT.findall(body)):
                    lit_n[lit] += 1
    return procs, name_n, lit_n


def collect_structure(src: Path, rnd: random.Random, n: int, only: set[str] | None = None, accept=lambda _o: True):
    """(имя объекта, запрос) по синониму реквизита/ТЧ, уникальному в выгрузке."""
    import xml.etree.ElementTree as ET
    from ingest.xml_cards import _child, _ln, _synonym, _text

    facts: list[tuple[str, str, str]] = []
    syn_n: Counter = Counter()
    for cls in ("Documents", "Catalogs", "InformationRegisters", "AccumulationRegisters", "Reports", "DataProcessors"):
        d = src / cls
        if not d.is_dir() or (only and cls not in only):
            continue
        for f in d.glob("*.xml"):
            if f.stat().st_size > 400_000:
                continue
            try:
                root = ET.parse(f).getroot()
            except Exception:  # noqa: BLE001
                continue
            obj = next((c for c in root if _ln(c.tag) != "InternalInfo"), None)
            for c in (_child(obj, "ChildObjects") or []):
                t = _ln(c.tag)
                if t in ("Attribute", "Dimension", "Resource", "TabularSection"):
                    s = _synonym(_child(c, "Properties"))
                    if len(s) >= 9:
                        facts.append((f.stem, "табличная часть " if t == "TabularSection" else "реквизит ", s.lower()))
                        syn_n[s.lower()] += 1
    uniq = [(o, pre + s) for o, pre, s in facts if syn_n[s] == 1 and accept(o)]
    rnd.shuffle(uniq)
    return uniq[:n]


def collect_ui(src: Path, rnd: random.Random, n: int, only: set[str] | None = None, accept=lambda _p: True):
    """Вопросы по формам (команда → форма) и макетам (параметр → макет); возвращает (форма[], макет[])."""
    import xml.etree.ElementTree as ET
    from ingest.ui_cards import _title
    from ingest.xml_cards import _child, _ln

    cmds: list[tuple[str, str]] = []
    params: list[tuple[str, str]] = []
    pn: Counter = Counter()
    tn: Counter = Counter()
    for dp, _d, fs in os.walk(src):
        rel_dir = Path(dp).relative_to(src).as_posix()
        if only and rel_dir.split("/")[0] not in only:
            continue
        for f in fs:
            p = Path(dp) / f
            if f == "Form.xml" and p.stat().st_size < 1_500_000:
                try:
                    root = ET.parse(p).getroot()
                except Exception:  # noqa: BLE001
                    continue
                for c in (_child(root, "Commands") or []):
                    t = _title(c)
                    if _ln(c.tag) == "Command" and len(t) >= 12:
                        cmds.append((p.relative_to(src).as_posix(), t.lower()))
                        tn[t.lower()] += 1
            elif f == "Template.xml" and p.stat().st_size < 3_000_000:
                try:
                    root = ET.parse(p).getroot()
                except Exception:  # noqa: BLE001
                    continue
                if _ln(root.tag) != "document":
                    continue
                for el in root.iter():
                    if _ln(el.tag) == "parameter" and len((el.text or "").strip()) >= 14:
                        params.append((p.relative_to(src).as_posix(), el.text.strip()))
                        pn[el.text.strip()] += 1
    cq = [(path, "команда формы " + t) for path, t in cmds if tn[t] == 1 and accept(path)]
    pq = [(path, "параметр печатной формы " + words(t)) for path, t in params if pn[t] == 1 and accept(path)]
    rnd.shuffle(cq)
    rnd.shuffle(pq)
    return cq[:n], pq[:n]


def _unit(path: str | None) -> str:
    """Forms/<Имя> или Templates/<Имя> (до Ext) — единица «форма/макет» независимо от типа файла."""
    parts = (path or "").split("/")
    for marker in ("Forms", "Templates"):
        if marker in parts:
            i = parts.index(marker)
            return "/".join(parts[: i + 2])
    return "/".join(parts[:3]) if parts and parts[0] in ("CommonForms", "CommonTemplates") else (path or "")


def main() -> int:
    from config import load_config

    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=Path(cfg["paths"]["code_dir"]))
    ap.add_argument("--collection", default="", help="имя коллекции кода (по умолчанию из config.yaml)")
    ap.add_argument("--n", type=int, default=40, help="вопросов каждого типа")
    ap.add_argument("--modes", default="hybrid,semantic,exact")
    ap.add_argument("--classes", default="", help="только эти корневые папки выгрузки, напр. CommonModules,Documents")
    ap.add_argument("--indexed-only", action="store_true", help="вопросы только по тому, что есть в коллекции (для частичной индексации, --limit)")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--out", type=Path, default=ROOT / "exports" / "eval_search.json")
    a = ap.parse_args()
    if a.collection:
        cfg.setdefault("qdrant", {})["collection_code"] = a.collection
    src = a.source if a.source.is_absolute() else ROOT / a.source
    from vector.client import collection_name, get_qdrant
    from vector.search import search_code_vectors

    col = collection_name("code", cfg)
    pts = get_qdrant(cfg).get_collection(col).points_count
    print(f"Коллекция {col}: {pts} точек. Выгрузка: {src}", flush=True)
    rnd = random.Random(a.seed)

    ok_path = ok_obj = lambda _x: True  # noqa: E731
    if a.indexed_only:
        paths: set[str] = set()
        objs: set[str] = set()
        off = None
        while True:
            pts_, off = get_qdrant(cfg).scroll(collection_name=col, limit=2000, offset=off,
                                               with_payload=["source_path", "object_name"], with_vectors=False)
            for p_ in pts_:
                paths.add(p_.payload.get("source_path") or "")
                objs.add(p_.payload.get("object_name") or "")
            if off is None:
                break
        units = {_unit(x) for x in paths}
        ok_path = lambda x: x in paths or _unit(x) in units  # noqa: E731
        ok_obj = lambda x: x in objs  # noqa: E731
        print(f"Только проиндексированное: файлов {len(paths)}, объектов {len(objs)}", flush=True)

    print("Читаю выгрузку, строю вопросы…", flush=True)
    procs, name_n, lit_n = collect_procs(src)
    classes = lambda rel: rel.split("/")[0]  # noqa: E731
    only = {c.strip() for c in a.classes.split(",") if c.strip()}
    pool = [p for p in procs if name_n[p[1]] == 1 and (not only or classes(p[0]) in only) and ok_path(p[0])]
    rnd.shuffle(pool)

    def pick(pred, n):
        out, per_cls = [], Counter()
        for p in pool:
            c = classes(p[0])
            if pred(p) and per_cls[c] < max(3, n // 5):  # разнообразие по классам метаданных
                out.append(p)
                per_cls[c] += 1
            if len(out) >= n:
                break
        return out

    qs: list[dict] = []
    for rel, name, desc, body in pick(lambda p: len(words(p[1]).split()) >= 3 and len(p[1]) >= 12, a.n):
        qs.append({"type": "имя", "q": words(name), "path": rel, "proc": name})
    for rel, name, desc, body in pick(lambda p: bool(p[2]) and name_n[p[1]] == 1, a.n):
        qs.append({"type": "описание", "q": desc[:200], "path": rel, "proc": name})
    lits = [(rel, name, lit) for rel, name, d, body in pool for lit in _LIT.findall(body)
            if lit_n[lit] == 1 and len(_CYR.findall(lit)) >= 3]
    rnd.shuffle(lits)
    seen = Counter()
    for rel, name, lit in lits:
        if seen[classes(rel)] < max(3, a.n // 5):
            qs.append({"type": "литерал", "q": lit, "path": rel, "proc": name})
            seen[classes(rel)] += 1
        if sum(1 for q in qs if q["type"] == "литерал") >= a.n:
            break
    for obj, q in collect_structure(src, rnd, a.n, only, ok_obj):
        qs.append({"type": "структура", "q": q, "object": obj})
    fq, pq = collect_ui(src, rnd, a.n, only, ok_path)
    qs += [{"type": "форма", "q": q, "path": path, "proc": ""} for path, q in fq]
    qs += [{"type": "макет", "q": q, "path": path, "proc": ""} for path, q in pq]
    cnt = Counter(q["type"] for q in qs)
    print(f"Вопросов: {dict(cnt)} (процедур в выгрузке {len(procs)}, уникальных имён {sum(1 for v in name_n.values() if v == 1)})", flush=True)

    def hit(q: dict, m: dict) -> bool:
        if q["type"] == "структура":
            return m.get("object_name") == q["object"]
        if q["type"] in ("форма", "макет"):
            return _unit(m.get("path")) == _unit(q["path"])  # форма/макет целиком: карточка или её модуль
        return m.get("path") == q["path"] and q["proc"] in (m.get("proc_names") or [])

    modes = [m.strip() for m in a.modes.split(",") if m.strip()]
    agg: dict = defaultdict(lambda: {"n": 0, "h1": 0, "h5": 0, "h10": 0, "mrr": 0.0, "t": 0.0, "f10": 0})
    cls_agg: dict = defaultdict(lambda: {"n": 0, "h10": 0})
    misses: list[dict] = []
    for i, q in enumerate(qs):
        for mode in modes:
            t0 = time.time()
            try:
                r = search_code_vectors(q["q"], limit=a.limit, match_mode=mode, cfg=cfg)
            except Exception as ex:  # noqa: BLE001
                r = {"matches": [], "error": str(ex)}
            dt = time.time() - t0
            rank = next((k + 1 for k, m in enumerate(r.get("matches") or []) if hit(q, m)), 0)
            rank_f = next((k + 1 for k, m in enumerate(r.get("matches") or [])
                           if (m.get("object_name") == q["object"] if q["type"] == "структура" else m.get("path") == q["path"])), 0)
            s = agg[(q["type"], mode)]
            s["n"] += 1; s["t"] += dt
            s["h1"] += rank == 1; s["h5"] += 0 < rank <= 5; s["h10"] += 0 < rank <= 10
            s["mrr"] += 1 / rank if rank else 0.0
            s["f10"] += 0 < rank_f <= 10
            if mode == "hybrid" and q["type"] != "структура":
                c = cls_agg[(q.get("path") or "").split("/")[0]]
                c["n"] += 1; c["h10"] += 0 < rank <= 10
            if not rank and mode == modes[0] and sum(1 for m_ in misses if m_["type"] == q["type"]) < 4:
                top = (r.get("matches") or [{}])[0]
                misses.append({"type": q["type"], "q": q["q"][:120], "ожидалось": q.get("path") or q.get("object"),
                               "процедура": q.get("proc"), "первый результат": top.get("path") or top.get("location")})
        if (i + 1) % 40 == 0:
            print(f"  … {i + 1}/{len(qs)}", flush=True)

    print(f"\n{'тип':<10}{'режим':<10}{'hit@1':>7}{'hit@5':>7}{'hit@10':>8}{'файл@10':>9}{'MRR':>7}{'сек/запрос':>12}")
    rows = []
    for (typ, mode), s in sorted(agg.items(), key=lambda kv: (kv[0][0], modes.index(kv[0][1]))):
        n = max(1, s["n"])
        row = {"type": typ, "mode": mode, "n": s["n"], "hit1": s["h1"] / n, "hit5": s["h5"] / n, "hit10": s["h10"] / n, "file10": s["f10"] / n,
               "mrr": s["mrr"] / n, "sec": s["t"] / n}
        rows.append(row)
        print(f"{typ:<10}{mode:<10}{row['hit1']:>7.2f}{row['hit5']:>7.2f}{row['hit10']:>8.2f}{row['file10']:>9.2f}{row['mrr']:>7.2f}{row['sec']:>12.2f}")
    print("\nhybrid hit@10 по классам метаданных (код):")
    for c, v in sorted(cls_agg.items(), key=lambda kv: -kv[1]["n"])[:8]:
        print(f"  {c:<28}{v['h10'] / max(1, v['n']):.2f}  (n={v['n']})")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"collection": col, "points": pts, "source": str(src), "rows": rows,
                                 "by_class_hybrid_hit10": {c: v["h10"] / max(1, v["n"]) for c, v in cls_agg.items()},
                                 "misses": misses}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nПолный отчёт и промахи: {a.out}")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    raise SystemExit(main())
