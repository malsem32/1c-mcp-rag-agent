# -*- coding: utf-8 -*-
"""
Единый роутер intent → tool (один порядок правил для всех потребителей).

Потребители:
  - preflight.plan_preflight  — авто-вызов tool до LLM (только decision с tool);
  - route_intent.route_user_message — route/prefer_tool (fallback build_report);
  - build_report / resolve_object — перенаправление на подходящий tool;
  - agent.loop — флаги need_* для подсказок LLM (compute_hints).

Имена объектов CF здесь не появляются: только структура фразы и предикаты is_*_question.
"""

from __future__ import annotations

import re
import sys
from typing import Any

from tools.route_intent import (
    ROUTE_ACCESS,
    ROUTE_ATTR,
    ROUTE_CODE,
    ROUTE_CONN,
    ROUTE_DATA,
    ROUTE_EVENTLOG,
    ROUTE_HELP,
    ROUTE_META,
    ROUTE_MOVEMENTS,
    ROUTE_SESSIONS,
    ROUTE_STRUCTURE,
)


def _norm(s: str) -> str:
    return (s or "").lower().replace("ё", "е")


def _mentions_form_ui(pl: str) -> bool:
    """
    UI-форма объекта 1С — не «сформируй отчёт» / «платформа».
    """
    pl = _norm(pl)
    if not pl:
        return False
    # явное слово «форма/формы/…»
    if re.search(r"(?<![а-яёa-z])форм(?:а|ы|е|у|ой|ами|ах)(?![а-яёa-z])", pl):
        return True
    # «форм модуль» / «модуль форм» без сформ/платформ
    if re.search(r"сформ|платформ|информац", pl):
        return False
    return bool(re.search(r"форм\s|модул\w*\s+форм|элемент\w*\s+форм", pl))


def _phrase_has_object_hint(phrase: str) -> bool:
    """Есть ли в фразе намёк на конкретный объект (PascalCase / Документ.Имя)."""
    raw = (phrase or "").strip()
    if not raw:
        return False
    pl = _norm(raw)
    if re.search(
        r"\b(документ|справочник|регистр|обработк|отчет|отчёт)\s*\.?\s*[a-zа-яё_]{3,}",
        pl,
    ):
        return True
    try:
        from tools.phrase_struct import has_pascal_identifier

        return bool(has_pascal_identifier(raw))
    except Exception:
        return False


def _is_meta_structure_question(phrase: str) -> bool:
    """Реквизиты / структура / формы / макеты / события — не data-отчёт."""
    raw = phrase or ""
    try:
        from tools.phrase_struct import speech_norm

        speech = speech_norm(raw)
    except Exception:
        speech = _norm(raw)
    pl = _norm(raw)
    if not speech and not pl:
        return False
    struct_markers = (
        "реквизит",
        "структур",
        "атрибут",
        "табличн",
        "измерен",
        "ресурс",
        "какие поля",
        "какие реквиз",
        "все поля",
        "все реквиз",
        "состав документ",
        "описание объект",
        "макет",
        "печатн",
        "событ",
        "элемент",
        "команд",
        "layout",
        "template",
    )
    # data-маркеры только в речи (PascalCase вырезан: УсловияПродаж ≠ «продаж»)
    if any(
        x in speech
        for x in (
            "сколько",
            "сумм",
            "продаж",
            "выручк",
            "excel",
            "эксель",
            "за 20",
            "остатк",
            "задолжен",
        )
    ) and not any(x in speech or x in pl for x in struct_markers) and not _mentions_form_ui(
        raw
    ):
        return False
    if any(x in speech or x in pl for x in struct_markers) or _mentions_form_ui(raw):
        return True
    # «какие есть документы / найди документ / какие документы про …»
    hay = speech or pl
    if re.search(
        r"(какие\s+есть\s+документ|какие\s+документ|найди\s+документ|"
        r"есть\s+ли\s+документ|документы\s+про\s+|документ\w*\s+про\s+|"
        r"какие\s+справочник|найди\s+справочник|какие\s+есть\s+объект)",
        hay,
    ):
        return True
    return False


def _is_short_structure_followup(phrase: str) -> bool:
    """Короткий follow-up без имени объекта: «какие поля в макете», «события формы»."""
    pl = _norm(phrase)
    if not pl or len(pl) > 90:
        return False
    # нет явного объекта (документ/справочник + имя) — опора на контекст сессии
    if re.search(
        r"\b(документ|справочник|регистр|обработк|отчет|отчёт)\s*\.?\s*[a-zа-яё_]{3,}",
        pl,
    ):
        return False
    # PascalCase / идентификатор объекта в фразе — не короткий follow-up
    try:
        from tools.phrase_struct import has_pascal_identifier

        if has_pascal_identifier(phrase):
            return False
    except Exception:
        pass
    return any(
        x in pl
        for x in (
            "макет",
            "печатн",
            "событ",
            "элемент",
            "реквизит",
            "какие поля",
            "какие полях",
            "что выводит",
            "что в макет",
            "layout",
            "template",
            "команд",
        )
    ) or _mentions_form_ui(pl)


