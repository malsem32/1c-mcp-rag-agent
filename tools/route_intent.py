"""
Универсальная маршрутизация вопроса пользователя → tool.

Не привязана к конкретным отчётам/формам конфигурации.
Все ветки: данные / код конфигурации / справка платформы / метаданные.
"""

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Маршруты
# ---------------------------------------------------------------------------
ROUTE_DATA = "data"  # цифры/списки из базы → build_report
ROUTE_ATTR = "attr"  # список по реквизитам шапки → query_by_attributes
ROUTE_CODE = "code"  # откуда данные, модуль, форма, логика отчёта → inspect_code
ROUTE_HELP = "help"  # синтаксис BSL / язык запросов / «как в 1С» → code_help
ROUTE_META = "meta"  # «какие объекты есть», full_name → resolve/find_metadata
ROUTE_STRUCTURE = "structure"  # реквизиты, ТЧ, формы, layout → describe_object
ROUTE_ACCESS = "access"  # роли, права, пользователи → access_overview / list_roles
ROUTE_EVENTLOG = "eventlog"  # журнал регистрации → analyze_event_log
ROUTE_SESSIONS = "sessions"  # активные пользователи / сеансы ИБ
ROUTE_CONN = "connection"  # связь с 1С
ROUTE_MOVEMENTS = "movements"  # регистры проведения / Движения → analyze_object_movements

# Классы метаданных для широкого поиска (HTTP list/search)
ALL_META_CLASSES: tuple[str, ...] = (
    "Документы",
    "Справочники",
    "РегистрыСведений",
    "РегистрыНакопления",
    "Отчеты",
    "Обработки",
    "ОбщиеФормы",
    "ОбщиеМодули",
    "Перечисления",
    "ПланыСчетов",
    "ПланыВидовХарактеристик",
    "БизнесПроцессы",
    "Задачи",
    "Роли",
    "Подсистемы",
    "Константы",
    "ПараметрыСеанса",
    "ПланыОбмена",
    "ЖурналыДокументов",
    "РегистрыБухгалтерии",
    "HTTPСервисы",
    "РегламентныеЗадания",
)


def is_sessions_question(phrase: str) -> bool:
    """Кто сейчас в базе (активные сеансы)."""
    pl = _norm(phrase)
    if not pl:
        return False
    markers = (
        "активные пользователи",
        "активных пользователей",
        "кто в базе",
        "кто сейчас в",
        "текущие сеансы",
        "активные сеансы",
        "список сеансов",
        "сеансы иб",
        "подключенные пользователи",
        "кто онлайн",
        "active users",
        "active sessions",
    )
    if any(m in pl for m in markers):
        return True
    if re.search(r"активн\w*\s+пользовател", pl):
        return True
    # одно слово «сеансы» / «сеанс»
    if re.fullmatch(r"сеанс\w*", pl.strip()):
        return True
    if re.search(r"\bсеанс", pl) and re.search(
        r"(актив|текущ|список|кто|подключ|баз)", pl
    ):
        return True
    return False


def is_eventlog_question(phrase: str) -> bool:
    """Журнал регистрации платформы."""
    pl = _norm(phrase)
    if not pl:
        return False
    if re.search(r"журнал\w*\s+регистрац", pl) or re.search(
        r"регистрац\w*\s+журнал", pl
    ):
        return True
    markers = (
        "eventlog",
        "event log",
        "лог ошибок",
        "логи ошибок",
        "записи журнала",
        "события журнала",
        "выгрузить журнал",
    )
    if any(m in pl for m in markers):
        return True
    # «ошибки в журнале» / «что в логе 1с»
    if re.search(r"журнал", pl) and re.search(
        r"(ошибк|предупрежд|событ|пользовател|сегодня|вчера|недел|дн(я|ей|и)|лог)",
        pl,
    ):
        return True
    if re.search(r"\bлог", pl) and re.search(r"(ошибк|1с|1c|сегодня|вчера)", pl):
        return True
    return False


def is_access_question(phrase: str) -> bool:
    """Роли, права, пользователи, ACL конфигурации."""
    pl = _norm(phrase)
    if not pl:
        return False
    markers = (
        "права на",
        "права для",
        "права доступа",
        "какие роли",
        "список рол",
        "все роли",
        "роли есть",
        "роли в конфигурац",
        "доступ к",
        "доступ на",
        "кто может",
        "кто имеет",
        "пользовател",
        "группы пользователей",
        "права рол",
    )
    if any(m in pl for m in markers):
        return True
    if re.search(r"\bрол[иьяе]\b", pl) and not re.search(
        r"\b(поля|реквизит|колонк)\b", pl
    ):
        return True
    if re.search(r"\bправ[ао]\b", pl) and re.search(
        r"\b(отч[её]т|форм|документ|справочник|обработк|объект|регистр|модул)\b",
        pl,
    ):
        return True
    # «права на <имя>» без класса — тоже access
    if re.search(r"\bправ[ао]\s+на\b", pl) or re.search(r"\bдоступ\s+к\b", pl):
        return True
    return False


