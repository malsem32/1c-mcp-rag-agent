# -*- coding: utf-8 -*-
"""
Связка нескольких объектов конфигурации в один ВЫБРАТЬ.

Принципы (любая конфигурация, без хардкода имён объектов):
  - имена только из resolve/find_metadata/structure ЭТОЙ базы;
  - виртуальные таблицы регистров: .Остатки / .Обороты / .СрезПоследних;
  - несколько ПОМЕСТИТЬ (до 10) — можно;
  - ВЛОЖЕННЫЕ подзапросы (ВЫБРАТЬ в ИЗ) — НЕ строим (менеджеру не нужны).
"""

from __future__ import annotations

import re
from typing import Any, Callable

from tools.query_1c_normalize import normalize_to_1c_query
from tools.query_builder import normalize_metadata_name, to_datavremya


def is_debt_unpaid_question(phrase: str) -> bool:
    """Узкий intent: задолженность / неоплата (без Qdrant)."""
    pl = (phrase or "").lower().replace("ё", "е")
    if not pl:
        return False
    markers = (
        "неоплачен",
        "не оплачен",
        "задолжен",
        "долг клиент",
        "долги клиент",
        "не закрыт счет",
        "незакрыт",
        "просроченн",
        "дебиторк",
        "непогашен",
    )
    return any(m in pl for m in markers)


def is_stock_balances_question(phrase: str) -> bool:
    """
    Остатки/наличие товаров (точка «на сейчас», не оборот за период).

    Ловит разговорные формы: «осталось», «сколько на складах», «в наличии».
    Не путать с «остальное», «осталось времени» без склада/товара.
    """
    pl = (phrase or "").lower().replace("ё", "е")
    if not pl:
        return False
    # долги / взаиморасчёты — не складские остатки
    if any(
        x in pl
        for x in (
            "задолжен",
            "неоплач",
            "не оплач",
            "дебитор",
            "кредитор",
            "взаиморасч",
            "долг клиент",
            "долги клиент",
        )
    ):
        return False
    # классика: остатки / остаток
    if re.search(r"\bостатк", pl):
        return True
    # «осталось / остались / остался» (не «остальное»)
    if re.search(r"\bостал(?:ось|ись|ся|о)\b", pl) and any(
        x in pl for x in ("склад", "товар", "номенклатур", "запас", "налич")
    ):
        return True
    # сколько/есть … на складе(ах)
    if any(x in pl for x in ("на склад", "по склад", "со склад", "складах", "складе")) and any(
        x in pl
        for x in (
            "товар",
            "сколько",
            "номенклатур",
            "колич",
            "наличие",
            "запас",
            "есть",
            "остал",
        )
    ):
        return True
    if any(
        x in pl
        for x in (
            "в наличии",
            "наличие на",
            "запасы",
            "запас товар",
            "товары на склад",
            "количество на склад",
        )
    ):
        return True
    return False


def is_linked_or_debt_data_question(phrase: str) -> bool:
    pl = (phrase or "").lower().replace("ё", "е")
    if not pl:
        return False
    if is_debt_unpaid_question(phrase):
        return True
    if is_stock_balances_question(phrase):
        return True
    markers = (
        "задолжен",
        "неоплачен",
        "не оплачен",
        "долг",
        "остатк",
        "свяжи",
        "соедини",
        "из нескольких",
        "из разных",
        "по регистр",
        "и уид",
        "uid строк",
        "цена и количеств",
        "количество и цена",
        "нужны поля",
        "мне нужны",
    )
    # не перехватывать обычные «продажи/реализации/топ» — это build_report
    if any(x in pl for x in ("продаж", "реализ", "топ ", "сколько документ", "выручк")):
        if not any(
            x in pl
            for x in ("задолжен", "неоплач", "долг", "остатк", "взаиморасч")
        ) and not is_stock_balances_question(phrase):
            return False
    if any(m in pl for m in markers):
        return True
    if re.search(r"остатк\w*\s+(по|взаиморасч|товар|склад)", pl):
        return True
    return False


def _year_from_text(text: str, default: int | None = None) -> int:
    m = re.search(r"\b((?:19|20)\d{2})\b", text or "")
    if m:
        return int(m.group(1))
    if default is not None:
        return int(default)
    from datetime import datetime as _dt

    return max(1990, _dt.now().year - 1)


def _end_of_year(year: int) -> str:
    return f"ДАТАВРЕМЯ({year},12,31,23,59,59)"


def _parse_sources(raw: str) -> list[str]:
    """«РегистрНакопления.X, Документ.Y» или по строкам."""
    if not (raw or "").strip():
        return []
    parts = re.split(r"[,;\n]+", raw)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        try:
            out.append(normalize_metadata_name(p))
        except ValueError:
            out.append(p)
    return out


def _parse_fields_needed(raw: str) -> list[str]:
    """цена, количество, товары, уид строки заказа → tokens."""
    if not (raw or "").strip():
        return []
    # split by comma or space
    toks = re.split(r"[,;]+|\s+и\s+", raw.strip())
    out = []
    for t in toks:
        t = t.strip()
        if len(t) >= 2:
            out.append(t)
    return out


