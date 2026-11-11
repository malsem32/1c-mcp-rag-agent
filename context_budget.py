# -*- coding: utf-8 -*-
"""
Бюджет контекста модели (целевой режим: 32k; масштаб на 64k/128k — через config).

A — лимиты из config
C — trim по user-turns, старые tool → summary
D — per-tool budget для compact_json
E — давление на budget → diary (снаружи)
"""

from __future__ import annotations

import json
from typing import Any


# Дефолты под context = 32_000
DEFAULT_CONTEXT_TOKENS = 32_000
DEFAULT_MAX_OUTPUT_TOKENS = 3_072
DEFAULT_TOOL_RESULT_MAX_CHARS = 8_000
DEFAULT_TOOL_RESULT_SOFT_CHARS = 4_000
DEFAULT_HISTORY_MAX_MESSAGES = 40
DEFAULT_HISTORY_MAX_TURNS = 6
DEFAULT_KEEP_RECENT_TOOL_FULL = 2
DEFAULT_OLD_TOOL_CHARS = 700
DEFAULT_DIARY_MAX_CHARS = 1_400
DEFAULT_QUERY_DEFAULT_LIMIT = 25
DEFAULT_QUERY_MAX_LIMIT = 80
DEFAULT_MAX_TOOL_ROUNDS = 8
DEFAULT_LIST_OBJECTS_CAP = 80
DEFAULT_STRUCTURE_ATTR_CAP = 80

# D: бюджеты по tool (символы JSON). Не выше tool_result_max_chars из config.
TOOL_BUDGETS: dict[str, int] = {
    # data / compare — нужны summary_by_period + оба периода
    "compare_periods": 10_000,
    "summarize_periods": 10_000,
    "analyst_report": 12_000,
    "report_builder": 12_000,
    "build_report": 9_000,
    "query_by_attributes": 9_000,
    "linked_query": 9_000,
    "margin_report": 8_000,
    "export_object_excel": 4_000,
    "export_document_period_excel": 4_000,
    "export_excel_report": 4_000,
    # structure — attributes_lines важны
    "get_object_structure": 12_000,
    "describe_object": 10_000,
    "list_object_attributes": 10_000,
    "search_fields": 6_000,
    # code
    "search_code": 10_000,
    "read_code": 12_000,
    "inspect_code": 10_000,
    "inspect_external_processing": 10_000,
    "find_modules": 6_000,
    "module_outline": 10_000,
    "trace_calls": 8_000,
    "find_callers": 6_000,
    "read_skd_query": 10_000,
    "analyze_object_movements": 9_000,
    "explain_fill": 7_000,
    "analyze_locks": 7_000,
    "recommend_managed_locks": 8_000,
    "scan_code_injection": 7_000,
    "audit_query_code": 8_000,
    # meta / light
    "find_metadata": 5_000,
    "resolve_object": 4_000,
    "quick_find": 4_000,
    "list_objects": 5_000,
    "manager_help": 4_000,
    "query_syntax_help": 6_000,
    "test_connection": 2_000,
    "list_active_sessions": 4_000,
    "access_overview": 6_000,
    "analyze_event_log": 6_000,
    "execute_query": 8_000,
}

# приоритетные ключи — первыми в JSON и не выкидывать при shrink
PRIORITY_KEYS = (
    "error",
    "hint",
    "ok",
    "need_clarification",
    "question",
    "options",
    "full_name",
    "document_name",
    "catalog_name",
    "roles",
    "amount_field",
    "counterparty_field",
    "found_count",
    "rows_count",
    "row_count",
    "period_spec",
    "periods",
    "summary_by_period",
    "manager_summary",
    "empty_reason",
    "data_range",
    "table_preview",
    "rows_preview",
    "analyst_summary",
    "top_growth",
    "top_decline",
    "variance",
    "abc",
    "anomalies",
    "funnel",
    "turnover",
    "prefer",
    "note",
    "delta",
    "delta_pct",
    "delta_note",
    "path",
    "download_url",
    "columns",
    "group_by",
    "value_field",
    "date_field",
    "register_names_full",
    "registers_all",
    "attributes_lines",
    "attribute_count",
    "structure_brief",
    "forms",
    "form_names",
    "templates",
    "default_forms",
    "tabular_sections",
    # code tools
    "text",
    "content",
    "code",
    "lines",
    "symbols",
    "matches",
    "hits",
    "path",
    "rel_path",
    "start_line",
    "end_line",
    "query",
    "tool",
    "rows_meta",
    "from_dialog_filter",
    "filter",
    "primary",
    "plan_role",
    "as_of",
)