# стоп-слова при очистке поисковой фразы для имён объектов
_SEARCH_NOISE = frozenset(
    {
        "откуда",
        "берет",
        "берёт",
        "берут",
        "берутся",
        "попадают",
        "попадает",
        "данные",
        "данный",
        "данных",
        "как",
        "работает",
        "работают",
        "работа",
        "логика",
        "логику",
        "модуль",
        "модуля",
        "модуле",
        "код",
        "кода",
        "коде",
        "посмотри",
        "посмотреть",
        "покажи",
        "покажите",
        "найди",
        "найти",
        "нужно",
        "надо",
        "есть",
        "это",
        "что",
        "чем",
        "где",
        "для",
        "про",
        "или",
        "если",
        "можно",
        "вообще",
        "именно",
        "пожалуйста",
        "расскажи",
        "объясни",
        "подскажи",
    }
)

# слова класса (не имя объекта) — убираем из search, если есть ещё токены
_CLASS_LEAD = frozenset(
    {
        "отчет",
        "отчёт",
        "отчета",
        "отчёта",
        "отчеты",
        "отчёты",
        "форма",
        "формы",
        "форму",
        "обработка",
        "обработки",
        "документ",
        "документа",
        "документы",
        "справочник",
        "справочника",
        "справочники",
        "регистр",
        "регистра",
        "регистры",
        "общий",
        "общая",
        "общие",
        "модуль",
    }
)


def _norm(s: str) -> str:
    return (s or "").lower().replace("ё", "е").strip()


def clean_object_search(phrase: str) -> str:
    """
    «отчет <имя> откуда берет данные» → «<имя>».
    Универсально: срезает класс/шум, имя объекта не хардкодится.
    """
    raw = (phrase or "").strip()
    if not raw:
        return ""
    words = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9_]+", _norm(raw))
    keep = [w for w in words if w not in _SEARCH_NOISE and len(w) >= 2]
    while keep and keep[0] in _CLASS_LEAD and len(keep) > 1:
        keep.pop(0)
    # хвост «… данные / код»
    while keep and keep[-1] in _CLASS_LEAD | _SEARCH_NOISE:
        keep.pop()
    return " ".join(keep) if keep else raw


def is_code_config_question(phrase: str) -> bool:
    """
    Вопрос про устройство/источник данных В КОНФИГУРАЦИИ (BSL/СКД),
    а не про цифры в базе.
    """
    pl = _norm(phrase)
    if not pl:
        return False

    # явные маркеры «исследовать код/источник»
    markers = (
        "откуда",
        "берет данн",
        "берёт данн",
        "берут данн",
        "откуда данн",
        "источник данн",
        "как устроен",
        "как устроена",
        "как устроено",
        "как работает отчет",
        "как работает отчёт",
        "как работает форма",
        "как работает модул",
        "как работает эта процедур",
        "как работает процедур",
        "как работает функци",
        "отследи",
        "отследить",
        "проследи",
        "проследить",
        "логика",
        "логику",
        "логики",
        "логик заполн",
        "в коде",
        "в модул",
        "модуль формы",
        "модуль отчета",
        "модуль отчёта",
        "модуль объект",
        "процедур",
        "функци",
        "текстзапроса",
        "текст запроса",
        "запрос в скл",
        "запрос в скд",
        "шаблон скд",
        "шаблон скл",
        "схема компонов",
        "набор данных",
        "наборыданных",
        "динамический список",
        "основная таблица",
        "посмотри код",
        "покажи код",
        "разбери код",
        "изучи запрос",
        "в конфигурац",
    )
    if any(m in pl for m in markers):
        return True

    # «отчет X / форма X / обработка X» + глагол про устройство без периода/сумм
    looks_config_object = bool(
        re.search(
            r"\b(отчет|отчёт|форма|обработка|общий\s+модул|общая\s+форма|"
            r"общий\s+модуль|http[- ]?сервис)\b",
            pl,
        )
    )
    asks_how = bool(
        re.search(
            r"\b(как|откуда|почему|зачем|что делает|из чего|на чем|на чём)\b",
            pl,
        )
    )
    asks_data_numbers = bool(
        re.search(
            r"\b(сколько|сумм|итог|за\s+\d{4}|январ|феврал|март|апрел|июн|июл|"
            r"август|сентябр|октябр|ноябр|декабр|остатк|оборот|список\s+за|"
            r"выгруз|excel|эксель|по\s+контрагент)\b",
            pl,
        )
    )
    if looks_config_object and asks_how and not asks_data_numbers:
        return True

    # «общая форма …» почти всегда про код/UI, не про ВЫБРАТЬ цифр
    if re.search(r"общ\w*\s+форм", pl) and not asks_data_numbers:
        return True
    if re.search(r"общ\w*\s+модул", pl):
        return True

    return False


