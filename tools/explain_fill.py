# -*- coding: utf-8 -*-
"""
Почему реквизит/поле не заполнилось или заполнилось не так.

Универсально: любой объект + любое имя поля (без хардкода метаданных).
Runtime: Qdrant.
"""

from __future__ import annotations

import re
from typing import Any

from tools.code_search import find_modules, parse_nl_query

# Qdrant — лениво (data-tools не должны падать без qdrant_client)

_ASSIGN_PATTERNS = (
    r"{field}\s*=",
    r"Объект\s*\.\s*{field}\s*=",
    r"Источник\s*\.\s*{field}",
    r"Строка\w*\s*\.\s*{field}\s*=",
    r"ДанныеЗаполнения\s*\.\s*{field}",
    r"ЗаполнитьЗначенияСвойств\s*\([^)]*{field}",
    r"Значение\s*=\s*.*{field}",
)


def is_fill_logic_question(phrase: str) -> bool:
    """
    Вопрос про логику/процесс заполнения (код), не «сколько продаж».

    Универсально: речь (без PascalCase) содержит заполн + (логик/как/расскажи/…).
    Имена процедур CF не перечисляются.
    """
    try:
        from tools.phrase_struct import speech_norm
    except Exception:
        speech_norm = lambda t: (t or "").lower().replace("ё", "е")  # noqa: E731

    speech = speech_norm(phrase)
    if not speech or "заполн" not in speech:
        return False
    return bool(
        re.search(
            r"логик|"
            r"(как|откуда|где|кто)\s+\w*заполн|"
            r"(расскаж|объясн|разбер)\w*.{0,40}заполн|"
            r"заполн\w*.{0,40}(логик|как|откуда|где)",
            speech,
        )
    )


def is_fill_reason_question(phrase: str) -> bool:
    """Почему пусто / как заполняется — по речи, без имён CF."""
    try:
        from tools.phrase_struct import speech_norm
    except Exception:
        speech_norm = lambda t: (t or "").lower().replace("ё", "е")  # noqa: E731

    speech = speech_norm(phrase)
    if not speech:
        return False
    if is_fill_logic_question(phrase):
        return True
    # отследи/цепочка — не fill (отдельный code-route)
    if re.search(r"(отслед|прослед|цепочк\w*\s+вызов|в\s+код[еу])", speech):
        return False
    markers = (
        "не заполнил",
        "не заполнен",
        "не заполняется",
        "пустое значен",
        "пустой реквизит",
        "почему не ",
        "не подставил",
        "не подставляется",
        "заполнилось не так",
        "неправильно заполнил",
        "не те пол",
        "не то пол",
        "неправильно записал",
        "не те поля",
        "куда записалось",
        "откуда заполн",
        "как заполняется",
        "где заполняется",
        "кто заполняет",
    )
    if any(m in speech for m in markers):
        return True
    if "заполн" in speech and re.search(
        r"(почему|где|как|не|реквизит|поле|логик|расскаж)",
        speech,
    ):
        return True
    return False


def is_posting_error_question(phrase: str) -> bool:
    """Почему не проводится / ошибка проведения / блокировка."""
    pl = (phrase or "").lower().replace("ё", "е")
    if not pl:
        return False
    markers = (
        "не проводится",
        "не проводится документ",
        "не провести",
        "не могу провести",
        "ошибка проведен",
        "ошибка при проведен",
        "не удается провести",
        "не получается провести",
        "lock conflict",
        "конфликт блокир",
        "отказ при проведен",
        "обработкапроведения",
        "почему не проводится",
        "не проводитс",
    )
    if any(m in pl for m in markers):
        return True
    # «блокировка» только вместе с проведением (не миграция managed locks)
    if re.search(r"блокир", pl) and re.search(
        r"провест|проведен|при\s+запис|конфликт", pl
    ):
        return True
    if re.search(r"провест\w*|не\s+проводи", pl) and re.search(
        r"(почему|ошибк|блокир|отказ|нельзя|не\s+могу|не\s+удается|не\s+получается)",
        pl,
    ):
        return True
    return False