def _field_names_from_structure(st: dict[str, Any]) -> list[dict[str, str]]:
    """Плоский список name/type/kind из structure."""
    rows: list[dict[str, str]] = []
    for d in st.get("dimensions") or []:
        if isinstance(d, dict) and d.get("name"):
            rows.append(
                {
                    "name": str(d["name"]),
                    "type": str(d.get("type") or d.get("тип") or ""),
                    "kind": "dimension",
                    "synonym": str(d.get("synonym") or ""),
                }
            )
    for r in st.get("resources") or []:
        if isinstance(r, dict) and r.get("name"):
            rows.append(
                {
                    "name": str(r["name"]),
                    "type": str(r.get("type") or r.get("тип") or ""),
                    "kind": "resource",
                    "synonym": str(r.get("synonym") or ""),
                }
            )
    for f in st.get("fields") or []:
        if not isinstance(f, dict):
            continue
        name = str(f.get("name") or "")
        if not name:
            continue
        kind = str(f.get("kind") or "attribute")
        rows.append(
            {
                "name": name,
                "type": str(f.get("type") or f.get("тип") or ""),
                "kind": kind,
                "synonym": str(f.get("synonym") or f.get("синоним") or ""),
            }
        )
    # unique by name
    seen: set[str] = set()
    uniq = []
    for r in rows:
        k = r["name"].lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    return uniq


def _match_needed(needed: list[str], fields: list[dict[str, str]]) -> list[dict[str, str]]:
    """Сопоставить «цена/количество/уид» с реальными именами полей."""
    if not needed:
        return []
    picked = []
    used: set[str] = set()
    aliases = {
        "цена": ("цена", "price", "ценаруб"),
        "количество": ("количество", "qty", "колич"),
        "сумма": ("сумма", "sum", "суммадокумент", "коплате", "долг"),
        "товар": ("номенклатура", "товар", "product"),
        "номенклатура": ("номенклатура", "товар"),
        "уид": ("уникальныйидентификатор", "uid", "идентификатор", "ключ"),
        "uid": ("уникальныйидентификатор", "uid", "идентификатор"),
        "строка": ("номерстроки", "костроки", "строка", "ids"),
        "заказ": ("заказ", "заказклиент", "распоряжен"),
        "контрагент": ("контрагент", "партнер", "partner", "клиент"),
        "партнер": ("партнер", "контрагент", "клиент"),
        "склад": ("склад", "warehouse"),
        "дата": ("дата", "период"),
        "номер": ("номер",),
    }
    for need in needed:
        nl = need.lower().replace("ё", "е").replace(" ", "")
        keys = [nl]
        for ak, variants in aliases.items():
            if ak in nl or nl in ak:
                keys.extend(variants)
        best = None
        best_score = 0
        for f in fields:
            if f["name"].lower() in used:
                continue
            blob = (
                f["name"] + " " + f.get("synonym", "") + " " + f.get("type", "")
            ).lower().replace("ё", "е")
            score = 0
            for k in keys:
                if k and k in blob:
                    score += len(k)
            if score > best_score:
                best_score = score
                best = f
        if best and best_score >= 3:
            used.add(best["name"].lower())
            picked.append({**best, "matched_from": need})
    return picked


def _is_accum(full_name: str) -> bool:
    return (full_name or "").startswith("РегистрНакопления.")


def _is_doc(full_name: str) -> bool:
    return (full_name or "").startswith("Документ.")


def _looks_ref_field(f: dict[str, str]) -> bool:
    """Ссылочное измерение/поле по type/имени (structure любой CF)."""
    typ = (f.get("type") or "").lower().replace(" ", "")
    name = (f.get("name") or "").lower().replace("ё", "е")
    if any(
        m in typ
        for m in (
            "ссылка",
            "ref",
            "catalogref",
            "documentref",
            "enumref",
            "справочникссылка",
            "документссылка",
        )
    ):
        return True
    if any(
        b in name
        for b in (
            "контрагент",
            "партнер",
            "организация",
            "склад",
            "договор",
            "валюта",
            "менеджер",
            "номенклатура",
            "аналитика",
            "заказ",
            "сценарий",
            "статус",
            "пользователь",
            "подразделение",
        )
    ):
        return True
    return False


def _money_resource_name(resources: list[dict[str, str]]) -> str | None:
    """Первый «денежный» ресурс для ORDER/WHERE, иначе первый."""
    if not resources:
        return None
    for r in resources:
        n = (r.get("name") or "").lower().replace("ё", "е")
        if any(k in n for k in ("сумма", "оплат", "долг", "выручк", "стоим", "кплате")):
            return r["name"]
    return resources[0]["name"]


def _wants_totals(phrase: str) -> bool:
    pl = (phrase or "").lower().replace("ё", "е")
    return any(
        x in pl
        for x in ("итог", "всего", "общ", "totals", "с итогом", "с итогами")
    )


def _wants_period_turnovers(phrase: str) -> bool:
    pl = (phrase or "").lower().replace("ё", "е")
    return any(
        x in pl
        for x in (
            "оборот",
            "помесяч",
            "по месяц",
            "динамик",
            "по периодам",
            "помесячно",
            "по квартал",
        )
    )


def _periodicity_from_phrase(phrase: str) -> str:
    pl = (phrase or "").lower().replace("ё", "е")
    if "квартал" in pl:
        return "Квартал"
    if "день" in pl or "по дням" in pl:
        return "День"
    return "Месяц"


