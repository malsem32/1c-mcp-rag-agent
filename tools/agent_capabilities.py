# -*- coding: utf-8 -*-
"""
Каталог возможностей агента (для «что умеешь?», «какие команды?», /help).

Не hardcode CF — только сценарии и tools. Имена объектов — из metadata текущей базы.
"""

from __future__ import annotations

import re
from typing import Any


def _norm(s: str) -> str:
    return (s or "").lower().replace("ё", "е").strip()


def is_capabilities_question(phrase: str) -> bool:
    """Пользователь просит рассказать, что умеет агент / какие команды."""
    pl = _norm(phrase)
    if not pl or len(pl) > 160:
        return False
    # «какие команды у справочника X / формы» — команды объекта, не меню агента
    if "команд" in pl and (
        re.search(r"(?<![а-яё])(у|формы?|документ\w*|справочник\w*|обработк\w*|отчет\w*|объект\w*)(?![а-яё])", pl)
        or re.search(r"[a-zа-яё][A-ZА-ЯЁ]", phrase or "")
    ):
        return False
    markers = (
        "что умеешь",
        "что ты умеешь",
        "что можешь",
        "что ты можешь",
        "какие команд",
        "какие есть команд",
        "список команд",
        "какие возможн",
        "твои возможн",
        "что ты делаешь",
        "чем можешь помочь",
        "чем поможешь",
        "help",
        "хелп",
        "справка по агент",
        "справка агент",
        "как с тобой",
        "как пользоваться",
        "как работать с тобой",
        "что спросить",
        "примеры вопрос",
        "примеры запрос",
        "capabilities",
        "what can you",
        "что доступно",
        "функции агент",
        "функционал агент",
        "твой функционал",
        "твое меню",
        "твоё меню",
        "меню команд",
        "меню агент",
        "инструкция агент",
        "инструкция по агент",
        "инструкция по работе с",
        "/help",
    )
    if any(m in pl for m in markers):
        return True
    if re.fullmatch(r"(help|\?|справка|команды|возможности)", pl):
        return True
    if pl in ("?", "??", "help!", "хелп"):
        return True
    if any(
        x in pl
        for x in (
            "расскажи о себе",
            "кто ты",
            "что ты такое",
            "представься",
            "твои навык",
        )
    ):
        return True
    return False