# тяжёлые дубликаты — выкидывать при shrink (rows уже на верхнем уровне)
DROP_ON_SHRINK = frozenset(
    {
        "execute_result",  # дубль rows
        "objects_detailed",  # structure dump
        "alt_plans",  # планы-альтернативы
        "rules",
        "raw",
        "debug",
        "structure",
        "fields",
        "matched_fields",
        "dimensions",
        "resources",
    }
)


def chars_budget(
    context_tokens: int,
    *,
    reserve_output: int = 3072,
    reserve_tools_schema: int = 7000,
    chars_per_token: float = 3.0,
) -> int:
    """Сколько символов roughly можно держать в истории+tool results."""
    usable = max(2000, int(context_tokens) - int(reserve_output) - int(reserve_tools_schema))
    return int(usable * chars_per_token)


def messages_total_chars(msgs: list[dict[str, Any]]) -> int:
    n = 0
    for m in msgs:
        c = m.get("content")
        if isinstance(c, str):
            n += len(c)
        elif c is not None:
            n += len(str(c))
        tcs = m.get("tool_calls")
        if tcs:
            try:
                n += len(json.dumps(tcs, ensure_ascii=False, default=str))
            except Exception:
                n += 200
    return n


def tool_budget(tool_name: str | None, default_max: int = DEFAULT_TOOL_RESULT_MAX_CHARS) -> int:
    """D: лимит символов для tool result (не выше default_max из config)."""
    name = (tool_name or "").strip()
    base = TOOL_BUDGETS.get(name, default_max)
    # per-tool бюджет может быть чуть выше общего (до 1.5×), иначе TOOL_BUDGETS не работают
    return max(1500, min(int(base), int(int(default_max) * 1.5)))


def compact_json(
    value: Any,
    max_chars: int = DEFAULT_TOOL_RESULT_MAX_CHARS,
    *,
    tool_name: str | None = None,
) -> str:
    """Сериализация с усечением; tool_name → per-tool budget (D). Всегда валидный JSON."""
    limit = tool_budget(tool_name, max_chars) if tool_name else int(max_chars)
    try:
        payload = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        payload = str(value)

    if len(payload) <= limit:
        return payload

    # итеративно ужимаем, пока JSON не влезет (без mid-string cut)
    row_caps = [20, 15, 12, 10, 8, 6, 4, 3, 2, 1, 0]
    compact: Any = value
    for cap in row_caps:
        compact = _shrink_value(value, limit, tool_name=tool_name, rows_cap=cap)
        try:
            payload = json.dumps(compact, ensure_ascii=False, default=str)
        except Exception:
            payload = str(compact)
        if len(payload) <= limit:
            return payload

    # универсальный рекурсивный shrink: списки/строки на любой глубине, ключи не теряются
    if isinstance(value, (dict, list)):
        generic = _generic_shrink(value, limit)
        if generic is not None:
            return generic

    # последний шанс: только priority + table_preview + structure_brief + мало rows
    if isinstance(value, dict):
        emergency: dict[str, Any] = {
            "ok": value.get("ok"),
            "error": value.get("error"),
            "need_clarification": value.get("need_clarification"),
            "question": value.get("question"),
            "manager_summary": value.get("manager_summary"),
            "table_preview": value.get("table_preview"),
            "structure_brief": value.get("structure_brief"),
            "prefer": value.get("prefer"),
            "full_name": value.get("full_name"),
            "primary": value.get("primary") or value.get("full_name"),
            "row_count": value.get("row_count") or value.get("rows_count"),
            "path": value.get("path") or value.get("rel_path"),
            "start_line": value.get("start_line"),
            "end_line": value.get("end_line"),
            "text": (
                (value.get("text") or value.get("content") or "")[:4000]
                if tool_name in ("read_code", "inspect_code", "read_skd_query")
                else None
            ),
            "symbols": (
                (value.get("symbols") or [])[:40]
                if tool_name == "module_outline"
                else None
            ),
            "matches": (
                (value.get("matches") or value.get("hits") or [])[:12]
                if tool_name in ("search_code", "find_callers", "trace_calls")
                else None
            ),
            "forms": value.get("forms") if tool_name in (
                "describe_object",
                "get_object_structure",
                "describe_form",
            ) else None,
            "templates": value.get("templates") if tool_name in (
                "describe_object",
                "get_object_structure",
            ) else None,
            "_truncated": True,
            "_truncate_note": (
                f"Сильное ужатие tool={tool_name or '?'}. "
                "Смотри text/symbols/matches/structure_brief/table_preview."
            ),
        }
        emergency = {k: v for k, v in emergency.items() if v is not None}
        rows = value.get("rows")
        if isinstance(rows, list) and rows:
            emergency["rows"] = rows[: min(5, len(rows))]
            emergency["rows_total"] = len(rows)
        elif isinstance(rows, dict) and isinstance(rows.get("items"), list):
            emergency["rows"] = rows["items"][:5]
            emergency["rows_total"] = rows.get("total")
        try:
            payload = json.dumps(emergency, ensure_ascii=False, default=str)
        except Exception:
            payload = json.dumps(
                {"ok": False, "error": "compact_failed", "preview": str(value)[:500]},
                ensure_ascii=False,
            )
        if len(payload) > limit:
            # валидный JSON-заглушка, не mid-string
            stub = {
                "ok": value.get("ok") if isinstance(value, dict) else None,
                "error": "result_too_large",
                "manager_summary": (
                    value.get("manager_summary") if isinstance(value, dict) else None
                ),
                "table_preview": (
                    (value.get("table_preview") or "")[: max(200, limit - 300)]
                    if isinstance(value, dict)
                    else None
                ),
                "_truncated": True,
            }
            payload = json.dumps(stub, ensure_ascii=False, default=str)[:limit]
            # если всё ещё обрезано — минимальный валидный объект
            try:
                json.loads(payload)
            except Exception:
                payload = json.dumps(
                    {
                        "ok": True,
                        "error": "truncated",
                        "hint": "Повтори tool с limit=10",
                    },
                    ensure_ascii=False,
                )
        return payload

    # non-dict fallback — валидный JSON string-обёртка
    return json.dumps(
        {"_truncated": True, "preview": str(value)[: max(100, limit - 80)]},
        ensure_ascii=False,
    )


