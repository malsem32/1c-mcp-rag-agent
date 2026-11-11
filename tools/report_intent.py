"""
Разбор фразы менеджера → intent базового отчёта (универсально, без имён конфигурации).
"""

from __future__ import annotations

import re
from typing import Any


def _norm(s: str) -> str:
    return (s or "").lower().replace("ё", "е")


def extract_period_from_text(text: str) -> str:
    """Вытащить кусок периода из общей фразы запроса."""
    t = _norm(text)
    # с ... по ...
    m = re.search(
        r"((?:с|от)\s+\d{1,4}[./\-]\d{1,2}[./\-]\d{1,4}"
        r"(?:\s+\d{1,2}:\d{2})?\s+(?:по|до)\s+\d{1,4}[./\-]\d{1,2}[./\-]\d{1,4}"
        r"(?:\s+\d{1,2}:\d{2})?)",
        t,
    )
    if m:
        return m.group(1).strip()
    # последний час/месяц/…
    m = re.search(
        r"((?:за\s+)?(?:последн\w*|прошл\w*)\s+(?:\d+\s+)?"
        r"(?:минут\w*|час\w*|день|дня|дней|недел\w*|месяц\w*|год\w*|квартал\w*))",
        t,
    )
    if m:
        return m.group(1).strip()
    # январь и февраль 2015 / январь 2015
    m = re.search(
        r"((?:январ\w*|феврал\w*|март\w*|апрел\w*|ма[йя]|июн\w*|июл\w*|"
        r"август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)"
        r"(?:\s*(?:и|,)\s*(?:январ\w*|феврал\w*|март\w*|апрел\w*|ма[йя]|июн\w*|июл\w*|"
        r"август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*))*"
        r"(?:\s+\d{4})?)",
        t,
    )
    if m:
        return m.group(1).strip()
    # два+ года: 2015 и 2016 / за 2016 и 2015 года / 2015–2016
    years = re.findall(r"\b((?:19|20)\d{2})\b", t)
    if len(years) >= 2:
        # сохранить порядок упоминания, уникальные
        seen: list[str] = []
        for y in years:
            if y not in seen:
                seen.append(y)
        if len(seen) >= 2:
            return " и ".join(seen)
    # 2015 год / за 2015
    m = re.search(r"((?:за|в|весь)?\s*(?:год\s+)?(?:19|20)\d{2}(?:\s*год(?:у|а)?)?)", t)
    if m:
        return m.group(1).strip()
    # только 2015
    m = re.search(r"\b((?:19|20)\d{2})\b", t)
    if m:
        return m.group(1)
    # сегодня / вчера
    if "сегодня" in t:
        if re.search(r"\d{1,2}:\d{2}", t):
            m2 = re.search(r"(сегодня.*?)\s*$", t)
            return (m2.group(1) if m2 else "сегодня").strip()
        return "сегодня"
    if "вчера" in t:
        return "вчера"
    return ""


# «какие есть / посмотри / список» — обзор объектов, не агрегаты
# (не «какие продажи за 2015» — там есть период/суммы)
_LIST_EXISTING_RE = re.compile(
    r"(?:"
    r"какие\s+есть|какой\s+есть|какая\s+есть|какие\s+у\s+нас|что\s+есть|"
    r"что\s+у\s+нас|есть\s+ли|имеющ\w*|наличн\w*|"
    r"можешь\s+посмотреть|можешь\s+показать|"
    r"посмотри(?:те)?\s+(?:какие\s+)?|"
    r"посмотреть\s+(?:какие\s+)?|"
    r"покажи(?:те)?\s+какие|"
    r"список\s+(?!за\s)|перечень\s+"
    r")",
    re.I,
)

