# -*- coding: utf-8 -*-
"""Smoke: universal report_builder."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_parse() -> None:
    from tools.report_builder import (
        is_report_builder_question,
        parse_report_request,
        explain_spec,
    )
    from tools.preflight import plan_preflight

    assert is_report_builder_question("построй отчёт продажи по контрагентам за 2016")
    assert is_report_builder_question("сформируй отчет топ 10")
    assert not is_report_builder_question("что умеешь")

    s = parse_report_request(
        "построй отчёт продажи по контрагентам за 2016 в excel с графиками топ 10"
    )
    assert s.route in ("aggregate", "compare")
    assert "контрагент" in s.group_by
    assert s.to_excel
    assert s.charts
    assert s.top_n == 10
    assert "2016" in s.period or "2016" in s.request
    print("OK parse", s.route, s.group_by, s.period, s.top_n)
    print("\n".join(explain_spec(s)))

    pf = plan_preflight("построй отчёт продажи по контрагентам за 2015")
    assert pf and pf["tool"] == "report_builder", pf
    print("OK preflight", pf["tool"])


def test_dry_and_live() -> None:
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
    dry = t.report_builder(
        request="построй отчёт продажи по контрагентам за 2016",
        dry_run=True,
    )
    assert dry.get("dry_run") and dry.get("plan")
    print("OK dry_run plan:", dry["plan"])

    live = t.report_builder(
        request="построй отчёт продажи по контрагентам за 2016",
        to_excel=False,
        limit=20,
    )
    print(
        "live ok",
        live.get("ok"),
        "fn",
        live.get("full_name"),
        "rows",
        len(live.get("rows") or []),
        "builder",
        live.get("builder"),
        "err",
        live.get("error") or live.get("need_clarification"),
    )
    assert live.get("builder") == "universal_report_builder"
    assert live.get("ok") or live.get("need_clarification")

    via_br = t.build_report(
        request="построй отчёт топ 5 клиентов по продажам за 2015"
    )
    print(
        "via build_report",
        via_br.get("builder"),
        via_br.get("ok"),
        len(via_br.get("rows") or []),
    )


if __name__ == "__main__":
    test_parse()
    test_dry_and_live()
    print("ALL REPORT_BUILDER OK")