_LONG_TEXT_KEYS = frozenset({"text", "content", "code", "body", "snippet", "query_text", "table_preview"})


def _dedupe_top(value: dict) -> dict:
    """Убрать дубли одного и того же списка под разными ключами (hits == data_source_hits и т.п.)."""
    out: dict[str, Any] = {}
    seen: dict[str, str] = {}
    for k, v in value.items():
        if isinstance(v, (list, dict)) and v:
            try:
                sig = json.dumps(v, ensure_ascii=False, default=str, sort_keys=True)
            except Exception:
                sig = None
            if sig and len(sig) > 300:
                if sig in seen:
                    out[f"{k}_same_as"] = seen[sig]
                    continue
                seen[sig] = k
        out[k] = v
    return out


def _rec_shrink(v: Any, k_items: int, str_len: int, key: str = "", depth: int = 0) -> Any:
    if isinstance(v, dict):
        out: dict[str, Any] = {}
        for kk, vv in v.items():
            if depth == 0 and kk in DROP_ON_SHRINK and len(v) > 3:
                continue
            out[kk] = _rec_shrink(vv, k_items, str_len, kk, depth + 1)
            if isinstance(vv, list) and len(vv) > _items_for(kk, k_items):
                out[f"{kk}_total"] = len(vv)
        return out
    if isinstance(v, list):
        n = _items_for(key, k_items)
        return [_rec_shrink(x, k_items, str_len, key, depth + 1) for x in v[:n]]
    if isinstance(v, str):
        lim = str_len * 4 if key in _LONG_TEXT_KEYS else str_len
        if len(v) > lim:
            return v[:lim] + "…"
    return v


def _items_for(key: str, k_items: int) -> int:
    # строки кода/outline ценнее — даём больше
    if key in ("lines", "symbols", "attributes_lines"):
        return k_items * 3
    return k_items