def build_balances_query(
    register_full_name: str,
    fields: list[dict[str, str]],
    *,
    as_of: str,
    limit: int = 50,
    only_nonzero_resource: str | None = None,
    with_presentation: bool = True,
    with_isnull: bool = True,
    order_desc: bool = True,
    with_totals: bool = False,
    allowed: bool = True,
) -> dict[str, Any]:
    """
    РегистрНакопления.X.Остатки(Дата, ) —
    ПРЕДСТАВЛЕНИЕ / ЕСТЬNULL / УПОРЯДОЧИТЬ / опционально ИТОГИ.
    """
    dims = [f for f in fields if f.get("kind") == "dimension"][:8]
    resources = [f for f in fields if f.get("kind") == "resource"][:6]
    if not dims and not resources:
        dims = [f for f in fields if f.get("kind") != "standard"][:6]
    select_parts: list[str] = []
    group_for_totals: list[str] = []
    for d in dims:
        n = d["name"]
        if with_presentation and _looks_ref_field(d):
            select_parts.append(f"ПРЕДСТАВЛЕНИЕ(Т.{n}) КАК {n}")
        else:
            select_parts.append(f"Т.{n} КАК {n}")
        group_for_totals.append(f"Т.{n}")
    money = only_nonzero_resource or _money_resource_name(resources)
    for r in resources:
        n = r["name"]
        col = f"{n}Остаток"
        expr = f"Т.{col}"
        if with_isnull:
            expr = f"ЕСТЬNULL(Т.{col}, 0)"
        select_parts.append(f"{expr} КАК {col}")
    if not select_parts:
        return {"error": "Нет dimensions/resources у регистра — get_object_structure"}
    source = f"{register_full_name}.Остатки({as_of}, )"
    where = ""
    if money:
        where = f" ГДЕ Т.{money}Остаток <> 0"
    elif resources:
        where = f" ГДЕ Т.{resources[0]['name']}Остаток <> 0"

    head = "ВЫБРАТЬ"
    if allowed:
        head += " РАЗРЕШЕННЫЕ"
    if not with_totals:
        head += f" ПЕРВЫЕ {max(1, int(limit))}"
    q = head + " " + ", ".join(select_parts) + f" ИЗ {source} КАК Т" + where
    if order_desc and money:
        q += f" УПОРЯДОЧИТЬ ПО {money}Остаток УБЫВ"
    if with_totals and resources:
        # ИТОГИ по денежным ресурсам (имена алиасов из SELECT)
        tot = ", ".join(
            f"СУММА({r['name']}Остаток)" for r in resources[:4]
        )
        # детализация по измерениям + общий итог
        if group_for_totals:
            q += f" ИТОГИ {tot} ПО ОБЩИЕ"
        else:
            q += f" ИТОГИ {tot} ПО ОБЩИЕ"
    return {
        "query": q,
        "virtual_tables": [source],
        "style": "register_balances",
        "select_fields": select_parts,
        "functions_used": [
            x
            for x, on in (
                ("ПРЕДСТАВЛЕНИЕ", with_presentation),
                ("ЕСТЬNULL", with_isnull),
                ("УПОРЯДОЧИТЬ ПО", order_desc and bool(money)),
                ("ИТОГИ", with_totals),
                ("РАЗРЕШЕННЫЕ", allowed),
                ("ДАТАВРЕМЯ", "ДАТАВРЕМЯ" in as_of),
            )
            if on
        ],
    }


def build_turnovers_period_query(
    register_full_name: str,
    fields: list[dict[str, str]],
    *,
    start: str,
    end: str,
    periodicity: str = "Месяц",
    limit: int = 50,
    group_dims: list[str] | None = None,
    with_totals: bool = False,
) -> dict[str, Any]:
    """
    .Обороты(Начало, Конец, Периодичность, ) + НАЧАЛОПЕРИОДА + СГРУППИРОВАТЬ + УПОРЯДОЧИТЬ.
    """
    dims = [f for f in fields if f.get("kind") == "dimension"]
    resources = [f for f in fields if f.get("kind") == "resource"][:4]
    if not resources:
        return {"error": "Нет resources для .Обороты — structure"}

    per = periodicity if periodicity in (
        "Запись",
        "Регистратор",
        "День",
        "Неделя",
        "Месяц",
        "Квартал",
        "Год",
        "Авто",
    ) else "Месяц"

    select_parts: list[str] = [
        f"НАЧАЛОПЕРИОДА(Т.Период, {per}) КАК Период",
        f"ГОД(НАЧАЛОПЕРИОДА(Т.Период, {per})) КАК Год",
        f"МЕСЯЦ(НАЧАЛОПЕРИОДА(Т.Период, {per})) КАК МесяцНомер",
    ]
    group_parts: list[str] = [
        f"НАЧАЛОПЕРИОДА(Т.Период, {per})",
        f"ГОД(НАЧАЛОПЕРИОДА(Т.Период, {per}))",
        f"МЕСЯЦ(НАЧАЛОПЕРИОДА(Т.Период, {per}))",
    ]

    # optional extra dimensions (e.g. manager)
    dim_names = {d["name"] for d in dims}
    extra: list[str] = []
    if group_dims:
        for g in group_dims:
            if g in dim_names and g not in extra:
                extra.append(g)
    # default: at most one ref dimension for readability
    if not extra:
        for d in dims[:1]:
            if _looks_ref_field(d):
                extra.append(d["name"])
                break

    for n in extra[:3]:
        d = next((x for x in dims if x["name"] == n), {"name": n, "type": ""})
        if _looks_ref_field(d):
            select_parts.append(f"ПРЕДСТАВЛЕНИЕ(Т.{n}) КАК {n}")
        else:
            select_parts.append(f"Т.{n} КАК {n}")
        group_parts.append(f"Т.{n}")

    for r in resources:
        n = r["name"]
        select_parts.append(f"СУММА(ЕСТЬNULL(Т.{n}Оборот, 0)) КАК {n}Оборот")

    source = f"{register_full_name}.Обороты({start}, {end}, {per}, )"
    q = (
        f"ВЫБРАТЬ ПЕРВЫЕ {max(1, int(limit))} "
        + ", ".join(select_parts)
        + f" ИЗ {source} КАК Т"
        + " СГРУППИРОВАТЬ ПО "
        + ", ".join(group_parts)
        + " УПОРЯДОЧИТЬ ПО Период"
    )
    if with_totals:
        tot = ", ".join(f"СУММА({r['name']}Оборот)" for r in resources)
        q += f" ИТОГИ {tot} ПО ОБЩИЕ"

    return {
        "query": q,
        "virtual_tables": [source],
        "style": "register_turnovers_period",
        "periodicity": per,
        "functions_used": [
            x
            for x in (
                "НАЧАЛОПЕРИОДА",
                "ГОД",
                "МЕСЯЦ",
                "СУММА",
                "ЕСТЬNULL",
                "СГРУППИРОВАТЬ ПО",
                "УПОРЯДОЧИТЬ ПО",
                "ПРЕДСТАВЛЕНИЕ" if extra else "",
                "ИТОГИ" if with_totals else "",
                "Обороты",
            )
            if x
        ],
    }


