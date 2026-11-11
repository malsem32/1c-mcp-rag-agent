# -*- coding: utf-8 -*-
"""Smoke: tool catalog trim + preflight false-positives."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backends import create_backend
from config import load_config
from tools.preflight import (
    _is_meta_structure_question,
    _mentions_form_ui,
    plan_preflight,
)
from tools.universal import (
    HIDDEN_FROM_AGENT,
    TOOL_DEFINITIONS,
    UniversalTools,
    _HIDDEN_FROM_LLM,
)


def main() -> int:
    cfg = load_config()
    tools = UniversalTools(create_backend(cfg), read_only=True)
    all_names = {
        ((t.get("function") or {}).get("name") or "") for t in TOOL_DEFINITIONS
    }
    llm_names = {
        ((t.get("function") or {}).get("name") or "") for t in tools.openai_tools()
    }
    print(
        f"definitions={len(all_names)} llm={len(llm_names)} "
        f"hidden={len(HIDDEN_FROM_AGENT)}"
    )
    missing_hide = sorted(HIDDEN_FROM_AGENT - all_names)
    assert HIDDEN_FROM_AGENT == _HIDDEN_FROM_LLM
    if missing_hide:
        print("WARN hidden not in definitions:", missing_hide)
    assert "code_help" in llm_names
    assert "report_builder" not in llm_names
    assert "report_builder" in all_names
    assert "quick_find" not in llm_names
    assert "manager_help" not in llm_names
    from tools.universal import agent_visible_tool_names

    assert llm_names == agent_visible_tool_names()

    # dispatch still works for hidden
    r = tools.dispatch("manager_help", {})
    assert isinstance(r, dict)
    r2 = tools.dispatch("code_help", {"query": "Запрос"})
    assert isinstance(r2, dict)

    cases = [
        ("сформируй отчет продажи за 2016", "report_builder", False),
        ("сформируй отчёт с графиком", "report_builder", False),
        ("какие формы у документа", "describe_object", True),
        ("события формы", None, True),  # may need ctx; form_ui true
        ("реквизиты документа", "describe_object", False),
        ("правила проведения", None, False),  # not access_overview
        ("какие роли могут удалять справочники", "list_roles_by_right", False),
        ("что умеешь", "agent_capabilities", False),
        ("почему не заполнился контрагент", "explain_fill", False),
    ]
    fails = 0
    for q, expect, want_form in cases:
        p = plan_preflight(q)
        tool = (p or {}).get("tool")
        form_ui = _mentions_form_ui(q)
        meta = _is_meta_structure_question(q)
        ok = True
        if expect and tool != expect:
            # report phrases must NOT be describe_object
            if expect == "report_builder" and tool == "describe_object":
                ok = False
            elif tool != expect:
                ok = False
        if expect == "report_builder" and tool not in (
            "report_builder",
            "build_report",
        ):
            ok = False
        if want_form is False and "сформ" in q.lower().replace("ё", "е") and form_ui:
            ok = False
        if want_form is True and not form_ui and "реквизит" not in q:
            # события формы / какие формы
            if "форм" in q.lower().replace("ё", "е"):
                ok = False
        mark = "OK" if ok else "FAIL"
        if not ok:
            fails += 1
        print(
            f"[{mark}] {tool or 'llm':28} form={form_ui} meta={meta} | {q}"
        )

    # «сформируй» must not be meta structure
    assert not _mentions_form_ui("сформируй отчет продажи")
    assert not _is_meta_structure_question("сформируй отчет продажи за 2016")
    assert _mentions_form_ui("какие формы у документа")

    print("FAILS", fails)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
