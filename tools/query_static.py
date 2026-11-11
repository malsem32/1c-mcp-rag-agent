"""Статический разбор текста ВЫБРАТЬ (без execute)."""

from __future__ import annotations

import re
from typing import Any


def _extract_query(text: str) -> str:
    raw = (text or "").strip()
    m = re.search(r"```(?:bsl|1c|sql)?\s*([\s\S]+?)```", raw, re.I)
    if m:
        raw = m.group(1).strip()
    if re.search(r"^\s*(ВЫБРАТЬ|SELECT)\b", raw, re.I | re.M):
        return raw
    return raw if "ВЫБРАТЬ" in raw.upper() or "SELECT" in raw.upper() else ""


def query_static_explain(
    query_text: str = "",
    request: str = "",
) -> dict[str, Any]:
    q = _extract_query(query_text or request or "")
    if not q:
        return {
            "ok": False,
            "error": "Нет текста ВЫБРАТЬ. Передай query_text или вставь запрос во фразу.",
        }
    compact = re.sub(r"\s+", " ", q)

    virt = re.findall(
        r"([\w.]+)\.(Остатки|Обороты|ОстаткиИОбороты|СрезПоследних|СрезПервых|SliceLast|SliceFirst|Balance|Turnovers)\s*\(",
        q,
        re.I,
    )
    tables = re.findall(
        r"(?:ИЗ|FROM|СОЕДИНЕНИЕ|JOIN)\s+([\w.]+)",
        q,
        re.I,
    )
    # virtual table full refs
    virt_full = [f"{t}.{k}" for t, k in virt]
    params = list(dict.fromkeys(re.findall(r"&[\wА-Яа-яЁё]+", q)))
    nested = bool(re.search(r"\(\s*(ВЫБРАТЬ|SELECT)\b", q, re.I))
    top = re.search(r"(?:ПЕРВЫЕ|TOP)\s+(\d+)", q, re.I)
    sel_m = re.search(
        r"(?:ВЫБРАТЬ|SELECT)\s+(?:ПЕРВЫЕ\s+\d+\s+|TOP\s+\d+\s+)?(.*?)(?:\s+ИЗ\s+|\s+FROM\s+|$)",
        compact,
        re.I | re.S,
    )
    select_raw = (sel_m.group(1) if sel_m else "")[:2000]
    star = "*" in select_raw or "РАЗЛИЧНЫЕ" in select_raw.upper()
    fields = [f.strip() for f in select_raw.split(",") if f.strip()][:40]

    return {
        "ok": True,
        "tables": list(dict.fromkeys(tables))[:40],
        "tables_total": len(set(tables)),
        "virtual_tables": virt_full[:20],
        "fields_preview": fields,
        "parameters": params[:40],
        "parameters_total": len(params),
        "has_nested_select": nested,
        "first_n": int(top.group(1)) if top else None,
        "select_star_or_raw": star,
        "prefer": (
            "Карта запроса без execute. Антипассерны — audit_query_code. "
            "Имена таблиц как в тексте, не подставляй другую CF."
        ),
        "note": "READ-ONLY разбор текста. Не путать с execute_query.",
    }
