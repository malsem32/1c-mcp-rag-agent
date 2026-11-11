# -*- coding: utf-8 -*-
"""Код конфигурации: поиск/чтение модулей, inspect, вызовы, движения, блокировки, аудит."""

from __future__ import annotations

from typing import Any


class CodeMixin:
    def _code_dir(self) -> str:
        return getattr(self, "code_dir", "") or ""

    def code_status(self) -> dict[str, Any]:
        """Есть ли выгрузка исходников для поиска по модулям."""
        from tools.code_search import code_status as _status

        return _status(self._code_dir() or None)

    def find_modules(
        self, search: str = "", kind: str = "", limit: int = 20
    ) -> dict[str, Any]:
        from tools.code_search import find_modules as _find

        return _find(
            search or "",
            kind=kind or "",
            limit=limit or 20,
            code_dir=self._code_dir() or None,
        )

    def module_outline(
        self,
        path: str = "",
        module: str = "",
        max_symbols: int = 200,
        name_filter: str = "",
    ) -> dict[str, Any]:
        from tools.code_search import module_outline as _outline

        return _outline(
            path=path or "",
            module=module or "",
            max_symbols=max_symbols or 200,
            name_filter=name_filter or "",
            code_dir=self._code_dir() or None,
        )

    def search_code(
        self,
        query: str = "",
        module_filter: str = "",
        kind: str = "",
        max_matches: int = 20,
        context_lines: int = 2,
        regex: bool = False,
        offset: int = 0,
        page: int = 0,
        limit: int = 0,
    ) -> dict[str, Any]:
        """
        max_matches/limit — сколько показать за раз (модель выбирает).
        offset/page — следующая порция, если в первых N нет нужного.
        """
        from tools.code_search import search_code as _search
        from tools.paging import (
            attach_paging,
            clamp_limit,
            fetch_limit_for_offset,
            resolve_offset,
            slice_page,
        )

        lim = clamp_limit(
            limit or max_matches or 20, default=20, maximum=80
        )
        off = resolve_offset(
            offset=offset, page=page if page else None, limit=lim
        )
        # +1 чтобы понять has_more (поиск не знает total)
        fetch = min(120, fetch_limit_for_offset(lim, off, max_fetch=119) + 1)
        raw = _search(
            query or "",
            module_filter=module_filter or "",
            kind=kind or "",
            max_matches=fetch,
            context_lines=context_lines if context_lines is not None else 2,
            regex=bool(regex),
            code_dir=self._code_dir() or None,
        )
        if not isinstance(raw, dict):
            return raw
        matches = list(raw.get("matches") or [])
        page_matches, meta = slice_page(
            matches, limit=lim, offset=off, default_limit=20, max_limit=80
        )
        # если вытащили full fetch — вероятно есть ещё
        if len(matches) >= fetch and fetch > off + lim:
            meta["has_more"] = True
            meta["next_offset"] = off + len(page_matches)
            meta["next_page"] = (off // lim) + 2 if lim else None
            meta["total_is_min"] = True
        elif len(matches) > off + lim:
            meta["has_more"] = True
            meta["next_offset"] = off + lim
            meta["next_page"] = (off // lim) + 2 if lim else None
        raw["matches"] = page_matches
        raw["count"] = meta.get("returned", len(page_matches))
        return attach_paging(
            raw, meta, tool="search_code",
            args={"query": query, "module_filter": module_filter, "kind": kind, "regex": regex, "limit": lim},
        )

    def read_code(
        self,
        path: str = "",
        module: str = "",
        start_line: int = 1,
        max_lines: int = 80,
    ) -> dict[str, Any]:
        from tools.code_search import read_code as _read

        res = _read(
            path=path or "",
            module=module or "",
            start_line=start_line or 1,
            max_lines=max_lines or 80,
            code_dir=self._code_dir() or None,
        )
        # вместо модуля передали имя процедуры («РассчитатьПлановуюСебестоимость») — подскажем, где она объявлена
        name = (module or path or "").strip()
        if isinstance(res, dict) and res.get("ok") is False and name and not any(c in name for c in "/\\."):
            try:
                from tools.proc_index import external_procs, find_definitions

                found = [{"path": d["path"], "signature": d.get("signature")} for d in find_definitions(name, "", limit=5)]
                ext = external_procs("", name, limit=5) or {}
                found += [{"path": d["path"], "line": d["line"], "signature": d.get("signature")}
                          for d in ext.get("definitions") or []]
            except Exception:
                found = []
            if found:
                res["procedure_found"] = found
                res["hint"] = "Это имя процедуры, а не модуля: read_code(path=procedure_found[0].path) или proc_index(procedure=…)."
        return res

    def inspect_code(
        self, request: str = "", path: str = "", max_hits: int = 10
    ) -> dict[str, Any]:
        """ONE-SHOT разбор формы/модуля/отчёта: где данные, без 8 tool-rounds."""
        from tools.epf_inspect import (
            extract_external_path,
            inspect_external_processing as _epf,
            is_external_file_question,
        )

        blob = f"{request or ''} {path or ''}"
        if is_external_file_question(request or "") or extract_external_path(blob):
            return _epf(
                path=path or extract_external_path(blob),
                request=request or blob,
                max_hits=max_hits or 12,
            )

        from tools.code_search import inspect_code as _inspect

        return _inspect(
            request=request or "",
            path=path or "",
            max_hits=max_hits or 10,
            code_dir=self._code_dir() or None,
        )

    def inspect_external_processing(
        self,
        path: str = "",
        request: str = "",
        max_hits: int = 12,
    ) -> dict[str, Any]:
        """Внешняя обработка/отчёт (.epf/.erf) — код из скинутого файла, не CF."""
        from tools.epf_inspect import inspect_external_processing as _epf

        return _epf(
            path=path or "",
            request=request or "",
            max_hits=max_hits or 12,
        )

    def build_external_processing(
        self,
        name: str = "",
        synonym: str = "",
        module: str = "",
        attributes: list[dict[str, Any]] | None = None,
        tabular_sections: list[dict[str, Any]] | None = None,
        form: bool = True,
        form_title: str = "",
        form_module: str = "",
        extra_forms: list[dict[str, Any]] | None = None,
        controls: list[dict[str, Any]] | None = None,
        form_width: int | None = None,
        templates: list[dict[str, Any]] | None = None,
        comment: str = "",
        kind: str = "",
        query: str = "",
        fields: list[Any] | None = None,
        parameters: list[dict[str, Any]] | None = None,
        resources: list[Any] | None = None,
        group_by: list[str] | None = None,
        order: list[str] | None = None,
        datasets: list[dict[str, Any]] | None = None,
        links: list[dict[str, Any]] | None = None,
        calculated_fields: list[dict[str, Any]] | None = None,
        filters: list[dict[str, Any]] | None = None,
        form_fields: bool = False,
        variants: list[dict[str, Any]] | None = None,
        structure: list[dict[str, Any]] | None = None,
        appearance: list[dict[str, Any]] | None = None,
        user_fields: list[dict[str, Any]] | None = None,
        output: dict[str, Any] | None = None,
        select: list[str] | None = None,
        bar_buttons: list[dict[str, Any]] | None = None,
        bar_menus: list[dict[str, Any]] | None = None,
        variant_buttons: bool = True,
        **_kw: Any,
    ) -> dict[str, Any]:
        """Собрать готовый .epf (обработка) или .erf (отчёт на СКД, kind='report'). Базу не трогает."""
        from tools.epf_build import BuildError, lint_bsl, lint_ordinary_form, write_epf
        from tools.excel_export import DEFAULT_EXPORT_DIR, enrich_export_meta

        if (kind or "").strip().lower() in ("report", "erf", "отчет", "отчёт") or (query or "").strip() or datasets:
            return self._build_external_report(
                name=name, synonym=synonym, module=module, form_title=form_title, comment=comment, query=query,
                fields=fields, parameters=parameters, resources=resources, group_by=group_by, order=order,
                datasets=datasets, links=links, calculated_fields=calculated_fields, filters=filters,
                attributes=attributes, tabular_sections=tabular_sections, form_module=form_module, extra_forms=extra_forms,
                controls=controls, form_width=form_width, form_fields=form_fields,
                bar_buttons=bar_buttons, bar_menus=bar_menus, variant_buttons=variant_buttons, templates=templates, variants=variants, structure=structure, appearance=appearance, user_fields=user_fields, output=output, select=select,
            )

        spec = {
            "name": name, "synonym": synonym, "module": module, "attributes": attributes or [],
            "tabular_sections": tabular_sections or [], "form": bool(form), "form_title": form_title,
            "form_module": form_module, "extra_forms": extra_forms or [], "comment": comment,
            "controls": controls or [], "form_width": form_width, "templates": templates or [],
        }
        try:
            res = write_epf(spec, DEFAULT_EXPORT_DIR)
        except BuildError as e:
            return {"ok": False, "error": str(e)}
        lint = lint_bsl(module) + lint_ordinary_form(module, "модуль объекта")
        lint += [f"модуль формы: {x}" for x in lint_bsl(form_module)] + lint_ordinary_form(form_module, "модуль формы")
        for ef in extra_forms or []:
            fm, label = ef.get("module") or "", f"форма {ef.get('name')}"
            lint += [f"{label}: {x}" for x in lint_bsl(fm)] + lint_ordinary_form(fm, label)
        v = res["verify"]
        plat = v.get("platform") or {}
        ok = bool(v.get("parsed_ok")) and (not plat.get("checked") or plat.get("ok"))
        out: dict[str, Any] = {
            "ok": ok,
            "file": res["path"].name,
            "size": res["size"],
            "verified": {"own_parser": v.get("parsed_ok"), "platform": plat.get("platform") if plat.get("ok") else plat.get("reason") or "не прошла"},
            "attributes": v.get("attributes"),
            "tabular_sections": v.get("tabular_sections"),
            **enrich_export_meta(res["path"], copy_to_downloads=False),
        }
        if lint:
            out["bsl_warnings"] = lint
        if form and not form_module and "ВыполнитьОбработку" not in module:
            out["note"] = "Кнопка «Выполнить» формы вызывает ВыполнитьОбработку() — в модуле такой процедуры нет."
        out["prefer"] = (
            "Скажи пользователю имя файла и download_url/path (только из этого ответа), перечисли реквизиты и что делает кнопка. "
            "Файл создан отдельно, в базу ничего не внесено: открыть через Файл → Открыть в Конфигураторе/клиенте. "
            "Если есть bsl_warnings — исправь модуль и вызови tool ещё раз с тем же name."
        )
        return out

    def _build_external_report(self, *, module: str = "", **spec: Any) -> dict[str, Any]:
        """Внешний отчёт .erf на СКД: запрос + параметры + ресурсы + группировки (tools/erf_build.py)."""
        from tools.epf_build import BuildError, lint_bsl, lint_ordinary_form
        from tools.erf_build import write_erf
        from tools.excel_export import DEFAULT_EXPORT_DIR, enrich_export_meta

        try:
            res = write_erf({**spec, "module": module}, DEFAULT_EXPORT_DIR)
        except BuildError as e:
            return {"ok": False, "error": str(e)}
        lint = lint_bsl(module) + lint_ordinary_form(module, "модуль отчёта")
        v = res["verify"]
        plat = v.get("platform") or {}
        out: dict[str, Any] = {
            "ok": bool(v.get("parsed_ok")) and (not plat.get("checked") or bool(plat.get("ok"))),
            "kind": "report",
            "file": res["path"].name,
            "size": res["size"],
            "verified": {"own_parser": v.get("parsed_ok"), "platform": plat.get("platform") if plat.get("ok") else plat.get("reason") or "не прошла"},
            **enrich_export_meta(res["path"], copy_to_downloads=False),
        }
        if lint:
            out["bsl_warnings"] = lint
        if v.get("dropped_parameters"):
            out["warnings"] = [f"Платформа не знает параметров вывода/оформления и отбросила их: {', '.join(v['dropped_parameters'])} — "
                               "поправь имена (подпараметры диаграммы пиши «ТипДиаграммы.ЦветФона» рядом с «ТипДиаграммы») или убери."]
        out["prefer"] = (
            "Скажи пользователю имя файла и download_url/path (только из этого ответа), перечисли параметры, поля, ресурсы и группировки. "
            "Отчёт открывается через Файл → Открыть в клиенте 1С (обычное приложение): кнопка «Сформировать», «Настройки…» меняет параметры и поля. "
            "Файл создан отдельно, в базу ничего не внесено. Запрос не проверялся на данных этой базы — имена объектов в нём должны быть из tools."
        )
        return out

    def analyze_object_movements(
        self, request: str = "", full_name: str = ""
    ) -> dict[str, Any]:
        """Регистры проведения / Движения — любой объект, только Qdrant."""
        from tools.object_movements import analyze_object_movements as _an

        return _an(request=request or "", full_name=full_name or "")

    def analyze_locks(
        self,
        request: str = "",
        object_hint: str = "",
        max_hits: int = 40,
    ) -> dict[str, Any]:
        """Управляемые блокировки в коде (Qdrant), не runtime wait-graph."""
        from tools.analyze_locks import analyze_managed_locks

        def _sc(query: str, max_matches: int = 12, context_lines: int = 3, **_kw):
            return self.search_code(
                query=query,
                max_matches=max_matches,
                context_lines=context_lines,
            )

        return analyze_managed_locks(
            request=request or "",
            object_hint=object_hint or "",
            max_hits=int(max_hits or 40),
            search_code_fn=_sc,
        )

    def recommend_managed_locks(
        self,
        request: str = "",
        full_name: str = "",
        max_registers: int = 15,
    ) -> dict[str, Any]:
        """Рекомендации: куда ставить управляемые блокировки при внедрении."""
        from tools.recommend_locks import recommend_managed_locks as _rec

        def _mov(request: str = "", full_name: str = "", **_k):
            return self.analyze_object_movements(
                request=request or "", full_name=full_name or ""
            )

        def _locks(request: str = "", object_hint: str = "", max_hits: int = 30, **_k):
            return self.analyze_locks(
                request=request or "",
                object_hint=object_hint or "",
                max_hits=max_hits,
            )

        def _struct(fn: str):
            try:
                return self.get_object_structure(fn)
            except Exception as e:
                return {"error": str(e), "full_name": fn}

        def _resolve(phrase: str):
            try:
                return self.resolve_object(phrase, "Документы", 8)
            except Exception as e:
                return {"error": str(e)}

        def _sc(query: str, max_matches: int = 5, context_lines: int = 2, **_k):
            return self.search_code(
                query=query,
                max_matches=max_matches,
                context_lines=context_lines,
            )

        return _rec(
            request=request or "",
            full_name=full_name or "",
            max_registers=int(max_registers or 15),
            movements_fn=_mov,
            locks_fn=_locks,
            structure_fn=_struct,
            resolve_fn=_resolve,
            search_code_fn=_sc,
        )

    def find_callers(
        self,
        module: str = "",
        procedure: str = "",
        limit: int = 15,
    ) -> dict[str, Any]:
        """Где вызывают Module.Procedure( — reverse usages."""
        from vector.search import search_code_vectors

        mod = (module or "").strip().rstrip(".")
        proc = (procedure or "").strip()
        # parse "Module.Procedure" in either field (имена — из текущей CF)
        if "." in proc and not mod:
            mod, _, proc = proc.partition(".")
        if "." in mod and not proc:
            parts = mod.split(".")
            if len(parts) >= 2:
                mod, proc = parts[0], parts[-1]
        if not proc:
            return {
                "ok": False,
                "error": "Нужен procedure= (и опционально module=)",
                "hint": "find_callers(module='<ОбщийМодульИзPath>', procedure='<Имя>') "
                "— имена только из search_code/find_modules ЭТОЙ базы",
            }
        queries = []
        if mod:
            queries.append(f"{mod}.{proc}(")
            queries.append(f"{mod}.{proc}")
        queries.append(f".{proc}(")
        queries.append(f"{proc}(")
        all_hits: list[dict[str, Any]] = []
        seen: set[str] = set()
        for q in queries:
            for mode in ("exact", "hybrid"):
                sc = search_code_vectors(
                    q, limit=max(8, int(limit or 15)), match_mode=mode
                )
                for m in sc.get("matches") or []:
                    path = (m.get("path") or "").replace("\\", "/")
                    # skip definition module itself when looking for callers
                    if mod and f"CommonModules/{mod}/" in path:
                        continue
                    text = (m.get("text") or m.get("snippet") or "")
                    # must look like a call site
                    if proc and proc not in text and proc not in path:
                        continue
                    key = f"{path}:{m.get('id')}"
                    if key in seen:
                        continue
                    seen.add(key)
                    all_hits.append(
                        {
                            "path": path,
                            "score": m.get("score"),
                            "snippet": text[:280],
                            "location": m.get("location"),
                            "object_name": m.get("object_name"),
                            "query": q,
                            "mode": mode,
                        }
                    )
                if len(all_hits) >= int(limit or 15):
                    break
            if len(all_hits) >= int(limit or 15):
                break
        all_hits.sort(key=lambda x: -float(x.get("score") or 0))
        all_hits = all_hits[: int(limit or 15)]
        return {
            "ok": True,
            "module": mod or None,
            "procedure": proc,
            "callers_count": len(all_hits),
            "callers": all_hits,
            "prefer": (
                "Перечисли path callers. Для тела — read_code. "
                "Определение модуля смотри CommonModules/<module>/ если module задан. "
                "Универсально: имена module/procedure из вопроса или search_code."
            ),
            "note": "Reverse usages (кто зовёт). Вниз по дереву — trace_calls.",
        }

    def trace_calls(
        self,
        request: str = "",
        path: str = "",
        procedure: str = "",
        depth: int = 2,
        max_edges: int = 30,
    ) -> dict[str, Any]:
        from tools.trace_calls import trace_calls as _tr

        # faster defaults
        d = min(int(depth or 2), 2)
        me = min(int(max_edges or 30), 28)
        return _tr(
            request=request or "",
            path=path or "",
            procedure=procedure or "",
            depth=d,
            max_edges=me,
            max_calls_per_node=22,
        )

    def explain_fill(
        self,
        request: str = "",
        field: str = "",
        full_name: str = "",
        object_name: str = "",
        max_hits: int = 20,
    ) -> dict[str, Any]:
        from tools.explain_fill import explain_fill as _ef

        return _ef(
            request=request or "",
            field=field or "",
            full_name=full_name or "",
            object_name=object_name or "",
            max_hits=max_hits or 20,
        )

    def read_skd_query(
        self,
        request: str = "",
        path: str = "",
        query_index: int = 0,
        max_chars: int = 6000,
    ) -> dict[str, Any]:
        """Текст ВЫБРАТЬ из СКД Templates (XML) — любой отчёт/обработка."""
        from tools.code_search import read_skd_query as _skd

        return _skd(
            request=request or "",
            path=path or "",
            query_index=query_index or 0,
            max_chars=max_chars or 6000,
            code_dir=self._code_dir() or None,
        )

    def query_static_explain(
        self,
        query_text: str = "",
        request: str = "",
    ) -> dict[str, Any]:
        from tools.query_static import query_static_explain as _qs

        return _qs(query_text=query_text or "", request=request or "")

    def proc_index(
        self,
        request: str = "",
        procedure: str = "",
        module: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        from tools.proc_index import proc_index as _pi

        return _pi(
            request=request or "",
            procedure=procedure or "",
            module=module or "",
            limit=limit or 20,
        )

    def scan_code_injection(
        self,
        scope: str = "exchange",
        limit: int = 40,
        include_query_execute: bool = False,
    ) -> dict[str, Any]:
        from tools.code_security import scan_code_injection as _sci

        return _sci(
            scope=scope or "exchange",
            limit=limit or 40,
            include_query_execute=bool(include_query_execute),
        )

    def audit_query_code(
        self,
        request: str = "",
        query_text: str = "",
        path: str = "",
        max_findings: int = 40,
    ) -> dict[str, Any]:
        from tools.query_antipatterns import audit_query_code as _aqc

        return _aqc(
            request=request or "",
            query_text=query_text or "",
            path=path or "",
            max_findings=int(max_findings or 40),
        )
