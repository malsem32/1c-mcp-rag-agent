# -*- coding: utf-8 -*-
"""Smoke: 4 бизнес-блока аналитика."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_detect() -> None:
    from tools.analyst_core import detect_analyst_mode
    from tools.preflight import plan_preflight

    cases = [
        ("анализ ассортимента за 2016", "assortment"),
        ("что продаётся и что нет", "assortment"),
        ("где заморожены деньги", "reserves"),
        ("скрытые резервы", "reserves"),
        ("как заработать больше", "profit"),
        ("маржинальность и рост прибыли 2015 2016", "profit"),
        ("клиентская аналитика 2015 и 2016", "customers"),
        ("VIP клиенты и отток", "customers"),
        ("работа с базой покупателей", "customers"),
    ]
    for q, want in cases:
        got = detect_analyst_mode(q)
        assert got == want, (q, got, want)
        pf = plan_preflight(q)
        assert pf and pf.get("tool") == "analyst_report", (q, pf)
        assert pf["args"].get("mode") == want
        print("OK detect", q, "->", want)


def test_core_units() -> None:
    from tools.analyst_core import (
        build_assortment_view,
        build_customer_rfm,
        build_reserves_view,
    )

    sales = []
    for name, s in [("ТоварA", 1000), ("ТоварB", 100), ("ТоварC", 10)]:
        sales.append({"Номенклатура": name, "Сумма": s, "Количество": 1})
    stock = [
        {"Номенклатура": "ТоварC", "Остаток": 50},
        {"Номенклатура": "Мертвый", "Остаток": 99},
    ]
    a = build_assortment_view(sales, stock_rows=stock, group_key="Номенклатура")
    assert a["top_sellers"][0]["Номенклатура"] == "ТоварA"
    assert any(x.get("Номенклатура") == "Мертвый" for x in a["dead_stock"])
    print("OK assortment", a["count_skus"], "dead", len(a["dead_stock"]))

    rows = []
    for y, data in (
        ("2015", [("Клиент1", 100), ("Клиент2", 50), ("Ушёл", 80)]),
        ("2016", [("Клиент1", 120), ("Клиент2", 20), ("Новый", 40)]),
    ):
        for n, s in data:
            rows.append({"Период": y, "Контрагент": n, "Сумма": s, "Количество": 1})
    c = build_customer_rfm(rows, group_key="Контрагент")
    assert c["lost"] and c["lost"][0]["Контрагент"] == "Ушёл"
    assert c["at_risk"]  # Клиент2 -60%
    assert c["new_customers"]
    print("OK customers vip", len(c["vip"]), "lost", len(c["lost"]), "risk", len(c["at_risk"]))

    r = build_reserves_view(
        stock_total=1000,
        ar_total=500,
        aging={
            "total": 500,
            "summary_by_bucket": [
                {"Корзина": "0–30", "Сумма": 100},
                {"Корзина": "90+", "Сумма": 400},
            ],
        },
        turnover={"days_on_hand": 90, "turns": 4},
    )
    assert r["total_frozen_approx"] and r["advice"]
    print("OK reserves", r["total_frozen_approx"], r["parts"])


def test_live() -> None:
    try:
        from backends import create_backend
        from config import load_config
        from tools.universal import UniversalTools
    except Exception as e:
        print("SKIP live", e)
        return
    cfg = load_config()
    t = UniversalTools(
        create_backend(cfg),
        read_only=True,
        default_limit=25,
        max_limit=80,
        list_objects_cap=80,
        tool_result_max_chars=8000,
        compact_results=True,
        code_dir=cfg.get("code_dir") or "",
    )
    for mode, req in (
        ("assortment", "анализ ассортимента за 2016"),
        ("reserves", "где заморожены деньги 2016"),
        ("profit", "как заработать больше 2015 и 2016"),
        ("customers", "клиентская аналитика 2015 и 2016"),
    ):
        r = t.analyst_report(request=req, mode=mode, to_excel=False, limit=40)
        ok = r.get("ok") or r.get("need_clarification")
        print(
            f"live {mode}: ok={r.get('ok')} block={r.get('business_block')} "
            f"rows={len(r.get('rows') or [])} "
            f"summary={(r.get('analyst_summary') or [])[:2]} "
            f"err={r.get('error') or r.get('need_clarification')}"
        )
        assert ok or r.get("analyst_summary") or r.get("need_clarification"), r


if __name__ == "__main__":
    test_detect()
    test_core_units()
    test_live()
    print("ALL BUSINESS BLOCKS OK")