def _generic_shrink(value: Any, limit: int) -> str | None:
    """
    Гарантированно сходящееся ужатие: k и L уменьшаются геометрически;
    затем — выкидываем самые тяжёлые НЕприоритетные ключи верхнего уровня
    (с пометкой _dropped_keys, чтобы модель знала, что их нет).
    """
    base = _dedupe_top(value) if isinstance(value, dict) else value
    if isinstance(base, dict):
        # приоритетные ключи — первыми
        ordered = {k: base[k] for k in PRIORITY_KEYS if k in base}
        ordered.update({k: v for k, v in base.items() if k not in ordered})
        base = ordered
    best: Any = None
    levels = ((15, 1200), (10, 700), (6, 400), (4, 250), (3, 160), (2, 100), (1, 70))
    # тяжёлые «вспомогательные» ключи ужимаются в первую очередь, даже если они в PRIORITY_KEYS
    prio_set = set(PRIORITY_KEYS) - {"tabular_sections", "forms", "templates", "structure_brief", "default_forms", "form_names", "columns", "periods"}
    note = (
        "Ответ ужат ТОЛЬКО для экономии контекста: списки урезаны (в *_total — сколько их всего), длинные строки обрезаны «…». "
        "Это НЕ всё. Нужного нет среди показанного — не отвечай «нет такого»: запроси следующую порцию "
        "(offset/page, уточнение имени, read_code с start_line) и посмотри остальное."
    )

    def _build(sec_level: tuple[int, int], prio_level: tuple[int, int]) -> Any:
        if not isinstance(base, dict):
            return _rec_shrink(base, *sec_level)
        out: dict[str, Any] = {}
        for kk, vv in base.items():
            lvl = prio_level if kk in prio_set else sec_level
            sub = _rec_shrink({kk: vv}, *lvl)
            out.update(sub)
        out["_truncated"] = True
        out["_truncate_note"] = note
        return out

    # фаза 1: ужимаем только второстепенные ключи (приоритетные — на мягком уровне)
    # фаза 2: если не влезло — ужимаем и приоритетные
    plan = [(lv, levels[0]) for lv in levels] + [(levels[-1], lv) for lv in levels[1:]]
    for sec_level, prio_level in plan:
        cand = _build(sec_level, prio_level)
        try:
            payload = json.dumps(cand, ensure_ascii=False, default=str)
        except Exception:
            return None
        best = cand
        if len(payload) <= limit:
            return payload
    if not isinstance(best, dict):
        return None
    prio = set(PRIORITY_KEYS) | {"_truncated", "_truncate_note"}
    dropped: list[str] = []
    sizes = sorted(
        ((len(json.dumps(v, ensure_ascii=False, default=str)), k) for k, v in best.items() if k not in prio),
        reverse=True,
    )
    for _, k in sizes:
        best.pop(k, None)
        dropped.append(k)
        best["_dropped_keys"] = dropped
        payload = json.dumps(best, ensure_ascii=False, default=str)
        if len(payload) <= limit:
            return payload
    # приоритетные ключи тоже по размеру, но ok/error/question/options — никогда
    keep = {"ok", "error", "hint", "need_clarification", "question", "options", "full_name", "_truncated", "_dropped_keys"}
    sizes = sorted(
        ((len(json.dumps(v, ensure_ascii=False, default=str)), k) for k, v in best.items() if k not in keep),
        reverse=True,
    )
    for _, k in sizes:
        best.pop(k, None)
        dropped.append(k)
        payload = json.dumps(best, ensure_ascii=False, default=str)
        if len(payload) <= limit:
            return payload
    return None