def is_status_question(phrase: str) -> bool:
    """Мета: какие ЗНАЧЕНИЯ статуса. Не «документы со статусом X» (это данные)."""
    pl = (phrase or "").lower().replace("ё", "е")
    if not pl:
        return False
    # список записей, отфильтрованный статусом — не сюда
    if re.search(
        r"(в\s+каких|у\s+каких|со\s+статус|с\s+статус\w+\s+[а-яёa-z]{3,})",
        pl,
    ):
        return False
    if re.search(
        r"(какие\s+статус|какой\s+статус|статус\w*\s+бывают|"
        r"значения\s+статус|список\s+статус|перечисл\w*\s+статус|"
        r"статус документа|статусы\s+(документ|заказ|есть))",
        pl,
    ):
        return True
    if re.fullmatch(r"статусы", pl.strip()):
        return True
    return False


def is_debt_unpaid_question(phrase: str) -> bool:
    """Совместимость: канон в linked_query (без зависимости от Qdrant)."""
    from tools.linked_query import is_debt_unpaid_question as _f

    return _f(phrase)


# Типовые реквизиты (разговорные → канон для поиска в коде)
_FIELD_ALIASES: dict[str, str] = {
    "контрагент": "Контрагент",
    "партнер": "Партнер",
    "партнёр": "Партнер",
    "склад": "Склад",
    "организация": "Организация",
    "номенклатура": "Номенклатура",
    "договор": "Договор",
    "менеджер": "Менеджер",
    "подразделение": "Подразделение",
    "валюта": "Валюта",
    "сумма": "СуммаДокумента",
    "комментарий": "Комментарий",
    "дата": "Дата",
    "номер": "Номер",
    "соглашение": "Соглашение",
    "сделка": "Сделка",
    "цена": "Цена",
    "количество": "Количество",
}

_OBJECT_SKIP = {
    "документ",
    "документа",
    "документе",
    "справочник",
    "справочника",
    "почему",
    "заполнился",
    "заполнилось",
    "заполняется",
    "не",
    "реквизит",
    "поле",
    "пусто",
    "пустое",
    "значение",
    "форма",
    "модуль",
    "как",
    "какой",
    "какая",
    "какие",
}