def is_platform_help_question(phrase: str) -> bool:
    """Синтаксис/методы платформы 1С (не объекты конфигурации)."""
    pl = _norm(phrase)
    if not pl:
        return False

    # код CF: «отследи процедуру X в документе Y» — НЕ справка платформы
    if re.search(
        r"(отслед|прослед|как\s+работает\s+(эта\s+)?(процедур|функци)|"
        r"в\s+документ[еу]|в\s+справочник|модул\w*\s+объект|"
        r"документ\s*\.|справочник\s*\.)",
        pl,
    ):
        # длинный PascalCase идентификатор = процедура CF, не «что такое Процедура»
        if re.search(r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_]{8,}", phrase or ""):
            return False
        if is_code_config_question(pl):
            return False

    if is_code_config_question(pl):
        # «как работает форма/отчёт <имя>» — код конфига, не справка платформы
        if re.search(
            r"\b(форма|отчет|отчёт|обработка|модул|документ|справочник|"
            r"процедур|функци|заполн|проведен)\b",
            pl,
        ) and not re.search(
            r"\b(синтаксис|оператор|для\s+каждого|пока\s+цикл|"
            r"что\s+такое\s+процедур|как\s+написать\s+процедур)\b",
            pl,
        ):
            return False

    markers = (
        "синтаксис",
        "язык запросов",
        "язык запрос",
        "выбрать ",
        "выбрать с",
        "как написать",
        "как писать",
        "как пишется",
        "что такое",
        "для чего",
        "чем отличается",
        "расскажи про",
        "пример кода",
        "цикл",
        "конеццикла",
        "процедура",
        "функция",
        "массив.",
        "запрос.",
        "httpсоединение",
        "встроенн",
        "платформ",
        "bsl",
        "добавитькдате",
        "началопериод",
        "с группировкой",
        "с группировками",
    )
    if any(m in pl for m in markers):
        # «отчёт с группировкой по контрагентам» — data, не syntax
        if any(
            x in pl
            for x in (
                "продаж",
                "реализ",
                "excel",
                "эксель",
                "по контрагент",
                "за 20",
                "сколько",
            )
        ) and not any(
            x in pl for x in ("как написать", "как писать", "синтаксис", "функци")
        ):
            return False
        # «процедура/функция X в документе» — код, не help
        if re.search(r"\b(процедур|функци)\b", pl) and re.search(
            r"\b(документ|справочник|модул|отслед|прослед|как\s+работает)\b", pl
        ):
            return False
        return True
    # «как написать выбрать…» / «напиши выбрать»
    if re.search(
        r"(как\s+написать|как\s+писать|напиши|пример)\s+.*\b(выбрать|запрос|групп)",
        pl,
    ):
        return True
    # «как работает Массив» / метод платформы без имени объекта конфигурации
    if re.search(r"\bкак\s+работает\b", pl) and not re.search(
        r"\b(отчет|отчёт|форма|обработка|документ|справочник|регистр|"
        r"процедур|функци|заполн)\b",
        pl,
    ):
        return True
    return False


