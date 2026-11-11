# -*- coding: utf-8 -*-
"""
Аналитические сценарии 1С (универсально).

Единая точка: analyst_report(request=…).
Использует compare_periods / execute_query / linked_query через callbacks.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from tools.analyst_core import (
    analyst_bullets,
    build_abc,
    build_aging,
    build_anomalies,
    build_assortment_view,
    build_customer_rfm,
    build_reserves_view,
    build_variance_contribution,
    detect_analyst_mode,
    wants_excel_charts,
    wants_excel_file,
)
from tools.report_format import enrich_report_result

# бизнес-блоки аналитика (suppliers = закупки, не sales/customers)
BUSINESS_MODES = frozenset(
    {"assortment", "reserves", "profit", "customers", "suppliers"}
)


def is_analyst_question(phrase: str) -> bool:
    # «закупки по месяцам / выручка помесячно» — обычный отчёт с шагом времени, не аналитический блок
    if re.search(r"по\s+(?:месяц|дн|недел|квартал)\w*|помесячн|понедельн|подневн", (phrase or "").lower()):
        mode = detect_analyst_mode(phrase)
        return mode in ("abc", "variance", "anomaly", "funnel", "plan_fact")
    return detect_analyst_mode(phrase) is not None


def _excel_pack(
    *,
    title: str,
    sheets: list[dict[str, Any]],
    bullets: list[str],
    meta: dict[str, Any],
    filename: str = "",
    do_excel: bool = False,
) -> dict[str, Any]:
    if not do_excel:
        return {}
    try:
        from tools.excel_export import save_analyst_excel

        saved = save_analyst_excel(
            title=title,
            sheets=sheets,
            bullets=bullets,
            meta_extra=meta,
            filename=filename,
        )
        if saved.get("error"):
            return {"excel_error": saved["error"]}
        out = {k: saved[k] for k in saved if k != "error"}
        out["excel"] = True
        return out
    except Exception as e:
        return {"excel_error": str(e)}


def run_analyst_report(
    request: str,
    *,
    compare_fn: Callable[..., dict[str, Any]],
    execute_fn: Callable[[str, int], dict[str, Any]] | None = None,
    linked_fn: Callable[..., dict[str, Any]] | None = None,
    margin_fn: Callable[..., dict[str, Any]] | None = None,
    last_report: dict[str, Any] | None = None,
    mode: str = "",
    to_excel: bool = False,
    filename: str = "",
    full_name: str = "",
    period: str = "",
    group_by: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    """
    Главный сценарий аналитика.
    compare_fn = tools.compare_periods
    """
    raw = (request or "").strip()
    if not raw:
        return {
            "need_clarification": True,
            "question": (
                "Опишите задачу аналитика, например:\n"
                "• ассортимент / что продаётся и что нет\n"
                "• где заморожены деньги\n"
                "• маржа / как заработать больше\n"
                "• клиентская база / VIP / отток\n"
                "• поставщики: кто ценный / слабый (закупки)\n"
                "• ABC, сравнение лет, просрочка, воронка"
            ),
            "about": "analyst",
        }
    pl = raw.lower().replace("ё", "е")
    mode = (mode or detect_analyst_mode(raw) or "dashboard").strip()
    # LLM часто ставит customers на «поставщики» → закупки, не Реализация
    try:
        from tools.analyst_core import is_supplier_analysis_question

        if is_supplier_analysis_question(raw) and mode in (
            "customers",
            "dashboard",
            "abc",
            "",
        ):
            mode = "suppliers"
        elif mode == "customers" and "поставщик" in pl and not any(
            x in pl for x in ("клиент", "покупат", "vip")
        ):
            mode = "suppliers"
    except Exception:
        pass
    excel = to_excel or wants_excel_file(raw)
    charts = wants_excel_charts(raw) or excel

    # --- drill-down from last_report / phrase ---
    if mode == "drill":
        return _drill_down(
            raw,
            last_report=last_report,
            compare_fn=compare_fn,
            execute_fn=execute_fn,
            to_excel=excel,
            filename=filename,
            charts=charts,
            limit=limit,
        )

    # --- aging ---
    if mode == "aging":
        return _aging(
            raw,
            linked_fn=linked_fn,
            compare_fn=compare_fn,
            to_excel=excel,
            filename=filename,
            charts=charts,
            limit=limit,
        )

    # --- 4 бизнес-блока ---
    if mode in BUSINESS_MODES:
        return _run_business_block(
            raw,
            mode=mode,
            compare_fn=compare_fn,
            linked_fn=linked_fn,
            margin_fn=margin_fn,
            period=period,
            group_by=group_by,
            full_name=full_name,
            to_excel=excel,
            filename=filename,
            charts=charts,
            limit=limit,
        )

    # --- base data via compare (sales-like) ---
    # НЕ period=raw («abc по контрагентам» ломает parse_periods)
    per = _years_period(raw, period)
    # для variance/abc: чистый search_query (без abc/вклад) + акцент на реализацию
    sq = raw if not full_name else ""
    if mode in ("variance", "anomaly", "abc", "dashboard", "multi"):
        # убрать аналитический шум из search
        sq = re.sub(
            r"\b(кто\s+дал\s+рост|кто\s+вырос|кто\s+упал|вклад\w*|аномал\w*|"
            r"abc|авс|парето|класс\w*|дашборд|dashboard|график\w*|диаграмм\w*|"
            r"красив\w*|выгруз\w*|excel|xlsx|эксель)\b",
            " ",
            raw,
            flags=re.I,
        )
        sq = re.sub(r"\s+", " ", sq).strip()
        from tools.business_stems import purchase_search, sales_search

        sql = sq.lower().replace("ё", "е")
        # поставщики → закупки; иначе продажи (стемы, не full_name CF)
        if any(x in pl for x in ("поставщик", "закуп", "приобрет")) and not any(
            x in pl for x in ("клиент", "покупат", "продаж", "реализ")
        ):
            if not any(x in sql for x in ("поступлен", "закуп", "приобрет")):
                sq = purchase_search(sq)
            if not sq:
                sq = purchase_search()
        elif not any(
            x in sql for x in ("продаж", "реализ", "поступлен", "выручк", "закуп")
        ):
            # стемы, не full_name CF; ranking отсечёт «ПланПродаж» / обработки
            sq = sales_search(sq)
        elif "реализ" not in sql and any(
            x in pl for x in ("продаж", "контрагент", "клиент")
        ):
            sq = sales_search(sq)
        if not sq:
            sq = sales_search()
    gb = group_by
    if not gb:
        if any(x in pl for x in ("номенклатур", "товар")):
            gb = "номенклатура"
        elif "склад" in pl:
            gb = "склад"
        elif any(x in pl for x in ("контрагент", "клиент", "покупат", "партнер", "партнёр")):
            gb = "контрагент"
        # иначе пусто — compare_periods сам выберет поля из structure
        else:
            gb = ""

    base = compare_fn(
        search_query=sq,
        full_name=full_name or "",
        period=per,
        group_by=gb,
        metrics="sum,count",
        to_excel=False,
    )
    if not isinstance(base, dict):
        return {"error": "compare_periods вернул не dict", "mode": mode}
    if base.get("need_clarification") or base.get("error"):
        base["mode"] = mode
        return base

    rows = [r for r in (base.get("rows") or []) if isinstance(r, dict)]
    # raw un-capped sometimes only in rows — ok
    out: dict[str, Any] = {
        "ok": True,
        "mode": mode,
        "full_name": base.get("full_name"),
        "period_spec": base.get("period_spec") or per,
        "group_by": base.get("group_by") or gb,
        "source_tool": "compare_periods",
    }

    variance = None
    if mode in ("variance", "anomaly", "dashboard", "abc") or (
        base.get("summary_by_period") and len(base.get("summary_by_period") or []) >= 2
    ):
        variance = build_variance_contribution(
            rows,
            group_key=str(base.get("group_by") or gb or ""),
            value_key="Сумма",
        )
        if variance.get("ok"):
            out["variance"] = {
                k: variance[k]
                for k in (
                    "period_base",
                    "period_current",
                    "total_base",
                    "total_current",
                    "delta",
                    "delta_pct",
                    "group_key",
                )
                if k in variance
            }
            out["summary_by_period"] = variance.get("summary_by_period")
            out["top_growth"] = variance.get("top_growth")
            out["top_decline"] = variance.get("top_decline")
            out["contribution"] = variance.get("contribution")
            out["delta"] = variance.get("delta")
            out["delta_pct"] = variance.get("delta_pct")
            out["period_base"] = variance.get("period_base")
            out["period_current"] = variance.get("period_current")
            out["group_key"] = variance.get("group_key")

    if mode == "abc" or (mode == "dashboard" and "abc" in pl):
        # ABC for latest / current period or all
        period_for_abc = ""
        if variance and variance.get("period_current"):
            period_for_abc = str(variance["period_current"])
        abc = build_abc(
            rows,
            group_key=str(base.get("group_by") or gb or ""),
            value_key="Сумма",
            period=period_for_abc if mode == "abc" else period_for_abc,
        )
        out["abc"] = {
            "class_counts": abc.get("class_counts"),
            "class_sums": abc.get("class_sums"),
            "total": abc.get("total"),
            "thresholds": abc.get("thresholds"),
        }
        out["class_counts"] = abc.get("class_counts")
        out["class_sums"] = abc.get("class_sums")
        out["rows"] = abc.get("rows")
        out["count"] = len(out["rows"] or [])
    elif mode == "variance" and variance and variance.get("ok"):
        out["rows"] = variance.get("rows")
        out["count"] = len(out["rows"] or [])
    elif mode == "anomaly" and variance and variance.get("ok"):
        an = build_anomalies(variance, drop_pct=50.0)
        out["anomalies"] = an
        out["rows"] = an.get("rows")
        out["count"] = len(out["rows"] or [])
    elif mode == "multi":
        # rows already interleaved by period; present as-is + variance
        out["rows"] = rows
        out["count"] = len(rows)
        out["note_multi"] = (
            "Двухмерный разрез: период × "
            f"{base.get('group_by') or gb}. "
            "Для «месяц × группа» укажите месяцы в period."
        )
    else:
        # dashboard default
        out["rows"] = rows
        out["count"] = len(rows)
        if variance and variance.get("ok"):
            an = build_anomalies(variance, drop_pct=50.0)
            out["anomalies"] = {
                "drops_n": len(an.get("drops") or []),
                "spikes_n": len(an.get("spikes") or []),
                "gone_n": len(an.get("gone") or []),
                "drops": (an.get("drops") or [])[:5],
                "spikes": (an.get("spikes") or [])[:5],
            }

    # plan-fact / turnover / funnel — best effort extensions
    if mode == "plan_fact":
        out.update(
            _plan_fact_note(raw, base=base, compare_fn=compare_fn)
        )
    if mode == "turnover":
        out.update(_turnover_note(raw, base=base, linked_fn=linked_fn))
    if mode == "funnel":
        out.update(_funnel_note(raw, compare_fn=compare_fn, execute_fn=execute_fn))

    bullets = analyst_bullets(out, mode=mode)
    out["analyst_summary"] = bullets
    out["prefer"] = (
        "Ответ аналитику:\n"
        "1) 3–7 буллетов из analyst_summary.\n"
        "2) Таблица rows (без выдумок).\n"
        "3) Если variance — покажи delta и топ роста/падения.\n"
        "4) Если excel path — дай path и download_url из tool result.\n"
        "5) Не hardcode чужих конфигураций."
    )

    sheets: list[dict[str, Any]] = []
    try:
        from tools.excel_export import (
            clean_summary_period_rows,
            summary_sheet_worth_it,
        )

        if summary_sheet_worth_it(out.get("summary_by_period")):
            sheets.append(
                {
                    "name": "Итоги",
                    "title": "Итоги по периодам",
                    "rows": clean_summary_period_rows(out["summary_by_period"]),
                    "chart": "bar" if charts else None,
                    "chart_category": "Период",
                }
            )
    except Exception:
        if out.get("summary_by_period") and len(out.get("summary_by_period") or []) >= 2:
            sheets.append(
                {
                    "name": "Итоги",
                    "title": "Итоги по периодам",
                    "rows": out["summary_by_period"],
                    "chart": "bar" if charts else None,
                }
            )
    if out.get("top_growth"):
        sheets.append(
            {
                "name": "Рост",
                "title": "Топ роста",
                "rows": out["top_growth"],
                "chart": "bar" if charts else None,
            }
        )
    if out.get("top_decline"):
        sheets.append(
            {
                "name": "Падение",
                "title": "Топ падения",
                "rows": out["top_decline"],
                "chart": "bar" if charts else None,
            }
        )
    if mode == "abc" and out.get("rows"):
        sheets.append(
            {
                "name": "ABC",
                "title": "ABC-анализ",
                "rows": out["rows"],
                "chart": "pie" if charts else None,
            }
        )
    elif out.get("contribution"):
        sheets.append(
            {
                "name": "Вклад",
                "title": "Contribution / дельта",
                "rows": out.get("contribution") or out.get("rows"),
                "chart": "bar" if charts else None,
            }
        )
    elif out.get("rows"):
        sheets.append(
            {
                "name": "Данные",
                "title": raw[:80],
                "rows": out["rows"],
                "chart": "bar" if charts else None,
            }
        )

    if excel:
        ex = _excel_pack(
            title=f"Аналитика: {raw[:60]}",
            sheets=sheets,
            bullets=bullets,
            meta={
                "mode": mode,
                "full_name": out.get("full_name"),
                "period": out.get("period_spec"),
            },
            filename=filename or f"analyst_{mode}.xlsx",
            do_excel=True,
        )
        out.update(ex)
        out["note"] = (
            (out.get("note") or "")
            + " Excel со сводкой"
            + (" и графиками." if charts else ".")
        ).strip()

    return enrich_report_result(
        out,
        request=raw,
        full_name=str(out.get("full_name") or ""),
        period=str(out.get("period_spec") or ""),
        report_mode=f"analyst_{mode}",
    )


def _years_period(raw: str, period: str = "") -> str:
    """Компактный period для compare — не вся user-фраза."""
    from tools.period_parse import extract_period_spec

    return extract_period_spec(period or "", raw or "")


def _run_business_block(
    raw: str,
    *,
    mode: str,
    compare_fn: Callable[..., dict[str, Any]],
    linked_fn: Callable[..., dict[str, Any]] | None,
    margin_fn: Callable[..., dict[str, Any]] | None,
    period: str,
    group_by: str,
    full_name: str,
    to_excel: bool,
    filename: str,
    charts: bool,
    limit: int,
) -> dict[str, Any]:
    """assortment | reserves | profit | customers | suppliers."""
    from tools.business_stems import purchase_search, sales_search

    per = _years_period(raw, period)
    titles = {
        "assortment": "1. Ассортимент и спрос",
        "reserves": "2. Скрытые резервы (замороженные деньги)",
        "profit": "3. Маржинальность и рост прибыли",
        "customers": "4. Клиентская аналитика",
        "suppliers": "5. Поставщики (ценность по закупкам)",
    }
    title = titles.get(mode, mode)

    if mode == "assortment":
        sales = compare_fn(
            search_query=sales_search(),
            full_name=full_name or "",
            period=per,
            group_by=group_by or "номенклатура",
            metrics="sum,count",
            to_excel=False,
        )
        if not isinstance(sales, dict) or sales.get("need_clarification") or sales.get("error"):
            if isinstance(sales, dict):
                sales["mode"] = mode
                sales["business_block"] = title
            return sales if isinstance(sales, dict) else {"error": str(sales), "mode": mode}
        stock_rows: list[dict[str, Any]] = []
        if linked_fn:
            st = linked_fn(
                request="остатки товаров на складах",
                execute=True,
                limit=min(limit, 80),
            )
            if isinstance(st, dict):
                stock_rows = list(
                    st.get("rows")
                    or (st.get("execute_result") or {}).get("rows")
                    or []
                )
        view = build_assortment_view(
            [r for r in (sales.get("rows") or []) if isinstance(r, dict)],
            stock_rows=stock_rows,
            group_key=str(sales.get("group_by") or "Номенклатура"),
        )
        out: dict[str, Any] = {
            "ok": True,
            "mode": mode,
            "business_block": title,
            "full_name": sales.get("full_name"),
            "period_spec": sales.get("period_spec") or per,
            "group_key": view.get("group_key"),
            "top_sellers": view.get("top_sellers"),
            "weak_sellers": view.get("weak_sellers"),
            "dead_stock": view.get("dead_stock"),
            "abc": view.get("abc"),
            "count_skus": view.get("count_skus"),
            "total_sales": view.get("total_sales"),
            "rows": view.get("top_sellers") or [],
            "summary_by_period": sales.get("summary_by_period"),
            "prefer": (
                "Блок АССОРТИМЕНТ: 1) analyst_summary 2) top_sellers "
                "3) weak_sellers 4) dead_stock (если есть) 5) ABC."
            ),
        }
        bullets = analyst_bullets(out, mode=mode)
        bullets.insert(0, title)
        if view.get("dead_stock"):
            bullets.append(
                "Рекомендация: распродажа/возврат поставщику/стоп-закупка неликвидов."
            )
        if view.get("top_sellers"):
            bullets.append("Рекомендация: не допускать out-of-stock по хитам.")
        out["analyst_summary"] = bullets
        if to_excel:
            sheets = [
                {
                    "name": "Хиты",
                    "title": "Топ продаж",
                    "rows": out.get("top_sellers") or [],
                    "chart": "bar" if charts else None,
                },
                {
                    "name": "Слабый спрос",
                    "rows": out.get("weak_sellers") or [],
                    "chart": "bar" if charts else None,
                },
            ]
            if out.get("dead_stock"):
                sheets.append(
                    {
                        "name": "Неликвиды",
                        "rows": out["dead_stock"],
                        "chart": "bar" if charts else None,
                    }
                )
            out.update(
                _excel_pack(
                    title=title,
                    sheets=sheets,
                    bullets=bullets,
                    meta={"mode": mode, "period": per},
                    filename=filename or "assortment.xlsx",
                    do_excel=True,
                )
            )
        return enrich_report_result(
            out, request=raw, full_name=str(out.get("full_name") or ""),
            period=per, report_mode=f"analyst_{mode}",
        )

    if mode == "reserves":
        stock_rows = []
        ar_rows = []
        aging = None
        stock_total = None
        if linked_fn:
            st = linked_fn(
                request="остатки товаров на складах",
                execute=True,
                limit=min(limit, 60),
            )
            if isinstance(st, dict):
                stock_rows = list(
                    st.get("rows")
                    or (st.get("execute_result") or {}).get("rows")
                    or []
                )
            ar = linked_fn(
                request=f"задолженность клиентов на конец {per}",
                execute=True,
                limit=min(limit, 60),
            )
            if isinstance(ar, dict):
                ar_rows = list(
                    ar.get("rows")
                    or (ar.get("execute_result") or {}).get("rows")
                    or []
                )
            aging = build_aging(ar_rows) if ar_rows else None
        # sales for turnover
        sales = compare_fn(
            search_query=sales_search(),
            period=per,
            group_by="",
            metrics="sum,count",
            to_excel=False,
        )
        sales_total = 0.0
        if isinstance(sales, dict):
            for p in sales.get("summary_by_period") or sales.get("rows") or []:
                if isinstance(p, dict):
                    try:
                        sales_total += float(p.get("Сумма") or 0)
                    except (TypeError, ValueError):
                        pass
        # stock sum rough
        st_sum = 0.0
        for r in stock_rows:
            if not isinstance(r, dict):
                continue
            for k, v in r.items():
                if any(x in str(k).lower() for x in ("сумм", "остат", "стоим")):
                    try:
                        st_sum += abs(float(v or 0))
                    except (TypeError, ValueError):
                        pass
                    break
        stock_total = st_sum or None
        turns = (sales_total / stock_total) if stock_total and sales_total else None
        doh = (stock_total / sales_total * 365) if stock_total and sales_total else None
        turnover = {
            "stock_total_approx": stock_total,
            "sales_total": round(sales_total, 2),
            "turns": round(turns, 2) if turns else None,
            "days_on_hand": round(doh, 1) if doh else None,
        }
        view = build_reserves_view(
            stock_total=stock_total,
            stock_rows=stock_rows[:20],
            ar_rows=ar_rows[:20],
            aging=aging if isinstance(aging, dict) else None,
            turnover=turnover,
        )
        out = {
            "ok": True,
            "mode": mode,
            "business_block": title,
            "period_spec": per,
            **{k: view[k] for k in view if k != "ok"},
            "prefer": (
                "Блок РЕЗЕРВЫ: analyst_summary + parts (где деньги) + "
                "aging_summary + advice. Не выдумывай суммы вне tool."
            ),
        }
        bullets = [title] + analyst_bullets(out, mode=mode)
        out["analyst_summary"] = bullets
        if to_excel:
            sheets = [
                {
                    "name": "Заморозка",
                    "title": "Где деньги",
                    "rows": view.get("parts") or [],
                    "chart": "pie" if charts else None,
                }
            ]
            if view.get("aging_summary"):
                sheets.append(
                    {
                        "name": "Просрочка",
                        "rows": view["aging_summary"],
                        "chart": "bar" if charts else None,
                    }
                )
            if stock_rows:
                sheets.append({"name": "Склад", "rows": stock_rows, "chart": None})
            if ar_rows:
                sheets.append({"name": "Дебиторка", "rows": ar_rows, "chart": None})
            out.update(
                _excel_pack(
                    title=title,
                    sheets=sheets,
                    bullets=bullets,
                    meta={"mode": mode, "period": per},
                    filename=filename or "reserves.xlsx",
                    do_excel=True,
                )
            )
        return enrich_report_result(
            out, request=raw, period=per, report_mode=f"analyst_{mode}"
        )

    if mode == "profit":
        # margin + sales variance by counterparty or nomenclature
        margin = None
        if margin_fn:
            try:
                margin = margin_fn(
                    request=raw or "маржа",
                    period=per,
                    group_by=group_by or "менеджер",
                    limit=min(limit, 40),
                )
            except TypeError:
                margin = margin_fn(request=raw, period=per, limit=min(limit, 40))
        sales = compare_fn(
            search_query=sales_search(),
            full_name=full_name or "",
            period=per,
            group_by=group_by or "контрагент",
            metrics="sum,count",
            to_excel=False,
        )
        out = {
            "ok": True,
            "mode": mode,
            "business_block": title,
            "period_spec": per,
            "full_name": (sales or {}).get("full_name") if isinstance(sales, dict) else None,
            "prefer": (
                "Блок ПРИБЫЛЬ: 1) analyst_summary 2) margin rows 3) top_growth "
                "4) рекомендации по марже. Excel — path."
            ),
        }
        margin_rows = []
        if isinstance(margin, dict) and not margin.get("error"):
            margin_rows = [
                r
                for r in (margin.get("rows") or margin.get("data") or [])
                if isinstance(r, dict)
            ]
            out["margin"] = {
                "full_name": margin.get("full_name") or margin.get("register"),
                "group_by": margin.get("group_by"),
                "row_count": len(margin_rows),
            }
            out["margin_highlights"] = margin_rows[:12]
            out["rows"] = margin_rows[:40]
            out["rows_total"] = len(margin_rows)
        if isinstance(sales, dict) and sales.get("ok"):
            rows = [r for r in (sales.get("rows") or []) if isinstance(r, dict)]
            out["summary_by_period"] = sales.get("summary_by_period")
            if len(sales.get("summary_by_period") or []) >= 2 or len(
                {str(r.get("Период")) for r in rows if r.get("Период")}
            ) >= 2:
                var = build_variance_contribution(
                    rows, group_key=str(sales.get("group_by") or "Контрагент")
                )
                if var.get("ok"):
                    out["variance"] = {
                        k: var[k]
                        for k in (
                            "period_base",
                            "period_current",
                            "total_base",
                            "total_current",
                            "delta",
                            "delta_pct",
                        )
                        if k in var
                    }
                    out["top_growth"] = var.get("top_growth")
                    out["top_decline"] = var.get("top_decline")
                    out["delta"] = var.get("delta")
                    out["delta_pct"] = var.get("delta_pct")
            if not out.get("rows"):
                out["rows"] = rows
        bullets = [title] + analyst_bullets(out, mode=mode)
        bullets.append(
            "Рекомендации: сдвиг продаж в высокомаржинальные позиции; "
            "пересмотр скидок на низкую маржу; рост доли VIP."
        )
        if not margin_rows:
            bullets.append(
                "Маржа: регистр выручки/себестоимости не найден или пуст — "
                "уточните объект/период; продажи для динамики выше."
            )
        out["analyst_summary"] = bullets
        if isinstance(margin, dict) and margin.get("error"):
            out["margin_error"] = margin.get("error")
        if to_excel:
            sheets = []
            if margin_rows:
                sheets.append(
                    {
                        "name": "Маржа",
                        "title": "Маржинальность",
                        "rows": margin_rows[:50],
                        "chart": "bar" if charts else None,
                    }
                )
            if out.get("top_growth"):
                sheets.append(
                    {
                        "name": "Рост продаж",
                        "rows": out["top_growth"],
                        "chart": "bar" if charts else None,
                    }
                )
            if out.get("top_decline"):
                sheets.append(
                    {
                        "name": "Падение",
                        "rows": out["top_decline"],
                        "chart": "bar" if charts else None,
                    }
                )
            if not sheets and out.get("rows"):
                sheets.append(
                    {
                        "name": "Данные",
                        "rows": out["rows"][:50],
                        "chart": "bar" if charts else None,
                    }
                )
            out.update(
                _excel_pack(
                    title=title,
                    sheets=sheets,
                    bullets=bullets,
                    meta={"mode": mode, "period": per},
                    filename=filename or "profit_margin.xlsx",
                    do_excel=True,
                )
            )
        return enrich_report_result(
            out,
            request=raw,
            full_name=str(out.get("full_name") or ""),
            period=per,
            report_mode=f"analyst_{mode}",
        )

    if mode == "customers":
        # период: явные годы из фразы; иначе — «за все время» (не 2024–2025)
        years = re.findall(r"\b((?:19|20)\d{2})\b", raw + " " + per)
        from tools.period_parse import default_period, is_all_time_phrase

        if is_all_time_phrase(per) or not (per or "").strip():
            per = default_period()
        elif len(set(years)) == 1:
            # один год в фразе — для VIP/оттока соседний (только если период задан годом)
            y = int(years[0])
            per = f"{y - 1} и {y}"
        elif len(set(years)) >= 2:
            per = " и ".join(dict.fromkeys(years))
        else:
            per = default_period()
        sales = compare_fn(
            search_query=sales_search(),
            full_name=full_name or "",
            period=per,
            group_by=group_by or "контрагент",
            metrics="sum,count",
            to_excel=False,
        )
        if not isinstance(sales, dict) or sales.get("need_clarification") or sales.get("error"):
            if isinstance(sales, dict):
                sales["mode"] = mode
                sales["business_block"] = title
            return sales if isinstance(sales, dict) else {"error": str(sales)}
        rows = [r for r in (sales.get("rows") or []) if isinstance(r, dict)]
        rfm = build_customer_rfm(
            rows, group_key=str(sales.get("group_by") or "Контрагент")
        )
        out = {
            "ok": True,
            "mode": mode,
            "business_block": title,
            "full_name": sales.get("full_name"),
            "period_spec": sales.get("period_spec") or per,
            "group_key": rfm.get("group_key"),
            "vip": rfm.get("vip"),
            "lost": rfm.get("lost"),
            "new_customers": rfm.get("new_customers"),
            "at_risk": rfm.get("at_risk"),
            "customers_total": rfm.get("customers_total"),
            "summary_by_period": sales.get("summary_by_period"),
            "rows": rfm.get("rows") or rfm.get("vip") or [],
            "prefer": (
                "Блок КЛИЕНТЫ: analyst_summary, VIP, lost, at_risk, new_customers. "
                "Предложи действия: реактивация lost, удержание at_risk."
            ),
        }
        # also variance tops
        var = build_variance_contribution(
            rows, group_key=str(sales.get("group_by") or "Контрагент")
        )
        if var.get("ok"):
            out["top_growth"] = var.get("top_growth")
            out["top_decline"] = var.get("top_decline")
            out["delta"] = var.get("delta")
            out["delta_pct"] = var.get("delta_pct")
        bullets = [title] + analyst_bullets(out, mode=mode)
        if rfm.get("lost"):
            bullets.append(
                "Действие: обзвон/КП «ушедшим» — были продажи в прошлом периоде."
            )
        if rfm.get("at_risk"):
            bullets.append("Действие: персональные условия для at_risk (−40%+).")
        if rfm.get("vip"):
            bullets.append("Действие: VIP — приоритет сервиса и наличие ассортимента.")
        out["analyst_summary"] = bullets
        if to_excel:
            sheets = [
                {
                    "name": "VIP",
                    "title": "VIP клиенты",
                    "rows": rfm.get("vip") or [],
                    "chart": "bar" if charts else None,
                },
                {
                    "name": "Ушли",
                    "rows": rfm.get("lost") or [],
                    "chart": "bar" if charts else None,
                },
                {
                    "name": "Риск",
                    "rows": rfm.get("at_risk") or [],
                    "chart": "bar" if charts else None,
                },
                {
                    "name": "Новые",
                    "rows": rfm.get("new_customers") or [],
                    "chart": None,
                },
            ]
            out.update(
                _excel_pack(
                    title=title,
                    sheets=sheets,
                    bullets=bullets,
                    meta={"mode": mode, "period": per},
                    filename=filename or "customers.xlsx",
                    do_excel=True,
                )
            )
        return enrich_report_result(
            out,
            request=raw,
            full_name=str(out.get("full_name") or ""),
            period=per,
            report_mode=f"analyst_{mode}",
        )

    if mode == "suppliers":
        # Закупки: стемы purchase_search → full_name из metadata ТЕКУЩЕЙ CF
        # без периода в фразе → «за все время»; full_name только из metadata
        years = re.findall(r"\b((?:19|20)\d{2})\b", raw + " " + per)
        from tools.period_parse import default_period, is_all_time_phrase

        if is_all_time_phrase(per) or not (per or "").strip():
            per = default_period()
        elif len(set(years)) == 1:
            y = int(years[0])
            per = f"{y - 1} и {y}"
        elif len(set(years)) >= 2:
            per = " и ".join(dict.fromkeys(years))
        else:
            per = default_period()
        purch = compare_fn(
            search_query=purchase_search(),
            full_name=full_name or "",
            period=per,
            group_by=group_by or "контрагент",
            metrics="sum,count",
            to_excel=False,
        )
        if not isinstance(purch, dict) or purch.get("need_clarification") or purch.get(
            "error"
        ):
            if isinstance(purch, dict):
                purch["mode"] = mode
                purch["business_block"] = title
                purch["hint"] = (
                    purch.get("hint")
                    or "Нужен документ/регистр закупок из metadata (стемы: поступление/закупка). "
                    "Не продажи (Реализация)."
                )
            return purch if isinstance(purch, dict) else {"error": str(purch)}
        rows = [r for r in (purch.get("rows") or []) if isinstance(r, dict)]
        gk = str(purch.get("group_by") or "Контрагент")
        rfm = build_customer_rfm(rows, group_key=gk)
        abc = build_abc(rows, group_key=gk)
        # слабые = класс C / хвост ABC (не hardcode имён)
        vip = list(rfm.get("vip") or [])
        weak: list[dict[str, Any]] = []
        if isinstance(abc, dict) and abc.get("rows"):
            for r in abc["rows"]:
                if not isinstance(r, dict):
                    continue
                if str(r.get("Класс") or r.get("class") or "") == "C":
                    weak.append(r)
            weak = weak[:20]
        if not weak and isinstance(abc, dict) and abc.get("rows"):
            weak = list(reversed(list(abc["rows"])))[:15]
        # переименовать сегменты для поставщиков
        for v in vip:
            if isinstance(v, dict) and v.get("Сегмент") == "VIP":
                v["Сегмент"] = "ценный (A/топ)"
        lost = list(rfm.get("lost") or [])
        for x in lost:
            if isinstance(x, dict):
                x["Сегмент"] = "не закупали в тек. периоде"
        out = {
            "ok": True,
            "mode": mode,
            "business_block": title,
            "full_name": purch.get("full_name"),
            "period_spec": purch.get("period_spec") or per,
            "group_key": rfm.get("group_key") or gk,
            "valuable_suppliers": vip,
            "weak_suppliers": weak,
            "lost_suppliers": lost,
            "at_risk": rfm.get("at_risk"),
            "new_suppliers": rfm.get("new_customers"),
            "suppliers_total": rfm.get("customers_total"),
            "abc": abc.get("rows") if isinstance(abc, dict) else None,
            "class_counts": abc.get("class_counts") if isinstance(abc, dict) else None,
            "class_sums": abc.get("class_sums") if isinstance(abc, dict) else None,
            "summary_by_period": purch.get("summary_by_period"),
            "rows": vip[:10] + (weak[:8] if weak else []),
            "prefer": (
                "Блок ПОСТАВЩИКИ (закупки, не продажи): "
                "1) valuable_suppliers (ценные) 2) weak_suppliers (мелкие/C) "
                "3) lost_suppliers 4) ABC. "
                "full_name — объект закупок из metadata ЭТОЙ CF. "
                "НЕ клиентская аналитика и НЕ документы продаж. "
                "full_name — из metadata (purchase stems). "
                "Имена поставщиков — только из rows tool, не выдумывать."
            ),
        }
        var = build_variance_contribution(rows, group_key=gk)
        if var.get("ok"):
            out["top_growth"] = var.get("top_growth")
            out["top_decline"] = var.get("top_decline")
            out["delta"] = var.get("delta")
            out["delta_pct"] = var.get("delta_pct")
        bullets = [title] + analyst_bullets(out, mode=mode)
        if vip:
            bullets.append(
                "Ценные: держать условия/сроки; не рвать отношения без альтернативы."
            )
        if weak:
            bullets.append(
                "Слабые по объёму: консолидация закупок или точечный поиск дешевле."
            )
        if lost:
            bullets.append(
                "Были закупки ранее, в текущем периоде нет — проверить смену поставщика."
            )
        out["analyst_summary"] = bullets
        if to_excel:
            sheets = [
                {
                    "name": "Ценные",
                    "title": "Ценные поставщики",
                    "rows": vip,
                    "chart": "bar" if charts else None,
                },
                {
                    "name": "Слабые",
                    "rows": weak,
                    "chart": "bar" if charts else None,
                },
                {
                    "name": "ABC",
                    "rows": (abc.get("rows") if isinstance(abc, dict) else None) or [],
                    "chart": "pie" if charts else None,
                },
                {
                    "name": "Не закупали",
                    "rows": lost,
                    "chart": None,
                },
            ]
            out.update(
                _excel_pack(
                    title=title,
                    sheets=sheets,
                    bullets=bullets,
                    meta={"mode": mode, "period": per},
                    filename=filename or "suppliers.xlsx",
                    do_excel=True,
                )
            )
        return enrich_report_result(
            out,
            request=raw,
            full_name=str(out.get("full_name") or ""),
            period=per,
            report_mode=f"analyst_{mode}",
        )

    return {"error": f"unknown business mode {mode}", "mode": mode}


def enrich_compare_with_analytics(result: dict[str, Any]) -> dict[str, Any]:
    """Добавить variance/contribution/bullets к compare_periods."""
    if not isinstance(result, dict) or not result.get("ok"):
        return result
    rows = result.get("rows")
    if not isinstance(rows, list) or len(rows) < 2:
        # всё равно bullets если variance уже посчитан снаружи
        if result.get("variance") or result.get("top_growth"):
            bullets = analyst_bullets(result, mode="variance")
            result["analyst_summary"] = bullets
            pref = (result.get("prefer") or "").strip()
            result["prefer"] = (
                "Сначала analyst_summary, summary_by_period, "
                "top_growth/top_decline (Статус: рост/новый/падение/ушёл), rows.\n"
                + pref
            )
        return result

    # если top_growth уже из полных rows_out — не перетирать урезанным rows
    already = bool(result.get("top_growth") and result.get("variance"))
    if not already:
        n_per = 0
        if isinstance(result.get("summary_by_period"), list):
            n_per = len(result["summary_by_period"])
        else:
            labs = {
                str(r.get("Период"))
                for r in rows
                if isinstance(r, dict) and r.get("Период")
            }
            n_per = len(labs)
        if n_per < 2:
            return result
        var = build_variance_contribution(
            rows,
            group_key=str(result.get("group_by") or ""),
            value_key="Сумма",
        )
        if not var.get("ok"):
            return result
        sbp = result.get("summary_by_period")
        if isinstance(sbp, list) and len(sbp) >= 2:
            by_lab = {
                str(p.get("Период")): float(p.get("Сумма") or 0)
                for p in sbp
                if isinstance(p, dict) and p.get("Период") is not None
            }
            a, b = var.get("period_base"), var.get("period_current")
            if a in by_lab and b in by_lab:
                var["total_base"] = by_lab[a]
                var["total_current"] = by_lab[b]
                var["delta"] = round(by_lab[b] - by_lab[a], 2)
                var["delta_pct"] = (
                    round((by_lab[b] - by_lab[a]) / by_lab[a] * 100.0, 1)
                    if by_lab[a]
                    else None
                )
                var["summary_by_period"] = sbp
        result["variance"] = {
            "period_base": var.get("period_base"),
            "period_current": var.get("period_current"),
            "total_base": var.get("total_base"),
            "total_current": var.get("total_current"),
            "delta": var.get("delta"),
            "delta_pct": var.get("delta_pct"),
        }
        result["top_growth"] = var.get("top_growth")
        result["top_decline"] = var.get("top_decline")
        result["contribution"] = (var.get("contribution") or [])[:15]
        result["delta"] = var.get("delta")
        result["delta_pct"] = var.get("delta_pct")

    bullets = analyst_bullets(result, mode="variance")
    result["analyst_summary"] = bullets
    pref = (result.get("prefer") or "").strip()
    result["prefer"] = (
        "Сначала analyst_summary (выводы), затем summary_by_period, "
        "затем top_growth/top_decline (колонка Статус: рост / новый / падение / ушёл), "
        "затем detail rows. Дельта_% пуста если база=0 (новый клиент).\n"
        + pref
    )
    return result


def _drill_down(
    raw: str,
    *,
    last_report: dict[str, Any] | None,
    compare_fn: Callable[..., dict[str, Any]],
    execute_fn: Callable[[str, int], dict[str, Any]] | None,
    to_excel: bool,
    filename: str,
    charts: bool,
    limit: int,
) -> dict[str, Any]:
    # extract entity name
    name = ""
    m = re.search(
        r"(?:только|по|для|контрагент[аеу]?)\s+[«\"]?([A-Za-zА-Яа-яЁё0-9 ._\-]{2,60})",
        raw,
        re.I,
    )
    if m:
        name = m.group(1).strip(" «»\"'")
    # filter last report first
    if last_report and last_report.get("rows") and name:
        from tools.dialog_context import try_local_filter

        # try_local_filter expects «только X»
        local = try_local_filter(f"только {name}", last_report)
        if local and local.get("ok"):
            local["mode"] = "drill"
            local["drill_from"] = "last_report_filter"
            bullets = analyst_bullets(local, mode="drill")
            local["analyst_summary"] = bullets or [
                f"Фильтр «{name}» по предыдущему отчёту"
            ]
            if to_excel:
                ex = _excel_pack(
                    title=f"Drill-down {name}",
                    sheets=[
                        {
                            "name": "Данные",
                            "rows": local.get("rows") or [],
                            "chart": "bar" if charts else None,
                            "title": name,
                        }
                    ],
                    bullets=local["analyst_summary"],
                    meta={"filter": name},
                    filename=filename or f"drill_{name}.xlsx",
                    do_excel=True,
                )
                local.update(ex)
            return enrich_report_result(
                local, request=raw, full_name=str(local.get("full_name") or ""),
                period=str(local.get("period_spec") or ""),
                report_mode="analyst_drill",
            )

    # document list via compare isn't enough — use execute if we have full_name
    fn = (last_report or {}).get("full_name") or ""
    from tools.period_parse import extract_period_spec

    period_spec = extract_period_spec(
        str((last_report or {}).get("period_spec") or ""),
        raw,
    )
    if not fn:
        # resolve via compare search (aggregates) then note
        base = compare_fn(
            search_query=raw,
            period=period_spec,
            group_by=str((last_report or {}).get("group_by") or ""),
            metrics="sum,count",
        )
        return {
            "ok": bool(base.get("ok")),
            "mode": "drill",
            "note": (
                "Для списка документов укажите full_name после compare "
                "или сначала получите отчёт, затем «документы по X»."
            ),
            "rows": base.get("rows") or [],
            "full_name": base.get("full_name"),
            "need_detail_query": True,
        }

    if not execute_fn:
        return {
            "error": "execute_fn недоступен для drill-down документов",
            "mode": "drill",
        }

    # structure-less best-effort query
    # use ПРЕДСТАВЛЕНИЕ for counterparty filter
    lim = max(5, min(int(limit or 50), 100))
    # period years from last report
    years = (last_report or {}).get("periods") or []
    where_period = ""
    if years and len(years) >= 1:
        # between min and max year
        try:
            ys = sorted(int(y) for y in years if str(y).isdigit())
            if ys:
                where_period = (
                    f"Т.Дата МЕЖДУ ДАТАВРЕМЯ({ys[0]},1,1,0,0,0) "
                    f"И ДАТАВРЕМЯ({ys[-1]},12,31,23,59,59)"
                )
        except Exception:
            where_period = ""
    filter_cp = ""
    if name:
        filter_cp = f"ПРЕДСТАВЛЕНИЕ(Т.Контрагент) ПОДОБНО \"%{name}%\""
    where = " И ".join(x for x in (where_period, filter_cp, "Т.Проведен = ИСТИНА") if x)
    q = (
        f"ВЫБРАТЬ ПЕРВЫЕ {lim} "
        f"Т.Номер КАК Номер, Т.Дата КАК Дата, "
        f"ПРЕДСТАВЛЕНИЕ(Т.Контрагент) КАК Контрагент, "
        f"Т.СуммаДокумента КАК Сумма "
        f"ИЗ {fn} КАК Т"
    )
    if where:
        q += " ГДЕ " + where
    q += " УПОРЯДОЧИТЬ ПО Т.Дата УБЫВ"
    res = execute_fn(q, lim)
    data = []
    if isinstance(res, dict):
        data = res.get("data") or res.get("rows") or []
        if res.get("error"):
            # try without posted / sum field variants
            return {
                "ok": False,
                "mode": "drill",
                "error": res.get("error"),
                "query": q,
                "hint": "Проверьте имена полей через get_object_structure",
                "full_name": fn,
            }
    rows = [r for r in data if isinstance(r, dict)]
    out = {
        "ok": True,
        "mode": "drill",
        "full_name": fn,
        "filter": name,
        "rows": rows,
        "count": len(rows),
        "query": q,
        "analyst_summary": [
            f"Документы {fn}" + (f" по «{name}»" if name else ""),
            f"Строк: {len(rows)}",
        ],
    }
    if to_excel:
        ex = _excel_pack(
            title=f"Документы {name or fn}",
            sheets=[{"name": "Документы", "rows": rows, "chart": None}],
            bullets=out["analyst_summary"],
            meta={"full_name": fn, "filter": name},
            filename=filename or "drill_docs.xlsx",
            do_excel=True,
        )
        out.update(ex)
    return enrich_report_result(
        out, request=raw, full_name=fn, period=str(period_spec), report_mode="analyst_drill"
    )


def _aging(
    raw: str,
    *,
    linked_fn: Callable[..., dict[str, Any]] | None,
    compare_fn: Callable[..., dict[str, Any]],
    to_excel: bool,
    filename: str,
    charts: bool,
    limit: int,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    source = ""
    if linked_fn:
        lr = linked_fn(request=raw, execute=True, limit=limit or 80)
        if isinstance(lr, dict) and (
            lr.get("rows") or (lr.get("execute_result") or {}).get("rows")
        ):
            rows = lr.get("rows") or (lr.get("execute_result") or {}).get("rows") or []
            source = "linked_query"
            # if no days field — still bucket with 0
            aging = build_aging([r for r in rows if isinstance(r, dict)])
            out = {
                "ok": True,
                "mode": "aging",
                "source_tool": source,
                "full_name": lr.get("full_name") or (lr.get("objects") or [None])[0],
                **{k: aging[k] for k in aging if k != "ok"},
            }
            # if linked returned days already ok
            if not any(
                isinstance(r, dict) and (r.get("Дней") is not None or r.get("ПросроченоДней") is not None)
                for r in rows[:5]
            ):
                out["note"] = (
                    "Корзины построены; поле «дней просрочки» в источнике не найдено — "
                    "часть в 0–30. Уточните регистр взаиморасчётов для точного aging."
                )
            bullets = analyst_bullets(out, mode="aging")
            out["analyst_summary"] = bullets
            if to_excel:
                ex = _excel_pack(
                    title="Просроченная задолженность",
                    sheets=[
                        {
                            "name": "Корзины",
                            "rows": out.get("summary_by_bucket") or [],
                            "chart": "pie" if charts else None,
                            "title": "Сумма по корзинам",
                        },
                        {
                            "name": "Детализация",
                            "rows": out.get("rows") or [],
                            "chart": None,
                        },
                    ],
                    bullets=bullets,
                    meta={"mode": "aging"},
                    filename=filename or "aging.xlsx",
                    do_excel=True,
                )
                out.update(ex)
            return enrich_report_result(
                out, request=raw, report_mode="analyst_aging"
            )

    # fallback: not enough for true aging
    return {
        "ok": False,
        "mode": "aging",
        "need_clarification": True,
        "question": (
            "Для aging нужен остаток взаиморасчётов (linked_query). "
            "Уточните: «просроченная задолженность клиентов на конец <год>»"
        ),
        "about": "aging",
    }


def _plan_fact_note(
    raw: str,
    *,
    base: dict[str, Any],
    compare_fn: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    from tools.business_stems import PLAN_SEARCH

    # try find plan-like object (стемы, не full_name CF)
    plan = compare_fn(
        search_query=PLAN_SEARCH,
        period=_years_period(raw, ""),
        group_by=str(base.get("group_by") or ""),
        metrics="sum,count",
        to_excel=False,
    )
    fact = base
    return {
        "plan_fact": {
            "fact_object": fact.get("full_name"),
            "plan_object": plan.get("full_name") if isinstance(plan, dict) else None,
            "plan_ok": bool(isinstance(plan, dict) and plan.get("ok")),
            "plan_rows_n": len((plan or {}).get("rows") or [])
            if isinstance(plan, dict)
            else 0,
            "fact_summary": fact.get("summary_by_period"),
            "plan_summary": (plan or {}).get("summary_by_period")
            if isinstance(plan, dict)
            else None,
            "note": (
                "План-факт: факт из основного отчёта; план — поиск «план продаж». "
                "Сверьте full_name; при другой CF укажите объект плана явно."
            ),
        }
    }


def _turnover_note(
    raw: str,
    *,
    base: dict[str, Any],
    linked_fn: Callable[..., dict[str, Any]] | None,
) -> dict[str, Any]:
    # rough: need stock + COGS/sales. Best-effort from linked remains.
    stock_total = None
    if linked_fn:
        lr = linked_fn(
            request="остатки товаров на складах",
            execute=True,
            limit=20,
        )
        if isinstance(lr, dict):
            rows = lr.get("rows") or (lr.get("execute_result") or {}).get("rows") or []
            s = 0.0
            for r in rows:
                if isinstance(r, dict):
                    for k, v in r.items():
                        if "остат" in str(k).lower() or "количество" in str(k).lower():
                            try:
                                s += float(v or 0)
                            except (TypeError, ValueError):
                                pass
            stock_total = s if s else None
    sales = 0.0
    for p in base.get("summary_by_period") or []:
        if isinstance(p, dict):
            try:
                sales += float(p.get("Сумма") or 0)
            except (TypeError, ValueError):
                pass
    days = 365.0
    turns = (sales / stock_total) if stock_total else None
    doh = (stock_total / sales * days) if sales and stock_total else None
    return {
        "turnover": {
            "stock_total_approx": stock_total,
            "sales_total": sales,
            "turns": round(turns, 2) if turns else None,
            "days_on_hand": round(doh, 1) if doh else None,
            "note": (
                "Оценка оборачиваемости (грубо). "
                "Точные дни/себестоимость — через регистр накопления остатков/оборотов."
            ),
        }
    }


def _funnel_note(
    raw: str,
    *,
    compare_fn: Callable[..., dict[str, Any]],
    execute_fn: Callable[[str, int], dict[str, Any]] | None,
) -> dict[str, Any]:
    from tools.business_stems import CASH_SEARCH, sales_search

    # стемы поиска — full_name подберёт compare/ranking в ТЕКУЩЕЙ CF
    # (не фиксированные Документ.X)
    stages = []
    for label, sq in (
        ("Заказы/счета", "счет оплат заказ клиент"),
        ("Отгрузки/продажи", sales_search()),
        ("Оплаты", CASH_SEARCH),
    ):
        r = compare_fn(
            search_query=sq,
            period=_years_period(raw, ""),
            group_by="",
            metrics="count,sum",
            to_excel=False,
        )
        cnt = 0
        sm = 0.0
        if isinstance(r, dict) and r.get("ok"):
            for row in r.get("rows") or []:
                if isinstance(row, dict):
                    try:
                        cnt += int(float(row.get("Количество") or 0))
                    except (TypeError, ValueError):
                        pass
                    try:
                        sm += float(row.get("Сумма") or 0)
                    except (TypeError, ValueError):
                        pass
            # summary_by_period
            if not cnt and r.get("summary_by_period"):
                for p in r["summary_by_period"]:
                    if isinstance(p, dict):
                        try:
                            cnt += int(float(p.get("Количество") or 0))
                        except (TypeError, ValueError):
                            pass
                        try:
                            sm += float(p.get("Сумма") or 0)
                        except (TypeError, ValueError):
                            pass
        stages.append(
            {
                "stage": label,
                "full_name": r.get("full_name") if isinstance(r, dict) else None,
                "count": cnt,
                "sum": round(sm, 2),
                "ok": bool(isinstance(r, dict) and r.get("ok")),
            }
        )
    return {
        "funnel": stages,
        "rows": stages,
        "note": (
            "Воронка: объекты найдены search_query по типовым смыслам. "
            "На другой CF имена могут отличаться — проверьте full_name."
        ),
    }