def build_join_two_balances(
    reg_a: str,
    fields_a: list[dict[str, str]],
    reg_b: str,
    fields_b: list[dict[str, str]],
    *,
    as_of: str,
    limit: int = 40,
) -> dict[str, Any] | None:
    """
    Две виртуальные таблицы .Остатки + ЛЕВОЕ СОЕДИНЕНИЕ по общим измерениям.
    Без ПОМЕСТИТЬ (HTTP часто не принимает пакет с ;).
    """
    dims_a = [f for f in fields_a if f.get("kind") == "dimension"]
    dims_b = [f for f in fields_b if f.get("kind") == "dimension"]
    res_a = [f for f in fields_a if f.get("kind") == "resource"]
    res_b = [f for f in fields_b if f.get("kind") == "resource"]
    names_a = {f["name"] for f in dims_a}
    names_b = {f["name"] for f in dims_b}
    common = [n for n in names_a & names_b if n not in ("Период", "Регистратор", "НомерСтроки")]
    if not common:
        # try fuzzy: same stem parts
        for da in dims_a:
            for db in dims_b:
                na, nb = da["name"].lower(), db["name"].lower()
                if na == nb or (len(na) >= 6 and (na in nb or nb in na)):
                    common = [da["name"]]
                    # map b side name if different
                    if da["name"] != db["name"]:
                        # can't join different names easily without alias equality of same name
                        pass
                    break
            if common:
                break
    if not common or not res_a:
        return None

    join_keys = common[:3]
    select_parts: list[str] = []
    for k in join_keys:
        fa = next((x for x in dims_a if x["name"] == k), {"name": k, "type": ""})
        if _looks_ref_field(fa):
            select_parts.append(f"ПРЕДСТАВЛЕНИЕ(А.{k}) КАК {k}")
        else:
            select_parts.append(f"А.{k} КАК {k}")

    money_a = _money_resource_name(res_a)
    for r in res_a[:3]:
        n = r["name"]
        select_parts.append(f"ЕСТЬNULL(А.{n}Остаток, 0) КАК {n}Остаток_А")
    for r in res_b[:3]:
        n = r["name"]
        # avoid alias clash
        select_parts.append(f"ЕСТЬNULL(Б.{n}Остаток, 0) КАК {n}Остаток_Б")

    on = " И ".join(f"А.{k} = Б.{k}" for k in join_keys)
    src_a = f"{reg_a}.Остатки({as_of}, )"
    src_b = f"{reg_b}.Остатки({as_of}, )"
    where = ""
    if money_a:
        where = f" ГДЕ А.{money_a}Остаток <> 0"
    q = (
        f"ВЫБРАТЬ РАЗРЕШЕННЫЕ ПЕРВЫЕ {max(1, int(limit))} "
        + ", ".join(select_parts)
        + f" ИЗ {src_a} КАК А "
        + f"ЛЕВОЕ СОЕДИНЕНИЕ {src_b} КАК Б ПО {on}"
        + where
    )
    if money_a:
        q += f" УПОРЯДОЧИТЬ ПО {money_a}Остаток_А УБЫВ"

    return {
        "query": q,
        "virtual_tables": [src_a, src_b],
        "style": "join_two_balances",
        "join": on,
        "functions_used": [
            "ПРЕДСТАВЛЕНИЕ",
            "ЕСТЬNULL",
            "ЛЕВОЕ СОЕДИНЕНИЕ",
            "УПОРЯДОЧИТЬ ПО",
            "РАЗРЕШЕННЫЕ",
            "Остатки",
        ],
    }


