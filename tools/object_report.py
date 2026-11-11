"""
Универсальная сборка ВЫБРАТЬ для отчёта Excel/чата
по ЛЮБОМУ объекту метаданных: Документ / Справочник / Регистр*.

Поля только из parse_structure_fields (structure ЭТОЙ базы).
"""

from __future__ import annotations

import calendar
from typing import Any

from tools.field_discovery import (
    FieldInfo,
    is_reference_type,
    match_role,
    parse_structure_fields,
)
from tools.query_builder import normalize_metadata_name


def object_kind(full_name: str) -> str:
    """document | catalog | info_register | accum_register | other"""
    try:
        name = normalize_metadata_name(full_name)
    except ValueError:
        name = (full_name or "").strip()
    if name.startswith("Документ."):
        return "document"
    if name.startswith("Справочник."):
        return "catalog"
    if name.startswith("РегистрСведений."):
        return "info_register"
    if name.startswith("РегистрНакопления."):
        return "accum_register"
    if name.startswith("Регистр"):
        return "other_register"
    return "other"


def _field_expr(f: FieldInfo, alias_prefix: str = "Т") -> str:
    if is_reference_type(f):
        return f"ПРЕДСТАВЛЕНИЕ({alias_prefix}.{f.name}) КАК {f.name}"
    return f"{alias_prefix}.{f.name} КАК {f.name}"


def _pick_fields(
    fields: list[FieldInfo],
    *,
    kind_order: tuple[str, ...] = ("dimension", "resource", "standard", "attribute"),
    max_cols: int = 12,
    skip_names: frozenset[str] | None = None,
) -> list[FieldInfo]:
    skip = skip_names or frozenset(
        {"Ссылка", "УникальныйИдентификатор", "DataVersion", "ВерсияДанных"}
    )
    header = [f for f in fields if not f.tabular_section and f.name not in skip]
    prio = {k: i for i, k in enumerate(kind_order)}
    header.sort(key=lambda f: (prio.get(f.kind, 50), f.name.lower()))
    # приоритетные роли вперёд, если есть
    role_boost: list[FieldInfo] = []
    for role in (
        "number",
        "date",
        "code",
        "description",
        "counterparty",
        "amount",
        "posted",
        "nomenclature",
        "quantity",
        "organization",
        "warehouse",
    ):
        fi = match_role(header, role)
        if fi and fi not in role_boost:
            role_boost.append(fi)
    ordered: list[FieldInfo] = []
    seen: set[str] = set()
    for f in role_boost + header:
        if f.name in seen:
            continue
        seen.add(f.name)
        ordered.append(f)
        if len(ordered) >= max_cols:
            break
    return ordered


def _period_bounds(year: int, month: int) -> tuple[str, str, int]:
    last = calendar.monthrange(year, month)[1]
    start = f"ДАТАВРЕМЯ({year},{month},1,0,0,0)"
    end = f"ДАТАВРЕМЯ({year},{month},{last},23,59,59)"
    return start, end, last


def build_object_report_query(
    full_name: str,
    structure: dict[str, Any],
    *,
    year: int | None = None,
    month: int | None = None,
    limit: int = 500,
    mode: str = "auto",
    only_posted: bool = True,
) -> dict[str, Any]:
    """
    mode:
      auto | list | period | balances | turnovers | slice
    """
    try:
        name = normalize_metadata_name(full_name)
    except ValueError as e:
        return {"error": str(e)}

    kind = object_kind(name)
    fields = parse_structure_fields(structure)
    if not fields:
        return {
            "error": "Нет полей в structure",
            "hint": "get_object_structure(full_name) — проверьте HTTP /object",
        }

    lim = max(1, min(int(limit or 500), 5000))
    mode_l = (mode or "auto").strip().lower()
    has_period = year is not None and month is not None and 1 <= int(month) <= 12
    y = int(year) if year is not None else 0
    m = int(month) if month is not None else 0

    # --- resolve auto mode ---
    if mode_l == "auto":
        if kind == "document" and has_period:
            mode_l = "period"
        elif kind == "accum_register" and has_period:
            mode_l = "turnovers"
        elif kind == "info_register" and has_period:
            mode_l = "slice"
        else:
            mode_l = "list"

    # --- document period ---
    if kind == "document" and mode_l in ("period", "list"):
        return _query_document(
            name, fields, lim, y, m, has_period and mode_l == "period", only_posted
        )

    # --- catalog ---
    if kind == "catalog":
        return _query_catalog(name, fields, lim)

    # --- accumulation register ---
    if kind == "accum_register":
        if mode_l == "balances" and has_period:
            return _query_accum_virtual(name, fields, lim, y, m, "balances")
        if mode_l in ("turnovers", "period") and has_period:
            return _query_accum_virtual(name, fields, lim, y, m, "turnovers")
        return _query_register_table(name, fields, lim, y, m, has_period)

    # --- info register ---
    if kind == "info_register":
        if mode_l == "slice" and has_period:
            return _query_info_slice(name, fields, lim, y, m)
        return _query_register_table(name, fields, lim, y, m, has_period)

    # --- fallback any table ---
    return _query_register_table(name, fields, lim, y, m, has_period)


