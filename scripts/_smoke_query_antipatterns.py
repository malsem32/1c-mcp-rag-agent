# -*- coding: utf-8 -*-
"""Smoke: антипаттерны запросов."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kb.store import get_query_antipatterns, reload_knowledge
from tools.preflight import plan_preflight
from tools.query_antipatterns import (
    is_query_audit_question,
    list_antipatterns_catalog,
    scan_bsl_module,
    scan_query_text,
)


def main() -> int:
    reload_knowledge()
    book = get_query_antipatterns()
    n = len(book.get("patterns") or [])
    assert n >= 10, f"expected patterns, got {n}"
    print("OK patterns", n)

    assert is_query_audit_question("проверь запросы на антипаттерны")
    assert not is_query_audit_question("продажи за 2015")
    print("OK intent")

    bad = (
        'ВЫБРАТЬ * ИЗ Документ.Тест КАК Т '
        'ГДЕ ПРЕДСТАВЛЕНИЕ(Т.Контрагент) = "X"'
    )
    ids = {x["id"] for x in scan_query_text(bad)}
    assert "select_star" in ids, ids
    assert "presentation_in_where" in ids, ids
    print("OK scan_query", sorted(ids))

    bsl = (
        "Для Каждого Стр Из Список Цикл\n"
        "    Запрос = Новый Запрос;\n"
        '    Запрос.Текст = "ВЫБРАТЬ * ИЗ Справочник.Номенклатура КАК Н";\n'
        "    Запрос.Выполнить();\n"
        "КонецЦикла;\n"
    )
    bids = {x["id"] for x in scan_bsl_module(bsl, path="Test/Module.bsl")}
    assert "query_in_loop" in bids, bids
    assert "select_star" in bids, bids
    print("OK scan_bsl", sorted(bids))

    pf = plan_preflight("аудит запросов модуля проведения")
    tool = (pf or {}).get("tool") or (pf or {}).get("plan", [{}])[0].get("tool")
    # plan_preflight shape may vary
    raw = str(pf)
    assert "audit_query_code" in raw, pf
    print("OK preflight", pf.get("tool") if isinstance(pf, dict) else pf)

    cat = list_antipatterns_catalog()
    assert cat.get("count", 0) >= 10
    print("OK catalog", cat["count"])
    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
