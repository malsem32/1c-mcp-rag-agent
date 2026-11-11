# -*- coding: utf-8 -*-
"""
Универсальная схема отчёта из structure 1С (любая CF).

Не hardcode Документ.X / «всегда Контрагент+Сумма»:
  dimensions / measures / date — из metadata + field_roles.yaml + имя/синоним.
"""

from __future__ import annotations

from typing import Any


def _norm(s: str) -> str:
    return (s or "").lower().replace("ё", "е").strip()


def fields_from_structure(structure: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Плоский список полей structure (header + dimensions + resources + ТЧ sample)."""
    if not isinstance(structure, dict):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: Any, *, kind: str = "", ts: str = "") -> None:
        if not isinstance(item, dict):
            return
        name = str(item.get("name") or item.get("Name") or "").strip()
        if not name or name in seen:
            return
        seen.add(name)
        out.append(
            {
                "name": name,
                "synonym": str(item.get("synonym") or item.get("Synonym") or ""),
                "type": str(item.get("type") or item.get("Type") or item.get("type_str") or ""),
                "kind": kind or str(item.get("kind") or "attribute"),
                "tabular_section": ts or str(item.get("tabular_section") or ""),
            }
        )

    for key, kind in (
        ("attributes", "attribute"),
        ("standard_attributes", "standard"),
        ("dimensions", "dimension"),
        ("resources", "resource"),
        ("header_fields", "attribute"),
        ("fields", "attribute"),
    ):
        for it in structure.get(key) or []:
            add(it, kind=kind)

    for ts in structure.get("tabular_sections") or []:
        if not isinstance(ts, dict):
            continue
        ts_name = str(ts.get("name") or "")
        for it in ts.get("attributes") or ts.get("fields") or []:
            add(it, kind="tabular", ts=ts_name)

    # attributes_table from get_object_structure slim
    for it in structure.get("attributes_table") or []:
        add(it, kind="attribute")

    return out


def classify_fields(fields: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """
    Разложить поля на:
      date_fields, dimension_fields, measure_fields, other
    """
    dates: list[dict[str, Any]] = []
    dims: list[dict[str, Any]] = []
    measures: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []

    measure_hints = (
        "сумм",
        "колич",
        "остат",
        "цена",
        "стоим",
        "выруч",
        "себестоим",
        "amount",
        "qty",
        "count",
        "вес",
        "объем",
        "объём",
        "час",
        "оборот",
        "приход",
        "расход",
        "дельта",
        "доля",
        "процент",
        "ресурс",
    )
    dim_hints = (
        "контрагент",
        "номенклатур",
        "склад",
        "организац",
        "договор",
        "заказ",
        "менеджер",
        "сотрудник",
        "подраздел",
        "цех",
        "участк",
        "серия",
        "характеристик",
        "партнер",
        "партнёр",
        "поставщик",
        "клиент",
        "проект",
        "статья",
        "вид",
        "тип",
        "групп",
        "категор",
        "ячейк",
        "издели",
        "материал",
    )
    date_hints = ("дата", "period", "период", "время")
    skip = (
        "удален",
        "пометк",
        "ссылка",
        "uid",
        "uuid",
        "version",
        "conducted",  # posted handled separately
    )

    for f in fields:
        name = str(f.get("name") or "")
        syn = str(f.get("synonym") or "")
        typ = str(f.get("type") or "").lower()
        kind = str(f.get("kind") or "")
        blob = _norm(f"{name} {syn} {typ}")
        if any(x in blob for x in skip):
            continue

        is_num = any(
            x in typ
            for x in ("число", "number", "decimal", "quantitative", "int", "float")
        ) or kind == "resource"
        is_date = any(x in typ for x in ("дата", "date", "time")) or any(
            h in blob for h in date_hints
        )
        is_ref = any(
            x in typ for x in ("ссылка", "ref", "catalog", "document", "enum", "перечисл")
        )

        if is_date or any(h in blob for h in date_hints):
            dates.append(f)
        elif kind == "resource" or is_num or any(h in blob for h in measure_hints):
            if not is_ref:
                measures.append(f)
            else:
                dims.append(f)
        elif kind == "dimension" or is_ref or any(h in blob for h in dim_hints):
            dims.append(f)
        elif kind in ("attribute", "standard", "tabular"):
            # строковые шапки — скорее измерение
            if is_num:
                measures.append(f)
            else:
                dims.append(f)
        else:
            other.append(f)

    return {
        "date_fields": dates,
        "dimension_fields": dims,
        "measure_fields": measures,
        "other_fields": other,
    }


def schema_suggestions(structure: dict[str, Any] | None) -> dict[str, Any]:
    """Подсказки для мастера: списки измерений/метрик из structure."""
    fields = fields_from_structure(structure)
    classified = classify_fields(fields)
    dims = classified["dimension_fields"][:25]
    measures = classified["measure_fields"][:20]
    dates = classified["date_fields"][:10]
    return {
        "full_name": str((structure or {}).get("full_name") or ""),
        "dimensions_total": len(classified["dimension_fields"]),
        "measures_total": len(classified["measure_fields"]),
        "dates_total": len(classified["date_fields"]),
        "dimensions": [
            {
                "name": d.get("name"),
                "synonym": d.get("synonym"),
                "label": (d.get("synonym") or d.get("name")),
            }
            for d in dims
        ],
        "measures": [
            {
                "name": m.get("name"),
                "synonym": m.get("synonym"),
                "label": (m.get("synonym") or m.get("name")),
            }
            for m in measures
        ],
        "dates": [
            {
                "name": d.get("name"),
                "synonym": d.get("synonym"),
                "label": (d.get("synonym") or d.get("name")),
            }
            for d in dates
        ],
        "dimension_names": [str(d.get("name")) for d in dims if d.get("name")],
        "measure_names": [str(m.get("name")) for m in measures if m.get("name")],
    }


def format_field_options(items: list[dict[str, Any]], *, max_n: int = 12) -> str:
    if not items:
        return "(поля подставятся из metadata объекта)"
    parts = []
    for it in items[:max_n]:
        lab = it.get("label") or it.get("synonym") or it.get("name")
        name = it.get("name")
        if lab and name and str(lab) != str(name):
            parts.append(f"«{lab}» ({name})")
        else:
            parts.append(f"«{name or lab}»")
    more = f" … ещё {len(items) - max_n}" if len(items) > max_n else ""
    return ", ".join(parts) + more