# служебные слова (не объект)
_STOP = frozenset(
    {
        "нужно",
        "можешь",
        "можно",
        "пожалуйста",
        "сравнить",
        "сравни",
        "покажи",
        "показать",
        "посмотри",
        "посмотреть",
        "выведи",
        "сделай",
        "отчёт",
        "отчет",
        "excel",
        "xlsx",
        "выгрузи",
        "выгрузка",
        "топ",
        "рейтинг",
        "лучшие",
        "суммы",
        "сумму",
        "итоги",
        "итого",
        "данные",
        "таблиц",
        "строк",
        "актуальн",

        "выгруз",
        "файл",
        "только",
        "сколько",
        "было",
        "были",
        "между",
        "нужен",
        "нужна",
        "за",
        "по",
        "из",
        "в",
        "на",
        "и",
        "для",
        "базов",
        "базовый",
        "простой",
        "простые",
        "какие",
        "какой",
        "какая",
        "какое",
        "каких",
        "есть",
        "у",
        "нас",
        "мне",
        "наш",
        "наши",
        "наша",
        "данных",
        "документ",
        "документы",
        "документов",
        "ли",
    }
)

_GROUP_STOP = frozenset(
    {
        "контрагентам",
        "контрагентов",
        "контрагенте",
        "контрагент",
        "клиентам",
        "клиентов",
        "покупателям",
        "покупателей",
        "поставщикам",
        "поставщиков",
        "поставщик",
        "номенклатуре",
        "номенклатуры",
        "товарам",
        "складам",
        "разрезе",
    }
)

_LIST_STOP = frozenset({"список", "перечень", "перечен", "все", "весь", "имеющиеся"})


