# -*- coding: utf-8 -*-
"""
Универсальный построитель отчётов 1С (любая CF).

Фраза → ReportSpec → resolve object/fields → query → rows → (excel).

Не hardcode имён Документ.X — только metadata + structure + роли полей.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable


def _norm(s: str) -> str:
    return (s or "").lower().replace("ё", "е").strip()


@dataclass
class ReportSpec:
    """План отчёта (человекочитаемый + для tools)."""

    request: str = ""
    # объект
    search_query: str = ""
    full_name: str = ""
    object_hint: str = ""  # document | catalog | register | any
    # период
    period: str = ""
    # разрезы (до 2)
    group_by: list[str] = field(default_factory=list)
    # метрики
    metrics: list[str] = field(default_factory=list)  # sum, count, avg
    value_field: str = ""  # роль/имя суммы
    # фильтры (текст → later)
    filter_text: str = ""
    only_posted: bool = True
    # вывод
    limit: int = 50
    offset: int = 0
    sort: str = "sum_desc"  # sum_desc | name | date
    to_excel: bool = False
    charts: bool = False
    filename: str = ""
    title: str = ""
    # маршрут
    route: str = "aggregate"  # aggregate | compare | list | linked | analyst
    analyst_mode: str = ""
    top_n: int = 0
    # «как» строить (из речи пользователя)
    time_grain: str = ""  # day | week | month | quarter | year
    filters: list[dict[str, Any]] = field(default_factory=list)
    soft_group_by: list[str] = field(default_factory=list)  # разрезы «по X» без словаря

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- parse NL → ReportSpec ---

_DIM_MAP: list[tuple[tuple[str, ...], str]] = [
    (
        (
            "контрагент",
            "по клиент",
            "по покупат",
            "поставщик",
            "по поставщик",
            "партнер",
            "партнёр",
            "заказчик",
        ),
        "контрагент",
    ),
    (
        (
            "номенклатур",
            "по товар",
            "по услуг",
            "sku",
            "артикул",
            "издели",
            "продукц",
            "материал",
            "полуфабрик",
        ),
        "номенклатура",
    ),
    (("по склад", "в разрезе склад", "на склад", "склад", "складам"), "склад"),
    (("по ячейк", "ячейк склад"), "ячейка"),
    (("по организац", "в разрезе организац"), "организация"),
    (("по менеджер", "по ответств", "по сотрудник"), "ответственный"),
    (("по договор",), "договор"),
    (("по заказ", "в разрезе заказ", "заказам"), "заказ"),
    (("по подраздел", "по цех", "по участк"), "подразделение"),
    (("по производств", "в производств"), "производство"),
    (("по серия", "по партий"), "серия"),
    (("по характеристи",), "характеристика"),
    (("по регион", "по город"), "регион"),
    (("по месяц", "помесячн"), "месяц"),
    (("по день", "подневн"), "день"),
]


# --- «как» строить: шаг времени, сортировка, топ, фильтры, свободные разрезы ---

_GRAIN_PATTERNS: list[tuple[str, str]] = [
    (r"\bпо\s+дням\b|\bподневн\w*|\bежедневн\w*|\bпо\s+датам\b|\bв\s+разрезе\s+дн", "day"),
    (r"\bпо\s+недел\w*|\bпонедельно\b|\bеженедельн\w*", "week"),
    (r"\bпо\s+месяц\w*|\bпомесячн\w*|\bежемесячн\w*|\bв\s+разрезе\s+месяц", "month"),
    (r"\bпо\s+квартал\w*|\bпоквартальн\w*|\bежеквартальн\w*", "quarter"),
    (r"\bпо\s+годам\b|\bпогодов\w*|\bежегодн\w*|\bв\s+разрезе\s+год", "year"),
]

# слова после «по …», которые НЕ разрез (время/сортировка/служебные)
_NOT_DIM_STEMS = (
    "месяц", "дн", "день", "дат", "недел", "квартал", "год", "период",
    "возраст", "убыв", "алфавит", "наименован", "сумм", "колич", "шаг", "мая", "май",
    "умолчан", "порядк", "всем", "все", "excel", "эксел", "запрос", "итог",
    "убыван", "размер", "величин", "очеред", "сегодн", "вчера", "текущ",
    "прошл", "последн", "январ", "феврал", "март", "апрел", "июн",
    "июл", "август", "сентябр", "октябр", "ноябр", "декабр",
)

_OP_WORDS: list[tuple[str, str]] = [
    (r"не\s+равн\w*\b|!=|<>|≠", "ne"),
    (r">=|не\s+меньше\b|не\s+менее\b|от\b", ">="),
    (r"<=|не\s+больше\b|не\s+более\b|до\b", "<="),
    (r">|больше\b|более\b|свыше\b|выше\b", ">"),
    (r"<|меньше\b|менее\b|ниже\b", "<"),
    (r"=|равн\w*\b", "eq"),
    (r"содерж\w*\b|включа\w*\b|похож\w*\b", "like"),
]


def _num(s: str) -> float | None:
    m = re.match(r"\s*(-?[\d\s]+(?:[.,]\d+)?)\s*(тыс\w*|млн\w*|k|к)?", s or "", re.I)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(" ", "").replace(",", "."))
    except ValueError:
        return None
    mult = (m.group(2) or "").lower()
    if mult.startswith("тыс") or mult in ("k", "к"):
        v *= 1000
    elif mult.startswith("млн"):
        v *= 1_000_000
    return v


def parse_filters_text(raw: str) -> tuple[list[dict[str, Any]], str]:
    """
    Фильтры из речи. Возвращает (filters, текст без распознанных фрагментов).
    filter: {field: слова поля или "", op: like|eq|ne|>|<|>=|<=, value, neg}
    Значения — как написал пользователь (регистр сохраняется); поле резолвится по structure.
    """
    text = raw or ""
    out: list[dict[str, Any]] = []
    spans: list[tuple[int, int]] = []

    def _add(m: re.Match, **kw: Any) -> None:
        out.append(kw)
        spans.append(m.span())

    # 1) «где/у которых/если ПОЛЕ ОП ЗНАЧЕНИЕ»
    op_alt = "|".join(p for p, _ in _OP_WORDS)
    for m in re.finditer(
        r"(?:\bгде\b|\bу\s+которых\b|\bесли\b|\bс\s+условием\b|\bпри\s+условии\b)\s+"
        r"(?P<field>[a-zа-яё][\w ]{1,40}?)\s*(?P<op>" + op_alt + r")\s*"
        r"[«\"]?(?P<val>[^«»\",;\n]{1,60}?)[»\"]?(?=\s*(?:,|;|\bи\b|\bза\b|$))",
        text,
        re.I,
    ):
        op = next(o for p, o in _OP_WORDS if re.fullmatch(p, m.group("op").strip(), re.I))
        val = m.group("val").strip()
        nv = _num(val) if re.fullmatch(r"-?[\d\s.,]+\s*(?:тыс\w*|млн\w*)?", val, re.I) else None
        if nv is not None:
            _add(m, field=m.group("field").strip(), op=op, value=nv, neg=False)
        else:  # текст/ссылка: =, ≠ → ПОДОБНО
            _add(m, field=m.group("field").strip(), op="like", value=val, neg=op == "ne")

    # 2) «сумма больше 1000» / «количество от 10» (без «где»)
    for m in re.finditer(
        r"(?P<field>сумм\w*|количеств\w*|кол-?во|выручк\w*)\s+"
        r"(?P<op>больше|более|свыше|меньше|менее|не\s+меньше|не\s+менее|не\s+больше|не\s+более|от|до|>=|<=|>|<)\s*"
        r"(?P<val>-?\d[\d\s.,]*(?:\s*(?:тыс\w*|млн\w*))?)",
        text,
        re.I,
    ):
        if any(a <= m.start() < b for a, b in spans):
            continue
        op = next(o for p, o in _OP_WORDS if re.fullmatch(p, m.group("op").strip(), re.I))
        v = _num(m.group("val"))
        if v is not None:
            _add(m, field=m.group("field"), op=op, value=v, neg=False)

    # 3) кавычки: «[кроме] [по] поле «Значение»»
    for m in re.finditer(
        r"(?P<neg>\bкроме\b|\bбез\b|\bисключая\b|\bне\s+считая\b)?\s*"
        r"(?:\bтолько\b\s*)?(?:\b(?:по|для|у|с)\b\s+)?"
        r"(?P<field>[a-zа-яё]{3,}(?:\s+[a-zа-яё]{3,})?)?\s*"
        r"[«\"„“](?P<val>[^»\"“”]{1,60})[»\"“”]",
        text,
        re.I,
    ):
        if any(a <= m.start() < b for a, b in spans):
            continue
        fld = (m.group("field") or "").strip()
        neg = bool(m.group("neg"))
        # «кроме контрагента «X»» — отрицание попало в поле
        m_neg = re.match(r"(кроме|без|исключая|только)\s+(?:по\s+|для\s+)?", fld, re.I)
        if m_neg:
            neg = neg or m_neg.group(1).lower() != "только"
            fld = fld[m_neg.end():].strip()
        if fld.lower() in ("только", "кроме", "без", "по", "для", "исключая"):
            neg = neg or fld.lower() in ("кроме", "без", "исключая")
            fld = ""
        _add(m, field=fld, op="like", value=m.group("val").strip(), neg=neg)

    # 4) «[только|кроме] по складу Основной» — поле в ед.ч. + значение с заглавной
    for m in re.finditer(
        r"(?P<neg>\bкроме\b|\bисключая\b)?\s*(?:\bтолько\b\s+)?\b(?:по|для|у)\s+"
        r"(?P<field>[а-яё]{4,}(?:у|ю|е|а|я))\s+"
        r"(?P<val>[A-ZА-ЯЁ0-9][^\s,;«»\"]*(?:\s+[A-ZА-ЯЁ0-9][^\s,;«»\"]*){0,3})",
        text,
    ):
        if any(a <= m.start() < b for a, b in spans):
            continue
        _add(m, field=m.group("field"), op="like", value=m.group("val").strip(), neg=bool(m.group("neg")))

    rest = text
    for a, b in sorted(spans, reverse=True):
        rest = rest[:a] + " " + rest[b:]
    return out, re.sub(r"\s+", " ", rest).strip()


def parse_report_shape(raw: str) -> dict[str, Any]:
    """
    Как пользователь хочет видеть отчёт (универсально, без имён CF):
    time_grain, sort (desc|asc|name|period), top_n, avg, filters, свободные разрезы.
    """
    pl = _norm(raw)
    shape: dict[str, Any] = {
        "time_grain": "",
        "sort": "",
        "top_n": 0,
        "avg": False,
        "filters": [],
        "free_dims": [],
    }
    for pat, g in _GRAIN_PATTERNS:
        if re.search(pat, pl):
            shape["time_grain"] = g
            break
    if not shape["time_grain"] and re.search(r"\bдинамик\w*|\bтренд\w*|\bпо\s+времени\b", pl):
        shape["time_grain"] = "month"

    # сортировка / топ / антитоп
    if re.search(
        r"по\s+возрастани|от\s+меньш|антитоп|худш|наименьш|меньше\s+всего|"
        r"самы\w*\s+мал|отстающ|слабы\w*|аутсайдер",
        pl,
    ):
        shape["sort"] = "asc"
    elif re.search(r"по\s+алфавит|по\s+наименовани|по\s+имени|по\s+названи", pl):
        shape["sort"] = "name"
    elif re.search(r"по\s+убывани|от\s+больш|крупнейш|наибольш|больше\s+всего|лучш|топ", pl):
        shape["sort"] = "desc"
    elif re.search(r"хронологи|по\s+порядку\s+дат|по\s+дате\b", pl):
        shape["sort"] = "period"
    m = (
        re.search(r"\b(?:топ|top|антитоп)[-\s]?(\d{1,3})\b", pl)
        or re.search(r"\b(?:первы[ех]|последни[ех])\s+(\d{1,3})\b", pl)
        or re.search(
            r"\b(\d{1,3})\s+(?:сам\w+\s+)?(?:лучш|худш|крупн|больш|мелк|наибол|наимен|перв)\w*",
            pl,
        )
    )
    if m:
        shape["top_n"] = int(m.group(1))
    if re.search(r"\bсредн\w*|\bсредний\s+чек", pl):
        shape["avg"] = True

    filters, rest = parse_filters_text(raw or "")
    shape["filters"] = filters

    # свободные разрезы: «по X», «в разрезе X и Y», «с разбивкой по X, Y»
    rest_l = _norm(rest)
    rest_l = re.sub(r"\b(?:отч[её]т\w*|данн\w*|выгрузк\w*|справк\w*|информаци\w*|аналитик\w*)\s+по\s+\w+", " ", rest_l)
    dims: list[str] = []
    word = r"[a-zа-яё]{3,}(?:\s+(?!за\b|по\b|и\b|в\b|с\b)[a-zа-яё]{4,})?"
    for m in re.finditer(
        r"(?:\bв\s+разрезе\b|\bс\s+разбивкой\s+по\b|\bразбивк\w*\s+по\b|"
        r"\bсгруппир\w*\s+по\b|\bгруппировк\w*\s+по\b|\bпо\b)\s+"
        r"(" + word + r"(?:\s*(?:,|\bи\b)\s*" + word + r")*)",
        rest_l,
    ):
        for w in re.split(r"\s*(?:,|\bи\b)\s*", m.group(1)):
            w = w.strip()
            if not w or any(w.startswith(s) for s in _NOT_DIM_STEMS):
                continue
            if w not in dims:
                dims.append(w)
    # «топ-10 товаров», «5 худших клиентов» — объект рейтинга = разрез
    m = re.search(
        r"(?:\b(?:топ|top|антитоп)[-\s]?\d{0,3}|\b\d{1,3}\s+(?:сам\w+\s+)?"
        r"(?:лучш|худш|крупн|больш|мелк|наибол|наимен)\w*)\s+([a-zа-яё]{4,})",
        rest_l,
    )
    if m and m.group(1) not in dims and not any(m.group(1).startswith(s) for s in _NOT_DIM_STEMS):
        dims.insert(0, m.group(1))
    shape["free_dims"] = dims[:3]
    return shape


def parse_filters_param(filters: Any) -> list[dict[str, Any]]:
    """
    Параметр filters tool'а: list[dict] | «Склад ~ Основной; Сумма > 1000; не Контрагент = X»
    | свободная речь («только по складу Основной»).
    """
    if not filters:
        return []
    if not isinstance(filters, str):
        return [f for f in filters if isinstance(f, dict) and f.get("value") not in (None, "")]
    out: list[dict[str, Any]] = []
    sym = {"~": "like", "=": "eq", "!=": "ne", "<>": "ne", "≠": "ne", "≥": ">=", "≤": "<="}
    for chunk in re.split(r"[;\n]", filters):
        m = re.match(
            r"\s*(?P<neg>не\s+|!)?(?P<f>[^~=<>!≠≥≤]+?)\s*"
            r"(?P<op>!=|<>|≠|>=|≥|<=|≤|~|=|>|<)\s*(?P<v>.+?)\s*$",
            chunk,
            re.I,
        )
        if not m:
            continue
        op = sym.get(m.group("op"), m.group("op"))
        val: Any = m.group("v").strip().strip("«»\"")
        nv = _num(val) if re.fullmatch(r"-?[\d\s.,]+\s*(?:тыс\w*|млн\w*)?", val, re.I) else None
        if nv is not None:
            val = nv
        elif op == "eq":
            op = "like"  # текст/ссылка: = → ПОДОБНО
        out.append({"field": m.group("f").strip(), "op": op, "value": val, "neg": bool(m.group("neg"))})
    if not out:  # не формат «поле оп значение» — разбор как речь
        out, _ = parse_filters_text(filters)
    return out


def filters_to_text(filters: list[dict[str, Any]]) -> str:
    """Для плана/LLM: «Склад ~ Основной; Сумма > 1000»."""
    sym = {"like": "~", "eq": "=", "ne": "≠", ">": ">", "<": "<", ">=": "≥", "<=": "≤"}
    parts = []
    for f in filters or []:
        neg = "НЕ " if f.get("neg") and f.get("op") != "ne" else ""
        parts.append(
            f"{neg}{f.get('field') or '*'} {sym.get(str(f.get('op')), '~')} {f.get('value')}"
        )
    return "; ".join(parts)


def parse_report_request(
    request: str,
    *,
    full_name: str = "",
    period: str = "",
    group_by: str = "",
    metrics: str = "",
    value_field: str = "",
    limit: int = 50,
    offset: int = 0,
    to_excel: bool = False,
    filename: str = "",
    title: str = "",
) -> ReportSpec:
    """Разбор фразы + явных параметров → ReportSpec."""
    from tools.report_intent import extract_period_from_text
    from tools.analyst_core import (
        detect_analyst_mode,
        wants_excel_charts,
        wants_excel_file,
    )
    from tools.report_format import wants_period_compare
    from tools.linked_query import is_linked_or_debt_data_question

    raw = (request or "").strip()
    pl = _norm(raw)
    spec = ReportSpec(
        request=raw,
        full_name=(full_name or "").strip(),
        period=(period or extract_period_from_text(raw) or "").strip(),
        limit=max(1, min(int(limit or 50), 500)),
        offset=max(0, int(offset or 0)),
        to_excel=bool(to_excel) or wants_excel_file(raw),
        charts=wants_excel_charts(raw),
        filename=filename or "",
        title=title or raw[:80],
        only_posted="непроведен" not in pl and "включая непроведен" not in pl,
    )

    # фильтры («только по складу X», «где сумма > N») не должны влиять на выбор объекта/разрезов
    _flt0, raw_nf = parse_filters_text(raw)
    pl_nf = _norm(raw_nf)

    # search_query: убрать служебные слова и «как строить» для metadata search
    sq = raw_nf
    for noise in (
        r"\b(построй|построить|сделай|сделать|нужен|нужна|отчет|отчёт|"
        r"выгруз\w*|excel|xlsx|эксель|график\w*|диаграмм\w*|красив\w*|"
        r"пожалуйста|покажи|выведи|сравни|сравнение)\b",
        r"\b(?:топ|top|антитоп)[-\s]?\d{0,3}\b|\b\d{1,3}\s+(?:сам\w+\s+)?"
        r"(?:лучш|худш|крупн|больш|мелк|наибол|наимен)\w*|\b(?:лучш|худш|крупнейш)\w*",
        r"\bпо\s+(?:возрастани|убывани|алфавит|наименовани|имени|дате)\w*|"
        r"\bпо\s+(?:дням|неделям|месяцам|кварталам|годам)\b|"
        r"\b(?:помесячн|поквартальн|подневн|ежемесячн|ежедневн|еженедельн|динамик)\w*|"
        r"\bпонедельно\b|\bсредн\w*",
    ):
        sq = re.sub(noise, " ", sq, flags=re.I)
    sq = re.sub(r"\s+", " ", sq).strip(" ,.;")
    spec.search_query = sq or raw

    # dimensions
    dims: list[str] = []
    if group_by:
        for part in re.split(r"[,;+/]| и ", group_by):
            p = part.strip()
            if p:
                dims.append(p)
    for keys, role in _DIM_MAP:
        if any(k in pl_nf for k in keys) and role not in dims:
            dims.append(role)
    # «по X и Y»
    m = re.search(
        r"(?:по|в разрезе)\s+([a-zа-яё]+)\s+и\s+([a-zа-яё]+)",
        pl_nf,
    )
    if m:
        for tok in (m.group(1), m.group(2)):
            for keys, role in _DIM_MAP:
                if any(k.startswith(tok[:4]) or tok in k for k in keys):
                    if role not in dims:
                        dims.append(role)
    # «по месяцам/дням» — это шаг времени (time_grain), не поле группировки
    spec.group_by = [d for d in dims if d not in ("месяц", "день")][:3]

    # metrics — сумма / количество / остаток / любые показатели
    mets: list[str] = []
    if metrics:
        for part in re.split(r"[,;\s]+", metrics.lower()):
            if part in ("sum", "сумма", "сумм", "стоим"):
                mets.append("sum")
            elif part in (
                "count",
                "кол",
                "количество",
                "число",
                "qty",
                "остаток",
                "остат",
            ):
                mets.append("count")
            elif part in ("avg", "средн"):
                mets.append("avg")
    if not mets:
        if any(
            x in pl
            for x in (
                "сколько",
                "количество",
                "число",
                "кол-во",
                "остатк",
                "на склад",
                "в заказ",
                "в производств",
                "штук",
            )
        ):
            if any(x in pl for x in ("сумм", "выручк", "руб", "стоим")):
                mets = ["sum", "count"]
            else:
                mets = ["count"]
        elif any(
            x in pl for x in ("сумм", "выручк", "оборот", "итого", "продаж", "стоим")
        ):
            mets = ["sum", "count"]
        else:
            mets = ["sum", "count"]
    shape = parse_report_shape(raw)
    if shape["avg"] and "avg" not in mets:
        mets.append("avg")
    spec.metrics = mets
    spec.value_field = (value_field or "").strip()
    if not spec.value_field:
        if any(x in pl for x in ("остатк", "свободн", "врезерв")):
            spec.value_field = "остаток"
        elif any(x in pl for x in ("колич", "штук", "qty")):
            spec.value_field = "количество"
        elif any(x in pl for x in ("сумм", "выручк", "стоим")):
            spec.value_field = "сумма"
        elif any(x in pl for x in ("цен", "price")):
            spec.value_field = "цена"

    # top N / сортировка / шаг времени / фильтры / свободные разрезы
    if shape["top_n"]:
        spec.top_n = shape["top_n"]
        spec.limit = min(spec.limit, max(spec.top_n, 10))
    elif any(x in pl for x in ("топ", "рейтинг", "лучш", "худш")):
        spec.top_n = spec.top_n or 10
    if shape["sort"]:
        spec.sort = {"desc": "sum_desc", "asc": "sum_asc"}.get(shape["sort"], shape["sort"])
    spec.time_grain = shape["time_grain"]
    spec.filters = shape["filters"]
    for w in shape["free_dims"]:
        # слово из _DIM_MAP («клиентов» → контрагент) — роль; иначе мягкий разрез по structure
        role = next(
            (r for keys, r in _DIM_MAP if any(k.split()[-1] in w for k in keys)), ""
        )
        if role in ("месяц", "день"):
            continue
        if role:
            if role not in spec.group_by and len(spec.group_by) < 3:
                spec.group_by.append(role)
        elif w not in spec.soft_group_by:
            spec.soft_group_by.append(w)

    # object hint — любые контуры учёта
    if any(x in pl for x in ("справочник", "список клиент", "список номенклатур")):
        spec.object_hint = "catalog"
    elif any(
        x in pl
        for x in (
            "регистр",
            "остатк",
            "оборот",
            "на склад",
            "в производств",
            "выпуск",
        )
    ):
        spec.object_hint = "register"
    elif any(
        x in pl
        for x in (
            "документ",
            "реализ",
            "поступлен",
            "заказ",
            "счет",
            "счёт",
            "требован",
            "перемещен",
            "выпуск",
        )
    ):
        spec.object_hint = "document"
    else:
        spec.object_hint = "any"

    # route: analyst → attr (реквизиты) → linked (остатки/долги) → compare → list → aggregate
    amode = detect_analyst_mode(raw)
    try:
        from tools.attr_query import is_attr_filter_question
    except Exception:
        is_attr_filter_question = lambda _p: False  # noqa: E731
    try:
        from tools.object_movements import (
            is_movements_or_posting_question,
            is_posting_matrix_question,
        )
        from tools.linked_query import is_stock_balances_question
    except Exception:
        is_movements_or_posting_question = is_posting_matrix_question = (  # noqa: E731
            lambda _p: False
        )
        is_stock_balances_question = lambda _p: False  # noqa: E731
    if is_movements_or_posting_question(raw):
        # «по каким регистрам проводится X» / «где какой документ по каким регистрам»
        spec.route = "movements"
        spec.analyst_mode = "matrix" if is_posting_matrix_question(raw) else "object"
    elif is_stock_balances_question(raw) and not shape["time_grain"]:
        # «просто остатки» — срез на сейчас, без аналитики
        spec.route = "linked"
    elif amode and amode not in ("dashboard",):
        spec.route = "analyst"
        spec.analyst_mode = amode
    elif is_attr_filter_question(raw):
        spec.route = "attr"
        spec.object_hint = spec.object_hint or "document"
    elif any(
        x in pl
        for x in (
            "остатк товар",
            "остатк на склад",
            "количество на склад",
            "товары на склад",
            "сколько товар",
            "на складах",
            "осталось",
            "остались",
            "в наличии",
            "в резерв",
            "к отгруз",
            "в производств",
            "незавершен",
            "задолжен",
            "взаиморасч",
        )
    ) or is_linked_or_debt_data_question(raw):
        spec.route = "linked"
    elif wants_period_compare(raw) and (
        len(re.findall(r"\b((?:19|20)\d{2})\b", pl)) >= 2
        or any(x in pl for x in ("сравни", "vs", "динамика", "к прошлом"))
    ):
        spec.route = "compare"
        # group_by только если пользователь сказал — иначе compare/structure
    elif any(
        x in pl
        for x in (
            "список",
            "перечень",
            "какие есть",
            "покажи все",
            "выведи все",
        )
    ) and not any(
        x in pl for x in ("сумм", "продаж", "сколько", "топ", "остатк", "колич")
    ):
        spec.route = "list"
    elif not spec.route or spec.route == "aggregate":
        if spec.route != "linked" and spec.route != "analyst":
            spec.route = "aggregate"
        # НЕ подставляем «контрагент» по умолчанию — универсально из фразы/structure

    # filter fragment: «только X» already handled by dialog; «где сумма >»
    m = re.search(r"только\s+[«\"]?([^«»\"\n]{2,40})", raw, re.I)
    if m and not spec.filters:  # структурные фильтры уже разобраны → не дублировать текстом
        spec.filter_text = m.group(1).strip()

    return spec


def explain_spec(spec: ReportSpec) -> list[str]:
    """Человекочитаемый план для пользователя/LLM."""
    lines = ["План отчёта:"]
    lines.append(f"  • маршрут: {spec.route}" + (f"/{spec.analyst_mode}" if spec.analyst_mode else ""))
    if spec.full_name:
        lines.append(f"  • объект: {spec.full_name}")
    else:
        lines.append(f"  • поиск объекта: «{spec.search_query}»")
    if spec.period:
        lines.append(f"  • период: {spec.period}")
    if spec.group_by or spec.soft_group_by:
        lines.append(f"  • разрезы: {', '.join(spec.group_by + spec.soft_group_by)}")
    if spec.time_grain:
        grain_ru = {"day": "дни", "week": "недели", "month": "месяцы", "quarter": "кварталы", "year": "годы"}
        lines.append(f"  • шаг времени: {grain_ru.get(spec.time_grain, spec.time_grain)}")
    if spec.metrics:
        lines.append(f"  • метрики: {', '.join(spec.metrics)}")
    if spec.top_n:
        lines.append(f"  • топ: {spec.top_n}" + (" (с конца)" if spec.sort == "sum_asc" else ""))
    if spec.sort and spec.sort != "sum_desc":
        lines.append(f"  • сортировка: {spec.sort}")
    if spec.filters:
        lines.append(f"  • фильтры: {filters_to_text(spec.filters)}")
    if spec.filter_text:
        lines.append(f"  • фильтр: {spec.filter_text}")
    lines.append(f"  • строк: limit={spec.limit}, offset={spec.offset}")
    if spec.to_excel:
        lines.append("  • Excel: да" + (" + графики" if spec.charts else ""))
    return lines


def run_report_builder(
    request: str = "",
    *,
    full_name: str = "",
    period: str = "",
    group_by: str = "",
    metrics: str = "",
    value_field: str = "",
    limit: int = 50,
    offset: int = 0,
    to_excel: bool = False,
    filename: str = "",
    title: str = "",
    # callbacks
    compare_fn: Callable[..., dict[str, Any]],
    build_report_fn: Callable[..., dict[str, Any]] | None = None,
    linked_fn: Callable[..., dict[str, Any]] | None = None,
    analyst_fn: Callable[..., dict[str, Any]] | None = None,
    list_fn: Callable[..., dict[str, Any]] | None = None,
    attr_fn: Callable[..., dict[str, Any]] | None = None,
    movements_fn: Callable[..., dict[str, Any]] | None = None,
    execute_fn: Callable[..., dict[str, Any]] | None = None,
    structure_fn: Callable[..., dict[str, Any]] | None = None,
    find_metadata_fn: Callable[..., dict[str, Any]] | None = None,
    dry_run: bool = False,
    filters: Any = None,
    sort: str = "",
    time_grain: str = "",
) -> dict[str, Any]:
    """
    Главная точка: построить и (если не dry_run) выполнить отчёт.
    filters/sort/time_grain — явные параметры важнее разбора фразы.
    """
    spec = parse_report_request(
        request,
        full_name=full_name,
        period=period,
        group_by=group_by,
        metrics=metrics,
        value_field=value_field,
        limit=limit,
        offset=offset,
        to_excel=to_excel,
        filename=filename,
        title=title,
    )
    if filters:
        spec.filters = parse_filters_param(filters)
    if sort:
        spec.sort = {"desc": "sum_desc", "asc": "sum_asc"}.get(sort, sort)
    if time_grain:
        spec.time_grain = time_grain
    plan = explain_spec(spec)
    base_out: dict[str, Any] = {
        "ok": True,
        "builder": "universal_report_builder",
        "spec": spec.to_dict(),
        "plan": plan,
        "prefer": (
            "Универсальный построитель: 1) plan 2) manager_summary/analyst_summary "
            "3) rows 4) path excel если есть. Не выдумывай full_name."
        ),
    }

    if dry_run:
        base_out["dry_run"] = True
        base_out["note"] = "Только план, без выполнения. dry_run=false для данных."
        return base_out

    # --- dispatch by route ---
    try:
        if spec.route == "movements":
            if spec.analyst_mode == "matrix":
                from tools.object_movements import posting_matrix

                r = posting_matrix(spec.request or request)
            elif movements_fn:
                r = movements_fn(request=spec.request or request, full_name=spec.full_name)
            else:
                r = None
            if isinstance(r, dict):
                return _merge_result(base_out, r, spec)

        if spec.route == "analyst" and analyst_fn:
            r = analyst_fn(
                request=spec.request or request,
                mode=spec.analyst_mode,
                full_name=spec.full_name,
                period=spec.period,
                group_by=spec.group_by[0] if spec.group_by else "",
                to_excel=spec.to_excel,
                filename=spec.filename,
                limit=spec.limit,
            )
            return _merge_result(base_out, r, spec)

        if spec.route == "linked" and linked_fn:
            r = linked_fn(
                request=spec.request or request,
                execute=True,
                limit=spec.limit,
                offset=spec.offset,
            )
            return _merge_result(base_out, r, spec)

        if spec.route == "attr" and (attr_fn or build_report_fn):
            fn = attr_fn or build_report_fn
            r = fn(
                request=spec.request or request,
                full_name=spec.full_name,
                limit=spec.limit,
                offset=spec.offset,
            )
            return _merge_result(base_out, r, spec)

        if spec.route == "compare" and compare_fn:
            from tools.period_parse import extract_period_spec

            # multi group: primary only; пустой group_by → compare без навязанного «контрагент»
            # 2D: «контрагент, номенклатура» → одна строка group_by
            gb_arg = ", ".join(spec.group_by) if spec.group_by else ""
            met = ",".join(spec.metrics) if spec.metrics else "sum,count"
            per_cmp = extract_period_spec(spec.period or "", spec.request or "")
            r = compare_fn(
                search_query=spec.search_query or spec.request,
                full_name=spec.full_name,
                period=per_cmp,
                group_by=gb_arg,
                metrics=met,
                value_field=spec.value_field,
                only_posted=spec.only_posted,
                to_excel=spec.to_excel,
                filename=spec.filename,
                title=spec.title,
                **_shape_kwargs(spec),
            )
            if isinstance(r, dict) and len(spec.group_by) > 1:
                r["group_by_dims"] = list(spec.group_by)
                r["note_multi_dim"] = (
                    f"Разрезы 2D: {', '.join(spec.group_by)}."
                )
            return _merge_result(base_out, r, spec)

        if spec.route == "list" and list_fn:
            r = list_fn(request=spec.request, full_name=spec.full_name, limit=spec.limit)
            return _merge_result(base_out, r, spec)

        # aggregate — через compare_periods (умеет period + group + metrics) или build_report
        if compare_fn and (
            spec.period
            or spec.group_by
            or spec.search_query
            or spec.request
        ):
            from tools.period_parse import extract_period_spec

            gb_arg = ", ".join(spec.group_by) if spec.group_by else ""
            met = ",".join(spec.metrics) if spec.metrics else "sum,count"
            per = spec.period
            if _norm(per) in ("без периода", "нет", "-"):
                per = ""
            # НЕ period=spec.request целиком («продажи по…» → unparsed)
            per = extract_period_spec(per or "", spec.request or "")
            r = compare_fn(
                search_query=spec.search_query or spec.request,
                full_name=spec.full_name,
                period=per,
                group_by=gb_arg,
                metrics=met,
                value_field=spec.value_field,
                only_posted=spec.only_posted,
                to_excel=spec.to_excel or spec.charts,
                filename=spec.filename,
                title=spec.title,
                **_shape_kwargs(spec),
            )
            if isinstance(r, dict) and len(spec.group_by) > 1:
                r["group_by_dims"] = list(spec.group_by)
            # post filter
            if isinstance(r, dict) and spec.filter_text and r.get("rows"):
                r = _apply_text_filter(r, spec.filter_text)
            if (
                isinstance(r, dict)
                and spec.top_n
                and isinstance(r.get("rows"), list)
                and not r.get("top_n_applied")
            ):
                r["rows"] = r["rows"][: spec.top_n]
                r["count"] = len(r["rows"])
                r["top_n_applied"] = spec.top_n
            return _merge_result(base_out, r, spec)

        if build_report_fn:
            r = build_report_fn(
                request=spec.request,
                search_query=spec.search_query,
                full_name=spec.full_name,
                period=spec.period,
                group_by=", ".join(spec.group_by) if spec.group_by else "",
                metrics=",".join(spec.metrics),
                value_field=spec.value_field,
                limit=spec.limit,
                offset=spec.offset,
                to_excel=spec.to_excel,
                filename=spec.filename,
                title=spec.title,
            )
            return _merge_result(base_out, r, spec)

        return {
            **base_out,
            "ok": False,
            "error": "Нет доступных executors (compare/build_report)",
            "need_clarification": True,
            "question": "Уточните объект и период отчёта.",
        }
    except Exception as e:
        return {
            **base_out,
            "ok": False,
            "error": str(e),
        }


def _shape_kwargs(spec: ReportSpec) -> dict[str, Any]:
    """«Как строить» → kwargs движка compare_periods."""
    return {
        "filters": list(spec.filters or []),
        "sort": {"sum_desc": "", "sum_asc": "asc"}.get(spec.sort, spec.sort),
        "time_grain": spec.time_grain,
        "top_n": int(spec.top_n or 0),
        "soft_group_by": ", ".join(spec.soft_group_by),
    }


def _apply_text_filter(result: dict[str, Any], needle: str) -> dict[str, Any]:
    n = _norm(needle)
    rows = result.get("rows") or []
    if not isinstance(rows, list) or not n:
        return result
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        blob = " ".join(str(v) for v in r.values() if v is not None)
        if n in _norm(blob):
            out.append(r)
    result = dict(result)
    result["rows"] = out
    result["count"] = len(out)
    result["filter_applied"] = needle
    return result


def _merge_result(
    base: dict[str, Any], result: Any, spec: ReportSpec
) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {**base, "ok": False, "error": f"unexpected result: {type(result)}"}
    out = dict(result)
    # preserve builder meta
    out["builder"] = "universal_report_builder"
    out["spec"] = spec.to_dict()
    out["plan"] = base.get("plan")
    if result.get("need_clarification"):
        out["ok"] = out.get("ok", False)
        return out
    if "ok" not in out and not out.get("error"):
        out["ok"] = True
    # prefer
    pref_b = base.get("prefer") or ""
    pref_r = (out.get("prefer") or "").strip()
    out["prefer"] = (pref_b + "\n" + pref_r).strip()
    # ensure manager sees plan
    ms = out.get("manager_summary")
    if isinstance(ms, dict):
        msg = ms.get("message") or ""
        plan_one = "; ".join(
            x.replace("  • ", "") for x in (base.get("plan") or [])[1:5]
        )
        if plan_one and "План:" not in str(msg):
            ms = dict(ms)
            ms["message"] = f"{msg} План: {plan_one}." if msg else f"План: {plan_one}."
            out["manager_summary"] = ms
    return out


def is_report_builder_question(phrase: str) -> bool:
    """Явный запрос «построй отчёт» / универсальный builder."""
    pl = _norm(phrase)
    if not pl:
        return False
    # синтаксис «как написать ВЫБРАТЬ с группировкой» — не excel-отчёт
    try:
        from tools.route_intent import is_platform_help_question

        if is_platform_help_question(phrase):
            return False
    except Exception:
        pass
    if re.search(
        r"(как\s+написать|как\s+писать|синтаксис|язык\s+запрос|"
        r"функци\w*\s+\w+|добавитькдате|выбрать\s+с\s+группир)",
        pl,
    ) and not any(x in pl for x in ("продаж", "реализ", "excel", "эксель", "в файл")):
        return False
    markers = (
        "построй отчёт",
        "построй отчет",
        "построить отчёт",
        "построить отчет",
        "построитель отчёт",
        "построитель отчет",
        "универсальный отчёт",
        "универсальный отчет",
        "report builder",
        "сделай отчёт",
        "сделай отчет",
        "сформируй отчёт",
        "сформируй отчет",
        "нужен отчёт",
        "нужен отчет",
        "отчёт по",
        "отчет по",
        "в разрезе",
        "запих",
        "в отчёт",
        "в отчет",
        "положи в отч",
        "выгрузи в отч",
        "оформи отч",
    )
    if any(m in pl for m in markers):
        return True
    # «с группировкой» только вместе с отчётом/продаж/excel — не синтаксис
    if "с группировкой" in pl or "с группировками" in pl:
        if any(
            x in pl
            for x in (
                "отчет",
                "отчёт",
                "продаж",
                "excel",
                "эксель",
                "построй",
                "сделай",
                "выгруз",
            )
        ):
            return True
    # «продажи по X за Y» — тоже builder, но preflight уже ведёт на compare/build
    return False
