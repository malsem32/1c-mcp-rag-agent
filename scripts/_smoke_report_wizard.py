# -*- coding: utf-8 -*-
"""Smoke: Excel chart wizard (step-by-step)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_wizard_flow() -> None:
    from tools.report_wizard import (
        apply_answer,
        answers_to_builder_kwargs,
        parse_charts_answer,
        start_wizard,
        wants_excel_chart_wizard,
    )
    from tools.preflight import plan_preflight

    assert wants_excel_chart_wizard("сделай excel с диаграммами")
    assert wants_excel_chart_wizard("выгрузка в эксель с графиками")
    assert not wants_excel_chart_wizard("продажи за 2016")

    pf = plan_preflight("excel с диаграммой")
    assert pf and pf["tool"] == "report_builder", pf
    print("OK preflight wizard")

    ch = parse_charts_answer("столбцы и pie, line по количеству")
    types = [c["type"] for c in ch]
    assert "bar" in types and "pie" in types
    print("OK charts parse", ch)

    st = start_wizard("excel с диаграммами продажи")
    assert st.get("wizard") and st.get("need_clarification")
    state = st["wizard_state"]
    print("start step", state.get("step_index"), "answers", state.get("answers"))

    # answer remaining steps
    for ans in (
        "продажи",
        "2016",
        "контрагент",
        "сумма и количество",
        "топ 10",
        "bar, pie, line",
    ):
        r = apply_answer(state, ans)
        state = r["state"]
        if r.get("ready_to_build"):
            break
        print("step→", state.get("step_index"), "ready", r.get("ready_to_build"))

    # confirm
    if not r.get("ready_to_build"):
        r = apply_answer(state, "да")
        state = r["state"]
    assert r.get("ready_to_build"), r
    kw = answers_to_builder_kwargs(state)
    assert kw["to_excel"] and kw["charts_spec"]
    print("OK kwargs", kw["request"], kw["charts_spec"])


def test_live_wizard_build() -> None:
    from backends import create_backend
    from config import load_config
    from tools.universal import UniversalTools

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
    # AUTO path (default): no wizard questions
    r_auto = t.report_builder(
        request="excel с диаграммами продажи 2016",
        skip_wizard=True,
        to_excel=True,
    )
    print(
        "AUTO",
        r_auto.get("path"),
        r_auto.get("full_name"),
        r_auto.get("charts_count"),
        "wizard=",
        r_auto.get("wizard"),
    )
    assert not r_auto.get("wizard")
    if r_auto.get("path"):
        assert Path(r_auto["path"]).exists()
        print("BUILT auto", r_auto["path"])
        return

    # optional step wizard only if explicitly requested
    r0 = t.report_builder(
        request="мастер excel по шагам",
        skip_wizard=False,
    )
    if r0.get("wizard") and r0.get("question"):
        print("Q0", (r0.get("question") or "")[:120].replace("\n", " "))
        for ans in (
            "продажи",
            "2015 и 2016",
            "контрагент",
            "сумма",
            "без фильтров",
            "столбцы и pie",
            "да",
        ):
            r = t.report_builder(
                wizard_answer=ans, request=ans, skip_wizard=False
            )
            if r.get("path"):
                print(
                    "BUILT wizard",
                    r.get("path"),
                    "charts",
                    r.get("charts_count"),
                )
                assert Path(r["path"]).exists()
                return
            if r.get("need_clarification"):
                print("Q", (r.get("question") or "")[:100].replace("\n", " | "))
    print("WARN: no file — last auto keys", list(r_auto.keys())[:12])


if __name__ == "__main__":
    test_wizard_flow()
    test_live_wizard_build()
    print("ALL WIZARD OK")
