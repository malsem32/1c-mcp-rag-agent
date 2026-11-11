# -*- coding: utf-8 -*-
"""Поиск объектов метаданных: resolve_object, find_metadata, списки типов/объектов."""

from __future__ import annotations

import re
from typing import Any

from backends.base import METADATA_TYPES, normalize_metadata_type
from tools.intent_search import suggest_objects as intent_suggest_objects
from tools.responses import err, need_clarification, slim_meta_item
from tools.uni.safety import clamp_limit


class MetadataMixin:
    def route_intent(self, request: str = "") -> dict[str, Any]:
        """Универсальный роутер: data | code | help | meta → prefer_tool."""
        from tools.route_intent import route_user_message

        out = route_user_message(request or "")
        out["ok"] = True
        out["prefer"] = (
            f"Вызови {out.get('prefer_tool')} по action_for_agent. "
            "Не игнорируй route."
        )
        return out

    # Классы метаданных платформы 1С (широкий поиск — любая конфигурация).
    _META_CLASSES_PRIMARY: tuple[str, ...] = (
        "Документы",
        "Справочники",
        "РегистрыСведений",
        "РегистрыНакопления",
        "Отчеты",
        "Обработки",
        "ОбщиеФормы",
        "ОбщиеМодули",
        "Роли",
    )

    def _vocab_extra_queries(self, phrase: str) -> list[str]:
        """Доп. слова из knowledge/словарь_бизнеса.yaml (не хардкод имён объектов)."""
        from tools.intent_search import has_any

        extra: list[str] = []
        pl = (phrase or "").lower().replace("ё", "е")
        try:
            from kb.store import get_business_vocab

            intents = get_business_vocab().get("intents") or {}
        except Exception:
            intents = {}
        for _key, words in intents.items():
            if not isinstance(words, list):
                continue
            if has_any(pl, words):
                for w in words:
                    w = str(w).strip()
                    if len(w) >= 3 and w not in pl:
                        extra.append(w)
        return extra[:12]

    def _merge_meta_hits(
        self,
        merged: dict[str, dict[str, Any]],
        items: list[Any] | None,
    ) -> None:
        for i in items or []:
            if not isinstance(i, dict):
                continue
            fn = str(i.get("full_name") or "")
            if not fn:
                continue
            prev = merged.get(fn)
            try:
                sc = float(i.get("score") or i.get("_score") or 0)
            except (TypeError, ValueError):
                sc = 0.0
            if prev is None or sc > float(prev.get("score") or 0):
                row = dict(i)
                row["score"] = sc
                merged[fn] = row

    def _classes_for_resolve(self, phrase: str, object_type: str) -> list[str]:
        """Классы метаданных по словам платформы — см. route_intent.classes_for_phrase."""
        from tools.route_intent import classes_for_phrase

        return classes_for_phrase(phrase, object_type)

    def _resolve_from_catalog(self, q: str, object_type: str, lim: int) -> dict[str, Any] | None:
        """resolve_full_name по каталогу выгрузки; None — каталога нет/ничего не нашли (старый путь)."""
        try:
            from backends.base import METADATA_TYPES, normalize_metadata_type
            from tools.object_catalog import resolve_full_name
        except Exception:
            return None
        prefix = ""
        canon = normalize_metadata_type(object_type or "") if object_type and object_type != "Все" else None
        if canon:
            prefix = METADATA_TYPES[canon].get("full_name_prefix") or ""
        try:
            r = resolve_full_name(q, class_prefix=prefix, top_k=max(5, min(lim, 15)))
        except Exception:
            return None
        cands = r.get("candidates") or []
        if not cands:
            return None
        items = [
            {
                "full_name": c["full_name"],
                "name": c["full_name"].split(".", 1)[-1],
                "type": c["full_name"].split(".", 1)[0],
                "synonym": c.get("synonym") or "",
                "score": c.get("score"),
            }
            for c in cands
        ]
        labels = [f"{i['full_name']}" + (f" — {i['synonym']}" if i["synonym"] else "") for i in items[:6]]
        out: dict[str, Any] = {
            "ok": True,
            "status": "found_one" if r.get("confident") else "found_many",
            "request": q,
            "count": len(items),
            "items": items,
            "top": r["full_name"],
            "full_name": r["full_name"] if r.get("confident") else "",
            "confident": bool(r.get("confident")),
            "source": "object_catalog",
            "matched_by": r.get("by"),
        }
        if not r.get("confident"):
            out.update(
                need_clarification=True,
                suggested=r["full_name"],
                question=f"По «{q}» подходят несколько объектов. Какой нужен?\n" + "\n".join(f"• {lb}" for lb in labels[:5]),
                options=[i["full_name"] for i in items[:5]],
                options_labeled=labels[:5],
                action_for_agent=(
                    "Покажи пользователю варианты (с синонимами) и предложи самый вероятный (suggested). "
                    "Если вопрос однозначно про suggested — можно продолжать с ним."
                ),
            )
        return out

    def resolve_object(
        self,
        request: str = "",
        object_type: str = "Все",
        limit: int = 15,
        offset: int = 0,
        page: int = 0,
    ) -> dict[str, Any]:
        """
        Универсальный поиск объекта конфигурации по фразе.
        limit/offset/page — порция options (модель: не нашёл в 15 → offset=15).
        """
        from tools.intent_search import expand_search_queries
        from tools.paging import attach_paging, clamp_limit, resolve_offset, slice_page

        q = (request or "").strip()
        if not q:
            return need_clarification(
                "Что найти в конфигурации? Опишите объект или задачу своими словами.",
                about="request",
                missing=["request"],
            )

        # Код/источник данных (единый роутер) — не resolve, а inspect_code
        from tools.route_intent import ROUTE_CODE, route_user_message

        routed = route_user_message(q)
        if routed.get("route") == ROUTE_CODE:
            out = self.inspect_code(request=q)
            if isinstance(out, dict):
                out["routed_from"] = "resolve_object"
                out["route"] = ROUTE_CODE
            return out

        lim = clamp_limit(limit, default=15, maximum=80)
        off = resolve_offset(
            offset=offset, page=page if page else None, limit=lim
        )

        # каталог объектов этой CF (имя + класс + синоним из выгрузки) — первым
        if not off:
            cat_out = self._resolve_from_catalog(q, object_type, lim)
            if cat_out is not None:
                return cat_out

        ql = q.lower().replace("ё", "е")
        classes = self._classes_for_resolve(q, object_type)

        search_q = q
        skip_domain_expand = False
        try:
            from tools.attr_query import (
                is_attr_filter_question,
                split_object_and_filter,
            )

            if is_attr_filter_question(q):
                split = split_object_and_filter(q)
                search_q = split.get("object_search") or q
                skip_domain_expand = True
                ql = search_q.lower().replace("ё", "е")
        except Exception:
            pass

        # запросы: фраза + стемы + синонимы из knowledge (не из кода)
        queries = expand_search_queries(search_q, max_extra=12)
        for extra in self._vocab_extra_queries(search_q):
            if extra not in queries:
                queries.append(extra)
            for e2 in expand_search_queries(extra, max_extra=3):
                if e2 not in queries:
                    queries.append(e2)
        # поисковые стемы — только если это не фильтр по реквизиту шапки
        ql0 = ql
        _domain_q: list[str] = []
        if not skip_domain_expand:
            try:
                from tools.business_stems import (
                    CASH_SEARCH,
                    PURCHASE_SEARCH,
                    SALES_SEARCH,
                )

                if any(x in ql0 for x in ("поступлен", "закупк", "приобрет", "поставщик")):
                    _domain_q.append(PURCHASE_SEARCH)
                if any(x in ql0 for x in ("продаж", "реализ", "отгруз", "выручк")):
                    _domain_q.append(SALES_SEARCH)
                if any(x in ql0 for x in ("оплат", "платеж", "деньг")):
                    _domain_q.append(CASH_SEARCH)
            except Exception:
                pass
            if "заказ" in ql0:
                _domain_q.append("заказ клиент покупател")
            if "спецификац" in ql0:
                _domain_q.append("спецификация номенклатур")
            if any(x in ql0 for x in ("перевозк", "доставк", "транспорт")):
                _domain_q.append("перевозка доставка")
            if any(x in ql0 for x in ("артикул", "тарикул")):
                _domain_q.append("артикул номенклатура")
            if any(x in ql0 for x in ("счет на", "счёт на", "счет оплат", "счёт оплат")):
                _domain_q.append("счет на оплату")
        for dq in _domain_q:
            if dq and dq not in queries:
                queries.insert(0, dq)

        searched_in: list[str] = []
        merged: dict[str, dict[str, Any]] = {}

        def _search_round(class_list: list[str], qlist: list[str]) -> None:
            for cls in class_list:
                if cls not in searched_in:
                    searched_in.append(cls)
                for qone in qlist:
                    meta = self.find_metadata(qone, cls, max(lim * 2, 16))
                    self._merge_meta_hits(merged, meta.get("items"))

        # 1) выбранные / все основные классы
        _search_round(classes, queries[:14])

        # 2) если сужали класс и пусто — расширить на все основные
        if not merged:
            more = [c for c in self._META_CLASSES_PRIMARY if c not in searched_in]
            if more:
                _search_round(more, queries[:10])

        # 3) бэкенд «Все»
        if not merged:
            for qone in queries[:8]:
                meta = self.find_metadata(qone, "Все", max(lim * 2, 16))
                self._merge_meta_hits(merged, meta.get("items"))
            if "Все" not in searched_in:
                searched_in.append("Все")

        # 4) локальный стем по спискам имён (любые классы, что уже смотрели + основные)
        if not merged:
            for cls in list(dict.fromkeys([*searched_in, *self._META_CLASSES_PRIMARY])):
                if cls in ("Все", "все"):
                    continue
                local = self._local_meta_search(q, cls, lim)
                self._merge_meta_hits(merged, local)
                for tok in queries[1:6]:
                    self._merge_meta_hits(
                        merged, self._local_meta_search(tok, cls, lim)
                    )

        if not merged:
            return need_clarification(
                f"По «{q}» в метаданных ничего не нашлось "
                f"(классы: {', '.join(searched_in)}). "
                "Если вопрос про код/форму/отчёт — inspect_code(request=фраза). "
                "Иначе уточните класс (справочник/документ/регистр/отчёт) "
                "или название в вашей 1С.",
                about="object",
                missing=["object"],
                status="not_found",
                request=q,
                searched_in=searched_in,
                searches_used=queries[:10],
                prefer_tool="inspect_code",
                next_steps=[
                    "inspect_code(request=исходная фраза) — если про код/источник",
                    "Спросить уточнение у пользователя",
                    "Снова resolve_object / build_report",
                ],
                action_for_agent=(
                    "Не пиши «в базе нет». Попробуй inspect_code(request=фраза) "
                    "или спроси класс/другое название. full_name не выдумывать."
                ),
            )

        ranked = self._rank_objects_for_phrase(list(merged.values()), ql)
        # data-path: при долге/остатках вынести РегистрНакопления вперёд до среза lim
        try:
            from tools.linked_query import is_linked_or_debt_data_question
            from tools.linked_query import is_debt_unpaid_question

            if is_linked_or_debt_data_question(q) or is_debt_unpaid_question(q):
                def _debt_key(it: dict) -> tuple:
                    fn = str(it.get("full_name") or "")
                    sc = float(it.get("_phrase_score") or it.get("score") or 0)
                    if fn.startswith("РегистрНакопления."):
                        low = fn.lower()
                        money = 0 if any(
                            x in low
                            for x in ("расчет", "взаиморасчет", "оплат", "долг")
                        ) else 1
                        return (0, money, -sc)
                    if fn.startswith("РегистрСведений."):
                        return (1, 0, -sc)
                    if fn.startswith("Отчет.") or fn.startswith("Отчёт."):
                        return (3, 0, -sc)
                    return (2, 0, -sc)

                ranked = sorted(ranked, key=_debt_key)
        except Exception:
            pass

        # порядок — только token affinity (rank_items_by_phrase), без доменного
        # «заказ → Документ.*Заказ* / реализац вниз»
        items_all = []
        for it in ranked:
            fn = str(it.get("full_name") or "")
            items_all.append(
                {
                    "full_name": fn,
                    "name": it.get("name") or (fn.split(".")[-1] if fn else ""),
                    "type": it.get("type") or "",
                    "synonym": it.get("synonym") or "",
                    "score": it.get("_phrase_score") or it.get("score"),
                }
            )
        items_out, page_meta = slice_page(
            items_all,
            limit=lim,
            offset=off,
            default_limit=15,
            max_limit=80,
        )
        if not items_out and items_all:
            # offset за пределами — вернуть meta
            return attach_paging(
                {
                    "ok": True,
                    "status": "page_empty",
                    "request": q,
                    "items": [],
                    "count": 0,
                    "need_clarification": True,
                    "question": (
                        f"Порция offset={off} пуста (всего {len(items_all)}). "
                        f"Возьми offset=0..{max(0, len(items_all)-lim)}."
                    ),
                },
                page_meta,
            )

        best = items_out[0] if items_out else items_all[0]
        best_fn = str(best.get("full_name") or "")
        try:
            best_sc = float(best.get("score") or 0)
            # gap по полному ranked, не только странице
            second_sc = (
                float(items_all[1].get("score") or 0) if len(items_all) > 1 else 0.0
            )
            if off == 0 and len(items_out) > 1:
                second_sc = float(items_out[1].get("score") or 0)
        except (TypeError, ValueError):
            best_sc, second_sc = 0.0, 0.0

        # близкие score → спросить (не угадывать); большой отрыв → confident
        score_gap = best_sc - second_sc if second_sc > 0 else best_sc
        ambiguous = len(items_all) > 1 and off == 0 and (
            best_sc < 40
            or (second_sc > 0 and best_sc < second_sc * 1.35)
            or abs(best_sc - second_sc) < 15
            or (second_sc > 0 and score_gap < 20 and best_sc < 120)
        )
        # если top явно регистр накопления при «долг/остатк» и gap >= 25 — не ambiguous
        try:
            from tools.linked_query import is_linked_or_debt_data_question
            from tools.linked_query import is_debt_unpaid_question

            debtish = is_linked_or_debt_data_question(q) or is_debt_unpaid_question(q)
        except Exception:
            debtish = False
        if (
            debtish
            and best_fn.startswith("РегистрНакопления.")
            and score_gap >= 25
            and best_sc >= 50
        ):
            ambiguous = False
        types_top = {str(i.get("type") or "") for i in items_out[:5]}
        if len(types_top) > 1 and len(items_out) > 1 and not (
            debtish and best_fn.startswith("РегистрНакопления.") and score_gap >= 30
        ):
            ambiguous = True

        by_class: dict[str, list[str]] = {}
        for i in items_out:
            t = str(i.get("type") or "?")
            by_class.setdefault(t, []).append(str(i.get("full_name")))

        base: dict[str, Any] = {
            "ok": True,
            "status": "found_many" if len(items_all) > 1 else "found_one",
            "request": q,
            "count": len(items_out),
            "items": items_out,
            "top": best_fn if off == 0 else (items_all[0].get("full_name") if items_all else best_fn),
            "by_class": by_class,
            "searched_in": searched_in,
            "searches_used": queries[:10],
        }
        base = attach_paging(base, page_meta)

        # если исходный вопрос про код — после нахождения имени → inspect
        from tools.route_intent import ROUTE_CODE, is_code_config_question

        code_q = is_code_config_question(q)

        if ambiguous:
            labels = []
            for i in items_out[:8]:
                t = i.get("type") or ""
                labels.append(f"{i['full_name']}" + (f" ({t})" if t else ""))
            next_after = (
                "inspect_code(request=фраза)"
                if code_q
                else "build_report(request=…, full_name=<выбор>)"
            )
            return {
                **base,
                "need_clarification": True,
                "confident": False,
                "question": (
                    f"По «{q}» несколько объектов конфигурации. Какой нужен?\n"
                    + "\n".join(f"• {lb}" for lb in labels)
                ),
                "options": [i["full_name"] for i in items_out[:8]],
                "options_labeled": labels,
                "action_for_agent": (
                    "ОСТАНОВИСЬ. Покажи options (с классом). Не выбирай наугад. "
                    f"После выбора: {next_after}."
                ),
                "score_gap": score_gap,
                "suggested": best_fn,
                "next": ["Спросить пользователя", next_after],
            }

        if code_q:
            return {
                **base,
                "status": "found_one",
                "confident": True,
                "need_clarification": False,
                "prefer_tool": "inspect_code",
                "hint": (
                    f"full_name={best_fn}. Вопрос про код/источник → "
                    "inspect_code(request=исходная фраза)."
                ),
                "action_for_agent": (
                    f"inspect_code(request={q!r}) — не build_report."
                ),
                "next": [f"inspect_code(request='{q}')"],
            }

        return {
            **base,
            "status": "found_one",
            "confident": True,
            "need_clarification": False,
            "hint": (
                f"full_name={best_fn} из метаданных этой базы. "
                "Дальше: build_report или get_object_structure."
            ),
            "action_for_agent": (
                f"Продолжай с full_name='{best_fn}': "
                "build_report(request=фраза, full_name=…) "
                "или structure → execute_query. Имя не подменять."
            ),
            "next": [
                f"build_report(request='{q}', full_name='{best_fn}')",
                f"get_object_structure('{best_fn}')",
            ],
        }

    def quick_find(
        self,
        query: str = "",
        object_type: str = "Все",
        limit: int = 8,
        with_fields: bool = False,
        request: str = "",
        search_query: str = "",
    ) -> dict[str, Any]:
        """
        Быстрый поиск: фраза → top full_name (+ опционально roles у лидера).
        Внутри — resolve_object (все классы, уточнение при неоднозначности).
        """
        q = (query or request or search_query or "").strip()
        if not q:
            return {
                "error": "Нужен query/request",
                "hint": "Используйте resolve_object(request=…)",
            }
        resolved = self.resolve_object(
            request=q, object_type=object_type, limit=limit
        )
        if resolved.get("need_clarification") or resolved.get("error"):
            return resolved

        items_out = list(resolved.get("items") or [])
        best_fn = str(resolved.get("top") or (items_out[0].get("full_name") if items_out else "") or "")
        out: dict[str, Any] = {
            "ok": True,
            "query": (query or "").strip(),
            "count": len(items_out),
            "items": items_out,
            "top": best_fn,
            "confident": bool(resolved.get("confident")),
            "searches_used": resolved.get("searches_used") or [],
            "searched_in": resolved.get("searched_in") or [],
            "next": resolved.get("next")
            or [
                f"get_object_structure('{best_fn}')",
                f"build_report(request=…, full_name='{best_fn}')",
            ],
            "hint": resolved.get("hint")
            or (f"Рекомендуется: {best_fn}." if best_fn else ""),
        }
        if with_fields and best_fn:
            roles = self.resolve_business_fields(best_fn)
            if not roles.get("error"):
                out["roles"] = roles.get("roles") or roles.get("имена_для_запроса")
                out["fields_note"] = (
                    f"roles для {best_fn} — подставляй в ВЫБРАТЬ/BSL."
                )
        return out

    def list_metadata_types(self) -> dict[str, Any]:
        # полный каталог как в конфигураторе (не урезанный HTTP)
        names = list(METADATA_TYPES.keys())
        return {
            "types": names,
            "note": (
                "Все классы из METADATA_TYPES. list_objects(type) — HTTP или dump. "
                "Роли/права: list_roles, access_overview. "
                "Активные пользователи: list_active_sessions. "
                "Журнал регистрации: analyze_event_log."
            ),
            "prefer": (
                "list_objects(metadata_type='Подсистемы'|'Роли'|'Отчеты'|…). "
                "Не говори «тип не поддерживается», если он в types."
            ),
        }

    def list_document_types(
        self, limit: int = 80, search: str = ""
    ) -> dict[str, Any]:
        """Документы ЭТОЙ конфигурации (в т.ч. доработанные)."""
        return self._list_type_names("Документы", limit, search=search)

    def list_catalog_types(
        self, limit: int = 80, search: str = ""
    ) -> dict[str, Any]:
        """Справочники ЭТОЙ конфигурации."""
        return self._list_type_names("Справочники", limit, search=search)

    def list_register_types(
        self, kind: str = "Все", limit: int = 80, search: str = ""
    ) -> dict[str, Any]:
        """
        Регистры ЭТОЙ базы. kind: Все | Сведений | Накопления
        (или полные РегистрыСведений / РегистрыНакопления).
        """
        k = (kind or "Все").strip().lower().replace("ё", "е")
        types: list[str]
        if k in ("все", "all", ""):
            types = ["РегистрыСведений", "РегистрыНакопления"]
        elif "накопл" in k:
            types = ["РегистрыНакопления"]
        elif "сведен" in k or "info" in k:
            types = ["РегистрыСведений"]
        elif kind in METADATA_TYPES:
            types = [kind]
        else:
            types = ["РегистрыСведений", "РегистрыНакопления"]

        all_names: list[str] = []
        for mt in types:
            part = self._list_type_names(mt, limit=2000, search=search)
            if part.get("error"):
                continue
            all_names.extend(part.get("names") or [])

        lim = clamp_limit(limit, 40, 300)
        # уже отсортированы по score внутри _list_type_names при search
        if not search:
            all_names.sort(key=lambda n: (n.split(".")[-1].lower() if "." in n else n.lower()))
        shown = all_names[:lim]
        return {
            "type": "Регистры",
            "kinds": types,
            "names": shown,
            "count": len(all_names),
            "shown": len(shown),
            **({"truncated": True, "not_all_shown": f"Показано {len(shown)} из {len(all_names)} — уточни search, чтобы увидеть остальные."}
               if len(all_names) > len(shown) else {}),
            "search": search or None,
            "note": (
                "full_name регистра → get_object_structure → "
                "query_syntax_help('virtual') → execute_query. "
                "Имена только из этой базы."
            ),
            "hint": (
                f"Рекомендуется: {shown[0]}" if shown else "Уточни search или list_metadata_types"
            ),
        }

    def list_objects_filtered(
        self,
        metadata_type: str = "",
        search: str = "",
        limit: int = 25,
        offset: int = 0,
        page: int = 0,
    ) -> dict[str, Any]:
        """list_objects + search; limit/offset/page — модель выбирает порцию."""
        if not (metadata_type or "").strip():
            return self._class_counts_overview(search)
        return self._list_type_names(
            metadata_type,
            limit,
            search=search,
            offset=offset,
            page=page or None,
        )

    def _class_counts_overview(self, search: str = "") -> dict[str, Any]:
        """metadata_type не задан: сколько объектов каждого класса (по каталогу выгрузки)."""
        counts: dict[str, int] = {}
        try:
            from tools.object_catalog import load_catalog

            for r in load_catalog():
                c = str(r.get("class") or "")
                if c:
                    counts[c] = counts.get(c, 0) + 1
        except Exception:
            pass
        if not counts:
            return need_clarification(
                "Объекты какого класса показать? (документы, справочники, регистры, отчёты…)",
                about="metadata_type",
                missing=["metadata_type"],
            )
        return {
            "ok": True,
            "class_counts": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
            "total": sum(counts.values()),
            "hint": "Для перечня имён вызови list_objects(metadata_type=<класс во мн. числе>, например Документы).",
        }

    def _list_type_names(
        self,
        metadata_type: str,
        limit: int = 25,
        search: str = "",
        *,
        offset: int = 0,
        page: int | None = None,
        cross_search: bool = True,
    ) -> dict[str, Any]:
        from tools.intent_search import name_matches_search, score_name_for_search
        from tools.paging import attach_paging, slice_page

        # полный список с бэкенда (не обрезать до limit до фильтра search)
        raw = self.list_objects(metadata_type, max_names=5000)
        if raw.get("error"):
            return raw
        names = [
            n
            for n in (raw.get("names") or [])
            if n and not self._is_noise_meta(n)
        ]
        q = (search or "").strip().lower().replace("ё", "е")
        if q:
            # стемминг + multi-word AND (универсально для любой конфигурации)
            scored = [
                (score_name_for_search(n, q), n)
                for n in names
                if name_matches_search(n, q)
            ]
            scored.sort(key=lambda t: (-t[0], len(t[1]), t[1].lower()))
            names = [n for sc, n in scored if sc >= 0]
        else:
            # алфавит по имени объекта (после точки), не по длине!
            names.sort(key=lambda n: (n.split(".")[-1].lower() if "." in n else n.lower()))

        shown, page_meta = slice_page(
            names,
            limit=limit,
            offset=offset,
            page=page if page else None,
            default_limit=25,
            max_limit=200,
        )
        out: dict[str, Any] = {
            "type": metadata_type,
            "names": shown,
            "count": len(names),
            "shown": len(shown),
            "note": (
                "Имена ТАБЛИЦ. limit/offset/page — сколько и какая порция (модель сама). "
                "has_more → следующая порция offset=next_offset. "
                "Данные: get_object_structure → execute_query / export_*."
            ),
        }
        out = attach_paging(out, page_meta, tool="list_objects", args={"metadata_type": metadata_type, "search": search})
        if q:
            out["search"] = search
            if not shown:
                alts: list[str] = []
                if cross_search:
                    other_types = [
                        t
                        for t in (
                            "Справочники",
                            "Документы",
                            "РегистрыСведений",
                            "РегистрыНакопления",
                        )
                        if t != metadata_type
                    ]
                    for ot in other_types:
                        # без cross_search — иначе рекурсия
                        part = self._list_type_names(
                            ot, limit=8, search=search, cross_search=False
                        )
                        for n in part.get("names") or []:
                            if n and n not in alts:
                                alts.append(n)
                        if len(alts) >= 12:
                            break
                out["found_in_class"] = 0
                out["alternatives"] = alts[:12]
                out["need_clarification"] = not bool(alts)
                if alts:
                    out["hint"] = (
                        f"В «{metadata_type}» пусто, но есть похожее в других классах. "
                        f"Не пиши «ничего нет». Варианты: {alts[:6]}. "
                        "Спроси пользователя или resolve_object(request=фраза)."
                    )
                    out["action_for_agent"] = (
                        "Покажи alternatives пользователю ИЛИ вызови "
                        "resolve_object(request=исходная фраза) / "
                        f"build_report. Top alternative: {alts[0]}"
                    )
                    out["next"] = [
                        f"resolve_object(request='{search}')",
                        f"build_report(request='{search}', full_name='{alts[0]}')",
                    ]
                else:
                    out["hint"] = (
                        "Нигде не найдено. resolve_object(request=фраза) или "
                        "спросить пользователя (справочник/документ, другое имя)."
                    )
                    out["action_for_agent"] = (
                        "Не утверждай что в базе нет объектов. "
                        "resolve_object или спроси уточнение."
                    )
                    out["next"] = [f"resolve_object(request='{search}')"]
            else:
                out["hint"] = (
                    f"Бери full_name из names (1-й: {shown[0]}). "
                    "Имена зависят от конфигурации. Дальше: get_object_structure → "
                    "query_examples / build_report / execute_query."
                )
        return out

    def list_objects(
        self, metadata_type: str, max_names: int | None = None
    ) -> dict[str, Any]:
        canon = normalize_metadata_type(metadata_type)
        if not canon:
            return err(
                f"Неизвестный metadata_type: {metadata_type}",
                hint=", ".join(METADATA_TYPES.keys()),
            )
        metadata_type = canon

        # кэш полного списка имён класса (сессия MCP)
        cached = self._meta_list_cache.get(metadata_type)
        if cached is not None:
            names = list(cached)
            total = len(names)
            cap = max_names if max_names is not None else self.list_objects_cap
            out: dict[str, Any] = {
                "type": metadata_type,
                "names": names[:cap],
                "count": total,
                "cached": True,
            }
            if total > cap:
                out["shown"] = cap
                out["truncated"] = True
                out["not_all_shown"] = (
                    f"Показано {cap} из {total} (только для экономии контекста). Остальное: "
                    f"list_objects(metadata_type='{metadata_type}', offset={cap}) или уточни search. "
                    "Нужного нет среди показанного — это не значит, что его нет в базе."
                )
            return out

        result = self.backend.list_objects(metadata_type)
        names: list[str] = []
        source = "http"

        if isinstance(result, dict) and not result.get("error"):
            objs = result.get("objects") or []
            for o in objs:
                if isinstance(o, dict):
                    names.append(str(o.get("full_name") or o.get("name") or ""))
                else:
                    prefix = METADATA_TYPES[metadata_type]["full_name_prefix"]
                    s = str(o)
                    names.append(s if "." in s else f"{prefix}.{s}")
            names = [n for n in names if n]

        # fallback: выгрузка конфигурации (Подсистемы, Роли, …)
        if not names:
            from tools.meta_dump import list_from_dump

            dump = list_from_dump(
                metadata_type,
                limit=max_names or 2000,
                code_dir=self._code_dir() or None,
            )
            if dump.get("objects"):
                names = [str(o.get("full_name") or o.get("name") or "") for o in dump["objects"]]
                names = [n for n in names if n]
                source = "config_dump"
            elif isinstance(result, dict) and result.get("error"):
                return err(
                    str(result.get("error")),
                    hint=dump.get("hint")
                    or ", ".join(METADATA_TYPES.keys()),
                )

        self._meta_list_cache[metadata_type] = names
        total = len(names)
        cap = max_names if max_names is not None else self.list_objects_cap
        out = {
            "type": metadata_type,
            "names": names[:cap],
            "count": total,
            "source": source,
        }
        if total > cap:
            out["shown"] = cap
            out["truncated"] = True
            out["not_all_shown"] = (
                f"Показано {cap} из {total} (только для экономии контекста). Остальное: "
                f"list_objects(metadata_type='{metadata_type}', offset={cap}) или уточни search. "
                "Нужного нет среди показанного — это не значит, что его нет в базе."
            )
        if metadata_type == "Подсистемы":
            out["prefer"] = (
                "full_name: Подсистема.Имя или Подсистема.Родитель.Дочерняя. "
                "Вложенность из dump Subsystems/*/Subsystems/."
            )
        return out

    def find_metadata(
        self,
        search_query: str = "",
        object_type: str = "Все",
        limit: int = 25,
        offset: int = 0,
        page: int = 0,
    ) -> dict[str, Any]:
        """
        Поиск объектов конфигурации.
        limit/offset/page — порция на выбор модели (если нет в 1-й — offset=limit).
        Пустой search_query запрещён.
        """
        import time as _time

        from tools.paging import (
            attach_paging,
            clamp_limit,
            fetch_limit_for_offset,
            resolve_offset,
            slice_page,
        )

        lim = clamp_limit(limit, default=25, maximum=100)
        off = resolve_offset(
            offset=offset,
            page=page if page else None,
            limit=lim,
        )
        # session cache (только первая страница / без offset)
        q_key = f"{(search_query or '').strip().lower()}|{object_type}|{lim}|{off}|{page}"
        if off == 0 and not page:
            hit = self._meta_search_cache.get(q_key)
            if hit and (_time.time() - hit[0]) < self._cache_ttl_sec:
                cached = dict(hit[1])
                cached["from_cache"] = True
                return cached
        # набрать с запасом для ранжирования, потом page-slice
        pool = fetch_limit_for_offset(lim, off, max_fetch=300)
        q, otype = self._normalize_meta_args(search_query, object_type)

        if not q:
            return err(
                "search_query обязателен",
                hint=(
                    "Пример: search_query='номенклатура', object_type='Справочники', "
                    "limit=20, offset=0. Нет в порции → offset=20."
                ),
            )

        # HTTP 1С часто ищет фразу целиком → 0; чистим шум и пробуем токены
        q_clean = self._meta_search_clean(q)
        raw = self.backend.find_metadata(q_clean or q, otype, max(pool * 2, 40))
        if not isinstance(raw, dict) or raw.get("error"):
            return err(str((raw or {}).get("error") if isinstance(raw, dict) else raw))

        ranked_all = self._filter_and_rank_meta(
            raw.get("results") or [], q_clean or q, pool
        )

        # мульти-слово: отдельные токены (без привязки к именам конфигурации)
        if len(ranked_all) < max(2, lim):
            tokens = re.findall(r"[a-zA-Zа-яА-ЯёЁ]{4,}", (q_clean or q).lower())
            merged: list[dict[str, Any]] = list(ranked_all)
            seen = {str(i.get("full_name")) for i in merged}
            for tok in tokens:
                if tok in ("откуда", "берет", "данные", "отчет", "отчёт", "модуль"):
                    continue
                raw_t = self.backend.find_metadata(tok, otype, max(pool, 20))
                if not isinstance(raw_t, dict) or raw_t.get("error"):
                    continue
                part = self._filter_and_rank_meta(
                    raw_t.get("results") or [], tok, pool
                )
                for it in part:
                    fn = str(it.get("full_name") or "")
                    if fn and fn not in seen:
                        seen.add(fn)
                        merged.append(it)
            if merged:
                ranked_all = self._filter_and_rank_meta(
                    merged, q_clean or q, max(pool, len(merged))
                )

        # 1С search часто не находит — локальный стем-поиск
        if len(ranked_all) < max(2, lim):
            local = self._local_meta_search(q_clean or q, otype, max(pool, 50))
            if local:
                seen = {str(i.get("full_name")) for i in local}
                ranked_all = local + [
                    i for i in ranked_all if str(i.get("full_name")) not in seen
                ]

        items, page_meta = slice_page(
            ranked_all,
            limit=lim,
            offset=off,
            default_limit=25,
            max_limit=100,
        )

        from tools.route_intent import is_code_config_question

        wants_code = is_code_config_question(search_query or q or "")

        out: dict[str, Any] = {
            "items": items,
            "count": page_meta.get("total", len(items)),
            "note": (
                "Объекты конфигурации. limit — сколько за раз (ты выбираешь). "
                "Нет нужного и has_more → find_metadata(..., offset=next_offset). "
                "full_name из items. Код/«откуда данные» → inspect_code."
            ),
        }
        out = attach_paging(out, page_meta, tool="find_metadata", args={"search_query": search_query})
        if not items:
            out["need_clarification"] = True
            out["question"] = (
                f"По «{q}» в метаданных пусто. "
                "Если это отчёт/форма — вызови inspect_code(request=исходная фраза) "
                "по выгрузке BSL. Иначе уточни название."
            )
            out["action_for_agent"] = (
                "НЕ сдавайся: inspect_code(request=фраза пользователя) "
                "или find_modules. Не выдумывай full_name."
            )
            out["prefer_tool"] = "inspect_code"
        elif wants_code:
            out["hint"] = (
                f"full_name={items[0].get('full_name')}. "
                "Вопрос про источник данных/код → inspect_code(request=фраза), "
                "не execute_query."
            )
            out["prefer_tool"] = "inspect_code"
            out["action_for_agent"] = (
                "Сразу inspect_code(request=исходная фраза пользователя)."
            )
        elif len(items) > 1:
            out["hint"] = (
                f"Несколько вариантов. Если не уверен — спроси пользователя. "
                f"Top: {items[0].get('full_name')}. "
                f"Список: {[i.get('full_name') for i in items[:6]]}"
            )
            # близкие score → явно уточнить
            try:
                s0 = float(items[0].get("score") or 0)
                s1 = float(items[1].get("score") or 0)
            except (TypeError, ValueError):
                s0, s1 = 0, 0
            if s0 and s1 and abs(s0 - s1) < 50:
                out["need_clarification"] = True
                out["question"] = "Найдено несколько похожих объектов. Какой использовать?"
                out["options"] = [i.get("full_name") for i in items[:8]]
                out["action_for_agent"] = (
                    "ОСТАНОВИСЬ и спроси пользователя, покажи options."
                )
        elif not wants_code:
            out["hint"] = (
                f"full_name={items[0].get('full_name')}. "
                "Дальше: structure → compare_periods / execute_query."
            )
        if q != (search_query or "").strip() or otype != (object_type or "Все"):
            out["normalized"] = {"search_query": q, "object_type": otype}
        if q_clean and q_clean != q:
            out.setdefault("normalized", {})
            if isinstance(out["normalized"], dict):
                out["normalized"]["search_clean"] = q_clean
        if off == 0 and not page and not out.get("error"):
            try:
                import time as _time

                self._meta_search_cache[q_key] = (_time.time(), dict(out))
                # cap cache size
                if len(self._meta_search_cache) > 200:
                    oldest = sorted(
                        self._meta_search_cache.items(), key=lambda x: x[1][0]
                    )[:50]
                    for k, _ in oldest:
                        self._meta_search_cache.pop(k, None)
            except Exception:
                pass
        return out

    @staticmethod
    def _meta_search_clean(q: str) -> str:
        """Убрать шум из поисковой фразы (универсально, любая конфигурация)."""
        from tools.route_intent import clean_object_search

        return clean_object_search(q)

    def _local_meta_search(
        self, search_query: str, object_type: str, limit: int
    ) -> list[dict[str, str]]:
        """Стем/мульти-слово поиск по list_objects, когда HTTP find пустой."""
        from tools.intent_search import score_name_for_search

        from tools.route_intent import ALL_META_CLASSES

        types: list[str]
        if object_type in ("Все", "все", ""):
            types = list(ALL_META_CLASSES)
        else:
            types = [object_type]

        q = self._meta_search_clean(search_query) or search_query
        scored: list[tuple[int, dict[str, str]]] = []
        for mt in types:
            raw = self.list_objects(mt, max_names=3000)
            for n in raw.get("names") or []:
                if not n or self._is_noise_meta(n):
                    continue
                sc = score_name_for_search(str(n), q)
                if sc < 0:
                    # попробуем по отдельным токенам (AND soft)
                    toks = re.findall(r"[a-zA-Zа-яА-ЯёЁ]{4,}", q.lower())
                    if len(toks) >= 2:
                        blob = str(n).lower().replace("ё", "е")
                        if all(
                            t[:4] in blob or t in blob
                            for t in toks
                        ):
                            sc = 20 + 10 * len(toks)
                        else:
                            continue
                    else:
                        continue
                name = str(n).split(".")[-1] if "." in str(n) else str(n)
                # лёгкий буст класса, если в запросе явно назван класс
                ql = q.lower().replace("ё", "е")
                if mt == "Отчеты" and re.search(r"\bотч[её]т", ql):
                    sc += 40
                if mt == "Обработки" and "обработк" in ql:
                    sc += 40
                if mt == "ОбщиеФормы" and re.search(r"общ\w*\s*форм", ql):
                    sc += 40
                scored.append(
                    (
                        sc,
                        {
                            "full_name": str(n),
                            "name": name,
                            "type": mt,
                            "score": str(sc),
                        },
                    )
                )
        scored.sort(key=lambda t: (-t[0], len(t[1].get("full_name") or "")))
        return [row for _, row in scored[:limit]]

    def suggest_objects(
        self,
        query: str,
        object_type: str = "Все",
        limit: int = 15,
    ) -> dict[str, Any]:
        lim = clamp_limit(limit, 10, 20)
        q, otype = self._normalize_meta_args(query, object_type)
        # сузить класс только по явным словам (не путать «отчет» с документами)
        ql = (q or query or "").lower()
        if otype in ("Все", "все", ""):
            if any(x in ql for x in ("отчет", "отчёт")) and not any(
                x in ql for x in ("документ", "реализац", "продаж")
            ):
                otype = "Отчеты"
            elif any(
                x in ql
                for x in (
                    "реализац",
                    "продаж",
                    "отгруз",
                    "заказ",
                    "выгруз",
                    "счет",
                    "счёт",
                    "покупател",
                )
            ):
                otype = "Документы"

        def _find(sq: str, ot: str, lim_inner: int) -> dict[str, Any]:
            return self.backend.find_metadata(sq, ot or "Все", lim_inner)

        raw = intent_suggest_objects(q or query or "", _find, object_type=otype, limit=lim * 2)
        items = self._filter_and_rank_meta(
            [
                {
                    "full_name": c.get("full_name"),
                    "name": c.get("name"),
                    "synonym": c.get("synonym"),
                    "type": c.get("type"),
                    "_score": c.get("score", 0),
                }
                for c in (raw.get("candidates") or [])
                if isinstance(c, dict)
            ],
            q or query or "",
            lim,
        )
        return {
            "items": items,
            "count": len(items),
            "note": "full_name → get_object_structure → execute_query",
        }

    def _normalize_meta_args(
        self, search_query: str, object_type: str
    ) -> tuple[str, str]:
        """object_type только классы; иначе переносим в search_query."""
        q = (search_query or "").strip()
        otype = (object_type or "Все").strip()
        allowed = set(METADATA_TYPES.keys()) | {"Все", "все", "ALL", "all"}

        if otype and otype not in allowed and otype not in METADATA_TYPES:
            # Модель передала «Номенклатура» как object_type
            if not q:
                q = otype
            elif otype.lower() not in q.lower():
                q = f"{q} {otype}".strip()
            # эвристика класса
            low = otype.lower()
            if any(x in low for x in ("документ", "реализац", "заказ", "поступлен", "счет")):
                otype = "Документы"
            elif any(x in low for x in ("регистр", "накоплен", "сведен")):
                otype = "РегистрыСведений" if "свед" in low else "РегистрыНакопления"
            else:
                otype = "Справочники"
        if otype in ("все", "ALL", "all"):
            otype = "Все"
        return q, otype

    @staticmethod
    def _is_noise_meta(full_name: str, name: str = "", synonym: str = "") -> bool:
        blob = f"{full_name} {name} {synonym}".lower()
        noise = (
            "присоединенн",
            "attached",
            "удалить",
            "delete",
            "fulltext",
            "настройк",
            "setting",
            "служебн",
            "history",
            "верси",
            "шаблон",
        )
        return any(n in blob for n in noise)

    def _filter_and_rank_meta(
        self, results: list[Any], search_query: str, limit: int
    ) -> list[dict[str, str]]:
        from tools.intent_search import rank_metadata_hit

        q = (search_query or "").lower().replace("ё", "е")
        keys = [q] if q else []
        # разбить на слова
        if q:
            keys.extend(w for w in re.findall(r"[a-zA-Zа-яА-ЯёЁ]{4,}", q) if w not in keys)

        prefer_docs = any(
            x in q
            for x in (
                "реализац",
                "продаж",
                "документ",
                "заказ",
                "поступлен",
                "счет",
                "счёт",
                "покупател",
            )
        )
        scored: list[tuple[int, dict[str, Any]]] = []

        for x in results:
            if not isinstance(x, dict):
                continue
            full = str(x.get("full_name") or "")
            name = str(x.get("name") or "")
            synonym = str(x.get("synonym") or "")
            if self._is_noise_meta(full, name, synonym):
                continue
            score = int(x.get("_score") or x.get("score") or 0)
            score += rank_metadata_hit(x, keys, prefer_documents=prefer_docs)
            # точные бизнес-имена
            name_l = name.lower().replace("ё", "е")
            full_l = full.lower().replace("ё", "е")
            if "номенклатур" in q and name_l == "номенклатура":
                score += 300
            if "контрагент" in q and name_l in ("контрагенты", "контрагент"):
                score += 300
            if "реализац" in q and full.startswith("Документ.") and "реализац" in name_l:
                if "товар" in name_l or "услуг" in name_l:
                    score += 400
            # универсальные бусты по стемам (без фиксированных имён Документ.X)
            if any(x in q for x in ("счет", "счёт")) and full.startswith("Документ."):
                if "счет" in name_l and "оплат" in name_l:
                    score += 300
                    if "покупател" in q and "покупател" in name_l:
                        score += 150
                if "счетфактур" in name_l:
                    score -= 100
                if name_l.startswith("оплата") or ("платежн" in name_l and "счет" not in name_l):
                    score -= 200
            if "покупател" in q and "покупател" in full_l and full.startswith("Документ."):
                score += 80
            scored.append((score, x))

        scored.sort(key=lambda t: (-t[0], len(str(t[1].get("name") or ""))))
        out: list[dict[str, str]] = []
        for sc, x in scored[:limit]:
            row = slim_meta_item(x)
            if sc:
                row["score"] = str(sc)
            out.append(row)
        return out

    def resolve_full_name(
        self, raw: str, prefer: str = "Все"
    ) -> dict[str, Any]:
        """
        Приводит короткое/кривое имя к full_name ЭТОЙ базы.
        Bare name / опечатка → list_objects / find_metadata / structure (не память CF).
        """
        from tools.query_builder import (
            normalize_metadata_name,
            try_prefix_bare_name,
        )

        s = (raw or "").strip()
        if not s:
            return err("Пустое имя", hint="find_metadata / list_*")

        # 0) ТОЛЬКО точное совпадение хвоста имени (не «Платеж» ⊆ «ПлатежноеПоручение»)
        bare = s.split(".")[-1] if "." in s else s
        bare_l = bare.lower().replace("ё", "е")
        had_typed = "." in s and s.split(".")[0].lower().replace("ё", "е") in (
            "документ",
            "справочник",
            "регистрсведений",
            "регистрнакопления",
        )
        for mt in (
            "Документы",
            "Справочники",
            "РегистрыСведений",
            "РегистрыНакопления",
        ):
            if bare_l and len(bare_l) >= 3:
                listed_all = self.list_objects(mt, max_names=5000)
                for fn in listed_all.get("names") or []:
                    tail = str(fn).split(".")[-1]
                    if tail.lower().replace("ё", "е") == bare_l:
                        return {
                            "full_name": str(fn),
                            "resolved_from": s,
                            "matched_list": mt,
                            "exact": True,
                        }

        # 1) уже Тип.Имя — только если structure реально есть
        try:
            name = normalize_metadata_name(s)
            _n, _st, error = self._require_structure(name)
            if not error:
                return {"full_name": _n, "resolved_from": s, "exact": True}
        except ValueError:
            name = ""

        # 2) голое имя → префиксы (строгое structure)
        candidates = try_prefix_bare_name(s)
        for cand in candidates:
            try:
                n = normalize_metadata_name(cand)
            except ValueError:
                continue
            _n, _st, error = self._require_structure(n)
            if not error:
                return {"full_name": _n, "resolved_from": s, "tried": cand, "exact": True}

        # 3) find_metadata — для фраз; выдуманный Тип.Имя не подменяем молча
        otype = prefer if prefer else "Все"
        if otype.lower() in ("документ", "документы", "documents"):
            otype = "Документы"
        meta = self.find_metadata(s, otype, 12)
        items = meta.get("items") or []
        if not items and otype != "Все":
            meta = self.find_metadata(s, "Все", 12)
            items = meta.get("items") or []
        if items:
            if otype == "Документы" or prefer in ("Документы", "document"):
                items = sorted(
                    items,
                    key=lambda i: (
                        0
                        if str(i.get("full_name") or "").startswith("Документ.")
                        else 1
                    ),
                )
            cands = [str(i.get("full_name") or "") for i in items[:8] if i.get("full_name")]
            # точное совпадение хвоста в candidates
            for fn in cands:
                if fn.split(".")[-1].lower().replace("ё", "е") == bare_l:
                    return {
                        "full_name": fn,
                        "resolved_from": s,
                        "candidates": cands,
                        "exact": True,
                    }
            # выдуманный Тип.Имя — не угадывать первый fuzzy
            if had_typed:
                return err(
                    f"Объект «{s}» не найден в metadata. Не угадывай имена.",
                    hint=(
                        "find_metadata(search_query=слова пользователя, object_type='Документы') "
                        "→ возьми items[0].full_name. "
                        "Или compare_periods(search_query='…фраза…')."
                    ),
                    candidates=cands,
                )
            # фраза / короткое имя — берём top + candidates
            fn = cands[0] if cands else ""
            if fn:
                return {
                    "full_name": fn,
                    "resolved_from": s,
                    "candidates": cands,
                    "exact": False,
                    "warning": "full_name выбран по поиску; проверь candidates",
                }

        return err(
            f"Не найден объект: {s}",
            hint=(
                "find_metadata(search_query=…, object_type='Документы'|'Все') "
                "или compare_periods(search_query='счет покупателя')"
            ),
            tried=candidates[:4],
        )