def is_structure_question(phrase: str) -> bool:
    """
    Реквизиты, ТЧ, список форм/макетов, layout формы — meta structure, не BSL.
    «как работает форма» / «модуль формы» → code, не сюда.
    """
    pl = _norm(phrase)
    if not pl:
        return False
    # код/логика — не structure
    if re.search(
        r"(модуль\s+форм|как\s+работает\s+форм|логика|процедур|функци|"
        r"откуда\s+данн|текст\s+запрос|скд|скл)",
        pl,
    ):
        return False
    markers = (
        "реквизит",
        "реквизиты",
        "табличн",
        "табличные части",
        "табличная часть",
        "состав объекта",
        "структура объекта",
        "структура документ",
        "структура справочник",
        "какие поля",
        "какие реквизит",
        "список реквизит",
        "макет формы",
        "layout формы",
        "элементы формы",
        "datapath",
        "data_path",
        "describe_object",
        "get_object_structure",
    )
    if any(m in pl for m in markers):
        return True
    # «формы документа X» / «какие формы у» / «макеты объекта»
    if re.search(
        r"(какие\s+форм|список\s+форм|формы\s+(документ|справочник|объект)|"
        r"макеты\s+(документ|справочник|объект|отч)|"
        r"какие\s+макет|"
        r"прочитай\s+(реквизит|форм|тч|таблич)|"
        r"покажи\s+(реквизит|тч|табличн|форм|макет\s+форм))",
        pl,
    ):
        return True
    return False


def is_meta_browse_question(phrase: str) -> bool:
    """«какие документы/справочники есть», поиск full_name без кода и без цифр."""
    pl = _norm(phrase)
    if is_code_config_question(pl) or is_platform_help_question(pl):
        return False
    if is_structure_question(pl):
        return False
    return bool(
        re.search(
            r"(какие\s+(есть\s+)?(документ|справочник|регистр|отчет|отчёт|обработк)\w*"
            r"|найди\s+(документ|справочник|объект|отчет|отчёт)\w*"
            r"|как\s+называется"
            r"|full_name"
            r"|метаданн"
            r"|список\s+(всех\s+)?(документ|справочник|регистр|отчет|отчёт)ов?\w*)",
            pl,
        )
    )


def classes_for_phrase(phrase: str, object_type: str = "Все") -> list[str]:
    """
    Какие классы метаданных смотреть — по словам платформы, не по именам объектов.
    """
    otype = (object_type or "Все").strip()
    if otype and otype not in ("Все", "все", "ALL", "all"):
        fixed = {
            "документ": "Документы",
            "документы": "Документы",
            "справочник": "Справочники",
            "справочники": "Справочники",
            "отчет": "Отчеты",
            "отчёт": "Отчеты",
            "отчеты": "Отчеты",
            "отчёты": "Отчеты",
            "reports": "Отчеты",
            "обработка": "Обработки",
            "обработки": "Обработки",
            "dataprocessors": "Обработки",
            "регистрсведений": "РегистрыСведений",
            "регистрысведений": "РегистрыСведений",
            "регистрнакопления": "РегистрыНакопления",
            "регистрынакопления": "РегистрыНакопления",
            "общаяформа": "ОбщиеФормы",
            "общиеформы": "ОбщиеФормы",
            "общиймодуль": "ОбщиеМодули",
            "общиемодули": "ОбщиеМодули",
        }.get(otype.lower().replace(" ", ""))
        if fixed:
            return [fixed]
        if otype in ALL_META_CLASSES:
            return [otype]
        return [otype]

    pl = _norm(phrase)
    if re.search(r"\b(отчет|отчёт|reports?)\b", pl) and not re.search(
        r"\bдокумент", pl
    ):
        return ["Отчеты"]
    if re.search(r"\bобработк", pl):
        return ["Обработки"]
    if re.search(r"общ\w*\s+форм", pl):
        return ["ОбщиеФормы"]
    if re.search(r"общ\w*\s+модул", pl):
        return ["ОбщиеМодули"]
    if re.search(r"\bсправочник", pl):
        return ["Справочники"]
    if re.search(r"\bдокумент", pl):
        return ["Документы"]
    if re.search(r"регистр\w*\s+свед", pl):
        return ["РегистрыСведений"]
    if re.search(r"регистр\w*\s+накоп", pl):
        return ["РегистрыНакопления"]
    if re.search(r"\bрегистр", pl) or re.search(r"\b(остатк|оборот|срез)\w*\b", pl):
        return ["РегистрыНакопления", "РегистрыСведений"]
    if re.search(r"\b(план\w*\s+счет|хозрасчетн)", pl):
        return ["ПланыСчетов"]
    # широко: все основные классы
    return list(ALL_META_CLASSES)


def _route_from_decision(
    decision: dict[str, Any], raw: str, clean: str, classes: list[str]
) -> dict[str, Any]:
    """Решение router.classify → формат route_user_message."""
    tool = str(decision.get("tool") or "")
    args = dict(decision.get("args") or {})
    args_s = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return {
        "route": decision.get("route") or ROUTE_DATA,
        "prefer_tool": tool,
        "search_clean": clean,
        "classes": classes,
        "reason": decision.get("reason") or "",
        "action_for_agent": f"{tool}({args_s}). Ответ по tool result.",
        "tool_args": args,
    }


