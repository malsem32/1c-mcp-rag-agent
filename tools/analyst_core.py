# -*- coding: utf-8 -*-
"""
Чистая аналитика поверх rows (без hardcode CF).

- variance / contribution (два периода)
- ABC
- aging buckets
- anomalies
- multi-dim flatten helpers
- NL bullets (эвристика, без LLM)
"""

from __future__ import annotations

import re
from typing import Any


def _norm(s: str) -> str:
    return (s or "").lower().replace("ё", "е").strip()


def _f(v: Any) -> float:
    try:
        if v is None:
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        return float(str(v).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def detect_group_key(rows: list[dict[str, Any]], preferred: str = "") -> str | None:
    if preferred:
        for r in rows:
            if preferred in r:
                return preferred
    skip = {
        "период",
        "period",
        "датаначала",
        "датаконца",
        "год",
        "месяц",
        "сумма",
        "количество",
        "error",
        "дата",
        "номер",
    }
    if not rows:
        return None
    # частота непустых строковых полей
    scores: dict[str, int] = {}
    for r in rows[:50]:
        if not isinstance(r, dict):
            continue
        for k, v in r.items():
            kl = _norm(str(k))
            if kl in skip or str(k).startswith("_"):
                continue
            if isinstance(v, str) and v.strip():
                scores[str(k)] = scores.get(str(k), 0) + 1
            elif v is not None and not isinstance(v, (int, float, bool)):
                scores[str(k)] = scores.get(str(k), 0) + 1
    if not scores:
        return None
    return max(scores.items(), key=lambda x: x[1])[0]


def detect_value_key(rows: list[dict[str, Any]], preferred: str = "") -> str:
    try:
        from tools.excel_export import infer_axes_from_rows, match_column_name

        if preferred:
            hit = match_column_name(rows, preferred)
            if hit:
                return hit
        _, vk = infer_axes_from_rows(
            rows, preferred_value=preferred or None
        )
        if vk:
            return vk
    except Exception:
        pass
    if preferred:
        return preferred
    for cand in ("Сумма", "Amount", "Остаток", "Выручка", "Количество"):
        for r in rows[:5]:
            if isinstance(r, dict) and cand in r:
                return cand
    for r in rows[:5]:
        if not isinstance(r, dict):
            continue
        for k, v in r.items():
            if "сумм" in _norm(str(k)):
                return str(k)
            try:
                float(v)
                if _norm(str(k)) not in ("год", "месяц", "количество"):
                    return str(k)
            except (TypeError, ValueError):
                pass
    return ""


def periods_in_rows(rows: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        lab = str(r.get("Период") or r.get("period") or "").strip()
        if lab and lab not in seen:
            seen.append(lab)
    return seen


def build_variance_contribution(
    rows: list[dict[str, Any]],
    *,
    period_a: str = "",
    period_b: str = "",
    group_key: str = "",
    value_key: str = "",
    top_n: int = 10,
) -> dict[str, Any]:
    """
    Сравнение двух периодов по группе:
      Δ, Δ%, вклад в общий прирост.
    period_a = база (раньше), period_b = текущий (если не заданы — порядок из rows).
    """
    clean = [r for r in rows if isinstance(r, dict) and not r.get("error")]
    periods = periods_in_rows(clean)
    if len(periods) < 2:
        return {"ok": False, "error": "Нужно ≥2 периода в rows", "periods": periods}
    a = period_a or periods[0]
    # если пользователь сказал 2016 и 2015 — periods[0]=2016. Для contribution:
    # база = более ранний год если оба int
    if not period_a and not period_b and len(periods) >= 2:
        try:
            sorted_p = sorted(periods, key=lambda x: int(re.sub(r"\D", "", x) or 0))
            if len(sorted_p) >= 2 and sorted_p[0] != sorted_p[1]:
                a, b = sorted_p[0], sorted_p[1]
            else:
                a, b = periods[0], periods[1]
        except Exception:
            a, b = periods[0], periods[1]
    else:
        a = period_a or periods[0]
        b = period_b or (periods[1] if len(periods) > 1 else periods[0])

    gk = group_key or detect_group_key(clean) or "Группа"
    vk = value_key or detect_value_key(clean)

    by_a: dict[str, float] = {}
    by_b: dict[str, float] = {}
    for r in clean:
        lab = str(r.get("Период") or "")
        g = str(r.get(gk) if r.get(gk) is not None else r.get("Контрагент") or "—")
        val = _f(r.get(vk))
        if lab == a:
            by_a[g] = by_a.get(g, 0.0) + val
        elif lab == b:
            by_b[g] = by_b.get(g, 0.0) + val

    total_a = sum(by_a.values())
    total_b = sum(by_b.values())
    total_delta = total_b - total_a
    total_pct = (total_delta / total_a * 100.0) if total_a else None

    all_g = sorted(set(by_a) | set(by_b))
    detail: list[dict[str, Any]] = []
    for g in all_g:
        va, vb = by_a.get(g, 0.0), by_b.get(g, 0.0)
        d = vb - va
        # % только если есть база; 0→N не «100%», а «новый»
        pct: float | None
        if va > 0 and vb == 0:
            pct = -100.0
            status = "ушёл"
        elif va > 0:
            pct = d / va * 100.0
            status = "рост" if d > 0 else ("падение" if d < 0 else "без изм.")
        elif vb > 0:
            pct = None
            status = "новый"
        else:
            pct = None
            status = ""
        contrib = (d / total_delta * 100.0) if total_delta else 0.0
        row_d: dict[str, Any] = {
            gk: g,
            f"{a}": round(va, 2),
            f"{b}": round(vb, 2),
            "Дельта": round(d, 2),
            "Дельта_%": round(pct, 1) if pct is not None else None,
            "Вклад_%": round(contrib, 1),
            "Статус": status,
        }
        detail.append(row_d)
    detail.sort(key=lambda x: abs(_f(x.get("Дельта"))), reverse=True)

    # «топ роста»: реальный рост (база>0) + отдельно новые; не путать 0→N с +100%
    growth_real = [
        x
        for x in detail
        if _f(x.get("Дельта")) > 0 and x.get("Статус") in ("рост",)
    ][:top_n]
    growth_new = [x for x in detail if x.get("Статус") == "новый"][:top_n]
    # для совместимости: top_growth = real first, then new (помечены Статус)
    growth = growth_real + [x for x in growth_new if x not in growth_real]
    growth = growth[:top_n]
    decline = [
        x
        for x in detail
        if _f(x.get("Дельта")) < 0 and x.get("Статус") in ("падение", "ушёл")
    ][:top_n]
    # sort decline by most negative
    decline.sort(key=lambda x: _f(x.get("Дельта")))

    summary_by_period = [
        {"Период": a, "Сумма": round(total_a, 2)},
        {"Период": b, "Сумма": round(total_b, 2)},
    ]
    return {
        "ok": True,
        "period_base": a,
        "period_current": b,
        "group_key": gk,
        "value_key": vk,
        "total_base": round(total_a, 2),
        "total_current": round(total_b, 2),
        "delta": round(total_delta, 2),
        "delta_pct": round(total_pct, 1) if total_pct is not None else None,
        "summary_by_period": summary_by_period,
        "contribution": detail[: max(top_n * 2, 20)],
        "top_growth": growth,
        "top_decline": decline,
        "rows": detail[:60],
        "rows_total": len(detail),
    }


def build_abc(
    rows: list[dict[str, Any]],
    *,
    group_key: str = "",
    value_key: str = "",
    period: str = "",
    a_share: float = 80.0,
    b_share: float = 15.0,
) -> dict[str, Any]:
    """ABC по сумме. Если несколько периодов — фильтр period или сумма по всем."""
    clean = [r for r in rows if isinstance(r, dict) and not r.get("error")]
    if period:
        clean = [
            r
            for r in clean
            if str(r.get("Период") or r.get("period") or "") == period
        ]
    gk = group_key or detect_group_key(clean) or "Группа"
    vk = value_key or detect_value_key(clean)

    totals: dict[str, float] = {}
    for r in clean:
        g = str(r.get(gk) if r.get(gk) is not None else "—")
        totals[g] = totals.get(g, 0.0) + _f(r.get(vk))

    ranked = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    grand = sum(v for _, v in ranked) or 1.0
    out_rows: list[dict[str, Any]] = []
    cum = 0.0
    counts = {"A": 0, "B": 0, "C": 0}
    sum_cls = {"A": 0.0, "B": 0.0, "C": 0.0}
    for g, v in ranked:
        cum += v
        share = v / grand * 100.0
        cum_share = cum / grand * 100.0
        if cum_share <= a_share or (counts["A"] == 0 and share >= a_share):
            # borderline: first items until exceed 80%
            cls = "A"
            if cum_share > a_share and counts["A"] > 0 and share < (100 - a_share):
                # re-evaluate: if previous cum without this > a_share already B
                prev_cum = (cum - v) / grand * 100.0
                if prev_cum >= a_share:
                    cls = "B" if prev_cum < a_share + b_share else "C"
        else:
            prev_cum = (cum - v) / grand * 100.0
            if prev_cum < a_share:
                cls = "A"
            elif prev_cum < a_share + b_share:
                cls = "B"
            else:
                cls = "C"
        # simpler classic: assign by cumulative after adding
        prev_cum = (cum - v) / grand * 100.0
        if prev_cum < a_share:
            cls = "A"
        elif prev_cum < a_share + b_share:
            cls = "B"
        else:
            cls = "C"
        counts[cls] = counts.get(cls, 0) + 1
        sum_cls[cls] = sum_cls.get(cls, 0.0) + v
        out_rows.append(
            {
                gk: g,
                "Сумма": round(v, 2),
                "Доля_%": round(share, 2),
                "Накопительно_%": round(cum_share, 2),
                "Класс": cls,
            }
        )

    return {
        "ok": True,
        "group_key": gk,
        "value_key": vk,
        "period": period or None,
        "total": round(grand if grand != 1.0 or ranked else 0.0, 2),
        "class_counts": counts,
        "class_sums": {k: round(v, 2) for k, v in sum_cls.items()},
        "rows": out_rows,
        "thresholds": {"A": a_share, "B": b_share, "C": 100 - a_share - b_share},
    }


def build_aging(
    rows: list[dict[str, Any]],
    *,
    amount_key: str = "",
    days_key: str = "",
    group_key: str = "",
    as_of_label: str = "",
) -> dict[str, Any]:
    """
    Корзины просрочки. Ожидает days (дней долга) или вычисляет из Дата.
    buckets: 0-30, 31-60, 61-90, 90+.
    """
    clean = [r for r in rows if isinstance(r, dict)]
    ak = amount_key or detect_value_key(clean)
    gk = group_key or detect_group_key(clean) or "Контрагент"
    buckets_def = [
        ("0–30", 0, 30),
        ("31–60", 31, 60),
        ("61–90", 61, 90),
        ("90+", 91, 10**9),
    ]
    bucket_sum = {b[0]: 0.0 for b in buckets_def}
    bucket_cnt = {b[0]: 0 for b in buckets_def}
    detail: list[dict[str, Any]] = []

    for r in clean:
        amt = _f(r.get(ak) or r.get("Остаток") or r.get("Сумма"))
        if amt == 0:
            continue
        days = None
        if days_key and r.get(days_key) is not None:
            days = int(_f(r.get(days_key)))
        elif r.get("Дней") is not None:
            days = int(_f(r.get("Дней")))
        elif r.get("ПросроченоДней") is not None:
            days = int(_f(r.get("ПросроченоДней")))
        else:
            days = 0
        label = "90+"
        for name, lo, hi in buckets_def:
            if lo <= days <= hi:
                label = name
                break
        bucket_sum[label] += abs(amt)
        bucket_cnt[label] += 1
        detail.append(
            {
                gk: r.get(gk) or r.get("Контрагент") or "—",
                "Сумма": round(amt, 2),
                "Дней": days,
                "Корзина": label,
            }
        )

    summary = [
        {
            "Корзина": name,
            "Сумма": round(bucket_sum[name], 2),
            "Количество": bucket_cnt[name],
        }
        for name, _, _ in buckets_def
    ]
    detail.sort(key=lambda x: (-_f(x.get("Дней")), -_f(x.get("Сумма"))))
    return {
        "ok": True,
        "as_of": as_of_label or None,
        "summary_by_bucket": summary,
        "total": round(sum(bucket_sum.values()), 2),
        "rows": detail[:100],
        "rows_total": len(detail),
        "group_key": gk,
        "value_key": ak,
    }


def build_anomalies(
    variance: dict[str, Any],
    *,
    drop_pct: float = 50.0,
    min_base: float = 0.0,
) -> dict[str, Any]:
    """Аномалии из variance: падение > drop_pct, рост > drop_pct, исчезнувшие топы."""
    if not variance.get("ok"):
        return {"ok": False, "error": variance.get("error")}
    base = variance.get("period_base")
    rows = variance.get("rows") or variance.get("contribution") or []
    drops = []
    spikes = []
    gone = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        dlt = _f(r.get("Дельта_%"))
        va = _f(r.get(str(base))) if base else 0.0
        # columns may be year labels
        if base and str(base) in r:
            va = _f(r.get(str(base)))
        if va >= min_base and dlt <= -drop_pct:
            drops.append(r)
        if dlt >= drop_pct and _f(r.get("Дельта")) > 0:
            spikes.append(r)
        cur_key = variance.get("period_current")
        if base and cur_key and _f(r.get(str(base))) > 0 and _f(r.get(str(cur_key))) == 0:
            gone.append(r)
    return {
        "ok": True,
        "drop_pct_threshold": drop_pct,
        "drops": drops[:15],
        "spikes": spikes[:15],
        "gone": gone[:15],
        "drops_total": len(drops),
        "spikes_total": len(spikes),
        "gone_total": len(gone),
        "rows": (drops + spikes)[:30],
        "rows_total": len(drops) + len(spikes),
    }


def analyst_bullets(payload: dict[str, Any], *, mode: str = "") -> list[str]:
    """Короткие выводы для чата и листа «Сводка»."""
    bullets: list[str] = []
    mode = mode or str(payload.get("mode") or "")

    sbp = payload.get("summary_by_period")
    if isinstance(sbp, list) and len(sbp) >= 1:
        parts = []
        for p in sbp[:6]:
            if isinstance(p, dict) and p.get("Период") is not None:
                parts.append(f"{p.get('Период')}={p.get('Сумма', p.get('Количество', '?'))}")
        if parts:
            bullets.append("Итого по периодам: " + "; ".join(parts))

    if payload.get("delta") is not None:
        dp = payload.get("delta_pct")
        bullets.append(
            f"Изменение: {payload.get('delta')} "
            + (f"({dp}%)" if dp is not None else "")
            + f" — база {payload.get('period_base')} → {payload.get('period_current')}"
        )
    tg = payload.get("top_growth") or []
    if tg:
        g0 = tg[0]
        gk = payload.get("group_key") or "Группа"
        bullets.append(
            f"Лидеры роста: {g0.get(gk)} Δ={g0.get('Дельта')} "
            f"({g0.get('Дельта_%')}%)"
            + (
                f"; ещё {len(tg)-1}" if len(tg) > 1 else ""
            )
        )
    td = payload.get("top_decline") or []
    if td:
        g0 = td[0]
        gk = payload.get("group_key") or "Группа"
        bullets.append(
            f"Сильнее всего упали: {g0.get(gk)} Δ={g0.get('Дельта')} "
            f"({g0.get('Дельта_%')}%)"
        )

    if payload.get("class_counts"):
        cc = payload["class_counts"]
        cs = payload.get("class_sums") or {}
        bullets.append(
            f"ABC: A={cc.get('A')} ({cs.get('A')}), "
            f"B={cc.get('B')} ({cs.get('B')}), "
            f"C={cc.get('C')} ({cs.get('C')})"
        )

    if payload.get("summary_by_bucket"):
        parts = [
            f"{b.get('Корзина')}={b.get('Сумма')}"
            for b in payload["summary_by_bucket"]
            if isinstance(b, dict)
        ]
        bullets.append("Просрочка по корзинам: " + "; ".join(parts))
        bullets.append(f"Всего в aging: {payload.get('total')}")

    if payload.get("anomalies"):
        an = payload["anomalies"]
        if isinstance(an, dict):
            bullets.append(
                f"Аномалии: падений={len(an.get('drops') or [])}, "
                f"скачков={len(an.get('spikes') or [])}, "
                f"ушли в ноль={len(an.get('gone') or [])}"
            )

    if payload.get("funnel"):
        fn = payload["funnel"]
        if isinstance(fn, list):
            bullets.append(
                "Воронка: "
                + " → ".join(
                    f"{x.get('stage')}={x.get('count')}"
                    for x in fn
                    if isinstance(x, dict)
                )
            )

    if payload.get("turnover"):
        t = payload["turnover"]
        if isinstance(t, dict):
            bullets.append(
                f"Оборачиваемость: дней запаса≈{t.get('days_on_hand')}, "
                f"коэф≈{t.get('turns')}"
            )

    if payload.get("top_sellers"):
        ts = payload["top_sellers"]
        if ts and isinstance(ts[0], dict):
            gk = payload.get("group_key") or "Позиция"
            bullets.append(
                f"Хиты: {ts[0].get(gk)} = {ts[0].get('Сумма')} "
                f"(всего SKU≈{payload.get('count_skus', '?')})"
            )
    if payload.get("dead_stock"):
        bullets.append(
            f"Неликвиды (остаток есть, продаж нет): {len(payload['dead_stock'])} поз."
        )
    if payload.get("total_frozen_approx") is not None:
        bullets.append(f"Оценка «заморозки»: {payload.get('total_frozen_approx')}")
    if payload.get("advice") and isinstance(payload["advice"], list):
        bullets.extend(str(a) for a in payload["advice"][:3])
    if mode == "suppliers" or payload.get("valuable_suppliers") is not None:
        vs = payload.get("valuable_suppliers") or payload.get("vip") or []
        ws = payload.get("weak_suppliers") or []
        if vs:
            bullets.append(f"Ценные поставщики (топ по закупкам): {len(vs)}")
        if ws:
            bullets.append(f"Слабые / мелкие по объёму: {len(ws)}")
        if payload.get("lost_suppliers") or (
            mode == "suppliers" and payload.get("lost")
        ):
            n = len(payload.get("lost_suppliers") or payload.get("lost") or [])
            if n:
                bullets.append(f"Не было закупок в тек. периоде (были ранее): {n}")
    elif payload.get("vip"):
        bullets.append(f"VIP-клиентов в топе: {len(payload['vip'])}")
    if mode != "suppliers":
        if payload.get("lost") and not payload.get("lost_suppliers"):
            bullets.append(
                f"Ушедшие (не покупали в тек. периоде): {len(payload['lost'])}"
            )
        if payload.get("at_risk"):
            bullets.append(f"Риск оттока (падение >40%): {len(payload['at_risk'])}")
    if payload.get("margin_highlights"):
        mh = payload["margin_highlights"]
        if isinstance(mh, list) and mh:
            bullets.append(f"Маржа: смотри top {len(mh)} позиций / разрезов")

    if not bullets and payload.get("manager_summary"):
        ms = payload["manager_summary"]
        if isinstance(ms, dict) and ms.get("message"):
            bullets.append(str(ms["message"])[:200])
    if not bullets:
        bullets.append(f"Режим {mode or 'analyst'}: строк {payload.get('count') or len(payload.get('rows') or [])}")
    return bullets[:12]


def build_assortment_view(
    sales_rows: list[dict[str, Any]],
    *,
    stock_rows: list[dict[str, Any]] | None = None,
    group_key: str = "",
    value_key: str = "",
    stock_key: str = "",
    dead_threshold: float = 0.0,
) -> dict[str, Any]:
    """
    Ассортимент и спрос: топ продаж, слабые позиции, ABC,
    при наличии остатков — «висит на складе / не продаётся».
    """
    clean = [r for r in sales_rows if isinstance(r, dict) and not r.get("error")]
    gk = group_key or detect_group_key(clean) or "Номенклатура"
    vk = value_key or detect_value_key(clean)
    # агрегат по группе
    totals: dict[str, float] = {}
    qty: dict[str, float] = {}
    for r in clean:
        g = str(r.get(gk) if r.get(gk) is not None else "—")
        totals[g] = totals.get(g, 0.0) + _f(r.get(vk))
        q = r.get("Количество")
        if q is not None:
            qty[g] = qty.get(g, 0.0) + _f(q)
    ranked = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    abc = build_abc(clean, group_key=gk, value_key=vk)
    top = [
        {gk: g, "Сумма": round(v, 2), "Количество": qty.get(g), "Статус": "лидер"}
        for g, v in ranked[:15]
        if v > dead_threshold
    ]
    weak = [
        {gk: g, "Сумма": round(v, 2), "Количество": qty.get(g), "Статус": "слабый спрос"}
        for g, v in ranked[-15:]
        if v > dead_threshold
    ]
    weak.reverse()

    stock_map: dict[str, float] = {}
    if stock_rows:
        sk = stock_key
        for r in stock_rows:
            if not isinstance(r, dict):
                continue
            g = str(
                r.get(gk)
                or r.get("Номенклатура")
                or r.get("Товар")
                or "—"
            )
            if not sk:
                for k, v in r.items():
                    kl = _norm(str(k))
                    if any(x in kl for x in ("остат", "количество", "qty", "stock")):
                        stock_map[g] = stock_map.get(g, 0.0) + _f(v)
                        break
            else:
                stock_map[g] = stock_map.get(g, 0.0) + _f(r.get(sk))

    dead: list[dict[str, Any]] = []
    for g, st in sorted(stock_map.items(), key=lambda x: -x[1])[:40]:
        sales = totals.get(g, 0.0)
        if st > 0 and sales <= dead_threshold:
            dead.append(
                {
                    gk: g,
                    "Остаток": round(st, 2),
                    "Продажи": round(sales, 2),
                    "Статус": "не продаётся (остаток есть)",
                }
            )

    return {
        "ok": True,
        "group_key": gk,
        "value_key": vk,
        "top_sellers": top,
        "weak_sellers": weak,
        "dead_stock": dead[:25],
        "dead_stock_total": len(dead),
        "abc": {
            "class_counts": abc.get("class_counts"),
            "class_sums": abc.get("class_sums"),
        },
        "rows": top[:15]
        + [{"_block": "weak"}]
        + weak[:10]
        + ([{"_block": "dead"}] + dead[:10] if dead else []),
        "count_skus": len(ranked),
        "total_sales": round(sum(totals.values()), 2),
    }


def build_customer_rfm(
    rows: list[dict[str, Any]],
    *,
    group_key: str = "",
    value_key: str = "",
    period_current: str = "",
    period_base: str = "",
) -> dict[str, Any]:
    """
    Клиентская аналитика (упрощённый RFM по двум периодам в rows):
      Monetary = сумма, Frequency = количество, «ушли» = были в base, 0 в current.
    """
    clean = [r for r in rows if isinstance(r, dict) and not r.get("error")]
    gk = group_key or detect_group_key(clean) or "Контрагент"
    vk = value_key or detect_value_key(clean)
    periods = periods_in_rows(clean)
    if not period_current and periods:
        # более поздний
        try:
            period_current = sorted(
                periods, key=lambda x: int(re.sub(r"\D", "", x) or 0)
            )[-1]
        except Exception:
            period_current = periods[0]
    if not period_base and len(periods) >= 2:
        try:
            period_base = sorted(
                periods, key=lambda x: int(re.sub(r"\D", "", x) or 0)
            )[0]
        except Exception:
            period_base = periods[0]

    mon: dict[str, float] = {}
    freq: dict[str, float] = {}
    mon_base: dict[str, float] = {}
    mon_cur: dict[str, float] = {}
    for r in clean:
        g = str(r.get(gk) if r.get(gk) is not None else "—")
        lab = str(r.get("Период") or "")
        v = _f(r.get(vk))
        mon[g] = mon.get(g, 0.0) + v
        freq[g] = freq.get(g, 0.0) + _f(r.get("Количество") or 1)
        if period_base and lab == period_base:
            mon_base[g] = mon_base.get(g, 0.0) + v
        if period_current and lab == period_current:
            mon_cur[g] = mon_cur.get(g, 0.0) + v

    # VIP = top monetary current or total
    cur_map = mon_cur if mon_cur else mon
    ranked = sorted(cur_map.items(), key=lambda x: x[1], reverse=True)
    vip = [
        {
            gk: g,
            "Сумма": round(v, 2),
            "Количество": round(freq.get(g, 0), 0),
            "Сегмент": "VIP",
        }
        for g, v in ranked[:15]
    ]
    lost = []
    for g, vb in mon_base.items():
        if vb > 0 and mon_cur.get(g, 0.0) == 0:
            lost.append(
                {
                    gk: g,
                    f"{period_base}": round(vb, 2),
                    f"{period_current or 'сейчас'}": 0,
                    "Сегмент": "ушёл (не покупал)",
                }
            )
    lost.sort(key=lambda x: -_f(x.get(period_base or "Сумма")))
    new_c = []
    for g, vc in mon_cur.items():
        if vc > 0 and mon_base.get(g, 0.0) == 0 and period_base:
            new_c.append(
                {
                    gk: g,
                    f"{period_current}": round(vc, 2),
                    "Сегмент": "новый",
                }
            )
    new_c.sort(key=lambda x: -_f(x.get(period_current or "Сумма")))
    # at risk: drop > 40%
    risk = []
    for g in set(mon_base) | set(mon_cur):
        vb, vc = mon_base.get(g, 0.0), mon_cur.get(g, 0.0)
        if vb > 0 and vc > 0 and (vc - vb) / vb <= -0.4:
            risk.append(
                {
                    gk: g,
                    f"{period_base}": round(vb, 2),
                    f"{period_current}": round(vc, 2),
                    "Дельта_%": round((vc - vb) / vb * 100, 1),
                    "Сегмент": "риск оттока",
                }
            )
    risk.sort(key=lambda x: _f(x.get("Дельта_%")))

    return {
        "ok": True,
        "group_key": gk,
        "period_base": period_base,
        "period_current": period_current,
        "vip": vip,
        "lost": lost[:20],
        "new_customers": new_c[:20],
        "at_risk": risk[:20],
        "lost_total": len(lost),
        "new_customers_total": len(new_c),
        "at_risk_total": len(risk),
        "customers_total": len(set(mon)),
        "rows": vip[:10]
        + (lost[:8] if lost else [])
        + (risk[:8] if risk else [])
        + (new_c[:5] if new_c else []),
    }


def build_reserves_view(
    *,
    stock_total: float | None = None,
    stock_rows: list[dict[str, Any]] | None = None,
    ar_total: float | None = None,
    ar_rows: list[dict[str, Any]] | None = None,
    aging: dict[str, Any] | None = None,
    turnover: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Где заморожены деньги: склад + ДЗ + просрочка + оборачиваемость."""
    frozen_stock = stock_total
    if frozen_stock is None and stock_rows:
        s = 0.0
        for r in stock_rows:
            if not isinstance(r, dict):
                continue
            for k, v in r.items():
                if any(
                    x in _norm(str(k))
                    for x in ("сумм", "остат", "стоим", "amount")
                ):
                    s += abs(_f(v))
                    break
        frozen_stock = s
    frozen_ar = ar_total
    if frozen_ar is None and aging and aging.get("total") is not None:
        frozen_ar = _f(aging.get("total"))
    if frozen_ar is None and ar_rows:
        s = 0.0
        for r in ar_rows:
            if not isinstance(r, dict):
                continue
            for k, v in r.items():
                if any(x in _norm(str(k)) for x in ("сумм", "остат", "долг", "amount")):
                    s += abs(_f(v))
                    break
        frozen_ar = s

    overdue = None
    if aging and isinstance(aging.get("summary_by_bucket"), list):
        overdue = sum(
            _f(b.get("Сумма"))
            for b in aging["summary_by_bucket"]
            if isinstance(b, dict) and str(b.get("Корзина") or "") not in ("0–30", "0-30")
        )

    parts = []
    if frozen_stock is not None:
        parts.append({"Статья": "Товарные запасы (оценка)", "Сумма": round(frozen_stock, 2)})
    if frozen_ar is not None:
        parts.append({"Статья": "Дебиторка / взаиморасчёты", "Сумма": round(frozen_ar, 2)})
    if overdue is not None:
        parts.append({"Статья": "в т.ч. просрочка (>30 дн.)", "Сумма": round(overdue, 2)})
    total = sum(_f(p.get("Сумма")) for p in parts if "т.ч." not in str(p.get("Статья")))

    advice = []
    if overdue and overdue > 0:
        advice.append(
            f"Просроченная ДЗ ≈ {round(overdue, 2)} — приоритет взыскания / стоп-отгрузки."
        )
    if turnover and turnover.get("days_on_hand"):
        doh = turnover.get("days_on_hand")
        advice.append(f"Дней запаса ≈ {doh} — смотреть неликвиды и закупки.")
    if frozen_stock and frozen_ar:
        advice.append(
            "Сравнивайте «склад vs дебиторка»: где больше «заморозки» — туда контроль."
        )
    if not advice:
        advice.append("Сверьте остатки и ДЗ; для точности укажите период/склад.")

    return {
        "ok": True,
        "parts": parts,
        "total_frozen_approx": round(total, 2) if parts else None,
        "overdue_approx": round(overdue, 2) if overdue is not None else None,
        "turnover": turnover,
        "aging_summary": (aging or {}).get("summary_by_bucket"),
        "stock_top": (stock_rows or [])[:15],
        "ar_top": (ar_rows or [])[:15],
        "stock_total": len(stock_rows or []),
        "ar_total": len(ar_rows or []),
        "advice": advice,
        "rows": parts,
    }


def is_supplier_analysis_question(phrase: str) -> bool:
    """
    Поставщики (закупки), не клиенты/продажи.
    «какие поставщики ценные / не очень / ABC по поставщикам».
    """
    pl = _norm(phrase)
    if not pl:
        return False
    if not any(x in pl for x in ("поставщик", "vendor", "supplier")):
        return False
    # явные клиенты/покупатели без «поставщик» уже отсечены выше
    if any(
        x in pl
        for x in (
            "клиентск",
            "vip клиент",
            "покупател",
            "отток клиент",
            "база клиент",
        )
    ) and "поставщик" not in pl:
        return False
    # поставщик + ценность/ранжирование/анализ — или просто «поставщики …»
    if any(
        x in pl
        for x in (
            "ценн",
            "не очень",
            "важн",
            "лучш",
            "худш",
            "плох",
            "топ",
            "abc",
            "авс",
            "парето",
            "анализ",
            "ранг",
            "рейтинг",
            "кто из",
            "какие",
            "самые",
            "ключев",
            "основн",
            "мелк",
            "крупн",
            "доля",
        )
    ):
        return True
    # «поставщики за …» / «по поставщикам»
    if "по поставщик" in pl or re.search(r"\bпоставщик\w*\s+за\b", pl):
        return True
    return False


def detect_analyst_mode(phrase: str) -> str | None:
    """abc | variance | aging | drill | plan_fact | turnover | funnel | anomaly | multi |
    assortment | reserves | profit | customers | suppliers | None."""
    pl = _norm(phrase)
    if not pl:
        return None
    # --- поставщики (закупки) ДО customers: не путать с VIP-клиентами ---
    if is_supplier_analysis_question(phrase):
        return "suppliers"
    # --- 4 бизнес-блока (приоритет над общим abc) ---
    if any(
        x in pl
        for x in (
            "ассортимент",
            "что продается",
            "что продаётся",
            "что не продается",
            "что не продаётся",
            "неликвит",
            "неликвид",
            "мертв",
            "мёртв",
            "хит продаж",
            "хиты",
            "бестселлер",
            "bestseller",
            "спрос на товар",
            "анализ спроса",
            "что покупают",
            "залежал",
            # разговорно: «хорошо шло / не брали / не берут»
            "хорошо шло",
            "плохо шло",
            "не брали",
            "не берут",
            "не брал",
            "что шло",
            "что из товаров",
            "что из номенклатур",
            "топ товар",
            "ходовые товар",
            "неходов",
        )
    ) or (
        # «что … шло / брали» + товар/номенклатура
        any(x in pl for x in ("товар", "номенклатур", "ассортимент"))
        and any(
            x in pl
            for x in (
                "хорошо",
                "плохо",
                "шло",
                "бра",
                "неликвид",
                "хит",
                "спрос",
            )
        )
        and not any(x in pl for x in ("контрагент", "клиент", "менеджер"))
    ):
        return "assortment"
    if any(
        x in pl
        for x in (
            "заморожен",
            "скрыт резерв",
            "скрытые резерв",
            "где деньги",
            "заморожены деньги",
            "заморозка денег",
            "оборотный капитал",
            "замороженный капитал",
            "где висят деньги",
            "затоварен",
        )
    ):
        return "reserves"
    if any(
        x in pl
        for x in (
            "как заработать больше",
            "рост прибыли",
            "увеличить прибыль",
            "маржинальн",
            "рентабельн",
            "валовая прибыл",
            "прибыль по",
            "где маржа",
            "низкая маржа",
        )
    ) or (
        "маржа" in pl
        and any(x in pl for x in ("рост", "анализ", "больше", "прибыл", "товар", "клиент"))
    ):
        return "profit"
    if any(
        x in pl
        for x in (
            "клиентск",
            "база покупател",
            "база клиент",
            "vip клиент",
            "клиенты vip",
            "vip-клиент",
            "вип клиент",
            "отток клиент",
            "отвалил",
            "отваливш",
            "ушедш",
            "лояльн",
            "сегмент клиент",
            "rfm",
            "повторн покупок",
            "работа с базой",
            "покупательск база",
            "кто не покупал",
            "не покупал",
            "не покупали",
            "потерянн клиент",
            "потерянные клиент",
            "ушедшие клиент",
            "клиенты которые",
        )
    ) or (
        # «vip + клиент/покупатель» в любом порядке
        ("vip" in pl or "вип" in pl)
        and any(x in pl for x in ("клиент", "покупат", "контрагент"))
    ):
        return "customers"
    if any(x in pl for x in ("abc", "авс", "класс a", "группы a b c", "парето")):
        return "abc"
    if any(
        x in pl
        for x in (
            "просроч",
            "aging",
            "корзин",
            "0-30",
            "0–30",
            "дней долг",
            "возраст долг",
        )
    ):
        return "aging"
    if any(
        x in pl
        for x in (
            "вклад",
            "за счёт кого",
            "за счет кого",
            "contribution",
            "дельта",
            "прирост",
            "падение продаж",
            "кто вырос",
            "кто упал",
            "кто дал рост",
            "дал рост",
            "дал падение",
            "variance",
        )
    ):
        return "variance"
    if any(
        x in pl
        for x in (
            "аномал",
            "скачок",
            "обвал",
            "пропал из топ",
            "падение больше",
            "> 50",
            "более 50%",
        )
    ):
        return "anomaly"
    # drill — follow-up расшифровка; standalone «документы по X за 2015» → data/list
    drill_markers = (
        "drill",
        "расшифруй",
        "детализац",
        "провалиться",
        "drill-down",
        "drill down",
    )
    if any(x in pl for x in drill_markers):
        return "drill"
    if any(x in pl for x in ("документ по", "документы по", "покажи документ", "список реализац")) or re.search(
        r"документ\w*\s+(по|для)\s+\w+", pl
    ):
        # если есть явный период/год — это список документов (build_report), не analyst drill
        has_period = bool(
            re.search(r"\b((?:19|20)\d{2})\b", pl)
            or any(
                x in pl
                for x in (
                    "за год",
                    "за месяц",
                    "январ",
                    "феврал",
                    "март",
                    "апрел",
                    "июн",
                    "июл",
                    "август",
                    "сентябр",
                    "октябр",
                    "ноябр",
                    "декабр",
                )
            )
        )
        if not has_period:
            return "drill"
    if any(x in pl for x in ("план-факт", "план факт", "план vs", "vs факт", "план/факт")):
        return "plan_fact"
    if any(
        x in pl
        for x in (
            "оборачив",
            "дней запас",
            "дни запас",
            "turnover",
            "скорость продаж запас",
        )
    ):
        return "turnover"
    if any(
        x in pl
        for x in (
            "воронк",
            "конверси",
            "заказ без",
            "без отгруз",
            "без оплат",
            "funnel",
        )
    ):
        return "funnel"
    # multi group: «по X и Y» / «в разрезе A и B»
    if re.search(
        r"(по|в разрезе)\s+\w+\s+и\s+\w+",
        pl,
    ) or any(x in pl for x in ("по месяцам и", "и по склад", "и контрагент")):
        return "multi"
    if any(
        x in pl
        for x in (
            "аналитик",
            "аналитическ",
            "с графиками",
            "красивый excel",
            "dashboard",
            "дашборд",
        )
    ):
        return "dashboard"
    return None


def wants_excel_charts(phrase: str) -> bool:
    """Графики: явно или «красивый отчёт / excel / dashboard»."""
    pl = _norm(phrase)
    return any(
        x in pl
        for x in (
            "график",
            "диаграмм",
            "chart",
            "с графиками",
            "красив",
            "dashboard",
            "дашборд",
            "наглядн",
            "столбчат",
            "круг",
            "pie",
            "excel",
            "xlsx",
            "выгруз",
            "эксель",
        )
    )


def wants_excel_file(phrase: str) -> bool:
    pl = _norm(phrase)
    return any(
        x in pl
        for x in (
            "excel",
            "xlsx",
            "выгруз",
            "эксель",
            "в файл",
            "таблиц excel",
            "с графиками",
            "красив",
            "dashboard",
            "дашборд",
            "в xlsx",
            "скачать отчёт",
            "скачать отчет",
        )
    )
