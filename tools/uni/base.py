# -*- coding: utf-8 -*-
"""Ядро UniversalTools: конструктор, кэш структуры, dispatch, compact, схема tools."""

from __future__ import annotations

import inspect
import json
from typing import Any, Callable

from backends.base import OneCBackend
from context_budget import compact_json
from tools.field_discovery import parse_structure_fields
from tools.query_builder import (
    is_catalog,
    is_document,
    normalize_metadata_name,
    normalize_table_name,
)
from tools.uni.catalog import is_agent_visible_tool, TOOL_DEFINITIONS


class BaseMixin:
    def __init__(
        self,
        backend: OneCBackend,
        *,
        read_only: bool = True,
        default_limit: int = 25,
        max_limit: int = 80,
        list_objects_cap: int = 80,
        tool_result_max_chars: int = 6000,
        compact_results: bool = True,
        code_dir: str = "",
    ) -> None:
        self.backend = backend
        self.read_only = read_only
        self.default_limit = default_limit
        self.max_limit = max_limit
        self.list_objects_cap = list_objects_cap
        self.tool_result_max_chars = tool_result_max_chars
        self.compact_results = compact_results
        self.code_dir = code_dir or ""
        # кэш list_objects: metadata_type → names (ускоряет find/quick_find)
        self._meta_list_cache: dict[str, list[str]] = {}
        # session cache: find_metadata / structure (TTL ~10 мин)
        self._meta_search_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._structure_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._cache_ttl_sec: float = 600.0

    def _load_structure(self, full_name: str) -> tuple[str, dict[str, Any]]:
        try:
            name = normalize_metadata_name(full_name)
        except ValueError:
            name = normalize_table_name(full_name)
            # для структуры всегда Тип.Имя
            parts = name.split(".")
            if len(parts) >= 2:
                name = f"{parts[0]}.{parts[1]}"
        structure = self.backend.get_object_structure(name)
        if not isinstance(structure, dict):
            return name, {"error": "Некорректный ответ структуры", "raw": str(structure)[:300]}
        return name, structure

    def _require_structure(self, full_name: str) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        """
        Returns (name, structure, error_dict).
        error_dict set if structure unavailable or empty fields.
        """
        try:
            name, structure = self._load_structure(full_name)
        except ValueError as e:
            return "", None, {"error": str(e)}

        # Ответ /list «все классы» — это не структура объекта
        if "Справочники" in structure and "Документы" in structure and not any(
            k in structure
            for k in ("attributes", "Attributes", "Реквизиты", "full_name", "fields")
        ):
            return name, None, {
                "error": "HTTP /object не вернул структуру объекта (пришёл общий list)",
                "hint": (
                    "Почините GET object?name=Тип.Имя в HTTP-сервисе 1С "
                    "(готово: onec_http/Module.bsl). Без этого агент не видит реквизиты конфигурации."
                ),
                "full_name": name,
            }

        if structure.get("error"):
            return name, None, {
                "error": structure.get("error"),
                "hint": structure.get("hint")
                or (
                    "Нужен рабочий GET /object?name=Тип.Имя на HTTP-сервисе 1С "
                    "(см. onec_http/Module.bsl). Без структуры поля конфигурации не видны."
                ),
                "full_name": name,
            }

        fields = parse_structure_fields(structure)
        if not fields:
            return name, structure, {
                "error": "Структура объекта пуста — в ответе 1С нет реквизитов",
                "hint": (
                    "Проверьте GET /object?name=... на стороне 1С. "
                    "Агент ищет сумму/контрагента/номер только среди реальных реквизитов."
                ),
                "full_name": name,
                "raw_keys": list(structure.keys())[:20],
            }
        return name, structure, None

    def _object_kind(self, name: str) -> str:
        if is_document(name):
            return "document"
        if is_catalog(name):
            return "catalog"
        return "other"

    def dispatch(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        args = arguments or {}
        handlers: dict[str, Callable[..., Any]] = {
            "manager_help": self.manager_help,
            "agent_capabilities": self.agent_capabilities,
            "query_syntax_help": self.query_syntax_help,
            "query_examples": self.query_examples,
            "test_connection": self.test_connection,
            "list_metadata_types": self.list_metadata_types,
            "list_document_types": self.list_document_types,
            "list_catalog_types": self.list_catalog_types,
            "list_register_types": self.list_register_types,
            "list_objects_filtered": self.list_objects_filtered,
            "list_objects": self.list_objects_filtered,
            "quick_find": self.quick_find,
            "resolve_object": self.resolve_object,
            "find_metadata": self.find_metadata,
            "suggest_objects": self.suggest_objects,
            "get_object_structure": self.get_object_structure,
            "list_object_attributes": self.list_object_attributes,
            "describe_object": self.describe_object,
            "describe_form": self.describe_form,
            "search_fields": self.search_fields,
            "query_by_attributes": self.query_by_attributes,
            "resolve_business_fields": self.resolve_business_fields,
            "get_manager_fields": self.get_manager_fields,
            "code_help": self.code_help,
            "execute_query": self.execute_query,
            "export_excel_report": self.export_excel_report,
            "export_object_excel": self.export_object_excel,
            "export_document_period_excel": self.export_document_period_excel,
            "build_report": self.build_report,
            "report_builder": self.report_builder,
            "compare_periods": self.compare_periods,
            "summarize_periods": self.summarize_periods,
            "analyst_report": self.analyst_report,
            "resolve_full_name": self.resolve_full_name,
            "code_status": self.code_status,
            "find_modules": self.find_modules,
            "module_outline": self.module_outline,
            "search_code": self.search_code,
            "read_code": self.read_code,
            "inspect_code": self.inspect_code,
            "inspect_external_processing": self.inspect_external_processing,
            "build_external_processing": self.build_external_processing,
            "analyze_object_movements": self.analyze_object_movements,
            "analyze_locks": self.analyze_locks,
            "recommend_managed_locks": self.recommend_managed_locks,
            "linked_query": self.linked_query,
            "find_callers": self.find_callers,
            "scan_code_injection": self.scan_code_injection,
            "audit_query_code": self.audit_query_code,
            "list_roles_by_right": self.list_roles_by_right,
            "explain_subscriptions": self.explain_subscriptions,
            "explain_rls": self.explain_rls,
            "describe_scheduled_job": self.describe_scheduled_job,
            "form_event_map": self.form_event_map,
            "print_form_bind": self.print_form_bind,
            "query_static_explain": self.query_static_explain,
            "http_service_map": self.http_service_map,
            "common_attr_and_types": self.common_attr_and_types,
            "exchange_composition": self.exchange_composition,
            "document_by_number": self.document_by_number,
            "explain_command": self.explain_command,
            "proc_index": self.proc_index,
            "margin_report": self.margin_report,
            "trace_calls": self.trace_calls,
            "explain_fill": self.explain_fill,
            "route_intent": self.route_intent,
            "read_skd_query": self.read_skd_query,
            "list_roles": self.list_roles,
            "role_rights": self.role_rights,
            "find_roles_for_object": self.find_roles_for_object,
            "list_users": self.list_users,
            "access_overview": self.access_overview,
            "query_event_log": self.query_event_log,
            "analyze_event_log": self.analyze_event_log,
            "list_active_sessions": self.list_active_sessions,
            "active_users_overview": self.active_users_overview,
        }
        handlers["explain_config"] = self.explain_config
        routed_from = ""
        try:
            from tools.tool_umbrella import route_umbrella

            rr = route_umbrella(name, dict(args or {}))
            if rr and rr[0] in handlers:
                routed_from, (name, args) = name, rr
        except Exception:
            pass
        if name not in handlers:
            return {"error": f"Неизвестный инструмент: {name}", "known": list(handlers)}
        # вопрос по внешней обработке (.epf/.erf) — отвечаем по её коду, не по одноимённому объекту CF
        try:
            from tools.epf_tools import external_dispatch

            ext = external_dispatch(name, dict(args or {}))
        except Exception as e:
            ext = {"ok": False, "source": "external_file", "error": f"Разбор внешнего файла: {e}"}
        if ext is not None:
            return ext
        fn = handlers[name]
        sig = inspect.signature(fn)
        accepted = {k: v for k, v in args.items() if k in sig.parameters}
        result = fn(**accepted)
        # регистр: какие документы делают по нему движения (обратный индекс RegisterRecords выгрузки)
        if name in ("describe_object", "get_object_structure") and isinstance(result, dict):
            rfn = str(result.get("full_name") or "")
            if rfn.startswith("Регистр") and "registrars" not in result:
                try:
                    from tools.object_catalog import documents_writing_register

                    regs = documents_writing_register(rfn)
                    if regs:
                        result["registrars"] = regs[:80]
                        result["registrars_total"] = len(regs)
                except Exception:
                    pass
        if (
            isinstance(result, dict)
            and name in ("search_code", "trace_calls", "find_callers", "module_outline", "proc_index")
            and result.get("source") != "external_file"
        ):
            # ответ из CF, а в кэше разобран внешний файл: модель могла иметь в виду его
            try:
                from tools.epf_inspect import _CACHE

                fname = str((_CACHE.get("parsed") or {}).get("file_name") or "")
            except Exception:
                fname = ""
            if fname:
                result["source_note"] = (
                    f"Искал в конфигурации (Qdrant), не во внешнем файле. Если вопрос про «{fname}» — "
                    f"повтори вызов с path=\"{fname}\". Терминал этот файл не видит."
                )
        # блокировки / качество запросов: к находкам в коде — разделы ИТС и стандартов, чтобы агент объяснял «как правильно»
        # по инструкции, а не по памяти (агент может и сам уточнить через code_help)
        if isinstance(result, dict) and name in ("analyze_locks", "audit_query_code", "recommend_managed_locks") \
                and args.get("request") and "документация" not in result:
            try:
                from vector.search import search_docs

                docs = search_docs(str(args["request"]), limit=2)
                if docs:
                    result["документация"] = [{**d, "text": d["text"][:900]} for d in docs]
            except Exception:  # noqa: BLE001
                pass
        if isinstance(result, dict):
            from tools.tool_umbrella import visible_tool_payload

            result = visible_tool_payload(result)
            if routed_from:
                result.setdefault("_routed", f"{routed_from} → {name}")
        return self._maybe_compact(result, tool_name=name)

    def explain_config(self, request: str = "") -> dict[str, Any]:
        """Зонтик для разделов конфигурации; сюда попадаем, если роутер не выбрал узкий tool уверенно."""
        try:
            from tools.router import classify
            from tools.tool_umbrella import UMBRELLAS

            d = classify(request or "") or {}
            if d.get("tool") in UMBRELLAS["explain_config"]:
                return self.dispatch(str(d["tool"]), dict(d.get("args") or {}))
        except Exception:
            pass
        return {
            "ok": True,
            "need_clarification": True,
            "question": "Что именно посмотреть в конфигурации?",
            "options": [
                "подписки на события объекта",
                "регламентное задание",
                "RLS / ограничения доступа",
                "HTTP-сервисы",
                "состав плана обмена",
                "общие реквизиты / определяемые типы",
                "события формы объекта",
                "параметры макета печатной формы",
                "запрос СКД отчёта",
                "опасный код (Выполнить/Вычислить)",
            ],
        }

    def _maybe_compact(self, result: Any, tool_name: str | None = None) -> Any:
        """Для MCP/LM Studio: ужать огромные JSON под 32k context."""
        if not self.compact_results:
            return result
        if not isinstance(result, (dict, list)):
            return result
        # structure tools: отдаём slim-срез, не 70k layout
        if (
            tool_name
            in (
                "describe_object",
                "get_object_structure",
                "list_object_attributes",
                "describe_form",
            )
            and isinstance(result, dict)
            and result.get("structure_brief")
        ):
            if result.get("_form_focus") and isinstance(result.get("form_layout"), dict):
                # вопрос про форму: суть ответа — раскладка формы (вкладки, элементы, кнопки)
                slim = {
                    "ok": result.get("ok", True),
                    "tool": result.get("tool") or tool_name,
                    "full_name": result.get("full_name"),
                    "synonym": result.get("synonym"),
                    "forms": [f.get("name") if isinstance(f, dict) else f for f in (result.get("forms") or [])][:30],
                    "form_layout_of": result.get("form_layout_of"),
                    "form_layout": result["form_layout"],
                }
                raw = compact_json(slim, self.tool_result_max_chars, tool_name=tool_name)
                try:
                    return json.loads(raw)
                except Exception:
                    return slim
            slim = {
                "ok": result.get("ok", True),
                "tool": result.get("tool") or tool_name,
                "full_name": result.get("full_name"),
                "synonym": result.get("synonym"),
                "structure_brief": result.get("structure_brief"),
                "prefer": result.get("prefer"),
                "default_forms": result.get("default_forms"),
                "forms": (result.get("forms") or [])[:30],
                "templates": (result.get("templates") or [])[:40],
                "tabular_sections": (result.get("tabular_sections") or [])[:20],
                "attributes_lines": (result.get("attributes_lines") or [])[:60],
                "hint": result.get("hint"),
            }
            # ужатие — только для модели: что не поместилось, явно посчитано и указано, как открыть «следующую страницу»
            fn = result.get("full_name") or ""
            cuts: list[str] = []
            for key, cap, nxt in (
                ("attributes_lines", 60, f"list_object_attributes(full_name='{fn}', offset=60)"),
                ("tabular_sections", 20, f"describe_object(full_name='{fn}') → tabular_sections / list_object_attributes(include_tabular=true)"),
                ("forms", 30, f"describe_object(full_name='{fn}', form_name=<имя формы>)"),
                ("templates", 40, f"explain_config(request='макет <имя> объекта {fn}')"),
            ):
                total = len(result.get(key) or [])
                if total > cap:
                    slim[f"{key}_total"] = total
                    cuts.append(f"{key}: показано {cap} из {total} — остальное: {nxt}")
            if cuts:
                slim["truncated_for_context"] = True
                slim["not_all_shown"] = (
                    "Это НЕ полный список (сокращён только для экономии контекста): " + "; ".join(cuts)
                    + ". Если нужного нет среди показанного — запроси следующую порцию, а не отвечай «нет такого»."
                )
            # значения перечисления / измерения / ресурсы / регистраторы / состав подсистемы — суть ответа
            for k in ("enum_values", "values", "dimensions", "resources", "registrars", "registrars_total",
                      "content", "subsystems", "register_records", "commands",
                      "properties", "predefined", "predefined_total", "predefined_note"):
                if result.get(k):
                    slim[k] = result[k]
            slim = {k: v for k, v in slim.items() if v is not None}
            raw = compact_json(slim, self.tool_result_max_chars, tool_name=tool_name)
            try:
                return json.loads(raw)
            except Exception:
                return slim
        raw = compact_json(
            result, self.tool_result_max_chars, tool_name=tool_name
        )
        if len(raw) < self.tool_result_max_chars and '"_truncated"' not in raw:
            return result
        try:
            return json.loads(raw)
        except Exception:
            return {"_compact_text": raw}

    def openai_tools(self, only: "frozenset[str] | set[str] | None" = None) -> list[dict[str, Any]]:
        """Схема для LLM и MCP list_tools: без алиасов (HIDDEN_FROM_AGENT). only — подмножество по категории."""
        out: list[dict[str, Any]] = []
        for t in TOOL_DEFINITIONS:
            name = ((t.get("function") or {}).get("name") or "")
            if not is_agent_visible_tool(name):
                continue
            if only is not None and name not in only:
                continue
            out.append(_with_short_description(t))
        return out


def _with_short_description(t: dict[str, Any]) -> dict[str, Any]:
    """Краткое описание для модели (tool_descriptions), параметры — как в TOOL_DEFINITIONS."""
    try:
        from tools.tool_descriptions import DESCRIPTIONS
    except Exception:
        return t
    f = t.get("function") or {}
    d = DESCRIPTIONS.get(f.get("name") or "")
    if not d:
        return t
    return {**t, "function": {**f, "description": d}}
