# -*- coding: utf-8 -*-
"""
Подмножество tools для LLM по категории вопроса (роутер → категория → 15–25 схем вместо 55).

Для моделей 20–30B это главный рычаг точности выбора tool и −10…15 тыс. символов контекста.
Неясная категория → None (показываем все tools).
"""

from __future__ import annotations

from typing import Any

CORE: frozenset[str] = frozenset(
    {
        "build_report",
        "linked_query",
        "query_by_attributes",
        "document_by_number",
        "resolve_object",
        "describe_object",
        "inspect_code",
        "search_code",
        "read_code",
        "proc_index",
        "explain_fill",
        "analyze_object_movements",
        "code_help",
        "agent_capabilities",
    }
)

BY_ROUTE: dict[str, frozenset[str]] = {
    "data": frozenset(
        {"compare_periods", "analyst_report", "margin_report", "export_object_excel", "export_excel_report", "execute_query", "list_objects"}
    ),
    "attr": frozenset({"compare_periods", "export_object_excel", "execute_query", "get_object_structure"}),
    "code": frozenset(
        {
            "trace_calls",
            "find_callers",
            "module_outline",
            "find_modules",
            "read_skd_query",
            "audit_query_code",
            "analyze_locks",
            "recommend_managed_locks",
            "form_event_map",
            "describe_form",
            "explain_command",
            "explain_config",
            "explain_subscriptions",
            "scan_code_injection",
            "query_static_explain",
            "inspect_external_processing",
            "describe_scheduled_job",
            "http_service_map",
            "print_form_bind",
        }
    ),
    "build_epf": frozenset(
        {"build_external_processing", "inspect_external_processing", "get_object_structure", "list_objects", "find_metadata",
         "search_fields", "describe_form", "module_outline", "find_modules", "query_syntax_help", "query_examples"}
    ),
    "movements": frozenset({"trace_calls", "module_outline", "get_object_structure", "analyze_locks"}),
    "structure": frozenset(
        {"describe_form", "explain_command", "explain_config", "get_object_structure", "list_objects", "find_metadata", "search_fields", "form_event_map", "print_form_bind", "common_attr_and_types", "exchange_composition"}
    ),
    "meta": frozenset(
        {"list_objects", "explain_config", "find_metadata", "get_object_structure", "search_fields", "describe_form", "common_attr_and_types", "exchange_composition", "http_service_map"}
    ),
    "access": frozenset({"explain_config", "access_overview", "list_users", "list_roles_by_right", "explain_rls"}),
    "help": frozenset({"query_syntax_help", "query_examples", "query_static_explain"}),
    "eventlog": frozenset({"analyze_event_log", "list_active_sessions", "test_connection"}),
    "sessions": frozenset({"analyze_event_log", "list_active_sessions", "test_connection"}),
    "conn": frozenset({"test_connection", "list_active_sessions"}),
}


def tool_subset_for(phrase: str, *, context_full_name: str = "") -> frozenset[str] | None:
    """Имена tools для этого хода или None (= все)."""
    try:
        from tools.router import classify, compute_hints
    except Exception:
        return None
    d: dict[str, Any] = classify(phrase or "", context_full_name=context_full_name) or {}
    route = str(d.get("route") or "")
    names: set[str] = set(CORE)
    if route in BY_ROUTE:
        names |= BY_ROUTE[route]
    hints = {}
    try:
        hints = compute_hints(phrase or "")
    except Exception:
        pass
    if hints.get("code") or hints.get("trace") or hints.get("query_audit"):
        names |= BY_ROUTE["code"]
    if hints.get("report") or hints.get("debt") or hints.get("attr"):
        names |= BY_ROUTE["data"]
    if hints.get("epf"):
        names.add("inspect_external_processing")
    if d.get("tool"):
        names.add(str(d["tool"]))
    # ничего не поняли — не обрезаем
    if not route and not any(hints.get(k) for k in ("code", "report", "debt", "attr", "epf", "trace")):
        return None
    return frozenset(names)
