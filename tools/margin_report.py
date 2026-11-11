# -*- coding: utf-8 -*-
"""
Маржа / выручка / себестоимость по менеджеру (или другому разрезу).

Имена регистра и полей — только из find_metadata + structure ЭТОЙ базы.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from tools.query_1c_normalize import normalize_to_1c_query
from tools.report_format import enrich_report_result


def is_margin_question(phrase: str) -> bool:
    pl = (phrase or "").lower().replace("ё", "е")
    if not pl:
        return False
    if any(
        x in pl
        for x in (
            "маржинальн",
            "маржа",
            "рентабельн",
            "валовая прибыл",
            "прибыль продаж",
        )
    ):
        return True
    if "себестоим" in pl and any(
        x in pl for x in ("выручк", "продаж", "менеджер", "рентаб")
    ):
        return True
    if "выручк" in pl and "себестоим" in pl:
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


def build_and_run_margin_report(
    request: str = "",
    *,
    period: str = "",
    group_by: str = "менеджер",
    exclude_returns: bool = True,
    limit: int = 50,
    find_metadata_fn: Callable[..., dict[str, Any]] | None = None,
    structure_fn: Callable[[str], dict[str, Any]] | None = None,
    execute_fn: Callable[[str, int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    1) Найти регистр выручки/себестоимости
    2) dimensions/resources
    3) ВЫБРАТЬ ... Обороты
    """
    req = (request or "").strip()
    year = _year_from_text(period or req)

    reg_name = ""
    candidates: list[str] = []
    if find_metadata_fn:
        # поисковые стемы (RU), не full_name объектов конкретной CF
        for q in (
            "выручка",
            "себестоимость",
            "продажи",
            "маржа",
        ):
            try:
                m = find_metadata_fn(q, "РегистрыНакопления", 8)
            except TypeError:
                m = find_metadata_fn(q)
            for it in (m.get("items") or m.get("results") or []) if isinstance(m, dict) else []:
                fn = it.get("full_name") if isinstance(it, dict) else ""
                if fn and fn.startswith("РегистрНакопления."):
                    candidates.append(fn)
        # prefer name with both revenue words
        for fn in candidates:
            low = fn.lower()
            if "выручк" in low and "себестоим" in low:
                reg_name = fn
                break
            if "выручк" in low or "продаж" in low:
                reg_name = reg_name or fn
        if not reg_name and candidates:
            reg_name = candidates[0]

    if not reg_name:
        return {
            "ok": False,
            "error": "Не найден регистр выручки/себестоимости в этой базе",
            "hint": "find_metadata(стемы: выручка/себестоимость/продажи, class=РегистрыНакопления)",
            "candidates": candidates[:10],
        }

    st = structure_fn(reg_name) if structure_fn else {}
    dims = st.get("dimension_names") or [
        d.get("name")
        for d in (st.get("dimensions") or [])
        if isinstance(d, dict) and d.get("name")
    ]
    res = st.get("resource_names") or [
        r.get("name")
        for r in (st.get("resources") or [])
        if isinstance(r, dict) and r.get("name")
    ]

    gb_key = (group_by or "менеджер").lower().replace("ё", "е")
    dim_pick = None
    for d in dims:
        dl = d.lower()
        if gb_key in dl or any(
            x in dl for x in ("менеджер", "manager", "ответств", "подраздел")
        ):
            if "менеджер" in gb_key and "менеджер" in dl:
                dim_pick = d
                break
            dim_pick = dim_pick or d
    if not dim_pick and dims:
        # fallback: partner analytics often used
        for d in dims:
            if "партнер" in d.lower() or "менеджер" in d.lower():
                dim_pick = d
                break
        dim_pick = dim_pick or dims[0]

    rev = None
    cost = None
    for r in res:
        rl = r.lower()
        if "выруч" in rl and not rev:
            rev = r
        if ("стоим" in rl or "себест" in rl) and "безндс" not in rl and not cost:
            cost = r
        if "стоим" in rl and not cost:
            cost = r
    if not rev and res:
        rev = res[0]

    if not rev or not dim_pick:
        return {
            "ok": False,
            "error": "В structure нет измерения менеджера или ресурса выручки",
            "full_name": reg_name,
            "dimensions": dims,
            "resources": res,
        }

    start = f"ДАТАВРЕМЯ({year},1,1,0,0,0)"
    end = f"ДАТАВРЕМЯ({year},12,31,23,59,59)"
    # past month heuristic
    if re.search(r"прошл\w*\s+месяц|предыдущ\w*\s+месяц", (req + period).lower()):
        # leave year-based; full month parse is heavier
        pass

    # ссылочное измерение → ПРЕДСТАВЛЕНИЕ; ресурсы → ЕСТЬNULL+СУММА; ORDER; опц. ИТОГИ
    dim_expr = f"ПРЕДСТАВЛЕНИЕ(Т.{dim_pick}) КАК {dim_pick}"
    select = [
        dim_expr,
        f"СУММА(ЕСТЬNULL(Т.{rev}Оборот, 0)) КАК {rev}Оборот",
    ]
    if cost:
        select.append(f"СУММА(ЕСТЬNULL(Т.{cost}Оборот, 0)) КАК {cost}Оборот")
        select.append(
            f"СУММА(ЕСТЬNULL(Т.{rev}Оборот, 0)) - СУММА(ЕСТЬNULL(Т.{cost}Оборот, 0)) КАК Маржа"
        )

    where = ""
    # exclude returns if dimension exists
    if exclude_returns and any("хозяйствен" in d.lower() or "операц" in d.lower() for d in dims):
        hop = next(
            (d for d in dims if "хозяйствен" in d.lower() or "операц" in d.lower()),
            None,
        )
        if hop:
            # soft filter: not empty — full enum filter needs metadata
            where = ""  # keep data; note in result

    want_tot = any(
        x in (req + " " + period).lower().replace("ё", "е")
        for x in ("итог", "всего", "общ")
    )
    q = (
        f"ВЫБРАТЬ РАЗРЕШЕННЫЕ ПЕРВЫЕ {max(1, min(int(limit or 50), 200))} "
        + ", ".join(select)
        + f" ИЗ {reg_name}.Обороты({start}, {end}, , ) КАК Т "
        + where
        + f" СГРУППИРОВАТЬ ПО Т.{dim_pick}"
        + f" УПОРЯДОЧИТЬ ПО {rev}Оборот УБЫВ"
    )
    if want_tot:
        tot_parts = [f"СУММА({rev}Оборот)"]
        if cost:
            tot_parts.append(f"СУММА({cost}Оборот)")
            tot_parts.append("СУММА(Маржа)")
        q += " ИТОГИ " + ", ".join(tot_parts) + " ПО ОБЩИЕ"
    norm = normalize_to_1c_query(q)
    if norm.get("query"):
        q = norm["query"]

    out: dict[str, Any] = {
        "ok": True,
        "full_name": reg_name,
        "period": f"{year}",
        "group_by_field": dim_pick,
        "revenue_field": rev,
        "cost_field": cost,
        "query": q,
        "virtual_tables": [f"{reg_name}.Обороты({start}, {end}, , )"],
        "functions_used": [
            "ПРЕДСТАВЛЕНИЕ",
            "ЕСТЬNULL",
            "СУММА",
            "СГРУППИРОВАТЬ ПО",
            "УПОРЯДОЧИТЬ ПО",
            "РАЗРЕШЕННЫЕ",
            "Обороты",
            "ДАТАВРЕМЯ",
            "ИТОГИ" if want_tot else "",
        ],
        "candidates": candidates[:8],
        "note_returns": (
            "Исключение возвратов: отфильтруйте ХозяйственнаяОперация по перечислению "
            "этой базы (значения — из structure/enum), если нужно жёстко."
            if exclude_returns
            else ""
        ),
        "report_mode": "margin_turnovers",
    }

    if execute_fn:
        er = execute_fn(q, int(limit or 50))
        out["execute_result"] = er
        if isinstance(er, dict):
            out["rows"] = er.get("rows")
            out["error"] = er.get("error")
            out["row_count"] = len(er.get("rows") or [])

    return enrich_report_result(
        out,
        request=req,
        full_name=reg_name,
        period=str(year),
        report_mode="margin_turnovers",
    )