def _extract_field_and_object(request: str, field: str, full_name: str) -> tuple[str, str]:
    """
    «почему не заполнился контрагент в реализации» → field=Контрагент, obj=реализация.
    Не путать object (реализация) с field.
    """
    fld = (field or "").strip()
    obj = ""
    fn = (full_name or "").strip()
    if fn and "." in fn:
        obj = fn.split(".")[-1]
    req = request or ""
    pl = req.lower().replace("ё", "е")

    # 1) «реквизит/поле X»
    if not fld:
        m = re.search(
            r"(?:реквизит|поле|колонк\w*|attribute|field)\s+"
            r"[«\"]?([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)",
            req,
            re.I,
        )
        if m:
            fld = m.group(1)

    # 2) «не заполнился/заполняется X» / «как заполняется X»
    if not fld:
        m = re.search(
            r"(?:не\s+)?заполнил(?:ся|ось|ась)?\s+"
            r"[«\"]?([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)",
            req,
            re.I,
        )
        if m and m.group(1).lower().replace("ё", "е") not in _OBJECT_SKIP:
            fld = m.group(1)
    if not fld:
        m = re.search(
            r"заполняется\s+[«\"]?([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)",
            req,
            re.I,
        )
        if m and m.group(1).lower().replace("ё", "е") not in _OBJECT_SKIP:
            fld = m.group(1)

    # 3) «X в реализации / в документе Y / в поступлении»
    if not fld:
        m = re.search(
            r"([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]{2,})\s+в\s+"
            r"(?:документ[аеу]?\s+)?"
            r"([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]{3,})",
            req,
            re.I,
        )
        if m:
            a, b = m.group(1), m.group(2)
            al = a.lower().replace("ё", "е")
            bl = b.lower().replace("ё", "е")
            if al not in _OBJECT_SKIP and al in _FIELD_ALIASES:
                fld = a
                if not obj and bl not in _OBJECT_SKIP:
                    obj = b
            elif al not in _OBJECT_SKIP and bl not in _OBJECT_SKIP:
                # «поле X в документе Y» — Y берём как есть (resolve позже)
                fld = a
                if not obj:
                    obj = b

    # 4) известные field-aliases в тексте
    if not fld:
        for alias, canon in _FIELD_ALIASES.items():
            if re.search(rf"\b{re.escape(alias)}\b", pl):
                fld = canon
                break

    # 5) object: «в <фраза объекта>» — без whitelist «реализация/поступление»
    if not obj:
        m = re.search(
            r"(?:в|документ[аеу]?|для)\s+"
            r"(?:документ[аеу]?\s+)?"
            r"([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]{4,})",
            req,
            re.I,
        )
        if m:
            cand = m.group(1)
            cl = cand.lower().replace("ё", "е")
            if cl not in _OBJECT_SKIP and cl not in _FIELD_ALIASES:
                # «реализации» → срез окончания для resolve (морфология, не CF)
                if cl.endswith("ии") and len(cl) > 5:
                    cand = cand[:-2] + "я" if cand[-2:].lower() == "ии" else cand
                elif cl.endswith(("е", "у", "а", "ы", "и")) and len(cl) > 5:
                    # оставить основу — resolve_object_name сам сопоставит
                    pass
                obj = cand

    if not obj:
        parsed = parse_nl_query(req)
        on = (parsed.get("object_name") or "").strip()
        if on and on.lower().replace("ё", "е") not in _FIELD_ALIASES:
            obj = on

    # 6) fallback tokens — field из aliases, object отдельно
    if not fld or not obj:
        tokens = re.findall(r"[A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]{2,}", req)
        for t in tokens:
            tl = t.lower().replace("ё", "е")
            if tl in _OBJECT_SKIP:
                continue
            if not fld and tl in _FIELD_ALIASES:
                fld = _FIELD_ALIASES[tl]
                continue
            if not obj and tl not in _FIELD_ALIASES and len(t) >= 5:
                # не брать склонение field как object
                if tl.rstrip("аяыиуе") in {k.rstrip("аяыиуе") for k in _FIELD_ALIASES}:
                    continue
                obj = t

    # канон field
    if fld:
        fl = fld.lower().replace("ё", "е")
        if fl in _FIELD_ALIASES:
            fld = _FIELD_ALIASES[fl]
        elif fld[0].islower():
            fld = fld[:1].upper() + fld[1:]

    # object не должен совпадать с field
    if obj and fld and obj.lower() == fld.lower():
        obj = ""
    # нормализация object → имя из CF (name_match), без hardcode «реализация»
    if obj:
        try:
            from tools.name_match import is_pascal_identifier, resolve_object_full_name

            if not is_pascal_identifier(obj):
                # «доллар», «рубль», «заказе» — слово речи: объект только если каталог уверенно его знает
                full = resolve_object_full_name(obj)
                obj = full.split(".", 1)[-1] if full else ""
        except Exception:
            pass

    return obj, fld



