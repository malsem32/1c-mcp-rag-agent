# -*- coding: utf-8 -*-
"""Каталог tools: TOOL_DEFINITIONS, видимость для LLM/MCP, системный промпт."""

from __future__ import annotations

from typing import Any


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


# Алиасы / узкие дубли: остаются в dispatch (preflight/совместимость),
# но НЕ в схеме LLM и НЕ в MCP list_tools — один allowlist.
HIDDEN_FROM_AGENT: frozenset[str] = frozenset(
    {
        "manager_help",  # → agent_capabilities
        "quick_find",  # → resolve_object
        "suggest_objects",  # → resolve_object / find_metadata
        "get_manager_fields",  # → resolve_business_fields
        "list_object_attributes",  # → describe_object / get_object_structure
        "list_document_types",  # → list_objects(metadata_type=Документы)
        "list_catalog_types",  # → list_objects(metadata_type=Справочники)
        "list_register_types",  # → list_objects(metadata_type=…)
        "export_document_period_excel",  # → export_object_excel(mode=period)
        "active_users_overview",  # → list_active_sessions
        "route_intent",  # preflight уже маршрутизирует
        "query_event_log",  # → analyze_event_log
        "role_rights",  # → access_overview
        "find_roles_for_object",  # → access_overview
        "list_roles",  # → access_overview
        "report_builder",  # preflight/excel; LLM/MCP → build_report(to_excel=true)
    }
)


_HIDDEN_FROM_LLM = HIDDEN_FROM_AGENT  # совместимость импортов


def is_agent_visible_tool(name: str) -> bool:
    """
    True = в openai_tools() и в MCP list_tools. False = только dispatch (роутер/preflight/ask_1c/зонтики).
    Видимый набор (~23) — tools.tool_umbrella.VISIBLE; узкие tools доступны через зонтики.
    """
    n = (name or "").strip()
    if not n or n in HIDDEN_FROM_AGENT:
        return False
    from tools.tool_umbrella import VISIBLE

    return n in VISIBLE


