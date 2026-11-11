# -*- coding: utf-8 -*-
"""Быстрые unit-проверки без обязательного live 1C."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_imports() -> None:
    import tools.report_wizard as rw
    import tools.report_builder as rb
    import tools.dialog_context as dc
    import tools.intent_search as ins
    import tools.preflight as pf
    import tools.excel_export as ee
    import tools.period_parse as pp
    import tools.analyst_core as ac
    assert rw.wants_excel_chart_wizard("отчет с графиком")
    assert not rw.wants_step_wizard("отчет с графиком")
    assert rw.wants_step_wizard("мастер excel по шагам")
    assert dc.wants_export_existing("запихни его в отчет")
    assert ac.wants_excel_file("в excel")
    print("OK imports + phrase flags")


def test_rank_blocks_pricelist() -> None:
    from tools.intent_search import rank_items_by_phrase

    items = [
        {"full_name": "Обработка.ПрайсЛист", "score": 200, "synonym": "Прайс"},
        {
            "full_name": "РегистрНакопления.ТоварыНаСкладах",
            "score": 40,
            "synonym": "Товары на складах",
        },
    ]
    r = rank_items_by_phrase(items, "остатки по складу")
    assert r[0]["full_name"].startswith("Регистр"), r
    print("OK rank pricelist blocked")


def test_wizard_confirm_not_object() -> None:
    from tools.report_wizard import apply_answer, new_wizard_state

    st = new_wizard_state("excel")
    r = apply_answer(st, "да делай")
    assert not r.get("ready_to_build")
    assert "да" not in str((r["state"].get("answers") or {}).get("object", "")).lower()
    print("OK wizard confirm not object")


def test_export_snapshot() -> None:
    from tools.dialog_context import export_snapshot_to_excel

    snap = {
        "tool": "compare_periods",
        "full_name": "РегистрНакопления.ТоварыНаСкладах",
        "period_spec": "2016",
        "group_by": "Склад",
        "rows": [
            {"Склад": "А", "Остаток": 10, "Сумма": 100},
            {"Склад": "Б", "Остаток": 5, "Сумма": 50},
        ],
    }
    out = export_snapshot_to_excel(snap, phrase="в excel с диаграммами")
    assert out.get("ok") and out.get("path"), out
    print("OK export snapshot", out.get("path"))


def test_preflight_auto() -> None:
    from tools.preflight import plan_preflight

    pf = plan_preflight("отчет с графиком продажи 2016")
    assert pf and pf["tool"] == "report_builder"
    assert pf["args"].get("skip_wizard") is True
    pf2 = plan_preflight("мастер excel по шагам")
    assert pf2 and pf2["args"].get("skip_wizard") is False
    print("OK preflight auto/step")


def test_period_parse() -> None:
    from tools.period_parse import parse_periods
    from datetime import datetime

    p = parse_periods("2015 и 2016", now=datetime(2020, 1, 1))
    assert not p.get("need_clarification"), p
    slices = p.get("slices") or []
    assert len(slices) >= 2, p
    print("OK period parse multi-year", len(slices))


def test_report_spec() -> None:
    from tools.report_builder import parse_report_request

    s = parse_report_request("продажи по контрагентам за 2016 в excel с графиками")
    assert s.to_excel and s.charts
    assert "контрагент" in s.group_by or s.group_by
    assert "2016" in s.period
    print("OK report spec", s.route, s.group_by, s.period)


def main() -> None:
    test_imports()
    test_rank_blocks_pricelist()
    test_wizard_confirm_not_object()
    test_export_snapshot()
    test_preflight_auto()
    test_period_parse()
    test_report_spec()
    print("ALL UNIT CORE OK")


if __name__ == "__main__":
    main()