def _rules(
    phrase: str,
    *,
    context_full_name: str = "",
) -> dict[str, Any] | None:
    """
    Единый упорядоченный список правил (порядок AGENTS: code / fill / attr ДО build_report).

    Возвращает:
      tool, args, confidence (0..1), reason, format (manager|facts)
      либо {tool: None, route: code} — «это код, но tool выберет LLM».
    None = правило не найдено.
    """
    raw = (phrase or "").strip()
    if not raw or len(raw) < 2:
        return None
    pl = _norm(raw)
    ctx_fn = (context_full_name or "").strip()

    # --- что умеешь / команды / help (раньше data!) ---
    try:
        from tools.agent_capabilities import is_capabilities_question

        if is_capabilities_question(raw):
            return {
                "tool": "agent_capabilities",
                "args": {"short": False},
                "confidence": 0.97,
                "reason": "каталог возможностей / команды",
                "format": "manager",
            }
    except Exception:
        pass

    # --- внешний .epf/.erf (не Qdrant CF) ---
    try:
        from tools.epf_inspect import (
            extract_external_path,
            is_external_file_question,
        )

        if is_external_file_question(raw):
            ep = extract_external_path(raw)
            return {
                "tool": "inspect_external_processing",
                "args": {"path": ep, "request": raw, "max_hits": 12},
                "confidence": 0.96,
                "reason": "внешняя обработка/отчёт — файл пользователя",
                "format": "facts",
            }
    except Exception:
        pass

    # --- высокоточные детекторы (структура фразы, без имён CF) — см. router_detect ---
    try:
        from tools.router_detect import detect

        det = detect(raw, context_full_name=ctx_fn)
        if det:
            return det
    except Exception as exc:
        print(f"[router] detect skip: {exc}", file=sys.stderr)

    # --- код/логика + PascalCase (ДО data/code_help) — структурно, без CF-имён ---
    try:
        from tools.phrase_struct import (
            has_pascal_identifier,
            is_code_intent,
            is_trace_intent,
            longest_pascal_identifier,
        )

        if is_code_intent(raw) and has_pascal_identifier(raw):
            if (
                re.search(r"(выбрать|select)\b.{8,}", pl)
                and re.search(r"\b(из|from)\b", pl)
                and not re.search(r"антипаттерн|аудит|оптимиз|тормоз|в\s+цикле", pl)
            ):
                return {
                    "tool": "query_static_explain",
                    "args": {"request": raw},
                    "confidence": 0.91,
                    "reason": "текст ВЫБРАТЬ во фразе — статический разбор",
                    "format": "facts",
                }
            proc_name = longest_pascal_identifier(raw)
            req_arg = raw
            if ctx_fn and ctx_fn.split(".")[-1].lower() not in pl:
                req_arg = f"{raw} {ctx_fn}"
            want_trace = is_trace_intent(raw)
            is_meta_obj = False
            if proc_name and want_trace:
                try:
                    from tools.name_match import name_in_object_catalog

                    is_meta_obj = name_in_object_catalog(proc_name)
                except Exception:
                    is_meta_obj = False
            if proc_name and want_trace and not is_meta_obj:
                if re.search(
                    r"кто\s+вызыв|где\s+объявл|где\s+определен",
                    pl,
                ) and not re.search(
                    r"отслед|прослед|цепочк\w*\s+вызов|trace_calls|путь\s+вызов",
                    pl,
                ):
                    return {
                        "tool": "proc_index",
                        "args": {
                            "request": req_arg,
                            "procedure": proc_name,
                            "limit": 20,
                        },
                        "confidence": 0.9,
                        "reason": "объявление/кто вызывает процедуру (Qdrant)",
                        "format": "facts",
                    }
                return {
                    "tool": "trace_calls",
                    "args": {
                        "request": req_arg,
                        "procedure": proc_name,
                        "depth": 2,
                    },
                    "confidence": 0.93,
                    "reason": "код: трассировка вызовов процедуры",
                    "format": "facts",
                }
            if proc_name and re.search(
                r"где\s+объявл|где\s+определен|кто\s+вызыв|где\s+вызыва",
                pl,
            ) and not re.search(
                r"отслед|прослед|цепочк\w*\s+вызов",
                pl,
            ):
                return {
                    "tool": "proc_index",
                    "args": {
                        "request": req_arg,
                        "procedure": proc_name,
                        "limit": 20,
                    },
                    "confidence": 0.9,
                    "reason": "объявление процедуры (Qdrant proc_names)",
                    "format": "facts",
                }
            return {
                "tool": "inspect_code",
                "args": {"request": req_arg, "max_hits": 12},
                "confidence": 0.9,
                "reason": "код/логика объекта или модуля (inspect)",
                "format": "facts",
            }
    except Exception as exc:
        print(f"[preflight] code_intent skip: {exc}", file=sys.stderr)

    # --- где объявлена / кто вызывает (не цепочка вниз) ---
    if re.search(
        r"где\s+объявл|где\s+определен\w*\s+процедур|"
        r"кто\s+вызыв|где\s+вызыва",
        pl,
    ) and not re.search(
        r"отслед|прослед|цепочк\w*\s+вызов|подписк",
        pl,
    ):
        return {
            "tool": "proc_index",
            "args": {"request": raw, "limit": 20},
            "confidence": 0.88,
            "reason": "индекс процедуры (объявление + callers)",
            "format": "facts",
        }

    # --- аудит запросов / «тормозит запрос» ДО справки синтаксиса ---
    try:
        from tools.query_antipatterns import is_query_audit_question

        if is_query_audit_question(raw):
            return {
                "tool": "audit_query_code",
                "args": {"request": raw, "max_findings": 40},
                "confidence": 0.92,
                "reason": "аудит антипаттернов запросов 1С",
                "format": "facts",
            }
    except Exception:
        pass

    # --- статический разбор ВЫБРАТЬ (есть текст запроса, не антипаттерны) ---
    if (
        re.search(r"(разбер|объясн|что\s+делает|из\s+каких\s+таблиц|"
                  r"параметр\w*\s+запрос|статическ)", pl)
        and re.search(r"(выбрать|select)\b.{8,}", pl)
        and not re.search(r"антипаттерн|аудит|оптимиз|тормоз|в\s+цикле", pl)
    ):
        return {
            "tool": "query_static_explain",
            "args": {"request": raw},
            "confidence": 0.9,
            "reason": "статический разбор текста ВЫБРАТЬ (без execute)",
            "format": "facts",
        }

    # --- синтаксис ВЫБРАТЬ / BSL / функции платформы (до excel/report!) ---
    try:
        from tools.route_intent import is_platform_help_question

        if is_platform_help_question(raw):
            # query functions vs general code help
            if any(
                x in pl
                for x in (
                    "выбрать",
                    "группир",
                    "запрос",
                    "добавитькдате",
                    "началопериод",
                    "язык запрос",
                    "функци",
                    "соединен",
                )
            ):
                return {
                    "tool": "query_syntax_help",
                    "args": {"topic": "group" if "группир" in pl else ""},
                    "confidence": 0.94,
                    "reason": "синтаксис языка запросов 1С",
                    "format": "facts",
                }
            return {
                "tool": "code_help",
                "args": {"query": raw},
                "confidence": 0.9,
                "reason": "справка BSL / платформа",
                "format": "facts",
            }
    except Exception:
        pass

    # --- dump: форма / ПФ / HTTP / общие реквизиты / план обмена (до describe_object) ---
    if re.search(
        r"событи\w*\s+форм|обработчик\w*\s+форм|"
        r"присозданиинасервере|oncreateatserver",
        pl,
    ) and not re.search(r"подписк", pl):
        args_fe: dict[str, Any] = {"request": raw, "limit": 80}
        if ctx_fn:
            args_fe["full_name"] = ctx_fn
        return {
            "tool": "form_event_map",
            "args": args_fe,
            "confidence": 0.92,
            "reason": "события Form.xml (dump)",
            "format": "facts",
        }
    if re.search(
        r"печатн\w*\s+форм|параметр\w*\s+макет|макет\w*\s+печат|"
        r"пф_|print\s*form|detailparameter",
        pl,
    ):
        args_pf: dict[str, Any] = {"request": raw, "limit": 80}
        if ctx_fn:
            args_pf["full_name"] = ctx_fn
        return {
            "tool": "print_form_bind",
            "args": args_pf,
            "confidence": 0.9,
            "reason": "параметры макета ПФ (Template.xml)",
            "format": "facts",
        }
    if re.search(r"http.?сервис|httpservice|url.?шаблон", pl):
        return {
            "tool": "http_service_map",
            "args": {"request": raw},
            "confidence": 0.9,
            "reason": "HTTP-сервис из dump HTTPServices",
            "format": "facts",
        }
    if re.search(
        r"общ\w*\s+реквизит|commonattribute|"
        r"определяем\w*\s+тип|definedtype",
        pl,
    ):
        return {
            "tool": "common_attr_and_types",
            "args": {"request": raw, "limit": 40},
            "confidence": 0.9,
            "reason": "общий реквизит / определяемый тип (dump)",
            "format": "facts",
        }
    if re.search(r"план\w*\s+обмен|exchange\s*plan", pl) and "счет" not in pl:
        return {
            "tool": "exchange_composition",
            "args": {"request": raw, "limit": 80},
            "confidence": 0.9,
            "reason": "состав плана обмена (не план счетов)",
            "format": "facts",
        }

    # --- мета: реквизиты / структура / формы / макеты (до build_report!) ---
    if _is_meta_structure_question(raw) or (
        ctx_fn and _is_short_structure_followup(raw)
    ):
        want_form = any(
            x in pl
            for x in (
                "событ",
                "элемент",
                "layout",
                "кнопк",
                "при нажат",
                "обработчик",
            )
        ) and (
            _mentions_form_ui(pl)
            or any(x in pl for x in ("элемент", "событ", "кнопк"))
        )
        want_struct = any(
            x in pl
            for x in (
                "реквизит",
                "структур",
                "атрибут",
                "табличн",
                "измерен",
                "ресурс",
                "какие поля",
                "все поля",
                "все реквиз",
                "макет",
                "печатн",
                "команд",
                "template",
            )
        ) or _mentions_form_ui(pl) or _is_short_structure_followup(raw)

        # follow-up без объекта + есть контекст → describe_object(full_name=…)
        if ctx_fn and (
            _is_short_structure_followup(raw)
            or (want_struct and not _phrase_has_object_hint(raw))
        ):
            if want_form:
                return {
                    "tool": "describe_form",
                    "args": {"full_name": ctx_fn, "form_name": ""},
                    "confidence": 0.93,
                    "reason": f"форма/события объекта из контекста ({ctx_fn})",
                    "format": "facts",
                }
            return {
                "tool": "describe_object",
                "args": {
                    "full_name": ctx_fn,
                    "request": raw,
                    "include_form_layout": True,
                },
                "confidence": 0.94,
                "reason": f"структура/макеты объекта из контекста ({ctx_fn})",
                "format": "facts",
            }

        if want_struct or want_form:
            args: dict[str, Any] = {"request": raw}
            if ctx_fn and _is_short_structure_followup(raw):
                args["full_name"] = ctx_fn
            return {
                "tool": "describe_form" if want_form and ctx_fn else "describe_object",
                "args": (
                    {"full_name": ctx_fn, "form_name": ""}
                    if want_form and ctx_fn
                    else args
                ),
                "confidence": 0.92,
                "reason": "структура/реквизиты/макеты объекта",
                "format": "facts",
            }
        return {
            "tool": "resolve_object",
            "args": {"request": raw, "object_type": "Все", "limit": 12},
            "confidence": 0.9,
            "reason": "поиск объектов metadata",
            "format": "facts",
        }

    # --- связь / сеансы / журнал (ранний ops) ---
    if re.search(r"(проверь\s+связ|test\s*connection|связь\s+с\s+1|подключен\w*\s+к\s+1)", pl):
        return {
            "tool": "test_connection",
            "args": {},
            "confidence": 0.95,
            "reason": "проверка связи с 1С",
            "format": "facts",
        }
    if re.search(
        r"(кто\s+(сейчас\s+)?(в\s+базе|сидит)|активн\w*\s+(пользовател|сеанс)|"
        r"список\s+сеанс|кто\s+в\s+1с)",
        pl,
    ):
        return {
            "tool": "list_active_sessions",
            "args": {},
            "confidence": 0.92,
            "reason": "активные сеансы",
            "format": "manager",
        }
    if re.search(
        r"(журнал\s+регистрац|ошибк\w*\s+в\s+журнал|event\s*log|"
        r"ошибк\w*\s+из\s+журнал)",
        pl,
    ):
        return {
            "tool": "analyze_event_log",
            "args": {"request": raw},
            "confidence": 0.9,
            "reason": "журнал регистрации",
            "format": "facts",
        }

    # --- внедрение managed locks ДО movements (оба могут матчить «при проведении») ---
    try:
        from tools.recommend_locks import is_lock_migration_question

        if is_lock_migration_question(raw):
            return {
                "tool": "recommend_managed_locks",
                "args": {"request": raw, "max_registers": 15},
                "confidence": 0.93,
                "reason": "внедрение/рекомендации управляемых блокировок",
                "format": "facts",
            }
    except Exception:
        pass

    # --- fill / why empty ДО report/data (AGENTS: fill before build_report) ---
    try:
        from tools.explain_fill import (
            is_fill_reason_question,
            is_posting_error_question,
        )
        from tools.attr_query import is_attr_filter_question as _is_attr

        attr_list = _is_attr(raw)
        # «блокировки при проведении» → analyze_locks, не fill
        # «какие X не проведены» — список по реквизиту, не «почему не проводится»
        if "блокир" not in pl and is_fill_reason_question(raw) and not attr_list:
            return {
                "tool": "explain_fill",
                "args": {"request": raw},
                "confidence": 0.9,
                "reason": "заполнение поля / почему пусто",
                "format": "facts",
            }
        if (
            "блокир" not in pl
            and is_posting_error_question(raw)
            and not attr_list
            and not re.search(
                r"по\s+каким\s+регистр|какие\s+движен|управляем|внедр|куда\s+ставить|managed",
                pl,
            )
        ):
            return {
                "tool": "explain_fill",
                "args": {"request": raw},
                "confidence": 0.85,
                "reason": "ошибка/отказ проведения",
                "format": "facts",
            }
    except Exception as exc:
        print(f"[preflight] fill_intent skip: {exc}", file=sys.stderr)

    # --- список по реквизитам шапки (ДО report/linked: не путать с продажами) ---
    try:
        from tools.attr_query import is_attr_filter_question

        if is_attr_filter_question(raw):
            try:
                from tools.linked_query import is_debt_unpaid_question as _debt
            except Exception:
                _debt = lambda _p: False  # noqa: E731
            if not _debt(raw):
                return {
                    "tool": "query_by_attributes",
                    "args": {"request": raw, "limit": 50},
                    "confidence": 0.92,
                    "reason": "список объектов по реквизитам шапки (не агрегат)",
                    "format": "manager",
                }
    except Exception as exc:
        print(f"[preflight] attr_filter skip: {exc}", file=sys.stderr)

    # --- документ по номеру (шапка), не агрегат продаж ---
    if (
        re.search(
            r"документ\w*.{0,80}(?:№|номер[ауе]?\s+[a-zа-яё0-9])|"
            r"(?:№|номер[ауе]?\s+\d).{0,40}документ",
            pl,
        )
        and not re.search(r"пуст\w*\s+номер|без\s+номер", pl)
        and not _is_meta_structure_question(raw)
    ):
        args_dn: dict[str, Any] = {"request": raw, "limit": 5}
        if ctx_fn and ctx_fn.startswith("Документ."):
            args_dn["full_name"] = ctx_fn
        return {
            "tool": "document_by_number",
            "args": args_dn,
            "confidence": 0.88,
            "reason": "документ по номеру (один ВЫБРАТЬ шапки)",
            "format": "manager",
        }

    # --- сравнение периодов ДО report_builder (иначе один год вместо двух) ---
    try:
        from tools.report_format import wants_period_compare
        from tools.phrase_struct import is_code_intent, speech_norm

        if (
            wants_period_compare(raw)
            and not is_code_intent(raw)
            and not _is_meta_structure_question(raw)
        ):
            speech = speech_norm(raw)
            gb = ""
            if any(x in speech for x in ("контрагент", "клиент", "покупат")):
                gb = "контрагент"
            elif any(x in speech for x in ("номенклатур", "товар")):
                gb = "номенклатура"
            elif "склад" in speech:
                gb = "склад"
            return {
                "tool": "compare_periods",
                "args": {
                    "search_query": raw,
                    "period": raw,
                    "group_by": gb,
                    "metrics": "sum,count",
                },
                "confidence": 0.93,
                "reason": "сравнение периодов (два года / два месяца)",
                "format": "manager",
            }
    except Exception:
        pass

    # --- отчёт / excel / графики → AUTO build (без мастера по шагам) ---
    try:
        from tools.dialog_context import wants_export_existing
        from tools.report_builder import is_report_builder_question
        from tools.report_wizard import (
            wants_excel_chart_wizard,
            wants_step_wizard,
        )
        from tools.analyst_core import wants_excel_file as _wex
        from tools.phrase_struct import speech_norm

        speech = speech_norm(raw)
        step = wants_step_wizard(raw)
        wants_report = (
            step
            or wants_export_existing(raw)
            or wants_excel_chart_wizard(raw)
            or is_report_builder_question(raw)
            or (
                _wex(raw)
                and any(
                    x in speech
                    for x in (
                        "отчет",
                        "отчёт",
                        "продаж",
                        "остатк",
                        "поступлен",
                        "задолжен",
                        "график",
                        "диаграмм",
                    )
                )
            )
        )
        if wants_report:
            return {
                "tool": "report_builder",
                "args": {
                    "request": raw,
                    "to_excel": True,
                    "limit": 80,
                    "skip_wizard": not step,  # default AUTO
                    "dry_run": False,
                },
                "confidence": 0.97 if not step else 0.93,
                "reason": (
                    "мастер по шагам (явно)"
                    if step
                    else "auto report+excel+charts (сам найдёт данные)"
                ),
                "format": "manager",
            }
    except Exception:
        pass

    # --- 4 бизнес-блока + аналитика (до margin, чтобы «рост прибыли» не ушёл только в margin) ---
    try:
        from tools.analyst_core import (
            detect_analyst_mode,
            wants_excel_file,
        )

        amode = detect_analyst_mode(raw)
        business = {
            "assortment",
            "reserves",
            "profit",
            "customers",
            "abc",
            "variance",
            "aging",
            "funnel",
            "anomaly",
            "turnover",
            "plan_fact",
            "drill",
            "multi",
        }
        try:
            from tools.linked_query import is_stock_balances_question as _stk0
        except Exception:
            _stk0 = lambda _p: False  # noqa: E731
        # «остатки по складам» — просто срез остатков, не multi-аналитика
        if amode == "multi" and _stk0(raw):
            amode = ""
        if amode in business:
            return {
                "tool": "analyst_report",
                "args": {
                    "request": raw,
                    "mode": amode,
                    "to_excel": wants_excel_file(raw),
                    "limit": 80,
                },
                "confidence": 0.94 if amode in (
                    "assortment",
                    "reserves",
                    "profit",
                    "customers",
                ) else 0.91,
                "reason": f"аналитика mode={amode}",
                "format": "manager",
            }
        if amode == "dashboard" and wants_excel_file(raw):
            return {
                "tool": "analyst_report",
                "args": {
                    "request": raw,
                    "mode": "dashboard",
                    "to_excel": True,
                    "limit": 80,
                },
                "confidence": 0.9,
                "reason": "аналитический excel/дашборд",
                "format": "manager",
            }
    except Exception:
        pass

    # --- margin ---
    try:
        from tools.margin_report import is_margin_question

        if is_margin_question(raw):
            return {
                "tool": "margin_report",
                "args": {"request": raw, "period": "", "limit": 40},
                "confidence": 0.92,
                "reason": "маржа/выручка/себестоимость",
                "format": "manager",
            }
    except Exception:
        pass

    # --- debt / multi-source / остатки / обороты помесячно ---
    try:
        from tools.linked_query import (
            is_linked_or_debt_data_question,
            is_stock_balances_question,
            _wants_period_turnovers,
        )
        from tools.linked_query import is_debt_unpaid_question

        if (
            is_stock_balances_question(raw)
            or is_linked_or_debt_data_question(raw)
            or is_debt_unpaid_question(raw)
            # «по месяцам» без оборотов регистра — шаг времени отчёта (report_builder)
            or ("оборот" in pl and _wants_period_turnovers(raw))
        ):
            return {
                "tool": "linked_query",
                "args": {
                    "request": raw,
                    "execute": True,
                    "limit": 40,
                },
                "confidence": 0.93 if is_stock_balances_question(raw) else 0.9,
                "reason": (
                    "остатки на складах (на сейчас, без периода)"
                    if is_stock_balances_question(raw)
                    else "задолженность/остатки/обороты/несколько источников"
                ),
                "format": "manager",
            }
    except Exception:
        pass

    # --- movements / posting registers (после recommend_locks!) ---
    try:
        from tools.object_movements import is_movements_or_posting_question

        if is_movements_or_posting_question(raw) and "блокир" not in pl:
            # не fill/error posting; не managed locks
            if re.search(
                r"почему\s+не\s+провед|ошибк\w*\s+провед",
                pl,
            ):
                return {
                    "tool": "explain_fill",
                    "args": {"request": raw},
                    "confidence": 0.75,
                    "reason": "ошибка/не проводится",
                    "format": "facts",
                }
            from tools.object_movements import is_posting_matrix_question

            if is_posting_matrix_question(raw):
                return {
                    "tool": "report_builder",
                    "args": {"request": raw, "skip_wizard": True},
                    "confidence": 0.9,
                    "reason": "сводка «документ → регистры» по всей CF",
                    "format": "manager",
                }
            return {
                "tool": "analyze_object_movements",
                "args": {"request": raw},
                "confidence": 0.9,
                "reason": "регистры проведения / движения",
                "format": "facts",
            }
    except Exception:
        pass

    # --- module-of-object: блокировки / оптимизация (контекст документа) ---
    # «проанализируй модуль объекта… блокировки/оптимизировать» → inspect_code
    # (ObjectModule + common modules), НЕ глобальный analyze_locks без object_hint
    try:
        module_focus = bool(
            re.search(
                r"(проанализир\w*|разбер\w*|изуч\w*|посмотри).{0,50}модул|"
                r"модул\w*\s+объект|код\s+модул|модул\w*\s+проведен|"
                r"что\s+можно\s+оптимиз|где\s+могут\s+возникать\s+блокир|"
                r"моменты\s+где\s+.*блокир",
                pl,
            )
        )
        want_opt = bool(
            re.search(r"оптимиз|ускор|тормоз|производительн|узк\w*\s+мест", pl)
        )
        from tools.analyze_locks import is_locks_or_contention_question

        want_lock_q = is_locks_or_contention_question(raw)
        if module_focus and (want_lock_q or want_opt):
            leaf = ""
            if ctx_fn:
                leaf = ctx_fn.split(".")[-1]
            req_arg = raw
            if leaf and leaf.lower() not in pl:
                req_arg = f"{raw} {ctx_fn} {leaf}"
            return {
                "tool": "inspect_code",
                "args": {"request": req_arg, "max_hits": 15},
                "confidence": 0.92,
                "reason": "анализ модуля объекта: блокировки/оптимизация (multi-module)",
                "format": "facts",
            }
    except Exception:
        pass

    # --- managed locks: где уже стоят в коде ---
    try:
        from tools.analyze_locks import is_locks_or_contention_question

        if is_locks_or_contention_question(raw):
            # «по каким регистрам» — movements, не locks
            if re.search(
                r"по\s+каким\s+регистр|какие\s+движен|что\s+пишет\s+в\s+регистр",
                pl,
            ) and "блокир" not in pl:
                pass
            else:
                args: dict[str, Any] = {"request": raw, "max_hits": 40}
                if ctx_fn:
                    # сузить к документу из session memory
                    args["object_hint"] = ctx_fn.split(".")[-1]
                return {
                    "tool": "analyze_locks",
                    "args": args,
                    "confidence": 0.88,
                    "reason": "управляемые блокировки / тормоза (код)",
                    "format": "facts",
                }
    except Exception:
        pass

    # --- query antipatterns / audit (дубль: основной блок раньше platform_help) ---
    try:
        from tools.query_antipatterns import is_query_audit_question

        if is_query_audit_question(raw):
            return {
                "tool": "audit_query_code",
                "args": {"request": raw, "max_findings": 40},
                "confidence": 0.9,
                "reason": "аудит антипаттернов запросов 1С",
                "format": "facts",
            }
    except Exception:
        pass

    # --- security ---
    if re.search(
        r"(инъекц|выполнить\s*\(|вычислить\s*\(|eval\b|unsafe|"
        r"опасн\w*\s+код|scan_code|code\s*injection)",
        pl,
    ):
        return {
            "tool": "scan_code_injection",
            "args": {"limit": 30},
            "confidence": 0.85,
            "reason": "скан опасных конструкций",
            "format": "facts",
        }

    # --- event subscriptions (до inspect_code и до ролей) ---
    if re.search(
        r"подписк\w*\s+на\s+событ|кто\s+подписан|"
        r"event\s*subscription",
        pl,
    ):
        return {
            "tool": "explain_subscriptions",
            "args": {"request": raw, "limit": 40},
            "confidence": 0.9,
            "reason": "подписки на события (dump EventSubscriptions)",
            "format": "facts",
        }

    # --- RLS (шаблон ограничения, не список ролей) ---
    if re.search(
        r"(?<![a-zа-яё])rls(?![a-zа-яё])|"
        r"ограничен\w*\s+(доступа\s+)?на\s+уровне|"
        r"шаблон\w*\s+ограничен|restriction\s*template|"
        r"restrictionbycondition",
        pl,
    ):
        return {
            "tool": "explain_rls",
            "args": {"request": raw, "limit": 40},
            "confidence": 0.9,
            "reason": "RLS / restrictionByCondition из Rights.xml",
            "format": "facts",
        }

    # --- scheduled jobs ---
    if re.search(r"регламентн\w*\s+задан|scheduled\s*jobs?", pl):
        return {
            "tool": "describe_scheduled_job",
            "args": {"request": raw, "limit": 30},
            "confidence": 0.88,
            "reason": "регл. задание из dump ScheduledJobs",
            "format": "facts",
        }

    # --- roles / access (не ловить «правила/направления» по «прав») ---
    if re.search(
        r"(?<![а-яё])рол(?:ь|и|ей|ям|ями|ях)?(?![а-яё])|"
        r"(?<![а-яё])права(?![а-яё])|"
        r"доступ\s+к|кто\s+может",
        pl,
    ) and not re.search(
        r"(удал|измен|интерактив|interactive|delete|edit)",
        pl,
    ):
        # «это все роли / полный список ролей» — снова access_overview по фразе/сессии
        if re.search(
            r"(это\s+все|все\s+ли|полный\s+список|ещё\s+рол|все\s+роли\s+для|"
            r"только\s+эти|не\s+все\s+рол)",
            pl,
        ) or (
            re.search(r"(?<![а-яё])рол", pl)
            and re.search(r"(документ|справочник|отчет|отчёт|объект|для\s+этого)", pl)
        ):
            return {
                "tool": "access_overview",
                "args": {"request": raw},
                "confidence": 0.9,
                "reason": "роли/права на объект (или «это все?»)",
                "format": "facts",
            }
        if re.search(
            r"(какие\s+роли|права\s+на|доступ\s+к|роли\s+для|роли\s+на|"
            r"кто\s+имеет\s+доступ|права\s+документа|роли\s+документа)",
            pl,
        ):
            return {
                "tool": "access_overview",
                "args": {"request": raw},
                "confidence": 0.88,
                "reason": "роли/права на объект",
                "format": "facts",
            }

    # --- roles by right ---
    if re.search(r"(?<![а-яё])рол|(?<![а-яё])права(?![а-яё])", pl) and re.search(
        r"(удал|измен|интерактив|interactive|delete|edit|справочник)", pl
    ):
        try:
            from tools.roles_access import rights_object_type_from_phrase

            _rights_ot = rights_object_type_from_phrase(raw)
        except Exception:
            _rights_ot = ""
        return {
            "tool": "list_roles_by_right",
            "args": {
                "rights": "InteractiveDelete,Update,Edit,InteractiveSetDeletionMark,Delete",
                "object_type": _rights_ot,
                "limit": 80,
            },
            "confidence": 0.85,
            "reason": (
                f"роли с правом на {_rights_ot}"
                if _rights_ot
                else "роли с правом (тип из фразы / все классы)"
            ),
            "format": "facts",
        }

    # --- code / queries in module (в т.ч. follow-up «какие запросы есть») ---
    try:
        from tools.route_intent import is_code_config_question

        code_q = is_code_config_question(raw)
    except Exception:
        code_q = False

    # короткий follow-up про запросы при известном объекте
    short_queries = bool(
        re.search(r"какие\s+запрос|текст\s+запрос|запросы\s+есть|где\s+запрос", pl)
        and len(raw) < 80
    )
    if short_queries or (
        code_q
        and re.search(
            r"(откуда|как\s+устроен|логик|модул|форм|скд|текст\s+запрос|"
            r"какие\s+запрос|inspect)",
            pl,
        )
    ):
        req_arg = raw
        if short_queries and context_full_name:
            # «какие запросы есть» + контекст документа
            leaf = context_full_name.split(".")[-1]
            req_arg = f"какие запросы есть в {context_full_name} {leaf}"
        return {
            "tool": "inspect_code",
            "args": {"request": req_arg},
            "confidence": 0.9 if short_queries else 0.82,
            "reason": "код/запросы/СКД (ObjectModule + common modules)",
            "format": "facts",
        }
    if code_q:
        # movements already handled; other code → LLM (preflight не вызывает tool)
        return {"tool": None, "route": ROUTE_CODE, "reason": "код без явного tool"}

    # --- manager report (data) — не meta/structure ---
    try:
        from tools.report_format import is_manager_report_question

        if is_manager_report_question(raw) and not _is_meta_structure_question(raw):
            return {
                "tool": "build_report",
                "args": {"request": raw},
                "confidence": 0.82,
                "reason": "данные/отчёт менеджеру",
                "format": "manager",
            }
    except Exception:
        pass

    return None


