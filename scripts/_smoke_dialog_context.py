# -*- coding: utf-8 -*-
"""Smoke: follow-up «только <контрагент>» помнит периоды прошлого отчёта."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.dialog_context import (
    expand_with_context,
    looks_like_followup,
    snapshot_from_result,
    try_local_filter,
)


def main() -> int:
    # Имена — плейсхолдеры, не демо-УТ
    rows = [
        {"Период": "2016", "Контрагент": "КонтрагентБ", "Сумма": 3724724.05, "Количество": 18},
        {"Период": "2015", "Контрагент": "КонтрагентВ", "Сумма": 1075000, "Количество": 1},
        {"Период": "2016", "Контрагент": "КонтрагентА", "Сумма": 529995.33, "Количество": 14},
        {"Период": "2015", "Контрагент": "КонтрагентА", "Сумма": 188512.90, "Количество": 4},
        {"Период": "2016", "Контрагент": "КонтрагентГ", "Сумма": 1333331.95, "Количество": 7},
    ]
    result = {
        "ok": True,
        "full_name": "Документ.ТестовыйДокументПродаж",
        "period_spec": "сравни продажи за 2016 и 2015 года",
        "periods": [{"label": "2016"}, {"label": "2015"}],
        "summary_by_period": [
            {"Период": "2016", "Сумма": 9555027.56, "Количество": 129},
            {"Период": "2015", "Сумма": 4271248.57, "Количество": 53},
        ],
        "group_by": "Контрагент",
        "rows": rows,
        "count": len(rows),
    }
    state = snapshot_from_result(
        "compare_periods",
        result,
        user_text="сравни продажи за 2016 и 2015 года",
        args={"group_by": "контрагент"},
    )
    assert state and len(state["rows"]) == 5
    assert state["periods"] == ["2016", "2015"]
    print("OK snapshot", state["periods"])

    phrase = "сравни только КонтрагентА"
    assert looks_like_followup(phrase, state)
    local = try_local_filter(phrase, state)
    assert local and local.get("ok")
    assert local["count"] == 2
    labs = {r["Период"] for r in local["rows"]}
    assert labs == {"2015", "2016"}, labs
    sbp = {x["Период"]: x["Сумма"] for x in local["summary_by_period"]}
    assert abs(sbp["2016"] - 529995.33) < 0.01
    assert abs(sbp["2015"] - 188512.90) < 0.01
    print("OK local_filter", local["summary_by_period"])

    exp = expand_with_context(phrase, state)
    assert exp and "2015" in exp and "2016" in exp and "контрагента" in exp.lower()
    print("OK expand", exp)

    # ответ только годами после pending
    pending = dict(state)
    pending["pending_clarification"] = True
    pending["user_text"] = "сравни только КонтрагентА"
    exp2 = expand_with_context("2015 2016 года", pending)
    assert exp2 and ("КонтрагентА" in exp2 or "контрагента" in exp2.lower())
    assert "2015" in exp2 and "2016" in exp2
    print("OK expand years", exp2)

    # missing name → None local
    assert try_local_filter("сравни только НетТакогоКлиентаXYZ", state) is None
    print("OK miss filter")

    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