def _query_document(
    name: str,
    fields: list[FieldInfo],
    lim: int,
    y: int,
    m: int,
    use_period: bool,
    only_posted: bool,
) -> dict[str, Any]:
    picked = _pick_fields(fields, max_cols=12)
    if not picked:
        return {"error": "Нет полей для выборки"}
    select_parts = [_field_expr(f) for f in picked]
    date_f = match_role(fields, "date")
    post_f = match_role(fields, "posted")
    where_parts: list[str] = []
    period_str = ""
    if use_period and date_f and y and m:
        start, end, last = _period_bounds(y, m)
        where_parts.append(f"Т.{date_f.name} МЕЖДУ {start} И {end}")
        period_str = f"{y}-{m:02d}-01 .. {y}-{m:02d}-{last:02d}"
    if only_posted and post_f:
        where_parts.append(f"Т.{post_f.name} = ИСТИНА")
    q = f"ВЫБРАТЬ ПЕРВЫЕ {lim} " + ", ".join(select_parts) + f" ИЗ {name} КАК Т"
    if where_parts:
        q += " ГДЕ " + " И ".join(where_parts)
    if date_f:
        q += f" УПОРЯДОЧИТЬ ПО Т.{date_f.name}"
    return {
        "query": q,
        "object": name,
        "kind": "document",
        "mode": "period" if use_period else "list",
        "period": period_str or None,
        "fields": [f.name for f in picked],
    }


def _query_catalog(name: str, fields: list[FieldInfo], lim: int) -> dict[str, Any]:
    picked = _pick_fields(fields, max_cols=10)
    del_f = match_role(fields, "deletion_mark")
    select_parts = [_field_expr(f) for f in picked]
    q = f"ВЫБРАТЬ ПЕРВЫЕ {lim} " + ", ".join(select_parts) + f" ИЗ {name} КАК Т"
    if del_f:
        q += f" ГДЕ Т.{del_f.name} = ЛОЖЬ"
    name_f = match_role(fields, "description") or match_role(fields, "code")
    if name_f:
        q += f" УПОРЯДОЧИТЬ ПО Т.{name_f.name}"
    return {
        "query": q,
        "object": name,
        "kind": "catalog",
        "mode": "list",
        "fields": [f.name for f in picked],
    }


def _dims_resources(
    fields: list[FieldInfo],
) -> tuple[list[FieldInfo], list[FieldInfo], list[FieldInfo]]:
    dims = [f for f in fields if f.kind == "dimension" and not f.tabular_section]
    resources = [f for f in fields if f.kind == "resource" and not f.tabular_section]
    attrs = [
        f
        for f in fields
        if f.kind in ("attribute", "standard") and not f.tabular_section
    ]
    if not dims and not resources:
        # structure без kind — берём все шапочные
        picked = _pick_fields(fields, max_cols=12)
        return [], [], picked
    return dims, resources, attrs