# tool → категория route (совместимость с route_intent.ROUTE_*)
_TOOL_ROUTE: dict[str, str] = {
    "build_external_processing": "build_epf",
    "access_overview": ROUTE_ACCESS,
    "explain_rls": ROUTE_ACCESS,
    "list_roles_by_right": ROUTE_ACCESS,
    "query_by_attributes": ROUTE_ATTR,
    "analyze_locks": ROUTE_CODE,
    "audit_query_code": ROUTE_CODE,
    "describe_scheduled_job": ROUTE_CODE,
    "explain_fill": ROUTE_CODE,
    "explain_subscriptions": ROUTE_CODE,
    "find_callers": ROUTE_CODE,
    "http_service_map": ROUTE_CODE,
    "inspect_code": ROUTE_CODE,
    "inspect_external_processing": ROUTE_CODE,
    "proc_index": ROUTE_CODE,
    "query_static_explain": ROUTE_CODE,
    "read_skd_query": ROUTE_CODE,
    "recommend_managed_locks": ROUTE_CODE,
    "scan_code_injection": ROUTE_CODE,
    "search_code": ROUTE_CODE,
    "trace_calls": ROUTE_CODE,
    "test_connection": ROUTE_CONN,
    "analyst_report": ROUTE_DATA,
    "build_report": ROUTE_DATA,
    "compare_periods": ROUTE_DATA,
    "document_by_number": ROUTE_DATA,
    "linked_query": ROUTE_DATA,
    "margin_report": ROUTE_DATA,
    "report_builder": ROUTE_DATA,
    "analyze_event_log": ROUTE_EVENTLOG,
    "code_help": ROUTE_HELP,
    "query_syntax_help": ROUTE_HELP,
    "agent_capabilities": ROUTE_META,
    "common_attr_and_types": ROUTE_META,
    "exchange_composition": ROUTE_META,
    "manager_help": ROUTE_META,
    "resolve_object": ROUTE_META,
    "analyze_object_movements": ROUTE_MOVEMENTS,
    "list_active_sessions": ROUTE_SESSIONS,
    "describe_form": ROUTE_STRUCTURE,
    "describe_object": ROUTE_STRUCTURE,
    "form_event_map": ROUTE_STRUCTURE,
    "get_object_structure": ROUTE_STRUCTURE,
    "print_form_bind": ROUTE_STRUCTURE,
}