def explain_fill(
    request: str = "",
    *,
    field: str = "",
    full_name: str = "",
    object_name: str = "",
    max_hits: int = 20,
) -> dict[str, Any]:
    """
    Найти места, где поле/реквизит заполняется или обрывается заполнение.
    """
    try:
        from vector.catalog import code_collection_ready
        from vector.search import search_code_vectors
    except Exception as e:
        return {
            "ok": False,
            "error": f"Qdrant/vector недоступен: {e}",
            "source": "qdrant",
            "hint": "pip install qdrant-client fastembed; поднять Qdrant",
        }

    st = code_collection_ready()
    if not st.get("ready"):
        return {"ok": False, "error": st.get("error") or st.get("hint"), "source": "qdrant"}

    req = (request or "").strip()
    obj, fld = _extract_field_and_object(req, field, full_name)
    if object_name:
        obj = object_name
    if not fld:
        # «документ не проводится / пишет не хватает …» — поля нет: разбор проверок проведения
        return explain_posting_checks(req, full_name=full_name, max_hits=max_hits)

    lim = max(5, min(int(max_hits or 20), 40))
    # resolve short object names (фраза → object_name из Qdrant/metadata)
    if obj and len(obj) < 14 and not re.search(r"[A-ZА-ЯЁ].*[a-zа-яё].*[A-ZА-ЯЁ]", obj):
        try:
            from tools.object_movements import _resolve_object_from_phrase

            ro = _resolve_object_from_phrase(obj)
            if ro:
                obj = ro
        except Exception:
            pass

    queries = [
        f"{fld} =",
        f"Объект.{fld}",
        f".{fld} =",
        "ЗаполнитьЗначенияСвойств",
        f"ОбработкаЗаполнения {fld}",
        "ОбработкаЗаполнения",
        f"ПриСозданииНаСервере {fld}",
        f"ДанныеЗаполнения.{fld}",
        f"{fld}",
    ]
    if obj:
        queries = [
            f"{obj} {fld}",
            f"{fld} {obj}",
            f"Documents/{obj} {fld}",
            f"ОбработкаЗаполнения {obj}",
        ] + queries

    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for q in queries:
        r = search_code_vectors(
            q,
            limit=min(15, lim),
            match_mode="hybrid",
            object_name=obj if obj and q == queries[0] else "",
            module_filter=obj or "",
        )
        if not r.get("ok"):
            # retry without object filter
            r = search_code_vectors(q, limit=min(15, lim), match_mode="hybrid")
        for m in r.get("matches") or []:
            path = (m.get("path") or "").replace("\\", "/")
            sn = m.get("snippet") or ""
            key = f"{path}|{sn[:60]}"
            if key in seen:
                continue
            if fld.lower() not in sn.lower() and fld.lower() not in path.lower():
                # still keep fill handlers
                if "заполнен" not in sn.lower() and "обработказаполнения" not in sn.lower():
                    if "заполнитьзначениясвойств" not in sn.lower():
                        continue
            seen.add(key)
            kind = "assignment"
            low = sn.lower()
            if "возврат" in low or "return" in low:
                kind = "early_return"
            elif "обработказаполнения" in low or "заполнен" in path.lower():
                kind = "fill_handler"
            elif "присоздании" in low or "oncreate" in low:
                kind = "form_create"
            elif "заполнитьзначениясвойств" in low:
                kind = "fill_properties"
            elif "если" in low and ("не " in low or "пуст" in low):
                kind = "condition"
            hits.append(
                {
                    "path": path,
                    "score": m.get("score"),
                    "match_kind": m.get("match_kind"),
                    "kind": kind,
                    "snippet": sn,
                    "object_name": m.get("object_name"),
                    "location": m.get("location"),
                }
            )
            if len(hits) >= lim:
                break
        if len(hits) >= lim:
            break

    # rank: same object first, then assignment/fill_handler
    def _rank(h: dict[str, Any]) -> float:
        sc = float(h.get("score") or 0)
        if obj and obj.lower() in (h.get("path") or "").lower():
            sc += 50
        sc += {
            "assignment": 20,
            "fill_handler": 25,
            "fill_properties": 18,
            "form_create": 15,
            "condition": 12,
            "early_return": 10,
        }.get(h.get("kind") or "", 0)
        if h.get("match_kind") == "exact":
            sc += 15
        return sc

    hits.sort(key=_rank, reverse=True)
    hits = hits[:lim]

    # modules of object for fill procedures
    modules = []
    if obj:
        fm = find_modules(obj, limit=8)
        modules = [
            {
                "path": m.get("path"),
                "full_name_1c": m.get("full_name_1c"),
                "kind": m.get("kind"),
            }
            for m in (fm.get("modules") or [])[:6]
        ]

    if not hits:
        # fallback: scan ObjectModule fill handlers via find_modules + chunks
        try:
            from vector.catalog import (
                assemble_module_text,
                get_chunks_by_source_path,
            )

            paths = []
            if obj:
                for m in (find_modules(obj, limit=10).get("modules") or []):
                    p = (m.get("path") or "").replace("\\", "/")
                    if "ObjectModule" in p or "ManagerModule" in p or "Form/Module" in p:
                        paths.append(p)
            for p in paths[:4]:
                chunks = get_chunks_by_source_path(p)
                if not chunks:
                    continue
                text = assemble_module_text(chunks)
                for i, ln in enumerate(text.splitlines()):
                    low = ln.lower()
                    if fld.lower() not in low and "обработказаполнения" not in low:
                        continue
                    if fld.lower() not in low and "заполнен" not in low:
                        continue
                    hits.append(
                        {
                            "path": p,
                            "line": i + 1,
                            "kind": (
                                "fill_handler"
                                if "заполнен" in low
                                else "assignment"
                            ),
                            "snippet": ln.strip()[:220],
                            "match_kind": "module_scan",
                            "score": 40,
                        }
                    )
                    if len(hits) >= lim:
                        break
                if len(hits) >= lim:
                    break
        except Exception:
            pass

    if not hits:
        summary = (
            f"По полю «{fld}»"
            + (f" объекта «{obj}»" if obj else "")
            + " точных присвоений в индексе не найдено. "
            "Возможны: заполнение на клиенте, подписка, типовой механизм без имени поля в коде."
        )
        conclusion = "no_hits"
        return {
            "ok": False,
            "source": "qdrant",
            "request": req,
            "field": fld,
            "object_hint": obj or None,
            "full_name": full_name or None,
            "conclusion": conclusion,
            "summary": summary,
            "hits": [],
            "related_modules": modules,
            "error": summary,
            "prefer": (
                "Скажи честно: в индексе нет присвоений. "
                "Предложи field=имя из structure и full_name=Документ.X. "
                "Не выдумывай причину."
            ),
        }

    kinds = {h["kind"] for h in hits}
    summary = (
        f"Поле «{fld}»"
        + (f" ({obj})" if obj else "")
        + f": найдено {len(hits)} мест. "
        f"Типы: {', '.join(sorted(kinds))}. "
        "Смотри hits[] (path + snippet). "
        "Условия/Возврат рядом могут обрывать заполнение."
    )
    conclusion = "found_write_sites"

    return {
        "ok": True,
        "source": "qdrant",
        "request": req,
        "field": fld,
        "object_hint": obj or None,
        "full_name": full_name or None,
        "conclusion": conclusion,
        "summary": summary,
        "hits": hits,
        "related_modules": modules,
        "prefer": (
            "Объясни по hits: где присваивается поле, есть ли early_return/условия. "
            "read_code(path, start_line≈) для контекста. "
            "Не утверждай причину без snippet. Универсально — без «типовых» домыслов."
        ),
    }


