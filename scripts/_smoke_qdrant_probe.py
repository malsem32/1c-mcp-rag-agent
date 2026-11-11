# -*- coding: utf-8 -*-
"""Быстрый зонд Qdrant (не для продакшена)."""
from __future__ import annotations

import time

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.code_search import code_status, find_modules, search_code
from tools.object_movements import analyze_object_movements
from tools.route_intent import route_user_message


def main() -> None:
    st = code_status()
    print("STATUS", st.get("source"), st.get("ready"), st.get("qdrant_points"), "fs", st.get("filesystem_runtime"))

    print("\n--- search_code ---")
    for q in (
        "ТекстЗапроса",
        "ОбработкаПроведения",
        "Движения.Добавить",
        "ПриСозданииНаСервере",
        "себестоимость",
        "ЗаполнитьЗначенияСвойств",
    ):
        t0 = time.time()
        r = search_code(q, max_matches=3)
        print(f"{time.time()-t0:5.1f}s src={r.get('source')} n={r.get('count')} | {q}")
        for m in (r.get("matches") or [])[:2]:
            print("      ", round(float(m.get("score") or 0), 3), (m.get("path") or "")[:72])

    print("\n--- find_modules ---")
    for q in (
        "ObjectModule документ",
        "общий модуль",
        "форма документа",
    ):
        t0 = time.time()
        r = find_modules(q, limit=3)
        print(f"{time.time()-t0:5.1f}s n={r.get('count')} src={r.get('source')} | {q}")
        for m in (r.get("modules") or [])[:2]:
            print("      ", m.get("object_name"), (m.get("path") or "")[:72])

    print("\n--- route ---")
    for p in (
        "почему не заполнился реквизит в документе",
        "проследи цепочку от ОбработкаПроведения",
        "по каким регистрам проводится документ",
        "продажи за 2015",
    ):
        r = route_user_message(p)
        print(p[:48], "->", r.get("route"), r.get("prefer_tool"))

    print("\n--- movements (from search path) ---")
    r = search_code("Процедура ОбработкаПроведения", max_matches=5)
    obj = ""
    for m in r.get("matches") or []:
        path = (m.get("path") or "").replace("\\", "/")
        if "Documents/" in path and "ObjectModule" in path:
            parts = path.split("/")
            if len(parts) > 1:
                obj = parts[1]
                break
    if obj:
        t0 = time.time()
        a = analyze_object_movements(
            request=f"регистры проведения {obj}",
            full_name=f"Документ.{obj}",
        )
        print(f"obj={obj} {time.time()-t0:.1f}s", a.get("conclusion"), "regs", len(a.get("registers") or []))
        print((a.get("summary") or "")[:220])
    else:
        print("no ObjectModule path from search")


if __name__ == "__main__":
    main()
