# -*- coding: utf-8 -*-
"""
Контекст диалога: короткие follow-up («только [контрагент]», «этот и прошлый год»)
не должны терять период/объект из предыдущего отчёта.

Preflight смотрит только текущую фразу → без этого спрашивает период заново
и «забывает» прошлый ответ.
"""

from __future__ import annotations

import json
import re
from typing import Any


def _norm(s: str) -> str:
    return (s or "").lower().replace("ё", "е").strip()


def snapshot_from_result(
    tool: str,
    result: Any,
    *,
    user_text: str = "",
    args: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Снимок последнего data-отчёта для follow-up (excel / «только X» / мастер)."""
    if not isinstance(result, dict):
        return None
    # wizard-шаги без данных — не затираем прошлый полный отчёт
    if result.get("wizard") and result.get("need_clarification") and not result.get("rows"):
        return {
            "tool": tool,
            "pending_clarification": True,
            "about": result.get("about") or "report_wizard",
            "question": result.get("question") or "",
            "full_name": result.get("full_name"),
            "user_text": (user_text or "")[:300],
            "args": dict(args or {}),
            "rows": [],
            "summary_by_period": [],
            "period_spec": "",
            "periods": [],
            "group_by": None,
        }

    report_tools = {
        "compare_periods",
        "summarize_periods",
        "build_report",
        "linked_query",
        "margin_report",
        "report_builder",
        "analyst_report",
        "object_report",
        "execute_query",
        "query_1c",
    }
    rows = result.get("rows")
    if not isinstance(rows, list):
        rows = []
    if not rows and isinstance(result.get("top_growth"), list):
        rows = [r for r in result["top_growth"] if isinstance(r, dict)]
    if not rows and isinstance(result.get("execute_result"), dict):
        rows = (
            result["execute_result"].get("rows")
            or result["execute_result"].get("data")
            or []
        )
        if not isinstance(rows, list):
            rows = []

    has_data = bool(rows) or bool(result.get("summary_by_period")) or bool(
        result.get("full_name") and result.get("ok")
    )
    if tool not in report_tools and not has_data:
        return None
    if result.get("need_clarification") and not rows:
        # запомним, что ждали период — для ответа «2015 2016»
        return {
            "tool": tool,
            "pending_clarification": True,
            "about": result.get("about") or "",
            "question": result.get("question") or result.get("error") or "",
            "full_name": result.get("full_name"),
            "user_text": (user_text or "")[:300],
            "args": dict(args or {}),
            "rows": [],
            "summary_by_period": [],
            "period_spec": (args or {}).get("period") or result.get("period_spec") or "",
            "periods": [],
            "group_by": (args or {}).get("group_by") or result.get("group_by"),
        }

    periods: list[str] = []
    sbp = result.get("summary_by_period")
    if isinstance(sbp, list):
        for p in sbp:
            if isinstance(p, dict) and p.get("Период"):
                periods.append(str(p["Период"]))
    if not periods and isinstance(result.get("periods"), list):
        for p in result["periods"]:
            if isinstance(p, dict) and p.get("label"):
                periods.append(str(p["label"]))
            elif p and not isinstance(p, dict):
                periods.append(str(p))
    if not periods:
        seen: set[str] = set()
        for r in rows:
            if isinstance(r, dict):
                lab = str(r.get("Период") or "").strip()
                if lab and lab not in seen:
                    seen.add(lab)
                    periods.append(lab)

    if not rows and not periods and not result.get("ok") and not result.get("full_name"):
        return None

    # keep enough rows for local filter «только X» и export без повторного запроса
    max_keep = 150
    rows_keep = [r for r in rows[:max_keep] if isinstance(r, dict)]

    title = ""
    ms = result.get("manager_summary")
    if isinstance(ms, dict):
        title = str(ms.get("title") or ms.get("object") or "")[:120]
    if not title:
        title = str(result.get("title") or result.get("mode") or "")[:120]

    return {
        "tool": tool,
        "pending_clarification": False,
        "full_name": result.get("full_name")
        or (ms.get("object") if isinstance(ms, dict) else None),
        "period_spec": str(
            result.get("period_spec")
            or result.get("period")
            or (args or {}).get("period")
            or ""
        ),
        "periods": periods,
        "group_by": result.get("group_by") or (args or {}).get("group_by"),
        "summary_by_period": sbp if isinstance(sbp, list) else [],
        "rows": rows_keep,
        "columns": result.get("columns"),
        "user_text": (user_text or "")[:300],
        "args": dict(args or {}),
        "manager_summary": result.get("manager_summary"),
        "value_field": result.get("value_field"),
        "metrics": result.get("metrics"),
        "title": title,
        "path": result.get("path"),
        "mode": result.get("mode") or result.get("about"),
    }


def extract_state_from_messages(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Запасной разбор: последний tool result в истории."""
    if not messages:
        return None
    # идём с конца: tool content JSON
    for m in reversed(messages[-40:]):
        if m.get("role") != "tool":
            continue
        content = m.get("content")
        if not isinstance(content, str) or len(content) < 20:
            continue
        try:
            data = json.loads(content.split("…[")[0])
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("rows") or data.get("summary_by_period") or data.get("full_name"):
            # tool name unknown — guess
            tool = "compare_periods" if data.get("summary_by_period") else "build_report"
            if data.get("objects") or data.get("route") == "linked":
                tool = "linked_query"
            snap = snapshot_from_result(tool, data)
            if snap:
                return snap
    return None


_FILTER_ONLY = re.compile(
    r"^\s*(?:сравни\s+)?(?:только|лишь|filter)\s+(.+?)\s*$",
    re.I,
)
_YEARS_ONLY = re.compile(
    r"^\s*((?:19|20)\d{2})"
    r"(?:\s*(?:и|,|/|-|–|—|\s)\s*((?:19|20)\d{2}))+"
    r"(?:\s*год[аы]?)?\s*$",
    re.I,
)
_SHORT_PERIOD = re.compile(
    r"^\s*(?:за\s+)?((?:19|20)\d{2})"
    r"(?:\s*(?:и\s+)?((?:19|20)\d{2}))?"
    r"\s*(?:год[аы]?)?\s*$",
    re.I,
)


def wants_full_list_followup(phrase: str) -> bool:
    """
    «там было 2», «все контрагенты», «полный список» —
    не отмахиваться «смотри Excel», а пересобрать/показать полный объём.
    """
    pl = _norm(phrase)
    if not pl:
        return False
    if any(
        x in pl
        for x in (
            "полный список",
            "полную таблиц",
            "все строки",
            "все контрагент",
            "всем контрагент",
            "всех контрагент",
            "покажи всех",
            "покажи все",
            "не только топ",
            "не два",
            "не 2",
        )
    ):
        return True
    if re.search(
        r"(там|было|только|лишь)\s+\d+\s*(контрагент|строк|позиц|клиент)",
        pl,
    ):
        return True
    if re.search(r"\b\d+\s+контрагент", pl) and any(
        x in pl for x in ("было", "там", "всего", "только", "лишь")
    ):
        return True
    return False


def wants_export_existing(phrase: str) -> bool:
    """
    «запихни в отчёт», «в excel», «выгрузи это» — экспорт УЖЕ показанных данных,
    не новый мастер с нуля.
    """
    pl = _norm(phrase)
    if not pl:
        return False
    # явный reuse + отчёт/файл
    if any(
        x in pl
        for x in (
            "запих",
            "засун",
            "положи",
            "кида",
            "скинь",
            "сохрани это",
            "сохрани его",
            "сохрани их",
            "выгрузи это",
            "выгрузи его",
            "выгрузи их",
            "выгрузи в",
            "экспорт",
            "export",
            "в файл",
            "в excel",
            "в эксель",
            "в ексель",
            "в xlsx",
            "в отчёт",
            "в отчет",
            "в таблиц",
            "из этого отч",
            "из этих данн",
            "из него отч",
            "сделай из этого",
            "сделай из него",
            "тот же в",
            "то же в",
            "тоже в excel",
            "тоже в эксель",
            "оформи в",
            "оформи отч",
            "красивый отч",
            "с диаграмм",
            "с график",
        )
    ):
        # не считать «продаж за 2016 в отчёт» как reuse без «это/его» —
        # но короткие «в excel» / «в отчёт» при наличии state — reuse
        if any(
            x in pl
            for x in (
                "это",
                "его",
                "их",
                "эти",
                "тот",
                "та же",
                "то же",
                "тоже",
                "текущ",
                "последн",
                "уже",
                "выше",
                "остатк",
                "из консол",
                "что показал",
                "что вывел",
            )
        ):
            return True
        # короткие команды без новой темы
        if len(pl) <= 55 and not re.search(
            r"\b(?:19|20)\d{2}\b",
            pl,
        ):
            if any(
                x in pl
                for x in (
                    "в отчёт",
                    "в отчет",
                    "в excel",
                    "в эксель",
                    "в файл",
                    "выгруз",
                    "с диаграмм",
                    "с график",
                    "xlsx",
                )
            ):
                return True
        if any(
            x in pl
            for x in (
                "запих",
                "засун",
                "положи в отч",
                "положи в excel",
                "из этого",
                "из него",
            )
        ):
            return True
    return False


def looks_like_followup(phrase: str, state: dict[str, Any] | None) -> bool:
    pl = _norm(phrase)
    if not pl or len(pl) > 120:
        return False
    if _FILTER_ONLY.match(phrase or ""):
        return True
    if _YEARS_ONLY.match(phrase or "") or _SHORT_PERIOD.match(phrase or ""):
        return True
    if wants_export_existing(phrase):
        return True
    # «а теперь по X», «в разрезе X»
    if any(
        pl.startswith(x)
        for x in (
            "а теперь",
            "теперь ",
            "только ",
            "а по ",
            "по ним",
            "то же ",
            "тоже ",
            "ещё раз",
            "еще раз",
            "без группир",
            "сгруппируй",
        )
    ):
        return True
    if state and state.get("pending_clarification") and len(pl) < 80:
        return True
    # очень короткая фраза при наличии state
    if state and not state.get("pending_clarification") and len(pl) < 40:
        has_period = bool(re.search(r"\b(?:19|20)\d{2}\b", pl))
        has_data_word = any(
            x in pl
            for x in (
                "продаж",
                "реализ",
                "сумм",
                "отчет",
                "отчёт",
                "сколько",
                "остатк",
            )
        )
        if not has_data_word and (not has_period or "только" in pl):
            return True
        # «в отчёт» / «в excel» при живом state
        if any(x in pl for x in ("отчёт", "отчет", "excel", "эксель", "файл", "выгруз")):
            return True
    return False

def has_reusable_report(state: dict[str, Any] | None) -> bool:
    """Есть данные прошлого отчёта, которые можно выгрузить без нового мастера."""
    if not isinstance(state, dict):
        return False
    if state.get("pending_clarification") and not state.get("rows"):
        return False
    rows = state.get("rows")
    if isinstance(rows, list) and any(isinstance(r, dict) for r in rows):
        return True
    if state.get("full_name") and (
        state.get("period_spec") or state.get("periods") or state.get("summary_by_period")
    ):
        return True
    return False


def export_snapshot_to_excel(
    snap: dict[str, Any],
    *,
    phrase: str = "",
    charts: bool = True,
) -> dict[str, Any]:
    """Собрать Excel (+диаграммы) из rows прошлого отчёта — без повторного запроса к 1С."""
    from tools.excel_export import (
        infer_axes_from_rows,
        save_analyst_excel,
    )

    rows = [r for r in (snap.get("rows") or []) if isinstance(r, dict)]
    if not rows:
        return {
            "ok": False,
            "error": "В прошлом отчёте нет строк для выгрузки. Сначала получите данные.",
            "need_data": True,
        }

    fn = str(snap.get("full_name") or "отчёт")
    period = str(snap.get("period_spec") or "")
    if not period and snap.get("periods"):
        period = " и ".join(str(p) for p in snap["periods"][:4])
    title = str(snap.get("title") or "").strip()
    if not title:
        short = fn.split(".")[-1] if "." in fn else fn
        title = f"Отчёт: {short}" + (f" ({period})" if period else "")

    cat_k, val_k = infer_axes_from_rows(rows)
    # эвристика value для остатков
    if not val_k and rows:
        for prefer in (
            "Остаток",
            "Количество",
            "Сумма",
            "КоличествоОстаток",
            "СуммаОстаток",
        ):
            for col in rows[0].keys():
                if prefer.lower() in str(col).lower():
                    val_k = str(col)
                    break
            if val_k:
                break
    metric = val_k or ""
    pl = _norm(phrase)
    charts_spec: list[dict[str, Any]] = []
    if charts:
        if any(x in pl for x in ("круг", "pie", "доли")):
            charts_spec.append(
                {"type": "pie", "value": metric, "title": f"pie: {metric}"}
            )
        if any(x in pl for x in ("лин", "line")):
            charts_spec.append(
                {"type": "line", "value": metric, "title": f"line: {metric}"}
            )
        if any(x in pl for x in ("столб", "bar", "гист")) or not charts_spec:
            charts_spec.insert(
                0, {"type": "bar", "value": metric, "title": f"bar: {metric}"}
            )
        if "и pie" in pl or "и круг" in pl or ("столб" in pl and "круг" in pl):
            if not any(c.get("type") == "pie" for c in charts_spec):
                charts_spec.append(
                    {"type": "pie", "value": metric, "title": f"pie: {metric}"}
                )
        if not charts_spec:
            charts_spec = [
                {"type": "bar", "value": metric, "title": f"bar: {metric}"},
                {"type": "pie", "value": metric, "title": f"pie: {metric}"},
            ]
        for c in charts_spec:
            if cat_k:
                c["chart_category"] = cat_k

    sheets = [
        {
            "name": "Данные",
            "title": title,
            "rows": rows[:500],
            "chart": (charts_spec[0]["type"] if charts_spec else "bar"),
            "chart_value": metric,
            "chart_category": cat_k,
        }
    ]
    sbp = snap.get("summary_by_period")
    try:
        from tools.excel_export import (
            clean_summary_period_rows,
            summary_sheet_worth_it,
        )

        if summary_sheet_worth_it(sbp):
            sbp_rows = clean_summary_period_rows(sbp)[:40]
            from tools.excel_export import infer_axes_from_rows as _inf

            _sc, _sv = _inf(sbp_rows, preferred_category="Период")
            sheets.append(
                {
                    "name": "Итоги",
                    "title": "Итоги по периодам",
                    "rows": sbp_rows,
                    "chart": "bar",
                    "chart_value": _sv,
                    "chart_category": _sc or "Период",
                }
            )
    except Exception:
        pass

    bullets = [
        f"Источник: прошлый ответ ({snap.get('tool') or 'отчёт'})",
        f"Объект: {fn}",
        f"Период: {period or '—'}",
        f"Строк: {len(rows)}",
        f"Группировка: {snap.get('group_by') or '—'}",
        "Без повторного запроса к 1С — данные из консоли/сессии.",
    ]
    saved = save_analyst_excel(
        title=title,
        sheets=sheets,
        bullets=bullets,
        meta_extra={
            "full_name": fn,
            "period": period,
            "from_last_report": True,
            "chart_category": cat_k,
            "chart_value": metric,
        },
        filename="from_last_report.xlsx",
        charts_spec=charts_spec or None,
    )
    out: dict[str, Any] = {
        "ok": not bool(saved.get("error")),
        "from_last_report": True,
        "reused_rows": len(rows),
        "full_name": fn,
        "period_spec": period,
        "group_by": snap.get("group_by"),
        "rows": rows[:30],
        "count": len(rows),
        "builder": "export_last_report",
        "prefer": (
            f"Это выгрузка УЖЕ показанных данных: **{len(rows)} строк** "
            "(без мастера и без нового запроса к 1С). "
            "Покажи path/download_url и число строк. "
            "Если в чате раньше мелькнуло 1–2 строки — это был урезанный preview; "
            "в Excel полный снимок. Не спрашивай объект/период заново."
        ),
        "note": f"Excel из прошлого отчёта: {len(rows)} строк.",
    }
    if saved.get("error"):
        out["error"] = saved["error"]
        out["ok"] = False
    else:
        for k in (
            "path",
            "filename",
            "download_url",
            "file_uri",
            "downloads_path",
            "how_to_open",
            "charts",
            "charts_count",
            "sheets",
        ):
            if saved.get(k) is not None:
                out[k] = saved[k]
        out["to_excel"] = True
    return out


def _extract_filter_name(phrase: str) -> str | None:
    m = _FILTER_ONLY.match((phrase or "").strip())
    if not m:
        # «только …» после отчёта — имя из фразы (не демо-словарь)
        return None
    name = m.group(1).strip().strip("«»\"'")
    # убрать хвост «контрагента/по»
    name = re.sub(
        r"^(?:контрагент[аеу]?\s+|клиент[аеу]?\s+|по\s+)",
        "",
        name,
        flags=re.I,
    ).strip()
    return name or None


def try_local_filter(
    phrase: str,
    state: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """
    «только [имя]» после compare → отфильтровать rows локально,
    без нового preflight/clarify периода.
    """
    if not state or not state.get("rows"):
        return None
    name = _extract_filter_name(phrase)
    if not name:
        return None
    needle = _norm(name)
    if len(needle) < 2:
        return None

    rows_in = [r for r in (state.get("rows") or []) if isinstance(r, dict)]
    # поля для фильтра: group_by или типичные
    keys = []
    gb = state.get("group_by")
    if gb:
        keys.append(str(gb))
    keys.extend(
        [
            "Контрагент",
            "Партнер",
            "Партнёр",
            "Номенклатура",
            "Организация",
            "Склад",
        ]
    )
    # любые строковые поля из первой строки
    if rows_in:
        for k, v in rows_in[0].items():
            if k not in keys and isinstance(v, str) and k not in (
                "Период",
                "ДатаНачала",
                "ДатаКонца",
                "error",
            ):
                keys.append(k)

    matched: list[dict[str, Any]] = []
    for r in rows_in:
        for k in keys:
            if k not in r:
                continue
            val = _norm(str(r.get(k) or ""))
            if needle in val or val in needle:
                matched.append(r)
                break

    if not matched:
        # нет в top-N — пусть expand + новый запрос
        return None

    # пересчитать summary по отфильтрованным
    by_p: dict[str, dict[str, float]] = {}
    for r in matched:
        lab = str(r.get("Период") or "—")
        slot = by_p.setdefault(lab, {"Сумма": 0.0, "Количество": 0.0})
        try:
            slot["Сумма"] += float(r.get("Сумма") or 0)
        except (TypeError, ValueError):
            pass
        try:
            slot["Количество"] += float(r.get("Количество") or 0)
        except (TypeError, ValueError):
            pass
    sbp = [
        {
            "Период": lab,
            "Сумма": v["Сумма"],
            "Количество": int(v["Количество"])
            if v["Количество"] == int(v["Количество"])
            else v["Количество"],
            "groups": sum(1 for r in matched if str(r.get("Период")) == lab),
        }
        for lab, v in by_p.items()
    ]
    periods = list(by_p.keys())
    msg = (
        f"Фильтр «{name}» по предыдущему отчёту "
        f"({state.get('full_name') or '…'}). "
        f"Периоды: {', '.join(periods) or state.get('period_spec') or '—'}. "
        f"Строк: {len(matched)}."
    )
    prefer = (
        "Ответ менеджеру: это уточнение ПРЕДЫДУЩЕГО отчёта (фильтр). "
        f"Покажи summary_by_period и rows только для «{name}». "
        "НЕ спрашивай период заново — он уже из прошлого ответа. "
        "НЕ вызывай linked_query / build_report без нужды."
    )
    return {
        "ok": True,
        "from_dialog_filter": True,
        "filter": name,
        "full_name": state.get("full_name"),
        "period_spec": state.get("period_spec"),
        "periods": [
            {"label": p} for p in (state.get("periods") or periods)
        ],
        "summary_by_period": sbp,
        "rows": matched,
        "count": len(matched),
        "group_by": state.get("group_by"),
        "columns": state.get("columns"),
        "manager_summary": {
            "object": state.get("full_name"),
            "period": state.get("period_spec"),
            "periods": periods,
            "row_count": len(matched),
            "message": msg,
            "request": phrase,
        },
        "prefer": prefer,
        "note": "Локальный фильтр предыдущего tool result (диалог). Период не потерян.",
    }


def expand_with_context(
    phrase: str,
    state: dict[str, Any] | None,
) -> str | None:
    """
    Развернуть короткую реплику в полный request для tools/preflight.
    None = оставляем как есть.
    """
    if not state:
        return None
    raw = (phrase or "").strip()
    if not raw:
        return None
    pl = _norm(raw)

    periods = state.get("periods") or []
    period_spec = str(state.get("period_spec") or "").strip()
    period_bit = ""
    if periods and len(periods) >= 2:
        period_bit = f"за {' и '.join(str(p) for p in periods)} "
    elif periods:
        period_bit = f"за {periods[0]} "
    elif period_spec and re.search(r"\b(?:19|20)\d{2}\b", period_spec):
        # взять годы из period_spec
        ys = re.findall(r"\b((?:19|20)\d{2})\b", period_spec)
        uniq = []
        for y in ys:
            if y not in uniq:
                uniq.append(y)
        if uniq:
            period_bit = f"за {' и '.join(uniq)} "

    fn = str(state.get("full_name") or "")
    short = fn.split(".")[-1] if "." in fn else fn
    base_topic = short or str(state.get("user_text") or "отчёт")
    prev = str(state.get("user_text") or "")

    # 1) только <имя>
    fname = _extract_filter_name(raw)
    if fname:
        if not period_bit and "сравни" in _norm(prev):
            # всё равно сравнить если прошлый был compare
            ys = re.findall(r"\b((?:19|20)\d{2})\b", prev)
            if len(set(ys)) >= 2:
                period_bit = f"за {' и '.join(dict.fromkeys(ys))} "
        return (
            f"сравни {base_topic} контрагента {fname} {period_bit}"
            f"по документу {fn}".strip()
            if fn
            else f"сравни {base_topic} контрагента {fname} {period_bit}".strip()
        )

    # 2) только годы — ответ на clarify или уточнение
    ys_in = re.findall(r"\b((?:19|20)\d{2})\b", raw)
    years_only_phrase = bool(ys_in) and re.fullmatch(
        r"\s*(?:за\s+)?(?:(?:19|20)\d{2})"
        r"(?:\s*(?:и|,|/|-|–|—)?\s*(?:19|20)\d{2})+"
        r"(?:\s*год[аы]?)?\s*",
        raw,
        flags=re.I,
    )
    if years_only_phrase or _YEARS_ONLY.match(raw) or (
        _SHORT_PERIOD.match(raw) and len(ys_in) >= 1 and len(pl) < 40
    ):
        ys = list(dict.fromkeys(ys_in))
        ybit = " и ".join(ys) if ys else raw
        # имя фильтра из prev user «только <контрагент>»
        prev_filter = _extract_filter_name(prev) if prev else None
        if not prev_filter:
            prev_filter = _extract_filter_name(str(state.get("user_text") or ""))
        if prev_filter:
            return f"сравни {base_topic} контрагента {prev_filter} за {ybit} года"
        if prev and len(prev) > 5:
            # «сравни продажи…» + годы
            if re.search(r"\b(?:19|20)\d{2}\b", prev):
                return re.sub(
                    r"\b(?:19|20)\d{2}(?:\s*(?:и|,)?\s*(?:19|20)\d{2})*\b",
                    ybit,
                    prev,
                    count=1,
                )
            return f"{prev} за {ybit} года"
        return f"сравни {base_topic} за {ybit} года"

    # 3) pending clarification + короткая фраза
    if state.get("pending_clarification") and len(pl) < 80:
        base = str(state.get("user_text") or prev or base_topic)
        return f"{base} {raw}".strip()

    return None