def agent_visible_tool_names() -> frozenset[str]:
    """Имена из TOOL_DEFINITIONS минус алиасы. Без MCP-only extras (test_connection)."""
    names: set[str] = set()
    for t in TOOL_DEFINITIONS:
        n = ((t.get("function") or {}).get("name") or "").strip()
        if is_agent_visible_tool(n):
            names.add(n)
    return frozenset(names)


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    _tool(
        "explain_config",
        "Прочие разделы конфигурации по фразе (подписки, регламентные задания, RLS, HTTP-сервисы, обмен, "
        "общие реквизиты, события форм, макеты печатных форм, запрос СКД, опасный код).",
        {
            "request": {"type": "string", "description": "фраза пользователя целиком", "default": ""},
        },
        ["request"],
    ),
    _tool(
        "agent_capabilities",
        "КОГДА: пользователь спросил «что умеешь», «что можешь», «какие команды», "
        "«help», «справка», «как пользоваться», «представься». "
        "ВЕРНЁТ answer — готовый текст возможностей (вывести пользователю). "
        "НЕ data-tools. short=true — короче.",
        {
            "short": {
                "type": "boolean",
                "default": False,
                "description": "true = короче (без locks/syntax)",
            }
        },
    ),
    _tool(
        "manager_help",
        "КОГДА: неясно, какой tool вызвать. "
        "ВЕРНЁТ: scenarios — когда metadata, когда structure, когда execute_query. "
        "Также capabilities_answer — краткий список «что умею».",
        {},
    ),
    _tool(
        "code_help",
        "КОГДА: справка по BSL / платформе 1С (не язык запросов). "
        "Синтаксис ВЫБРАТЬ → query_syntax_help. Код конфигурации → inspect_code. "
        "query/topic — тема или фрагмент вопроса.",
        {
            "query": {
                "type": "string",
                "description": "Вопрос: «как работает Запрос», «БлокировкаДанных»…",
                "default": "",
            },
            "topic": {
                "type": "string",
                "description": "Опционально: registers|query|http|bsl|…",
                "default": "",
            },
        },
    ),
    _tool(
        "test_connection",
        "КОГДА: проверить HTTP-связь с 1С. ВЕРНЁТ: {ok, config, version}.",
        {},
    ),
    _tool(
        "query_syntax_help",
        "КОГДА: справка по языку запросов 1С (не ANSI SQL). "
        "topic=functions|examples|oneline|dates|document|join|errors|antipatterns… "
        "functions = каталог конструктора; antipatterns = антипаттерны + как лучше. "
        "Скан кода — audit_query_code. Готовые ВЫБРАТЬ — query_examples(scenario). "
        "ВЕРНЁТ: {topic, title, content, example} или каталог.",
        {
            "topic": {
                "type": "string",
                "description": "functions|antipatterns|examples|oneline|select|"
                "where|dates|document|join|catalog|errors|overview. Пусто = список тем.",
                "default": "",
            }
        },
    ),
    _tool(
        "query_examples",
        "КОГДА (важно): перед execute_query — УНИВЕРСАЛЬНЫЙ шаблон ВЫБРАТЬ. "
        "scenario: document_period | any_report | sum_period | catalog_search | tabular | … "
        "document_name = full_name из find_metadata ЭТОЙ базы (обязательно подставь). "
        "Шаблоны без имён объектов CF — только плейсхолдеры + full_name из metadata. "
        "Excel за месяц → export_object_excel(full_name, mode=period, year, month). "
        "ВЕРНЁТ: {examples:[{query, prefer_tool, note}], rules, pipeline}.",
        {
            "scenario": {
                "type": "string",
                "description": "document_period | any_report | sum_period | top_group | "
                "catalog_search | tabular | excel | '' = список",
                "default": "",
            },
            "object_name": {
                "type": "string",
                "description": "full_name любого класса (Документ/Справочник/Регистр…) из find_metadata",
                "default": "",
            },
            "document_name": {
                "type": "string",
                "description": "Документ.X из find_metadata/suggest_objects (не выдумывать)",
                "default": "",
            },
            "catalog_name": {
                "type": "string",
                "description": "Справочник.Y из find_metadata, для catalog_* сценариев",
                "default": "",
            },
        },
    ),
    _tool(
        "list_metadata_types",
        "КОГДА: какие классы метаданных есть. ВЕРНЁТ: {types: [...]}.",
        {},
    ),
    _tool(
        "list_document_types",
        "КОГДА: «какие документы есть», «названия документов». "
        "search — слова из фразы менеджера (стемминг): счет, покупател, реализац… "
        "Имена зависят от конфигурации — бери names[0] из ответа, не угадывай. "
        "Лучше: find_metadata / suggest_objects(фраза). ВЕРНЁТ: {names, count, hint}.",
        {
            "limit": {"type": "integer", "default": 80},
            "search": {
                "type": "string",
                "description": "Подстрока/стем или несколько слов из задачи пользователя.",
                "default": "",
            },
        },
    ),
    _tool(
        "list_catalog_types",
        "КОГДА: «какие справочники есть». search='номенклатур' чтобы найти нужный. "
        "ВЕРНЁТ: {names: [\"Справочник.…\"], count}.",
        {
            "limit": {"type": "integer", "default": 80},
            "search": {
                "type": "string",
                "description": "Подстрока: номенклатур, контрагент…",
                "default": "",
            },
        },
    ),
    _tool(
        "list_objects",
        "КОГДА: список объектов ЛЮБОГО класса ЭТОЙ базы (в т.ч. доработанных). "
        "metadata_type из list_metadata_types. search — опциональный стем. "
        "limit/offset/page — порция (ты выбираешь сколько; has_more → next_offset). "
        "ВЕРНЁТ: {type, names, paging, has_more, next_offset}.",
        {
            "metadata_type": {
                "type": "string",
                "description": "Подсистемы | Справочники | Документы | Отчеты | Роли | Константы | … (list_metadata_types)",
            },
            "search": {"type": "string", "default": ""},
            "limit": {
                "type": "integer",
                "default": 25,
                "description": "Сколько имён за раз (1–200)",
            },
            "offset": {
                "type": "integer",
                "default": 0,
                "description": "0, затем next_offset если has_more",
            },
            "page": {
                "type": "integer",
                "default": 0,
                "description": "page=1,2… вместо offset; 0=нет",
            },
        },
        ["metadata_type"],
    ),
    _tool(
        "list_register_types",
        "КОГДА: регистры ЭТОЙ базы (сведений/накопления), в т.ч. после доработок. "
        "kind=Все|Сведений|Накопления, search=стем. ВЕРНЁТ: {names, count}.",
        {
            "kind": {"type": "string", "default": "Все"},
            "limit": {"type": "integer", "default": 80},
            "search": {"type": "string", "default": ""},
        },
    ),
    _tool(
        "find_metadata",
        "КОГДА: найти full_name в ЭТОЙ конфигурации по подстроке (любой класс). "
        "search_query ОБЯЗАТЕЛЕН. object_type по умолчанию Все. "
        "НЕ угадывай имена — бери items[].full_name. Дальше structure → query. "
        "limit/offset: сколько и какая порция — ты выбираешь; has_more → offset=next_offset. "
        "ВЕРНЁТ: {items, paging, has_more, next_offset}.",
        {
            "search_query": {
                "type": "string",
                "description": "Слова/стемы из задачи пользователя",
            },
            "object_type": {
                "type": "string",
                "description": "Все | Справочники | Документы | РегистрыСведений | РегистрыНакопления | …",
                "default": "Все",
            },
            "limit": {
                "type": "integer",
                "default": 25,
                "description": "Сколько объектов за раз (1–100), решаешь сам",
            },
            "offset": {
                "type": "integer",
                "default": 0,
                "description": "Порция: 0, потом next_offset если has_more",
            },
            "page": {
                "type": "integer",
                "default": 0,
                "description": "page=1,2,3… вместо offset; 0=не использовать",
            },
        },
        ["search_query"],
    ),
    _tool(
        "suggest_objects",
        "КОГДА: фраза на естественном языке — объекты ЭТОЙ базы (и доработанные). "
        "limit/offset — порция. ВЕРНЁТ: {items, paging}.",
        {
            "query": {"type": "string", "description": "фраза менеджера"},
            "object_type": {
                "type": "string",
                "description": "Все | Справочники | Документы | Регистры…",
                "default": "Все",
            },
            "limit": {"type": "integer", "default": 15},
            "offset": {"type": "integer", "default": 0},
            "page": {"type": "integer", "default": 0},
        },
        ["query"],
    ),
    _tool(
        "get_object_structure",
        "КОГДА: РЕАЛЬНЫЕ реквизиты/ТЧ/формы/макеты/команды объекта ЭТОЙ базы. "
        "full_name из find/list/suggest. "
        "«все реквизиты» → attributes_lines / attributes_table — вывести ВСЕ, не «основные». "
        "attribute_count = полное число. ВЕРНЁТ: attributes_table, fields, tabular_sections, forms…",
        {
            "full_name": {
                "type": "string",
                "description": "Тип.Имя из metadata tools, без кода элемента",
            },
            "include_form_layout": {
                "type": "boolean",
                "description": "true — разобрать layout Form.xml (привязки реквизитов)",
                "default": False,
            },
            "form_name": {
                "type": "string",
                "description": "Имя формы ТОЧНО как в forms[].name конфигурации. "
                "Пусто = default_forms.object / list из метаданных",
                "default": "",
            },
        },
        ["full_name"],
    ),
    _tool(
        "list_object_attributes",
        "КОГДА: «все реквизиты», «полный список полей» объекта — "
        "ТОЛЬКО таблица name|synonym|type (+ ТЧ). "
        "limit/offset — порция, если список длинный (has_more → next_offset). "
        "НЕ сокращать до «основных». full_name из resolve.",
        {
            "full_name": {
                "type": "string",
                "description": "full_name из find_metadata (Документ.X / Справочник.Y)",
            },
            "limit": {
                "type": "integer",
                "default": 0,
                "description": "0 = все; иначе порция (модель выбирает)",
            },
            "offset": {"type": "integer", "default": 0},
            "page": {"type": "integer", "default": 0},
            "include_tabular": {
                "type": "boolean",
                "default": True,
                "description": "включить поля табличных частей",
            },
        },
        ["full_name"],
    ),
    _tool(
        "describe_object",
        "КОГДА: полное описание объекта как в конфигураторе — "
        "реквизиты, табличные части, ВСЕ формы/макеты/команды с ИМЕНАМИ из конфигурации "
        "(имена форм/макетов — только из structure/find_modules, не угадывать). "
        "для Подсистема — content/child_subsystems. "
        "include_form_layout=true — раскладка формы (обычной и управляемой): вкладки → поля/надписи/таблицы/флажки "
        "с путями к данным и событиями, кнопки → процедуры, события формы; form_name — другая форма, tab — одна вкладка. "
        "ВЕРНЁТ: fields, tabular_sections, forms[{name,synonym}], templates, "
        "default_forms, form_layout.",
        {
            "full_name": {
                "type": "string",
                "description": "Документ.X | Справочник.Y | Подсистема.Z | …",
            },
            "include_form_layout": {
                "type": "boolean",
                "description": "разбор Form.xml (default true)",
                "default": True,
            },
            "form_name": {
                "type": "string",
                "description": "имя из forms[] этой базы; пусто = default_forms из метаданных",
                "default": "",
            },
            "tab": {
                "type": "string",
                "description": "вкладка формы (подпись страницы) — подробно только её элементы",
                "default": "",
            },
        },
        ["full_name"],
    ),
    _tool(
        "explain_command",
        "Что делает кнопка/команда формы и почему кнопка или поле недоступно/не видно. "
        "Цепочка: кнопка → команда → обработчик (тело из модуля формы) → серверный вызов; "
        "причины недоступности: свойства элемента, функциональные опции, код Видимость/Доступность. "
        "Примеры: «что делает кнопка Заполнить в заказе», «почему поле Склад недоступно в форме реализации». "
        "Не для списка всех элементов формы → describe_form.",
        {
            "request": {"type": "string", "description": "фраза пользователя целиком", "default": ""},
            "full_name": {"type": "string", "description": "Документ.X / Справочник.Y, если известен", "default": ""},
            "form_name": {"type": "string", "description": "имя формы; пусто = основная", "default": ""},
            "command": {"type": "string", "description": "имя или заголовок кнопки/поля, если известен", "default": ""},
        },
        [],
    ),
    _tool(
        "describe_form",
        "КОГДА: макет/состав КОНКРЕТНОЙ формы — DataPath, element_types, events. "
        "form_name ОБЯЗАТЕЛЬНО из describe_object/get_object_structure → forms[].name "
        "(имена любые, как в конфигураторе). Пусто = DefaultObjectForm. "
        "ВЕРНЁТ: data_paths, element_types, events, path, available_forms при ошибке.",
        {
            "full_name": {
                "type": "string",
                "description": "Документ.X | Справочник.Y | …",
            },
            "form_name": {
                "type": "string",
                "description": "точное имя формы из forms[].name конфигурации",
                "default": "",
            },
            "request": {
                "type": "string",
                "description": "фраза пользователя, если full_name неизвестен (объект найдётся по каталогу)",
                "default": "",
            },
        },
        [],
    ),
    _tool(
        "query_by_attributes",
        "КОГДА: список документов/справочников, у которых свойство в РЕКВИЗИТАХ шапки "
        "(не оборот регистра, не «сколько продаж»). "
        "Примеры: «в каких заказах ожидается оплата», «какие X не проведены», "
        "«X со статусом …», «X с пустым договором». "
        "Сам: resolve → structure → матчинг имени/синонима/enum → ВЫБРАТЬ ГДЕ. "
        "НЕ build_report (агрегат). Имена полей только из ЭТОЙ CF. "
        "ВЕРНЁТ rows + filters[] (реальные реквизиты) + query.",
        {
            "request": {
                "type": "string",
                "description": "фраза целиком: в каких заказах клиента ожидается оплата",
                "default": "",
            },
            "full_name": {
                "type": "string",
                "default": "",
                "description": "Документ.X | Справочник.Y из resolve, если уже известен",
            },
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Строк за раз (1–200)",
            },
            "offset": {"type": "integer", "default": 0},
        },
    ),
    _tool(
        "search_fields",
        "КОГДА: найти реквизит по смыслу («сумма», «контрагент») внутри объекта. "
        "Список документов по значению реквизита → query_by_attributes. "
        "ВЕРНЁТ: {fields: [{name, type, synonym?}], count} — name подставляй в ВЫБРАТЬ.",
        {
            "full_name": {"type": "string", "description": "Документ.X | Справочник.Y"},
            "search_text": {
                "type": "string",
                "description": "сумма | контрагент | партнер | номер | инн",
            },
            "header_only": {"type": "boolean", "default": True},
            "limit": {"type": "integer", "default": 15},
        },
        ["full_name", "search_text"],
    ),
    _tool(
        "resolve_business_fields",
        "КОГДА: быстро получить roles amount/counterparty/date/number для ВЫБРАТЬ. "
        "ВЕРНЁТ: {full_name, roles: {amount: \"…\", counterparty: \"…\", date: \"…\"}}.",
        {"full_name": {"type": "string", "description": "Документ.X | Справочник.Y"}},
        ["full_name"],
    ),
    _tool(
        "get_manager_fields",
        "КОГДА: алиас resolve_business_fields. ВЕРНЁТ: {full_name, roles}.",
        {"full_name": {"type": "string"}},
        ["full_name"],
    ),
    _tool(
        "execute_query",
        "КОГДА: нужны данные из 1С (просмотр в чате). "
        "Язык запросов 1С (ВЫБРАТЬ…), не ANSI SQL. "
        "Перед: full_name + get_object_structure/resolve (поля только оттуда). "
        "Для файла Excel → export_excel_report (тот же pipeline + сохранение .xlsx). "
        "ВЕРНЁТ: {rows, count}.",
        {
            "query": {
                "type": "string",
                "description": "ВЫБРАТЬ ПЕРВЫЕ N … ИЗ Справочник.X / Документ.Y … "
                "Поля только из get_object_structure/resolve.",
            },
            "limit": {
                "type": "integer",
                "default": 25,
                "description": "Сколько строк за раз (ты выбираешь, 1–200). Нет нужного → offset+=limit",
            },
            "offset": {
                "type": "integer",
                "default": 0,
                "description": "Сдвиг порции (0, 25, 50…). has_more → next_offset",
            },
            "page": {
                "type": "integer",
                "default": 0,
                "description": "Альтернатива offset: page=1,2,3… (1-based). 0=не использовать",
            },
        },
        ["query"],
    ),
    _tool(
        "export_object_excel",
        "КОГДА (приоритет для Excel): отчёт по ЛЮБОМУ объекту ЭТОЙ базы — "
        "Документ / Справочник / РегистрСведений / РегистрНакопления. "
        "Сам: structure → ВЫБРАТЬ. full_name из find_metadata/list_*/suggest. "
        "mode: auto|list|period|balances|turnovers|slice. year/month для периода. "
        "ВЕРНЁТ: {ok, path, object, kind, mode, rows_count}.",
        {
            "full_name": {
                "type": "string",
                "description": "Документ.X | Справочник.Y | РегистрСведений.Z | РегистрНакопления.W",
            },
            "year": {"type": "integer", "description": "Год (для period/оборотов/среза)", "default": 0},
            "month": {"type": "integer", "description": "Месяц 1–12", "default": 0},
            "limit": {"type": "integer", "default": 500},
            "mode": {
                "type": "string",
                "default": "auto",
                "description": "auto | list | period | balances | turnovers | slice",
            },
            "only_posted": {"type": "boolean", "default": True},
            "filename": {"type": "string", "default": ""},
            "title": {"type": "string", "default": ""},
        },
        ["full_name"],
    ),
    _tool(
        "export_document_period_excel",
        "КОГДА: удобная обёртка — только ДОКУМЕНТ за месяц. "
        "Для справочников/регистров → export_object_excel. "
        "document_name из find_metadata. ВЕРНЁТ: {ok, path, document, period}.",
        {
            "document_name": {
                "type": "string",
                "description": "Документ.X из find_metadata",
            },
            "year": {"type": "integer"},
            "month": {"type": "integer"},
            "limit": {"type": "integer", "default": 500},
            "only_posted": {"type": "boolean", "default": True},
            "filename": {"type": "string", "default": ""},
            "title": {"type": "string", "default": ""},
        },
        ["document_name", "year", "month"],
    ),
    _tool(
        "export_excel_report",
        "КОГДА: Excel по своему ВЫБРАТЬ (JOIN, сложная группировка). "
        "Иначе предпочти export_object_excel(full_name) — без ручного SQL. "
        "query — язык 1С, одна строка, поля из structure. "
        "ВЕРНЁТ: {ok, path, filename, rows_count}.",
        {
            "query": {
                "type": "string",
                "description": "ВЫБРАТЬ ПЕРВЫЕ N … ИЗ Документ|Справочник|Регистр…",
            },
            "limit": {"type": "integer", "default": 500},
            "title": {"type": "string", "default": ""},
            "filename": {"type": "string", "default": ""},
            "sheet_name": {"type": "string", "default": "Отчёт"},
        },
        ["query"],
    ),
    _tool(
        "code_status",
        "КОГДА: перед поиском по коду — есть ли выгрузка BSL + карта EN-папок. "
        "Dump: CommonForms=ОбщиеФормы, CommonModules=ОбщиеМодули, Catalogs=Справочники. "
        "ВЕРНЁТ: {ready, top_folders[{en,ru}], dump_layout}.",
        {},
    ),
    _tool(
        "find_modules",
        "КОГДА: найти модуль/форму по русской фразе или имени (без тела). "
        "search = фраза ПОЛЬЗОВАТЕЛЯ ЦЕЛИКОМ (не отбрасывать «форма документа»): "
        "«модуль формы документа …» (имя из find_metadata), "
        "«Документ.X.Форма.ФормаДокумента». "
        "kind=form без имени формы → первой ФормаДокумента/ФормаЭлемента, не ФормаВыбора. "
        "В ответе: kind_ru, full_name_1c, form_name. Дальше module_outline / read_code.",
        {
            "search": {
                "type": "string",
                "description": "Фраза целиком: «форма документа Счет…», «общая форма …», имя объекта",
                "default": "",
            },
            "kind": {
                "type": "string",
                "description": "common_form | form | common_module | catalog | … (опционально)",
                "default": "",
            },
            "limit": {"type": "integer", "default": 20},
        },
    ),
    _tool(
        "module_outline",
        "КОГДА: модуль большой (тысячи строк) — только список Процедура/Функция. "
        "path из find_modules/search_code. НЕ читай модуль целиком. "
        "ВЕРНЁТ: {symbols:[{line,type,name}], total_lines_est}.",
        {
            "path": {
                "type": "string",
                "description": "Полный path к .bsl из find_modules",
                "default": "",
            },
            "module": {
                "type": "string",
                "description": "Имя модуля, если path неизвестен",
                "default": "",
            },
            "max_symbols": {"type": "integer", "default": 60},
            "name_filter": {
                "type": "string",
                "description": "Подстрока имени: 'печать' / 'макет' — не терять хвост модуля",
                "default": "",
            },
        },
    ),
    _tool(
        "search_code",
        "КОГДА: «где в коде», «покажи кусок», «найди вызов», grep по BSL/запросу. "
        "После hits → read_code(path, start_line). "
        "max_matches/limit — сколько hits за раз (ты выбираешь). "
        "offset/page — след. порция если has_more. "
        "ВЕРНЁТ matches + paging.",
        {
            "query": {
                "type": "string",
                "description": "Фрагмент КОДА: ДинамическийСписок, Запрос.Текст, …",
            },
            "module_filter": {
                "type": "string",
                "description": "CommonForms | общие формы | Catalogs | имя объекта…",
                "default": "",
            },
            "kind": {
                "type": "string",
                "description": "common_form | общая форма | common_module | …",
                "default": "",
            },
            "max_matches": {
                "type": "integer",
                "default": 20,
                "description": "Сколько matches за раз (синоним limit)",
            },
            "limit": {
                "type": "integer",
                "default": 0,
                "description": "Альтернатива max_matches; 0 = использовать max_matches",
            },
            "offset": {"type": "integer", "default": 0},
            "page": {"type": "integer", "default": 0},
            "context_lines": {"type": "integer", "default": 2},
            "regex": {"type": "boolean", "default": False},
        },
        ["query"],
    ),
    _tool(
        "read_code",
        "КОГДА: прочитать ОКНО строк модуля (после search_code/outline). "
        "max_lines ≤80–120. НЕ весь модуль (32k context). "
        "ВЕРНЁТ: {content с номерами строк, start_line, end_line}.",
        {
            "path": {"type": "string", "description": "path к .bsl", "default": ""},
            "module": {"type": "string", "default": ""},
            "start_line": {"type": "integer", "default": 1},
            "max_lines": {"type": "integer", "default": 80},
        },
    ),
    _tool(
        "inspect_code",
        "КОГДА (ПРИОРИТЕТ, универсально): «откуда данные», «как устроен» "
        "отчёт/форма/модуль/документ — объект КОНФИГУРАЦИИ (Qdrant). "
        "Скинутый .epf/.erf → inspect_external_processing. "
        "ОДИН вызов (BSL + СКД Templates). request=фраза целиком. "
        "ВЕРНЁТ: full_name_1c, likely_sources, data_source_hits, skd_templates. "
        "После — сразу ответь. НЕ цепочка find_metadata+search_code×N.",
        {
            "request": {
                "type": "string",
                "description": "Любая фраза: «отчет X откуда данные», «форма Y», «обработка Z»",
                "default": "",
            },
            "path": {
                "type": "string",
                "description": "Уже известный path к .bsl (опционально)",
                "default": "",
            },
            "max_hits": {"type": "integer", "default": 10},
        },
    ),
    _tool(
        "inspect_external_processing",
        "КОГДА: во фразе есть .epf/.erf/.cfe ИЛИ пользователь вложил внешнюю обработку. "
        "СРАЗУ этот tool. НЕ Cursor Search/Read (бинарник «не виден» в чате). "
        "НЕ писать «файла нет» без результата tool. "
        "path=имя файла или полный путь (пробелы/кириллица ок). "
        "Tool сам ищет: папка проекта, uploads/, Desktop, Downloads. "
        "request=вопрос. НЕ inspect_code (тот = Qdrant CF). НЕ запуск в базе. "
        "ВЕРНЁТ: kind, name, modules[], forms[], templates[], queries[], hits[].",
        {
            "path": {
                "type": "string",
                "description": "Имя файла (Форма для СПОТ.epf) или полный путь / папка выгрузки",
                "default": "",
            },
            "request": {
                "type": "string",
                "description": "Вопрос по файлу целиком (фраза пользователя)",
                "default": "",
            },
            "max_hits": {"type": "integer", "default": 12},
        },
    ),
    _tool(
        "build_external_processing",
        "КОГДА: «сделай / создай / напиши внешнюю обработку (epf)» или внешний ОТЧЁТ (erf), «собери обработку, которая …». "
        "Ты сам пишешь BSL и описываешь реквизиты — tool собирает готовый .epf (модуль объекта, реквизиты, ТЧ, "
        "обычная форма: надпись + поле ввода на каждый реквизит, кнопка «Выполнить» вызывает экспортную "
        "ВыполнитьОбработку() модуля; значения полей доступны в модуле по имени реквизита; каждая ТЧ выводится таблицей, редактируемой в форме; значения доступны в модуле как ТЧ). "
        "Имена объектов и полей конфигурации сначала проверь tools (describe_object, inspect_code), не выдумывай. "
        "Типы реквизитов: Строка(N), Число(N,M), Булево, Дата, ДатаВремя, Время, Произвольный, "
        "ссылки на объекты этой CF (СправочникСсылка.Имя, ДокументСсылка.Имя, ПеречислениеСсылка.Имя …), составной — через запятую; "
        "имя объекта сначала уточни describe_object/resolve_object. Базу НЕ меняет. "
        "ОБЫЧНЫЕ ФОРМЫ: процедура формы с именем события (ПриОткрытии — проверено в клиенте; ПередЗакрытием и др. — по выгрузке, не гоняли) "
        "назначается событию автоматически; события полей — по имени <ИмяРеквизита><Событие>(Элемент): ПодразделениеПриИзменении "
        "(проверено в клиенте), также …НачалоВыбора, …Очистка, …Регулирование, …Открытие, …ОбработкаВыбора, …ОкончаниеВводаТекста, "
        "…АвтоПодборТекста (по выгрузке, не гоняли; параметры сверяй по справке). "
        "Общую логику (запрос, заполнение) — в модуле обработки (module) экспортными процедурами, в формах только UI. "
        "Схема владельца: две формы — основная с полями для теста + extra_forms с одним результатом, строится в его ПриОткрытии; "
        "вызов из кода: Обр = ВнешниеОбработки.Создать(путь); Обр.Реквизит = …; Обр.ПолучитьФорму(\"Имя\").Открыть(). "
        "Без модальных окон (Предупреждение/Вопрос) — только Сообщить(). Любые элементы палитры обычной формы — параметр controls "
        "(у основной формы) или extra_forms[].controls: Кнопка, Надпись, ПолеВвода, Флажок, Переключатель, ПолеВыбора, ПолеСписка, "
        "ТабличноеПоле, ПолеТабличногоДокумента, ПолеТекстовогоДокумента, ПолеHTMLДокумента, ПолеКалендаря, ПолеКартинки, "
        "Диаграмма, ДиаграммаГанта, СводнаяДиаграмма, Дендрограмма, ПолеГрафическойСхемы, ПолеГеографическойСхемы, Индикатор, "
        "ПолосаРегулирования, Панель, РамкаГруппы, Разделитель, КоманднаяПанель. Элемент без привязки к реквизиту: в модуле формы "
        "ЭлементыФормы.<Имя> (значение — .Значение, заполнять в ПриОткрытии). События — по имени процедуры <ИмяЭлемента><Событие>: "
        "КнопкаНажатие(Элемент), …ПриИзменении (Флажок, Переключатель, ПолеВыбора, ПолосаРегулирования, ПолеКалендаря), "
        "проверено в клиенте: отображение всех 25, Нажатие, ПриИзменении флажка/полосы/календаря, Выбор табличного поля; "
        "…Выбор / …ПриВыводеСтроки / …ПриАктивизацииСтроки (ТабличноеПоле), …ОбработкаРасшифровки (ПолеТабличногоДокумента). "
        "МАКЕТЫ (печатные формы): параметр templates — табличный документ без запуска 1С (платформа читает, в клиенте не проверялось); в коде: "
        "Макет = ПолучитьМакет(\"Имя\") в модуле обработки; Обл = Макет.ПолучитьОбласть(\"Строка\"); Обл.Параметры.Имя = …; ТабДок.Вывести(Обл). "
        "Рецепты: knowledge «сборка_обработок». "
        "ВНЕШНИЙ ОТЧЁТ (.erf): kind=\"report\" + query (текст запроса СКД, параметры &Имя) — tool собирает отчёт на СКД: "
        "стандартная форма «Сформировать / Настройки…», поля берутся из колонок запроса (или fields), parameters — обязательно "
        "для каждого &Параметра запроса {name,title,type,value}, resources — суммируемые поля, group_by — группировки строк, "
        "order — сортировка. Имена объектов в запросе только из tools (describe_object/resolve_object). Реквизиты/ТЧ/формы для отчёта не нужны. "
        "ВЕРНЁТ: file, download_url, path, verified, bsl_warnings.",
        {
            "name": {"type": "string", "description": "Имя обработки без пробелов: ВыгрузкаОстатков"},
            "synonym": {"type": "string", "description": "Заголовок: Выгрузка остатков", "default": ""},
            "module": {
                "type": "string",
                "description": "Полный текст модуля объекта (BSL). Для кнопки формы нужна Процедура ВыполнитьОбработку() Экспорт",
            },
            "attributes": {
                "type": "array",
                "description": "Реквизиты: [{name, synonym, type}] (у отчёта — реквизиты объекта-отчёта, доступны в его модуле)",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "synonym": {"type": "string"},
                        "type": {"type": "string", "description": "Строка(50) | Число(15,2) | Булево | Дата | СправочникСсылка.Номенклатура | Произвольный"},
                    },
                    "required": ["name"],
                },
                "default": [],
            },
            "tabular_sections": {
                "type": "array",
                "description": "Табличные части: [{name, synonym, columns:[{name, synonym, type}]}]",
                "items": {"type": "object"},
                "default": [],
            },
            "form": {"type": "boolean", "description": "Форма с кнопками Выполнить/Закрыть", "default": True},
            "form_title": {"type": "string", "default": ""},
            "form_module": {
                "type": "string",
                "description": "Свой модуль формы (иначе кнопка вызывает ВыполнитьОбработку()). Обработчик кнопки называется "
                "ОсновныеДействияФормыОсновныеДействияФормыВыполнить(Кнопка)",
                "default": "",
            },
            "extra_forms": {
                "type": "array",
                "description": "Доп. формы без полей реквизитов: [{name, title, module, controls, width}]. Открываются из кода: "
                "ПолучитьФорму(\"<name>\").Открыть(). Типично: основная «Форма» с полями для ручного теста + "
                "«ФормаРезультата» с одним элементом результата в controls (ДиаграммаГанта, ТабличноеПоле …).",
                "items": {"type": "object"},
                "default": [],
            },
            "controls": {
                "type": "array",
                "description": "Элементы палитры на основной форме (под полями реквизитов и таблицами ТЧ): "
                "[{type, name, title, left, top, width, height, value_type, tab_order, bind, anchor}]. tab_order — порядок обхода Tab (по умолчанию в порядке перечисления). "
                "bind — привязка сторон: {top|bottom|left|right: \"form\" | \"ИмяЭлемента\" | \"ИмяЭлемента.сторона\" | {to, side, offset}}; "
                "form — к краю формы (растягивается вместе с окном), имя — к соседу из controls (по умолчанию верх к низу соседа, лево к правому краю соседа); "
                "offset по умолчанию из координат. anchor: true — все четыре стороны к форме. type — Кнопка | Надпись | ПолеВвода | Флажок | "
                "Переключатель | ПолеВыбора | ПолеСписка | ТабличноеПоле | ПолеТабличногоДокумента | ПолеТекстовогоДокумента | "
                "ПолеHTMLДокумента | ПолеКалендаря | ПолеКартинки | Диаграмма | ДиаграммаГанта | СводнаяДиаграмма | Дендрограмма | "
                "ПолеГрафическойСхемы | ПолеГеографическойСхемы | Индикатор | ПолосаРегулирования | Панель | РамкаГруппы | "
                "Разделитель | КоманднаяПанель. Без top — друг под другом; без width — во всю ширину; title — заголовок "
                "(кнопка, надпись, флажок, переключатель, рамка); value_type — тип значения ПолеВвода (Строка(50), Дата …). "
                "Не привязаны к реквизитам: ЭлементыФормы.<name> в модуле формы.",
                "items": {"type": "object"},
                "default": [],
            },
            "form_width": {"type": "integer", "description": "Ширина основной формы (по умолчанию 400 или по самому правому элементу)"},
            "templates": {
                "type": "array",
                "description": "Макеты табличного документа (печатные формы): [{name, synonym, columns:[ширины], styles:{имя:{bold,size,font,italic,"
                "underline,strike,align left|center|right,valign top|center|bottom,wrap,border all|left,top,…,line solid|dotted|double|dashed,"
                "border_width,fill #RRGGBB,color #RRGGBB,border_color,numfmt «ЧЦ=15; ЧДЦ=2; ЧГ=3,0»}}, rows:[{height, cells:[{text | param [+detail], style, colspan, rowspan, col, "
                "…свойства стиля}]}], drawings:[{file путь к PNG/JPEG/GIF/BMP | base64, row, col, rows, cols (занимаемые клетки), size proportional|stretch|real, offset:[4 числа]}], areas:[{name, rows:[с,по]} | {name, cols:[с,по]} | {name, rect:[стр1,кол1,стр2,кол2]}]}]. Номера с 0. "
                "Текст с [Имя] — шаблон, param — параметр области (Обл.Параметры.Имя). Области именуются как в ПолучитьОбласть(\"Имя\"). "
                "Ширина: 1 ед ≈ 0,87 px (колонка 72 ≈ 63 px); высота строки: 1 ед ≈ 0,38 px (строка 10 pt ≈ 40 ед) — height обычно НЕ задавай, строка растёт по тексту сама (в т.ч. с переносом); height < 40 схлопывает строку.",
                "items": {"type": "object"},
                "default": [],
            },
            "comment": {"type": "string", "default": ""},
            "kind": {
                "type": "string",
                "description": "processing (по умолчанию) — обработка .epf; report — внешний отчёт .erf на СКД",
                "default": "",
            },
            "query": {
                "type": "string",
                "description": "Только для отчёта: текст запроса (ВЫБРАТЬ … ИЗ …); итоговый ВЫБРАТЬ пакета — результат отчёта; у колонок нужны псевдонимы КАК",
                "default": "",
            },
            "fields": {
                "type": "array",
                "description": "Только для отчёта: колонки [{name, title}] (по умолчанию берутся из запроса)",
                "items": {"type": "object"},
                "default": [],
            },
            "parameters": {
                "type": "array",
                "description": "Только для отчёта: параметры запроса [{name, title, type, value}]. type: Дата | ДатаВремя | Строка(N) | "
                "Число(N,M) | Булево | СправочникСсылка.Имя …; value даты: 2026-01-01 или «Начало месяца», «Начало года», "
                "«Начало следующего месяца» …; у ссылочного значения нет",
                "items": {"type": "object"},
                "default": [],
            },
            "resources": {
                "type": "array",
                "description": "Только для отчёта: суммируемые поля — имена колонок или [{name, aggregate: Сумма|Количество|Максимум|Минимум|Среднее}]",
                "items": {},
                "default": [],
            },
            "group_by": {
                "type": "array",
                "description": "Только для отчёта: колонки группировки строк по порядку вложенности (пусто — список записей)",
                "items": {"type": "string"},
                "default": [],
            },
            "datasets": {
                "type": "array",
                "description": "Только для отчёта: несколько наборов данных [{name, query, fields?}] вместо одного query. "
                "Без links наборы объединяются (одноимённые колонки сливаются); со связью — соединяются по выражениям"
                "Виды набора: запрос {name, query}; набор-объект для внешних данных {name, object, fields:[{name,title,type}]} (данные в модуль отчёта подставляет код); "
                "объединение {name, union:[{name, query}|{name, object, fields}, …]}; таблицы внешних источников данных 1С берутся прямо в тексте запроса",
                "items": {"type": "object"},
                "default": [],
            },
            "links": {
                "type": "array",
                "description": "Только для отчёта: связи наборов [{source, dest, source_expression, dest_expression}] — имена наборов и колонки/выражения",
                "items": {"type": "object"},
                "default": [],
            },
            "calculated_fields": {
                "type": "array",
                "description": "Только для отчёта: вычисляемые поля [{name, expression, title}], выражение на языке выражений СКД "
                "(например «Сумма / Количество», «Выбор Когда … Тогда … Иначе … Конец»); доступны в resources/group_by/order",
                "items": {"type": "object"},
                "default": [],
            },
            "bar_buttons": {
                "type": "array",
                "description": "Только для отчёта: свои кнопки на верхней командной панели отчёта (рядом с «Сформировать/Настройки…») "
                "[{name, title, handler}]; handler — процедура модуля формы (по умолчанию <name>Нажатие(Кнопка))",
                "items": {"type": "object"},
                "default": [],
            },
            "bar_menus": {
                "type": "array",
                "description": "Только для отчёта: подменю на верхней командной панели отчёта [{name, title, items:[{name, title, handler}]}]; "
                "handler — процедура модуля формы (по умолчанию <имя пункта>Нажатие(Кнопка))",
                "items": {"type": "object"},
                "default": [],
            },
            "variant_buttons": {
                "type": "boolean",
                "description": "Только для отчёта: при variants добавлять подменю «Вариант отчёта» на панель (в обычной форме выбора варианта нет); false — не добавлять",
                "default": True,
            },
            "variants": {
                "type": "array",
                "description": "Только для отчёта: дополнительные варианты отчёта [{name, title, group_by, order, filters, select, structure, "
                "appearance, user_fields, output}] — те же ключи, что у верхнего уровня (основной вариант «Основной» берётся из верхнего уровня)",
                "items": {"type": "object"},
                "default": [],
            },
            "structure": {
                "type": "array",
                "description": "Только для отчёта: структура варианта вместо group_by: [{group:[поля], select, order, filters, output, items:[вложенные]}, "
                "{table:{rows:[поля цепочкой], columns:[поля]}}, {chart:{points:[поля], series:[поля], output:{ТипДиаграммы}}}]",
                "items": {"type": "object"},
                "default": [],
            },
            "appearance": {
                "type": "array",
                "description": "Только для отчёта: условное оформление [{fields:[поля], filters:[{field,op,value}], text_color, back_color "
                "(#RRGGBB или «красный»), bold, italic, underline, format («ЧЦ=15; ЧДЦ=2»), text}]",
                "items": {"type": "object"},
                "default": [],
            },
            "user_fields": {
                "type": "array",
                "description": "Только для отчёта: пользовательские поля настроек [{name, title, expression}] или "
                "[{name, title, cases:[{filters:[…], value}]}]; попадают в выбранные поля и доступны в group_by/order/filters",
                "items": {"type": "object"},
                "default": [],
            },
            "output": {
                "type": "object",
                "description": "Только для отчёта: параметры вывода {ВыводитьЗаголовок|ВыводитьОтбор|ВыводитьПараметрыДанных: Auto|Output|DontOutput, "
                "РасположениеИтогов|Вертикальное/ГоризонтальноеРасположениеОбщихИтогов: Auto|None|Begin|End|BeginAndEnd, "
                "РасположениеПолейГруппировки, РасположениеРеквизитов, ТипЗаголовкаПолей: Full|Short, МакетОформления, Заголовок}; у диаграммы (chart.output): ""ТипДиаграммы (Гистограмма, Круговая, График, Column3D, Pie…), ТипДиаграммы.ВидПодписей (Значение, Процент, Значение и процент…), ""ТипДиаграммы.ЦветФона #RRGGBB, ТипДиаграммы.Шрифт {font:{bold,italic,name,size}}, ТипДиаграммы.МинимальноеЗначение/МаксимальноеЗначение числа; ""параметр можно задать {value, use, params:{…}, type}; имена, которых платформа не знает, она отбрасывает — сборка вернёт warnings",
            },
            "select": {
                "type": "array",
                "description": "Только для отчёта: выбранные поля варианта (по умолчанию все колонки запроса и вычисляемые поля)",
                "items": {"type": "string"},
                "default": [],
            },
            "form_fields": {
                "type": "boolean",
                "description": "Только для отчёта: своя форма — поля ввода реквизитов и таблицы ТЧ отчёта над полем «Результат»; "
                "controls, form_module (события полей <Реквизит>ПриИзменении, кнопок <Имя>Нажатие, формы ПриОткрытии), "
                "form_width и extra_forms работают как у обработки; стандартные кнопки «Сформировать/Настройки» остаются",
                "default": False,
            },
            "filters": {
                "type": "array",
                "description": "Только для отчёта: отборы настроек по умолчанию [{field, op, value, use}]; field — колонка запроса или вычисляемое поле; "
                "op: = <> > >= < <= содержит «не содержит» «начинается с» подобно заполнено «не заполнено» «в списке» «не в списке» «в группе»; value — строка/число/Истина/дата, ""ссылочное «Справочник.Имя.ПустаяСсылка» / «Перечисление.Имя.Значение» / «ПланСчетов.Имя.Предопределённый» (объект проверяется по каталогу базы), ""или {field: другое поле}; values:[…] для «в списке» (без values/value — список заполнит пользователь); view=обычный|недоступен; группы: ""{group: И|ИЛИ|НЕ, items:[вложенные отборы и группы]}; "
                "use=false — отбор есть, но выключен (пользователь включит в настройках)",
                "items": {"type": "object"},
                "default": [],
            },
            "order": {
                "type": "array",
                "description": "Только для отчёта: сортировка «Поле» или «Поле Убыв»",
                "items": {"type": "string"},
                "default": [],
            },
        },
        ["name"],
    ),
    _tool(
        "linked_query",
        "КОГДА: задолженность/остатки/обороты; НЕСКОЛЬКО регистров/документов; "
        "поля («цена, количество…») и/или sources. "
        "Строит сложный ВЫБРАТЬ: .Остатки/.Обороты, ЛЕВОЕ СОЕДИНЕНИЕ, "
        "ПРЕДСТАВЛЕНИЕ/ЕСТЬNULL/СУММА/НАЧАЛОПЕРИОДА, УПОРЯДОЧИТЬ, ИТОГИ (если «итого»), "
        "РАЗРЕШЕННЫЕ. Без вложенного ВЫБРАТЬ в ИЗ и без ПОМЕСТИТЬ (HTTP). "
        "Имена ТОЛЬКО из resolve/structure ЭТОЙ базы. Менеджеру — таблица rows.",
        {
            "request": {
                "type": "string",
                "description": "Фраза: задолженность / обороты помесячно / свяжи регистры / с итогом",
                "default": "",
            },
            "sources": {
                "type": "string",
                "description": "Опционально: РегистрНакопления.A,РегистрНакопления.B (из find_metadata)",
                "default": "",
            },
            "fields_needed": {
                "type": "string",
                "description": "Опционально: цена, количество, номенклатура… (стемы → structure)",
                "default": "",
            },
            "as_of": {
                "type": "string",
                "description": "Дата остатков YYYY-MM-DD (пусто = конец года из фразы)",
                "default": "",
            },
            "limit": {
                "type": "integer",
                "default": 40,
                "description": "Строк за раз (1–200), выбираешь сам",
            },
            "offset": {
                "type": "integer",
                "default": 0,
                "description": "След. порция: offset=next_offset если has_more",
            },
            "page": {
                "type": "integer",
                "default": 0,
                "description": "page=1,2… вместо offset",
            },
            "execute": {
                "type": "boolean",
                "description": "true = сразу execute_query",
                "default": True,
            },
        },
    ),
    _tool(
        "find_callers",
        "КОГДА: «кто вызывает», «где вызывается Модуль.Процедура», usages. "
        "Lexical поиск вызовов X.Y( по Qdrant — reverse call sites. "
        "НЕ путать с trace_calls (тот идёт вниз по дереву от старта).",
        {
            "module": {
                "type": "string",
                "description": "Префикс общего модуля из ЭТОЙ CF (как в search_code/path)",
                "default": "",
            },
            "procedure": {
                "type": "string",
                "description": "Имя процедуры/функции из кода этой CF",
                "default": "",
            },
            "limit": {"type": "integer", "default": 15},
        },
    ),
    _tool(
        "scan_code_injection",
        "КОГДА: «Выполнить/Вычислить», инъекции кода, уязвимости в обмене. "
        "Ищет Выполнить(/Вычислить( (не Запрос.Выполнить). "
        "scope=exchange|all. risk high/medium/low. Универсально по Qdrant.",
        {
            "scope": {
                "type": "string",
                "description": "exchange — только обмен; all — вся конфигурация",
                "default": "exchange",
            },
            "limit": {"type": "integer", "default": 40},
            "include_query_execute": {
                "type": "boolean",
                "description": "true = включать Запрос.Выполнить (обычно false)",
                "default": False,
            },
        },
    ),
    _tool(
        "audit_query_code",
        "КОГДА: аудит/ревью запросов 1С, антипаттерны, «как лучше писать ВЫБРАТЬ», "
        "запрос в цикле, SELECT *, оптимизация запросов в модуле. "
        "request=фраза (найдёт модули) | path=…bsl | query_text=готовый ВЫБРАТЬ. "
        "Каталог: knowledge/антипаттерны_запросов.yaml. READ-ONLY + «Рекомендация НЕ применено». "
        "Не путать с scan_code_injection (Выполнить/Вычислить).",
        {
            "request": {
                "type": "string",
                "description": "Фраза: объект/модуль для скана запросов",
                "default": "",
            },
            "query_text": {
                "type": "string",
                "description": "Готовый текст ВЫБРАТЬ для проверки",
                "default": "",
            },
            "path": {
                "type": "string",
                "description": "path модуля из Qdrant (…/Module.bsl)",
                "default": "",
            },
            "max_findings": {"type": "integer", "default": 40},
        },
    ),
    _tool(
        "list_roles_by_right",
        "КОГДА: «какие роли могут удалять/изменять справочники», InteractiveDelete. "
        "Читает Roles/*/Rights.xml выгрузки. "
        "rights=InteractiveDelete,Update,Edit; "
        "object_type=Catalog|Document|Справочник или пусто=все классы этой CF. "
        "Не назначения пользователям ИБ.",
        {
            "rights": {
                "type": "string",
                "description": "Через запятую: InteractiveDelete,Update,Edit,Delete,…",
                "default": "InteractiveDelete,Update,Edit,InteractiveSetDeletionMark",
            },
            "object_type": {
                "type": "string",
                "description": "Catalog / Document / Справочник / Документ. Пусто = все классы.",
                "default": "",
            },
            "object_name": {
                "type": "string",
                "description": "Опционально bare name из find_metadata (не выдумывать)",
                "default": "",
            },
            "limit": {"type": "integer", "default": 80},
        },
    ),
    _tool(
        "explain_subscriptions",
        "КОГДА: «кто подписан на ПередЗаписью / проведение», подписки на события объекта. "
        "Читает EventSubscriptions/*.xml выгрузки этой CF. "
        "request=фраза; object_name из find_metadata; event=BeforeWrite|OnWrite|Processing|… "
        "Не inspect_code — там легко мимо подписок.",
        {
            "request": {
                "type": "string",
                "description": "Фраза целиком (событие + объект)",
                "default": "",
            },
            "object_name": {
                "type": "string",
                "description": "Bare name из find_metadata / каталога, не выдумывать",
                "default": "",
            },
            "full_name": {
                "type": "string",
                "description": "Документ.X / Справочник.X если уже известен",
                "default": "",
            },
            "event": {
                "type": "string",
                "description": "BeforeWrite / OnWrite / Processing / … (платформа)",
                "default": "",
            },
            "limit": {"type": "integer", "default": 40},
        },
    ),
    _tool(
        "explain_rls",
        "КОГДА: RLS, ограничение на уровне записей, шаблон ограничения роли на объект. "
        "Читает Roles/*/Rights.xml restrictionByCondition этой выгрузки. "
        "Не access_overview (там роли без текста шаблона). object_name из find_metadata.",
        {
            "request": {
                "type": "string",
                "description": "Фраза: RLS на объект",
                "default": "",
            },
            "object_name": {
                "type": "string",
                "description": "Bare name из каталога этой CF",
                "default": "",
            },
            "full_name": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 40},
        },
    ),
    _tool(
        "describe_scheduled_job",
        "КОГДА: какое регл. задание, метод, расписание. "
        "Читает ScheduledJobs/*.xml + Ext/Schedule.xml этой выгрузки. "
        "use_in_dump — флаг метаданных, не включено ли в ИБ.",
        {
            "request": {
                "type": "string",
                "description": "Фраза или имя задания из этой CF",
                "default": "",
            },
            "name": {
                "type": "string",
                "description": "Имя регл. задания из выгрузки, не выдумывать",
                "default": "",
            },
            "limit": {"type": "integer", "default": 30},
        },
    ),
    _tool(
        "form_event_map",
        "КОГДА: события формы, обработчики Form.xml (OnCreateAtServer → ПриСозданииНаСервере). "
        "Dump Forms/*/Ext/Form.xml этой CF. Не describe_object (там реквизиты, не Event). "
        "full_name из find_metadata; класс только если в речи «документ/справочник/…».",
        {
            "request": {"type": "string", "description": "Фраза целиком", "default": ""},
            "full_name": {
                "type": "string",
                "description": "Документ.X / Справочник.Y из этой CF",
                "default": "",
            },
            "form_name": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 80},
        },
    ),
    _tool(
        "print_form_bind",
        "КОГДА: параметры печатной формы / макета ПФ (parameter + detailParameter). "
        "Dump Templates/*/Ext/Template.xml. Не describe_object. "
        "full_name из find_metadata этой базы.",
        {
            "request": {"type": "string", "description": "Фраза / имя макета ПФ_", "default": ""},
            "full_name": {"type": "string", "default": ""},
            "template_name": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 80},
        },
    ),
    _tool(
        "query_static_explain",
        "КОГДА: разбери этот ВЫБРАТЬ — таблицы, вирт. таблицы, параметры, без execute. "
        "Не audit_query_code (антипаттерны) и не execute_query. "
        "query_text=текст или вставь ВЫБРАТЬ во фразу.",
        {
            "query_text": {"type": "string", "description": "Текст ВЫБРАТЬ", "default": ""},
            "request": {"type": "string", "description": "Фраза с текстом запроса", "default": ""},
        },
    ),
    _tool(
        "http_service_map",
        "КОГДА: HTTP-сервис, URL-шаблон, обработчик GET/POST. "
        "Dump HTTPServices/*.xml этой выгрузки. Не выдумывать имена сервисов.",
        {
            "request": {"type": "string", "default": ""},
            "name": {"type": "string", "description": "Имя HTTP-сервиса из dump", "default": ""},
        },
    ),
    _tool(
        "common_attr_and_types",
        "КОГДА: общий реквизит (состав Use), определяемый тип (v8:Type). "
        "Dump CommonAttributes / DefinedTypes этой CF.",
        {
            "request": {"type": "string", "default": ""},
            "name": {"type": "string", "default": ""},
            "object_name": {"type": "string", "default": ""},
            "kind": {
                "type": "string",
                "description": "attr | type | пусто=оба",
                "default": "",
            },
            "limit": {"type": "integer", "default": 40},
        },
    ),
    _tool(
        "exchange_composition",
        "КОГДА: состав плана обмена, AutoRecord. Не план счетов. "
        "Dump ExchangePlans/*/Ext/Content.xml этой выгрузки.",
        {
            "request": {"type": "string", "default": ""},
            "name": {"type": "string", "description": "Имя плана обмена из dump", "default": ""},
            "limit": {"type": "integer", "default": 80},
        },
    ),
    _tool(
        "document_by_number",
        "КОГДА: покажи документ № / номер — шапка одним ВЫБРАТЬ. "
        "Тип документа только из find_metadata этой CF (не угадывать). "
        "number= или № во фразе. ТЧ — отдельно по Ссылка.",
        {
            "request": {"type": "string", "default": ""},
            "full_name": {
                "type": "string",
                "description": "Документ.X из resolve/find_metadata",
                "default": "",
            },
            "number": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 5},
        },
    ),
    _tool(
        "proc_index",
        "КОГДА: где объявлена процедура, кто вызывает (proc_names + callees Qdrant). "
        "Не trace_calls (тот — цепочка вниз). Имя процедуры из этой CF, не выдумывать.",
        {
            "request": {"type": "string", "default": ""},
            "procedure": {"type": "string", "default": ""},
            "module": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 20},
        },
    ),
    _tool(
        "margin_report",
        "КОГДА: маржинальность, маржа, выручка vs себестоимость по менеджеру. "
        "Сам находит регистр выручки/себестоимости ЭТОЙ базы, строит .Обороты, выполняет. "
        "НЕ build_report по произвольному документу — только регистр оборотов из metadata. period/год из фразы.",
        {
            "request": {
                "type": "string",
                "description": "Фраза: маржа по менеджерам за прошлый год без возвратов",
                "default": "",
            },
            "period": {"type": "string", "default": ""},
            "group_by": {
                "type": "string",
                "description": "менеджер | подразделение | …",
                "default": "менеджер",
            },
            "exclude_returns": {"type": "boolean", "default": True},
            "limit": {"type": "integer", "default": 50},
        },
    ),
    _tool(
        "analyze_object_movements",
        "КОГДА (универсально, ЛЮБОЙ Документ/объект): «по каким регистрам проводится», "
        "«какие движения», «что пишет в регистры», «выведи ВСЕ регистры», "
        "«полный список регистров», «перечисли регистры документа». "
        "ОДИН tool: RegisterRecords (метаданные) + ObjectModule (Отразить*/Движения). "
        "request=фраза целиком; full_name=Документ.Имя если уже известен из чата. "
        "НЕ get_object_structure — он НЕ даёт полный список регистров проведения. "
        "После tool — перечисли ВСЕ из registers_all / register_records_meta, "
        "не сокращай до 10–15 штук. "
        "ЗАПРЕТ: «подождите / анализирую» без tool; ЗАПРЕТ выдумывать регистры.",
        {
            "request": {
                "type": "string",
                "description": "Фраза пользователя целиком",
                "default": "",
            },
            "full_name": {
                "type": "string",
                "description": "Документ.X если уже из resolve/structure",
                "default": "",
            },
        },
    ),
    _tool(
        "analyze_locks",
        "КОГДА: УЖЕ есть блокировки в коде — где стоят, режимы, пространства. "
        "Сканирует Qdrant. НЕ live wait-graph. "
        "Если НУЖНО ВНЕДРИТЬ / куда ставить — recommend_managed_locks.",
        {
            "request": {
                "type": "string",
                "description": "Фраза: где блокировки в коде…",
                "default": "",
            },
            "object_hint": {
                "type": "string",
                "description": "Опционально bare name / документ",
                "default": "",
            },
            "max_hits": {"type": "integer", "default": 40},
        },
    ),
    _tool(
        "recommend_managed_locks",
        "КОГДА: внедряете УПРАВЛЯЕМЫЕ блокировки (сейчас авто/нет locks); "
        "«куда ставить», «что исправить», «как перевести на managed», "
        "«подскажи блокировки для проведения документа». "
        "Строит рекомендации по движениям документа (analyze_object_movements) + "
        "structure измерений + шаблон BSL + чеклист миграции. "
        "full_name=Документ.X из find_metadata этой базы. "
        "НЕ выдумывать регистры. НЕ live-мониторинг.",
        {
            "request": {
                "type": "string",
                "description": "Фраза: внедрить управляемые / куда ставить…",
                "default": "",
            },
            "full_name": {
                "type": "string",
                "description": "Документ.X из resolve/find_metadata",
                "default": "",
            },
            "max_registers": {"type": "integer", "default": 15},
        },
    ),
    _tool(
        "trace_calls",
        "КОГДА: «проследи вызовы ВНИЗ», «цепочка из ОбработкаПроведения», deep-dive BSL. "
        "Старт: ObjectModule документа если проведение. depth=1..2 для скорости. "
        "«Кто вызывает X» → find_callers, не trace_calls. "
        "ВЕРНЁТ edges from→call→targets. Потом read_code по line.",
        {
            "request": {"type": "string", "description": "Фраза / имя объекта+процедуры", "default": ""},
            "path": {"type": "string", "description": "Documents/…/ObjectModule.bsl", "default": ""},
            "procedure": {"type": "string", "description": "Имя Процедуры/Функции", "default": ""},
            "depth": {"type": "integer", "default": 2},
            "max_edges": {"type": "integer", "default": 30},
        },
    ),
    _tool(
        "explain_fill",
        "КОГДА: «почему не заполнился», «пустое поле», «как заполняется реквизит». "
        "Универсально: field=ИмяРеквизита, full_name/object_name опционально, request=фраза. "
        "Qdrant hybrid-поиск присвоений/ОбработкаЗаполнения/условий. НЕ structure alone.",
        {
            "request": {"type": "string", "description": "Фраза пользователя", "default": ""},
            "field": {"type": "string", "description": "Имя реквизита/поля", "default": ""},
            "full_name": {"type": "string", "description": "Документ.X", "default": ""},
            "object_name": {"type": "string", "description": "Имя объекта без префикса", "default": ""},
            "max_hits": {"type": "integer", "default": 20},
        },
    ),
    _tool(
        "route_intent",
        "КОГДА: неясно, data / code / help / movements / fill. "
        "ВЕРНЁТ: {route, prefer_tool, action_for_agent}. Вызови prefer_tool.",
        {
            "request": {
                "type": "string",
                "description": "Фраза пользователя целиком",
            }
        },
        ["request"],
    ),
    _tool(
        "read_skd_query",
        "КОГДА: «текст запроса», «СКД», «шаблон схемы компоновки», разбор ВЫБРАТЬ отчёта. "
        "Читает Templates/**/Template.xml (не только .bsl). "
        "request=фраза/имя отчёта ИЛИ path из inspect_code.module.path. "
        "query_index=1..N для конкретного запроса. "
        "ВЕРНЁТ: tables_all, summaries, first_query_full.text.",
        {
            "request": {
                "type": "string",
                "description": "Имя/фраза отчёта или path; без хардкода конфигурации",
                "default": "",
            },
            "path": {
                "type": "string",
                "description": "path к Module.bsl или папке отчёта",
                "default": "",
            },
            "query_index": {
                "type": "integer",
                "description": "0=все кратко+первый полный; 1..N=один запрос",
                "default": 0,
            },
            "max_chars": {"type": "integer", "default": 6000},
        },
    ),
    _tool(
        "list_roles",
        "КОГДА: «какие роли есть», список ролей конфигурации. "
        "Источник: выгрузка Roles/ (не list_metadata_types). "
        "ВЕРНЁТ: roles[{name, synonym, full_name}].",
        {
            "search": {"type": "string", "default": "", "description": "фильтр по имени/синониму"},
            "limit": {"type": "integer", "default": 50},
        },
    ),
    _tool(
        "role_rights",
        "КОГДА: права конкретной роли (что можно читать/менять). "
        "role=имя из list_roles. object_filter=подстрока объекта. "
        "ВЕРНЁТ: objects[{object_ru, rights_summary}].",
        {
            "role": {"type": "string", "description": "ИмяРоли или Роль.Имя"},
            "object_filter": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 80},
        },
        ["role"],
    ),
    _tool(
        "find_roles_for_object",
        "КОГДА: «права на отчёт/форму/документ X», «какие роли дают доступ». "
        "object_name=Отчет.Имя | Report.Name | имя из find_metadata. "
        "ВЕРНЁТ: roles[{role, synonym, access}].",
        {
            "object_name": {
                "type": "string",
                "description": "Отчет.Имя | Справочник.Имя | bare name из поиска",
            },
            "right": {
                "type": "string",
                "default": "",
                "description": "фильтр: View|Read|Use|Просмотр…",
            },
            "limit": {"type": "integer", "default": 40},
        },
        ["object_name"],
    ),
    _tool(
        "list_users",
        "КОГДА: пользователи базы (справочник пользователей — имя из metadata). "
        "Не роли ИБ. Роли конфигурации — list_roles.",
        {
            "search": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 50},
            "include_invalid": {"type": "boolean", "default": False},
        },
    ),
    _tool(
        "access_overview",
        "КОГДА (ПРИОРИТЕТ): любая фраза про роли/права/пользователей. "
        "ONE-SHOT: list_roles | find_roles_for_object | list_users. "
        "request=фраза целиком. Не говори «роли недоступны».",
        {
            "request": {
                "type": "string",
                "description": "«какие роли», «права на отчёт X», «пользователи»",
            }
        },
        ["request"],
    ),
    _tool(
        "query_event_log",
        "КОГДА: выборка из журнала регистрации 1С (платформа). "
        "Фильтры: date_from/date_to ISO, level=Error|Warning|Information, "
        "user, event, search. limit≤200. "
        "ВЕРНЁТ: rows[{date,level,user,event,comment}], by_level, by_event.",
        {
            "date_from": {"type": "string", "default": ""},
            "date_to": {"type": "string", "default": ""},
            "level": {
                "type": "string",
                "default": "",
                "description": "Error|Warning|Information|Note|Ошибка|…",
            },
            "user": {"type": "string", "default": ""},
            "event": {"type": "string", "default": ""},
            "search": {"type": "string", "default": "", "description": "подстрока q"},
            "comment": {"type": "string", "default": ""},
            "metadata": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 50},
        },
    ),
    _tool(
        "analyze_event_log",
        "КОГДА (ПРИОРИТЕТ): «журнал регистрации», «ошибки за сегодня», "
        "«что в логе» — ONE-SHOT разбор фразы + выборка + сводка. "
        "request=фраза целиком. Не путать с регистрами сведений.",
        {
            "request": {
                "type": "string",
                "description": "«ошибки журнала за 7 дней», «журнал регистрации сегодня»",
                "default": "",
            },
            "date_from": {"type": "string", "default": ""},
            "date_to": {"type": "string", "default": ""},
            "level": {"type": "string", "default": ""},
            "user": {"type": "string", "default": ""},
            "search": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 100},
        },
    ),
    _tool(
        "list_active_sessions",
        "КОГДА: «активные пользователи», «кто в базе», «сеансы» — "
        "кто СЕЙЧАС подключен (сеансы ИБ). "
        "Не list_users (справочник) и не list_roles. "
        "ВЕРНЁТ: sessions[{user,application,computer,started}], users, by_application.",
        {
            "user": {"type": "string", "default": "", "description": "фильтр имени"},
            "application": {
                "type": "string",
                "default": "",
                "description": "1CV8|1CV8C|BackgroundJob|…",
            },
            "unique_users": {
                "type": "boolean",
                "default": False,
                "description": "только уникальные пользователи",
            },
        },
    ),
    _tool(
        "active_users_overview",
        "КОГДА: фраза про активных пользователей целиком — ONE-SHOT. "
        "request=«активные пользователи» / «кто сейчас в базе».",
        {
            "request": {
                "type": "string",
                "description": "фраза пользователя",
                "default": "",
            }
        },
    ),
    _tool(
        "resolve_object",
        "КОГДА: найти full_name по фразе (документ/справочник/регистр…). "
        "limit/offset — порция options: не нашёл в 15 → offset=15, limit=15. "
        "ВЕРНЁТ: top, items, options?, paging, has_more, next_offset.",
        {
            "request": {
                "type": "string",
                "description": "фраза: заказ клиента, поступление, номенклатура…",
            },
            "object_type": {
                "type": "string",
                "default": "Все",
                "description": "Все | Документы | Справочники | …",
            },
            "limit": {
                "type": "integer",
                "default": 15,
                "description": "Сколько кандидатов за раз (1–80)",
            },
            "offset": {"type": "integer", "default": 0},
            "page": {"type": "integer", "default": 0},
        },
        ["request"],
    ),
    _tool(
        "quick_find",
        "КОГДА: короткий поиск объекта (алиас resolve_object). limit/offset — порция.",
        {
            "request": {"type": "string", "default": ""},
            "search_query": {"type": "string", "default": ""},
            "object_type": {"type": "string", "default": "Все"},
            "limit": {"type": "integer", "default": 15},
            "offset": {"type": "integer", "default": 0},
            "page": {"type": "integer", "default": 0},
        },
    ),
    _tool(
        "report_builder",
        "УНИВЕРСАЛЬНЫЙ ПОСТРОИТЕЛЬ ОТЧЁТОВ (любая CF) — AUTO. "
        "Сам: найти объект (документ/регистр, не Обработка) → период → "
        "поля/группировки → запрос (в т.ч. linked/объединения) → Excel + графики. "
        "НЕ спрашивать шаги. skip_wizard=true по умолчанию (preflight). "
        "Мастер по шагам ТОЛЬКО если пользователь сказал «по шагам»/«мастер». "
        "КОГДА: «отчёт с графиком», «excel», «запихни в отчёт», «остатки в excel». "
        "ВЕРНЁТ: path/download_url + sheets + charts_count.",
        {
            "request": {
                "type": "string",
                "description": "фраза: отчёт/excel/график + тема (продажи, остатки…)",
                "default": "",
            },
            "wizard_answer": {
                "type": "string",
                "default": "",
                "description": "только для режима «по шагам»",
            },
            "full_name": {"type": "string", "default": ""},
            "period": {"type": "string", "default": ""},
            "group_by": {
                "type": "string",
                "default": "",
                "description": "контрагент | номенклатура | склад | «a,b»",
            },
            "metrics": {
                "type": "string",
                "default": "",
                "description": "sum,count | sum | count",
            },
            "value_field": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 50},
            "offset": {"type": "integer", "default": 0},
            "to_excel": {"type": "boolean", "default": True},
            "filename": {"type": "string", "default": ""},
            "title": {"type": "string", "default": ""},
            "dry_run": {
                "type": "boolean",
                "default": False,
                "description": "true = только план отчёта",
            },
            "skip_wizard": {
                "type": "boolean",
                "default": True,
                "description": "true = AUTO без вопросов (default). false + «по шагам» = мастер",
            },
            "charts_spec": {
                "type": "string",
                "default": "",
                "description": "опц. JSON диаграмм; иначе bar+pie автоматически",
            },
            "filters": {
                "type": "string",
                "default": "",
                "description": "опц.: «Склад ~ Основной; Сумма > 100000; не Контрагент ~ Ромашка» "
                "(иначе берётся из фразы)",
            },
            "sort": {
                "type": "string",
                "default": "",
                "description": "desc | asc (антитоп) | name (по алфавиту) | period",
            },
            "time_grain": {
                "type": "string",
                "default": "",
                "description": "day | week | month | quarter | year — разбить период на шаги",
            },
        },
    ),
    _tool(
        "build_report",
        "КОГДА: отчёт/список/цифры/excel по фразе менеджера (продажи, список, сколько…). "
        "НЕ сырой SQL — tool сам. to_excel=true для файла. "
        "«в каких X свойство Y» / реквизит шапки → query_by_attributes. "
        "Долги/остатки/несколько регистров → linked_query. "
        "Сравнение двух периодов → compare_periods. "
        "ВЕРНЁТ rows + manager_summary + paging (+ excel path).",
        {
            "request": {
                "type": "string",
                "description": "фраза целиком: продажи по контрагентам за прошлый год",
                "default": "",
            },
            "search_query": {"type": "string", "default": ""},
            "full_name": {
                "type": "string",
                "default": "",
                "description": "из resolve/find_metadata если уже есть",
            },
            "period": {"type": "string", "default": ""},
            "group_by": {"type": "string", "default": ""},
            "metrics": {"type": "string", "default": ""},
            "value_field": {"type": "string", "default": ""},
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Строк за раз (1–200), выбираешь сам",
            },
            "offset": {
                "type": "integer",
                "default": 0,
                "description": "Порция: 0, 50, 100…",
            },
            "page": {"type": "integer", "default": 0},
            "to_excel": {"type": "boolean", "default": False},
            "filename": {"type": "string", "default": ""},
            "title": {"type": "string", "default": ""},
        },
    ),
    _tool(
        "compare_periods",
        "КОГДА: сравнить периоды (янв vs фев, этот/прошлый месяц). "
        "Вернёт variance/contribution/analyst_summary. to_excel → xlsx с графиками.",
        {
            "full_name": {"type": "string", "default": ""},
            "search_query": {"type": "string", "default": ""},
            "period": {"type": "string", "default": ""},
            "group_by": {"type": "string", "default": ""},
            "metrics": {"type": "string", "default": ""},
            "value_field": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 50},
            "to_excel": {"type": "boolean", "default": False},
            "filename": {"type": "string", "default": ""},
            "title": {"type": "string", "default": ""},
            "filters": {
                "type": "string",
                "default": "",
                "description": "«Поле ~ Значение; Сумма > 1000» — поле из structure",
            },
            "sort": {"type": "string", "default": "", "description": "desc|asc|name|period"},
            "time_grain": {
                "type": "string",
                "default": "",
                "description": "day|week|month|quarter|year",
            },
            "top_n": {"type": "integer", "default": 0},
        },
    ),
    _tool(
        "analyst_report",
        "КОГДА: аналитика 1С. Бизнес-блоки: "
        "assortment (ассортимент/спрос/неликвиды), "
        "reserves (где заморожены деньги: склад+ДЗ), "
        "profit (маржа и рост прибыли), "
        "customers (VIP/отток клиентов — ПРОДАЖИ), "
        "suppliers (ценные/слабые ПОСТАВЩИКИ — ЗАКУПКИ, не Реализация). "
        "«поставщики ценные» → mode=suppliers или request без mode. "
        "Также: ABC, variance, aging, drill, plan_fact, turnover, funnel, anomaly, "
        "dashboard. Excel+графики: «excel/график». request — фраза целиком. "
        "full_name только из metadata; не подставляй Документ.X из памяти.",
        {
            "request": {
                "type": "string",
                "description": (
                    "фраза: анализ ассортимента; где заморожены деньги; "
                    "как заработать больше; клиентская аналитика; "
                    "какие поставщики ценные / не очень"
                ),
                "default": "",
            },
            "mode": {
                "type": "string",
                "default": "",
                "description": (
                    "assortment|reserves|profit|customers|suppliers|abc|variance|aging|"
                    "drill|plan_fact|turnover|funnel|anomaly|multi|dashboard"
                ),
            },
            "full_name": {"type": "string", "default": ""},
            "period": {"type": "string", "default": ""},
            "group_by": {"type": "string", "default": ""},
            "to_excel": {"type": "boolean", "default": False},
            "filename": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 100},
        },
    ),
]