def _code_route(raw: str, pl: str, clean: str, classes: list[str]) -> dict[str, Any]:
    """Вопрос про код/источник данных: read_skd_query (текст СКД) или inspect_code."""
    want_skd = any(
        x in pl
        for x in (
            "текст запрос",
            "запрос в скл",
            "запрос в скд",
            "шаблон скд",
            "шаблон скл",
            "скд",
            "скл",
            "схема компонов",
            "изучи запрос",
            "покажи запрос",
        )
    )
    if want_skd:
        return {
            "route": ROUTE_CODE,
            "prefer_tool": "read_skd_query",
            "search_clean": clean,
            "classes": classes,
            "reason": "текст запроса СКД (шаблон XML отчёта/обработки)",
            "action_for_agent": (
                f"read_skd_query(request={raw!r}) — тексты ВЫБРАТЬ из Templates. "
                "Потом ответь по tables_all и first_query_full. "
                "Не говори «XML недоступен»."
            ),
            "tool_args": {"request": raw},
        }
    return {
        "route": ROUTE_CODE,
        "prefer_tool": "inspect_code",
        "search_clean": clean,
        "classes": classes,
        "reason": "вопрос про код/источник данных конфигурации (BSL/СКД)",
        "action_for_agent": (
            f"inspect_code(request={raw!r}) — один вызов, потом ответь. "
            "Полный ВЫБРАТЬ из СКД: read_skd_query. "
            "Не find_metadata alone. Не build_report."
        ),
        "tool_args": {"request": raw},
    }


