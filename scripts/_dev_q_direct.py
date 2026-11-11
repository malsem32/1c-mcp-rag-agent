# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backends import create_backend
from config import load_config
from tools.universal import UniversalTools
from vector.search import search_code_vectors


def main() -> int:
    cfg = load_config()
    t = UniversalTools(
        create_backend(cfg),
        read_only=True,
        default_limit=25,
        max_limit=80,
        list_objects_cap=80,
        tool_result_max_chars=6000,
        compact_results=True,
        code_dir=cfg.get("code_dir") or "",
    )

    print("=== stock balances ТоварыНаСкладах ===")
    st = t.get_object_structure("РегистрНакопления.ТоварыНаСкладах")
    print("dims", st.get("dimension_names"))
    print("res", st.get("resource_names"))
    print("vt", st.get("virtual_tables"))
    r = t.linked_query(
        request="остатки товаров на складах",
        sources="РегистрНакопления.ТоварыНаСкладах",
        execute=True,
        limit=5,
    )
    print("q", (r.get("query") or "")[:280])
    print("rows", r.get("row_count"), "err", (r.get("execute_result") or {}).get("error"))
    rows = (r.get("execute_result") or {}).get("rows") or []
    if rows:
        print("sample", rows[0])

    print("=== counterparties INN ===")
    st = t.get_object_structure("Справочник.Контрагенты")
    for f in st.get("fields") or []:
        n = (f.get("name") or "") + " " + (f.get("synonym") or "")
        if any(x in n.lower() for x in ("инн", "унп", "tax", "нн")):
            print(" field", f.get("name"), f.get("type"), f.get("synonym"))
    q = (
        "ВЫБРАТЬ ПЕРВЫЕ 20 Т.ИНН КАК ИНН, КОЛИЧЕСТВО(Т.Ссылка) КАК Кнт "
        "ИЗ Справочник.Контрагенты КАК Т "
        'ГДЕ Т.ИНН <> "" '
        "СГРУППИРОВАТЬ ПО Т.ИНН "
        "ИМЕЮЩИЕ КОЛИЧЕСТВО(Т.Ссылка) > 1"
    )
    er = t.execute_query(q, 20)
    print("inn err", er.get("error"), "n", len(er.get("rows") or []))
    if er.get("rows"):
        print(er["rows"][:3])

    print("=== debt РасчетыСКлиентами ===")
    r = t.linked_query(
        request="долги на текущую дату",
        sources="РегистрНакопления.РасчетыСКлиентами",
        execute=True,
        limit=5,
    )
    print("q", (r.get("query") or "")[:260])
    print("rows", r.get("row_count"), "err", (r.get("execute_result") or {}).get("error"))

    print("=== negative stock / control ===")
    for q in (
        "КонтрольОстатков",
        "ОтрицательныеОстатки",
        "ПроверитьНаличие",
        "ВыполнитьКонтроль",
    ):
        sc = search_code_vectors(q, limit=5, match_mode="hybrid")
        print(q)
        for m in (sc.get("matches") or [])[:3]:
            print(" ", (m.get("path") or "")[:80])

    print("=== ПолучитьПараметрыДоговора ===")
    sc = search_code_vectors("ПолучитьПараметрыДоговора", limit=8, match_mode="exact")
    for m in sc.get("matches") or []:
        print(" ", m.get("path"), m.get("score"))

    print("=== FIFO / партион ===")
    for q in ("УправлениеЗапасамиПартионныйУчет", "ФИФО", "СписатьПартии"):
        sc = search_code_vectors(q, limit=3, match_mode="hybrid")
        print(q, len(sc.get("matches") or []), [(m.get("path") or "")[:60] for m in (sc.get("matches") or [])[:2]])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