# Каталог для ответа менеджеру (RU). examples — разговорные фразы, без full_name CF.
CAPABILITIES: list[dict[str, Any]] = [
    {
        "id": "biz_assortment",
        "title": "Ассортимент и спрос",
        "examples": [
            "что продаётся а что нет",
            "хиты и неликвиды с остатками",
            "анализ ассортимента за прошлый год",
        ],
        "tools": ["analyst_report mode=assortment"],
    },
    {
        "id": "biz_reserves",
        "title": "Скрытые резервы (где заморожены деньги)",
        "examples": [
            "где заморожены деньги",
            "склад + дебиторка + просрочка",
            "скрытые резервы оборотного капитала",
        ],
        "tools": ["analyst_report mode=reserves"],
    },
    {
        "id": "biz_profit",
        "title": "Маржа и прибыль",
        "examples": [
            "как заработать больше",
            "где низкая маржа",
            "маржа по менеджерам за прошлый год",
        ],
        "tools": ["analyst_report mode=profit", "margin_report"],
    },
    {
        "id": "biz_customers",
        "title": "Клиенты (VIP, отток)",
        "examples": [
            "vip клиенты и кто отвалился",
            "кто не покупал",
            "клиентская аналитика за два года",
        ],
        "tools": ["analyst_report mode=customers"],
    },
    {
        "id": "biz_suppliers",
        "title": "Поставщики (ценные / слабые)",
        "examples": [
            "какие поставщики самые ценные а какие не очень",
            "ABC по поставщикам",
            "топ поставщиков по закупкам",
        ],
        "tools": ["analyst_report mode=suppliers"],
    },
    {
        "id": "data",
        "title": "Отчёты и данные из 1С",
        "examples": [
            "продажи по контрагентам за прошлый год",
            "сколько реализаций за январь",
            "список номенклатуры",
            "документы по контрагенту …",
            "в каких заказах ожидается оплата",
            "какие документы не проведены",
        ],
        "tools": ["build_report", "query_by_attributes", "compare_periods", "report_builder"],
    },
    {
        "id": "compare",
        "title": "Сравнение периодов",
        "examples": [
            "сравни продажи за этот и прошлый год",
            "кто вырос кто упал",
            "сравни два года в excel с графиками",
        ],
        "tools": ["compare_periods", "analyst_report"],
    },
    {
        "id": "analyst",
        "title": "Аналитика (ABC, aging, воронка…)",
        "examples": [
            "abc по контрагентам",
            "просроченные долги по корзинам",
            "воронка продаж",
            "оборачиваемость",
        ],
        "tools": ["analyst_report"],
    },
    {
        "id": "excel",
        "title": "Красивые отчёты: Excel, таблицы, графики",
        "examples": [
            "продажи по контрагентам за прошлый год в excel с графиками",
            "красивый отчёт: топ-15 номенклатуры, таблица и столбчатый график",
            "сравни два года в excel с графиками",
            "остатки на складах — выгрузка с итогами",
            "круг (pie) по долям контрагентов",
        ],
        "tools": ["build_report", "report_builder", "compare_periods", "analyst_report"],
        "tips": [
            "В чате: markdown-таблица + Итого",
            "В Excel: цветная шапка, зебра, автофильтр, строка Итого, график",
            "Слова-триггеры: excel, график, красивый отчёт, dashboard, pie/круг",
        ],
    },
    {
        "id": "debt",
        "title": "Долги, остатки, регистры",
        "examples": [
            "сколько товаров на складах осталось",
            "задолженность клиентов",
            "обороты расчётов помесячно",
        ],
        "tools": ["linked_query"],
    },
    {
        "id": "followup",
        "title": "Уточнения в диалоге",
        "examples": [
            "только [контрагент из прошлого ответа]",
            "а теперь за прошлый год",
            "за все время",
            "документы по этому контрагенту",
        ],
        "tools": ["session memory", "drill-down"],
    },
    {
        "id": "meta",
        "title": "Метаданные и структура",
        "examples": [
            "какие документы есть про …",
            "все реквизиты документа …",
            "структура объекта …",
        ],
        "tools": ["resolve_object", "describe_object", "get_object_structure"],
    },
    {
        "id": "movements",
        "title": "Проведение / регистры документа",
        "examples": [
            "по каким регистрам проводится …",
            "какие движения пишет документ",
        ],
        "tools": ["analyze_object_movements"],
    },
    {
        "id": "code",
        "title": "Код конфигурации",
        "examples": [
            "откуда берётся сумма в …",
            "как устроена форма документа …",
            "проследи вызовы",
            "почему не заполнился реквизит …",
        ],
        "tools": ["inspect_code", "search_code", "trace_calls", "explain_fill"],
    },
    {
        "id": "subscriptions_jobs_rls",
        "title": "Подписки, RLS, регл. задания",
        "examples": [
            "кто подписан на ПередЗаписью …",
            "какой RLS на справочник/документ",
            "какое регл. задание, метод, расписание",
        ],
        "tools": [
            "explain_subscriptions",
            "explain_rls",
            "describe_scheduled_job",
        ],
    },
    {
        "id": "dump_extra",
        "title": "Формы, HTTP, обмен, документ по номеру",
        "examples": [
            "события формы документа …",
            "параметры печатной формы …",
            "HTTP-сервис / состав плана обмена / общий реквизит",
        ],
        "tools": [
            "form_event_map",
            "print_form_bind",
            "http_service_map",
            "common_attr_and_types",
            "exchange_composition",
            "document_by_number",
            "query_static_explain",
            "proc_index",
        ],
    },
    {
        "id": "epf",
        "title": "Внешняя обработка / отчёт (файл)",
        "examples": [
            "разбери эту .epf — как работает",
            "какие запросы во внешней обработке",
            "какие реквизиты и формы в скинутом файле",
        ],
        "tools": ["inspect_external_processing"],
    },
    {
        "id": "epf_build",
        "title": "Сборка внешней обработки (.epf) и отчёта (.erf)",
        "examples": [
            "сделай обработку, которая выгружает остатки в файл",
            "создай epf с реквизитами «Период» и «Склад» и кнопкой выполнить",
            "сделай внешний отчёт (erf) по остаткам с группировкой по складам",
        ],
        "tools": ["build_external_processing"],
    },
    {
        "id": "locks",
        "title": "Управляемые блокировки",
        "examples": [
            "где в коде стоят управляемые блокировки",
            "куда ставить managed locks при проведении …",
        ],
        "tools": ["analyze_locks", "recommend_managed_locks"],
    },
    {
        "id": "access",
        "title": "Права и роли",
        "examples": [
            "какие роли могут удалять справочники",
            "права пользователя",
            "список ролей",
        ],
        "tools": ["access_overview", "list_roles_by_right", "list_roles", "list_users"],
    },
    {
        "id": "ops",
        "title": "Сеансы и журнал",
        "examples": [
            "кто сейчас в базе",
            "ошибки в журнале регистрации",
        ],
        "tools": ["list_active_sessions", "analyze_event_log"],
    },
    {
        "id": "query_help",
        "title": "Синтаксис запросов / BSL",
        "examples": [
            "как написать выбрать с группировкой",
            "функция ДОБАВИТЬКДАТЕ",
        ],
        "tools": ["query_syntax_help", "code_help"],
    },
    {
        "id": "query_audit",
        "title": "Аудит запросов (антипаттерны)",
        "examples": [
            "проверь запросы на антипаттерны",
            "как лучше писать запросы 1С",
            "найди запрос в цикле в модуле …",
        ],
        "tools": ["audit_query_code", "query_syntax_help topic=antipatterns"],
    },
    {
        "id": "conn",
        "title": "Связь с 1С",
        "examples": ["проверь связь", "test connection"],
        "tools": ["test_connection"],
    },
]