def _query_register_table(
    name: str,
    fields: list[FieldInfo],
    lim: int,
    y: int,
    m: int,
    use_period: bool,
) -> dict[str, Any]:
    dims, resources, attrs = _dims_resources(fields)
    picked = (dims + resources + attrs)[:12]
    if not picked:
        picked = _pick_fields(fields, max_cols=12)
    select_parts = [_field_expr(f) for f in picked]
    # Период — типовое поле движений регистра
    period_f = next((f for f in fields if f.name.lower() == "период"), None)
    if not period_f:
        period_f = match_role(fields, "date")

    q = f"ВЫБРАТЬ ПЕРВЫЕ {lim} " + ", ".join(select_parts) + f" ИЗ {name} КАК Т"
    period_str = None
    if use_period and period_f and y and m:
        start, end, last = _period_bounds(y, m)
        q += f" ГДЕ Т.{period_f.name} МЕЖДУ {start} И {end}"
        period_str = f"{y}-{m:02d}-01 .. {y}-{m:02d}-{last:02d}"
        q += f" УПОРЯДОЧИТЬ ПО Т.{period_f.name}"
    return {
        "query": q,
        "object": name,
        "kind": object_kind(name),
        "mode": "list",
        "period": period_str,
        "fields": [f.name for f in picked],
    }


def _query_accum_virtual(
    name: str,
    fields: list[FieldInfo],
    lim: int,
    y: int,
    m: int,
    which: str,
) -> dict[str, Any]:
    dims, resources, _attrs = _dims_resources(fields)
    start, end, last = _period_bounds(y, m)
    if which == "balances":
        # Остатки на конец периода
        source = f"{name}.Остатки({end}, )"
        suffix = "Остаток"
        mode = "balances"
    else:
        source = f"{name}.Обороты({start}, {end}, , )"
        suffix = "Оборот"
        mode = "turnovers"

    select_parts: list[str] = []
    for f in dims[:8]:
        select_parts.append(_field_expr(f))
    money_alias = None
    for f in resources[:6]:
        # виртуальная таблица: Ресурс + Остаток/Оборот + ЕСТЬNULL
        alias = f"{f.name}{suffix}"
        select_parts.append(f"ЕСТЬNULL(Т.{alias}, 0) КАК {alias}")
        if money_alias is None:
            nl = f.name.lower()
            if any(k in nl for k in ("сумма", "оплат", "выручк", "стоим", "колич")):
                money_alias = alias
            elif money_alias is None:
                money_alias = alias
    if not select_parts:
        # fallback: все поля structure как list
        return _query_register_table(name, fields, lim, y, m, True)

    q = (
        f"ВЫБРАТЬ РАЗРЕШЕННЫЕ ПЕРВЫЕ {lim} "
        + ", ".join(select_parts)
        + f" ИЗ {source} КАК Т"
    )
    if money_alias:
        q += f" УПОРЯДОЧИТЬ ПО {money_alias} УБЫВ"
    return {
        "query": q,
        "object": name,
        "kind": "accum_register",
        "mode": mode,
        "period": f"{y}-{m:02d}-01 .. {y}-{m:02d}-{last:02d}",
        "fields": [p.split(" КАК ")[-1] for p in select_parts],
        "virtual_table": source,
        "functions_used": [
            "ПРЕДСТАВЛЕНИЕ",
            "ЕСТЬNULL",
            "УПОРЯДОЧИТЬ ПО",
            "РАЗРЕШЕННЫЕ",
            "Остатки" if which == "balances" else "Обороты",
            "ДАТАВРЕМЯ",
        ],
    }


def _query_info_slice(
    name: str,
    fields: list[FieldInfo],
    lim: int,
    y: int,
    m: int,
) -> dict[str, Any]:
    dims, resources, attrs = _dims_resources(fields)
    start, end, last = _period_bounds(y, m)
    source = f"{name}.СрезПоследних({end}, )"
    picked = (dims + resources + attrs)[:12]
    if not picked:
        picked = _pick_fields(fields, max_cols=12)
    select_parts = [_field_expr(f) for f in picked]
    q = f"ВЫБРАТЬ ПЕРВЫЕ {lim} " + ", ".join(select_parts) + f" ИЗ {source} КАК Т"
    return {
        "query": q,
        "object": name,
        "kind": "info_register",
        "mode": "slice",
        "period": f"срез на {y}-{m:02d}-{last:02d}",
        "fields": [f.name for f in picked],
        "virtual_table": source,
    }
