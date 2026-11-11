# -*- coding: utf-8 -*-
"""
Preflight: высокий confidence → сразу tool, без «пустых» LLM-rounds.

Не заменяет LLM: выполняет 1 tool и кладёт результат в контекст,
модель только формулирует ответ (или дотягивает, если error/пусто).
"""

from __future__ import annotations

from typing import Any, Callable

# правила — в tools/router.py (единый роутер); helpers реэкспорт для tests/scripts
from tools.router import (
    _is_meta_structure_question,
    _is_short_structure_followup,
    _mentions_form_ui,
    _norm,
    _phrase_has_object_hint,
    classify,
)

__all__ = [
    "format_preflight_nudge",
    "plan_preflight",
    "preflight_result_ok",
    "run_preflight",
    "_is_meta_structure_question",
    "_is_short_structure_followup",
    "_mentions_form_ui",
    "_norm",
    "_phrase_has_object_hint",
]


def plan_preflight(
    phrase: str,
    *,
    context_full_name: str = "",
) -> dict[str, Any] | None:
    """
    План авто-вызова. None = пусть LLM сам выбирает tools.

    Возвращает:
      tool, args, confidence (0..1), reason, format (manager|facts)
    """
    d = classify(phrase, context_full_name=context_full_name)
    if not d or not d.get("tool"):
        return None
    return {k: d[k] for k in ("tool", "args", "confidence", "reason", "format") if k in d}


