# -*- coding: utf-8 -*-
"""compare_periods: сравнение периодов и списки документов за период."""

from __future__ import annotations

import re
from typing import Any

from tools.responses import err, need_clarification


class CompareMixin:
    @staticmethod
    def _rank_objects_for_phrase(
        items: list[dict[str, Any]], phrase: str
    ) -> list[dict[str, Any]]:
        """Универсальное ранжирование (intent_search) — любые объекты, не только услуги."""
        from tools.intent_search import rank_items_by_phrase

        return rank_items_by_phrase(items, phrase)

    def _list_recent_documents(
        self,
        full_name: str = "",
        search_query: str = "",
        limit: int = 50,
        to_excel: bool = False,
        filename: str = "",
        title: str = "",
    ) -> dict[str, Any]:
        """
        Список последних записей ЛЮБОГО объекта (документ/справочник) без периода.
        Универсально: «какие есть X», «посмотри Y», «список Z» — X/Y/Z из metadata базы.
        """
        from tools.field_discovery import (
            is_reference_type,
            match_role,
            parse_structure_fields,
        )
        from tools.intent_search import expand_search_queries

        lim = max(1, min(int(limit or 50), 200))
        fn = (full_name or "").strip()
        sq = (search_query or "").strip()
        cands: list[str] = []

        if not fn and sq:
            queries = expand_search_queries(sq, max_extra=12)
            merged: dict[str, dict[str, Any]] = {}
            # документы + справочники (ранжирование само выберет класс)
            for otype in ("Документы", "Справочники"):
                for qone in queries:
                    meta = self.find_metadata(qone, otype, 15)
                    for i in meta.get("items") or []:
                        if not isinstance(i, dict):
                            continue
                        f = str(i.get("full_name") or "")
                        if not f or not (
                            f.startswith("Документ.") or f.startswith("Справочник.")
                        ):
                            continue
                        prev = merged.get(f)
                        sc = float(i.get("score") or 0)
                        if prev is None or sc > float(prev.get("score") or 0):
                            row = dict(i)
                            row["score"] = sc
                            merged[f] = row
            if not merged:
                for qone in queries[:4]:
                    meta = self.find_metadata(qone, "Все", 12)
                    for i in meta.get("items") or []:
                        if not isinstance(i, dict):
                            continue
                        f = str(i.get("full_name") or "")
                        if f.startswith("Документ.") or f.startswith("Справочник."):
                            if f not in merged:
                                merged[f] = dict(i)
            if not merged:
                return need_clarification(
                    f"По «{sq}» объект не найден. Уточните название "
                    f"(как в конфигурации: накладная, реализация, контрагент…).",
                    about="object",
                    missing=["full_name"],
                    search_query=sq,
                )
            ranked = self._rank_objects_for_phrase(list(merged.values()), sq)
            cands = [str(x.get("full_name")) for x in ranked[:8] if x.get("full_name")]
            best = ranked[0]
            best_sc = int(best.get("_phrase_score") or 0)
            second_sc = (
                int(ranked[1].get("_phrase_score") or 0) if len(ranked) > 1 else 0
            )
            # неоднозначно — спросить (универсально, не только накладные)
            if (
                len(cands) > 1
                and best_sc < 80
                and best_sc < second_sc + 25
            ) or (
                len(cands) > 1
                and best_sc > 0
                and second_sc > 0
                and best_sc - second_sc < 20
                and best_sc < 150
            ):
                return need_clarification(
                    f"По «{sq}» несколько объектов. Какой full_name нужен? "
                    f"Ближе всего: {best.get('full_name')}.",
                    about="object",
                    options=cands[:8],
                    missing=["full_name"],
                    search_query=sq,
                    suggested=str(best.get("full_name") or ""),
                )
            fn = str(best.get("full_name") or "")
        else:
            cands = [fn] if fn else []

        if not fn:
            return need_clarification(
                "Укажите объект (смысл или full_name из quick_find).",
                about="object",
                missing=["full_name"],
            )

        prefer = (
            "Документы"
            if fn.startswith("Документ") or not fn.startswith("Справочник")
            else "Справочники"
        )
        name, structure, err_st = self._require_structure(fn)
        if err_st or not structure:
            r0 = self.resolve_full_name(fn, prefer=prefer)
            if r0.get("full_name"):
                name, structure, err_st = self._require_structure(str(r0["full_name"]))
            if err_st or not structure:
                return err(
                    str(
                        (err_st or {}).get("error")
                        if isinstance(err_st, dict)
                        else err_st
                    )
                    or f"structure {fn}",
                    hint="quick_find / find_metadata",
                )

        fields = parse_structure_fields(structure)
        is_doc = name.startswith("Документ.")
        is_cat = name.startswith("Справочник.")

        date_f = match_role(fields, "date") if is_doc else None
        num_f = match_role(fields, "number") if is_doc else match_role(fields, "code")
        post_f = match_role(fields, "posted") if is_doc else None
        name_f = match_role(fields, "description") if is_cat else None
        cp_f = match_role(fields, "counterparty")
        amt_f = match_role(fields, "amount") if is_doc else None
        org_f = match_role(fields, "organization")
        wh_f = match_role(fields, "warehouse")

        select_parts: list[str] = []
        if num_f:
            select_parts.append(f"Т.{num_f.name} КАК {num_f.name}")
        if name_f:
            select_parts.append(f"Т.{name_f.name} КАК {name_f.name}")
        if date_f:
            select_parts.append(f"Т.{date_f.name} КАК {date_f.name}")
        if post_f:
            select_parts.append(f"Т.{post_f.name} КАК {post_f.name}")
        for role_f in (cp_f, org_f, wh_f, amt_f):
            if not role_f:
                continue
            # не дублировать
            if any(role_f.name in s for s in select_parts):
                continue
            if is_reference_type(role_f):
                select_parts.append(
                    f"ПРЕДСТАВЛЕНИЕ(Т.{role_f.name}) КАК {role_f.name}"
                )
            else:
                select_parts.append(f"Т.{role_f.name} КАК {role_f.name}")
        if not select_parts:
            select_parts.append("Т.Ссылка КАК Ссылка")

        if date_f:
            order = f"Т.{date_f.name} УБЫВ"
        elif name_f:
            order = f"Т.{name_f.name}"
        else:
            order = "Т.Ссылка УБЫВ"
        q = (
            f"ВЫБРАТЬ ПЕРВЫЕ {lim} "
            + ", ".join(select_parts)
            + f" ИЗ {name} КАК Т УПОРЯДОЧИТЬ ПО {order}"
        )
        out = self.execute_query(q, limit=lim)
        if not isinstance(out, dict):
            return err(str(out))
        if out.get("error"):
            return out

        rows = out.get("rows") or out.get("data") or []
        result: dict[str, Any] = {
            "ok": True,
            "full_name": name,
            "report_mode": "document_list" if is_doc else "object_list",
            "rows": rows,
            "count": len(rows) if isinstance(rows, list) else 0,
            "query": q,
            "note": (
                f"Последние записи «{name}» (без фильтра периода, лимит {lim}). "
                "Суммы за период: build_report(request='… за <год>')."
            ),
            "prefer": (
                "Покажи таблицу rows и full_name. "
                "Не list_metadata_types, не лекции про tools."
            ),
        }
        if cands:
            result["candidates"] = cands
        if to_excel:
            from tools.excel_export import save_rows_excel

            saved = save_rows_excel(
                rows if isinstance(rows, list) else [],
                filename=filename or f"list_{name.split('.')[-1]}.xlsx",
                title=title or f"Список {name}",
                sheet_name="Данные",
            )
            if saved.get("error"):
                result["excel_error"] = saved["error"]
            else:
                for k in (
                    "path",
                    "filename",
                    "folder",
                    "file_uri",
                    "download_url",
                    "downloads_path",
                    "how_to_open",
                ):
                    if saved.get(k) is not None:
                        result[k] = saved[k]
        return result

    def _resolve_field_by_user(
        self,
        fields: list[Any],
        user_name: str,
        *,
        role_fallback: str = "",
    ) -> Any | None:
        """Имя поля из structure или role (amount/date/…)."""
        from tools.field_discovery import match_role

        u = (user_name or "").strip()
        if not u:
            return match_role(fields, role_fallback) if role_fallback else None
        ul = u.lower().replace("ё", "е")
        # роль
        if ul in (
            "sum",
            "сумма",
            "amount",
            "count",
            "количество",
            "date",
            "дата",
            "month",
            "месяц",
            "year",
            "год",
        ):
            role_map = {
                "sum": "amount",
                "сумма": "amount",
                "amount": "amount",
                "date": "date",
                "дата": "date",
            }
            if ul in role_map:
                return match_role(fields, role_map[ul])
            return None
        for f in fields:
            if f.tabular_section:
                continue
            if f.name.lower().replace("ё", "е") == ul:
                return f
            if f.synonym and f.synonym.lower().replace("ё", "е") == ul:
                return f
        for f in fields:
            if f.tabular_section:
                continue
            if ul in f.name.lower().replace("ё", "е") or (
                f.synonym and ul in f.synonym.lower().replace("ё", "е")
            ):
                return f
        if role_fallback:
            return match_role(fields, role_fallback)
        return None

    def compare_periods(
        self,
        full_name: str = "",
        year: int = 0,
        months: str = "",
        value_field: str = "",
        group_by: str = "",
        metrics: str = "sum,count",
        only_posted: bool = True,
        to_excel: bool = False,
        filename: str = "",
        title: str = "",
        search_query: str = "",
        period: str = "",
        filters: Any = None,
        sort: str = "",
        time_grain: str = "",
        top_n: int = 0,
        soft_group_by: str = "",
    ) -> dict[str, Any]:
        """
        Универсальное сравнение/агрегация по ЛЮБЫМ периодам.

        «Как» строить (из речи пользователя / report_builder):
          filters — [{field, op: like|eq|ne|>|<|>=|<=, value, neg}] или текст
                    «Склад ~ Основной; Сумма > 1000»; поле ищется по structure объекта
          sort — desc (по умолч.) | asc | name | period
          time_grain — day|week|month|quarter|year: разбить период на шаги
          top_n — строк на период; soft_group_by — разрезы «по X», если найдутся в structure

        period — свободный текст: «январь и февраль 2015», «последний месяц»,
        «с 01.01.2015 по 15.02.2015», «последние 7 дней», «2015-02-15», …
        year/months — устаревшие; если period пуст, собираются из year+months.
        search_query — фраза для поиска объекта («счет покупателя»).
        """
        # фазы — методы _cp_* ниже; состояние между фазами — cp_state
        cp_state: dict[str, Any] = {
            "full_name": full_name,
            "year": year,
            "months": months,
            "value_field": value_field,
            "group_by": group_by,
            "metrics": metrics,
            "only_posted": only_posted,
            "to_excel": to_excel,
            "filename": filename,
            "title": title,
            "search_query": search_query,
            "period": period,
            "filters": filters,
            "sort": sort,
            "time_grain": time_grain,
            "top_n": top_n,
            "soft_group_by": soft_group_by,
        }
        done = self._cp_prepare_request(cp_state)
        if done is not None:
            return done
        done = self._cp_resolve_object(cp_state)
        if done is not None:
            return done
        done = self._cp_resolve_fields(cp_state)
        if done is not None:
            return done
        done = self._cp_parse_periods(cp_state)
        if done is not None:
            return done
        done = self._cp_run_queries(cp_state)
        if done is not None:
            return done
        return self._cp_build_result(cp_state)

    def _cp_prepare_request(self, cp_state: dict[str, Any]) -> dict[str, Any] | None:
        """Разбор фразы: тип объекта (документ/остатки), группировки, фильтры, excel."""
        filename = cp_state["filename"]
        filters = cp_state["filters"]
        full_name = cp_state["full_name"]
        group_by = cp_state["group_by"]
        period = cp_state["period"]
        search_query = cp_state["search_query"]
        time_grain = cp_state["time_grain"]
        to_excel = cp_state["to_excel"]

        from tools.excel_export import save_rows_excel
        from tools.field_discovery import (
            is_numeric_type,
            is_reference_type,
            match_field_by_words,
            match_role,
            parse_structure_fields,
            word_stem,
        )
        from tools.period_parse import parse_periods

        sq = (search_query or "").strip()
        fn_raw = (full_name or "").strip()
        # «Документ» / «Справочник» без имени — мусор от модели
        if fn_raw.lower() in (
            "документ",
            "документы",
            "справочник",
            "справочники",
            "регистр",
            "все",
        ) or (
            fn_raw.count(".") == 0
            and fn_raw.lower() in ("document", "catalog")
        ):
            fn_raw = ""
        if fn_raw.lower() in ("документ.", "справочник.") or fn_raw.endswith("."):
            fn_raw = ""

        prefer = "Документы"
        blob = f"{sq} {fn_raw} {period} {group_by}".lower().replace("ё", "е")
        try:
            from tools.linked_query import is_stock_balances_question as _is_stock
        except Exception:
            _is_stock = lambda _p: False  # noqa: E731
        stock_like = _is_stock(blob) or any(
            x in blob
            for x in (
                "остатк",
                "на склад",
                "по склад",
                "налич",
                "запас",
                "товары на склад",
                "осталось",
                "остались",
            )
        )
        if any(x in blob for x in ("регистр", "остатк", "оборот", "срез")) or stock_like:
            prefer = "Все"
        if stock_like:
            # остатки — регистры, не «Обработка.ПрайсЛист»
            prefer = "РегистрыНакопления" if prefer != "Документы" else "Все"
        # движение/суммы — документ (или регистр), НЕ справочник контрагентов
        doc_report = any(
            x in blob
            for x in (
                "продаж",
                "реализ",
                "выручк",
                "отгруз",
                "сумм",
                "сравни",
                "оборот",
                "поступлен",
                "закуп",
                "приобрет",
                "оплат",
                "платеж",
                "возврат",
                "остатк",
                "склад",
            )
        )

        sales_like = any(
            x in blob
            for x in ("продаж", "реализ", "выручк", "отгруз", "реализован")
        )
        purchase_like = any(
            x in blob
            for x in (
                "поступлен",
                "закуп",
                "приобрет",
                "приход",
                "поставщик",
            )
        ) and not sales_like
        # «поступления» без «на р/с/банк» = товары; с деньгами — cash/bank
        goods_flow = (
            any(
                x in blob
                for x in (
                    "продаж",
                    "реализ",
                    "поступлен",
                    "закуп",
                    "приобрет",
                    "отгруз",
                    "товар",
                    "услуг",
                )
            )
            and not any(
                x in blob
                for x in (
                    "на расчет",
                    "на р/с",
                    "расчетн счет",
                    "денежн",
                )
            )
        )
        # full_name=Справочник.* при «продажи…» — ошибка модели (нужен документ/регистр)
        if fn_raw.startswith("Справочник.") and doc_report:
            fn_raw = ""
            if not sq:
                from tools.business_stems import PURCHASE_SEARCH, SALES_SEARCH

                sq = SALES_SEARCH if sales_like else PURCHASE_SEARCH
            prefer = "Документы"
        # group_by=«месяц» / «по кварталам» — это шаг времени, не поле
        _grain_words = (
            ("дням", "day"), ("дни", "day"), ("день", "day"), ("недел", "week"), ("месяц", "month"),
            ("квартал", "quarter"), ("год", "year"),
        )
        _gb_keep = []
        for _p in re.split(r"[,;+/]| и ", group_by or ""):
            _pl = re.sub(r"^по\s+", "", _p.strip().lower().replace("ё", "е"))
            _g = next((g for st, g in _grain_words if _pl.startswith(st)), "")
            if _g and len(_pl) <= 10:
                time_grain = time_grain or _g
            elif _p.strip():
                _gb_keep.append(_p.strip())
        group_by = ", ".join(_gb_keep)
        time_grain = (time_grain or "").strip().lower()
        # фильтры: list[dict] или текст «Поле ~ Значение; Сумма > 1000»
        from tools.report_builder import parse_filters_param

        filters = parse_filters_param(filters)
        # «по складу» / «контрагент, номенклатура» → нормальные имена (1–2D)
        gb_in = (group_by or "").strip()
        if gb_in:
            m_gb = re.match(r"^по\s+(.+)$", gb_in, re.I)
            if m_gb:
                gb_in = m_gb.group(1).strip()
                group_by = gb_in
            # multi: не схлопывать «контрагент, номенклатура» в одно слово
            parts_gb = [
                p.strip()
                for p in re.split(r"[,;+/]| и ", gb_in)
                if p and p.strip()
            ]
            if len(parts_gb) >= 2:
                canon_parts = []
                for p in parts_gb[:2]:
                    pl0 = p.lower().replace("ё", "е")
                    mapped = p
                    for keys, canon in (
                        (("склад",), "склад"),
                        (("контрагент", "клиент", "покупат"), "контрагент"),
                        (("номенклатур", "товар"), "номенклатура"),
                        (("организац",), "организация"),
                    ):
                        if any(k in pl0 for k in keys):
                            mapped = canon
                            break
                    canon_parts.append(mapped)
                group_by = ", ".join(canon_parts)
                gb_in = group_by
            else:
                # один разрез
                gbl0 = gb_in.lower().replace("ё", "е")
                for keys, canon in (
                    (("склад",), "склад"),
                    (("контрагент", "клиент", "покупат"), "контрагент"),
                    (("номенклатур", "товар"), "номенклатура"),
                    (("организац",), "организация"),
                ):
                    if any(k in gbl0 for k in keys):
                        group_by = canon
                        gb_in = canon
                        break
        if not gb_in and any(
            x in blob
            for x in (
                "контрагент",
                "по клиент",
                "по покупат",
                "между контрагент",
                "в разрезе контрагент",
                "по контрагент",
                "поставщик",
                "по поставщик",
                "в разрезе поставщик",
            )
        ):
            group_by = "контрагент"
        if not (group_by or "").strip() and any(
            x in blob for x in ("по склад", "в разрезе склад", "по складам")
        ):
            group_by = "склад"
        # мусор full_name от мастера (ПрайсЛист) — сбросить
        if fn_raw.startswith("Обработка.") or fn_raw.startswith("Отчет.") or fn_raw.startswith(
            "Отчёт."
        ):
            if stock_like or doc_report or any(
                x in blob for x in ("продаж", "остатк", "поступлен", "сумм")
            ):
                if not sq:
                    sq = (
                        "остатки товары склады"
                        if stock_like
                        else (fn_raw.split(".")[-1] if "." in fn_raw else "продажи")
                    )
                    if stock_like:
                        sq = "товары на складах остатки"
                fn_raw = ""
        # Excel только по явной просьбе (модель часто ставит to_excel=true зря)
        excel_asked = any(
            x in blob
            for x in ("excel", "xlsx", "выгруз", "файл", "эксель")
        )
        if to_excel and not excel_asked and not (filename or "").strip():
            to_excel = False

        cp_state.update(
            {
                "blob": blob,
                "doc_report": doc_report,
                "filters": filters,
                "fn_raw": fn_raw,
                "goods_flow": goods_flow,
                "group_by": group_by,
                "is_numeric_type": is_numeric_type,
                "is_reference_type": is_reference_type,
                "match_field_by_words": match_field_by_words,
                "match_role": match_role,
                "parse_periods": parse_periods,
                "parse_structure_fields": parse_structure_fields,
                "prefer": prefer,
                "purchase_like": purchase_like,
                "sales_like": sales_like,
                "save_rows_excel": save_rows_excel,
                "sq": sq,
                "stock_like": stock_like,
                "time_grain": time_grain,
                "to_excel": to_excel,
                "word_stem": word_stem,
            }
        )
        return None

    _VOLUME_CACHE: dict[str, int] = {}

    def _data_volume(self, full_names: list[str]) -> dict[str, int]:
        """Число записей в таблицах объектов (один запрос, кэш на процесс). -1 — не удалось."""
        out: dict[str, int] = {}
        todo = [fn for fn in full_names if fn not in self._VOLUME_CACHE]
        blocked = ("удалить", "изменить", "выполнить", "поместить", "уничтожить")  # HTTP H1
        todo = [fn for fn in todo if not any(b in fn.lower() for b in blocked)]
        if todo:
            parts = [f'ВЫБРАТЬ "{fn}" КАК Объект, КОЛИЧЕСТВО(*) КАК Кол ИЗ {fn} КАК Т' for fn in todo]
            try:
                res = self.backend.execute_query("\nОБЪЕДИНИТЬ ВСЕ\n".join(parts), limit=len(parts) + 1)
                rows = res.get("rows") or res.get("data") or []
                for r in rows:
                    try:
                        self._VOLUME_CACHE[str(r.get("Объект"))] = int(float(str(r.get("Кол") or 0).replace("\xa0", "").replace(" ", "").replace(",", ".")))
                    except (TypeError, ValueError):
                        pass
            except Exception:
                pass
        for fn in full_names:
            out[fn] = self._VOLUME_CACHE.get(fn, -1)
        return out

    def _prefer_by_data_volume(
        self, ranked: list[dict[str, Any]], *, window: int = 30, top: int = 8, phrase: str = ""
    ) -> list[dict[str, Any]]:
        """
        Универсальный тай-брейк (любая CF): среди кандидатов-документов/регистров с близким
        баллом фразы поднять тот, у которого на порядок больше записей.
        «продали» → основной документ продаж, а не узкоспециальный с тем же корнем.
        """
        if len(ranked) < 2:
            return ranked
        try:
            best_sc = float(ranked[0].get("_phrase_score") or 0)
        except (TypeError, ValueError):
            return ranked
        close = [
            x for x in ranked[:top]
            if str(x.get("full_name") or "").split(".")[0] in ("Документ", "РегистрНакопления", "РегистрСведений", "Справочник")
            and (
                best_sc - float(x.get("_phrase_score") or 0) <= window
                or float(x.get("_phrase_score") or 0) >= 0.35 * best_sc
            )
        ]
        if len(close) < 2:
            return ranked
        # пользователь назвал объект явно (имя/синоним из каталога) — не подменять
        if phrase:
            try:
                from tools.object_catalog import resolve_full_name

                rc = resolve_full_name(phrase)
                if rc.get("confident") and rc.get("full_name") in {str(x.get("full_name")) for x in close}:
                    return ranked
            except Exception:
                pass
        vol = self._data_volume([str(x["full_name"]) for x in close])
        first = str(ranked[0].get("full_name") or "")
        v_first = vol.get(first, -1)
        top_fn, v_top = max(vol.items(), key=lambda kv: kv[1])
        if v_top > 0 and top_fn != first and (v_first <= 0 or v_top >= 3 * v_first):
            pick = next(x for x in ranked if str(x.get("full_name")) == top_fn)
            pick = dict(pick)
            pick["_phrase_score"] = max(float(pick.get("_phrase_score") or 0), best_sc + 30)
            pick["_picked_by_volume"] = {"rows": v_top, "instead_of": first, "instead_rows": v_first}
            return [pick] + [x for x in ranked if str(x.get("full_name")) != top_fn]
        return ranked

    def _cp_resolve_object(self, cp_state: dict[str, Any]) -> dict[str, Any] | None:
        """Выбор объекта метаданных и его полей (дата/сумма) из каталога этой CF."""
        blob = cp_state["blob"]
        doc_report = cp_state["doc_report"]
        fn_raw = cp_state["fn_raw"]
        goods_flow = cp_state["goods_flow"]
        group_by = cp_state["group_by"]
        match_role = cp_state["match_role"]
        metrics = cp_state["metrics"]
        parse_structure_fields = cp_state["parse_structure_fields"]
        prefer = cp_state["prefer"]
        purchase_like = cp_state["purchase_like"]
        sales_like = cp_state["sales_like"]
        sq = cp_state["sq"]
        stock_like = cp_state["stock_like"]
        value_field = cp_state["value_field"]

        resolved: dict[str, Any]
        # 1) search_query важнее выдуманного full_name
        if sq:
            otype = prefer if prefer == "Документы" else "Все"
            # универсальное расширение фразы (любые домены, не hardcode «услуг»)
            from tools.intent_search import expand_search_queries

            queries = expand_search_queries(sq, max_extra=10)
            # смысл «продажи/закупки» → поисковые стемы (business_stems — слова, не full_name)
            try:
                from tools.business_stems import PURCHASE_SEARCH, SALES_SEARCH

                if sales_like and SALES_SEARCH not in queries:
                    queries.append(SALES_SEARCH)
                if purchase_like and PURCHASE_SEARCH not in queries:
                    queries.append(PURCHASE_SEARCH)
            except Exception:
                pass

            merged: dict[str, dict[str, Any]] = {}
            for qone in queries:
                meta = self.find_metadata(qone, otype, 15)
                for i in meta.get("items") or []:
                    if not isinstance(i, dict):
                        continue
                    fn = str(i.get("full_name") or "")
                    if not fn:
                        continue
                    prev = merged.get(fn)
                    sc = float(i.get("score") or 0)
                    if prev is None or sc > float(prev.get("score") or 0):
                        merged[fn] = dict(i)
            if not merged and otype == "Документы":
                meta = self.find_metadata(sq, "Все", 15)
                for i in meta.get("items") or []:
                    if isinstance(i, dict) and i.get("full_name"):
                        merged[str(i["full_name"])] = dict(i)

            if not merged:
                return need_clarification(
                    f"По запросу «{sq}» объект не найден. Уточните название "
                    f"(документ / справочник / регистр) или другие ключевые слова.",
                    about="object",
                    missing=["object_name"],
                    search_query=sq,
                )

            # ранжирование по смыслу фразы (стемы фразы ↔ стемы full_name)
            ranked = self._rank_objects_for_phrase(list(merged.values()), blob)
            cands = [str(x.get("full_name")) for x in ranked if x.get("full_name")]
            name = ""
            if fn_raw:
                r0 = self.resolve_full_name(fn_raw, prefer=prefer)
                if not r0.get("error") and r0.get("exact"):
                    name = str(r0["full_name"])
                elif not r0.get("error") and str(r0.get("full_name")) in cands:
                    name = str(r0["full_name"])

            def _is_bad_data_object(fn: str) -> bool:
                return bool(
                    fn.startswith("Обработка.")
                    or fn.startswith("Отчет.")
                    or fn.startswith("Отчёт.")
                )

            def _pick_good(cands_list: list[str]) -> str:
                for c in cands_list:
                    if c and not _is_bad_data_object(c):
                        if stock_like and c.startswith("Регистр"):
                            return c
                for c in cands_list:
                    if c and not _is_bad_data_object(c):
                        return c
                return ""

            if not name and ranked:
                # выкинуть обработки/отчёты из лидеров data-отчёта
                ranked_good = [
                    x
                    for x in ranked
                    if x.get("full_name")
                    and not _is_bad_data_object(str(x["full_name"]))
                ] or ranked
                if stock_like:
                    reg_first = [
                        x
                        for x in ranked_good
                        if str(x.get("full_name") or "").startswith("Регистр")
                    ]
                    if reg_first:
                        ranked_good = reg_first + [
                            x for x in ranked_good if x not in reg_first
                        ]
                # близкие по смыслу кандидаты → предпочесть тот, где реально есть данные
                ranked_good = self._prefer_by_data_volume(ranked_good, phrase=sq)
                best = ranked_good[0]
                best_sc = int(best.get("_phrase_score") or 0)
                second_sc = (
                    int(ranked_good[1].get("_phrase_score") or 0)
                    if len(ranked_good) > 1
                    else 0
                )
                cands = [
                    str(x.get("full_name"))
                    for x in ranked_good
                    if x.get("full_name")
                ]
                # явный лидер по смыслу фразы
                if best_sc >= 50 and best_sc >= second_sc + 25:
                    name = str(best.get("full_name") or "")
                elif best_sc >= 80:
                    name = str(best.get("full_name") or "")
                elif len(cands) == 1:
                    name = cands[0]
                else:
                    # короткий/неоднозначный запрос — спросить, но с подсказкой
                    hint_top = ""
                    if best_sc > 0:
                        hint_top = f" Наиболее близко: {best.get('full_name')}."
                    return need_clarification(
                        f"Найдено несколько объектов по «{sq}».{hint_top} "
                        f"Какой full_name использовать?",
                        about="object",
                        options=cands[:8],
                        missing=["full_name"],
                        search_query=sq,
                        suggested=str(best.get("full_name") or ""),
                        searches_used=queries[:8],
                    )
            if not name:
                name = _pick_good(cands) or (cands[0] if cands else "")
            if name and _is_bad_data_object(name) and (stock_like or doc_report):
                alt = _pick_good(cands)
                if alt:
                    name = alt
                else:
                    # добор: поиск регистра остатков
                    from tools.business_stems import stock_search

                    meta2 = self.find_metadata(
                        stock_search() if stock_like else sq,
                        "РегистрыНакопления",
                        12,
                    )
                    for i in meta2.get("items") or []:
                        if isinstance(i, dict) and i.get("full_name"):
                            name = str(i["full_name"])
                            break
            resolved = {
                "full_name": name,
                "resolved_from": sq,
                "candidates": cands[:8],
                "via": "search_query",
            }
        elif fn_raw:
            resolved = self.resolve_full_name(fn_raw, prefer=prefer)
            if resolved.get("error"):
                # candidates → уточнение, не «угадай сам»
                cands = resolved.get("candidates") or []
                if cands:
                    return need_clarification(
                        f"«{fn_raw}» не найден. Выберите объект из списка "
                        f"или уточните название:",
                        about="object",
                        options=cands[:8] if isinstance(cands, list) else [cands],
                        missing=["full_name"],
                        error_detail=resolved.get("error"),
                    )
                return need_clarification(
                    f"Не понял объект «{fn_raw}». Уточните смысл разговорно "
                    f"или full_name из find_metadata этой базы.",
                    about="object",
                    missing=["full_name", "search_query"],
                )
            if resolved.get("exact") is False and resolved.get("candidates"):
                cands = resolved.get("candidates") or []
                if len(cands) > 1:
                    return need_clarification(
                        "Несколько похожих объектов. Какой нужен?",
                        about="object",
                        options=cands[:8],
                        missing=["full_name"],
                    )
        else:
            return need_clarification(
                "Уточните, по какому документу/регистру сравнивать "
                "(название) и за какие периоды (год, месяцы).",
                about="object_and_period",
                missing=["search_query", "year", "months"],
            )

        name = str(resolved["full_name"])
        cand_names = list(resolved.get("candidates") or [name])
        if name not in cand_names:
            cand_names.insert(0, name)
        # финальный барьер: не строим data-отчёт по Обработка.ПрайсЛист
        if name.startswith("Обработка.") or name.startswith("Отчет.") or name.startswith(
            "Отчёт."
        ):
            if stock_like or doc_report or any(
                x in blob for x in ("продаж", "остатк", "поступлен", "склад")
            ):
                alt = ""
                for c in cand_names:
                    cs = str(c)
                    if cs and not (
                        cs.startswith("Обработка.")
                        or cs.startswith("Отчет.")
                        or cs.startswith("Отчёт.")
                    ):
                        alt = cs
                        break
                if not alt:
                    qfix = (
                        "товары на складах"
                        if stock_like
                        else (sq or blob or "").strip()
                    )
                    if not qfix:
                        return need_clarification(
                            f"«{name}» — обработка/отчёт UI, не таблица данных. "
                            "Укажите документ или регистр из этой базы "
                            "(search_query=слова из отчёта, не UI-обработку).",
                            options=cand_names[:8],
                        )
                    meta_fix = self.find_metadata(
                        qfix,
                        "РегистрыНакопления" if stock_like else "Документы",
                        12,
                    )
                    for i in meta_fix.get("items") or []:
                        if isinstance(i, dict) and i.get("full_name"):
                            fnx = str(i["full_name"])
                            if not fnx.startswith("Обработка."):
                                alt = fnx
                                break
                if alt:
                    name = alt
                    resolved["full_name"] = name
                    resolved["replaced_bad_object"] = True
                else:
                    return need_clarification(
                        f"«{name}» — обработка/отчёт UI, не таблица остатков/продаж. "
                        "Укажите регистр накопления или документ "
                        "(например search_query=«остатки товары склады»).",
                        about="object",
                        missing=["full_name"],
                        search_query=sq or "остатки товары",
                    )
        # «продажи + контрагент» → документ, у которого есть и сумма, и контрагент
        need_cp = any(
            x in blob
            for x in ("контрагент", "по клиент", "по покупат", "между контрагент", "по контрагент")
        )
        gb_want = (group_by or "").strip()
        vf_l = (value_field or "").strip().lower().replace("ё", "е")

        def _load_doc_fields(fn: str) -> tuple[str, list[Any], Any] | None:
            n2, st, err_st = self._require_structure(fn)
            if err_st or not st:
                return None
            flds = parse_structure_fields(st)
            return n2, flds, st

        picked = None
        best_fit = -10**9
        for rank_i, fn_try in enumerate(cand_names[:10]):
            loaded = _load_doc_fields(str(fn_try))
            if not loaded:
                continue
            n2, flds, st = loaded
            nl = n2.lower().replace("ё", "е")
            if n2.startswith("Справочник.") and doc_report:
                continue
            has_date = match_role(flds, "date") or any(
                f.name.lower() in ("дата", "период") for f in flds if not f.tabular_section
            )
            has_amt = match_role(flds, "amount") or self._resolve_field_by_user(
                flds, value_field, role_fallback="amount"
            )
            has_cp = match_role(flds, "counterparty")
            if doc_report and not has_date:
                continue
            if need_cp and not has_cp:
                continue
            if not has_amt and (
                "sum" in (metrics or "").lower() or "сумм" in (metrics or "").lower()
            ):
                has_amt = next(
                    (f for f in flds if f.kind == "resource" and not f.tabular_section),
                    None,
                )
            fit = 100 - rank_i * 5
            if has_cp:
                fit += 25
            if has_amt:
                fit += 15
            is_goods_svc = "товар" in nl and "услуг" in nl
            # направление: продажи vs закупки — не путать реализацию и поступление
            if sales_like:
                if "реализ" in nl:
                    fit += 70
                if any(x in nl for x in ("поступлен", "приобрет", "закуп")):
                    fit -= 80
            if purchase_like:
                if any(x in nl for x in ("поступлен", "приобрет", "закуп", "приходн")):
                    fit += 70
                if "реализ" in nl or "продаж" in nl:
                    fit -= 80
            # «заказ» → счёт на оплату, не реализация (если не просили продажи)
            if any(x in blob for x in ("заказ", "order")) and not sales_like:
                if "счет" in nl and "оплат" in nl:
                    fit += 90
                if "заказ" in nl:
                    fit += 100
                if "реализ" in nl:
                    fit -= 70
            if goods_flow and is_goods_svc:
                fit += 80
            if goods_flow and any(
                x in nl for x in ("поступленнма", "поступленвлизинг", "нма", "лизинг")
            ):
                if not any(x in blob for x in ("нма", "лизинг", "нематериальн")):
                    fit -= 90
            if goods_flow and any(
                x in nl
                for x in (
                    "нарасчетн",
                    "денежныхдокумент",
                    "изпереработ",
                    "допрасход",
                )
            ):
                if not any(x in blob for x in ("расчет", "банк", "переработ", "доп")):
                    fit -= 70
            # «Отчет*ОПродажах» / комиссия — только если явно просили
            if any(x in nl for x in ("отчет", "отчёт", "комиссионер", "комитент")):
                if not any(x in blob for x in ("отчет", "отчёт", "комисс", "комитент")):
                    fit -= 50
            if "рознич" in nl and "рознич" not in blob:
                fit -= 35
            if "корректиров" in nl and "корректир" not in blob:
                fit -= 60
            if fit > best_fit:
                best_fit = fit
                picked = (n2, flds, st)
        if not picked:
            for fn_try in cand_names[:8]:
                loaded = _load_doc_fields(str(fn_try))
                if loaded and not str(loaded[0]).startswith("Справочник."):
                    picked = loaded
                    break
        if not picked:
            return err(
                str((self._require_structure(name)[2] or {}).get("error") or "structure"),
                hint="get_object_structure",
            )
        name, fields, structure = picked
        assert structure is not None

        cp_state.update(
            {
                "fields": fields,
                "gb_want": gb_want,
                "name": name,
                "resolved": resolved,
                "vf_l": vf_l,
            }
        )
        return None

    def _cp_resolve_fields(self, cp_state: dict[str, Any]) -> dict[str, Any] | None:
        """Поля даты/суммы/проведения, группировки, фильтры по реквизитам."""
        doc_report = cp_state["doc_report"]
        fields = cp_state["fields"]
        filters = cp_state["filters"]
        gb_want = cp_state["gb_want"]
        group_by = cp_state["group_by"]
        is_numeric_type = cp_state["is_numeric_type"]
        match_field_by_words = cp_state["match_field_by_words"]
        match_role = cp_state["match_role"]
        metrics = cp_state["metrics"]
        name = cp_state["name"]
        soft_group_by = cp_state["soft_group_by"]
        value_field = cp_state["value_field"]
        vf_l = cp_state["vf_l"]
        word_stem = cp_state["word_stem"]

        date_f = match_role(fields, "date")
        if not date_f:
            date_f = next(
                (
                    f
                    for f in fields
                    if f.name.lower() in ("период", "дата") and not f.tabular_section
                ),
                None,
            )
        amt_f = self._resolve_field_by_user(
            fields, value_field, role_fallback="amount"
        )
        if not amt_f:
            amt_f = next(
                (f for f in fields if f.kind == "resource" and not f.tabular_section),
                None,
            )
        post_f = match_role(fields, "posted")

        group_f = None
        group_fields: list[Any] = []  # 1–2 разреза (2D: контрагент + номенклатура)
        ts_name = ""  # если group_by из табличной части (номенклатура в ТЧ Товары)
        # multi: «контрагент, номенклатура» / «склад и номенклатура»
        gb_parts = [
            p.strip()
            for p in re.split(r"[,;+/]| и ", gb_want or "")
            if p and p.strip()
        ][:2] or ([gb_want.strip()] if (gb_want or "").strip() else [])
        bad_group = {
            "количество",
            "сумма",
            "count",
            "sum",
            "amount",
            "qty",
            vf_l,
            "month",
            "месяц",
            "period",
            "период",
            "none",
            "-",
            "",
        }

        def _resolve_one_group(gb_one: str) -> Any:
            """Один разрез → field; выставляет ts_name при ТЧ."""
            nonlocal ts_name
            gb_one = (gb_one or "").strip()
            if not gb_one or gb_one.lower().replace("ё", "е") in bad_group:
                return None
            m = re.match(r"^по\s+(.+)$", gb_one, re.I)
            if m:
                gb_one = m.group(1).strip()
            gf = self._resolve_field_by_user(fields, gb_one)
            if gf and gf.name.lower() in (
                "количество",
                "сумма",
                "суммадокумента",
            ):
                gf = None
            if not gf:
                gbl = gb_one.lower().replace("ё", "е")
                if any(
                    x in gbl
                    for x in (
                        "контрагент",
                        "клиент",
                        "покупат",
                        "partner",
                        "поставщик",
                    )
                ):
                    gf = match_role(fields, "counterparty")
                elif any(x in gbl for x in ("организац",)):
                    gf = match_role(fields, "organization")
                elif any(x in gbl for x in ("склад",)):
                    gf = match_role(fields, "warehouse")
                elif any(x in gbl for x in ("номенклатур", "товар", "услуг")):
                    gf = match_role(fields, "nomenclature", header_only=False)
                    if gf and gf.tabular_section:
                        ts_name = gf.tabular_section
                    elif not gf:
                        prefer_ts = ("товары", "услуги", "goods", "products")
                        ts_fields = [f for f in fields if f.tabular_section]
                        for pref in prefer_ts:
                            for f in ts_fields:
                                if f.tabular_section.lower() == pref and (
                                    "номенклатур" in f.name.lower()
                                    or "товар" in f.name.lower()
                                ):
                                    gf = f
                                    ts_name = f.tabular_section
                                    break
                            if gf:
                                break
                        if not gf:
                            for f in ts_fields:
                                if "номенклатур" in f.name.lower():
                                    if "тар" in f.tabular_section.lower():
                                        continue
                                    gf = f
                                    ts_name = f.tabular_section
                                    break
            if not gf:
                # любое поле structure по словам пользователя («менеджерам», «виду операции»)
                gf = match_field_by_words(fields, gb_one, ts_name=ts_name)
                if gf and is_numeric_type(gf):
                    gf = None
            if gf and gf.tabular_section and not ts_name:
                ts_name = gf.tabular_section
            return gf

        for part in gb_parts:
            gf = _resolve_one_group(part)
            if gf and all(gf.name != x.name for x in group_fields):
                group_fields.append(gf)
        group_f = group_fields[0] if group_fields else None
        if gb_parts and not group_fields:
            g_opts = [
                f.name
                for f in fields
                if not f.tabular_section
                and f.kind in ("attribute", "dimension", "standard")
            ][:15]
            ts_opts = sorted(
                {
                    f.tabular_section
                    for f in fields
                    if f.tabular_section and "номенклатур" in f.name.lower()
                }
            )
            bad_obj = name.startswith("Обработка.") or name.startswith("Отчет.")
            hint_obj = ""
            if bad_obj:
                hint_obj = (
                    f" Объект «{name}» — не источник остатков/продаж "
                    "(это обработка UI)."
                )
            return need_clarification(
                f"Поле группировки «{group_by or gb_want}» не найдено в «{name}». "
                f"Укажите точное имя или «без группировки»."
                + (f" Есть ТЧ с номенклатурой: {ts_opts}." if ts_opts else "")
                + hint_obj,
                about="object" if bad_obj else "group_by",
                options=g_opts or None,
                missing=["full_name", "group_by"] if bad_obj else ["group_by"],
                full_name=name if not bad_obj else "",
                suggested_search=(
                    "остатки товары на складах" if bad_obj else None
                ),
            )
        # мягкие разрезы («по X» из речи): берём, только если нашлось измерение в structure
        dropped_groups: list[str] = []
        name_l0 = name.lower().replace("ё", "е")
        for part in [p.strip() for p in re.split(r"[,;+/]", soft_group_by or "") if p.strip()]:
            st0 = word_stem(part.split()[0])
            if len(group_fields) >= 3 or (len(st0) >= 4 and st0 in name_l0):
                dropped_groups.append(part)  # слово объекта («по продажам»), не разрез
                continue
            gf = match_field_by_words(fields, part, ts_name=ts_name, prefix_only=True)
            if gf and not is_numeric_type(gf) and all(gf.name != x.name for x in group_fields):
                if gf.tabular_section and not ts_name:
                    ts_name = gf.tabular_section
                group_fields.append(gf)
            else:
                dropped_groups.append(part)
        group_f = group_fields[0] if group_fields else None
        # фильтры по полям structure → условия ГДЕ; по итоговой метрике → после агрегата
        where_filters: list[tuple[Any, dict[str, Any]]] = []
        post_filters: list[dict[str, Any]] = []
        filters_unresolved: list[dict[str, Any]] = []
        for flt in filters:
            fword = str(flt.get("field") or "").strip()
            fwl = fword.lower().replace("ё", "е")
            op = str(flt.get("op") or "like")
            if re.match(r"(сумм|выручк|оборот)", fwl) and op in (">", "<", ">=", "<=", "eq", "ne"):
                post_filters.append({**flt, "metric": "Сумма"})
                continue
            if re.match(r"(количеств|кол-?во)", fwl) and op in (">", "<", ">=", "<=", "eq", "ne"):
                post_filters.append({**flt, "metric": "Количество"})
                continue
            ff = None
            if fword:
                # префикс имени важнее подстроки («контрагента» → Контрагент, не БанковскийСчетКонтрагента)
                for cand in (fword, fword.split()[-1], fword.split()[0]):
                    ff = match_field_by_words(
                        fields, cand, ts_name=ts_name, prefix_only=True
                    ) or match_field_by_words(fields, cand, ts_name=ts_name)
                    if ff:
                        break
                if not ff:
                    # роль по смыслу (склад/контрагент/организация) из field_roles.yaml
                    for role in ("counterparty", "warehouse", "organization", "nomenclature"):
                        rf = match_role(fields, role, header_only=False)
                        if rf and word_stem(fwl.split()[-1]) in (rf.name + " " + (rf.synonym or "")).lower():
                            ff = rf
                            break
            if ff and ff.tabular_section and ts_name and ff.tabular_section != ts_name:
                ff = None
            t_ff = ((ff.type_str if ff else "") or "").lower().replace("ё", "е")
            if ff and ("перечислен" in t_ff or "enum" in t_ff or "," in t_ff):
                # перечисление / составной тип: нет строкового реквизита → по колонке результата
                filters_unresolved.append({**flt, "field": ff.name})
                if all(ff.name != g.name for g in group_fields) and len(group_fields) < 3:
                    group_fields.append(ff)  # колонка нужна, чтобы отфильтровать строки
                    group_f = group_fields[0]
                    if ff.tabular_section and not ts_name:
                        ts_name = ff.tabular_section
                continue
            if ff:
                if ff.tabular_section and not ts_name:
                    ts_name = ff.tabular_section
                where_filters.append((ff, flt))
            else:
                filters_unresolved.append(flt)

        if not date_f:
            date_opts = [
                f.name
                for f in fields
                if not f.tabular_section
                and (
                    "дата" in f.name.lower()
                    or "период" in f.name.lower()
                    or "date" in f.name.lower()
                )
            ][:10]
            return need_clarification(
                f"В «{name}» не найдено поле даты. Какое поле использовать?",
                about="date_field",
                options=date_opts or None,
                missing=["date_field"],
                full_name=name,
            )
        _ms = (metrics or "sum").lower()
        if not amt_f and ("sum" in _ms or "сумм" in _ms):
            amt_opts = [
                f.name
                for f in fields
                if not f.tabular_section
                and (
                    "сумм" in f.name.lower()
                    or f.kind == "resource"
                    or "amount" in f.name.lower()
                )
            ][:12]
            return need_clarification(
                f"В «{name}» не ясно поле суммы. Укажите value_field.",
                about="value_field",
                options=amt_opts or None,
                missing=["value_field"],
                full_name=name,
            )
        # Справочник без даты документа — нельзя «продажи/закупки за год»
        if name.startswith("Справочник.") and doc_report:
            return need_clarification(
                "Для отчёта по суммам/периоду нужен документ (не справочник). "
                "Укажите документ через search_query / find_metadata.",
                about="object",
                missing=["full_name"],
                hint="quick_find / build_report(request=…)",
            )

        cp_state.update(
            {
                "amt_f": amt_f,
                "date_f": date_f,
                "dropped_groups": dropped_groups,
                "filters_unresolved": filters_unresolved,
                "group_f": group_f,
                "group_fields": group_fields,
                "post_f": post_f,
                "post_filters": post_filters,
                "ts_name": ts_name,
                "where_filters": where_filters,
            }
        )
        return None

    def _cp_parse_periods(self, cp_state: dict[str, Any]) -> dict[str, Any] | None:
        """Разбор периодов и метрик (sum/count/avg)."""
        blob = cp_state["blob"]
        metrics = cp_state["metrics"]
        months = cp_state["months"]
        name = cp_state["name"]
        parse_periods = cp_state["parse_periods"]
        period = cp_state["period"]
        sq = cp_state["sq"]
        stock_like = cp_state["stock_like"]
        time_grain = cp_state["time_grain"]
        year = cp_state["year"]

        # --- периоды (универсальный разбор) ---
        from tools.period_parse import (
            extract_period_spec,
            is_all_time_phrase,
        )
        from datetime import datetime as _dt

        period_spec = (period or "").strip()
        # модель часто шлёт period=2015-01-01 при «за 2015 год» + year=2015
        if re.match(r"^\d{4}-01-01$", period_spec) and (
            (year and int(year) == int(period_spec[:4]))
            or "год" in blob
            or re.search(r"\bза\s+\d{4}\b", blob)
        ):
            period_spec = period_spec[:4]  # → весь год
        if re.match(r"^\d{4}-\d{2}-\d{2}$", period_spec) and "год" in blob:
            period_spec = period_spec[:4]
        if not period_spec and (year or months):
            period_spec = f"{months} {year}".strip() if months else str(year)
        # остатки «на сейчас»
        if stock_like and (
            not period_spec or is_all_time_phrase(period_spec) is False and period_spec in ("",)
        ):
            if not period_spec:
                period_spec = "сейчас"
        # сжать шум / all-time / default years (не «abc по контрагентам» в period_spec)
        if stock_like and period_spec == "сейчас":
            pass
        else:
            period_spec = extract_period_spec(period_spec, blob, sq)

        parsed = parse_periods(
            period_spec,
            legacy_year=int(year) if year and int(year) > 0 else None,
            legacy_months=months or "",
            now=_dt.now(),
        )
        if parsed.get("need_clarification") or parsed.get("error"):
            if stock_like:
                parsed = parse_periods("сейчас", now=_dt.now())
                period_spec = "сейчас"
            elif is_all_time_phrase(blob) or is_all_time_phrase(period_spec):
                parsed = parse_periods("за все время", now=_dt.now())
                period_spec = "за все время"
            else:
                from tools.period_parse import default_period

                period_spec = default_period()
                parsed = parse_periods(period_spec, now=_dt.now())
            if parsed.get("need_clarification") or parsed.get("error"):
                return need_clarification(
                    str(
                        parsed.get("question")
                        or "Уточните период (день, диапазон, месяцы, последняя неделя…)."
                    ),
                    about="period",
                    missing=["period"],
                    examples=list(parsed.get("examples") or [])
                    + ["за все время", "последний год", "прошлый месяц"],
                    full_name=name,
                )
        period_slices = parsed.get("slices") or []
        if not period_slices:
            return need_clarification(
                "Не удалось получить срезы периода. Уточните даты.",
                about="period",
                missing=["period"],
                full_name=name,
            )
        grain_used = ""
        if time_grain:
            from tools.period_parse import split_by_grain

            period_slices, grain_used = split_by_grain(period_slices, time_grain)

        want_sum = "sum" in (metrics or "sum").lower() or "сумм" in (metrics or "").lower()
        want_cnt = "count" in (metrics or "").lower() or "колич" in (metrics or "").lower()
        if not want_sum and not want_cnt:
            want_sum, want_cnt = True, True
        want_avg = any(x in (metrics or "").lower() for x in ("avg", "средн"))

        rows_out: list[dict[str, Any]] = []
        queries_used: list[str] = []
        period_totals_exact: list[dict[str, Any]] = []

        cp_state.update(
            {
                "grain_used": grain_used,
                "parsed": parsed,
                "period_slices": period_slices,
                "period_spec": period_spec,
                "period_totals_exact": period_totals_exact,
                "queries_used": queries_used,
                "rows_out": rows_out,
                "want_avg": want_avg,
                "want_cnt": want_cnt,
                "want_sum": want_sum,
            }
        )
        return None

    def _cp_run_queries(self, cp_state: dict[str, Any]) -> dict[str, Any] | None:
        """ВЫБРАТЬ по каждому периоду, пост-фильтры, сортировка."""
        amt_f = cp_state["amt_f"]
        date_f = cp_state["date_f"]
        fields = cp_state["fields"]
        filters_unresolved = cp_state["filters_unresolved"]
        group_f = cp_state["group_f"]
        group_fields = cp_state["group_fields"]
        is_numeric_type = cp_state["is_numeric_type"]
        is_reference_type = cp_state["is_reference_type"]
        match_role = cp_state["match_role"]
        name = cp_state["name"]
        only_posted = cp_state["only_posted"]
        period_slices = cp_state["period_slices"]
        period_totals_exact = cp_state["period_totals_exact"]
        post_f = cp_state["post_f"]
        post_filters = cp_state["post_filters"]
        queries_used = cp_state["queries_used"]
        rows_out = cp_state["rows_out"]
        sort = cp_state["sort"]
        top_n = cp_state["top_n"]
        ts_name = cp_state["ts_name"]
        vf_l = cp_state["vf_l"]
        want_avg = cp_state["want_avg"]
        want_cnt = cp_state["want_cnt"]
        want_sum = cp_state["want_sum"]
        where_filters = cp_state["where_filters"]

        # отчёт по строкам ТЧ (номенклатура и т.п.)
        from_table = f"{name}.{ts_name}" if ts_name else name
        # поля суммы/кол-ва в ТЧ, если группируем по строкам
        ts_amt_f = amt_f
        ts_qty_f = None
        if ts_name:
            ts_flds = [f for f in fields if f.tabular_section == ts_name]
            ts_amt_f = next(
                (
                    f
                    for f in ts_flds
                    if f.name.lower() in ("сумма", "суммавсего")
                    or (f.name.lower().startswith("сумма") and "ндс" not in f.name.lower())
                ),
                None,
            ) or match_role(ts_flds, "amount", header_only=False)
            ts_qty_f = next(
                (f for f in ts_flds if f.name.lower() in ("количество", "qty")),
                None,
            )
        # «сколько услуг» + value_field=Количество → СУММА(Количество), не COUNT документов
        qty_f = None
        if ts_name and ts_qty_f:
            qty_f = ts_qty_f
        elif vf_l in ("количество", "qty", "колво") or (
            amt_f and amt_f.name.lower() in ("количество",)
        ):
            qty_f = amt_f if amt_f and "колич" in amt_f.name.lower() else (
                self._resolve_field_by_user(fields, "Количество")
            )
        use_amt = ts_amt_f if ts_name else amt_f
        def _filter_conds() -> list[str]:
            conds: list[str] = []
            for ff, flt in where_filters:
                path = f"Т.Ссылка.{ff.name}" if ts_name and not ff.tabular_section else f"Т.{ff.name}"
                op = str(flt.get("op") or "like")
                val = flt.get("value")
                num = None
                try:
                    num = float(str(val).replace(" ", "").replace(",", "."))
                except (TypeError, ValueError):
                    pass
                if op in (">", "<", ">=", "<=") and num is not None:
                    c = f"{path} {op} {num:g}"
                elif op in ("eq", "ne") and num is not None and is_numeric_type(ff):
                    c = f"{path} {'=' if op == 'eq' else '<>'} {num:g}"
                else:
                    esc = str(val).replace('"', '""')
                    # ПРЕДСТАВЛЕНИЕ() в ПОДОБНО 1С не принимает → строковый реквизит ссылки
                    t_l = (ff.type_str or "").lower().replace(" ", "")
                    if not is_reference_type(ff):
                        expr = path
                    elif "документ" in t_l or "documentref" in t_l:
                        expr = f"{path}.Номер"
                    else:
                        expr = f"{path}.Наименование"
                    c = f'{expr} ПОДОБНО "%{esc}%"'
                    if op == "ne" or flt.get("neg"):
                        c = f"НЕ {c}"
                    conds.append(c)
                    continue
                if flt.get("neg"):
                    c = f"НЕ ({c})"
                conds.append(c)
            return conds

        def _period_where(pslice: Any) -> tuple[str, Any, str, str] | None:
            label = getattr(pslice, "label", None) or str(pslice.get("label", ""))
            if not hasattr(pslice, "datavremya_start"):
                return None
            start_dv = pslice.datavremya_start()
            end_dv = pslice.datavremya_end()
            start_d = pslice.start
            if ts_name:
                where = f"Т.Ссылка.{date_f.name} МЕЖДУ {start_dv} И {end_dv}"
                if only_posted and post_f and name.startswith("Документ."):
                    where += f" И Т.Ссылка.{post_f.name} = ИСТИНА"
            else:
                where = f"Т.{date_f.name} МЕЖДУ {start_dv} И {end_dv}"
                if only_posted and post_f and name.startswith("Документ."):
                    where += f" И Т.{post_f.name} = ИСТИНА"
            for c in _filter_conds():
                where += f" И {c}"
            return label, start_d, where, end_dv

        def _agg_select_parts(*, with_group: bool) -> tuple[list[str], list[str]]:
            select_parts: list[str] = []
            group_parts: list[str] = []
            if with_group and group_fields:
                for gf in group_fields:
                    # ТЧ: шапочные поля через Т.Ссылка.X
                    if ts_name and not gf.tabular_section:
                        path = f"Т.Ссылка.{gf.name}"
                    else:
                        path = f"Т.{gf.name}"
                    if is_reference_type(gf):
                        select_parts.append(
                            f"ПРЕДСТАВЛЕНИЕ({path}) КАК {gf.name}"
                        )
                    else:
                        select_parts.append(f"{path} КАК {gf.name}")
                    group_parts.append(path)
            if qty_f and (want_cnt or want_sum) and vf_l in (
                "количество",
                "qty",
                "колво",
            ):
                select_parts.append(f"СУММА(Т.{qty_f.name}) КАК Количество")
            else:
                if want_sum and use_amt:
                    select_parts.append(f"СУММА(Т.{use_amt.name}) КАК Сумма")
                if want_avg and use_amt:
                    select_parts.append(f"СРЕДНЕЕ(Т.{use_amt.name}) КАК Среднее")
                if want_cnt:
                    if ts_name and qty_f:
                        select_parts.append(f"СУММА(Т.{qty_f.name}) КАК Количество")
                    elif name.startswith("Регистр") and not ts_name:
                        select_parts.append("КОЛИЧЕСТВО(*) КАК Количество")
                    elif ts_name:
                        select_parts.append("КОЛИЧЕСТВО(*) КАК Количество")
                    else:
                        select_parts.append("КОЛИЧЕСТВО(Т.Ссылка) КАК Количество")
            return select_parts, group_parts

        from tools.period_parse import is_all_time_phrase as _is_all_time

        def _period_meta_row(label: str, pslice: Any) -> dict[str, Any]:
            """
            Период / даты всегда.
            Год/Месяц — только если срез реально один месяц или один год.
            «за все время» и диапазоны >1 года: НЕ писать start.year/month
            (иначе в Excel везде 2011 / 1 — вводит в заблуждение).
            """
            lab = str(label or "")
            start_d = pslice.start
            end_d = pslice.end
            row: dict[str, Any] = {
                "Период": lab,
                "ДатаНачала": start_d.strftime("%Y-%m-%d"),
                "ДатаКонца": end_d.strftime("%Y-%m-%d"),
            }
            if _is_all_time(lab) or "все время" in lab.lower().replace("ё", "е"):
                return row
            same_month = (
                start_d.year == end_d.year and start_d.month == end_d.month
            )
            same_year = start_d.year == end_d.year
            # срез «2015» / весь год
            year_only = bool(re.fullmatch(r"(?:19|20)\d{2}", lab.strip()))
            month_label = bool(re.fullmatch(r"(?:19|20)\d{2}-\d{2}", lab.strip()))
            if same_month or month_label:
                row["Год"] = start_d.year
                row["Месяц"] = start_d.month
            elif same_year or year_only:
                row["Год"] = start_d.year
                # Месяц не ставим: это весь год, не «январь»
            # иначе multi-year range — только даты
            return row

        for pslice in period_slices:
            pw = _period_where(pslice)
            if not pw:
                continue
            label, start_d, where, _end_dv = pw
            base_row = _period_meta_row(label, pslice)

            # --- точные итоги периода (без group_by), всегда 1 строка ---
            tot_sel, _ = _agg_select_parts(with_group=False)
            if tot_sel:
                q_tot = (
                    "ВЫБРАТЬ ПЕРВЫЕ 1 "
                    + ", ".join(tot_sel)
                    + f" ИЗ {from_table} КАК Т ГДЕ {where}"
                )
                queries_used.append(q_tot)
                prep_t = self._prepare_1c_query(q_tot)
                tot_item = dict(base_row)
                if prep_t.get("error"):
                    tot_item["error"] = prep_t["error"]
                else:
                    rt = self.backend.execute_query(prep_t["query"], 1)
                    if not isinstance(rt, dict) or rt.get("error"):
                        tot_item["error"] = str((rt or {}).get("error") or rt)
                    else:
                        data_t = rt.get("data") or rt.get("rows") or []
                        raw_t = data_t[0] if data_t and isinstance(data_t[0], dict) else {}
                        if want_sum:
                            sv = raw_t.get("Сумма")
                            if sv is None:
                                for k, v in raw_t.items():
                                    if "сумм" in str(k).lower():
                                        sv = v
                                        break
                            tot_item["Сумма"] = sv if sv is not None else 0
                        if want_cnt:
                            tot_item["Количество"] = raw_t.get("Количество") or 0
                        if want_avg and raw_t.get("Среднее") is not None:
                            tot_item["Среднее"] = raw_t.get("Среднее")
                period_totals_exact.append(tot_item)

            # --- detail: group_by top-N (или 1 строка без группировки) ---
            select_parts, group_parts = _agg_select_parts(with_group=bool(group_f))
            if not select_parts:
                return err("metrics пустые — укажи sum и/или count")

            # multi-period + group_by: достаточно групп для variance (оба года),
            # в LLM/excel detail всё равно balance; раньше lim=12 → ложные «2015=0»
            n_slices = max(1, len(period_slices))
            if group_f:
                lim = 120 if n_slices >= 2 else 80
                # топ/антитоп N в запросе (фильтр по метрике — после агрегата, берём с запасом)
                if top_n and not post_filters:
                    lim = max(1, int(top_n))
            else:
                lim = 1
            q = (
                f"ВЫБРАТЬ ПЕРВЫЕ {lim} "
                + ", ".join(select_parts)
                + f" ИЗ {from_table} КАК Т ГДЕ {where}"
            )
            if group_parts:
                q += " СГРУППИРОВАТЬ ПО " + ", ".join(group_parts)
            # sort=name — после выборки (ПРЕДСТАВЛЕНИЕ в УПОРЯДОЧИТЬ 1С не разрешает)
            sort_l = (sort or "").strip().lower()
            direction = "ВОЗР" if sort_l in ("asc", "sum_asc", "возр") else "УБЫВ"
            if any("КАК Сумма" in s for s in select_parts):
                q += f" УПОРЯДОЧИТЬ ПО Сумма {direction}"
            elif any("КАК Количество" in s for s in select_parts):
                q += f" УПОРЯДОЧИТЬ ПО Количество {direction}"

            queries_used.append(q)
            prep = self._prepare_1c_query(q)
            if prep.get("error"):
                rows_out.append({**base_row, "error": prep["error"]})
                continue
            result = self.backend.execute_query(prep["query"], lim)
            if not isinstance(result, dict) or result.get("error"):
                rows_out.append(
                    {
                        **base_row,
                        "error": str((result or {}).get("error") or result),
                    }
                )
                continue
            data = result.get("data") or result.get("rows") or []
            if not data:
                row = dict(base_row)
                for gf in group_fields:
                    row[gf.name] = None
                if want_sum:
                    row["Сумма"] = 0
                if want_cnt:
                    row["Количество"] = 0
                rows_out.append(row)
                continue
            for raw_row in data:
                if not isinstance(raw_row, dict):
                    continue
                row = dict(base_row)
                for gf in group_fields:
                    row[gf.name] = raw_row.get(gf.name)
                if want_sum:
                    sv = raw_row.get("Сумма")
                    if sv is None:
                        for k, v in raw_row.items():
                            if "сумм" in str(k).lower():
                                sv = v
                                break
                    row["Сумма"] = sv if sv is not None else 0
                if want_cnt:
                    row["Количество"] = raw_row.get("Количество") or 0
                if want_avg and "Среднее" in raw_row:
                    row["Среднее"] = raw_row.get("Среднее")
                rows_out.append(row)
        # фильтр по итоговой метрике («сумма больше 100 тыс») — HAVING по строкам
        if post_filters:
            def _keep(r: dict[str, Any]) -> bool:
                for pf in post_filters:
                    try:
                        v = float(r.get(pf["metric"]) or 0)
                        t = float(str(pf.get("value")).replace(" ", "").replace(",", "."))
                    except (TypeError, ValueError):
                        continue
                    ok = {
                        ">": v > t, "<": v < t, ">=": v >= t, "<=": v <= t,
                        "eq": v == t, "ne": v != t,
                    }.get(str(pf.get("op")), True)
                    if not ok:
                        return False
                return True

            rows_out = [
                r for r in rows_out if not isinstance(r, dict) or r.get("error") or _keep(r)
            ]
            if top_n and group_f:
                per_cnt: dict[str, int] = {}
                kept = []
                for r in rows_out:
                    k = str(r.get("Период") or "")
                    per_cnt[k] = per_cnt.get(k, 0) + 1
                    if per_cnt[k] <= int(top_n):
                        kept.append(r)
                rows_out = kept
        # значение без распознанного поля («только Ромашка») — поиск по колонкам разрезов
        for fu in filters_unresolved:
            needle = str(fu.get("value") or "").lower().replace("ё", "е")
            if not needle or not group_fields:
                continue
            neg = bool(fu.get("neg")) or fu.get("op") == "ne"
            rows_out = [
                r
                for r in rows_out
                if (
                    needle
                    in " ".join(str(r.get(g.name) or "") for g in group_fields)
                    .lower()
                    .replace("ё", "е")
                )
                != neg
            ]

        if (sort or "").strip().lower() == "name" and group_fields:
            p_order = {str(getattr(s, "label", "")): i for i, s in enumerate(period_slices)}
            g0 = group_fields[0].name
            rows_out.sort(
                key=lambda r: (
                    p_order.get(str(r.get("Период") or ""), 0),
                    str(r.get(g0) or "").lower(),
                )
            )

        cp_state.update(
            {
                "rows_out": rows_out,
                "ts_amt_f": ts_amt_f,
            }
        )
        return None

    def _cp_build_result(self, cp_state: dict[str, Any]) -> dict[str, Any]:
        """Сводка по периодам, дельта, Excel, итоговый ответ."""
        amt_f = cp_state["amt_f"]
        date_f = cp_state["date_f"]
        dropped_groups = cp_state["dropped_groups"]
        filename = cp_state["filename"]
        filters_unresolved = cp_state["filters_unresolved"]
        grain_used = cp_state["grain_used"]
        group_by = cp_state["group_by"]
        group_f = cp_state["group_f"]
        group_fields = cp_state["group_fields"]
        metrics = cp_state["metrics"]
        name = cp_state["name"]
        parsed = cp_state["parsed"]
        period_slices = cp_state["period_slices"]
        period_spec = cp_state["period_spec"]
        period_totals_exact = cp_state["period_totals_exact"]
        post_filters = cp_state["post_filters"]
        queries_used = cp_state["queries_used"]
        resolved = cp_state["resolved"]
        rows_out = cp_state["rows_out"]
        save_rows_excel = cp_state["save_rows_excel"]
        sort = cp_state["sort"]
        title = cp_state["title"]
        to_excel = cp_state["to_excel"]
        top_n = cp_state["top_n"]
        ts_amt_f = cp_state["ts_amt_f"]
        ts_name = cp_state["ts_name"]
        want_avg = cp_state["want_avg"]
        want_cnt = cp_state["want_cnt"]
        want_sum = cp_state["want_sum"]
        where_filters = cp_state["where_filters"]

        columns = ["Период", "ДатаНачала", "ДатаКонца"]
        # Год/Месяц — только если реально заполнены (не all-time)
        if any(r.get("Год") is not None for r in rows_out if isinstance(r, dict)):
            columns.append("Год")
        if any(r.get("Месяц") is not None for r in rows_out if isinstance(r, dict)):
            columns.append("Месяц")
        for gf in group_fields:
            columns.append(gf.name)
        if want_sum:
            columns.append("Сумма")
        if want_cnt:
            columns.append("Количество")
        if want_avg:
            columns.append("Среднее")

        labels = [str(getattr(s, "label", "") or "") for s in period_slices]
        from tools.report_format import (
            balance_period_rows,
            enrich_report_result,
        )
        # detail: чередование периодов; итоги — из точного запроса без group_by
        _, rows_balanced, rows_meta = balance_period_rows(
            rows_out,
            labels,
            per_period_cap=(int(top_n) if top_n else 8) if group_f else 1,
            # динамика по шагам: строк больше (Excel/график по всем шагам)
            total_cap=(200 if grain_used else 24) if group_f else max(4, len(labels)),
            want_sum=want_sum,
            want_cnt=want_cnt,
        )
        rows_for_llm = rows_balanced if group_f and len(period_slices) >= 2 else rows_out
        # точные totals (не сумма top-N)
        if period_totals_exact:
            summary_by_period = []
            for t in period_totals_exact:
                item: dict[str, Any] = {"Период": t.get("Период")}
                if want_sum:
                    item["Сумма"] = t.get("Сумма", 0)
                if want_cnt:
                    item["Количество"] = t.get("Количество", 0)
                if want_avg and t.get("Среднее") is not None:
                    item["Среднее"] = t.get("Среднее")
                if t.get("error"):
                    item["error"] = t["error"]
                # сколько групп в detail для этого периода
                lab = str(t.get("Период") or "")
                item["groups_shown"] = sum(
                    1 for r in rows_for_llm if str(r.get("Период") or "") == lab
                )
                summary_by_period.append(item)
        else:
            summary_by_period, _, rows_meta = balance_period_rows(
                rows_out,
                labels,
                per_period_cap=80,
                total_cap=500,
                want_sum=want_sum,
                want_cnt=want_cnt,
            )

        delta_note = None
        delta_val = None
        if want_sum and len(period_slices) >= 2 and not grain_used:
            by_label: dict[str, float] = {}
            for item in summary_by_period:
                try:
                    by_label[str(item.get("Период"))] = float(item.get("Сумма") or 0)
                except (TypeError, ValueError):
                    pass
            if len(by_label) >= 2 and len(labels) >= 2:
                a, b = labels[0], labels[1]
                delta_val = by_label.get(b, 0) - by_label.get(a, 0)
                delta_note = (
                    f"{b} − {a} = {delta_val} (поле {amt_f.name if amt_f else '?'})"
                )

        period_list = ", ".join(labels) if labels else period_spec
        prefer_cmp = (
            f"Сравнение периодов: {period_list}. "
            "1) Сначала таблица summary_by_period (итого по КАЖДОМУ периоду). "
            "2) Затем detail rows (топ контрагентов/групп, периоды чередуются). "
            "MUST: покажи ВСЕ периоды из summary_by_period — не только первый. "
            "НЕ пиши «уточните другой год» / «только 2016» — оба уже в результате. "
            "Не вызывай export_* без просьбы о файле."
        )
        note_cmp = (
            "Универсальные периоды. "
            + (
                f"rows: топ до {rows_meta.get('per_period_cap', 8)} на период "
                f"(raw={rows_meta.get('rows_total_raw')}); "
                "полные итоги — summary_by_period. "
                if group_f and len(period_slices) >= 2
                else ""
            )
            + (
                "Файл НЕ создан (to_excel=false). "
                if not to_excel
                else "Результат также в Excel. "
            )
            + "Ответь: итоги по периодам + таблица rows."
        )
        # ключи в порядке важности для compact_json (приоритет + порядок dump)
        summary: dict[str, Any] = {
            "ok": True,
            "full_name": name,
            "period_spec": period_spec,
            "periods": parsed.get("periods"),
            "summary_by_period": summary_by_period,
            "prefer": prefer_cmp,
            "note": note_cmp,
            "value_field": (
                (ts_amt_f.name if ts_name and ts_amt_f else None)
                or (amt_f.name if amt_f else None)
            ),
            "date_field": date_f.name,
            "group_by": group_f.name if group_f else None,
            "tabular_section": ts_name or None,
            "metrics": metrics,
            "columns": columns,
            "rows": rows_for_llm,
            "count": len(rows_for_llm),
            "rows_meta": rows_meta,
            "to_excel": bool(to_excel),
        }
        # что из «как просили» применено — для плана/ответа
        if grain_used:
            summary["time_grain"] = grain_used
            summary["prefer"] = (
                f"Динамика по шагам ({grain_used}): summary_by_period — итог каждого шага "
                "(таблица/график по порядку), затем rows. " + str(summary.get("prefer") or "")
            )
        if where_filters or post_filters:
            summary["filters_applied"] = [
                {"field": ff.name, **{k: v for k, v in flt.items() if k != "field"}}
                for ff, flt in where_filters
            ] + post_filters
        if filters_unresolved:
            summary["filters_unresolved"] = filters_unresolved
            summary["note"] = (
                str(summary.get("note") or "")
                + f" Фильтр(ы) без поля в «{name}»: "
                + ", ".join(
                    f"{f.get('field') or '*'}={f.get('value')}" for f in filters_unresolved
                )
                + " — применены как текстовый поиск по разрезам (если есть)."
            )
        if sort:
            summary["sort"] = sort
        if top_n:
            summary["top_n_applied"] = int(top_n)
        if dropped_groups:
            summary["dropped_group_by"] = dropped_groups
        if delta_note is not None:
            summary["delta"] = delta_val
            summary["delta_note"] = delta_note
        if queries_used:
            summary["queries"] = queries_used[:8]
        if resolved.get("resolved_from") and str(resolved.get("resolved_from")) != name:
            summary["resolved_from"] = resolved.get("resolved_from")
        if resolved.get("candidates"):
            summary["candidates"] = resolved["candidates"]
        # variance / contribution по ПОЛНЫМ rows_out (не по урезанным rows_for_llm)
        try:
            from tools.analyst_core import build_variance_contribution
            from tools.analyst_reports import enrich_compare_with_analytics

            if group_f and len(period_slices) >= 2 and not grain_used:
                var_full = build_variance_contribution(
                    [r for r in rows_out if isinstance(r, dict)],
                    group_key=group_f.name,
                    value_key="Сумма",
                    top_n=12,
                )
                if var_full.get("ok"):
                    # точные period totals
                    if summary_by_period and len(summary_by_period) >= 2:
                        by_lab = {
                            str(p.get("Период")): float(p.get("Сумма") or 0)
                            for p in summary_by_period
                            if isinstance(p, dict)
                        }
                        a0, b0 = var_full.get("period_base"), var_full.get(
                            "period_current"
                        )
                        if a0 in by_lab and b0 in by_lab:
                            var_full["total_base"] = by_lab[a0]
                            var_full["total_current"] = by_lab[b0]
                            var_full["delta"] = round(by_lab[b0] - by_lab[a0], 2)
                            if by_lab[a0]:
                                var_full["delta_pct"] = round(
                                    (by_lab[b0] - by_lab[a0]) / by_lab[a0] * 100, 1
                                )
                    summary["top_growth"] = var_full.get("top_growth")
                    summary["top_decline"] = var_full.get("top_decline")
                    summary["contribution"] = (var_full.get("contribution") or [])[:20]
                    if len(var_full.get("contribution") or []) > 20:
                        summary["contribution_total"] = len(var_full["contribution"])
                    summary["variance"] = {
                        k: var_full[k]
                        for k in (
                            "period_base",
                            "period_current",
                            "total_base",
                            "total_current",
                            "delta",
                            "delta_pct",
                            "group_key",
                        )
                        if k in var_full
                    }
                    summary["delta"] = var_full.get("delta")
                    summary["delta_pct"] = var_full.get("delta_pct")
                    # note: contribution from full top-N per year (lim=120)
                    summary["variance_note"] = (
                        "Дельта по контрагентам из top-120 групп каждого периода "
                        "(не только 8 строк чата). "
                        "Статус «новый» = 0 в базовом периоде; "
                        "Дельта_% пусто если база 0 (не «100%»)."
                    )
            summary = enrich_compare_with_analytics(summary)
        except Exception:
            try:
                from tools.analyst_reports import enrich_compare_with_analytics

                summary = enrich_compare_with_analytics(summary)
            except Exception:
                pass

        if to_excel:
            try:
                from tools.excel_export import save_analyst_excel

                charts = True
                sheets = []
                if summary.get("summary_by_period"):
                    sheets.append(
                        {
                            "name": "Итоги",
                            "title": "Итоги по периодам",
                            "rows": summary["summary_by_period"],
                            "chart": "bar" if charts else None,
                            "chart_value": "Сумма",
                        }
                    )
                if summary.get("top_growth"):
                    sheets.append(
                        {
                            "name": "Рост",
                            "title": "Топ роста (база>0) и новые",
                            "rows": summary["top_growth"],
                            "chart": "bar" if charts else None,
                            "chart_value": "Дельта",
                        }
                    )
                if summary.get("top_decline"):
                    sheets.append(
                        {
                            "name": "Падение",
                            "title": "Топ падения",
                            "rows": summary["top_decline"],
                            "chart": "bar" if charts else None,
                            "chart_value": "Дельта",
                        }
                    )
                # ABC по текущему/суммарному year from contribution
                try:
                    from tools.analyst_core import build_abc

                    abc_rows_src = []
                    cur = (summary.get("variance") or {}).get("period_current")
                    for r in rows_out:
                        if not isinstance(r, dict):
                            continue
                        if cur and str(r.get("Период")) != str(cur):
                            continue
                        abc_rows_src.append(r)
                    if not abc_rows_src:
                        abc_rows_src = [
                            r for r in rows_out if isinstance(r, dict)
                        ]
                    abc = build_abc(
                        abc_rows_src,
                        group_key=group_f.name if group_f else "",
                        value_key="Сумма",
                    )
                    if abc.get("ok") and abc.get("rows"):
                        sheets.append(
                            {
                                "name": "ABC",
                                "title": f"ABC-анализ ({cur or 'период'})",
                                "rows": abc["rows"],
                                "chart": "pie" if charts else None,
                                "chart_value": "Сумма",
                                "pie_top": 10,
                            }
                        )
                except Exception:
                    pass
                sheets.append(
                    {
                        "name": "Детализация",
                        "title": title or f"{name}: {period_spec}",
                        "rows": [
                            {k: v for k, v in r.items() if k != "error"}
                            for r in rows_out
                            if isinstance(r, dict)
                        ],
                        "chart": None,
                    }
                )
                saved = save_analyst_excel(
                    title=title or f"Сравнение {period_spec}",
                    sheets=sheets,
                    bullets=summary.get("analyst_summary") or [],
                    meta_extra={
                        "full_name": name,
                        "period": period_spec,
                        "group_by": group_f.name if group_f else "",
                    },
                    filename=filename
                    or f"compare_{name.split('.')[-1]}_{period_slices[0].label}.xlsx",
                )
            except Exception:
                saved = save_rows_excel(
                    [{k: v for k, v in r.items() if k != "error"} for r in rows_out],
                    filename=filename
                    or f"compare_{name.split('.')[-1]}_{period_slices[0].label}.xlsx",
                    title=title or f"{name}: {period_spec}",
                    sheet_name="Сравнение",
                    charts=True,
                )
            if saved.get("error"):
                summary["excel_error"] = saved["error"]
            else:
                for k in (
                    "path",
                    "filename",
                    "folder",
                    "file_uri",
                    "download_url",
                    "downloads_path",
                    "how_to_open",
                    "prefer",
                    "explorer_opened",
                    "charts",
                    "styled",
                    "sheets",
                ):
                    if saved.get(k) is not None:
                        summary[k] = saved[k]
                summary["note"] = (
                    "Excel (сводка"
                    + (" + графики" if saved.get("charts") else "")
                    + ") сохранён. Покажи path и download_url "
                    "(только из ответа tools). НЕ example.com."
                )
                if "prefer" in saved:
                    summary["prefer"] = prefer_cmp + " " + str(saved.get("prefer") or "")

        return enrich_report_result(
            summary,
            request=f"{period_spec} {group_by}".strip(),
            full_name=name,
            period=period_spec,
            report_mode="compare_periods",
        )
