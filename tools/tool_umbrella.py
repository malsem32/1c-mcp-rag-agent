# -*- coding: utf-8 -*-
"""
Видимые для модели «зонтичные» tools ↔ скрытые узкие tools.

Модель (LLM-цикл и MCP) видит ~23 инструмента. Узкие остаются в dispatch: роутер/preflight/ask_1c
вызывают их напрямую, а вызов зонтика с фразой перенаправляется роутером в нужный узкий tool.

  UMBRELLAS[зонтик] — какие скрытые tools он может выполнить по решению роутера;
  HIDDEN_ALIAS[скрытый] — чем его называть в текстах для модели (подсказки, prefer, правила).
"""

from __future__ import annotations

import re
from typing import Any

UMBRELLAS: dict[str, frozenset[str]] = {
    "build_report": frozenset(
        {"linked_query", "compare_periods", "analyst_report", "margin_report", "report_builder",
         "export_object_excel", "export_excel_report", "summarize_periods"}
    ),
    "explain_config": frozenset(
        {"explain_subscriptions", "describe_scheduled_job", "explain_rls", "http_service_map", "exchange_composition",
         "common_attr_and_types", "read_skd_query", "form_event_map", "print_form_bind", "scan_code_injection",
         "describe_form"}
    ),
    "code_help": frozenset({"query_syntax_help", "query_examples", "query_static_explain"}),
    "access_overview": frozenset({"list_users", "list_roles_by_right", "explain_rls"}),
    "analyze_event_log": frozenset({"list_active_sessions", "test_connection"}),
    "analyze_locks": frozenset({"recommend_managed_locks"}),
    "resolve_object": frozenset({"find_metadata", "quick_find", "suggest_objects"}),
    "describe_object": frozenset({"describe_form", "get_object_structure"}),
    "proc_index": frozenset({"find_callers"}),
}

# скрытый tool → видимая замена в текстах для модели
HIDDEN_ALIAS: dict[str, str] = {}
for _vis, _hidden in UMBRELLAS.items():
    for _h in _hidden:
        HIDDEN_ALIAS.setdefault(_h, _vis)
HIDDEN_ALIAS.update(
    {
        "get_object_structure": "describe_object",
        "list_object_attributes": "describe_object",
        "search_fields": "describe_object",
        "resolve_business_fields": "describe_object",
        "describe_form": "describe_object",
        "find_modules": "search_code",
        "module_outline": "read_code",
        "find_callers": "proc_index",
        "list_metadata_types": "list_objects",
        "list_document_types": "list_objects",
        "list_catalog_types": "list_objects",
        "list_register_types": "list_objects",
        "manager_help": "agent_capabilities",
        "code_status": "agent_capabilities",
        "export_document_period_excel": "build_report",
        "query_event_log": "analyze_event_log",
        "active_users_overview": "analyze_event_log",
        "role_rights": "access_overview",
        "find_roles_for_object": "access_overview",
        "list_roles": "access_overview",
    }
)

VISIBLE: frozenset[str] = frozenset(
    {
        # данные
        "build_report", "query_by_attributes", "document_by_number", "execute_query",
        # объекты и структура
        "resolve_object", "list_objects", "describe_object", "explain_config",
        # код
        "inspect_code", "search_code", "read_code", "proc_index", "trace_calls", "explain_fill",
        "analyze_object_movements", "explain_command", "audit_query_code", "analyze_locks",
        # справка, доступ, журнал, файлы, о себе
        "code_help", "access_overview", "analyze_event_log", "inspect_external_processing", "build_external_processing", "agent_capabilities",
    }
)

_ALIAS_RE = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(sorted(map(re.escape, HIDDEN_ALIAS), key=len, reverse=True)) + r")(?![A-Za-z0-9_])")


def visible_tool_text(text: str) -> str:
    """Имена скрытых tools в тексте для модели → видимые замены (иначе модель зовёт то, чего нет в списке)."""
    if not text or "_" not in text:
        return text
    return _ALIAS_RE.sub(lambda m: HIDDEN_ALIAS.get(m.group(1), m.group(1)), text)


_TEXT_KEYS = frozenset({"prefer", "hint", "action_for_agent", "next", "next_steps", "prefer_tool", "note", "note_paging", "prefer_paging", "tip"})


def visible_tool_payload(value: Any, _depth: int = 0) -> Any:
    """Тот же переписчик для служебных текстовых полей результата (prefer/hint/…)."""
    if _depth > 3:
        return value
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in _TEXT_KEYS and isinstance(v, str):
                out[k] = visible_tool_text(v)
            elif k in _TEXT_KEYS and isinstance(v, list):
                out[k] = [visible_tool_text(x) if isinstance(x, str) else x for x in v]
            elif isinstance(v, dict):
                out[k] = visible_tool_payload(v, _depth + 1)
            else:
                out[k] = v
        return out
    return value


def route_umbrella(name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """
    Вызов зонтика с фразой → (узкий tool, args), если роутер уверенно выбрал tool из его набора.
    None — выполнять сам зонтик.
    """
    allowed = UMBRELLAS.get(name)
    if not allowed:
        return None
    req = str(args.get("request") or args.get("query") or args.get("search_query") or "").strip()
    if not req:
        return None
    # явно заданный объект/параметры — модель знает, что делает: не перенаправляем
    if name in ("describe_object", "resolve_object") and (args.get("full_name") or args.get("object_type") not in (None, "", "Все")):
        return None
    try:
        from tools.router import classify

        d = classify(req) or {}
    except Exception:
        return None
    tool = d.get("tool")
    if tool in allowed and float(d.get("confidence") or 0) >= 0.78:
        merged = dict(d.get("args") or {})
        for k, v in args.items():
            if k not in ("request", "query", "search_query") and v not in (None, "", 0, False):
                merged.setdefault(k, v)
        return str(tool), merged
    return None
