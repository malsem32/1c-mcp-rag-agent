# -*- coding: utf-8 -*-
"""Отчёты по данным: build_report, report_builder (Excel/графики), маржа, аналитика."""

from __future__ import annotations

import re
from typing import Any

from tools.responses import need_clarification


class ReportsMixin:
    def margin_report(
        self,
        request: str = "",
        period: str = "",
        group_by: str = "менеджер",
        exclude_returns: bool = True,
        limit: int = 50,
    ) -> dict[str, Any]:
        from tools.margin_report import build_and_run_margin_report

        def _fm(q: str, otype: str = "РегистрыНакопления", lim: int = 8) -> dict:
            return self.find_metadata(q, otype, lim)

        return build_and_run_margin_report(
            request=request or "",
            period=period or "",
            group_by=group_by or "менеджер",
            exclude_returns=bool(exclude_returns),
            limit=int(limit or 50),
            find_metadata_fn=_fm,
            structure_fn=self.get_object_structure,
            execute_fn=self.execute_query,
        )

    def report_builder(
        self,
        request: str = "",
        full_name: str = "",
        period: str = "",
        group_by: str = "",
        metrics: str = "",
        value_field: str = "",
        limit: int = 50,
        offset: int = 0,
        to_excel: bool = False,
        filename: str = "",
        title: str = "",
        dry_run: bool = False,
        skip_wizard: bool = False,
        wizard_answer: str = "",
        charts_spec: str = "",
        filters: str = "",
        sort: str = "",
        time_grain: str = "",
    ) -> dict[str, Any]:
        """
        Универсальный построитель отчётов (любая CF).

        «Как» строить берётся из фразы (по месяцам, топ-5 худших, только по складу X,
        где сумма > 1000, по алфавиту, средний чек); filters/sort/time_grain — явное переопределение.

        Excel + диаграммы → мастер по шагам (поля, даты, фильтры, типы графиков),
        затем build. skip_wizard=true — сразу без вопросов.
        wizard_answer — ответ на текущий шаг мастера.
        """
        from tools.report_wizard import (
            apply_answer,
            answers_to_builder_kwargs,
            default_charts_spec,
            prefill_wizard_from_context,
            start_wizard,
            wants_excel_chart_wizard,
            wants_step_wizard,
            wizard_result_payload,
        )
        from tools.analyst_core import wants_excel_file, wants_excel_charts
        from tools.dialog_context import (
            export_snapshot_to_excel,
            has_reusable_report,
            wants_export_existing,
            wants_full_list_followup,
        )

        raw = (request or wizard_answer or "").strip()
        last_snap = getattr(self, "_last_report_snapshot", None)
        if not isinstance(last_snap, dict):
            last_snap = None

        # AUTO by default: отчёт с графиком = сам найти данные и собрать Excel
        wants_charts = (
            wants_excel_charts(raw)
            or wants_excel_chart_wizard(raw)
            or wants_export_existing(raw)
            or to_excel
        )
        force_excel = bool(
            to_excel
            or wants_excel_file(raw)
            or wants_excel_charts(raw)
            or wants_excel_chart_wizard(raw)
            or wants_export_existing(raw)
        )
        # мастер ТОЛЬКО если явно «по шагам» / skip_wizard=false + wants_step
        use_wizard = (not skip_wizard) and wants_step_wizard(raw) and not dry_run

        # AUTO-режим: любой «залипший» мастер сбрасываем (мастер только use_wizard)
        wiz = getattr(self, "_report_wizard", None)
        if isinstance(wiz, dict) and wiz.get("active") and not use_wizard:
            self._report_wizard = None
            wiz = None

        # «там 2 контрагента / полный список» — не отмахиваться, пересобрать отчёт
        if wants_full_list_followup(raw) and last_snap and not dry_run and not use_wizard:
            fn0 = str(last_snap.get("full_name") or full_name or "")
            per0 = str(
                last_snap.get("period_spec")
                or period
                or "за все время"
            )
            gb0 = str(last_snap.get("group_by") or group_by or "")
            req0 = (
                f"{fn0 or 'отчёт'}"
                + (f" по {gb0}" if gb0 else "")
                + f" за {per0} — полный список "
                f"(все группы, limit 200)"
            )
            out_full = self._report_builder_execute(
                request=req0,
                full_name=fn0,
                period=per0 if per0 else "за все время",
                group_by=gb0,
                metrics=metrics or "sum,count",
                limit=max(int(limit or 100), 200),
                to_excel=True,
                filename=filename or "",
                title=title or "",
            )
            if isinstance(out_full, dict) and (
                out_full.get("rows") or out_full.get("ok")
            ):
                out_full["full_list_retry"] = True
                out_full["prefer"] = (
                    (out_full.get("prefer") or "")
                    + "\nПользователь жаловался, что в чате было мало строк. "
                    "Покажи table_preview целиком + row_count. "
                    "Если есть Excel — path. Не говори «уже выгружено» без новой таблицы."
                )
                return out_full

        # --- reuse: уже есть rows в сессии ---
        try:
            short_reuse = has_reusable_report(last_snap) and (
                wants_export_existing(raw)
                or (
                    force_excel
                    and last_snap
                    and not re.search(r"\b(?:19|20)\d{2}\b", raw or "")
                    and len(raw) < 80
                    and not any(
                        x in (raw or "").lower()
                        for x in (
                            "другой",
                            "новые",
                            "вместо",
                            "заново",
                        )
                    )
                )
            )
            # «всем/полный» + excel: reuse ок, но prefer с явным count
            if short_reuse and last_snap and not dry_run and not use_wizard:
                rows = last_snap.get("rows") or []
                if rows:
                    out = export_snapshot_to_excel(
                        last_snap, phrase=raw, charts=True
                    )
                    if out.get("ok"):
                        n = int(out.get("reused_rows") or out.get("count") or len(rows))
                        out["prefer"] = (
                            f"Excel из прошлого отчёта: **{n} строк** (полный снимок сессии). "
                            "Скажи число строк явно. Если пользователь думал, что «было 2» — "
                            "в чате был урезанный preview, в файле полный список. "
                            "path/download_url обязательны."
                        )
                        return out
                if last_snap.get("full_name"):
                    per = str(
                        last_snap.get("period_spec")
                        or " и ".join(
                            str(p) for p in (last_snap.get("periods") or [])[:3]
                        )
                        or ""
                    )
                    gb = str(last_snap.get("group_by") or group_by or "")
                    req = raw or (
                        f"{last_snap.get('full_name')} за {per}"
                        + (f" по {gb}" if gb else "")
                    )
                    return self._report_builder_execute(
                        request=req,
                        full_name=str(last_snap.get("full_name") or full_name or ""),
                        period=per or period,
                        group_by=gb,
                        metrics=str(last_snap.get("metrics") or metrics or ""),
                        value_field=str(
                            last_snap.get("value_field") or value_field or ""
                        ),
                        limit=limit or 80,
                        offset=offset,
                        to_excel=True,
                        filename=filename or "report.xlsx",
                        title=title or str(last_snap.get("title") or "Отчёт")[:80],
                        dry_run=False,
                        charts_spec=default_charts_spec(raw),
                        filter_text="",
                        top_n=0,
                        wizard_answers={"source": "last_report"},
                    )
        except Exception:
            pass

        # --- опциональный мастер (только «по шагам») ---
        wiz = getattr(self, "_report_wizard", None)
        if isinstance(wiz, dict) and wiz.get("active") and use_wizard:
            ans = wizard_answer or raw
            try:
                wiz = prefill_wizard_from_context(
                    wiz, last_report=last_snap, session=None
                )
            except Exception:
                pass
            step_res = apply_answer(wiz, ans)
            st = step_res["state"]
            try:
                self._wizard_attach_schema(st)
            except Exception:
                pass
            from tools.report_wizard import format_step_question

            if not step_res.get("ready_to_build") and not step_res.get("cancel"):
                step_res["message"] = format_step_question(st)
            self._report_wizard = st
            if step_res.get("cancel"):
                self._report_wizard = None
                return {
                    "ok": True,
                    "wizard": True,
                    "cancelled": True,
                    "answer": step_res.get("message"),
                    "prefer": "Мастер отменён.",
                }
            if not step_res.get("ready_to_build"):
                return wizard_result_payload(
                    message=str(step_res.get("message") or ""),
                    state=st,
                    need_clarification=True,
                    ready=False,
                )
            kw = answers_to_builder_kwargs(st)
            if not kw.get("full_name") and st.get("resolved_full_name"):
                kw["full_name"] = st["resolved_full_name"]
            self._report_wizard = None
            return self._report_builder_execute(
                request=kw["request"],
                full_name=kw.get("full_name") or full_name,
                period=kw.get("period") or period,
                group_by=kw.get("group_by") or group_by,
                metrics=kw.get("metrics") or metrics,
                value_field=kw.get("value_field") or value_field,
                limit=limit or 80,
                offset=offset,
                to_excel=True,
                filename=kw.get("filename") or filename,
                title=kw.get("title") or title,
                dry_run=False,
                charts_spec=kw.get("charts_spec") or default_charts_spec(raw),
                filter_text=kw.get("filter_text") or "",
                top_n=int(kw.get("top_n") or 0),
                wizard_answers=kw.get("wizard_answers"),
            )

        if use_wizard and not dry_run:
            started = start_wizard(raw, last_report=last_snap)
            st0 = started.get("wizard_state") or {}
            try:
                self._wizard_attach_schema(st0)
            except Exception:
                pass
            self._report_wizard = st0
            return started

        # --- AUTO BUILD: сам резолв + запрос + excel + графики ---
        # подтянуть контекст сессии, если во фразе нет объекта/периода
        auto_fn = (full_name or "").strip()
        auto_per = (period or "").strip()
        auto_gb = (group_by or "").strip()
        auto_req = raw
        if last_snap and not dry_run:
            if not auto_fn and last_snap.get("full_name"):
                # не тащить ПрайсЛист
                fns = str(last_snap["full_name"])
                if not (
                    fns.startswith("Обработка.")
                    or fns.startswith("Отчет.")
                    or fns.startswith("Отчёт.")
                ):
                    # если фраза новая тема — не подставлять
                    if not any(
                        x in (raw or "").lower()
                        for x in (
                            "продаж",
                            "реализ",
                            "поступлен",
                            "остатк",
                            "задолжен",
                        )
                    ) or any(
                        x in (last_snap.get("user_text") or "").lower()
                        for x in (
                            "продаж",
                            "реализ",
                            "поступлен",
                            "остатк",
                            "задолжен",
                        )
                    ):
                        auto_fn = fns
            if not auto_per and last_snap.get("period_spec"):
                auto_per = str(last_snap["period_spec"])
            elif not auto_per and last_snap.get("periods"):
                auto_per = " и ".join(
                    str(p) for p in last_snap["periods"][:3]
                )
            if not auto_gb and last_snap.get("group_by"):
                auto_gb = str(last_snap["group_by"])
            # короткая фраза «в excel» — request = прошлый + excel
            if wants_export_existing(raw) or (
                force_excel and len(raw) < 40 and not auto_req
            ):
                auto_req = (
                    (last_snap.get("user_text") or "")
                    + " "
                    + raw
                    + " excel с диаграммами"
                ).strip()

        charts = default_charts_spec(auto_req or raw) if (force_excel or wants_charts) else []
        if charts_spec:
            # charts_spec param is str in signature - may be empty
            pass
        # если в сигнатуре передали charts_spec как str — игнор, list ниже

        return self._report_builder_execute(
            request=auto_req or raw,
            full_name=auto_fn,
            period=auto_per,
            group_by=auto_gb,
            metrics=metrics,
            value_field=value_field,
            limit=limit or 80,
            offset=offset,
            to_excel=force_excel or bool(charts),
            filename=filename or "report.xlsx",
            title=title or (auto_req or raw)[:80],
            dry_run=bool(dry_run),
            charts_spec=charts,
            filter_text="",
            top_n=0,
            wizard_answers={"mode": "auto"} if force_excel else None,
            filters=filters,
            sort=sort,
            time_grain=time_grain,
        )

    def _wizard_attach_schema(self, state: dict[str, Any]) -> None:
        """Резолв объекта + structure → schema (dimensions/measures) для мастера."""
        if not isinstance(state, dict):
            return
        from tools.report_wizard import is_garbage_answer, _guess_object_from_seed

        answers = state.get("answers") or {}
        obj_phrase = str(answers.get("object") or "").strip()
        # «да делай» / пусто → seed / last — НЕ искать metadata по мусору
        if not obj_phrase or is_garbage_answer(obj_phrase):
            obj_phrase = (
                state.get("last_full_name")
                or _guess_object_from_seed(str(state.get("seed_request") or ""))
                or ""
            )
        if not obj_phrase:
            return
        # уже есть schema на тот же full_name — не дёргать зря
        prev = (state.get("schema") or {}).get("full_name") or state.get(
            "resolved_full_name"
        )
        if prev and prev == state.get("resolved_full_name") and state.get("schema"):
            # object phrase changed to different topic?
            if answers.get("object") and not is_garbage_answer(str(answers["object"])):
                # if user set explicit object different from resolved — re-resolve
                pass
            elif state.get("schema"):
                return
        try:
            from tools.universal_schema import schema_suggestions

            # предпочтения: документы продаж/остатков, не Обработка.ПрайсЛист
            prefer_kinds = (
                "Документ",
                "РегистрНакопления",
                "РегистрСведений",
                "Справочник",
            )
            meta = self.find_metadata(obj_phrase, "Все", 12)
            items = meta.get("items") or meta.get("results") or []
            full = ""
            ranked: list[tuple[int, str]] = []
            for it in items:
                if not isinstance(it, dict) or not it.get("full_name"):
                    continue
                fn = str(it["full_name"])
                score = 0
                fl = fn.lower()
                if any(fn.startswith(k + ".") for k in prefer_kinds):
                    score += 5
                if fn.startswith("Обработка.") or fn.startswith("Отчет."):
                    score -= 10
                pl = obj_phrase.lower()
                if "продаж" in pl or "реализ" in pl:
                    if any(x in fl for x in ("реализ", "продаж", "выручк", "отгруз")):
                        score += 8
                if any(x in pl for x in ("поступлен", "закуп", "приобрет", "поставщик")):
                    if any(x in fl for x in ("поступлен", "закуп", "приобрет")):
                        score += 8
                if "остат" in pl:
                    if "остат" in fl or "товар" in fl:
                        score += 8
                if "склад" in pl and "склад" in fl:
                    score += 3
                ranked.append((score, fn))
            if ranked:
                ranked.sort(key=lambda x: (-x[0], x[1]))
                full = ranked[0][1]
            if not full and answers.get("object") and not is_garbage_answer(
                str(answers["object"])
            ):
                r = self.resolve_full_name(str(answers["object"]), prefer="Все")
                if isinstance(r, dict) and r.get("full_name"):
                    full = str(r["full_name"])
            if not full and state.get("last_full_name"):
                full = str(state["last_full_name"])
            if not full:
                return
            st = self.get_object_structure(full)
            if not isinstance(st, dict) or st.get("error"):
                st = st if isinstance(st, dict) else {}
            schema = schema_suggestions(
                st if not st.get("error") else {"full_name": full}
            )
            schema["full_name"] = full or schema.get("full_name")
            state["schema"] = schema
            state["resolved_full_name"] = full
        except Exception:
            return

    def _report_builder_execute(
        self,
        *,
        request: str,
        full_name: str = "",
        period: str = "",
        group_by: str = "",
        metrics: str = "",
        value_field: str = "",
        limit: int = 50,
        offset: int = 0,
        to_excel: bool = False,
        filename: str = "",
        title: str = "",
        dry_run: bool = False,
        charts_spec: list | None = None,
        filter_text: str = "",
        top_n: int = 0,
        wizard_answers: dict | None = None,
        filters: Any = None,
        sort: str = "",
        time_grain: str = "",
    ) -> dict[str, Any]:
        from tools.report_builder import run_report_builder

        shape_kw = {"filters": filters, "sort": sort, "time_grain": time_grain}

        # если есть schema из мастера — уточнить full_name / value_field / group_by
        wiz = getattr(self, "_report_wizard", None)
        if isinstance(wiz, dict):
            if not full_name and wiz.get("resolved_full_name"):
                full_name = str(wiz["resolved_full_name"])
        if wizard_answers and not full_name:
            # try resolve from object answer
            try:
                self._wizard_attach_schema(
                    {
                        "answers": wizard_answers,
                        "seed_request": request,
                    }
                )
            except Exception:
                pass

        def _analyst(**kw: Any) -> dict[str, Any]:
            return self.analyst_report(**kw)

        def _list_report(**kw: Any) -> dict[str, Any]:
            return self.compare_periods(
                search_query=kw.get("request") or "",
                full_name=kw.get("full_name") or "",
                period="",
                group_by="",
                metrics="count",
                to_excel=False,
            )

        # force excel if charts / auto
        if charts_spec:
            to_excel = True
        if wizard_answers and wizard_answers.get("mode") == "auto":
            to_excel = True

        # дефолт периода: extract_period_spec, НЕ вся фраза
        from tools.period_parse import extract_period_spec, is_all_time_phrase

        per_eff = (period or "").strip()
        try:
            from tools.linked_query import is_stock_balances_question as _stk
        except Exception:
            _stk = lambda _p: False  # noqa: E731
        pl_req = (request or "").lower().replace("ё", "е")
        stock_req = _stk(request or "") or any(
            x in pl_req for x in ("остатк", "налич", "на склад", "осталось", "остались")
        )
        if stock_req and not per_eff:
            per_eff = "сейчас"
        elif is_all_time_phrase(per_eff) or is_all_time_phrase(request or ""):
            per_eff = "за все время"
        else:
            last = getattr(self, "_last_report_snapshot", None)
            last_per = ""
            if isinstance(last, dict) and last.get("period_spec"):
                last_per = str(last["period_spec"])
            per_eff = extract_period_spec(per_eff, request or "", last_per)

        result = run_report_builder(
            request or "",
            full_name=full_name,
            period=per_eff,
            group_by=group_by,
            metrics=metrics,
            value_field=value_field,
            limit=limit,
            offset=offset,
            to_excel=False,  # excel ниже с multi-charts
            filename=filename,
            title=title,
            compare_fn=self.compare_periods,
            build_report_fn=None,
            linked_fn=self.linked_query,
            analyst_fn=_analyst,
            list_fn=_list_report,
            attr_fn=self.query_by_attributes,
            movements_fn=self.analyze_object_movements,
            **shape_kw,
            dry_run=bool(dry_run),
        )
        if not isinstance(result, dict):
            return {"error": "report_builder failed", "raw": str(result)}

        # авто-retry: period clarification → другой год / сейчас
        if (
            result.get("need_clarification")
            and result.get("about") == "period"
            and not dry_run
            and to_excel
        ):
            from datetime import datetime as _dt_alt
            from tools.period_parse import default_period_years

            _y = _dt_alt.now().year
            for alt in (
                default_period_years(),
                str(_y - 1),
                str(_y),
                "сейчас",
                "за все время",
            ):
                if alt == per_eff:
                    continue
                r2 = run_report_builder(
                    request or "",
                    full_name=full_name,
                    period=alt,
                    group_by=group_by,
                    metrics=metrics,
                    value_field=value_field,
                    limit=limit,
                    offset=offset,
                    to_excel=False,
                    filename=filename,
                    title=title,
                    compare_fn=self.compare_periods,
                    linked_fn=self.linked_query,
                    analyst_fn=_analyst,
                    list_fn=_list_report,
            attr_fn=self.query_by_attributes,
            movements_fn=self.analyze_object_movements,
            **shape_kw,
                    dry_run=False,
                )
                if isinstance(r2, dict) and r2.get("rows") and not r2.get(
                    "need_clarification"
                ):
                    result = r2
                    result["auto_period"] = alt
                    break

        # object clarification на мусорном full_name — retry search_query only
        if (
            result.get("need_clarification")
            and result.get("about") in ("object", "group_by", "object_and_period")
            and not dry_run
            and (request or full_name)
        ):
            r3 = run_report_builder(
                request or full_name,
                full_name="",  # заставить search
                period=per_eff or "",
                group_by="" if result.get("about") == "group_by" else group_by,
                metrics=metrics,
                value_field=value_field,
                limit=limit,
                offset=offset,
                to_excel=False,
                filename=filename,
                title=title,
                compare_fn=self.compare_periods,
                linked_fn=self.linked_query,
                analyst_fn=_analyst,
                list_fn=_list_report,
            attr_fn=self.query_by_attributes,
            movements_fn=self.analyze_object_movements,
            **shape_kw,
                dry_run=False,
            )
            if isinstance(r3, dict) and (
                r3.get("rows") or r3.get("ok")
            ) and not r3.get("need_clarification"):
                result = r3
                result["auto_object_retry"] = True

        if filter_text and result.get("rows"):
            from tools.report_builder import _apply_text_filter

            result = _apply_text_filter(result, filter_text)
        if top_n and isinstance(result.get("rows"), list):
            if len(result["rows"]) > top_n:
                result["rows_total"] = len(result["rows"])  # топ-N по просьбе: сколько строк было всего
            result["rows"] = result["rows"][:top_n]
            result["count"] = len(result["rows"])

        if wizard_answers:
            result["wizard_answers"] = wizard_answers
        if charts_spec:
            result["charts_spec"] = charts_spec

        # rows могут лежать глубже (linked / execute)
        if not result.get("rows"):
            for key in ("data", "items", "top_growth", "top_drop"):
                if isinstance(result.get(key), list) and result[key]:
                    if all(isinstance(x, dict) for x in result[key][:3]):
                        result["rows"] = result[key]
                        break
            er = result.get("execute_result")
            if not result.get("rows") and isinstance(er, dict):
                erows = er.get("rows") or er.get("data")
                if isinstance(erows, list):
                    result["rows"] = erows

        # multi-chart excel — даже если need_clarification но rows уже есть
        has_rows = bool(
            result.get("rows")
            or result.get("summary_by_period")
            or result.get("top_growth")
        )
        if to_excel and not dry_run and has_rows and (
            result.get("ok") or not result.get("error")
        ):
            try:
                from tools.excel_export import (
                    save_analyst_excel,
                    infer_axes_from_rows,
                    bind_charts_spec_to_rows,
                    clean_summary_period_rows,
                    summary_sheet_worth_it,
                )
                from tools.analyst_core import analyst_bullets

                rows = [
                    r
                    for r in (result.get("rows") or result.get("top_growth") or [])
                    if isinstance(r, dict)
                ]
                cat_k, val_k = infer_axes_from_rows(rows)
                cs = bind_charts_spec_to_rows(
                    list(charts_spec or []),
                    rows,
                    phrase=request or "",
                )
                if not cs and val_k:
                    cs = [
                        {"type": "bar", "value": val_k, "title": f"bar: {val_k}"},
                        {"type": "pie", "value": val_k, "title": f"pie: {val_k}"},
                    ]
                    if cat_k:
                        for c in cs:
                            c["chart_category"] = cat_k
                charts_spec = cs
                sheets = []
                # сначала Данные (графики строятся с них); Итоги — только 2+ периода
                if rows:
                    sheets.append(
                        {
                            "name": "Данные",
                            "title": title or request[:60],
                            "rows": rows[:20000],
                            "chart": "bar",
                            "chart_value": val_k,
                            "chart_category": cat_k,
                        }
                    )
                sbp_rows = clean_summary_period_rows(result.get("summary_by_period"))
                if summary_sheet_worth_it(result.get("summary_by_period")):
                    _sc, _sv = infer_axes_from_rows(
                        sbp_rows,
                        preferred_category="Период",
                    )
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
                if result.get("top_growth"):
                    g_rows = [
                        r
                        for r in (result.get("top_growth") or [])
                        if isinstance(r, dict)
                    ]
                    _gc, _gv = infer_axes_from_rows(
                        g_rows, preferred_value="Дельта"
                    )
                    sheets.append(
                        {
                            "name": "Рост",
                            "rows": result["top_growth"],
                            "chart": "bar",
                            "chart_value": _gv or "Дельта",
                            "chart_category": _gc,
                        }
                    )
                bullets = result.get("analyst_summary") or analyst_bullets(
                    result, mode="report"
                )
                if wizard_answers:
                    bullets = [
                        "Параметры мастера: "
                        + "; ".join(f"{k}={v}" for k, v in wizard_answers.items())
                    ] + list(bullets or [])

                saved = save_analyst_excel(
                    title=title or f"Отчёт: {request[:50]}",
                    sheets=sheets
                    or [
                        {
                            "name": "Данные",
                            "rows": rows,
                            "chart": "bar",
                            "chart_value": val_k,
                            "chart_category": cat_k,
                        }
                    ],
                    bullets=bullets,
                    meta_extra={
                        "full_name": result.get("full_name"),
                        "period": result.get("period_spec") or period,
                        "wizard": bool(wizard_answers),
                        "chart_category": cat_k,
                        "chart_value": val_k,
                    },
                    filename=filename or "report_wizard.xlsx",
                    charts_spec=cs,
                )
                if not saved.get("error"):
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
                            result[k] = saved[k]
                    result["to_excel"] = True
                    result["ok"] = True
                    # файл собран — не мучить need_clarification
                    if result.get("need_clarification") and result.get("path"):
                        result["need_clarification"] = False
                        result.pop("question", None)
                    result["note"] = (
                        (result.get("note") or "")
                        + f" Excel: {saved.get('charts_count', len(cs))} диаграмм(ы). "
                        "AUTO: объект/поля/графики без пошагового мастера."
                    ).strip()
                    result["prefer"] = (
                        (result.get("prefer") or "")
                        + " Покажи path/download_url и кратко листы. "
                        "Не спрашивай шаги мастера."
                    ).strip()
                    try:
                        import sys as _sys

                        print(
                            f"[auto] full_name={result.get('full_name')} "
                            f"period={result.get('period_spec') or period} "
                            f"path={result.get('path')} "
                            f"charts={result.get('charts_count', len(cs))}",
                            file=_sys.stderr,
                        )
                    except Exception:
                        pass
                else:
                    result["excel_error"] = saved.get("error")
            except Exception as e:
                result["excel_error"] = str(e)

        result["builder"] = "universal_report_builder"
        result["auto_build"] = True
        return result

    def build_report(
        self,
        request: str = "",
        search_query: str = "",
        full_name: str = "",
        period: str = "",
        group_by: str = "",
        metrics: str = "",
        value_field: str = "",
        limit: int = 50,
        offset: int = 0,
        page: int = 0,
        to_excel: bool = False,
        filename: str = "",
        title: str = "",
    ) -> dict[str, Any]:
        """
        Универсальный базовый отчёт по фразе менеджера (любой объект конфигурации).

        request — текст целиком: «продажи по контрагентам за 2015», «список номенклатуры»…
        limit/offset/page — порция rows (модель: не хватило → offset+=limit).
        «Построй отчёт …» → report_builder.
        """
        from tools.report_intent import detect_report_intent
        from tools.report_builder import is_report_builder_question

        raw = (request or search_query or "").strip()
        if not raw and not full_name:
            return need_clarification(
                "Что нужно в отчёте? Например: продажи за прошлый год по контрагентам; "
                "поступления за январь; список номенклатуры; остатки. "
                "Или: «построй отчёт продажи по контрагентам за 2016».",
                about="request",
                missing=["request"],
            )

        if raw and is_report_builder_question(raw):
            return self.report_builder(
                request=raw,
                full_name=full_name,
                period=period,
                group_by=group_by,
                metrics=metrics,
                value_field=value_field,
                limit=limit,
                offset=offset,
                to_excel=to_excel,
                filename=filename,
                title=title,
            )

        from tools.report_format import (
            enrich_report_result,
            is_manager_report_question,
            wants_period_compare,
        )
        from tools.linked_query import is_linked_or_debt_data_question
        from tools.linked_query import is_debt_unpaid_question

        # реквизиты шапки — даже если full_name уже из сессии
        try:
            from tools.attr_query import is_attr_filter_question
        except Exception:
            is_attr_filter_question = lambda _p: False  # noqa: E731
        if raw and is_attr_filter_question(raw) and not is_debt_unpaid_question(raw):
            out = self.query_by_attributes(
                request=raw,
                full_name=full_name or "",
                limit=min(int(limit or 50), 200),
                offset=int(offset or 0),
            )
            if isinstance(out, dict):
                out["routed_from"] = "build_report"
                out["route"] = "attr_filter"
            return out

        # Единый роутер (router.classify + fallback route_intent): code / access / help ≠ data build_report
        if raw and not full_name:
            from tools.route_intent import (
                ROUTE_ACCESS,
                ROUTE_CODE,
                ROUTE_EVENTLOG,
                ROUTE_HELP,
                ROUTE_SESSIONS,
                route_user_message,
            )

            routed = route_user_message(raw)
            if routed.get("route") == ROUTE_SESSIONS:
                out = self.active_users_overview(request=raw)
                if isinstance(out, dict):
                    out["routed_from"] = "build_report"
                    out["route"] = ROUTE_SESSIONS
                return out
            if routed.get("route") == ROUTE_ACCESS:
                out = self.access_overview(request=raw)
                if isinstance(out, dict):
                    out["routed_from"] = "build_report"
                    out["route"] = ROUTE_ACCESS
                return out
            if routed.get("route") == ROUTE_EVENTLOG:
                out = self.analyze_event_log(request=raw)
                if isinstance(out, dict):
                    out["routed_from"] = "build_report"
                    out["route"] = ROUTE_EVENTLOG
                return out
            if routed.get("route") == "movements":
                out = self.analyze_object_movements(request=raw)
                if isinstance(out, dict):
                    out["routed_from"] = "build_report"
                    out["route"] = "movements"
                return out
            # аналитика: ABC / variance / aging / funnel / excel+графики
            try:
                from tools.analyst_reports import is_analyst_question
            except Exception:
                is_analyst_question = lambda _p: False  # noqa: E731
            if is_analyst_question(raw):
                out = self.analyst_report(
                    request=raw,
                    full_name=full_name or "",
                    period=period or "",
                    group_by=group_by or "",
                    to_excel=bool(to_excel),
                    filename=filename or "",
                    limit=min(int(limit or 100), 200),
                )
                if isinstance(out, dict):
                    out["routed_from"] = "build_report"
                    out["route"] = "analyst"
                return out

            # маржа / выручка-себестоимость — регистр оборотов из metadata, не «первый попавшийся» документ
            try:
                from tools.margin_report import is_margin_question
            except Exception:
                is_margin_question = lambda _p: False  # noqa: E731
            if is_margin_question(raw) or (routed.get("prefer_tool") or "") == "margin_report":
                out = self.margin_report(
                    request=raw, limit=min(int(limit or 50), 200)
                )
                if isinstance(out, dict):
                    out["routed_from"] = "build_report"
                    out["route"] = "margin"
                return out

            # долг / остатки / multi-source — linked_query (не ad-hoc SQL)
            prefer_lq = (routed.get("prefer_tool") or "") == "linked_query"
            try:
                from tools.linked_query import is_stock_balances_question
            except Exception:
                is_stock_balances_question = lambda _p: False  # noqa: E731
            if (
                prefer_lq
                or is_debt_unpaid_question(raw)
                or is_linked_or_debt_data_question(raw)
                or is_stock_balances_question(raw)
            ):
                out = self.linked_query(
                    request=raw,
                    execute=True,
                    limit=min(int(limit or 50), 200),
                    offset=int(offset or 0),
                    page=int(page or 0),
                )
                if isinstance(out, dict):
                    out["routed_from"] = "build_report"
                    out["route"] = "linked"
                    # flatten rows for manager
                    er = out.get("execute_result") if isinstance(out.get("execute_result"), dict) else {}
                    if er.get("rows") is not None and "rows" not in out:
                        out["rows"] = er.get("rows")
                    if er.get("error") and not out.get("error"):
                        out["error"] = er.get("error")
                    out = enrich_report_result(
                        out,
                        request=raw,
                        full_name=", ".join(out.get("objects") or [])[:200],
                        period=str(out.get("as_of") or out.get("year") or ""),
                        report_mode=out.get("plan_role") or "linked_query",
                    )
                return out
            if routed.get("route") == ROUTE_CODE and not is_manager_report_question(raw):
                prefer = routed.get("prefer_tool") or "inspect_code"
                if prefer == "read_skd_query":
                    out = self.read_skd_query(request=raw)
                elif prefer in ("find_callers", "search_code"):
                    out = self.search_code(query=raw)
                else:
                    out = self.inspect_code(request=raw)
                if isinstance(out, dict):
                    out["routed_from"] = "build_report"
                    out["route"] = ROUTE_CODE
                    out.setdefault(
                        "prefer",
                        "Вопрос про код/СКД — ответь по tool result. Не цифры из базы.",
                    )
                return out
            if routed.get("route") == ROUTE_HELP:
                out = self.code_help(query=raw)
                if isinstance(out, dict):
                    out["routed_from"] = "build_report"
                    out["route"] = ROUTE_HELP
                return out

        intent = detect_report_intent(raw) if raw else {
            "mode": "summary",
            "search_query": search_query or full_name,
            "period": period,
            "group_by": group_by,
            "metrics": metrics or "sum,count",
            "value_field": value_field,
            "to_excel": to_excel,
            "prefer_kind": "",
        }

        # переопределения от модели/пользователя
        sq = (search_query or intent.get("search_query") or raw).strip()
        per = (period or intent.get("period") or "").strip()
        gb = (group_by or intent.get("group_by") or "").strip()
        met = (metrics or intent.get("metrics") or "sum,count").strip()
        vf = (value_field or intent.get("value_field") or "").strip()
        excel = bool(to_excel or intent.get("to_excel"))
        mode = intent.get("mode") or "summary"
        fn = (full_name or "").strip()
        prefer_kind = str(intent.get("prefer_kind") or "")

        # --- справочник: простой список ---
        if mode == "catalog_list" or (
            fn.startswith("Справочник.") and not per and "сумм" not in raw.lower()
        ):
            if not fn:
                qf = self.quick_find(sq, object_type="Справочники", limit=6)
                if qf.get("need_clarification") and not qf.get("top"):
                    return qf
                fn = str(qf.get("top") or "")
                if not fn and qf.get("options"):
                    return qf
            if not fn:
                return need_clarification(
                    "Уточните справочник для списка.",
                    about="object",
                    missing=["full_name"],
                )
            # список через export / query
            if excel:
                out = self.export_object_excel(
                    full_name=fn, mode="list", limit=limit, filename=filename, title=title
                )
            else:
                st = self.get_object_structure(fn)
                if st.get("error"):
                    return st
                roles = self.resolve_business_fields(fn)
                rmap = roles.get("roles") if isinstance(roles, dict) else {}
                code = (rmap or {}).get("code") or "Код"
                name_f = (rmap or {}).get("description") or (rmap or {}).get("name") or "Наименование"
                q = (
                    f"ВЫБРАТЬ ПЕРВЫЕ {max(1, min(int(limit), 500))} "
                    f"Т.{code} КАК {code}, Т.{name_f} КАК {name_f} "
                    f"ИЗ {fn} КАК Т"
                )
                out = self.execute_query(
                    q,
                    limit=limit,
                    offset=int(offset or 0),
                    page=int(page or 0),
                )
            if isinstance(out, dict):
                out["report_mode"] = "catalog_list"
                out["full_name"] = fn
                out["intent"] = intent
                out = enrich_report_result(
                    out, request=raw, full_name=fn, period=per, report_mode="catalog_list"
                )
            return out

        # --- «какие есть» / «документы по X» / detail: список документов ---
        list_existing = bool(intent.get("list_existing")) or (
            mode == "detail" and not per
        )
        filter_txt = str(intent.get("filter_text") or "").strip()
        if list_existing or (mode == "detail" and not per) or filter_txt:
            # «документы по <контрагент>» — не подменять объект на «реализация»
            list_sq = sq or raw
            if filter_txt and filter_txt.lower() in (list_sq or "").lower():
                list_sq = re.sub(
                    re.escape(filter_txt), " ", list_sq, flags=re.I
                )
                list_sq = re.sub(r"\s+", " ", list_sq).strip()
            listed = self._list_recent_documents(
                full_name=fn,
                search_query=list_sq,
                limit=max(int(limit or 50), 80) if filter_txt else limit,
                to_excel=False,
                filename=filename,
                title=title or (raw[:80] if raw else ""),
            )
            if isinstance(listed, dict):
                listed["intent"] = intent
                # фильтр по контрагенту/имени из фразы
                if filter_txt and isinstance(listed.get("rows"), list) and listed["rows"]:
                    try:
                        from tools.dialog_context import _apply_text_filter
                    except Exception:
                        _apply_text_filter = None  # type: ignore
                    if _apply_text_filter:
                        listed = _apply_text_filter(listed, filter_txt)
                    else:
                        fl = filter_txt.lower()
                        listed["rows"] = [
                            r
                            for r in listed["rows"]
                            if isinstance(r, dict)
                            and fl in " ".join(str(v).lower() for v in r.values())
                        ]
                        listed["row_count"] = len(listed["rows"])
                    listed["filter"] = filter_txt
                if listed.get("need_clarification") or listed.get("error"):
                    if listed.get("need_clarification") or listed.get("error"):
                        if "report_mode" not in listed:
                            listed["report_mode"] = "document_list"
                        return enrich_report_result(
                            listed,
                            request=raw,
                            full_name=str(listed.get("full_name") or fn),
                            period=per,
                            report_mode="document_list",
                        )
                if listed.get("ok") is not False and (
                    listed.get("ok")
                    or isinstance(listed.get("rows"), list)
                    or listed.get("full_name")
                ):
                    listed["report_mode"] = listed.get("report_mode") or "document_list"
                    if excel and listed.get("rows"):
                        try:
                            from tools.excel_export import save_rows_excel

                            saved = save_rows_excel(
                                listed["rows"],
                                title=title or raw[:80],
                                filename=filename or "docs.xlsx",
                            )
                            if isinstance(saved, dict):
                                listed.update(
                                    {
                                        k: saved[k]
                                        for k in ("path", "download_url")
                                        if saved.get(k)
                                    }
                                )
                        except Exception:
                            pass
                    return enrich_report_result(
                        listed,
                        request=raw,
                        full_name=str(listed.get("full_name") or fn),
                        period=per,
                        report_mode="document_list",
                    )

        # сравнение периодов — group_by только если уже из фразы/intent
        if wants_period_compare(raw) and not gb:
            pass

        # «как» из фразы: по месяцам / топ-N / антитоп / фильтры / свободные разрезы
        shape_kw: dict[str, Any] = {}
        if raw:
            try:
                from tools.report_builder import _shape_kwargs, parse_report_request

                shape_kw = _shape_kwargs(parse_report_request(raw))
            except Exception:
                shape_kw = {}

        # --- detail list с периодом: агрегаты / compare ---
        if mode == "detail" and per:
            out = self.compare_periods(
                full_name=fn,
                search_query=sq or raw,
                period=per,
                group_by=gb,
                metrics=met or "count",
                value_field=vf,
                to_excel=excel,
                filename=filename,
                title=title or (raw[:80] if raw else ""),
                **shape_kw,
            )
            if isinstance(out, dict) and out.get("ok"):
                out["report_mode"] = "detail_or_summary"
                out["intent"] = intent
                out = self._report_retry_if_empty(
                    out,
                    raw=raw,
                    sq=sq,
                    per=per,
                    gb=gb,
                    met=met or "count",
                    vf=vf,
                    excel=excel,
                    filename=filename,
                    title=title,
                    limit=limit,
                )
                return enrich_report_result(
                    out,
                    request=raw,
                    full_name=str(out.get("full_name") or fn),
                    period=per,
                    report_mode="detail_or_summary",
                )
            if isinstance(out, dict) and out.get("need_clarification"):
                return out

        # --- summary: суммы / сравнение / разрезы (основной путь) ---
        # без периода: для движений запросим уточнение, для остатков — compare сам
        if not per and prefer_kind == "document" and mode != "catalog_list":
            # всё равно пробуем compare — он вернёт need_clarification по period
            pass
        out = self.compare_periods(
            full_name=fn,
            search_query=sq or raw,
            period=per,
            group_by=gb,
            metrics=met or "sum,count",
            value_field=vf,
            to_excel=excel,
            filename=filename,
            title=title or (raw[:80] if raw else ""),
            **shape_kw,
        )
        if isinstance(out, dict):
            out["report_mode"] = "summary"
            out["intent"] = intent
            if out.get("ok") and not out.get("need_clarification"):
                out["report_title"] = title or raw[:120]
            out = self._report_retry_if_empty(
                out,
                raw=raw,
                sq=sq,
                per=per,
                gb=gb,
                met=met or "sum,count",
                vf=vf,
                excel=excel,
                filename=filename,
                title=title,
                limit=limit,
            )
            # если 0 данных — подсказка по candidates
            rows = out.get("rows") if isinstance(out.get("rows"), list) else []
            if out.get("ok") and rows is not None:
                from tools.report_format import count_nonzero_rows

                if count_nonzero_rows(rows) == 0 and "data_range" not in out:
                    self._attach_data_range(out)
                if count_nonzero_rows(rows) == 0 and out.get("candidates"):
                    out["hint_empty"] = (
                        "По выбранному объекту за период данных нет (0). "
                        "Попробуйте другой full_name из candidates или другой период."
                    )
            out = enrich_report_result(
                out,
                request=raw,
                full_name=str(out.get("full_name") or fn),
                period=per,
                report_mode="summary",
            )
        return out

    def _report_retry_if_empty(
        self,
        out: dict[str, Any],
        *,
        raw: str,
        sq: str,
        per: str,
        gb: str,
        met: str,
        vf: str,
        excel: bool,
        filename: str,
        title: str,
        limit: int,
    ) -> dict[str, Any]:
        """При 0 строк/нулях — другие candidates, без group_by, соседний год."""
        from tools.report_format import count_nonzero_rows

        if not isinstance(out, dict) or out.get("need_clarification") or out.get("error"):
            return out
        rows = out.get("rows") if isinstance(out.get("rows"), list) else None
        if rows is None:
            return out
        if count_nonzero_rows(rows) > 0:
            return out

        attempts: list[str] = []
        # 1) candidates other full_name
        cands = out.get("candidates") or []
        cur = str(out.get("full_name") or "")
        for c in cands[:4]:
            fn2 = ""
            if isinstance(c, dict):
                fn2 = str(c.get("full_name") or "")
            elif isinstance(c, str):
                fn2 = c
            if not fn2 or fn2 == cur:
                continue
            r2 = self.compare_periods(
                full_name=fn2,
                search_query=sq or raw,
                period=per,
                group_by=gb,
                metrics=met,
                value_field=vf,
                to_excel=excel,
                filename=filename,
                title=title or (raw[:80] if raw else ""),
            )
            attempts.append(fn2)
            if (
                isinstance(r2, dict)
                and isinstance(r2.get("rows"), list)
                and count_nonzero_rows(r2["rows"]) > 0
            ):
                r2["retried_from"] = cur
                r2["retry_reason"] = "alternate_candidate"
                r2["retry_attempts"] = attempts
                return r2

        # 2) same object without group_by
        if gb:
            r2 = self.compare_periods(
                full_name=cur,
                search_query=sq or raw,
                period=per,
                group_by="",
                metrics=met,
                value_field=vf,
                to_excel=excel,
                filename=filename,
                title=title or (raw[:80] if raw else ""),
            )
            attempts.append("no_group_by")
            if (
                isinstance(r2, dict)
                and isinstance(r2.get("rows"), list)
                and count_nonzero_rows(r2["rows"]) > 0
            ):
                r2["retried_from"] = cur
                r2["retry_reason"] = "drop_group_by"
                return r2

        # 3) year ±1 if period is a year
        import re as _re

        ym = _re.search(r"\b(20\d{2})\b", per or raw or "")
        if ym:
            y = int(ym.group(1))
            for y2 in (y - 1, y + 1):
                per2 = (per or str(y)).replace(str(y), str(y2))
                r2 = self.compare_periods(
                    full_name=cur,
                    search_query=sq or raw,
                    period=per2,
                    group_by=gb,
                    metrics=met,
                    value_field=vf,
                    to_excel=excel,
                    filename=filename,
                    title=title or (raw[:80] if raw else ""),
                )
                attempts.append(f"year_{y2}")
                if (
                    isinstance(r2, dict)
                    and isinstance(r2.get("rows"), list)
                    and count_nonzero_rows(r2["rows"]) > 0
                ):
                    r2["retried_from"] = cur
                    r2["retry_reason"] = f"period_{y2}"
                    r2["retry_attempts"] = attempts
                    return r2

        out["retry_attempts"] = attempts
        out["retry_exhausted"] = True
        self._attach_data_range(out)
        return out

    def _attach_data_range(self, out: dict[str, Any]) -> None:
        """Пустой отчёт ≠ ответ: показать, за какие даты в объекте вообще есть данные."""
        fn = str(out.get("full_name") or "")
        if not fn.startswith("Документ."):
            return
        q = f"ВЫБРАТЬ МИНИМУМ(Т.Дата) КАК ДатаС, МАКСИМУМ(Т.Дата) КАК ДатаПо, КОЛИЧЕСТВО(Т.Ссылка) КАК Всего ИЗ {fn} КАК Т"
        try:
            res = self.backend.execute_query(q, limit=1)
            if res.get("error"):
                return
            row = (res.get("rows") or res.get("data") or [{}])[0]
        except Exception:
            return
        row = {"С": str(row.get("ДатаС") or "")[:10], "По": str(row.get("ДатаПо") or "")[:10], "Всего": row.get("Всего")}
        total = str(row.get("Всего") or "0").replace("\xa0", "").replace(" ", "")
        out["data_range"] = {"from": row.get("С"), "to": row.get("По"), "documents_total": total}
        if total in ("0", ""):
            msg = f"В {fn} нет ни одного документа — объект в этой базе не используется."
        else:
            msg = (
                f"За запрошенный период данных нет. В {fn} всего {total} документов "
                f"с {row.get('С')} по {row.get('По')}. Предложи пользователю период внутри этого диапазона."
            )
        out["empty_reason"] = msg
        ms = out.get("manager_summary")
        if isinstance(ms, dict):
            ms["empty_reason"] = msg

    def analyst_report(
        self,
        request: str = "",
        mode: str = "",
        full_name: str = "",
        period: str = "",
        group_by: str = "",
        to_excel: bool = False,
        filename: str = "",
        limit: int = 100,
    ) -> dict[str, Any]:
        """
        Аналитика 1С: ABC, variance/вклад, aging, drill-down, план-факт,
        оборачиваемость, воронка, аномалии, dashboard + Excel с графиками.
        """
        from tools.analyst_reports import run_analyst_report

        last = getattr(self, "_last_report_snapshot", None)

        def _exec(q: str, lim: int = 50) -> dict[str, Any]:
            prep = self._prepare_1c_query(q)
            if prep.get("error"):
                return {"error": prep["error"]}
            return self.backend.execute_query(prep["query"], lim)

        return run_analyst_report(
            request,
            compare_fn=self.compare_periods,
            execute_fn=_exec,
            linked_fn=self.linked_query,
            margin_fn=self.margin_report,
            last_report=last,
            mode=mode,
            to_excel=to_excel,
            filename=filename,
            full_name=full_name,
            period=period,
            group_by=group_by,
            limit=limit,
        )

    def summarize_periods(
        self,
        full_name: str = "",
        year: int = 0,
        months: str = "",
        only_posted: bool = True,
        to_excel: bool = False,
        filename: str = "",
        title: str = "",
        value_field: str = "",
        group_by: str = "",
        metrics: str = "sum,count",
        search_query: str = "",
        period: str = "",
    ) -> dict[str, Any]:
        """Алиас compare_periods."""
        return self.compare_periods(
            full_name=full_name,
            year=year,
            months=months,
            value_field=value_field,
            group_by=group_by,
            metrics=metrics,
            only_posted=only_posted,
            to_excel=to_excel,
            filename=filename,
            title=title,
            search_query=search_query,
            period=period,
        )
