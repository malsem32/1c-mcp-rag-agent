# -*- coding: utf-8 -*-
"""Выгрузка в Excel: произвольный запрос, объект, документы за период."""

from __future__ import annotations

from typing import Any

from tools.responses import err, need_clarification
from tools.uni.safety import clamp_limit


class ExportMixin:
    def export_excel_report(
        self,
        query: str,
        limit: int = 500,
        title: str = "",
        filename: str = "",
        sheet_name: str = "Отчёт",
    ) -> dict[str, Any]:
        """
        Выполняет ВЫБРАТЬ 1С и сохраняет результат в .xlsx.
        Поля в query должны быть из get_object_structure / resolve.
        """
        from tools.excel_export import save_rows_excel

        lim = clamp_limit(limit, default=500, maximum=5000)
        prep = self._prepare_1c_query(query)
        if prep.get("error"):
            return err(str(prep["error"]), hint=prep.get("hint"))

        q = prep["query"]
        result = self.backend.execute_query(q, lim)
        if not isinstance(result, dict):
            return err(str(result))
        if result.get("error"):
            err_t = str(result.get("error") or "")
            hint = result.get("hint") or (
                "Проверьте POST /query и текст ВЫБРАТЬ (язык запросов 1С, не SELECT/WHERE)"
            )
            low = err_t.lower()
            if "синтаксическ" in low or "\\n" in (query or "") or "BETWEEN" in (query or "").upper():
                hint = (
                    "Для отчёта без ручного SQL: "
                    "export_object_excel(full_name=<из find_metadata>, year?, month?, mode='auto') "
                    "— работает для Документ / Справочник / Регистр*. "
                    "Запрос ВЫБРАТЬ передавайте одной строкой, без переносов."
                )
            return err(
                err_t,
                hint=hint,
                used_query=q[:400],
                prefer_tool="export_object_excel",
            )


        rows = result.get("data") or result.get("rows") or []
        if not isinstance(rows, list):
            return err("Некорректный формат данных от 1С")

        saved = save_rows_excel(
            rows,
            filename=filename or "",
            title=title or "",
            sheet_name=sheet_name or "Отчёт",
        )
        if saved.get("error"):
            return err(str(saved["error"]))

        saved["query_preview"] = q[:200]
        if prep.get("converted") or prep.get("warnings"):
            saved["query_normalized"] = True
            saved["warnings"] = prep.get("warnings")
        if result.get("truncated"):
            saved["truncated"] = True
            saved["note"] = f"Выгружено не более {lim} строк (limit)."
        return saved

    def export_object_excel(
        self,
        full_name: str,
        year: int | None = None,
        month: int | None = None,
        limit: int = 500,
        mode: str = "auto",
        only_posted: bool = True,
        filename: str = "",
        title: str = "",
        period: str = "",
    ) -> dict[str, Any]:
        """
        Универсальный отчёт Excel по ЛЮБОМУ объекту metadata ЭТОЙ базы.
        period — свободный текст периода (день/диапазон/месяц/последняя неделя…).
        year/month — запасной вариант. mode: auto|list|period|balances|turnovers|slice.
        """
        from tools.object_report import build_object_report_query, object_kind
        from tools.period_parse import parse_periods

        raw = (full_name or "").strip()
        if not raw:
            return err(
                "full_name обязателен",
                hint="find_metadata / suggest_objects / list_* → full_name",
            )

        resolved = self.resolve_full_name(raw)
        if resolved.get("error"):
            if resolved.get("candidates"):
                return need_clarification(
                    f"Объект «{raw}» не найден. Выберите из списка:",
                    about="object",
                    options=resolved.get("candidates"),
                    missing=["full_name"],
                )
            return resolved
        name = str(resolved["full_name"])

        name2, structure, error = self._require_structure(name)
        if error:
            return err(str(error.get("error")), hint=error.get("hint"), full_name=name)
        assert structure is not None
        name = name2

        y = int(year) if year is not None and int(year) > 0 else None
        m = int(month) if month is not None and int(month) > 0 else None
        if m is not None and (m < 1 or m > 12):
            return err("month должен быть 1–12")

        # period text → first slice as year/month or custom range via query path
        from tools.period_parse import extract_period_spec

        period_text = extract_period_spec(period or "", default="")
        if period_text and not (y and m):
            pr = parse_periods(period_text, legacy_year=y)
            if pr.get("slices"):
                sl0 = pr["slices"][0]
                # multi-slice: better use compare_periods for aggregates
                if len(pr["slices"]) > 1 and (mode or "auto") in ("auto", "period", "list"):
                    return self.compare_periods(
                        full_name=name,
                        period=period_text,
                        only_posted=only_posted,
                        to_excel=True,
                        filename=filename,
                        title=title,
                        metrics="sum,count",
                    )
                y = sl0.start.year
                m = sl0.start.month
                # single arbitrary range: inject via built query after
                if sl0.start.date() != sl0.start.replace(
                    day=1
                ).date() or sl0.end.day != __import__("calendar").monthrange(
                    sl0.end.year, sl0.end.month
                )[1]:
                    # non-full-month range — build custom query via compare one slice
                    return self.compare_periods(
                        full_name=name,
                        period=period_text,
                        only_posted=only_posted,
                        to_excel=True,
                        filename=filename,
                        title=title,
                        metrics="sum,count",
                    )

        built = build_object_report_query(
            name,
            structure,
            year=y,
            month=m,
            limit=limit,
            mode=mode or "auto",
            only_posted=only_posted,
        )
        if built.get("error"):
            return err(str(built["error"]), hint=built.get("hint"))

        query = str(built["query"])
        lim = clamp_limit(limit, 500, 5000)
        kind = built.get("kind") or object_kind(name)
        period = built.get("period")
        auto_title = title or (
            f"{name}" + (f" ({period})" if period else f" [{built.get('mode')}]")
        )
        safe_tail = name.split(".")[-1]
        if period and y and m:
            auto_file = filename or f"{safe_tail}_{y}_{m:02d}.xlsx"
        else:
            auto_file = filename or f"{safe_tail}.xlsx"

        out = self.export_excel_report(
            query=query,
            limit=lim,
            title=auto_title,
            filename=auto_file,
            sheet_name="Отчёт",
        )
        # если виртуальная таблица регистра упала — fallback на основную таблицу
        if out.get("error") and kind in ("accum_register", "info_register"):
            built2 = build_object_report_query(
                name,
                structure,
                year=y,
                month=m,
                limit=limit,
                mode="list",
                only_posted=only_posted,
            )
            if built2.get("query") and built2["query"] != query:
                out2 = self.export_excel_report(
                    query=str(built2["query"]),
                    limit=lim,
                    title=auto_title,
                    filename=auto_file,
                    sheet_name="Отчёт",
                )
                if not out2.get("error"):
                    out = out2
                    built = built2
                    out["fallback"] = "list_table_after_virtual_error"

        if not out.get("error"):
            out["object"] = name
            out["kind"] = kind
            out["mode"] = built.get("mode")
            out["built_query"] = built.get("query")
            if built.get("period"):
                out["period"] = built["period"]
            if built.get("virtual_table"):
                out["virtual_table"] = built["virtual_table"]
            out["fields_used"] = built.get("fields")
        else:
            out.setdefault(
                "hint",
                "Проверьте structure и mode (list|period|balances|turnovers|slice). "
                "Или export_excel_report с query из query_examples.",
            )
            out["built_query"] = built.get("query")
            out["object"] = name
        return out

    def export_document_period_excel(
        self,
        document_name: str,
        year: int,
        month: int,
        limit: int = 500,
        only_posted: bool = True,
        filename: str = "",
        title: str = "",
    ) -> dict[str, Any]:
        """
        Обёртка: документ за месяц → export_object_excel(mode=period).
        Для справочников/регистров используй export_object_excel.
        """
        out = self.export_object_excel(
            full_name=document_name,
            year=year,
            month=month,
            limit=limit,
            mode="period",
            only_posted=only_posted,
            filename=filename,
            title=title,
        )
        if not out.get("error") and out.get("object"):
            out["document"] = out["object"]
        return out
