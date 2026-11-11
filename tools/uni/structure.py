# -*- coding: utf-8 -*-
"""Структура объектов: реквизиты, формы, describe, поиск полей, список по реквизитам."""

from __future__ import annotations

import re
from typing import Any

from tools.field_discovery import (
    parse_structure_fields,
    resolve_business_map,
    search_fields as discover_search_fields,
)
from tools.query_builder import normalize_metadata_name
from tools.responses import err, need_clarification, slim_field
from tools.uni.safety import clamp_limit


def _phrase_has_object_for_describe(phrase: str) -> bool:
    """Есть ли намёк на объект (тип метаданных / PascalCase), без доменных стемов."""
    pl = (phrase or "").lower().replace("ё", "е")
    if re.search(
        r"\b(документ|справочник|регистр|обработк|отчет|отчёт)\b",
        pl,
    ):
        return True
    try:
        from tools.phrase_struct import has_pascal_identifier

        return bool(has_pascal_identifier(phrase))
    except Exception:
        return False


class StructureMixin:
    def list_object_attributes(
        self,
        full_name: str = "",
        limit: int = 0,
        offset: int = 0,
        page: int = 0,
        include_tabular: bool = True,
    ) -> dict[str, Any]:
        """
        Полный список реквизитов (и опционально ТЧ) — для «выведи все реквизиты».
        limit=0 → все; иначе paging.
        """
        from tools.paging import attach_paging, clamp_limit, resolve_offset, slice_page

        st = self.get_object_structure(full_name or "")
        if st.get("error") and not st.get("attributes_table") and not st.get("fields"):
            return st
        rows = list(st.get("attributes_table") or [])
        if not rows:
            for f in st.get("fields") or []:
                if isinstance(f, dict) and f.get("name") and not f.get("tabular_section"):
                    rows.append(
                        {
                            "name": f.get("name"),
                            "synonym": f.get("synonym") or "",
                            "type": str(f.get("type") or f.get("тип") or ""),
                        }
                    )
        tabular_out: list[dict[str, Any]] = []
        if include_tabular:
            for ts in st.get("tabular_sections") or []:
                if not isinstance(ts, dict):
                    continue
                tname = ts.get("name")
                tattrs = []
                for a in ts.get("attributes") or []:
                    if isinstance(a, dict) and a.get("name"):
                        tattrs.append(
                            {
                                "name": a.get("name"),
                                "synonym": a.get("synonym") or "",
                                "type": str(a.get("type") or ""),
                            }
                        )
                tabular_out.append(
                    {"tabular_section": tname, "attributes": tattrs, "count": len(tattrs)}
                )
        total = len(rows)
        import json as _json

        from context_budget import tool_budget

        # реквизиты идут в ответ дважды (attributes и attributes_lines) — отсюда множитель; страница подбирается по размеру,
        # чтобы общий ужиматель её не резал и next_offset не пропускал строки
        fit = int(tool_budget("list_object_attributes", self.tool_result_max_chars) * 0.4)
        explicit = bool(limit and int(limit) > 0)
        too_big = len(_json.dumps(rows, ensure_ascii=False, default=str)) > fit
        if explicit or too_big or int(offset or 0) > 0 or page:
            page_rows, meta = slice_page(
                rows,
                limit=int(limit) if explicit else None,
                offset=offset,
                page=page if page else None,
                default_limit=50,
                max_limit=300,
                fit_chars=None if explicit else fit,
            )
        else:
            page_rows = rows
            meta = {
                "limit": total,
                "offset": 0,
                "page": 1,
                "returned": total,
                "total": total,
                "has_more": False,
                "next_offset": None,
                "next_page": None,
                "prefer_paging": "Показаны ВСЕ реквизиты шапки.",
            }
        if meta.get("offset"):
            tabular_out = []  # ТЧ отдаются на первой странице; на следующих — только реквизиты шапки
        lines = [
            f"{a.get('name')} | {a.get('synonym') or ''} | {a.get('type') or ''}"
            for a in page_rows
        ]
        out = {
            "ok": True,
            "full_name": st.get("full_name") or full_name,
            "synonym": st.get("synonym"),
            "attribute_count_total": total,
            "attributes": page_rows,
            "attributes_lines": lines,
            "tabular_sections": tabular_out if include_tabular else [],
            "prefer": (
                f"Всего реквизитов шапки: {total}. "
                f"В этой порции: {len(page_rows)}. "
                "Выведи пользователю КАЖДУЮ строку attributes_lines "
                "(имя | синоним | тип). НЕ «основные», НЕ сокращай, "
                "НЕ пиши «список может быть дополнен». "
                "ТЧ — отдельно из tabular_sections."
            ),
        }
        return attach_paging(
            out, meta, tool="list_object_attributes",
            args={"full_name": full_name, "include_tabular": include_tabular, "limit": limit if explicit else 0},
        )

    def get_object_structure(
        self,
        full_name: str,
        include_form_layout: bool = False,
        form_name: str = "",
    ) -> dict[str, Any]:
        """
        Реквизиты, ТЧ, формы, макеты, команды.
        Подсистема: content + child_subsystems.
        include_form_layout: layout Form.xml из dump (DataPath, element_types).
        """
        import time as _time

        raw_name = (full_name or "").strip()
        if raw_name and "." not in raw_name:
            meta = self.find_metadata(raw_name.replace("_", " "), "Все", 5)
            items = meta.get("items") or []
            if items and items[0].get("full_name"):
                raw_name = str(items[0]["full_name"])
            else:
                # bare subsystem name
                if not raw_name.startswith("Подсистема"):
                    raw_name = f"Подсистема.{raw_name}"
        try:
            name = normalize_metadata_name(raw_name)
        except ValueError:
            name = raw_name  # Подсистема.A.B и др.

        cache_key = f"{name}|{bool(include_form_layout)}|{form_name or ''}"
        hit = self._structure_cache.get(cache_key)
        if hit and (_time.time() - hit[0]) < self._cache_ttl_sec:
            cached = dict(hit[1])
            cached["from_cache"] = True
            return cached

        structure: dict[str, Any] = {}
        if not str(name).startswith("Подсистема."):
            structure = self.backend.get_object_structure(name)
            if not isinstance(structure, dict):
                structure = {"error": str(structure)}
        else:
            structure = {"full_name": name}

        # dump enrichment always (forms/templates/subsystem content)
        from tools.object_detail import enrich_from_dump

        # always pass structure (even with error) so dump can fill attributes/forms
        base = structure if isinstance(structure, dict) else {"full_name": name}
        if base.get("error") and not any(
            base.get(k)
            for k in ("attributes", "fields", "tabular_sections", "forms", "Реквизиты")
        ):
            base = {"full_name": name, "error": base.get("error")}
        structure = enrich_from_dump(
            name,
            base,
            code_dir=self._code_dir() or None,
            include_form_layout=bool(include_form_layout),
            form_name=form_name or "",
        )

        has_dump_data = any(
            structure.get(k)
            for k in (
                "attributes",
                "fields",
                "tabular_sections",
                "forms",
                "dimensions",
                "resources",
                "subsystem",
                "Реквизиты",
            )
        )
        if structure.get("error") and not has_dump_data:
            return err(
                str(structure.get("error")),
                hint="GET object?name=Тип.Имя или выгрузка конфигурации",
            )
        if has_dump_data:
            structure.pop("error", None)

        fields = parse_structure_fields(structure)
        header = [f for f in fields if not f.tabular_section]
        field_rows = []
        for f in header:  # все реквизиты шапки, без обрезки «первых 100»
            row = slim_field(f.to_dict())
            if not row.get("type") and f.type_str:
                row["type"] = f.type_str
                row["тип"] = f.type_str
            field_rows.append(row)
        # компактная полная таблица для ответа «все реквизиты» (модель не сокращать!)
        attributes_table = [
            {
                "name": str(f.get("name") or ""),
                "synonym": str(f.get("synonym") or ""),
                "type": str(f.get("type") or f.get("тип") or "")[:120],
            }
            for f in field_rows
            if f.get("name")
        ]
        attributes_lines = [
            f"{a['name']} | {a['synonym']} | {a['type']}" for a in attributes_table
        ]

        # Статусы/перечисления — в начало ответа (не теряются при обрезке 6k)
        try:
            from tools.object_detail import enrich_enum_fields_on_structure

            enrich_enum_fields_on_structure(
                structure, field_rows, code_dir=self._code_dir() or None
            )
        except Exception:
            pass

        ts_map: dict[str, list[dict[str, str]]] = {}
        # prefer raw tabular_sections with nested attributes
        raw_ts = structure.get("tabular_sections") or []
        if isinstance(raw_ts, list) and raw_ts:
            for ts in raw_ts:
                if not isinstance(ts, dict):
                    continue
                tname = str(ts.get("name") or "")
                attrs = ts.get("attributes") or []
                rows = []
                for a in attrs:
                    if isinstance(a, dict):
                        rows.append(
                            {
                                "name": a.get("name"),
                                "synonym": a.get("synonym") or "",
                                "type": a.get("type") or a.get("тип") or "",
                                "тип": a.get("тип") or a.get("type") or "",
                            }
                        )
                if tname:
                    ts_map[tname] = rows
        else:
            for f in fields:
                if not f.tabular_section:
                    continue
                ts_map.setdefault(f.tabular_section, []).append(slim_field(f.to_dict()))

        full = str(structure.get("full_name") or name)
        meta_class = full.split(".", 1)[0] if "." in full else ""
        out: dict[str, Any] = {
            "full_name": full,
            "type": meta_class,
            "тип_объекта": meta_class,
            "synonym": str(structure.get("synonym") or structure.get("Синоним") or ""),
            # ПОЛНЫЙ список реквизитов — в начале (не теряется при compact / не сокращать LLM!)
            "attribute_count": len(attributes_table),
            "attributes_table": attributes_table,
            "attributes_lines": attributes_lines,
            "prefer": (
                f"В structure {len(attributes_table)} реквизитов шапки. "
                "Если пользователь просит «все реквизиты» — выведи ВСЕ строки "
                "из attributes_lines (имя | синоним | тип), НЕ «основные», НЕ урезай. "
                "ТЧ — отдельно tabular_sections. "
                "Не пиши «список может быть дополнен» — это полный список из structure."
            ),
            "note": (
                "attributes_table / attributes_lines — ПОЛНЫЙ список реквизитов. "
                "fields — то же подробно. status/dimensions/resources/virtual_tables. "
                "Layout: describe_form(full_name, form_name=forms[].name)."
            ),
        }
        # status/enum FIRST — не теряются при tool_result_max_chars
        for k in (
            "status_fields",
            "enum_fields",
            "enum_values_for_status",
        ):
            if structure.get(k):
                out[k] = structure[k]
        # измерения/ресурсы регистров — сразу после status (для .Остатки)
        if structure.get("dimensions"):
            out["dimensions"] = structure["dimensions"]
            out["dimension_names"] = [
                d.get("name")
                for d in structure["dimensions"]
                if isinstance(d, dict) and d.get("name")
            ]
        if structure.get("resources"):
            out["resources"] = structure["resources"]
            out["resource_names"] = [
                r.get("name")
                for r in structure["resources"]
                if isinstance(r, dict) and r.get("name")
            ]
        # подсказка виртуальных таблиц для регистров
        _fn = str(full or structure.get("full_name") or name or "")
        if _fn.startswith("РегистрНакопления."):
            out["virtual_tables"] = {
                "balances": f"{_fn}.Остатки(&Дата, )",
                "turnovers": f"{_fn}.Обороты(&Начало, &Конец, , )",
                "note": "Ресурсы в ВТ: ИмяРесурсаОстаток / ИмяРесурсаОборот. "
                "Измерения — dimensions[]. Без вложенных ВЫБРАТЬ.",
            }
        elif _fn.startswith("РегистрСведений."):
            out["virtual_tables"] = {
                "slice": f"{_fn}.СрезПоследних(&Дата, )",
                "note": "СрезПоследних для периодических РС.",
            }
        out["fields"] = field_rows
        out["field_count"] = len(header) or len(field_rows)
        if ts_map:
            out["tabular"] = {ts: rows for ts, rows in ts_map.items()}
            out["tabular_names"] = list(ts_map.keys())
            out["tabular_sections"] = [
                {"name": k, "attributes": v, "attribute_count": len(v)}
                for k, v in ts_map.items()
            ]

        # forms / templates / commands — full list as in config
        for key in (
            "forms",
            "templates",
            "commands",
            "modules",
            "form_names",
            "template_names",
            "command_names",
            "forms_count",
            "templates_count",
            "commands_count",
            "default_forms",
            "form_layout_of",
            "note_children",
        ):
            if structure.get(key) is not None and structure.get(key) != []:
                out[key] = structure[key]
            elif key in ("forms_count", "templates_count", "commands_count") and structure.get(
                key
            ) == 0:
                out[key] = 0
        if structure.get("dimensions"):
            out["dimensions"] = structure["dimensions"]
        if structure.get("resources"):
            out["resources"] = structure["resources"]
        if structure.get("enum_values"):
            out["enum_values"] = structure["enum_values"]
        if structure.get("content") is not None:
            out["content"] = structure.get("content")
            out["content_by_type"] = structure.get("content_by_type")
            out["child_subsystems"] = structure.get("child_subsystems")
        if structure.get("form_layout"):
            out["form_layout"] = structure["form_layout"]
        if structure.get("subsystem"):
            out["subsystem"] = {
                k: structure["subsystem"][k]
                for k in (
                    "synonym",
                    "content_count",
                    "content_by_type",
                    "child_subsystems",
                    "prefer",
                )
                if k in structure["subsystem"]
            }

        out["prefer"] = (
            "Покажи: 1) synonym 2) fields (имя+тип) 3) tabular_sections "
            "4) ВСЕ forms[].name (+ synonym) из ответа — имена как в конфигурации "
            "5) ВСЕ templates[].name 6) commands. "
            "Не подставляй «ФормаДокумента», если forms[] другие. "
            "Layout: describe_form(full_name, form_name) с именем из forms[]. "
            "default_forms — главные формы, назначенные в конфигураторе."
        )
        if len(header) > 100:
            out["truncated"] = True
            out["field_count_total"] = len(header)
        try:
            self._structure_cache[cache_key] = (_time.time(), dict(out))
            if len(self._structure_cache) > 120:
                oldest = sorted(
                    self._structure_cache.items(), key=lambda x: x[1][0]
                )[:40]
                for k, _ in oldest:
                    self._structure_cache.pop(k, None)
        except Exception:
            pass
        return out

    def describe_form(
        self, full_name: str = "", form_name: str = "", request: str = "", tab: str = ""
    ) -> dict[str, Any]:
        """Раскладка формы (управляемой Form.xml и обычной Form.bin): вкладки, элементы, кнопки, события."""
        from tools.object_detail import pick_main_form_name, read_form_layout

        fn = (full_name or "").strip()
        form = (form_name or "").strip()
        if not fn and request:
            from tools.form_map import _ui_object_full_name

            fn = _ui_object_full_name(request)
            if not form:
                # «ФормаДокумента / ФормаСписка» прямо во фразе
                import re as _re

                m = _re.search(r"(Форма[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_]*)", request)
                form = m.group(1) if m else ""
        if not fn:
            return need_clarification(
                "Форму какого объекта показать? Назовите документ или справочник.",
                about="object",
                missing=["full_name"],
            )
        if not form:
            # default form from config (DefaultObjectForm etc.), not hardcoded name
            st = self.get_object_structure(fn, include_form_layout=False)
            names = list(st.get("form_names") or [])
            if not names:
                for x in st.get("forms") or []:
                    if isinstance(x, dict) and x.get("name"):
                        names.append(str(x["name"]))
                    elif x:
                        names.append(str(x))
            form = pick_main_form_name(
                names, st.get("default_forms") or {}, preferred=""
            )
            if not form:
                e = err(
                    "Не указан form_name и форм нет в конфигурации",
                    hint="describe_object → forms[].name (имена как в конфигураторе)",
                )
                if isinstance(e, dict):
                    e["available_forms"] = names
                return e
        result = read_form_layout(fn, form, code_dir=self._code_dir() or None, tab=tab or "")
        if isinstance(result, dict) and result.get("error"):
            # help agent with real names
            st = self.get_object_structure(fn, include_form_layout=False)
            result.setdefault(
                "available_forms",
                st.get("form_names")
                or [
                    (x.get("name") if isinstance(x, dict) else x)
                    for x in (st.get("forms") or [])
                ],
            )
            result.setdefault("default_forms", st.get("default_forms") or {})
            result.setdefault(
                "hint",
                "form_name должен быть из available_forms (как в конфигурации)",
            )
        return result

    def describe_object(
        self,
        full_name: str = "",
        request: str = "",
        include_form_layout: bool = True,
        form_name: str = "",
        tab: str = "",
    ) -> dict[str, Any]:
        """
        Полное описание объекта: реквизиты, ТЧ, формы, макеты, команды;
        имена children — фактические из конфигурации; для подсистемы — состав.
        request — разговорная фраза («все реквизиты реализации») → resolve → structure.
        """
        fn = (full_name or "").strip()
        req = (request or "").strip()
        pl_req = req.lower().replace("ё", "е")
        if not fn and req:
            # каталог выгрузки: точное имя или «класс + уверенный матч» (справочник номенклатура)
            from tools.name_match import resolve_object_full_name

            fn = resolve_object_full_name(req, strict=True)
        # «макеты/формы/события» без объекта — нельзя resolve по слову «макет»
        # (иначе служебный регистр макетов печати вместо документа)
        followup_only = bool(
            req
            and not fn
            and re.search(
                r"\b(макет|печатн|форм|событ|элемент|какие\s+поля|что\s+вывод)",
                pl_req,
            )
            and not _phrase_has_object_for_describe(req)
        )
        if followup_only and not fn:
            return {
                "ok": False,
                "need_clarification": True,
                "about": "object",
                "error": None,
                "question": (
                    "Уточните объект (документ/справочник), чьи макеты/формы смотреть. "
                    "Или продолжите диалог после «реквизиты …» — подставлю объект из контекста."
                ),
                "missing": ["full_name"],
                "hint": "describe_object(full_name=Документ.X) или request=«реквизиты реализации»",
                "tool": "describe_object",
            }
        if not fn and req:
            # «все реквизиты у …» → full_name только из resolve этой CF
            ro = self.resolve_object(req, "Все", 8)
            if isinstance(ro, dict):
                top = ro.get("top") or ro.get("full_name") or ro.get("suggested")
                if not top and ro.get("options"):
                    o0 = ro["options"][0]
                    top = o0.get("full_name") if isinstance(o0, dict) else o0
                items = list(ro.get("items") or ro.get("options") or [])

                def _struct_rank(fn0: str) -> tuple[int, int]:
                    f = (fn0 or "").lower().replace("ё", "е")
                    leaf = f.split(".")[-1] if f else ""
                    # не служебные «макеты печати» при вопросе о документе
                    if "пользовательски" in f and "макет" in f:
                        return (100, 0)
                    # совпадение стемов фразы с именем объекта
                    stems = re.findall(r"[а-яa-zё]{4,}", pl_req)
                    hit = 0
                    for s in stems:
                        if s in ("реквизит", "структур", "элемент", "событ", "макет",
                                 "форм", "какие", "поля", "все", "документ"):
                            continue
                        if s in leaf or s[:5] in leaf:
                            hit += 1
                    # токены фразы в leaf — уже в hit; доп. штраф только если
                    # leaf содержит «лишние» слова, которых нет во фразе (универсально)
                    leaf_parts = re.findall(r"[а-яa-zё]{5,}", leaf)
                    extra = 0
                    for lp in leaf_parts:
                        if not any(s[:4] in lp or lp[:4] in s for s in stems if len(s) >= 4):
                            extra += 1
                    if extra >= 2 and hit <= 1:
                        hit -= 2
                    class_r = 10
                    if f.startswith("документ."):
                        class_r = 0
                    elif f.startswith("справочник."):
                        class_r = 1
                    elif f.startswith("регистрнакопления."):
                        class_r = 2
                    elif f.startswith("регистрсведений."):
                        class_r = 5
                    elif f.startswith(("обработк", "отчет", "отчёт")):
                        class_r = 20
                    return (class_r, -hit)

                # prefer document/catalog from items
                ranked: list[str] = []
                for it in items:
                    if isinstance(it, dict) and it.get("full_name"):
                        ranked.append(str(it["full_name"]))
                    elif isinstance(it, str):
                        ranked.append(it)
                if top:
                    ranked = [str(top)] + [x for x in ranked if x != str(top)]
                ranked = sorted(set(ranked), key=_struct_rank)
                if ranked:
                    top = ranked[0]
                if top and _struct_rank(str(top))[0] < 50:
                    fn = str(top)
                elif ro.get("need_clarification"):
                    # filter out print-template register from options
                    opts = [
                        x
                        for x in (ro.get("options") or ranked)[:12]
                        if _struct_rank(
                            x.get("full_name") if isinstance(x, dict) else str(x)
                        )[0]
                        < 50
                    ]
                    ro["tool"] = "describe_object"
                    ro["options"] = opts or ro.get("options")
                    ro["prefer"] = (
                        "Нужен full_name документа/справочника из options. "
                        "Не бери служебный регистр макетов печати вместо объекта. "
                        "Покажи options."
                    )
                    return ro
            if not fn:
                return {
                    "ok": False,
                    "error": "Не удалось определить объект для структуры",
                    "hint": "Укажите full_name=Документ.X или фразу с именем объекта",
                    "request": req,
                }
        if not fn:
            return {
                "ok": False,
                "error": "Нужен full_name или request",
                "hint": "describe_object(full_name='Документ.<ИмяИзResolve>')",
            }
        out = self.get_object_structure(
            fn,
            include_form_layout=include_form_layout,
            form_name=form_name or "",
        )
        if isinstance(out, dict) and tab and out.get("form_layout_of"):
            # подробно одна вкладка формы
            from tools.object_detail import read_form_layout

            out = dict(out)
            out["form_layout"] = read_form_layout(
                str(out.get("full_name") or fn), str(out["form_layout_of"]), code_dir=self._code_dir() or None, tab=tab
            )
        if isinstance(out, dict) and out.get("form_layout") and (
            form_name or tab or re.search(r"форм|вкладк|закладк|страниц|кнопк|пол[еяю]|элемент|надпис", pl_req)
        ) and not (not form_name and not tab and "предопредел" in pl_req):  # «предопределённые элементы» — не форма

            out = dict(out)
            out["_form_focus"] = True
        if isinstance(out, dict) and not out.get("error"):
            out["tool"] = "describe_object"
            out["ok"] = True
            # краткий brief для LLM (не выдумывать DefaultObjectForm)
            form_names: list[str] = []
            for f in out.get("forms") or []:
                if isinstance(f, dict) and f.get("name"):
                    form_names.append(
                        str(f.get("name"))
                        + (f" ({f.get('synonym')})" if f.get("synonym") else "")
                    )
                elif isinstance(f, str):
                    form_names.append(f)
            if not form_names and out.get("form_names"):
                form_names = [str(x) for x in out["form_names"][:40]]
            tmpl_names: list[str] = []
            for t0 in out.get("templates") or []:
                if isinstance(t0, dict) and t0.get("name"):
                    tmpl_names.append(
                        str(t0.get("name"))
                        + (f" ({t0.get('synonym')})" if t0.get("synonym") else "")
                    )
                elif isinstance(t0, str):
                    tmpl_names.append(t0)
            attr_names: list[str] = []
            for a in (out.get("attributes") or out.get("header_fields") or [])[:40]:
                if isinstance(a, dict) and a.get("name"):
                    attr_names.append(str(a["name"]))
                elif isinstance(a, str):
                    attr_names.append(a)
            if out.get("attributes_lines") and not attr_names:
                attr_names = [str(x) for x in out["attributes_lines"][:40]]
            ts_brief: list[str] = []
            for ts in out.get("tabular_sections") or []:
                if isinstance(ts, dict) and ts.get("name"):
                    ts_brief.append(str(ts["name"]))
            # свойства объекта (вид регистра, владельцы, периодичность, проведение…) и предопределённые элементы
            props, predef = [], []
            try:
                from ingest.xml_cards import object_extras
                from tools.object_detail import dump_meta_xml_path

                xp = dump_meta_xml_path(str(out.get("full_name") or fn), self._code_dir() or None)
                if xp is not None:
                    props, predef = object_extras(xp)
            except Exception:  # noqa: BLE001 — дополнительные сведения не должны ронять описание
                pass
            if props:
                out["properties"] = props
            if predef:
                lim = 120 if re.search(r"предопредел|счет|счёт", pl_req) else 30
                out["predefined"] = [x.split(" ", 1)[1] if x.startswith("Предопределённый ") else x for x in predef[:lim]]
                out["predefined_total"] = len(predef)
                if len(predef) > lim:
                    out["predefined_note"] = (f"Показано {lim} из {len(predef)}; остальные — "
                                              f"search_code(query='Предопределённый {fn}…')")
            out["structure_brief"] = {
                "full_name": out.get("full_name") or fn,
                **({"properties": props} if props else {}),
                **({"predefined_total": len(predef)} if predef else {}),
                "synonym": out.get("synonym"),
                "attributes_sample": attr_names[:30],
                "attributes_count": len(attr_names)
                or out.get("attribute_count")
                or out.get("attributes_count"),
                "tabular_sections": ts_brief[:20],
                "forms": form_names[:25],
                "templates": tmpl_names[:30],
                "default_forms": out.get("default_forms"),
            }
            out["prefer"] = (
                "Ответь СТРОГО по structure_brief + fields tool result. "
                f"Объект: {out.get('full_name') or fn}. "
                "1) Реквизиты — attributes_sample / attributes_lines (реальные имена). "
                "2) ТЧ — tabular_sections. "
                "3) Формы — forms[] (НЕ выдумывай DefaultObjectForm, если нет в списке). "
                "4) Макеты печати — templates[] (реальные имена из CF). "
                "5) События/элементы формы — вызови describe_form(full_name, form_name=…). "
                "Поля макета: перечисли templates; детали layout — describe_form / read. "
                "НЕ спрашивай период. НЕ подменяй объект служебным регистром макетов."
                + (" Свойства объекта (вид регистра, владельцы, периодичность, проведение) — properties[]." if props else "")
                + (" Предопределённые элементы — predefined[]." if predef else "")
            )
            # вопрос про макеты — явный hint
            if any(x in pl_req for x in ("макет", "печатн", "template")):
                out["prefer"] = (
                    f"Вопрос про МАКЕТЫ объекта {fn}. "
                    f"Список templates: {tmpl_names[:20] or 'см. structure_brief.templates'}. "
                    "Покажи имена макетов из templates[]; поля печатной формы — "
                    "в макете/СКД объекта, не в служебном регистре. "
                    "Не выдумывай имена макетов, если их нет в templates."
                )
        return out

    def search_fields(
        self,
        full_name: str,
        search_text: str,
        header_only: bool = True,
        limit: int = 30,
    ) -> dict[str, Any]:
        """Только найденные поля (name/synonym/type)."""
        try:
            full_name = normalize_metadata_name(full_name)
        except ValueError as e:
            return err(str(e))
        name, structure, error = self._require_structure(full_name)
        if error:
            return err(str(error.get("error")), hint=error.get("hint"))
        assert structure is not None
        fields = parse_structure_fields(structure)
        lim = clamp_limit(limit, 15, 30)
        matched = discover_search_fields(
            fields, search_text, header_only=header_only, limit=lim
        )
        return {
            "fields": [slim_field(f.to_dict()) for f in matched],
            "count": len(matched),
        }

    def query_by_attributes(
        self,
        request: str = "",
        full_name: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """
        Список документов/элементов по реквизитам шапки текущей CF.
        «в каких заказах ожидается оплата» → structure → ГДЕ Т.<поле>.
        """
        from tools.attr_query import run_attr_query
        from tools.report_format import enrich_report_result

        raw = (request or "").strip()
        fn = (full_name or "").strip()
        lim = clamp_limit(limit, 50, 200)

        def _resolve(search: str) -> dict[str, Any]:
            # документы предпочтительнее справочников для «в каких …»
            qf = self.resolve_object(search, object_type="Документы", limit=8)
            if isinstance(qf, dict) and (
                qf.get("full_name") or qf.get("top") or qf.get("need_clarification")
            ):
                return qf
            return self.resolve_object(search, object_type="Все", limit=8)

        def _structure(name: str) -> dict[str, Any]:
            st = self.get_object_structure(name)
            if isinstance(st, dict) and not st.get("full_name"):
                st["full_name"] = name
            return st if isinstance(st, dict) else {"error": str(st)}

        def _execute(query: str, limit: int = 50) -> dict[str, Any]:  # noqa: A002
            return self.execute_query(
                query,
                limit=limit,
                offset=int(offset or 0),
            )

        out = run_attr_query(
            raw,
            full_name=fn,
            limit=lim,
            resolve_fn=_resolve if not fn else None,
            structure_fn=_structure,
            execute_fn=_execute,
        )
        if not isinstance(out, dict):
            return err(str(out))
        mode = str(out.get("report_mode") or "attr_filter")
        return enrich_report_result(
            out,
            request=raw,
            full_name=str(out.get("full_name") or fn),
            report_mode=mode,
        )

    def resolve_business_fields(self, full_name: str) -> dict[str, Any]:
        """
        Реквизиты 1С объекта: имя как в конфигураторе + как писать в ВЫБРАТЬ.
        """
        try:
            full_name = normalize_metadata_name(full_name)
        except ValueError as e:
            return err(str(e))
        name, structure, error = self._require_structure(full_name)
        if error:
            return err(str(error.get("error")), hint=error.get("hint"))
        assert structure is not None
        kind = self._object_kind(name)
        raw = resolve_business_map(structure, object_kind=kind)
        roles_raw = raw.get("roles") or {}
        roles: dict[str, str] = {}
        for sense, info in roles_raw.items():
            if isinstance(info, dict) and info.get("name"):
                roles[str(sense)] = str(info["name"])
            elif isinstance(info, str):
                roles[str(sense)] = info

        # краткая шпаргалка для запроса 1С
        parts = []
        for key, label in (
            ("date", "Дата"),
            ("number", "Номер"),
            ("amount", "Сумма"),
            ("counterparty", "Контрагент"),
            ("posted", "Проведен"),
        ):
            rn = roles.get(key)
            if not rn:
                continue
            if key == "counterparty":
                parts.append(f"ПРЕДСТАВЛЕНИЕ(Т.{rn}) КАК {rn}")
            else:
                parts.append(f"Т.{rn} КАК {rn}")
        sample = ""
        if parts:
            sample = (
                f"ВЫБРАТЬ ПЕРВЫЕ 50 {', '.join(parts)} "
                f"ИЗ {name} КАК Т"
            )
            if roles.get("posted"):
                sample += f" ГДЕ Т.{roles['posted']} = ИСТИНА"

        return {
            "full_name": name,
            "реквизиты_1с": raw.get("реквизиты_1с") or {},
            "имена_для_запроса": roles,  # смысл → ИмяРеквизита (Т.Имя)
            "roles": roles,  # совместимость
            "date_field": roles.get("date"),
            "number_field": roles.get("number"),
            "amount_field": roles.get("amount"),
            "counterparty_field": roles.get("counterparty"),
            "пример_запроса_1с": sample,
            "как_в_запросе_1с": raw.get("как_в_запросе_1с"),
            "note": (
                "Имена — реквизиты метаданных 1С. В запросе: Т.ИмяРеквизита. "
                "Для ссылочных: ПРЕДСТАВЛЕНИЕ(Т.Контрагент)."
            ),
        }

    def get_manager_fields(self, full_name: str) -> dict[str, Any]:
        """Алиас: реквизиты объекта для языка запросов 1С."""
        return self.resolve_business_fields(full_name)