def build_document_list_query(
    doc_full_name: str,
    fields: list[dict[str, str]],
    *,
    year: int | None,
    limit: int = 50,
    conducted_only: bool = True,
) -> dict[str, Any]:
    preferred = []
    name_map = {f["name"].lower(): f["name"] for f in fields}
    field_by_name = {f["name"]: f for f in fields}
    for cand in (
        "Номер",
        "Дата",
        "Ссылка",
        "Контрагент",
        "Партнер",
        "СуммаДокумента",
        "Сумма",
        "Проведен",
        "Организация",
    ):
        if cand.lower() in name_map:
            preferred.append(name_map[cand.lower()])
    for f in fields:
        if f.get("matched_from") and f["name"] not in preferred:
            preferred.append(f["name"])
    if not preferred:
        preferred = [f["name"] for f in fields[:8] if f.get("kind") != "standard"]
    select_parts = []
    for n in preferred[:12]:
        f = field_by_name.get(n) or {"name": n, "type": ""}
        if _looks_ref_field(f) or n in (
            "Контрагент",
            "Партнер",
            "Организация",
            "Склад",
            "Менеджер",
        ):
            select_parts.append(f"ПРЕДСТАВЛЕНИЕ(Т.{n}) КАК {n}")
        elif any(x in n.lower() for x in ("сумма", "количеств", "цена")):
            select_parts.append(f"ЕСТЬNULL(Т.{n}, 0) КАК {n}")
        else:
            select_parts.append(f"Т.{n} КАК {n}")
    where_parts = []
    if conducted_only and any(f["name"] == "Проведен" for f in fields):
        where_parts.append("Т.Проведен = ИСТИНА")
    if year and any(f["name"] == "Дата" for f in fields):
        where_parts.append(
            f"Т.Дата МЕЖДУ ДАТАВРЕМЯ({year},1,1,0,0,0) "
            f"И ДАТАВРЕМЯ({year},12,31,23,59,59)"
        )
    where = (" ГДЕ " + " И ".join(where_parts)) if where_parts else ""
    order = ""
    if any(f["name"] == "Дата" for f in fields):
        order = " УПОРЯДОЧИТЬ ПО Т.Дата УБЫВ"
    q = (
        f"ВЫБРАТЬ РАЗРЕШЕННЫЕ ПЕРВЫЕ {limit} "
        + ", ".join(select_parts)
        + f" ИЗ {doc_full_name} КАК Т"
        + where
        + order
    )
    return {
        "query": q,
        "virtual_tables": [],
        "style": "document_list",
        "functions_used": [
            "ПРЕДСТАВЛЕНИЕ",
            "ЕСТЬNULL",
            "МЕЖДУ",
            "ДАТАВРЕМЯ",
            "УПОРЯДОЧИТЬ ПО",
            "РАЗРЕШЕННЫЕ",
        ],
    }


def build_join_doc_and_balances(
    doc_name: str,
    doc_fields: list[dict[str, str]],
    reg_name: str,
    reg_fields: list[dict[str, str]],
    *,
    as_of: str,
    limit: int = 40,
    year: int | None = None,
) -> dict[str, Any] | None:
    """
    Документ + .Остатки через ЛЕВОЕ СОЕДИНЕНИЕ (без вложенного SELECT / без ПОМЕСТИТЬ).
    Join: Ссылка↔Регистратор / поле типа Документ.* / общее имя.
    """
    doc_names = {f["name"] for f in doc_fields}
    reg_names = {f["name"] for f in reg_fields}
    common = [n for n in doc_names & reg_names if n not in ("Ссылка", "Дата", "Номер")]
    join_left = None
    join_right = None
    if "Ссылка" in doc_names:
        if "Регистратор" in reg_names:
            join_left, join_right = "Ссылка", "Регистратор"
        else:
            for f in reg_fields:
                typ = (f.get("type") or "").replace(" ", "")
                if "Документ." in typ or "DocumentRef." in typ or "Document." in typ:
                    join_left, join_right = "Ссылка", f["name"]
                    break
            if not join_left:
                for n in reg_names:
                    if n == "Документ" or n.endswith("Документ") or "Документ" in n:
                        join_left, join_right = "Ссылка", n
                        break
            # измерения ссылочные (объект расчётов, заказ…) — по совпадению с шапкой
            if not join_left:
                for n in common:
                    if any(
                        k in n.lower()
                        for k in ("заказ", "объект", "расчет", "документ", "основан")
                    ):
                        join_left = join_right = n
                        break
    if not join_left and common:
        join_left = join_right = common[0]
    if not join_left:
        return None

    dsel = []
    for n in ("Номер", "Дата", "СуммаДокумента", "Сумма", "Контрагент", "Партнер", "Организация"):
        if n not in doc_names:
            continue
        if n in ("Контрагент", "Партнер", "Организация"):
            dsel.append(f"ПРЕДСТАВЛЕНИЕ(Д.{n}) КАК {n}")
        elif "сумма" in n.lower():
            dsel.append(f"ЕСТЬNULL(Д.{n}, 0) КАК {n}")
        else:
            dsel.append(f"Д.{n} КАК {n}")
    if not dsel:
        dsel = [f"Д.{f['name']} КАК {f['name']}" for f in doc_fields[:5]]

    rsel = []
    money = None
    for f in reg_fields:
        if f.get("kind") == "resource":
            n = f["name"]
            rsel.append(f"ЕСТЬNULL(О.{n}Остаток, 0) КАК {n}Остаток")
            if money is None:
                money = n
    if not rsel:
        return None

    where_parts = []
    if "Проведен" in doc_names:
        where_parts.append("Д.Проведен = ИСТИНА")
    if year and "Дата" in doc_names:
        where_parts.append(
            f"Д.Дата МЕЖДУ ДАТАВРЕМЯ({year},1,1,0,0,0) "
            f"И ДАТАВРЕМЯ({year},12,31,23,59,59)"
        )
    where = (" ГДЕ " + " И ".join(where_parts)) if where_parts else ""
    order = " УПОРЯДОЧИТЬ ПО Д.Дата УБЫВ" if "Дата" in doc_names else ""
    if money and not order:
        order = f" УПОРЯДОЧИТЬ ПО {money}Остаток УБЫВ"

    src = f"{reg_name}.Остатки({as_of}, )"
    q = (
        f"ВЫБРАТЬ РАЗРЕШЕННЫЕ ПЕРВЫЕ {limit} "
        + ", ".join(dsel)
        + ", "
        + ", ".join(rsel)
        + f" ИЗ {doc_name} КАК Д "
        f"ЛЕВОЕ СОЕДИНЕНИЕ {src} КАК О "
        f"ПО Д.{join_left} = О.{join_right}"
        + where
        + order
    )
    return {
        "query": q,
        "virtual_tables": [src],
        "style": "join_doc_balances",
        "join": f"Д.{join_left} = О.{join_right}",
        "functions_used": [
            "ПРЕДСТАВЛЕНИЕ",
            "ЕСТЬNULL",
            "ЛЕВОЕ СОЕДИНЕНИЕ",
            "МЕЖДУ",
            "ДАТАВРЕМЯ",
            "УПОРЯДОЧИТЬ ПО",
            "РАЗРЕШЕННЫЕ",
            "Остатки",
        ],
    }