CLI_COMMANDS: list[dict[str, str]] = [
    {"cmd": "/help", "desc": "Этот список возможностей"},
    {"cmd": "/reset", "desc": "Сброс истории диалога и памяти сессии"},
    {"cmd": "/exit", "desc": "Выход из CLI"},
]


def format_capabilities_text(*, short: bool = False) -> str:
    """Текст для /help и tool agent_capabilities."""
    lines = [
        "Я помощник по **1С** (только чтение).",
        "",
        "Работаю с **вашей** базой (УТ / УПП / ERP / любая CF):",
        "имена объектов — из metadata и Qdrant **этой** выгрузки, не из другой конфигурации.",
        "",
        "### Что умею",
        "",
    ]
    for block in CAPABILITIES:
        if short and block["id"] in ("locks", "query_help", "conn", "code"):
            continue
        lines.append(f"**{block['title']}**")
        for ex in block["examples"][:3]:
            lines.append(f"  • «{ex}»")
        lines.append("")

    lines.append("### Команды")
    for c in CLI_COMMANDS:
        lines.append(f"  • `{c['cmd']}` — {c['desc']}")

    lines.extend(
        [
            "",
            "### Как спрашивать",
            "  • Своими словами, можно с опечатками и вразнобой",
            "  • Период: «прошлый год», «январь–март», «этот и прошлый год», "
            "«за все время», «сейчас» (остатки)",
            "  • Без периода для данных — подставлю разумный default "
            "(два последних полных года)",
            "  • Excel: «в excel» / «с графиками» / «запихни в отчёт»",
            "  • Follow-up: «только …», «а теперь по месяцам», «документы по …»",
            "  • Не нужно писать full_name — найду сам; если несколько — спрошу",
            "",
            "### Примеры",
            "  • «продажи по контрагентам за прошлый год в excel с графиками»",
            "  • «сколько товаров на складах осталось»",
            "  • «что продаётся а что нет»",
            "  • «по каким регистрам проводится [ваш документ]»",
            "  • «все реквизиты …» / «как устроена форма …»",
            "",
            "Спросите задачу — подберу tool сам "
            "(build_report / linked_query / analyst_report / inspect_code …).",
        ]
    )
    return "\n".join(lines).strip()


def get_capabilities_payload(*, short: bool = False) -> dict[str, Any]:
    """Структура для tool result + готовый answer."""
    text = format_capabilities_text(short=short)
    skip = ("locks", "query_help", "conn", "code") if short else ()
    return {
        "ok": True,
        "kind": "capabilities",
        "answer": text,
        "sections": [
            {
                "id": b["id"],
                "title": b["title"],
                "examples": b["examples"],
                "tools": b["tools"],
            }
            for b in CAPABILITIES
            if b["id"] not in skip
        ],
        "cli_commands": CLI_COMMANDS,
        "prefer": (
            "Ответь пользователю ТЕКСТОМ из поля answer (можно слегка оформить). "
            "Не вызывай data-tools. Не выдумывай full_name. "
            "Предложи 2–3 примера под роль (менеджер / аналитик / разработчик)."
        ),
        "note": "Каталог возможностей агента, не данные 1С.",
    }