_CHECK_LINE = re.compile(
    r"Отказ\s*=\s*Истина|СообщитьПользователю|ОбщегоНазначения\.СообщитьПользователю|"
    r"ПроверяемыеРеквизиты|ВызватьИсключение|КонтрольОстатков|ПроверитьЗаполнение",
    re.I,
)
# «на основании / не перенеслись» — обработка заполнения объекта
_FILL_LINE = re.compile(
    r"Процедура\s+ОбработкаЗаполнения|ДанныеЗаполнения|ЗаполнитьЗначенияСвойств|ЗаполнитьПоОснованию|"
    r"ИнициализироватьДокумент|ЗаполнитьДокументНаОсновании|ТипЗнч\(ДанныеЗаполнения\)",
    re.I,
)


def _message_from_request(req: str) -> str:
    """Текст сообщения 1С из фразы: в кавычках или после «пишет/ошибка»."""
    m = re.search(r"[«\"']([^«»\"']{6,160})[»\"']", req or "")
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:пишет|выда[её]т|ругается|сообщени\w*|ошибк\w*)\s*[:\-—]?\s*(?:что\s+)?(.{6,120})$", req or "", re.I)
    return m.group(1).strip(" .,!?") if m else ""


def explain_posting_checks(request: str, *, full_name: str = "", max_hits: int = 20) -> dict[str, Any]:
    """
    Почему документ не проводится / откуда сообщение при проведении:
    1) места «Отказ = Истина / СообщитьПользователю / ПроверяемыеРеквизиты» в модуле объекта;
    2) если в фразе есть текст сообщения — где этот текст встречается в коде (поиск по Qdrant).
    Объект — из каталога выгрузки (resolve_object_full_name), без имён CF в коде.
    """
    req = (request or "").strip()
    fn = (full_name or "").strip()
    if not fn:
        try:
            from tools.name_match import resolve_object_full_name

            fn = resolve_object_full_name(req)
            if not fn:
                from tools.form_map import _ui_object_full_name

                fn = _ui_object_full_name(req)
        except Exception:
            fn = ""
    msg = _message_from_request(req)
    based_on = bool(re.search(r"основани|перенес|перенос|скопир|копирован", req.lower()))
    long_code_q = len(req.split()) >= 6 and bool(re.search(r"отказ|провер|остат|запис|провед|основани|перенес", req.lower()))
    if not fn and not msg and long_code_q:
        # объекта нет, но вопрос содержательный — ищем по смыслу в коде, а не переспрашиваем
        try:
            from vector.search import search_code_vectors

            sc = search_code_vectors(req, limit=10, match_mode="hybrid")
            sites = [{"path": m.get("path"), "snippet": (m.get("snippet") or "")[:300]} for m in sc.get("matches") or []]
        except Exception:
            sites = []
        if sites:
            return {
                "ok": True,
                "mode": "posting_checks_search",
                "request": req,
                "summary": f"Найдено {len(sites)} мест в коде по описанию.",
                "message_sites": sites,
                "prefer": "Покажи найденные места (path + snippet) и что в них проверяется. Не выдумывай.",
            }
    if not fn and not msg:
        return {
            "ok": True,
            "need_clarification": True,
            "question": "Какой документ не проводится и какой текст ошибки показывает 1С? "
            "Пришлите название документа и сообщение (можно скриншот текста).",
            "options": ["Название документа", "Текст сообщения об ошибке", "Номер документа"],
            "about": "posting_error",
        }

    hits: list[dict[str, Any]] = []
    lim = max(5, min(int(max_hits or 20), 40))
    if fn and "." in fn:
        cls, name = fn.split(".", 1)
        folder = {
            "Документ": "Documents",
            "Справочник": "Catalogs",
            "РегистрСведений": "InformationRegisters",
            "РегистрНакопления": "AccumulationRegisters",
        }.get(cls, "")
        try:
            from vector.catalog import assemble_module_text, get_chunks_by_source_path

            for role in ("Ext/ObjectModule.bsl", "Ext/ManagerModule.bsl"):
                path = f"{folder}/{name}/{role}"
                text = assemble_module_text(get_chunks_by_source_path(path))
                proc = ""
                for i, ln in enumerate(text.splitlines(), 1):
                    mp = re.match(r"\s*(?:Процедура|Функция)\s+([\wА-Яа-яЁё]+)", ln)
                    if mp:
                        proc = mp.group(1)
                    pat_hit = _FILL_LINE.search(ln) if based_on else _CHECK_LINE.search(ln)
                    if pat_hit and not ln.strip().startswith("//"):
                        hits.append({"path": path, "line": i, "procedure": proc, "snippet": ln.strip()[:220], "kind": "fill" if based_on else "check"})
                        if len(hits) >= lim:
                            break
        except Exception:
            pass
    msg_hits: list[dict[str, Any]] = []
    if msg:
        try:
            from vector.search import search_code_vectors

            words = [w for w in re.findall(r"[А-Яа-яЁё]{5,}", msg)][:4]
            sc = search_code_vectors(" ".join(words) or msg, limit=8, match_mode="hybrid")
            for m in sc.get("matches") or []:
                msg_hits.append({"path": m.get("path"), "snippet": (m.get("snippet") or "")[:300]})
        except Exception:
            pass
    summary = (
        (f"Объект {fn}: найдено {len(hits)} проверок проведения/записи. " if fn else "")
        + (f"По тексту сообщения «{msg}» — {len(msg_hits)} мест в коде." if msg else "")
    ).strip()
    return {
        "ok": bool(hits or msg_hits),
        "mode": "posting_checks",
        "request": req,
        "full_name": fn or None,
        "message_text": msg or None,
        "summary": summary or "Проверок не найдено.",
        "checks": hits,
        "message_sites": msg_hits,
        "prefer": (
            "Объясни, какие условия ставят Отказ = Истина (checks: процедура + строка) и где формируется "
            "сообщение (message_sites). Если ничего не найдено — попроси точный текст ошибки. "
            "Не выдумывай причину без snippet."
        ),
    }
