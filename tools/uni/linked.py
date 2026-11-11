# -*- coding: utf-8 -*-
"""linked_query: долги, остатки, обороты, несколько источников."""

from __future__ import annotations

import re
from typing import Any


class LinkedMixin:
    def linked_query(
        self,
        request: str = "",
        sources: str = "",
        fields_needed: str = "",
        as_of: str = "",
        limit: int = 40,
        offset: int = 0,
        page: int = 0,
        execute: bool = True,
    ) -> dict[str, Any]:
        """
        Связка документов/регистров → ВЫБРАТЬ (вирт. таблицы, без вложенных SELECT).
        Универсально для любой CF.
        """
        from tools.linked_query import plan_linked_query

        def _resolve(phrase: str) -> dict[str, Any]:
            from tools.linked_query import is_linked_or_debt_data_question

            # find_metadata wide
            out: dict[str, Any] = {"items": []}
            try:
                m = self.find_metadata(phrase, "Все", 12)
                if isinstance(m, dict):
                    out["items"] = list(m.get("items") or [])
            except Exception:
                pass
            # долг/остатки/обороты: искать регистры по стемам фразы (не full_name CF)
            from tools.linked_query import (
                _wants_period_turnovers as _wants_turn,
            )

            need_regs = is_linked_or_debt_data_question(phrase) or _wants_turn(phrase)
            if need_regs:
                stems = re.findall(r"[а-яa-zё]{4,}", (phrase or "").lower().replace("ё", "е"))
                stop = {
                    "нужно",
                    "покажи",
                    "данные",
                    "текущ",
                    "конец",
                    "начало",
                    "какой",
                    "какие",
                    "остаток",
                    "остатки",
                    "оборот",
                    "обороты",
                    "помесяч",
                    "месяц",
                }
                queries = [s for s in stems if s not in stop][:6]
                for stem in (
                    "расчет",
                    "взаиморасчет",
                    "задолжен",
                    "оплат",
                    "товар",
                    "склад",
                    "выручк",
                    "продаж",
                ):
                    if stem not in queries:
                        queries.append(stem)
                for q in queries:
                    try:
                        m = self.find_metadata(q, "РегистрыНакопления", 12)
                        if isinstance(m, dict):
                            for it in m.get("items") or []:
                                out.setdefault("items", []).append(it)
                    except Exception:
                        pass
            try:
                r = self.resolve_object(phrase, "Все", 8)
                if isinstance(r, dict):
                    if r.get("full_name"):
                        out["full_name"] = r["full_name"]
                    for it in r.get("options") or r.get("items") or []:
                        if isinstance(it, dict) and it.get("full_name"):
                            out.setdefault("items", []).append(it)
            except Exception:
                pass
            return out

        def _struct(fn: str) -> dict[str, Any]:
            try:
                return self.get_object_structure(fn)
            except Exception as e:
                return {"error": str(e), "full_name": fn}

        plan = plan_linked_query(
            request=request or "",
            sources=sources or "",
            fields_needed=fields_needed or "",
            as_of=as_of or "",
            limit=int(limit or 40),
            resolve_fn=_resolve,
            structure_fn=_struct,
        )
        if not plan.get("ok"):
            return plan
        if not execute:
            return plan
        q = plan.get("query") or ""
        if not q:
            return plan
        lim_i = int(limit or 40)
        off_i = int(offset or 0)
        page_i = int(page or 0)
        result = self.execute_query(
            q, limit=lim_i, offset=off_i, page=page_i
        )
        rows = result.get("rows") if isinstance(result, dict) else None
        err_t = str((result or {}).get("error") or "") if isinstance(result, dict) else ""
        # error / 0 rows → alt_plans, затем balances по другим регистрам из objects
        if isinstance(result, dict) and (
            result.get("error") or (isinstance(rows, list) and len(rows) == 0 and off_i == 0)
        ):
            for alt in plan.get("alt_plans") or []:
                aq = alt.get("query") or ""
                if not aq or aq == q:
                    continue
                r2 = self.execute_query(
                    aq, limit=lim_i, offset=off_i, page=page_i
                )
                if isinstance(r2, dict) and not r2.get("error") and r2.get("rows"):
                    result = r2
                    plan["query_used"] = aq
                    plan["retried_alt"] = alt.get("role")
                    break
            # virtual table missing → try other accum registers from plan objects
            if isinstance(result, dict) and (
                result.get("error")
                and ("не найдена" in err_t.lower() or "таблица" in err_t.lower())
            ):
                from tools.linked_query import build_balances_query

                for fn in plan.get("objects") or []:
                    if not str(fn).startswith("РегистрНакопления."):
                        continue
                    if fn in (plan.get("primary") or ""):
                        continue
                    st = _struct(fn)
                    from tools.linked_query import _field_names_from_structure

                    fields = _field_names_from_structure(st if isinstance(st, dict) else {})
                    as_of_retry = plan.get("as_of")
                    if not as_of_retry:
                        from datetime import datetime as _dt_now

                        n = _dt_now.now()
                        as_of_retry = (
                            f"ДАТАВРЕМЯ({n.year},{n.month},{n.day},"
                            f"{n.hour},{n.minute},{n.second})"
                        )
                    bq = build_balances_query(
                        fn,
                        fields,
                        as_of=as_of_retry,
                        limit=lim_i,
                    )
                    aq = bq.get("query") or ""
                    if not aq:
                        continue
                    r2 = self.execute_query(
                        aq, limit=lim_i, offset=off_i, page=page_i
                    )
                    if isinstance(r2, dict) and not r2.get("error") and r2.get("rows"):
                        result = r2
                        plan["query_used"] = aq
                        plan["retried_register"] = fn
                        plan["query"] = aq
                        break
                err_t = str((result or {}).get("error") or "") if isinstance(result, dict) else ""
        if isinstance(result, dict) and result.get("rows") is not None:
            plan["rows"] = result.get("rows")
            plan["row_count"] = len(result.get("rows") or [])
            # проброс paging из execute_query
            if result.get("paging"):
                plan["paging"] = result["paging"]
                for k in (
                    "limit",
                    "offset",
                    "page",
                    "has_more",
                    "next_offset",
                    "next_page",
                    "total",
                    "returned",
                ):
                    if k in result:
                        plan[k] = result[k]
            # не дублировать rows в execute_result (раздувает compact_json)
            if result.get("error"):
                plan["execute_error"] = result.get("error")
        else:
            # ошибка / пусто — оставить краткий execute_result
            if isinstance(result, dict):
                plan["execute_result"] = {
                    k: result.get(k)
                    for k in ("ok", "error", "hint", "query", "row_count")
                    if result.get(k) is not None
                }
            else:
                plan["execute_result"] = result
        # objects_detailed — тяжёлый dump structure; LLM не нужен при готовых rows
        if plan.get("rows") and plan.get("objects_detailed"):
            plan["objects"] = [
                o.get("full_name") if isinstance(o, dict) else o
                for o in (plan.get("objects") or [])
            ][:8] or plan.get("objects")
            plan.pop("objects_detailed", None)
        if plan.get("rows") and plan.get("alt_plans"):
            # оставить только role/primary без query
            slim_alts = []
            for a in (plan.get("alt_plans") or [])[:4]:
                if isinstance(a, dict):
                    slim_alts.append(
                        {
                            "role": a.get("role"),
                            "primary": a.get("primary"),
                        }
                    )
            plan["alt_plans"] = slim_alts
        try:
            from tools.report_format import enrich_report_result

            primary_fn = (
                plan.get("primary")
                or plan.get("retried_register")
                or (
                    (plan.get("objects") or [None])[0]
                    if plan.get("objects")
                    else ""
                )
                or ""
            )
            if isinstance(primary_fn, dict):
                primary_fn = primary_fn.get("full_name") or ""
            plan = enrich_report_result(
                plan,
                request=request or "",
                full_name=str(primary_fn)[:200],
                period=str(plan.get("as_of") or plan.get("year") or ""),
                report_mode=plan.get("plan_role") or "linked_query",
            )
            # список кандидатов — отдельно, не в object
            if plan.get("objects") and len(plan.get("objects") or []) > 1:
                plan.setdefault("manager_summary", {})
                if isinstance(plan["manager_summary"], dict):
                    objs = plan.get("objects") or []
                    plan["manager_summary"]["candidates"] = [
                        (o.get("full_name") if isinstance(o, dict) else o)
                        for o in objs[:8]
                    ]
        except Exception:
            plan["prefer"] = (
                "Покажи таблицу rows / table_preview менеджеру. Не объясняй JOIN/ВТ. "
                f"Источники: {', '.join(str(x) for x in (plan.get('objects') or []))}."
            )
        return plan
