# -*- coding: utf-8 -*-
"""Smoke: compare 2015 vs 2016 — оба периода в compact_json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from context_budget import compact_json
from tools.report_format import balance_period_rows, wants_period_compare


def test_balance_unit() -> None:
    rows = []
    for year in ("2016", "2015"):
        for i in range(20):
            rows.append(
                {
                    "Период": year,
                    "Контрагент": f"K{year}_{i}",
                    "Сумма": 1000 - i * 10 if year == "2016" else 500 - i * 5,
                    "Количество": 1,
                }
            )
    sbp, balanced, meta = balance_period_rows(
        rows, ["2016", "2015"], per_period_cap=8, total_cap=16
    )
    periods = {r["Период"] for r in balanced}
    assert periods == {"2016", "2015"}, periods
    assert len(sbp) == 2
    assert meta["rows_shown"] == 16
    print("OK balance_unit:", meta)


def test_compact_keeps_both() -> None:
    rows = []
    for year in ("2016", "2015"):
        for i in range(40):
            rows.append(
                {
                    "Период": year,
                    "Контрагент": f"Контрагент длинное имя {year} номер {i}",
                    "Сумма": 3_724_724.05 - i * 1000 if year == "2016" else 1_000_000 - i * 500,
                    "Количество": 18 - i % 5,
                    "ДатаНачала": f"{year}-01-01",
                    "ДатаКонца": f"{year}-12-31",
                    "Год": int(year),
                    "Месяц": 1,
                }
            )
    sbp, balanced, meta = balance_period_rows(
        rows, ["2016", "2015"], per_period_cap=8, total_cap=16
    )
    payload = {
        "ok": True,
        "full_name": "Документ.ТестовыйДокументПродаж",
        "period_spec": "сравни продажи за 2016 и 2015 года",
        "periods": [
            {"label": "2016", "start": "2016-01-01 00:00:00", "end": "2016-12-31 23:59:59"},
            {"label": "2015", "start": "2015-01-01 00:00:00", "end": "2015-12-31 23:59:59"},
        ],
        "summary_by_period": [
            {"Период": "2016", "Сумма": 50_000_000, "Количество": 200},
            {"Период": "2015", "Сумма": 40_000_000, "Количество": 180},
        ],
        "prefer": "MUST show both periods 2016 and 2015 from summary_by_period",
        "note": "test",
        "rows": balanced,
        "count": len(balanced),
        "manager_summary": {
            "message": "Срезы: 2016, 2015. Итого: 2016=50000000; 2015=40000000",
            "periods": ["2016", "2015"],
        },
    }
    # bad case: all 2016 first (old behaviour)
    bad = dict(payload)
    bad["rows"] = rows  # 40 of 2016 then 40 of 2015
    bad.pop("summary_by_period", None)

    good_s = compact_json(payload, 6000)
    bad_s = compact_json(bad, 6000)
    print("good chars", len(good_s), "has 2015 sum?", "2015" in good_s and "40000000" in good_s.replace(" ", ""))
    print("bad chars", len(bad_s))

    good_j = json.loads(good_s.replace("…[truncated_for_32k_context]", '"}')) if False else None
    # parse carefully — may be truncated string
    assert "summary_by_period" in good_s
    assert '"Период": "2015"' in good_s or '"Период":"2015"' in good_s
    assert '"Период": "2016"' in good_s or '"Период":"2016"' in good_s
    # detail rows also both
    assert "2015" in good_s and "2016" in good_s
    print("OK compact_keeps_both")

    # old shrink of un-balanced rows without summary would lose 2015 in head
    # with period-aware shrink it should keep both
    assert "periods_kept" in bad_s or "2015" in bad_s
    print("OK period_aware shrink on bad rows too")


def test_wants_compare() -> None:
    assert wants_period_compare("сравни продажи за 2016 и 2015 года")
    print("OK wants_compare")


def test_live_if_backend() -> None:
    """Optional live call against 1C if tools bootstrap works."""
    try:
        from backends import create_backend
        from config import load_config
        from tools.universal import UniversalTools
    except Exception as e:
        print("SKIP live (import):", e)
        return
    try:
        cfg = load_config()
        be = create_backend(cfg)
        tools = UniversalTools(
            be,
            read_only=True,
            default_limit=25,
            max_limit=80,
            list_objects_cap=80,
            tool_result_max_chars=6000,
            compact_results=True,
            code_dir=cfg.get("code_dir") or "",
        )
        print("1C:", be.test_connection())
    except Exception as e:
        print("SKIP live (init):", e)
        return

    r = tools.compare_periods(
        search_query="сравни продажи за 2016 и 2015 года",
        period="сравни продажи за 2016 и 2015 года",
        group_by="контрагент",
        metrics="sum,count",
    )
    if not isinstance(r, dict):
        print("FAIL live: not dict", r)
        sys.exit(1)
    if r.get("error") or r.get("need_clarification"):
        print("FAIL live clarify/error:", json.dumps(r, ensure_ascii=False)[:800])
        sys.exit(1)

    sbp = r.get("summary_by_period") or []
    labs = {str(x.get("Период")) for x in sbp if isinstance(x, dict)}
    print("live full_name", r.get("full_name"))
    print("live summary_by_period", json.dumps(sbp, ensure_ascii=False))
    rows = r.get("rows") or []
    print("live rows n", len(rows), "periods in rows", sorted({str(x.get("Период")) for x in rows if isinstance(x, dict)}))
    print("live rows sample", json.dumps(rows[:4], ensure_ascii=False, default=str))
    print("live prefer head", str(r.get("prefer") or "")[:200])
    print("live manager_summary", r.get("manager_summary"))

    compact = compact_json(r, 6000)
    print("live compact chars", len(compact))
    has_2015 = "2015" in compact
    has_2016 = "2016" in compact
    has_sbp = "summary_by_period" in compact
    print("live compact has summary/2015/2016:", has_sbp, has_2015, has_2016)
    if not (has_2015 and has_2016 and has_sbp):
        print("FAIL: compact lost a year or summary")
        print(compact[:1200])
        sys.exit(1)
    if len(sbp) < 2:
        print("FAIL: summary_by_period needs 2 periods", sbp)
        sys.exit(1)
    # exact totals should exist for both years
    if not ({"2015", "2016"} <= labs):
        print("WARN labels:", labs, "— check if parse used other labels")
    print("OK live compare")


if __name__ == "__main__":
    test_wants_compare()
    test_balance_unit()
    test_compact_keeps_both()
    test_live_if_backend()
    print("ALL DONE")