def _shrink_value(
    value: Any,
    max_chars: int,
    *,
    tool_name: str | None = None,
    rows_cap: int | None = None,
) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k in PRIORITY_KEYS:
            if k in value and k not in DROP_ON_SHRINK:
                out[k] = value[k]

        # structure: attributes_lines целиком важнее fields
        if tool_name in ("get_object_structure", "describe_object", "list_object_attributes"):
            if "attributes_lines" in value and "attributes_lines" not in out:
                lines = value["attributes_lines"]
                if isinstance(lines, list):
                    out["attributes_lines"] = lines
                else:
                    out["attributes_lines"] = lines

        data_tools = {
            "linked_query",
            "build_report",
            "compare_periods",
            "summarize_periods",
            "analyst_report",
            "report_builder",
            "margin_report",
            "execute_query",
        }
        for k, v in value.items():
            if k in out or k in DROP_ON_SHRINK:
                continue
            if k in (
                "data",
                "rows",
                "results",
                "candidates",
                "objects",
                "fields",
                "attributes",
                "symbols",
                "matches",
                "hits",
                "lines",
            ):
                if k == "rows" and isinstance(v, list):
                    cap = (
                        rows_cap
                        if rows_cap is not None
                        else _list_cap_for_key(k, tool_name)
                    )
                    # data-tools: rows — обычный list (не {items:…}), модели проще
                    if tool_name in data_tools or rows_cap is not None:
                        if len(v) > cap:
                            out[k] = v[:cap]
                            out["rows_total"] = len(v)
                            out["rows_shown"] = cap
                        else:
                            out[k] = v
                    else:
                        out[k] = _shrink_period_aware_rows(v, max_items=cap)
                elif k == "symbols" and isinstance(v, list):
                    # outline: cap symbols, keep name+line
                    cap = 50 if rows_cap is None else max(20, rows_cap)
                    slim = []
                    for s in v[:cap]:
                        if isinstance(s, dict):
                            slim.append(
                                {
                                    "line": s.get("line"),
                                    "type": s.get("type"),
                                    "name": s.get("name"),
                                }
                            )
                        else:
                            slim.append(s)
                    out[k] = slim
                    if len(v) > cap:
                        out["symbols_total"] = len(v)
                elif k == "lines" and isinstance(v, list):
                    cap = 80 if rows_cap is None else max(20, rows_cap * 4)
                    out[k] = v[:cap]
                elif k in ("matches", "hits") and isinstance(v, list):
                    cap = 15 if rows_cap is None else max(5, rows_cap)
                    out[k] = v[:cap]
                else:
                    out[k] = _shrink_list(v, max_items=_list_cap_for_key(k, tool_name))
            elif k in ("text", "content", "code") and isinstance(v, str) and len(v) > 6000:
                out[k] = v[:6000] + "\n…[truncated]"
            elif k in (
                "header_fields",
                "header_field_names",
                "all_fields_sample",
                "parsed_header_names",
                "attributes_table",
            ):
                out[k] = _shrink_list(v, max_items=50 if k == "attributes_table" else 40)
            elif k == "tabular_sections":
                out[k] = _shrink_tabular(v)
            elif k == "queries":
                out[k] = _shrink_list(v, max_items=4)
            elif k in ("registers", "matches", "findings", "recommendations"):
                out[k] = _shrink_list(v, max_items=25)
            elif k == "query" and isinstance(v, str) and len(v) > 400:
                out[k] = v[:400] + "…"
            elif k == "prefer" and isinstance(v, str) and len(v) > 900:
                out[k] = v[:900] + "…"
            else:
                out[k] = v

        out["_truncated"] = True
        out["_truncate_note"] = (
            f"Ответ ужат (~{max_chars} симв., tool={tool_name or '?'}). "
            "Покажи table_preview / rows / manager_summary. Не выдумывай пустую таблицу."
        )
        return out

    if isinstance(value, list):
        return _shrink_list(value, max_items=30)

    s = str(value)
    if len(s) > max_chars:
        return s[: max_chars - 20] + "…[truncated]"
    return value


def _list_cap_for_key(key: str, tool_name: str | None = None) -> int:
    if key in ("data", "rows"):
        if tool_name in (
            "compare_periods",
            "summarize_periods",
            "build_report",
            "linked_query",
            "analyst_report",
            "report_builder",
        ):
            return 18
        return 20
    if key in ("results", "candidates", "fields"):
        return 30
    if key in ("objects", "attributes"):
        return 12 if tool_name == "linked_query" else 60
    if key == "matches":
        return 15
    return 25


def _shrink_list(value: Any, max_items: int) -> Any:
    if not isinstance(value, list):
        return value
    if len(value) <= max_items:
        return value
    head = value[:max_items]
    return {
        "items": head,
        "shown": max_items,
        "total": len(value),
        "truncated": True,
        "note": "Показана часть списка (shown из total) — только для экономии контекста. Остальное запроси следующей порцией "
                "(offset/page или уточнение); отсутствие в показанном не значит отсутствие в данных.",
    }