def plan_linked_query(
    request: str = "",
    *,
    sources: str = "",
    fields_needed: str = "",
    as_of: str = "",
    limit: int = 40,
    resolve_fn: Callable[[str], dict[str, Any]] | None = None,
    structure_fn: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    План + текст запроса. Имена — через resolve_fn/structure_fn снаружи (UniversalTools).
    """
    req = (request or "").strip()
    year = _year_from_text(req)
    has_year_in_phrase = bool(re.search(r"\b((?:19|20)\d{2})\b", req))
    stockish0 = is_stock_balances_question(req)
    if as_of.strip():
        try:
            as_of_expr = to_datavremya(as_of, end_of_day=True)
        except Exception:
            as_of_expr = _end_of_year(year)
    elif stockish0 and not has_year_in_phrase:
        # «сколько на складах осталось» → срез на сейчас, не «укажите период»
        from datetime import datetime as _dt

        now = _dt.now()
        as_of_expr = (
            f"ДАТАВРЕМЯ({now.year},{now.month},{now.day},"
            f"{now.hour},{now.minute},{now.second})"
        )
    else:
        as_of_expr = _end_of_year(year)

    source_list = _parse_sources(sources)
    needed = _parse_fields_needed(fields_needed)
    # from request: «нужны цена количество»
    if not needed:
        m = re.search(
            r"(?:нужн\w*|поля?|хочу|дай)\s*[:\s]+(.+)$",
            req,
            re.I,
        )
        if m:
            needed = _parse_fields_needed(m.group(1))

    resolved_objects: list[dict[str, Any]] = []
    # explicit sources first
    for s in source_list:
        resolved_objects.append({"full_name": s, "from": "sources_param"})

    # resolve from phrase if empty
    if not resolved_objects and resolve_fn and req:
        ro = resolve_fn(req)
        debtish0 = is_linked_or_debt_data_question(req)
        turnish0 = _wants_period_turnovers(req)
        prefer_regs = (
            debtish0
            or turnish0
            or stockish0
            or any(
                x in req.lower().replace("ё", "е")
                for x in ("остатк", "регистр", "взаиморасч", "склад")
            )
        )
        # expect items / top / full_name
        if isinstance(ro, dict):
            if ro.get("full_name") and not prefer_regs:
                resolved_objects.append(
                    {"full_name": ro["full_name"], "from": "resolve"}
                )
            items_all = list(ro.get("items") or []) + list(ro.get("options") or [])
            # при долге/оборотах — сначала регистры накопления по стемам (не hardcode CF)
            if prefer_regs:
                _reg_stems = (
                    "расчет",
                    "взаиморасчет",
                    "задолжен",
                    "оплат",
                    "долг",
                    "выручк",
                    "себестоим",
                    "продаж",
                    "товар",
                    "склад",
                    "settlement",
                    "debt",
                )

                def _reg_rank(it: dict) -> int:
                    fn = str((it or {}).get("full_name") or "").lower().replace("ё", "е")
                    if not fn.startswith("регистрнакопления."):
                        return 3
                    if any(s in fn for s in _reg_stems):
                        return 0
                    return 1

                items_all = sorted(items_all, key=_reg_rank)
            for it in items_all[:20]:
                fn = it.get("full_name") if isinstance(it, dict) else None
                if fn:
                    resolved_objects.append({"full_name": fn, "from": "resolve"})
            if prefer_regs:
                resolved_objects = [
                    o
                    for o in resolved_objects
                    if str(o.get("full_name") or "").startswith(
                        ("РегистрНакопления.", "РегистрСведений.")
                    )
                    and not str(o.get("full_name") or "").startswith("Отчет.")
                ]

    # unique full_names
    seen: set[str] = set()
    objs = []
    for o in resolved_objects:
        fn = o.get("full_name") or ""
        try:
            fn = normalize_metadata_name(fn)
        except Exception:
            pass
        if fn and fn not in seen:
            seen.add(fn)
            objs.append({**o, "full_name": fn})

    # stock: регистры «товар+склад/запас» вперёд; «к отбору/отгрузке» — в конец
    pl_early = req.lower().replace("ё", "е")
    if any(
        x in pl_early
        for x in ("остатк", "налич", "запас", "на склад")
    ) and not any(
        x in pl_early for x in ("задолжен", "клиент", "взаиморасч", "долг")
    ):

        def _stock_obj_rank(o: dict[str, Any]) -> int:
            fn = str(o.get("full_name") or "").lower().replace("ё", "е").replace(" ", "")
            if any(
                x in fn
                for x in (
                    "котбору",
                    "котгрузке",
                    "кпоступлению",
                    "впути",
                    "вячейк",
                    "кперемещ",
                )
            ):
                return 50
            if ("товар" in fn or "номенклатур" in fn) and (
                "склад" in fn or "запас" in fn or "остат" in fn or "организац" in fn
            ):
                return 0
            if "свободн" in fn and "остат" in fn:
                return 1
            if "товар" in fn:
                return 5
            return 20

        objs = sorted(objs, key=_stock_obj_rank)

    if not objs:
        return {
            "ok": False,
            "error": "Не удалось определить объекты. Укажите sources=Документ.X,РегистрНакопления.Y "
            "(X,Y из find_metadata этой базы) или уточните фразу (задолженность / остатки / …).",
            "hint": "resolve_object + get_object_structure, затем linked_query снова",
            "prefer": "Универсально: только имена из find_metadata/structure текущей базы.",
        }

    # load structures
    detailed = []
    if structure_fn:
        for o in objs[:8]:
            st = structure_fn(o["full_name"])
            if not isinstance(st, dict) or st.get("error"):
                continue
            fields = _field_names_from_structure(st)
            matched = _match_needed(needed, fields)
            detailed.append(
                {
                    "full_name": o["full_name"],
                    "from": o.get("from"),
                    "synonym": st.get("synonym"),
                    "dimensions": (st.get("dimensions") or [])[:15],
                    "resources": (st.get("resources") or [])[:15],
                    "fields": fields[:40],
                    "dimensions_total": len(st.get("dimensions") or []),
                    "resources_total": len(st.get("resources") or []),
                    "fields_total": len(fields),
                    "matched_fields": matched,
                    "status_fields": st.get("status_fields"),
                }
            )

    if not detailed:
        return {
            "ok": False,
            "error": "structure пустой — нет code_dir/HTTP object",
            "objects": [o["full_name"] for o in objs],
        }

    # choose strategy
    regs = [d for d in detailed if _is_accum(d["full_name"])]
    docs = [d for d in detailed if _is_doc(d["full_name"])]

    # при задолженности предпочесть регистр с денежным ресурсом (по structure, не по conf-name)
    def _money_score(d: dict[str, Any]) -> int:
        fn = (d.get("full_name") or "").lower()
        res_names = [
            *(x.get("name") or "" for x in (d.get("resources") or [])),
            *(
                f.get("name") or ""
                for f in (d.get("fields") or [])
                if f.get("kind") == "resource"
            ),
        ]
        names = " ".join([fn, *res_names]).lower().replace("ё", "е")
        sc = 0
        # денежные стемы в имени/ресурсах (любая CF)
        for k in (
            "расчет",
            "взаиморасчет",
            "сумма",
            "оплат",
            "долг",
            "задолжен",
            "дебитор",
            "кредитор",
        ):
            if k in names:
                sc += 12
        # складские/количественные — слабее для «долг»
        qty_hits = sum(
            1
            for k in ("количество", "вналичии", "остат", "товар", "склад", "серия")
            if k in names
        )
        sc -= qty_hits * 4
        return sc

    debtish = is_linked_or_debt_data_question(req)
    pl_req = req.lower().replace("ё", "е")
    stockish = stockish0 or any(
        x in pl_req
        for x in (
            "остатк товар",
            "остатк на склад",
            "товары на склад",
            "налич",
            "запас",
            "остатки товар",
            "остатки на склад",
        )
    ) or (
        "остатк" in pl_req
        and any(x in pl_req for x in ("склад", "товар", "номенклатур"))
        and not any(x in pl_req for x in ("задолжен", "клиент", "взаиморасч", "долг"))
    )
    if debtish and regs and not stockish:
        regs = sorted(regs, key=_money_score, reverse=True)
    if stockish and regs:

        def _stock_score(d: dict[str, Any]) -> int:
            fn = (d.get("full_name") or "").lower().replace("ё", "е").replace(" ", "")
            sc = 0
            if "товарынаскладах" in fn or (
                "товар" in fn and "склад" in fn and "отбор" not in fn and "отгруз" not in fn
            ):
                sc += 100
            if "товарыорганизаций" in fn:
                sc += 70
            if "свободныеостат" in fn:
                sc += 50
            if any(
                x in fn
                for x in (
                    "котбору",
                    "котгрузке",
                    "кпоступлению",
                    "впути",
                    "вячейк",
                    "кперемещ",
                )
            ):
                sc -= 80
            return sc

        regs = sorted(regs, key=_stock_score, reverse=True)

    plans: list[dict[str, Any]] = []
    want_tot = _wants_totals(req)
    want_turn = _wants_period_turnovers(req)
    want_multi = any(
        x in req.lower().replace("ё", "е")
        for x in ("свяжи", "соедини", "два регистр", "нескольк", "сравни регистр")
    ) or len(source_list) >= 2

    # 1) pure balances for debt / остатки
    if regs and (debtish or not docs or not want_turn):
        reg = regs[0]
        fields = list(reg["fields"])
        for m in reg.get("matched_fields") or []:
            if m not in fields:
                fields = [m] + fields
        bq = build_balances_query(
            reg["full_name"],
            fields,
            as_of=as_of_expr,
            limit=limit,
            with_presentation=True,
            with_isnull=True,
            order_desc=True,
            with_totals=want_tot,
        )
        if not bq.get("error"):
            plans.append({**bq, "primary": reg["full_name"], "role": "balances"})

    # 1b) period turnovers (.Обороты + НАЧАЛОПЕРИОДА + GROUP + ORDER)
    if regs and want_turn:
        reg = regs[0]
        start_y = f"ДАТАВРЕМЯ({year},1,1,0,0,0)"
        end_y = as_of_expr if as_of_expr else _end_of_year(year)
        tq = build_turnovers_period_query(
            reg["full_name"],
            reg["fields"],
            start=start_y,
            end=end_y,
            periodicity=_periodicity_from_phrase(req),
            limit=limit,
            with_totals=want_tot,
        )
        if not tq.get("error"):
            plans.append({**tq, "primary": reg["full_name"], "role": "turnovers_period"})

    # 1c) two registers join on common dimensions
    if len(regs) >= 2 and (want_multi or len(source_list) >= 2):
        j2 = build_join_two_balances(
            regs[0]["full_name"],
            regs[0]["fields"],
            regs[1]["full_name"],
            regs[1]["fields"],
            as_of=as_of_expr,
            limit=limit,
        )
        if j2:
            plans.append(
                {
                    **j2,
                    "primary": regs[0]["full_name"],
                    "role": "join_two_regs",
                }
            )

    # 2) join document + balances
    if docs and regs:
        j = build_join_doc_and_balances(
            docs[0]["full_name"],
            docs[0]["fields"],
            regs[0]["full_name"],
            regs[0]["fields"],
            as_of=as_of_expr,
            limit=limit,
            year=year if re.search(r"20\d{2}|год", req) else None,
        )
        if j:
            plans.append({**j, "primary": docs[0]["full_name"], "role": "join_doc_reg"})

    # 3) document list
    if docs:
        dq = build_document_list_query(
            docs[0]["full_name"],
            docs[0]["fields"]
            + (docs[0].get("matched_fields") or []),
            year=year if re.search(r"20\d{2}|год", req) else None,
            limit=limit,
        )
        plans.append({**dq, "primary": docs[0]["full_name"], "role": "document"})

    # pick best plan
    plan = None
    if want_turn and plans:
        plan = next((p for p in plans if p.get("role") == "turnovers_period"), None)
    if want_multi and not plan:
        plan = next((p for p in plans if p.get("role") == "join_two_regs"), None)
    if debtish and not plan and plans:
        plan = next((p for p in plans if p.get("role") == "balances"), plans[0])
    elif not plan and plans:
        plan = next((p for p in plans if p.get("role") == "join_doc_reg"), plans[0])

    if not plan or plan.get("error"):
        return {
            "ok": False,
            "error": plan.get("error") if plan else "Не удалось собрать запрос",
            "objects_detailed": [
                {
                    "full_name": d["full_name"],
                    "dimensions": [x.get("name") for x in (d.get("dimensions") or [])],
                    "resources": [x.get("name") for x in (d.get("resources") or [])],
                    "matched_fields": d.get("matched_fields"),
                }
                for d in detailed
            ],
            "hint": "Укажите sources= явно и fields_needed=цена,количество,…",
        }

    q = plan["query"]
    norm = normalize_to_1c_query(q)
    if norm.get("query"):
        q = norm["query"]

    return {
        "ok": True,
        "request": req,
        "as_of": as_of_expr,
        "year": year,
        "primary": plan.get("primary") or (detailed[0]["full_name"] if detailed else ""),
        "objects": [d["full_name"] for d in detailed],
        "objects_detailed": [
            {
                "full_name": d["full_name"],
                "synonym": d.get("synonym"),
                "dimensions": [
                    {"name": x.get("name"), "type": (x.get("type") or "")[:80]}
                    for x in (d.get("dimensions") or [])[:10]
                ],
                "resources": [x.get("name") for x in (d.get("resources") or [])[:10]],
                "dimensions_total": len(d.get("dimensions") or []),
                "resources_total": len(d.get("resources") or []),
                "matched_fields": d.get("matched_fields"),
            }
            for d in detailed
        ],
        "plan_role": plan.get("role"),
        "join": plan.get("join"),
        "virtual_tables": plan.get("virtual_tables") or [],
        "functions_used": plan.get("functions_used") or [],
        "query": q,
        "alt_plans": [
            {
                "role": p.get("role"),
                "query": (p.get("query") or "")[:400],
                "functions_used": p.get("functions_used") or [],
                "virtual_tables": p.get("virtual_tables") or [],
            }
            for p in plans
            if p is not plan
        ][:5],
        "rules": [
            "Виртуальные таблицы: .Остатки / .Обороты / .СрезПоследних",
            "Несколько ВТ: ЛЕВОЕ/ВНУТРЕННЕЕ СОЕДИНЕНИЕ (без вложенного ВЫБРАТЬ в ИЗ)",
            "ПОМЕСТИТЬ + «;» — не через HTTP (часто не принимается пакетом)",
            "Каталог функций конструктора: query_syntax_help(topic=functions) "
            "/ knowledge/функции_запросов.yaml "
            "(СУММА,МИН/МАКС,СРЕДНЕЕ,КОЛИЧЕСТВО; строки; даты; ЕСТЬNULL; "
            "ПРЕДСТАВЛЕНИЕ; ВЫБОР; ВЫРАЗИТЬ; ДАТАВРЕМЯ; ЗНАЧЕНИЕ; …)",
            "По умолчанию builders: ПРЕДСТАВЛЕНИЕ, ЕСТЬNULL, СУММА, НАЧАЛОПЕРИОДА, "
            "ГОД, МЕСЯЦ, МЕЖДУ, ДАТАВРЕМЯ, РАЗРЕШЕННЫЕ, УПОРЯДОЧИТЬ",
            "ИТОГИ … ПО ОБЩИЕ — если во фразе «итого/всего»",
            "Имена объектов/полей — только structure этой базы",
        ],
        "prefer": (
            "Выполни execute_query(query=…) этим текстом. "
            "Если error/0 rows — alt_plans или другие регистры из objects. "
            "Менеджеру — таблица rows, не разбор JOIN. "
            "Не подставляй имена из другой конфигурации."
        ),
        "note": (
            "Сложные связки: sources='РегистрНакопления.A,РегистрНакопления.B' "
            "или фразы «обороты помесячно», «с итогом», «свяжи регистры». "
            "fields_needed= для сопоставления полей structure."
        ),
    }
