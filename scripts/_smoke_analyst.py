# -*- coding: utf-8 -*-
"""Smoke: analyst core + excel charts + live analyst_report."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_core() -> None:
    from tools.analyst_core import (
        analyst_bullets,
        build_abc,
        build_anomalies,
        build_variance_contribution,
        detect_analyst_mode,
    )

    rows = []
    for y, mult in (("2015", 1.0), ("2016", 1.5)):
        for i, name in enumerate(
            ["КонтрагентА", "КонтрагентБ", "КонтрагентВ", "КонтрагентГ", "КонтрагентД"]
        ):
            rows.append(
                {
                    "Период": y,
                    "Контрагент": name,
                    "Сумма": (1000 - i * 100) * mult,
                    "Количество": 1,
                }
            )
    var = build_variance_contribution(rows, group_key="Контрагент")
    assert var["ok"], var
    assert var["delta"] != 0
    assert var["top_growth"]
    print("OK variance", var["delta"], var["delta_pct"], "growth0", var["top_growth"][0])

    abc = build_abc([r for r in rows if r["Период"] == "2016"], group_key="Контрагент")
    assert abc["ok"]
    assert set(abc["class_counts"]) >= {"A", "B", "C"} or sum(abc["class_counts"].values()) == 5
    print("OK abc", abc["class_counts"], abc["class_sums"])

    an = build_anomalies(var, drop_pct=10)
    assert an["ok"]
    print("OK anomalies", len(an.get("spikes") or []), len(an.get("drops") or []))

    assert detect_analyst_mode("ABC по контрагентам за 2016") == "abc"
    assert detect_analyst_mode("кто дал рост продаж") == "variance"
    assert detect_analyst_mode("просроченная задолженность корзинами") == "aging"
    b = analyst_bullets({**var, "mode": "variance"}, mode="variance")
    assert b
    print("OK bullets", b[:2])


def test_excel() -> None:
    from tools.excel_export import save_analyst_excel

    saved = save_analyst_excel(
        title="Smoke analyst",
        sheets=[
            {
                "name": "Итоги",
                "title": "По годам",
                "rows": [
                    {"Период": "2015", "Сумма": 100},
                    {"Период": "2016", "Сумма": 150},
                ],
                "chart": "bar",
            },
            {
                "name": "ABC",
                "title": "Классы",
                "rows": [
                    {"Контрагент": "A", "Сумма": 80, "Класс": "A"},
                    {"Контрагент": "B", "Сумма": 15, "Класс": "B"},
                    {"Контрагент": "C", "Сумма": 5, "Класс": "C"},
                ],
                "chart": "pie",
            },
        ],
        bullets=["Рост 50%", "A-класс держит 80%"],
        meta_extra={"mode": "smoke"},
        filename="smoke_analyst_charts.xlsx",
    )
    assert saved.get("ok"), saved
    assert Path(saved["path"]).exists()
    print("OK excel", saved["path"], "charts", saved.get("charts"))


def test_live() -> None:
    try:
        from backends import create_backend
        from config import load_config
        from tools.universal import UniversalTools
    except Exception as e:
        print("SKIP live import", e)
        return
    cfg = load_config()
    tools = UniversalTools(
        create_backend(cfg),
        read_only=True,
        default_limit=25,
        max_limit=80,
        list_objects_cap=80,
        tool_result_max_chars=8000,
        compact_results=True,
        code_dir=cfg.get("code_dir") or "",
    )
    r = tools.compare_periods(
        search_query="сравни продажи за 2016 и 2015",
        period="сравни продажи за 2016 и 2015",
        group_by="контрагент",
        metrics="sum,count",
        to_excel=False,
    )
    assert r.get("ok"), r.get("error") or r.get("need_clarification")
    assert r.get("analyst_summary"), "no analyst_summary"
    assert r.get("top_growth") is not None
    print("OK live compare analytics", r.get("variance"), r.get("analyst_summary")[:2])

    abc = tools.analyst_report(
        request="ABC по контрагентам за 2016",
        mode="abc",
        to_excel=True,
        filename="smoke_abc_2016.xlsx",
    )
    assert abc.get("ok") or abc.get("rows"), abc
    print(
        "OK live abc",
        abc.get("class_counts"),
        "excel",
        abc.get("path") or abc.get("excel_error"),
    )

    var = tools.analyst_report(
        request="кто дал рост и кто упал продажи 2015 и 2016",
        mode="variance",
        to_excel=True,
        filename="smoke_variance.xlsx",
    )
    print(
        "OK live variance",
        var.get("delta"),
        var.get("path") or var.get("excel_error"),
        (var.get("analyst_summary") or [])[:2],
    )


if __name__ == "__main__":
    test_core()
    test_excel()
    test_live()
    print("ALL ANALYST OK")