def detect_report_intent(request: str) -> dict[str, Any]:
    """
    request — фраза пользователя целиком.
    mode: summary | detail | catalog_list | document_list (через list_existing)
    Универсально для ЛЮБОГО объекта конфигурации.
    """
    t = _norm(request)
    excel = any(x in t for x in ("excel", "xlsx", "выгруз", "файл", "эксель"))

    # разрез
    group_by = ""
    if any(
        x in t
        for x in (
            "контрагент",
            "по клиент",
            "по покупат",
            "между контрагент",
            "в разрезе контрагент",
            "по контрагент",
            "поставщик",
            "по поставщик",
            "в разрезе поставщик",
        )
    ):
        group_by = "контрагент"
    elif any(
        x in t
        for x in (
            "по номенклатур",
            "по товар",
            "в разрезе номенклатур",
            "по услуг",
            "в разрезе товар",
        )
    ):
        group_by = "номенклатура"
    elif any(x in t for x in ("по склад", "в разрезе склад")):
        group_by = "склад"
    elif any(x in t for x in ("по организац", "в разрезе организац")):
        group_by = "организация"
    elif any(x in t for x in ("по менеджер", "по ответств")):
        group_by = "ответственный"
    elif any(x in t for x in ("по договор", "в разрезе договор")):
        group_by = "договор"

    # «кто из клиентов больше всех купил» — разрез по контрагенту
    if not group_by and re.search(r"кто\s+из\s+(?:клиент|покупател|контрагент|поставщик)", t):
        group_by = "контрагент"
    # «топ 10 клиентов / товаров / менеджеров» — разрез без слова «по»
    if not group_by:
        m_top = re.search(r"(?:топ|top|лучш\w*|крупнейш\w*|первы\w*)\s*-?\s*\d*\s*(клиент|покупател|контрагент|поставщик|товар|номенклатур|менеджер|склад)", t)
        if m_top:
            group_by = {
                "клиент": "контрагент", "покупател": "контрагент", "контрагент": "контрагент", "поставщик": "контрагент",
                "товар": "номенклатура", "номенклатур": "номенклатура", "менеджер": "ответственный", "склад": "склад",
            }[m_top.group(1)]

    # метрики
    metrics = "sum,count"
    value_field = ""
    if any(x in t for x in ("сколько", "количество", "число", "кол-во", "колво")):
        if any(x in t for x in ("сумм", "руб", "выручк", "на сумм", "продал", "продан", "заработ", "потрат", "купил", "оплатил")):
            metrics = "sum,count"
            value_field = "сумма"
        else:
            metrics = "count"
            if any(x in t for x in ("штук", "единиц", "услуг", "товар")):
                value_field = "количество"
    elif any(x in t for x in ("сумм", "выручк", "оборот", "итого")):
        metrics = "sum"
        value_field = "сумма"

    # «средний чек» — метрика продаж (сумма/количество документов), а не объект «Чек»
    avg_check = bool(re.search(r"средн\w*\s+чек", t))
    if avg_check:
        metrics = "avg,sum,count"
        value_field = "сумма"

    period = extract_period_from_text(request)

    # «какие есть X» / «посмотри X» / «список X» без агрегатов
    list_existing = bool(_LIST_EXISTING_RE.search(t)) or any(
        x in t
        for x in (
            "какие есть",
            "что есть",
            "есть ли",
            "посмотри",
            "посмотреть",
            "какие у нас",
            "что у нас",
            "в каких",
            "у каких",
        )
    )
    # явный список / детализация
    docs_by_partner = bool(
        re.search(r"документ\w*\s+(по|для)\s+", t)
        or "документы по" in t
        or "документ по" in t
    )
    want_list = list_existing or any(
        x in t
        for x in (
            "список",
            "перечен",
            "все документ",
            "покажи документ",
            "документы по",
            "документ по",
            "строк",
            "детализац",
            "в каких",
            "у каких",
        )
    ) or docs_by_partner

    # фильтр «документы по [контрагенту] <имя>»
    filter_name = ""
    if docs_by_partner:
        m_f = re.search(
            r"документ\w*\s+(?:по|для)\s+"
            r"(?:контрагент\w*|клиент\w*|покупател\w*|партнер\w*|партнёр\w*)?\s*"
            r"[«\"]?([a-zа-яё0-9][a-zа-яё0-9\-\.]{1,40})[»\"]?",
            t,
            re.I,
        )
        if m_f:
            cand = m_f.group(1).strip()
            if cand.lower() not in (
                "контрагенту",
                "контрагента",
                "клиенту",
                "покупателю",
                "всем",
                "всем",
            ) and not cand.startswith("контрагент"):
                filter_name = cand

    mode = "summary"
    if want_list:
        mode = "detail"
    # «по контрагентам продажи» → summary; «документы по <контрагент>» → detail
    if (
        any(
            x in t
            for x in (
                "сравни",
                "сравнен",
                "между ",
                "по контрагент",
                "по поставщик",
                "в разрезе",
            )
        )
        and not list_existing
        and not docs_by_partner
    ):
        mode = "summary"
        if not group_by and (
            "контрагент" in t or "поставщик" in t or "клиент" in t or "покупат" in t
        ):
            group_by = "контрагент"

    # справочник (явный список без движений)
    catalog_like = any(
        x in t
        for x in (
            "справочник",
            "список номенклатур",
            "список контрагент",
            "перечень номенклатур",
            "все контрагент",
            "весь справочник",
        )
    ) or (
        "номенклатур" in t
        and any(x in t for x in ("список", "перечен", "справочник", "какие"))
        and not any(
            x in t
            for x in (
                "продаж",
                "реализ",
                "поступлен",
                "закуп",
                "сумм",
                "остатк",
                "за 20",
                "год",
                "месяц",
            )
        )
    )
    if catalog_like and not any(
        x in t
        for x in (
            "продаж",
            "реализ",
            "поступлен",
            "закуп",
            "оплат",
            "сумм",
            "остатк",
            "оборот",
        )
    ):
        mode = "catalog_list"

    # класс объекта (эвристика, не имена Документ.X)
    prefer_kind = ""
    if any(
        x in t
        for x in (
            "остатк",
            "осталось",
            "остались",
            "оборот",
            "срез послед",
            "регистр",
            "на склад",
            "в наличии",
        )
    ):
        prefer_kind = "register"
    elif mode == "catalog_list" or (
        any(x in t for x in ("справочник", "номенклатур", "контрагент"))
        and not any(
            x in t
            for x in (
                "продаж",
                "реализ",
                "поступлен",
                "закуп",
                "оплат",
                "документ",
                "накладн",
                "заказ",
                "счет",
                "счёт",
            )
        )
    ):
        prefer_kind = "catalog"
    elif any(
        x in t
        for x in (
            "продаж",
            "реализ",
            "поступлен",
            "закуп",
            "оплат",
            "платеж",
            "заказ",
            "счет",
            "счёт",
            "возврат",
            "отгруз",
            "накладн",
            "требован",
            "перемещ",
            "документ",
            "акт",
        )
    ) or list_existing:
        prefer_kind = "document"

    # поисковая фраза = смысл объекта (без служебных слов)
    search = t
    if period:
        search = search.replace(_norm(period), " ")
    parts = re.findall(r"[a-zа-яё0-9\-\.]+", search)
    keep = [
        w
        for w in parts
        if w not in _STOP
        and w not in _GROUP_STOP
        and w not in _LIST_STOP
        and not re.fullmatch(r"\d{4}", w)
        and len(w) >= 2
    ]
    search = " ".join(keep).strip()

    if avg_check:
        search = re.sub(r"средн\w*\s+чек\w*", " ", search).strip() + " продажа реализация"
    # усилить поиск объекта: «продажи/реализации» не выкидывать из-за «топ/суммы»
    # «выручка / продали / продано» — тот же смысл, что «продажи»
    if any(x in t for x in ("продаж", "реализ", "отгруз", "выручк", "продал", "продан", "купил", "покупал", "покупк")) and not any(
        x in search for x in ("продаж", "реализ", "отгруз")
    ):
        from tools.business_stems import SALES_SEARCH

        search = f"{search} {SALES_SEARCH}".strip()
    if any(x in t for x in ("поступлен", "закуп")) and "поступлен" not in search and "закуп" not in search:
        from tools.business_stems import PURCHASE_SEARCH

        search = f"{search} {PURCHASE_SEARCH}".strip()

    if not search:
        # fallback по доменам (универсальные стемы, не fixed Документ.X)
        from tools.business_stems import (
            CASH_SEARCH,
            PURCHASE_SEARCH,
            SALES_SEARCH,
        )

        if any(x in t for x in ("продаж", "реализ")):
            search = SALES_SEARCH
        elif any(x in t for x in ("поступлен", "закуп", "приобрет")):
            search = PURCHASE_SEARCH
        elif any(x in t for x in ("оплат", "платеж")):
            search = CASH_SEARCH
        elif "номенклатур" in t:
            search = "номенклатура"
        elif "контрагент" in t and not docs_by_partner:
            search = "контрагенты"
        else:
            search = t

    # «документы по [контрагенту] X» — объект = документ (не имя контрагента)
    if docs_by_partner:
        if filter_name and filter_name.lower() in (search or "").lower():
            search = re.sub(re.escape(filter_name), " ", search, flags=re.I)
            search = re.sub(r"\s+", " ", search).strip()
        # убрать «контрагенту/клиенту» из search
        search = re.sub(
            r"\b(контрагент\w*|клиент\w*|покупател\w*|партн[её]р\w*)\b",
            " ",
            search or "",
            flags=re.I,
        )
        search = re.sub(r"\s+", " ", search).strip()
        if not search or len(search) < 3:
            # не подставлять SALES_SEARCH — иначе любая CF уезжает в «реализацию»
            search = (t or "").strip() or "документ"
        prefer_kind = "document"
        mode = "detail"
        metrics = "count"
        group_by = ""

    if mode == "catalog_list":
        if "номенклатур" in t:
            search = "номенклатура"
        elif "контрагент" in t:
            search = "контрагенты"

    # список без периода → не суммы
    if (list_existing or (mode == "detail" and not period) or docs_by_partner) and not any(
        x in t for x in ("сумм", "выручк", "итого", "оборот")
    ):
        metrics = metrics if "сколько" in t else "count"
        if mode == "summary":
            mode = "detail"
        # документы по X за 2015 — list с периодом (не сравнивать периоды)
        list_existing = True

    return {
        "mode": mode,
        "search_query": search,
        "period": period,
        "group_by": group_by,
        "metrics": metrics,
        "value_field": value_field,
        "to_excel": excel,
        "prefer_kind": prefer_kind,
        "list_existing": bool(list_existing),
        "filter_text": filter_name or "",
        "raw": request,
    }