def preflight_result_ok(tool: str, result: Any) -> bool:
    """Достаточно ли данных, чтобы LLM мог ответить без новых tools."""
    if not isinstance(result, dict):
        return False
    if result.get("error") and not result.get("rows") and not result.get("registers"):
        # soft errors with partial data still useful
        if not any(
            result.get(k)
            for k in (
                "rows",
                "registers",
                "register_names_full",
                "findings",
                "roles",
                "manager_summary",
                "options",
            )
        ):
            return False
    if result.get("need_clarification") and result.get("options"):
        return True  # LLM should ask user
    if tool == "agent_capabilities":
        return bool(result.get("answer") or result.get("ok"))
    if tool == "manager_help":
        return bool(result.get("capabilities_answer") or result.get("scenarios") or result.get("ok") is not False)
    if tool in ("query_syntax_help", "code_help", "query_examples"):
        return bool(
            result.get("ok") is not False
            and (
                result.get("answer")
                or result.get("content")
                or result.get("example")
                or result.get("rules")
                or result.get("groups")
                or result.get("topic")
            )
        )
    if tool == "explain_command":
        return bool(result.get("element") or result.get("need_clarification"))
    if tool == "list_objects":
        return bool(result.get("names") or result.get("objects") or result.get("class_counts") or result.get("count"))
    if tool == "proc_index":
        return bool(result.get("definitions") or result.get("callers"))
    if tool == "explain_fill" and result.get("need_clarification"):
        return True
    if tool in ("resolve_object", "find_metadata", "quick_find"):
        return bool(
            result.get("top")
            or result.get("full_name")
            or result.get("items")
            or result.get("options")
            or result.get("need_clarification")
        )
    if tool in (
        "describe_object",
        "get_object_structure",
        "list_object_attributes",
        "describe_form",
    ):
        return bool(
            result.get("attributes")
            or result.get("attributes_lines")
            or result.get("fields")
            or result.get("header_fields")
            or result.get("structure_brief")
            or result.get("forms")
            or result.get("templates")
            or result.get("full_name")
            or (result.get("ok") and not result.get("error"))
        )
    if tool in ("test_connection", "list_active_sessions", "analyze_event_log"):
        return result.get("ok") is not False and not (
            result.get("error") and not result.get("sessions") and not result.get("events")
        )
    if tool == "list_roles_by_right":
        # 0 roles — всё равно «ok» для ответа (честно пусто)
        return "roles" in result or result.get("ok") is True
    if tool == "report_builder":
        if result.get("need_clarification"):
            return True
        if result.get("dry_run") and result.get("plan"):
            return True
        if result.get("rows") or result.get("manager_summary") or result.get("analyst_summary"):
            return True
        return bool(result.get("ok") and result.get("plan"))
    if tool in (
        "linked_query",
        "build_report",
        "query_by_attributes",
        "margin_report",
        "compare_periods",
        "analyst_report",
    ):
        rows = result.get("rows")
        if isinstance(rows, list) and len(rows) > 0:
            return True
        if result.get("manager_summary") or result.get("analyst_summary"):
            return True
        if result.get("summary_by_period") or result.get("abc") or result.get("funnel"):
            return True
        # 0 rows but query built — still answer honestly
        if result.get("ok") and result.get("query"):
            return True
        return bool(result.get("ok"))
    if tool == "analyze_object_movements":
        return bool(
            result.get("registers")
            or result.get("register_names_full")
            or result.get("summary")
            or result.get("conclusion")
        )
    if tool == "analyze_locks":
        return bool(
            result.get("ok")
            and (
                result.get("hits_count")
                or result.get("hot_objects")
                or result.get("summary")
            )
        )
    if tool == "recommend_managed_locks":
        return bool(
            result.get("ok")
            and (
                result.get("recommendations") is not None
                or result.get("bsl_template")
                or result.get("summary")
            )
        )
    if tool == "scan_code_injection":
        return result.get("ok") is True or "findings" in result
    if tool == "audit_query_code":
        return result.get("ok") is True or "findings" in result
    if tool == "list_roles_by_right":
        return bool(result.get("roles") is not None or result.get("ok"))
    if tool == "explain_subscriptions":
        return bool(result.get("subscriptions") is not None or result.get("ok"))
    if tool == "explain_rls":
        return bool(result.get("hits") is not None or result.get("ok"))
    if tool == "describe_scheduled_job":
        return bool(result.get("jobs") is not None or result.get("ok"))
    if tool == "form_event_map":
        return bool(result.get("events") is not None or result.get("ok"))
    if tool == "print_form_bind":
        return bool(result.get("parameters") is not None or result.get("ok"))
    if tool == "query_static_explain":
        return bool(result.get("tables") is not None or result.get("ok"))
    if tool == "http_service_map":
        return bool(result.get("services") is not None or result.get("ok"))
    if tool == "common_attr_and_types":
        return bool(
            result.get("common_attributes") is not None
            or result.get("defined_types") is not None
            or result.get("ok")
        )
    if tool == "exchange_composition":
        return bool(result.get("plans") is not None or result.get("ok"))
    if tool == "document_by_number":
        if result.get("ok") and result.get("query"):
            return True
        return bool(result.get("rows") or result.get("ok"))
    if tool == "proc_index":
        return bool(
            result.get("definitions") is not None
            or result.get("callers") is not None
            or result.get("ok")
        )
    if tool in ("inspect_code", "explain_fill"):
        return not result.get("error") or bool(
            result.get("matches")
            or result.get("hits")
            or result.get("data_source_hits")
            or result.get("modules")
            or result.get("module")
            or result.get("outline")
        )
    return bool(result.get("ok"))