def _shrink_period_aware_rows(value: list, max_items: int) -> Any:
    """При multi-period rows чередуем периоды, а не режем «голову = один год»."""
    if not isinstance(value, list) or len(value) <= max_items:
        return value
    periods: list[str] = []
    by_p: dict[str, list] = {}
    rest: list = []
    for r in value:
        if not isinstance(r, dict):
            rest.append(r)
            continue
        lab = str(r.get("Период") or r.get("period") or "").strip()
        if not lab:
            rest.append(r)
            continue
        if lab not in by_p:
            by_p[lab] = []
            periods.append(lab)
        by_p[lab].append(r)
    if len(periods) < 2:
        return _shrink_list(value, max_items=max_items)

    cap_each = max(2, max_items // len(periods))
    out: list = []
    idxs = {p: 0 for p in periods}
    while len(out) < max_items:
        progressed = False
        for p in periods:
            rs = by_p[p]
            i = idxs[p]
            if i >= cap_each or i >= len(rs):
                continue
            out.append(rs[i])
            idxs[p] = i + 1
            progressed = True
            if len(out) >= max_items:
                break
        if not progressed:
            break
    for r in rest:
        if len(out) >= max_items:
            break
        out.append(r)
    return {
        "items": out,
        "shown": len(out),
        "total": len(value),
        "truncated": True,
        "periods_kept": periods,
        "note": "rows чередованы по Период (оба/все периоды в context)",
    }


def _shrink_tabular(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    out = []
    for ts in value[:15]:
        if not isinstance(ts, dict):
            out.append(ts)
            continue
        out.append(
            {
                "name": ts.get("name"),
                "synonym": ts.get("synonym"),
                "attributes": _shrink_list(ts.get("attributes") or [], max_items=12),
            }
        )
    if len(value) > 15:
        return {"items": out, "total": len(value), "truncated": True}
    return out


def summarize_tool_payload(content: str, *, max_chars: int = DEFAULT_OLD_TOOL_CHARS) -> str:
    """C: старый tool result → короткий summary (без полного JSON)."""
    if not content or len(content) <= max_chars:
        return content
    summary: dict[str, Any] = {"_old_tool_summary": True}
    try:
        # обрезанный хвост truncate marker
        raw = content
        if "…[truncated" in raw:
            raw = raw.split("…[truncated")[0]
        data = json.loads(raw)
        if isinstance(data, dict):
            for k in (
                "ok",
                "error",
                "full_name",
                "need_clarification",
                "question",
                "period_spec",
                "group_by",
                "count",
                "row_count",
                "attribute_count",
                "prefer",
                "note",
                "filter",
                "from_dialog_filter",
            ):
                if k in data and data[k] is not None:
                    v = data[k]
                    if isinstance(v, str) and len(v) > 200:
                        v = v[:200] + "…"
                    summary[k] = v
            ms = data.get("manager_summary")
            if isinstance(ms, dict):
                summary["manager_summary"] = {
                    kk: ms.get(kk)
                    for kk in ("object", "period", "periods", "row_count", "message")
                    if ms.get(kk) is not None
                }
            sbp = data.get("summary_by_period")
            if isinstance(sbp, list):
                summary["summary_by_period"] = sbp[:8]
            periods = data.get("periods")
            if isinstance(periods, list):
                labs = []
                for p in periods[:8]:
                    if isinstance(p, dict) and p.get("label"):
                        labs.append(p["label"])
                    elif isinstance(p, str):
                        labs.append(p)
                if labs:
                    summary["period_labels"] = labs
            regs = data.get("register_names_full") or data.get("registers_all")
            if isinstance(regs, list):
                summary["registers_n"] = len(regs)
                summary["registers_sample"] = regs[:12]
            if data.get("rows") is not None:
                rows = data["rows"]
                if isinstance(rows, dict) and "total" in rows:
                    summary["rows_total"] = rows.get("total")
                    summary["rows_shown"] = rows.get("shown")
                elif isinstance(rows, list):
                    summary["rows_n"] = len(rows)
            opts = data.get("options")
            if isinstance(opts, list):
                summary["options"] = opts[:6]
    except Exception:
        summary["preview"] = content[: max_chars - 40]
        return json.dumps(summary, ensure_ascii=False)[:max_chars]

    try:
        s = json.dumps(summary, ensure_ascii=False, default=str)
    except Exception:
        s = str(summary)
    if len(s) > max_chars:
        s = s[: max_chars - 20] + "…[old_tool]"
    return s


def split_into_turns(rest: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """
    Ход = user (не nudge-only? ) + следующие assistant/tool до следующего «реального» user.
    Nudge messages (role=user с [agent]/preflight) остаются внутри хода.
    Новый ход — user, который не похож на системный nudge.
    """
    if not rest:
        return []
    turns: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []

    def is_turn_start(m: dict[str, Any]) -> bool:
        if m.get("role") != "user":
            return False
        c = (m.get("content") or "") if isinstance(m.get("content"), str) else ""
        # внутренние nudge не начинают ход
        markers = (
            "[контекст диалога]",
            "[память сессии]",
            "СТОП.",
            "ЗАПРЕЩЕНО",
            "Лимит вызовов tools",
            "Это фильтр ПРЕДЫДУЩЕГО",
            "Это ответ по ДАННЫМ",
            "Результат с ошибкой",
            "Нужно уточнение у пользователя",
            "Уже выполнен tool",
            "Structure получен",
            "Ответ менеджеру СТРОГО",
            "Обязательно вызови",
            "ОБЯЗАТЕЛЬНО вызови",
        )
        cl = c.strip()
        if any(cl.startswith(x) for x in markers):
            return False
        # preflight / agent nudges
        if "(preflight)" in cl[:120] or cl.startswith("[preflight"):
            return False
        if cl.startswith("Tool "):
            return False
        if "Сформулируй manager_summary" in cl[:120]:
            return False
        if "Если compare_periods" in cl[:120]:
            return False
        return True

    for m in rest:
        if is_turn_start(m) and cur:
            turns.append(cur)
            cur = [m]
        else:
            cur.append(m)
    if cur:
        turns.append(cur)
    return turns


def trim_messages(
    messages: list[dict[str, Any]],
    *,
    max_messages: int = DEFAULT_HISTORY_MAX_MESSAGES,
    max_total_chars: int | None = None,
    max_turns: int = DEFAULT_HISTORY_MAX_TURNS,
    keep_recent_tool_full: int = DEFAULT_KEEP_RECENT_TOOL_FULL,
    old_tool_chars: int = DEFAULT_OLD_TOOL_CHARS,
) -> list[dict[str, Any]]:
    """
    C: system + последние max_turns ходов.
    Старые tool (кроме keep_recent_tool_full последних) → summarize_tool_payload.
    Затем soft trim по max_messages / max_total_chars.
    """
    if not messages:
        return messages

    # не мутировать исходный список глубоко без копий content tool
    system = [m for m in messages if m.get("role") == "system"][:1]
    rest = [dict(m) for m in messages if m.get("role") != "system"]

    # --- по ходам ---
    turns = split_into_turns(rest)
    if max_turns > 0 and len(turns) > max_turns:
        turns = turns[-max_turns:]
    rest = [m for t in turns for m in t]

    if max_messages > 0 and len(rest) > max_messages:
        # режем с начала целыми сообщениями, не оставляя orphan tool
        while len(rest) > max_messages:
            rest.pop(0)
            while rest and rest[0].get("role") == "tool":
                rest.pop(0)

    # --- tool: полные только последние keep_recent_tool_full ---
    tool_indices = [i for i, m in enumerate(rest) if m.get("role") == "tool"]
    full_set = set(tool_indices[-keep_recent_tool_full:]) if keep_recent_tool_full > 0 else set()
    for i, m in enumerate(rest):
        if m.get("role") != "tool":
            continue
        if i in full_set:
            continue
        content = m.get("content")
        if isinstance(content, str) and len(content) > old_tool_chars:
            m["content"] = summarize_tool_payload(content, max_chars=old_tool_chars)

    if max_total_chars is None:
        return system + rest

    # ужесточаем: ещё раз ужать tool с головы, потом drop старых ходов
    def total() -> int:
        return messages_total_chars(system + rest)

    for m in rest:
        if total() <= max_total_chars:
            break
        if m.get("role") == "tool" and isinstance(m.get("content"), str):
            if len(m["content"]) > 400:
                m["content"] = summarize_tool_payload(m["content"], max_chars=400)

    while len(rest) > 6 and total() > max_total_chars:
        rest.pop(0)
        while rest and rest[0].get("role") == "tool":
            rest.pop(0)

    # assistant narrative можно чуть укоротить если всё ещё жмёт
    for m in rest:
        if total() <= max_total_chars:
            break
        if m.get("role") == "assistant" and isinstance(m.get("content"), str):
            c = m["content"]
            if len(c) > 1500 and not m.get("tool_calls"):
                m["content"] = c[:1200] + "…[answer_trimmed]"

    return system + rest


def budget_pressure(
    messages: list[dict[str, Any]],
    *,
    max_total_chars: int,
) -> float:
    """0..1+ доля заполнения char budget."""
    if max_total_chars <= 0:
        return 0.0
    return messages_total_chars(messages) / float(max_total_chars)
