# -*- coding: utf-8 -*-
"""Выполнение запросов 1С (только ВЫБРАТЬ)."""

from __future__ import annotations

import re
from typing import Any

from tools.query_1c_normalize import normalize_to_1c_query
from tools.responses import err, only_rows, pick_rows_keys
from tools.uni.safety import is_safe_select_query


class QueryMixin:
    def _prepare_1c_query(self, query: str) -> dict[str, Any]:
        """ANSI SQL → язык 1С + запрет SELECT * и выдуманных таблиц."""
        from tools.query_1c_normalize import EXAMPLE_1C_DOCUMENT_PERIOD

        norm = normalize_to_1c_query(query or "")
        if norm.get("error"):
            out = {
                "error": norm["error"],
                "hint": norm.get("hint") or EXAMPLE_1C_DOCUMENT_PERIOD,
                "original_query": (query or "")[:400],
                "normalized_query": (norm.get("query") or "")[:400],
            }
            if norm.get("prefer_tool"):
                out["prefer_tool"] = norm["prefer_tool"]
            if norm.get("placeholders"):
                out["placeholders"] = norm["placeholders"]
            return out

        q = norm["query"]
        if self.read_only:
            ok, reason = is_safe_select_query(q)
            if not ok:
                return {
                    "error": reason,
                    "hint": EXAMPLE_1C_DOCUMENT_PERIOD,
                    "original_query": (query or "")[:400],
                    "normalized_query": q[:400],
                }
        return {
            "query": q,
            "converted": norm.get("converted"),
            "warnings": norm.get("warnings") or [],
        }

    def execute_query(
        self,
        query: str,
        limit: int | None = None,
        offset: int = 0,
        page: int = 0,
    ) -> dict[str, Any]:
        """
        limit — сколько строк вернуть (модель).
        offset/page — следующая порция (1С: ПЕРВЫЕ offset+limit, срез в Python).
        """
        from tools.paging import (
            attach_paging,
            clamp_limit,
            fetch_limit_for_offset,
            resolve_offset,
        )

        lim = clamp_limit(
            limit,
            default=self.default_limit or 25,
            maximum=max(int(self.max_limit or 80), 200),
        )
        off = resolve_offset(
            offset=offset, page=page if page else None, limit=lim
        )
        fetch_n = fetch_limit_for_offset(
            lim, off, max_fetch=max(500, lim + off)
        )
        # Менеджерские отчёты — не через «голый» SQL (плейсхолдеры/кривые даты)
        q0 = (query or "").strip()
        if q0 and not q0.upper().startswith("ВЫБРАТЬ") and not q0.upper().startswith("SELECT"):
            # иногда модель шлёт фразу вместо query
            from tools.report_format import is_manager_report_question
            from tools.linked_query import is_linked_or_debt_data_question

            if is_manager_report_question(q0) or is_linked_or_debt_data_question(q0):
                return {
                    "error": "Для отчёта по фразе используйте build_report или linked_query, не execute_query.",
                    "prefer_tool": "build_report",
                    "action_for_agent": (
                        f"Вызови build_report(request={q0!r}) "
                        "или linked_query(request=…, execute=true) для долгов/остатков."
                    ),
                    "hint": "execute_query — только готовый ВЫБРАТЬ с именами из structure.",
                }
        prep = self._prepare_1c_query(query)
        if prep.get("error"):
            out = err(str(prep["error"]), hint=prep.get("hint"))
            if prep.get("original_query"):
                out["original_query"] = prep["original_query"]
            if prep.get("prefer_tool"):
                out["prefer_tool"] = prep["prefer_tool"]
            if prep.get("placeholders"):
                out["placeholders"] = prep["placeholders"]
                out["action_for_agent"] = (
                    "ОСТАНОВИСЬ. Не повторяй execute_query с ИмяДокумента. "
                    "Вызови build_report(request=фраза пользователя) "
                    "или quick_find → реальный full_name."
                )
            return out

        q = prep["query"]
        # Поднять ПЕРВЫЕ N до offset+limit (язык 1С без OFFSET)
        import re as _re

        def _bump_pervye(text: str, n: int) -> str:
            # 1С: ВЫБРАТЬ [РАЗРЕШЕННЫЕ] [РАЗЛИЧНЫЕ] [ПЕРВЫЕ n] — ПЕРВЫЕ строго после модификаторов
            head = r"^(\s*ВЫБРАТЬ(?:\s+(?:РАЗРЕШЕННЫЕ|РАЗЛИЧНЫЕ)\b)*)"
            if _re.match(head + r"\s+ПЕРВЫЕ\s+\d+", text, _re.I):
                return _re.sub(
                    head + r"\s+ПЕРВЫЕ\s+\d+",
                    rf"\1 ПЕРВЫЕ {n}",
                    text,
                    count=1,
                    flags=_re.I,
                )
            if _re.match(head + r"(?=\s|$)", text, _re.I):
                return _re.sub(head, rf"\1 ПЕРВЫЕ {n}", text, count=1, flags=_re.I)
            return text

        q_fetch = _bump_pervye(q, fetch_n)
        result = self.backend.execute_query(q_fetch, fetch_n)
        # retry: параметры периода / компактные даты
        if isinstance(result, dict) and result.get("error"):
            err_t = str(result.get("error") or "")
            low = err_t.lower()
            if "не задано значение параметра" in low or (
                "синтаксическ" in low and re.search(r"20\d{6,}", q)
            ):
                from tools.query_1c_normalize import normalize_to_1c_query

                # force re-normalize (params) and retry once
                n2 = normalize_to_1c_query(q)
                q2 = (n2.get("query") or q).strip()
                if q2 and q2 != q:
                    result2 = self.backend.execute_query(_bump_pervye(q2, fetch_n), fetch_n)
                    if isinstance(result2, dict) and not result2.get("error"):
                        result = result2
                        q = q2
                        prep = dict(prep)
                        prep["converted"] = True
                        prep["warnings"] = list(prep.get("warnings") or []) + list(
                            n2.get("warnings") or []
                        ) + ["auto-retry after date/param normalize"]
        slim = only_rows(result, max_rows=fetch_n)
        if "rows" in slim and isinstance(slim["rows"], list):
            all_rows = pick_rows_keys(slim["rows"], max_cols=12)
            # all_rows = первые fetch_n=offset+limit строк запроса (1С без OFFSET)
            # порция: all_rows[off : off+lim]
            page_rows = all_rows[off : off + lim]
            maybe_more = (
                len(all_rows) >= fetch_n
                or bool(slim.get("truncated"))
                or bool(result.get("truncated") if isinstance(result, dict) else False)
            )
            page_meta = {
                "limit": lim,
                "offset": off,
                "page": (off // lim) + 1 if lim else 1,
                "returned": len(page_rows),
                "has_more": bool(maybe_more and len(page_rows) >= lim),
                "next_offset": (off + len(page_rows))
                if (maybe_more and len(page_rows) >= lim)
                else None,
                "next_page": ((off // lim) + 2)
                if (maybe_more and len(page_rows) >= lim and lim)
                else None,
                "total": (off + len(all_rows) + (1 if maybe_more else 0))
                if maybe_more
                else (off + len(all_rows)),
                "total_is_min": bool(maybe_more),
                "prefer_paging": (
                    "limit — сколько строк за раз (ты выбираешь). "
                    "has_more → execute_query(..., offset=next_offset, limit=…). "
                    "В 1С нет OFFSET: внутри запрашивается ПЕРВЫЕ offset+limit."
                ),
            }
            slim["rows"] = page_rows
            slim = attach_paging(slim, page_meta)
            slim["fetched"] = len(all_rows)
        if prep.get("converted") or prep.get("warnings"):
            slim["query_normalized"] = True
            slim["warnings"] = prep.get("warnings")
            slim["used_query"] = q_fetch[:400]
        # если 1С вернула синтаксическую ошибку — подсказка про язык 1С
        if isinstance(slim, dict) and slim.get("error"):
            err_t = str(slim.get("error", ""))
            low = err_t.lower()
            if (
                "синтаксическ" in low
                or re.search(r"(19|20)\d{2}-\d", err_t)
                or "неизвестн" in low
                or "не задано значение параметра" in low
                or "поле" in low
                and "не найден" in low
            ):
                slim["hint"] = (
                    "build_report / linked_query(request=…). "
                    "Даты: ДАТАВРЕМЯ. Остатки: РегистрНакопления.X.Остатки(Дата,). "
                    "Поля — только из get_object_structure dimensions/resources/fields."
                )
                slim["prefer_tool"] = "linked_query"
                slim["used_query"] = q_fetch[:400]
                slim["action_for_agent"] = (
                    "Не повторяй тот же query. "
                    "linked_query(request=фраза) или structure регистра + .Остатки."
                )
        # 0 rows — подсказка retry
        if (
            isinstance(slim, dict)
            and not slim.get("error")
            and isinstance(slim.get("rows"), list)
            and len(slim["rows"]) == 0
        ):
            if off > 0:
                slim["hint"] = (
                    f"Порция offset={off} пуста. Уменьши offset или смени фильтр."
                )
            else:
                slim["hint"] = (
                    "0 строк: смени период; проверь Проведен; "
                    "для остатков — linked_query / .Остатки + ненулевой ресурс; "
                    "поле Контрагент vs Партнер — из structure."
                )
            slim["action_for_agent"] = (
                "Попробуй linked_query / другой год / offset=0, не сдавайся сразу."
            )
        return slim