def route_user_message(phrase: str) -> dict[str, Any]:
    """
    Главный роутер. Возвращает:
      route, prefer_tool, search_clean, classes, reason, action_for_agent
    """
    raw = (phrase or "").strip()
    pl = _norm(raw)
    clean = clean_object_search(raw)
    classes = classes_for_phrase(raw)

    # единый роутер (порядок preflight): решение с tool → route; code без tool → ветка кода
    from tools.router import classify

    decision = classify(raw)
    if decision and decision.get("tool"):
        return _route_from_decision(decision, raw, clean, classes)
    if decision and decision.get("route") == ROUTE_CODE:
        return _code_route(raw, pl, clean, classes)

    if re.search(r"\b(связ|подключ|ping|test_connection|коннект)\b", pl):
        return {
            "route": ROUTE_CONN,
            "prefer_tool": "test_connection",
            "search_clean": clean,
            "classes": classes,
            "reason": "проверка связи с 1С",
            "action_for_agent": "test_connection()",
        }

    if is_sessions_question(raw):
        return {
            "route": ROUTE_SESSIONS,
            "prefer_tool": "list_active_sessions",
            "search_clean": clean,
            "classes": [],
            "reason": "активные сеансы / кто сейчас в базе",
            "action_for_agent": (
                f"list_active_sessions() или active_users_overview(request={raw!r}). "
                "Не list_users (справочник) — это сеансы ИБ."
            ),
            "tool_args": {"request": raw},
        }

    if is_eventlog_question(raw):
        return {
            "route": ROUTE_EVENTLOG,
            "prefer_tool": "analyze_event_log",
            "search_clean": clean,
            "classes": [],
            "reason": "журнал регистрации платформы 1С",
            "action_for_agent": (
                f"analyze_event_log(request={raw!r}) или query_event_log. "
                "Не путай с РегистрСведений.*Журнал*."
            ),
            "tool_args": {"request": raw},
        }

    if (
        re.search(
            r"документ\w*.{0,80}(?:№|номер[ауе]?\s+[a-zа-яё0-9])",
            pl,
        )
        and "пуст" not in pl
    ):
        return {
            "route": ROUTE_DATA,
            "prefer_tool": "document_by_number",
            "search_clean": clean,
            "classes": ["Документы"],
            "reason": "документ по номеру",
            "action_for_agent": (
                f"document_by_number(request={raw!r}). "
                "Тип только из find_metadata этой CF."
            ),
            "tool_args": {"request": raw},
        }

    if is_access_question(raw) or re.search(
        r"(ролл?\w*|права?\w*).*(удал|измен|интерактив)|"
        r"(удал|измен|интерактив).*(ролл?\w*|справочник)|"
        r"interactive\s*delete|право\s+на\s+удален",
        pl,
    ):
        # права InteractiveDelete/Edit на справочники — точный tool
        if re.search(
            r"(удал|измен|интерактив|interactive|edit|delete|справочник|документ)",
            pl,
        ) and re.search(r"(рол|прав|доступ)", pl):
            try:
                from tools.roles_access import rights_object_type_from_phrase

                ot = rights_object_type_from_phrase(raw)
            except Exception:
                ot = ""
            return {
                "route": ROUTE_ACCESS,
                "prefer_tool": "list_roles_by_right",
                "search_clean": clean,
                "classes": ["Роли"],
                "reason": (
                    f"роли с правом удаления/изменения ({ot})"
                    if ot
                    else "роли с правом удаления/изменения (все классы)"
                ),
                "action_for_agent": (
                    "list_roles_by_right("
                    "rights='InteractiveDelete,Update,Edit,InteractiveSetDeletionMark,Delete'"
                    + (f", object_type={ot!r}" if ot else "")
                    + "). Перечисли roles[] + rights_summary."
                ),
                "tool_args": {
                    "rights": "InteractiveDelete,Update,Edit,InteractiveSetDeletionMark,Delete",
                    "object_type": ot,
                },
            }
        return {
            "route": ROUTE_ACCESS,
            "prefer_tool": "access_overview",
            "search_clean": clean,
            "classes": ["Роли"],
            "reason": "роли / права / пользователи справочника",
            "action_for_agent": (
                f"access_overview(request={raw!r}) или "
                "list_roles / list_roles_by_right / find_roles_for_object / list_users. "
                "Активные сейчас → list_active_sessions."
            ),
            "tool_args": {"request": raw},
        }

    # Цепочка вызовов — раньше movements (иначе «ОбработкаПроведения» уводит в регистры)
    if re.search(
        r"(прослед|цепочк|кто\s+вызыв|откуда\s+вызыв|call\s*chain|trace_calls|"
        r"вызов\w*\s+процедур|путь\s+вызов)",
        pl,
    ):
        return {
            "route": ROUTE_CODE,
            "prefer_tool": "trace_calls",
            "search_clean": clean,
            "classes": classes,
            "reason": "цепочка вызовов BSL",
            "action_for_agent": (
                f"trace_calls(request={raw!r}) или path=… + procedure=…. "
                "depth=2..3. Ответ по edges/summary_lines."
            ),
            "tool_args": {"request": raw},
        }

    # Почему не заполнилось / fill reason — до structure
    try:
        from tools.linked_query import is_debt_unpaid_question
        from tools.explain_fill import (
            is_fill_reason_question,
            is_posting_error_question,
            is_status_question,
        )
    except Exception:
        is_fill_reason_question = lambda _p: False  # noqa: E731
        is_posting_error_question = lambda _p: False  # noqa: E731
        is_status_question = lambda _p: False  # noqa: E731
        is_debt_unpaid_question = lambda _p: False  # noqa: E731

    # security: Выполнить/Вычислить / инъекции
    try:
        from tools.code_security import is_injection_scan_question
    except Exception:
        is_injection_scan_question = lambda _p: False  # noqa: E731
    if is_injection_scan_question(raw):
        return {
            "route": ROUTE_CODE,
            "prefer_tool": "scan_code_injection",
            "search_clean": clean,
            "classes": classes,
            "reason": "скан Выполнить/Вычислить / инъекции",
            "action_for_agent": (
                "scan_code_injection(scope='exchange' или 'all'). "
                "Не путай с Запрос.Выполнить(). Ответ по risk+path."
            ),
            "tool_args": {"scope": "exchange" if "обмен" in pl else "all"},
        }

    # роли: InteractiveDelete / изменение справочников
    if re.search(
        r"(ролл?\w*|права?\w*).*(удал|измен|интерактив)|"
        r"(удал|измен|интерактив).*(ролл?\w*|справочник)|"
        r"interactive\s*delete|право\s+на\s+удален",
        pl,
    ):
            try:
                from tools.roles_access import rights_object_type_from_phrase

                ot = rights_object_type_from_phrase(raw)
            except Exception:
                ot = ""
            return {
                "route": ROUTE_ACCESS,
                "prefer_tool": "list_roles_by_right",
                "search_clean": clean,
                "classes": classes,
                "reason": (
                    f"роли по праву ({ot})"
                    if ot
                    else "роли по праву (все классы метаданных)"
                ),
                "action_for_agent": (
                    "list_roles_by_right(rights='InteractiveDelete,Update,Edit'"
                    + (f", object_type={ot!r}" if ot else "")
                    + "). Перечисли roles[]."
                ),
                "tool_args": {
                    "rights": "InteractiveDelete,Update,Edit,InteractiveSetDeletionMark,Delete",
                    "object_type": ot,
                },
            }

    # «где вызывается / покажи код / кусок модуля»
    if re.search(
        r"(где\s+в\s+коде|где\s+вызыв|покажи\s+кусок|покажи\s+код|"
        r"фрагмент\s+кода|кто\s+вызыв|usages|найди\s+вызов)",
        pl,
    ):
        if re.search(r"кто\s+вызыв|где\s+вызыв\w*\s+", pl):
            return {
                "route": ROUTE_CODE,
                "prefer_tool": "find_callers",
                "search_clean": clean,
                "classes": classes,
                "reason": "reverse usages — кто вызывает процедуру",
                "action_for_agent": (
                    "find_callers(module=…, procedure=…) из фразы; "
                    "или search_code + read_code. Не movements."
                ),
                "tool_args": {"request": raw},
            }
        return {
            "route": ROUTE_CODE,
            "prefer_tool": "search_code",
            "search_clean": clean,
            "classes": classes,
            "reason": "поиск фрагмента кода",
            "action_for_agent": (
                f"search_code(query=…) → read_code(path, start_line). "
                f"Фраза: {raw!r}. Не analyze_object_movements."
            ),
            "tool_args": {"request": raw},
        }

    # Неоплаченные / долг / многоисточниковые данные
    try:
        from tools.linked_query import is_linked_or_debt_data_question
    except Exception:
        is_linked_or_debt_data_question = is_debt_unpaid_question  # noqa: F821

    if is_debt_unpaid_question(raw) or is_linked_or_debt_data_question(raw):
        return {
            "route": ROUTE_DATA,
            "prefer_tool": "linked_query",
            "search_clean": clean,
            "classes": classes,
            "reason": "задолженность/остатки/связь нескольких объектов — linked_query",
            "action_for_agent": (
                f"linked_query(request={raw!r}, execute=true). "
                "Имена только resolve/structure ЭТОЙ базы. "
                "Вирт.таблицы .Остатки — да; вложенный ВЫБРАТЬ — нет. "
                "Менеджеру — таблица, не теория JOIN. "
                "Опционально sources= и fields_needed= из фразы пользователя."
            ),
            "tool_args": {"request": raw},
        }

    # Статусы документа → structure + status_fields / enum
    if is_status_question(raw):
        return {
            "route": ROUTE_STRUCTURE,
            "prefer_tool": "get_object_structure",
            "search_clean": clean,
            "classes": classes,
            "reason": "статусы / значения перечисления",
            "action_for_agent": (
                "resolve full_name → get_object_structure(full_name). "
                "Смотри status_fields, enum_fields, enum_values_for_status — "
                "перечисли ВСЕ значения. Не говори «статуса нет» если status_fields не пуст."
            ),
            "tool_args": {"request": raw},
        }

    # Не проводится / блокировка / ошибка проведения
    if is_posting_error_question(raw):
        return {
            "route": ROUTE_CODE,
            "prefer_tool": "explain_fill",
            "search_clean": clean,
            "classes": classes,
            "reason": "ошибка/отказ проведения / блокировки",
            "action_for_agent": (
                f"1) explain_fill/analyze_object_movements + search_code по Отказ/Сообщить "
                f"в ObjectModule документа (request={raw!r}). "
                "2) При блокировках: query_event_log / analyze_event_log. "
                "3) trace_calls(procedure=ОбработкаПроведения) при необходимости. "
                "Не общие советы без tool."
            ),
            "tool_args": {"request": raw},
        }

    if is_fill_reason_question(raw):
        return {
            "route": ROUTE_CODE,
            "prefer_tool": "explain_fill",
            "search_clean": clean,
            "classes": classes,
            "reason": "почему не заполнилось / где заполняется реквизит (универсально)",
            "action_for_agent": (
                f"explain_fill(request={raw!r}) "
                "[field=ИмяРеквизита, full_name=Документ.X если известны]. "
                "Затем read_code по hits. НЕ только get_object_structure."
            ),
            "tool_args": {"request": raw},
        }

    # Регистры проведения / движения — ДО structure
    try:
        from tools.object_movements import is_movements_or_posting_question
    except Exception:
        is_movements_or_posting_question = lambda _p: False  # noqa: E731

    if is_movements_or_posting_question(raw):
        return {
            "route": ROUTE_MOVEMENTS,
            "prefer_tool": "analyze_object_movements",
            "search_clean": clean,
            "classes": classes,
            "reason": "регистры проведения / Движения / что пишет объект (универсально)",
            "action_for_agent": (
                f"analyze_object_movements(request={raw!r}) "
                "[или full_name='Документ.Имя' если уже известен]. "
                "Один tool — ответ по summary/registers. "
                "НЕ get_object_structure alone. НЕ «подождите, анализирую»."
            ),
            "tool_args": {"request": raw},
        }

    if is_structure_question(raw):
        return {
            "route": ROUTE_STRUCTURE,
            "prefer_tool": "describe_object",
            "search_clean": clean,
            "classes": classes,
            "reason": "реквизиты / ТЧ / формы / макет формы объекта",
            "action_for_agent": (
                "resolve full_name → describe_object(full_name) "
                "(реквизиты, tabular_sections, forms, templates, form_layout). "
                "Конкретная форма → describe_form(full_name, form_name). "
                "Не говори «нельзя прочитать форму/реквизиты»."
            ),
            "tool_args": {"request": raw},
        }

    if is_code_config_question(raw):
        return _code_route(raw, pl, clean, classes)

    if is_platform_help_question(raw):
        return {
            "route": ROUTE_HELP,
            "prefer_tool": "code_help",
            "search_clean": clean,
            "classes": classes,
            "reason": "справка платформы / синтаксис BSL",
            "action_for_agent": f"code_help(query={raw!r}) или knowledge_search",
            "tool_args": {"query": raw},
        }

    if is_meta_browse_question(raw):
        return {
            "route": ROUTE_META,
            "prefer_tool": "resolve_object",
            "search_clean": clean,
            "classes": classes,
            "reason": "обзор/поиск объектов метаданных",
            "action_for_agent": f"resolve_object(request={raw!r})",
            "tool_args": {"request": raw},
        }

    if not pl:
        return {
            "route": ROUTE_META,
            "prefer_tool": "manager_help",
            "search_clean": "",
            "classes": list(ALL_META_CLASSES),
            "reason": "пустой запрос",
            "action_for_agent": "manager_help()",
        }
    # если только имя объекта без data-маркеров — всё равно data через build_report
    return {
        "route": ROUTE_DATA,
        "prefer_tool": "build_report",
        "search_clean": clean,
        "classes": classes,
        "reason": "данные/списки/суммы из базы",
        "action_for_agent": f"build_report(request={raw!r})",
        "tool_args": {"request": raw},
    }