def format_preflight_nudge(tool: str, result: Any, phrase: str) -> str:
    """Инструкция модели после preflight tool."""
    ok = preflight_result_ok(tool, result)
    base = (
        f"Уже выполнен tool `{tool}` (preflight) для фразы пользователя. "
        "Результат в предыдущем tool message. "
    )
    if not ok:
        return (
            base
            + "Результат с ошибкой/пустой. "
            "Можно 1–2 tool: resolve_object / get_object_structure / "
            "linked_query с sources= / build_report с full_name=. "
            "Не выдумывай full_name. Не пиши «подождите»."
        )
    if isinstance(result, dict) and result.get("need_clarification"):
        about = str(result.get("about") or "")
        opts = result.get("options") or []
        q = str(result.get("question") or "")
        # остатки «на сейчас» — не проси период; попроси только объект, если options
        if about == "period":
            return (
                base
                + "Tool просит период. Если вопрос про ОСТАТКИ/наличие на складах — "
                "НЕ спрашивай период, вызови linked_query(request=исходная_фраза, execute=true) "
                "или build_report с period='сейчас'. "
                "Для оборотов/продаж — спроси период кратко. "
                f"question: {q[:200]}"
            )
        return (
            base
            + "Нужно уточнение у пользователя. "
            f"Покажи options ({len(opts)} шт.) и спроси какой full_name. "
            "Не выбирай наугад. "
            f"{('question: ' + q[:180]) if q else ''}"
        )
    if tool in ("describe_object", "get_object_structure", "describe_form"):
        return (
            base
            + "Это СТРУКТУРА объекта (метаданные), НЕ данные/отчёт. "
            "Покажи structure_brief: attributes_sample, tabular_sections, "
            "forms, templates — РЕАЛЬНЫЕ имена из tool result. "
            "Если вопрос про макеты — перечисли templates (ПФ_MXL_… и synonym). "
            "НЕ выдумывай DefaultObjectForm / СчетНаОплату / ТОРГ12, "
            "если их нет в forms/templates. "
            "События/элементы — describe_form. НЕ спрашивай период. "
            "ЗАПРЕЩЕНО: build_report / compare_periods / linked_query "
            "(это не «сколько/суммы/продажи за год»)."
        )
    if tool == "agent_capabilities":
        return (
            base
            + "Это каталог возможностей. "
            "Выведи пользователю поле answer (markdown). "
            "Можно слегка оформить, но не выкидывай разделы. "
            "НЕ вызывай data-tools. Предложи 2–3 примера фраз."
        )
    if tool == "report_builder":
        return (
            base
            + "Универсальный построитель / мастер Excel. "
            "Если wizard + question — ПОКАЖИ question целиком, жди ответ пользователя, "
            "НЕ строй файл и НЕ вызывай compare. "
            "Следующий ход: report_builder(wizard_answer=ответ_пользователя). "
            "Если path/download_url — покажи excel (несколько листов-графиков). "
            "need_clarification без wizard → options. Не JOIN-лекцию."
        )
    if tool in (
        "linked_query",
        "build_report",
        "query_by_attributes",
        "margin_report",
        "compare_periods",
        "analyst_report",
    ):
        if tool == "query_by_attributes":
            return (
                base
                + "Это список документов/элементов по РЕКВИЗИТАМ шапки (не оборот). "
                "Покажи filters[] (реальные имена полей CF) и table_preview/rows. "
                "0 строк — честно: в базе нет таких или другое имя реквизита "
                "(candidate_fields). "
                "ЗАПРЕЩЕНО: compare_periods / analyst_report / «продажи за год». "
                "need_clarification about=field → спроси какое поле."
            )
        return (
            base
            + "Это ответ по ДАННЫМ/аналитике. "
            "Если есть analyst_summary — начни с 3–7 буллетов-выводов. "
            "Если summary_by_period — итоги по КАЖДОМУ периоду. "
            "ОБЯЗАТЕЛЬНО: table_preview (markdown) или rows — реальные строки "
            "(номенклатура/склад/количество). "
            "ЗАПРЕЩЕНО: пустая таблица с «…» / placeholder. "
            "Если path/download_url (excel) — покажи из tool result, не example.com. "
            "compare/analyst: ВСЕ периоды, не проси «уточнить другой год». "
            "Не вызывай снова те же tools без нужды. Не объясняй JOIN менеджеру."
        )
    if tool == "analyze_object_movements":
        return (
            base
            + "Перечисли регистры из registers / register_names_full "
            "(все, не урезай «из головы»). Кратко actions/fields. "
            "Не get_object_structure вместо этого."
        )
    if tool == "analyze_locks":
        return (
            base
            + "Покажи summary, hot_objects, hot_spaces, modes. "
            "Advice — практично. "
            "Явно: это СТАТИКА кода, не live «кто ждёт». "
            "Runtime: ТЖ TLOCK/TTIMEOUT, кластер. "
            "Тяжёлое проведение → analyze_object_movements. "
            "Если object_hint задан и hits пусты — не останавливайся: "
            "inspect_code(request='модуль объекта блокировки оптимизация '+hint) "
            "и related common modules. НЕ спрашивай «хотите найти модуль?»."
        )
    if tool == "inspect_code":
        return (
            base
            + "Смотри module, related_modules, query_functions, risk_hits, thin_object_module. "
            "Если thin_object_module — логика в CommonModules (path из risk_hits/query_functions), "
            "не только инфраструктурный ПроведениеСервер*. "
            "Перечисли risk_hits (path+line+kind) и дай рекомендации. "
            "НЕ спрашивай «хотите чтобы я нашёл/прочитал?» — сразу read_code нужных мест."
        )
    if tool == "recommend_managed_locks":
        return (
            base
            + "Это РЕКОМЕНДАЦИИ по внедрению managed locks. "
            "Покажи: summary, places_to_edit, recommendations "
            "(space/mode/dimensions/action), bsl_template, checklist. "
            "Не выдумывай регистры. Если full_name пуст — спроси документ. "
            "Код в template — черновик, подставить значения измерений из structure."
        )
    if tool == "audit_query_code":
        return (
            base
            + "Это АУДИТ запросов/BSL (антипаттерны), НЕ бизнес-отчёт. "
            "1) Перечисли findings (severity, id, path, line, title). "
            "2) severity_counts / how_better. "
            "3) Рекомендация BSL — «НЕ применено, CF не менялась». "
            "ЗАПРЕЩЕНО: build_report, «уточните период», «это не для отчёта». "
            "Если findings пусто — скажи «антипаттернов не найдено» + modules_scanned. "
            f"Вопрос: {phrase!r}."
        )
    if tool == "query_static_explain":
        return (
            base
            + "Карта запроса без execute: tables, virtual_tables, parameters. "
            "Не подставляй имена из другой CF. Антипассерны — audit_query_code."
        )
    if tool == "form_event_map":
        return (
            base
            + "Таблица event → handler из Form.xml. "
            "in_module=false — процедуры нет в Module.bsl. "
            "Не describe_object. Не выдумывай события."
        )
    if tool == "print_form_bind":
        return (
            base
            + "parameter / detailParameter макета. Имена только из tool. "
            "Заполнение в BSL — inspect_code той же CF."
        )
    if tool == "document_by_number":
        return (
            base
            + "Покажи rows шапки. 0 строк — честно. ТЧ не выдумывай. "
            "full_name только из tool, не угадывай тип документа."
        )
    if tool == "proc_index":
        return (
            base
            + "definitions — где объявлена; callers — кто вызывает. "
            "Тело — read_code(path=…). Не trace_calls вниз."
        )
    if tool == "scan_code_injection":
        return (
            base
            + "Это скан Выполнить/Вычислить. Покажи findings risk/path. "
            "НЕ build_report. Не путай с Запрос.Выполнить."
        )
    return (
        base
        + "Ответь по фактам tool result. Минимум новых tools. "
        f"Исходный вопрос: {phrase!r}."
    )


def run_preflight(
    phrase: str,
    dispatch: Callable[[str, dict[str, Any]], Any],
    *,
    min_confidence: float = 0.78,
    context_full_name: str = "",
) -> dict[str, Any] | None:
    """
    Выполнить preflight. Возвращает:
      {tool, args, result, plan, ok} или None

    context_full_name — объект из session memory (follow-up «какие поля в макете»).
    """
    plan = plan_preflight(phrase, context_full_name=context_full_name or "")
    if not plan:
        return None
    if float(plan.get("confidence") or 0) < min_confidence:
        return None
    tool = str(plan["tool"])
    args = dict(plan.get("args") or {})
    try:
        result = dispatch(tool, args)
    except Exception as e:
        result = {"error": str(e), "preflight_failed": True}
    return {
        "tool": tool,
        "args": args,
        "result": result,
        "plan": plan,
        "ok": preflight_result_ok(tool, result),
        "nudge": format_preflight_nudge(tool, result, phrase),
    }
