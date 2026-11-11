# -*- coding: utf-8 -*-
"""
Список объектов по реквизитам шапки (любая CF).

Класс вопросов: «в каких X свойство Y», «какие X не проведены»,
«X со статусом …», «X с пустым договором».

Имена полей / enum / full_name — только из structure ЭТОЙ базы.
Не hardcode Документ.ЗаказКлиента и не подмена агрегатом build_report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from tools.field_discovery import (
    FieldInfo,
    is_reference_type,
    match_role,
    parse_structure_fields,
    select_expr_for_field,
)


def _norm(s: str) -> str:
    return (s or "").lower().replace("ё", "е").strip()


# служебные / операторы фильтра — не имя объекта
_STOP = frozenset(
    {
        "в",
        "во",
        "на",
        "по",
        "из",
        "от",
        "до",
        "за",
        "с",
        "со",
        "у",
        "и",
        "или",
        "а",
        "но",
        "как",
        "что",
        "где",
        "для",
        "это",
        "этот",
        "эта",
        "эти",
        "тех",
        "всех",
        "все",
        "весь",
        "какие",
        "какой",
        "какая",
        "какое",
        "каких",
        "каком",
        "каким",
        "есть",
        "ли",
        "нужно",
        "можно",
        "покажи",
        "покажите",
        "выведи",
        "список",
        "перечень",
        "документ",
        "документа",
        "документе",
        "документы",
        "документов",
        "пожалуйста",
        "мне",
        "нас",
        "наш",
        "не",
        "без",
        "кроме",
    }
)

# «в каких / у каких» — какие записи, не «какие реквизиты»
_WHICH_RE = re.compile(r"(?:в|у|из)\s+каких\b", re.I)

# предикат (грамматика / платформа), не предметная область CF
_PRED_START_RE = re.compile(
    r"(?:"
    r"ожида\w+|"
    r"требу\w+|"
    r"не\s+\w+|"
    r"со\s+статус|"
    r"с\s+состоян|"
    r"с\s+пуст|"
    r"без\s+[а-яёa-z]{3,}|"
    r"где\s+(?:есть\s+)?|"
    r"помечен\w*|"
    r"просроч\w+|"
    r"непроведен"
    r")",
    re.I,
)

_LIST_CUE_RE = re.compile(
    r"(?:"
    r"в\s+каких|у\s+каких|из\s+каких|"
    r"какие\s+(?:из\s+)?|"
    r"список\s+|"
    r"перечень\s+|"
    r"у\s+которых|"
    r"в\s+которых|"
    r"со\s+статус|"
    r"с\s+статус|"
    r"с\s+пуст|"
    r"со\s+состоян"
    r")",
    re.I,
)

# агрегаты / регистры — не этот маршрут
_AGG_MARKERS = (
    "сколько",
    "сумм",
    "выручк",
    "оборот",
    "топ ",
    "рейтинг",
    "по контрагент",
    "в разрезе",
    "сравни",
    "excel",
    "эксель",
    "график",
)

_META_MARKERS = (
    "реквизит",
    "структур",
    "какие поля",
    "какие форм",
    "какие макет",
    "какие есть документ",
    "найди документ",
    "какие статус",  # значения перечисления, не список документов
    "значения статус",
)


def _speech(phrase: str) -> str:
    try:
        from tools.phrase_struct import speech_norm

        return speech_norm(phrase)
    except Exception:
        return _norm(phrase)


def _tokens(text: str) -> list[str]:
    raw = [w for w in re.findall(r"[a-zа-яё0-9]+", _norm(text)) if len(w) >= 2]
    out: list[str] = []
    seen: set[str] = set()
    for w in raw:
        extras = [w]
        # опечатка хвоста: «заказх» → «заказ»
        if len(w) >= 5 and w[-1] in "хьъ":
            extras.append(w[:-1])
        for e in extras:
            if e not in seen:
                seen.add(e)
                out.append(e)
    return out


def _stem(word: str) -> str:
    w = _norm(word)
    if len(w) <= 4:
        return w
    for suf in (
        "ается",
        "ется",
        "нного",
        "нная",
        "нный",
        "нными",
        "ами",
        "ями",
        "ах",
        "ях",
        "ов",
        "ев",
        "ии",
        "ий",
        "ой",
        "ая",
        "ое",
        "ые",
        "ых",
        "ым",
        "ом",
        "ем",
        "ей",
        "ию",
        "ья",
        "ье",
        "ии",
    ):
        if len(w) - len(suf) >= 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w[:7] if len(w) > 7 else w


def _camel_parts(name: str) -> list[str]:
    parts = re.findall(
        r"[A-ZА-ЯЁ][a-zа-яё]+|[A-ZА-ЯЁ]+(?![a-zа-яё])|[a-zа-яё]+",
        name or "",
    )
    return [_norm(p) for p in parts if len(p) >= 2]


def _has_predicate(speech: str) -> bool:
    """Предикат фильтра — грамматика/платформа, не словарь CF."""
    if not speech:
        return False
    if _PRED_START_RE.search(speech):
        return True
    if re.search(r"(пуст\w+\s+[а-яё]{3,}|не\s+заполн|пометк\w*\s+удален)", speech):
        return True
    return False


def is_attr_filter_question(phrase: str) -> bool:
    """
    Список записей по реквизиту шапки.

    Структура фразы («в каких X …», «какие X не …», «со статусом»).
    Без whitelist имён документов CF.
    """
    raw = (phrase or "").strip()
    if not raw or len(raw) < 8:
        return False
    try:
        from tools.phrase_struct import is_code_intent

        if is_code_intent(raw):
            return False
    except Exception:
        pass
    speech = _speech(raw)
    if not speech:
        return False
    if any(x in speech for x in _META_MARKERS):
        return False
    if re.search(r"какие\s+статус|статус\w*\s+бывают|значения\s+статус", speech):
        return False
    if re.search(
        r"(почему|логик|как\s+заполн|в\s+код|"
        r"по\s+каким\s+регистр|какие\s+движен|"
        r"в\s+каких\s+регистр|в\s+каких\s+модул)",
        speech,
    ):
        return False
    if any(x in speech for x in _AGG_MARKERS) and not _WHICH_RE.search(speech):
        return False
    if any(
        x in speech
        for x in (
            "задолжен",
            "дебитор",
            "кредитор",
            "взаиморасч",
            "остатк",
            "на склад",
        )
    ) and not _WHICH_RE.search(speech):
        return False

    which = bool(_WHICH_RE.search(speech))
    pred = _has_predicate(speech)
    list_cue = bool(_LIST_CUE_RE.search(speech))
    if which:
        return True
    if list_cue and pred:
        return True
    if pred and re.search(
        r"(со\s+статус|с\s+пуст|без\s+[а-яё]{3,}|"
        r"не\s+проведен|непроведен|просроч|пометк\w*\s+удален)",
        speech,
    ):
        try:
            from tools.phrase_struct import has_pascal_identifier

            if has_pascal_identifier(raw):
                return True
        except Exception:
            pass
        toks = [w for w in _tokens(speech) if w not in _STOP]
        if len(toks) >= 2:
            return True
    return False


def split_object_and_filter(phrase: str) -> dict[str, str]:
    """Объект | предикат — граница по грамматическому маркеру, не по словарю CF."""
    speech = _speech(phrase)
    obj = ""
    pred = ""
    m = re.search(r"(?:в|у|из)\s+каких\s+(.+)$", speech)
    if m:
        rest = (m.group(1) or "").strip()
        pm = _PRED_START_RE.search(rest)
        if pm and pm.start() > 0:
            obj = rest[: pm.start()].strip()
            pred = rest[pm.start() :].strip()
        else:
            obj = rest
            pred = rest
    elif re.search(r"^какие\s+", speech):
        rest = re.sub(r"^какие\s+", "", speech).strip()
        rest = re.sub(r"^есть\s+", "", rest).strip()
        pm = _PRED_START_RE.search(rest)
        if pm and pm.start() > 0:
            obj = rest[: pm.start()].strip()
            pred = rest[pm.start() :].strip()
        else:
            obj = rest
            pred = rest
    else:
        pm = _PRED_START_RE.search(speech)
        if pm and pm.start() > 0:
            obj = speech[: pm.start()].strip()
            pred = speech[pm.start() :].strip()
        else:
            obj = speech
            pred = speech
    obj_toks = [w for w in _tokens(obj) if w not in _STOP]
    pred_toks = [w for w in _tokens(pred) if w not in _STOP]
    return {
        "object_search": " ".join(obj_toks).strip() or speech,
        "filter_text": " ".join(pred_toks).strip(),
        "speech": speech,
    }


def _field_blob(fi: FieldInfo, extra: str = "") -> str:
    parts = _camel_parts(fi.name) + _camel_parts(fi.synonym)
    return _norm(
        " ".join(
            [
                fi.name,
                fi.synonym,
                fi.type_str,
                " ".join(parts),
                extra,
            ]
        )
    )


def _is_bool_field(fi: FieldInfo) -> bool:
    t = (fi.type_str or "").lower()
    if "булево" in t or "boolean" in t:
        return True
    n = _norm(fi.name)
    if n.startswith("это") or n.startswith("есть") or n.startswith("признак"):
        return True
    return False


def _is_date_field(fi: FieldInfo) -> bool:
    t = (fi.type_str or "").lower()
    if "дата" in t or "date" in t:
        return True
    n = _norm(fi.name)
    return n.startswith("дата") or n.endswith("дата") or "дат" in n[:6]


def _is_enum_field(fi: FieldInfo) -> bool:
    t = (fi.type_str or "").lower().replace("ё", "е")
    return "перечислен" in t or t.startswith("enum") or "enumref" in t.replace(" ", "")


def _enum_type_name(type_str: str) -> str:
    raw = (type_str or "").strip()
    if "." in raw:
        raw = raw.split(".")[-1]
    raw = re.sub(
        r"^(ПеречислениеСсылка|Перечисление|EnumRef)\.?",
        "",
        raw,
        flags=re.I,
    )
    return raw.strip()


def _negated(speech: str) -> bool:
    return bool(
        re.search(
            r"(?<![а-яё])(не|без|кроме)(?![а-яё])",
            speech,
        )
    )


@dataclass
class AttrFilter:
    field: FieldInfo
    op: str  # eq_true | eq_false | eq_enum | empty | not_empty | date_from_today | date_overdue
    score: int
    reason: str
    enum_type: str = ""
    enum_value: str = ""
    like_value: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "field": self.field.name,
            "synonym": self.field.synonym,
            "type": self.field.type_str,
            "op": self.op,
            "score": self.score,
            "reason": self.reason,
        }
        if self.enum_type:
            d["enum_type"] = self.enum_type
            d["enum_value"] = self.enum_value
        if self.like_value:
            d["like"] = self.like_value
        return d


def _enum_entries(structure: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(structure, dict):
        return []
    out: list[dict[str, Any]] = []
    for key in ("enum_fields", "status_fields"):
        items = structure.get(key)
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, dict):
                out.append(it)
    return out


def _score_token_in_blob(token: str, blob: str) -> int:
    t = _norm(token)
    st = _stem(t)
    if not st:
        return 0
    if t and t in blob:
        return 50 if len(t) >= 5 else 35
    if st and st in blob:
        return 40 if len(st) >= 4 else 20
    return 0


def match_filters(
    fields: list[FieldInfo],
    phrase: str,
    *,
    structure: dict[str, Any] | None = None,
) -> list[AttrFilter]:
    """Подобрать фильтры: токены фразы ↔ имя/синоним/enum ЭТОЙ CF."""
    speech = _speech(phrase)
    split = split_object_and_filter(phrase)
    prop_toks = _tokens(split["filter_text"] or speech)
    prop_toks = [w for w in prop_toks if w not in _STOP]
    if not prop_toks:
        prop_toks = [w for w in _tokens(speech) if w not in _STOP]
    header = [f for f in fields if not f.tabular_section]
    enums = _enum_entries(structure)
    enum_by_field = {
        str(e.get("name") or ""): e
        for e in enums
        if e.get("name")
    }
    neg = _negated(speech)
    expect = any(x in speech for x in ("ожида", "ждет", "ждэт", "будущ"))
    overdue = "просроч" in speech
    empty_ask = bool(
        re.search(r"(пуст|не\s+заполн|незаполн|без\s+)", speech)
    )
    posted_ask = bool(re.search(r"проведен", speech))
    del_ask = bool(re.search(r"пометк|удален", speech))

    scored: list[AttrFilter] = []

    for fi in header:
        extra = ""
        ev = enum_by_field.get(fi.name) or {}
        vals = ev.get("enum_values") or []
        if isinstance(vals, list):
            extra = " ".join(str(v) for v in vals)
        blob = _field_blob(fi, extra)
        score = 0
        hit_toks: list[str] = []
        for tok in prop_toks:
            s = _score_token_in_blob(tok, blob)
            if s:
                score += s
                hit_toks.append(tok)

        # enum value exact-ish
        best_eval = ""
        best_ev_score = 0
        if isinstance(vals, list):
            for v in vals:
                vs = str(v)
                vb = _norm(vs + " " + " ".join(_camel_parts(vs)))
                vs_score = 0
                for tok in prop_toks:
                    vs_score += _score_token_in_blob(tok, vb)
                if vs_score > best_ev_score:
                    best_ev_score = vs_score
                    best_eval = vs
            if best_ev_score >= 40:
                score = max(score, best_ev_score + 20)

        # тип поля из structure (не словарь предметной области)
        if expect and "ожида" in blob:
            score += 30
        elif expect and _is_date_field(fi):
            score += 12
        if overdue and _is_date_field(fi):
            score += 20
        if empty_ask:
            # «без договора» / «пустой контрагент»
            for tok in prop_toks:
                if tok in ("пуст", "пустой", "пустая", "пустое", "без"):
                    continue
                if _score_token_in_blob(tok, blob) >= 35:
                    score += 15
        if posted_ask and match_role([fi], "posted"):
            score += 40
        if del_ask and match_role([fi], "deletion_mark"):
            score += 40

        if score < 35:
            continue

        op = "not_empty"
        reason = f"токены {hit_toks} ↔ {fi.name}"
        enum_type = _enum_type_name(str(ev.get("enum_name") or fi.type_str))
        enum_value = best_eval

        if posted_ask and (
            _norm(fi.name) in ("проведен",) or match_role([fi], "posted")
        ):
            op = "eq_false" if neg else "eq_true"
            reason = "проведённость документа"
        elif del_ask and match_role([fi], "deletion_mark"):
            op = "eq_true" if not re.search(r"без\s+пометк|не\s+удален", speech) else "eq_false"
            reason = "пометка удаления"
        elif empty_ask and score >= 50:
            op = "empty" if (neg or empty_ask) else "not_empty"
            if "без" in speech or "пуст" in speech or "не заполн" in speech:
                op = "empty"
            reason = "пустое/не заполнено"
        elif _is_bool_field(fi):
            op = "eq_false" if neg else "eq_true"
            reason = "булев реквизит"
        elif best_eval and best_ev_score >= 40:
            op = "eq_enum"
            reason = f"значение перечисления {best_eval}"
        elif _is_date_field(fi) and overdue:
            op = "date_overdue"
            reason = "дата в прошлом (просрочено)"
        elif _is_date_field(fi) and expect:
            op = "date_from_today"
            reason = "дата ≥ сегодня (ожидается)"
        elif _is_enum_field(fi) and best_eval:
            op = "eq_enum"
            reason = f"перечисление {best_eval}"
        elif is_reference_type(fi) and empty_ask:
            op = "empty"
            reason = "ссылка не заполнена"
        else:
            # текстовый/прочий — не пусто + поле в SELECT
            op = "not_empty"
            reason = "реквизит упомянут в фразе"

        scored.append(
            AttrFilter(
                field=fi,
                op=op,
                score=score,
                reason=reason,
                enum_type=enum_type if op == "eq_enum" else "",
                enum_value=enum_value if op == "eq_enum" else "",
            )
        )

    scored.sort(key=lambda x: (-x.score, len(x.field.name)))
    # лучшие, без дублей имени; если scores близки — до 3 фильтров
    out: list[AttrFilter] = []
    used: set[str] = set()
    if not scored:
        return []
    top = scored[0].score
    for af in scored:
        if af.field.name in used:
            continue
        if af.score < max(35, int(top * 0.55)) and len(out) >= 1:
            continue
        used.add(af.field.name)
        out.append(af)
        if len(out) >= 3:
            break
    return out


def _where_clause(af: AttrFilter, *, today: datetime | None = None) -> str:
    n = af.field.name
    now = today or datetime.now()
    dv = f"ДАТАВРЕМЯ({now.year},{now.month},{now.day},0,0,0)"
    if af.op == "eq_true":
        return f"Т.{n} = ИСТИНА"
    if af.op == "eq_false":
        return f"Т.{n} = ЛОЖЬ"
    if af.op == "eq_enum" and af.enum_type and af.enum_value:
        return f"Т.{n} = ЗНАЧЕНИЕ(Перечисление.{af.enum_type}.{af.enum_value})"
    if af.op == "empty":
        if _is_date_field(af.field):
            return f"(Т.{n} = ДАТАВРЕМЯ(1,1,1) ИЛИ Т.{n} ЕСТЬ NULL)"
        if is_reference_type(af.field) or _is_enum_field(af.field):
            return f"(Т.{n} = НЕОПРЕДЕЛЕНО ИЛИ Т.{n} ЕСТЬ NULL)"
        return f'(Т.{n} = "" ИЛИ Т.{n} ЕСТЬ NULL ИЛИ Т.{n} = НЕОПРЕДЕЛЕНО)'
    if af.op == "not_empty":
        if _is_date_field(af.field):
            return f"Т.{n} > ДАТАВРЕМЯ(1,1,1)"
        if _is_bool_field(af.field):
            return f"Т.{n} = ИСТИНА"
        return f"Т.{n} <> НЕОПРЕДЕЛЕНО И Т.{n} ЕСТЬ НЕ NULL"
    if af.op == "date_from_today":
        return f"Т.{n} >= {dv} И Т.{n} > ДАТАВРЕМЯ(1,1,1)"
    if af.op == "date_overdue":
        return f"Т.{n} > ДАТАВРЕМЯ(1,1,1) И Т.{n} < {dv}"
    if af.op == "like" and af.like_value:
        esc = (af.like_value or "").replace('"', '""')
        return f'Т.{n} ПОДОБНО "%{esc}%"'
    return f"Т.{n} <> НЕОПРЕДЕЛЕНО"


def build_attr_query(
    full_name: str,
    fields: list[FieldInfo],
    filters: list[AttrFilter],
    *,
    structure: dict[str, Any] | None = None,
    limit: int = 50,
    only_posted: bool = False,
    today: datetime | None = None,
) -> dict[str, Any]:
    """Собрать ВЫБРАТЬ … ГДЕ по сматченным реквизитам."""
    from tools.query_builder import normalize_metadata_name

    try:
        name = normalize_metadata_name(full_name)
    except ValueError as e:
        return {"error": str(e)}

    header = [f for f in fields if not f.tabular_section]
    date_f = match_role(header, "date")
    num_f = match_role(header, "number") or match_role(header, "code")
    post_f = match_role(header, "posted")
    del_f = match_role(header, "deletion_mark")
    cp_f = match_role(header, "counterparty")
    amt_f = match_role(header, "amount")

    pick: list[FieldInfo] = []
    seen: set[str] = set()

    def add(fi: FieldInfo | None) -> None:
        if not fi or fi.name in seen:
            return
        seen.add(fi.name)
        pick.append(fi)

    add(num_f)
    add(date_f)
    add(post_f)
    add(cp_f)
    add(amt_f)
    for af in filters:
        add(af.field)
    # ещё пару шапочных
    for fi in header:
        if len(pick) >= 10:
            break
        add(fi)

    if not pick:
        pick = header[:8] or [FieldInfo(name="Ссылка")]

    lim = max(1, min(int(limit or 50), 200))
    select_parts = [select_expr_for_field(f) for f in pick]
    where: list[str] = []
    for af in filters:
        where.append(_where_clause(af, today=today))
    if only_posted and post_f and post_f.name not in {
        af.field.name for af in filters if af.op in ("eq_true", "eq_false")
    }:
        where.append(f"Т.{post_f.name} = ИСТИНА")
    # не удалённые — если не спросили про пометку
    if del_f and not any(
        af.field.name == del_f.name for af in filters
    ):
        where.append(f"Т.{del_f.name} = ЛОЖЬ")

    q = f"ВЫБРАТЬ ПЕРВЫЕ {lim} " + ", ".join(select_parts) + f" ИЗ {name} КАК Т"
    if where:
        q += " ГДЕ " + " И ".join(where)
    if date_f:
        q += f" УПОРЯДОЧИТЬ ПО Т.{date_f.name} УБЫВ"
    return {
        "query": q,
        "object": name,
        "filters": [af.to_dict() for af in filters],
        "select_fields": [f.name for f in pick],
    }


def run_attr_query(
    request: str,
    *,
    full_name: str = "",
    limit: int = 50,
    resolve_fn: Callable[[str], dict[str, Any]] | None = None,
    structure_fn: Callable[[str], dict[str, Any]] | None = None,
    execute_fn: Callable[..., dict[str, Any]] | None = None,
    today: datetime | None = None,
) -> dict[str, Any]:
    """
    Полный pipeline: resolve → structure → match → ВЫБРАТЬ → rows.
    Callbacks — текущая база (не FS, не другая CF).
    """
    raw = (request or "").strip()
    split = split_object_and_filter(raw)
    fn = (full_name or "").strip()
    search = split["object_search"] or raw

    if not fn:
        # каталог выгрузки: только классы с данными (документ/справочник/регистр)
        try:
            from tools.object_catalog import resolve_data_object

            rd = resolve_data_object(search)
            if rd.get("full_name"):
                fn = str(rd["full_name"])
                alt = [c["full_name"] for c in rd.get("candidates") or [] if c["full_name"] != fn][:3]
            else:
                alt = []
        except Exception:
            alt = []
    else:
        alt = []

    if not fn and resolve_fn:
        resolved = resolve_fn(search) or {}
        if resolved.get("need_clarification"):
            return resolved
        fn = str(
            resolved.get("full_name")
            or resolved.get("top")
            or ""
        ).strip()
        if not fn:
            opts = resolved.get("options") or resolved.get("items")
            if opts:
                return {
                    "ok": False,
                    "need_clarification": True,
                    "about": "object",
                    "question": (
                        "Несколько объектов по фразе. Какой full_name выбрать?"
                    ),
                    "options": opts,
                    "search_query": search,
                }
            return {
                "ok": False,
                "error": f"Не найден объект по «{search}».",
                "hint": "resolve_object / find_metadata — уточните имя.",
                "search_query": search,
            }

    if not fn:
        return {
            "ok": False,
            "need_clarification": True,
            "about": "object",
            "missing": ["full_name"],
            "question": "Укажите объект (как в конфигурации) или уточните фразу.",
            "search_query": search,
        }

    structure: dict[str, Any] = {}
    if structure_fn:
        st = structure_fn(fn) or {}
        if st.get("error") and not st.get("attributes"):
            return st
        structure = st
        fn = str(st.get("full_name") or fn)

    fields = parse_structure_fields(structure)
    if not fields:
        return {
            "ok": False,
            "error": "Нет реквизитов в structure",
            "full_name": fn,
            "hint": "get_object_structure(full_name)",
        }

    filters = match_filters(fields, raw, structure=structure)
    if not filters:
        # показать кандидатов полей — не уходить в продажи
        cands = []
        for fi in fields:
            if fi.tabular_section:
                continue
            blob = _field_blob(fi)
            if any(_score_token_in_blob(t, blob) for t in _tokens(split["filter_text"] or raw)):
                cands.append(
                    {
                        "name": fi.name,
                        "synonym": fi.synonym,
                        "type": fi.type_str,
                    }
                )
            if len(cands) >= 12:
                break
        header_sample = [
            {"name": f.name, "synonym": f.synonym, "type": f.type_str}
            for f in fields
            if not f.tabular_section
        ][:25]
        return {
            "ok": False,
            "need_clarification": True,
            "about": "field",
            "full_name": fn,
            "question": (
                "Не удалось однозначно сопоставить фразу с реквизитом. "
                "Какое поле фильтровать? Ниже кандидаты из structure этой базы."
            ),
            "candidate_fields": cands or header_sample[:12],
            "header_fields_sample": header_sample,
            "header_fields_total": len([f for f in fields if not f.tabular_section]),
            "not_all_shown": "Показаны не все реквизиты шапки (sample) — полный список: list_object_attributes(full_name=…, offset=…).",
            "filter_text": split["filter_text"],
            "prefer": (
                "Это вопрос про ДАННЫЕ по реквизитам, не агрегат продаж. "
                "Покажи candidate_fields и спроси поле. "
                "НЕ вызывай compare_periods / analyst_report."
            ),
        }

    built = build_attr_query(
        fn, fields, filters, structure=structure, limit=limit, today=today
    )
    if built.get("error"):
        return {"ok": False, **built}

    rows: list[Any] = []
    exec_out: dict[str, Any] = {}
    if execute_fn:
        exec_out = execute_fn(built["query"], limit=limit) or {}
        if exec_out.get("error"):
            # enum ЗНАЧЕНИЕ могло не совпасть с именем — второй шанс: not_empty
            retry_filters = [
                AttrFilter(
                    field=af.field,
                    op="not_empty" if af.op == "eq_enum" else af.op,
                    score=af.score,
                    reason=af.reason + " (fallback без ЗНАЧЕНИЕ)",
                )
                for af in filters
            ]
            if any(af.op == "eq_enum" for af in filters):
                built2 = build_attr_query(
                    fn,
                    fields,
                    retry_filters,
                    structure=structure,
                    limit=limit,
                    today=today,
                )
                exec_out2 = execute_fn(built2.get("query") or "", limit=limit) or {}
                if not exec_out2.get("error"):
                    built = built2
                    exec_out = exec_out2
                    exec_out["enum_fallback"] = True
            if exec_out.get("error"):
                return {
                    "ok": False,
                    "error": exec_out.get("error"),
                    "query": built.get("query"),
                    "full_name": built.get("object") or fn,
                    "filters": built.get("filters"),
                    "hint": "Имена полей из structure; проверьте enum_value.",
                }
        rows = exec_out.get("rows") or exec_out.get("data") or []

    n_rows = len(rows) if isinstance(rows, list) else 0
    filt_human = ", ".join(
        f"{af.field.name} ({af.reason})" for af in filters
    )
    result: dict[str, Any] = {
        "ok": True,
        "report_mode": "attr_filter",
        "full_name": built.get("object") or fn,
        "rows": rows,
        "count": n_rows,
        "query": built.get("query"),
        "filters": built.get("filters"),
        "select_fields": built.get("select_fields"),
        "object_search": search,
        "filter_text": split["filter_text"],
        "manager_summary": {
            "message": (
                f"Список {built.get('object') or fn} по реквизитам: {filt_human}. "
                f"Строк: {n_rows}."
                + (
                    " Совпадений нет — в этой базе у объекта другие имена полей "
                    "или нет документов с таким значением."
                    if n_rows == 0
                    else ""
                )
            )
        },
        "prefer": (
            "Ответ по ДАННЫМ документа: table_preview/rows. "
            "Назови реквизиты из filters (реальные имена CF). "
            "Не подменяй агрегированным отчётом продаж. "
            "0 строк — честно + candidate/другой фильтр."
        ),
    }
    if exec_out.get("has_more") is not None:
        result["has_more"] = exec_out.get("has_more")
        result["next_offset"] = exec_out.get("next_offset")
    if alt:
        result["also_possible"] = alt
    return result