def route_prompt_block() -> str:
    """Короткий блок для system prompt (все случаи)."""
    return (
        "Маршрутизация (универсально, любая конфигурация):\n"
        "0) ЗАПРЕТ: нельзя писать «подождите / анализирую / сейчас изучаю» "
        "вместо вызова tool. Сначала tool, потом ответ по фактам.\n"
        "0b) READ-ONLY: нельзя «я/мы исправили», «уже исправлено», "
        "«разработчик уже оптимизировал» — код CF как есть; "
        "BSL-пример только «Рекомендация (НЕ применено)». "
        "Пустой ``` — не выдумывать патч.\n"
        "1) «по каким регистрам проводится / какие движения / что пишет документ» → "
        "analyze_object_movements(request=фраза) [+ full_name если известен]. "
        "НЕ только get_object_structure.\n"
        "1b) «почему не заполнился / пустой реквизит» → explain_fill(request=…).\n"
        "1c) «проследи вызовы / цепочка процедур» → trace_calls(request=…|path=…).\n"
        "1d) скинули .epf/.erf/.cfe → inspect_external_processing(path=…, request=…). "
        "Не inspect_code (это CF в Qdrant). Не запускать в базе.\n"
        "2) «откуда данные / как устроен отчёт|форма|модуль» → "
        "inspect_code(request=фраза). НЕ только find_metadata.\n"
        "2b) «текст запроса / СКД» → read_skd_query(request=…).\n"
        "2c) «реквизиты / ТЧ / формы» → describe_object / get_object_structure.\n"
        "3) суммы/списки/за период → build_report(request=фраза).\n"
        "3b) «в каких X свойство Y» / реквизит шапки → query_by_attributes.\n"
        "4) синтаксис BSL → code_help(query=фраза).\n"
        "5) «какие документы есть» → resolve_object / find_metadata.\n"
        "6) роли/права → access_overview / list_roles.\n"
        "7) журнал регистрации → analyze_event_log.\n"
        "8) активные сеансы → list_active_sessions.\n"
        "Код runtime — только Qdrant. Не выдумывай full_name."
    )