def classify(phrase: str, *, context_full_name: str = "") -> dict[str, Any] | None:
    """
    Решение роутера: {tool, args, confidence, reason, format, route}.
    tool=None — категория известна (code), конкретный tool выбирает LLM.
    None — ни одно правило не подошло.
    """
    d = _rules(phrase, context_full_name=context_full_name)
    if not d:
        return None
    out = dict(d)
    out.setdefault("route", _TOOL_ROUTE.get(str(out.get("tool") or ""), ROUTE_DATA))
    return out


def compute_hints(user_text: str) -> dict[str, bool]:
    """
    Флаги типа вопроса для подсказок LLM в agent.loop (nudge, если модель не вызвала нужный tool).
    Не выбирают tool сами — дополняют classify.
    """
    try:
        from tools.object_movements import (
            is_empty_code_paste,
            is_movements_or_posting_question,
        )
        from tools.route_intent import is_code_config_question
        from tools.linked_query import is_debt_unpaid_question
        from tools.explain_fill import (
            is_fill_reason_question,
            is_posting_error_question,
            is_status_question,
        )
    except Exception:
        is_movements_or_posting_question = lambda _p: False  # noqa: E731
        is_empty_code_paste = lambda _p: False  # noqa: E731
        is_code_config_question = lambda _p: False  # noqa: E731
        is_fill_reason_question = lambda _p: False  # noqa: E731
        is_debt_unpaid_question = lambda _p: False  # noqa: E731
        is_status_question = lambda _p: False  # noqa: E731
        is_posting_error_question = lambda _p: False  # noqa: E731

    need_movements = is_movements_or_posting_question(user_text)
    # fill/posting — не цеплять «оптимизация / запросы в цикле / цепочка»
    _ut = (user_text or "").lower().replace("ё", "е")
    _not_fill_context = bool(
        re.search(
            r"(оптимиз|запрос\w*\s+в\s+цикл|цепочк|trace|ускор|производительн|"
            r"тяжел\w+\s+в\s+провед|отразить|кто\s+вызыв)",
            _ut,
        )
    )
    need_fill = (
        not _not_fill_context
        and (
            is_fill_reason_question(user_text)
            or is_posting_error_question(user_text)
        )
    )
    need_debt = is_debt_unpaid_question(user_text)
    need_status = is_status_question(user_text)

    try:
        from tools.report_format import is_manager_report_question
        from tools.linked_query import is_linked_or_debt_data_question
        from tools.attr_query import is_attr_filter_question
        from tools.epf_inspect import is_external_file_question
    except Exception:
        is_manager_report_question = lambda _p: False  # noqa: E731
        is_linked_or_debt_data_question = lambda _p: False  # noqa: E731
        is_attr_filter_question = lambda _p: False  # noqa: E731
        is_external_file_question = lambda _p: False  # noqa: E731

    need_trace = bool(
        re.search(
            r"(прослед|цепочк|кто\s+вызыв|trace_calls|путь\s+вызов)",
            user_text or "",
            re.I,
        )
    )
    need_code = (
        is_code_config_question(user_text)
        or need_movements
        or need_fill
        or need_trace
    )
    need_report = (
        is_manager_report_question(user_text)
        or is_linked_or_debt_data_question(user_text)
        or need_debt
        or is_attr_filter_question(user_text)
    )
    need_attr = is_attr_filter_question(user_text) and not need_debt
    need_epf = is_external_file_question(user_text)
    if need_epf:
        need_code = True
        need_report = False
    # code / audit overrides report (кроме долга/остатков)
    try:
        from tools.query_antipatterns import is_query_audit_question

        need_query_audit = is_query_audit_question(user_text)
    except Exception:
        need_query_audit = False
    if need_query_audit:
        need_code = True
        need_report = False
    if need_code and not need_debt and not need_attr and not is_linked_or_debt_data_question(
        user_text
    ):
        need_report = False
    return {
        "movements": need_movements,
        "fill": need_fill,
        "debt": need_debt,
        "status": need_status,
        "trace": need_trace,
        "code": need_code,
        "report": need_report,
        "attr": need_attr,
        "epf": need_epf,
        "query_audit": need_query_audit,
        "empty_code_paste": bool(is_empty_code_paste(user_text)),
    }