def _system_prompt_ru() -> str:
    # Краткие правила из knowledge содержат и запреты, и таблицу маршрутизации — не дублируем
    try:
        from kb.store import get_agent_rules

        rules = (get_agent_rules() or "").strip()
        if rules and "правила (кратко)" in rules[:200]:
            return rules
    except Exception:
        pass

    from tools.route_intent import route_prompt_block

    base = (
        "Помощник по 1С (только чтение). Отвечай по-русски, коротко.\n"
        "Имена объектов — только из tools этой базы, не выдумывай.\n"
        "Excel — только если просили выгрузку/файл.\n"
        "«Что умеешь / команды / help» → agent_capabilities, выведи answer.\n"
        "READ-ONLY: не правишь CF/BSL. Запрет: «я/мы исправили», «уже исправлено», "
        "«разработчик уже вынес/оптимизировал» (если пользователь не сказал, что правил). "
        "Код из Qdrant = как есть в выгрузке. Пример BSL — только "
        "«Рекомендация (НЕ применено)», не «Исправленный вариант». "
        "Пустой ``` — не выдумывай патч, уточни запрос.\n\n"
        + route_prompt_block()
        + "\nЕсли не уверен — спроси пользователя."
    )
    # Полные правила из knowledge (если есть)
    try:
        from kb.store import get_agent_rules

        rules = (get_agent_rules() or "").strip()
        if rules and len(rules) > 80:
            # rules могут быть длинными — усечь хвост, критичное в начале файла
            if len(rules) > 6000:
                rules = rules[:6000] + "\n…(правила усечены; см. knowledge/правила_агента.md)"
            return base + "\n\n--- Правила ---\n" + rules
    except Exception:
        pass
    return base


def _manager_help() -> dict:
    from kb.store import get_manager_help

    return get_manager_help()
