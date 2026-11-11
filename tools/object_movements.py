# -*- coding: utf-8 -*-
"""
Универсальный разбор: в какие регистры пишет объект (документ и др.).

Любая конфигурация / любой full_name.
Источники:
  1) Qdrant — модули объекта (ObjectModule / ManagerModule)
  2) выгрузка CF (если есть) — RegisterRecords из XML документа (полный список)
  3) разбор ОбработкаПроведения: Отразить*(…, Движения) + Движения.X + Регистры*.X
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tools.code_search import find_modules, parse_nl_query
from vector.catalog import (
    assemble_module_text,
    code_collection_ready,
    get_chunks_by_source_path,
    read_module,
    strip_context_header,
)

# Движения.ИмяРегистра.Добавить / Записать / Очистить
_DVIZH = re.compile(
    r"Движения\s*\.\s*([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)\s*\.\s*"
    r"(Добавить|Записать|Очистить|Удалить|Load|Add)\s*\(",
    re.I,
)
# Типовой паттерн: Массив.Добавить(Движения.ИмяРегистра)
_DVIZH_AS_ARG = re.compile(
    r"(?:Добавить|Add)\s*\(\s*Движения\s*\.\s*([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)\s*\)",
    re.I,
)
# Любое обращение Движения.ИмяРегистра.Свойство (кроме служебных)
_DVIZH_ANY = re.compile(
    r"Движения\s*\.\s*([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)\b",
    re.I,
)
# РегистрыНакопления.X / РегистрыСведений.X в тексте
_REG_META = re.compile(
    r"\b(Регистры(?:Накопления|Сведений|Бухгалтерии|Расчета)|"
    r"AccumulationRegisters|InformationRegisters|AccountingRegisters)\s*\.\s*"
    r"([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)",
    re.I,
)
# Паттерн: XxxСервер.ОтразитьИмя(ДополнительныеСвойства, Движения, …)
_OTRAZIT_DVIZH = re.compile(
    r"([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)\s*\.\s*"
    r"Отразить([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)\s*\(\s*"
    r"[^)]*?\bДвижения\b",
    re.I | re.S,
)
# РегистрыСведений.X.Записать… / Отразить… (прямая запись без коллекции Движения)
_REG_DIRECT_WRITE = re.compile(
    r"\b(Регистры(?:Накопления|Сведений|Бухгалтерии|Расчета))\s*\.\s*"
    r"([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)\s*\.\s*"
    r"([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)\s*\(",
    re.I,
)
# RegisterRecords в XML выгрузки
_REG_RECORDS_XML = re.compile(
    r"<(?:xr:)?Item[^>]*>\s*"
    r"(AccumulationRegister|InformationRegister|AccountingRegister|CalculationRegister)"
    r"\.([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)\s*"
    r"</(?:xr:)?Item>",
    re.I,
)
_KIND_RU = {
    "accumulationregister": "РегистрНакопления",
    "informationregister": "РегистрСведений",
    "accountingregister": "РегистрБухгалтерии",
    "calculationregister": "РегистрРасчета",
}
# Служебные «псевдо-регистры» / свойства набора / поля строк, не имена регистров
_DVIZH_SKIP = {
    "этоновый",
    "дополнительныесвойства",
    "записать",
    "очистить",
    "добавить",
    "прочитать",
    "количество",
    "блокироватьдляизменения",
    "обменданными",
    "передзаписью",
    "послезаписи",
    "отбор",
    "filter",
    "вставить",
    "удалить",
    "найти",
    "получить",
    "период",
    "регистратор",
    "активность",
    "виддвижения",
    "номерстроки",
    "строка",
    "номенклатура",
    "характеристика",
    "серия",
    "склад",
    "сумма",
    "суммандс",
    "ставкандс",
    "кодстроки",
    "организация",
    "подразделение",
    "партнер",
    "контрагент",
    "договор",
    "валюта",
    "комментарий",
}

# Действия, которые считаем «записью» (не простое упоминание)
_WRITE_ACTIONS = {
    "добавить",
    "записать",
    "очистить",
    "удалить",
    "load",
    "add",
    "добавить(набор)",
}
_POSTING = re.compile(
    r"(Процедура|Функция|Procedure|Function)\s+"
    r"(ОбработкаПроведения|ОбработкаУдаленияПроведения|"
    r"BeforeWrite|OnWrite|ОбработкаЗаполнения)\b",
    re.I,
)
_NARRATIVE_BAN = re.compile(
    r"(подождите|сейчас\s+(изуч|анализ|смотр|читаю)|"
    r"я\s+(анализ|изуч|смотр|читаю|начал)|анализирую\s+код|"
    r"изучаю\s+содержимое|"
    r"мне\s+нужно|"
    r"мне\s+прочитать|"
    r"могу\s+ли\s+я|"
    r"продолжить\s*\?|"
    r"мне\s+продолжить|"
    r"хотите\s+чтобы|"
    r"разрешить\s+мне|"
    r"пока\s+я\s+вижу|"
    r"нашел\s+процедур|"
    r"нашёл\s+процедур|"
    r"теперь\s+мне\s+нужно|"
    r"хотите\s+посмотреть|"
    r"хотите\s+продолж|"
    r"хотите\s+чтобы\s+я|"
    r"хотите,?\s+чтобы\s+я|"
    r"если\s+вам\s+нужно|"
    r"если\s+нужно\s+увидеть|"
    r"могу\s+прочитать|"
    r"я\s+могу\s+прочитать|"
    r"я\s+нашел\s+этот\s+модуль|"
    r"я\s+нашёл\s+этот\s+модуль|"
    r"основные\s+запросы,?\s+скорее\s+всего|"
    r"нужно\s+исследовать\s+модуль)",
    re.I,
)


def is_posting_matrix_question(phrase: str) -> bool:
    """«Где какой документ проводится по каким регистрам» — обзор по всей CF, без одного объекта."""
    pl = (phrase or "").lower().replace("ё", "е")
    if not is_movements_or_posting_question(phrase):
        return False
    return bool(
        re.search(r"\b(как(ой|ие|ая)|все|всех|каждый|кажд\w+)\s+(документ|регистр)", pl)
        or re.search(r"документ\w*\s+(и|×|x)\s+регистр", pl)
        or re.search(r"(матриц|сводн|таблиц)\w*\s+(документ|регистр|проведен|движен)", pl)
        or re.search(r"(какие|кто)\s+(документ\w*\s+)?(пиш|дела|формир)\w*\s+.*регистр", pl)
    )


def posting_matrix(
    request: str = "",
    *,
    register_hint: str = "",
    document_hint: str = "",
    limit: int = 5000,
) -> dict[str, Any]:
    """
    Отчёт «Документ → Регистр» по RegisterRecords всех документов выгрузки этой CF.
    register_hint / document_hint — подстрока имени (фильтр), можно из фразы.
    """
    root = _code_dir()
    docs_dir = root / "Documents" if root else None
    if not docs_dir or not docs_dir.is_dir():
        return {
            "ok": False,
            "error": "Нет выгрузки метаданных (code_dir/Documents) для сводки по всем документам.",
            "hint": "Для одного документа: analyze_object_movements(request=…).",
        }
    # фильтр из фразы: PascalCase-имя регистра/документа («по регистру ТоварыНаСкладах»)
    if not register_hint and not document_hint:
        for tok in re.findall(r"\b[А-ЯЁA-Z][а-яёa-z0-9]+(?:[А-ЯЁA-Z][а-яёa-z0-9]+)+\b", request or ""):
            ctx = (request or "")[: (request or "").find(tok)].lower()
            if "регистр" in ctx[-30:]:
                register_hint = tok
            else:
                document_hint = tok
    rh = register_hint.lower().replace("ё", "е")
    dh = document_hint.lower().replace("ё", "е")
    rows: list[dict[str, Any]] = []
    docs_without: list[str] = []
    for p in sorted(docs_dir.glob("*.xml")):
        doc = p.stem
        if dh and dh not in doc.lower().replace("ё", "е"):
            continue
        recs = _load_register_records(doc, f"Документ.{doc}")
        if not recs:
            docs_without.append(doc)
            continue
        for r in recs:
            if rh and rh not in r["name"].lower().replace("ё", "е"):
                continue
            rows.append(
                {"Документ": f"Документ.{doc}", "ВидРегистра": r["kind"], "Регистр": r["name"]}
            )
    by_reg: dict[str, int] = {}
    for r in rows:
        k = f"{r['ВидРегистра']}.{r['Регистр']}"
        by_reg[k] = by_reg.get(k, 0) + 1
    summary = [
        {"Регистр": k, "Документов": v}
        for k, v in sorted(by_reg.items(), key=lambda x: -x[1])
    ]
    return {
        "ok": True,
        "source": "metadata RegisterRecords (выгрузка CF)",
        "columns": ["Документ", "ВидРегистра", "Регистр"],
        "rows": rows[:limit],
        "count": len(rows),
        "documents": len({r["Документ"] for r in rows}),
        "summary_by_register": summary,
        "documents_without_movements": docs_without[:50] if not rh else [],
        "documents_without_movements_total": len(docs_without) if not rh else 0,
        "filters": {"register": register_hint, "document": document_hint},
        "note": (
            "Это «может писать» по метаданным (RegisterRecords), не факт движений в данных. "
            "Детали кода проведения одного документа — analyze_object_movements."
        ),
        "prefer": (
            "Покажи summary_by_register (сколько документов пишет в каждый регистр) "
            "+ rows (Документ → Регистр). Если Excel — path."
        ),
    }


def is_movements_or_posting_question(phrase: str) -> bool:
    """Универсально: регистры проведения / движения / что пишет документ."""
    pl = (phrase or "").lower().replace("ё", "е")
    if not pl:
        return False
    # managed locks / внедрение блокировок — не movements (см. recommend_locks)
    try:
        from tools.recommend_locks import is_lock_migration_question

        if is_lock_migration_question(phrase):
            return False
    except Exception:
        pass
    if re.search(r"managed\s*locks?|управляем\w*\s+блокир", pl) and re.search(
        r"ставить|рекоменд|внедр|куда|где\s+ставить", pl
    ):
        return False
    markers = (
        "по каким регистр",
        "какие регистр",
        "в какие регистр",
        "какие движения",
        "формирует движения",
        "пишет в регистр",
        "запись в регистр",
        "проведение документ",
        "при проведении",
        "обработкапроведения",
        "обработка проведения",
        "движения.",
        "register records",
        "posting",
        "куда пиш",
        "что пишет в",
        "движения по регистр",
        "регистры проведен",
        # короткие follow-up в чате после обсуждения документа
        "все регистр",
        "весь список регистр",
        "полный список регистр",
        "полный перечень регистр",
        "выведи все регистр",
        "выведи регистр",
        "покажи все регистр",
        "покажи регистр",
        "перечисли регистр",
        "список регистр",
        "список всех регистр",
        "регистры документа",
        "регистры по документ",
    )
    if any(m in pl for m in markers):
        return True
    if re.search(r"регистр\w*", pl) and re.search(
        r"(провед|движен|документ|пиш|запис|формир|все|полн|список|вывед|показ|перечисл)",
        pl,
    ):
        return True
    if re.search(r"движен\w*", pl) and re.search(
        r"(документ|регистр|провед|формир|добав|все|список)", pl
    ):
        return True
    # одно слово «регистры» / «движения» в follow-up
    if pl.strip() in ("регистры", "все регистры", "движения", "все движения"):
        return True
    return False


def looks_like_fake_progress(text: str) -> bool:
    """Ответ «подождите / могу ли прочитать?» без фактов — запрещён."""
    t = (text or "").strip()
    if not t:
        return False
    # мета без tool: «Понял. Буду отвечать сразу по фактам, используя инструменты…»
    if len(t) < 500 and re.search(
        r"(буду\s+отвечать|используя\s+инструмент|без\s+лишних\s+уточнен|"
        r"сразу\s+по\s+фактам.*инструмент|понял\.?\s+буду)",
        t,
        re.I,
    ):
        if not re.search(
            r"(Роль\.|roles_count|complete=|Регистр|ВЫБРАТЬ|```|rights_summary|"
            r"roles_total|full_name\s*=)",
            t,
        ):
            return True
    # вопросы «разрешения» вместо tool
    if re.search(
        r"(мне\s+(нужно|прочитать|продолжить)|могу\s+ли|продолжить\s*\?|"
        r"хотите,?\s+чтобы\s+я|хотите\s+(посмотреть|продолж)|разрешить|"
        r"я\s+могу\s+прочитать|если\s+вам\s+нужно|нашёл\s+этот\s+модуль|"
        r"нашел\s+этот\s+модуль|проанализировал\s+его\s+метод)",
        t,
        re.I,
    ) and re.search(
        r"(прочитать|код|процедур|модул|макет|запрос|структур|tool|вызов|"
        r"objectmodule|записатьнабор|инициализировать)",
        t,
        re.I,
    ):
        return True
    # «запросов не обнаружено… хотите?» без списка ТекстЗапроса* — fake progress
    if re.search(r"запрос\w*\s+не\s+обнаруж|явных\s+текст\w*\s+запрос", t, re.I) and re.search(
        r"хотите|могу\s+прочитать|модул[ье]\s+объект", t, re.I
    ):
        if not re.search(r"ТекстЗапроса\w*|query_functions|CommonModules/", t):
            return True
    if _NARRATIVE_BAN.search(t) and len(t) < 1600:
        # нет признаков готового разбора (факты/таблицы/код)
        if not re.search(
            r"(Регистр|Движения\.|ОбработкаПроведения|не формирует|"
            r"ПФ_MXL_|```|ВЫБРАТЬ|Процедура\s+\w+|Функция\s+\w+|"
            r"ТекстЗапроса\w*|CommonModules/)",
            t,
        ):
            return True
        if t.count("\n") < 3 and "Движения." not in t and "```" not in t:
            return True
    return False


def is_empty_code_paste(phrase: str) -> bool:
    """Пользователь вставил пустой fenced-блок кода (``` … ``` без тела)."""
    t = (phrase or "").strip()
    if not t:
        return False
    # один/несколько fence без содержимого
    if re.fullmatch(r"(?:```[\w]*\s*```\s*)+", t, re.I):
        return True
    # fence + только пробелы/пустые строки (не короткий реальный код)
    if "```" not in t:
        return False
    body = re.sub(r"```[\w]*", "", t)
    body = body.replace("`", "").strip()
    if not body:
        return True
    # нет букв/цифр — «пустой» визуально
    if not re.search(r"[A-Za-zА-Яа-яЁё0-9_]", body):
        return True
    return False


def looks_like_false_fix_claim(text: str) -> bool:
    """
    Агент притворяется, что код уже исправлен / разработчик уже оптимизировал.
    READ-ONLY: CF не менялась агентом; «как есть» ≠ «вы исправили».
    """
    t = (text or "").strip()
    if not t:
        return False
    tl = t.lower().replace("ё", "е")
    # явная пометка рекомендации — ок
    if re.search(
        r"рекомендац\w*.{0,40}не\s+примен|"
        r"пример\s*\(не\s+примен|"
        r"не\s+применено|"
        r"cf\s+не\s+меня|"
        r"черновик|"
        r"если\s+будете\s+менять|"
        r"как\s+можно\s+сделать",
        tl,
        re.I,
    ):
        # но «Исправленный вариант» в заголовке без «не применено» рядом — плохо
        if not re.search(r"исправленн\w*\s+вариант|я\s+исправил|мы\s+исправили", tl):
            return False

    claims = (
        r"я\s+исправил",
        r"мы\s+исправили",
        r"уже\s+исправл",
        r"код\s+уже\s+оптимизир",
        r"проблема\s+решена",
        r"разработчик\s+\*?\*?уже",
        r"разработчик\s+уже\s+(вынес|перенес|оптимиз|исправ)",
        r"уже\s+вынес\w*\s+.{0,30}за\s+предел",
        r"уже\s+вынесен[ао]?\s+за\s+предел",
        r"уже\s+оптимизирован\s+по\s+этому",
        r"исправленн\w*\s+вариант",
        r"после\s+исправления",
        r"вот\s+как\s+это\s+должно\s+выглядеть.{0,40}исправлен",
        r"исправление\s+оптимизации",
    )
    if any(re.search(p, tl, re.I) for p in claims):
        return True
    return False


# Слова-шум в вопросах про регистры/проведение (не имена объектов)
_HINT_STOP = {
    "документ",
    "документа",
    "документы",
    "документов",
    "справочник",
    "справочника",
    "регистр",
    "регистра",
    "регистры",
    "регистрам",
    "регистров",
    "регистрах",
    "проведение",
    "проведения",
    "проводится",
    "проводят",
    "проводить",
    "проведен",
    "проведенных",
    "движения",
    "движений",
    "движение",
    "данные",
    "какие",
    "каким",
    "какой",
    "какая",
    "каких",
    "какого",
    "какую",
    "пишется",
    "пишутся",
    "пишет",
    "писать",
    "каждом",
    "нужно",
    "скажи",
    "покажи",
    "найди",
    "подскажи",
    "пожалуйста",
    "делает",
    "делают",
    "формирует",
    "формируют",
    "список",
    "полный",
    "всех",
    "все",
    "этот",
    "этого",
    "объект",
    "объекта",
    "модуль",
    "модуля",
    "код",
    "коде",
    "базе",
    "конфигурации",
    "метаданным",
    "метаданные",
}


def _is_stop_hint(word: str) -> bool:
    w = (word or "").lower().replace("ё", "е")
    if not w or len(w) < 3:
        return True
    if w in _HINT_STOP:
        return True
    if w.startswith("регистр"):
        return True
    if w.startswith("провед"):
        return True
    if w.startswith("движен"):
        return True
    if w.startswith("как"):
        return True
    return False


def _strip_movements_noise(phrase: str) -> str:
    """Убрать «по каким регистрам проводится» → оставить имя объекта."""
    p = (phrase or "").strip()
    patterns = (
        r"по\s+каким\s+регистрам\s+проводится\s+",
        r"по\s+каким\s+регистрам\s+",
        r"в\s+какие\s+регистры\s+(пиш|прово)",
        r"какие\s+регистры\s+",
        r"какие\s+движения\s+",
        r"при\s+проведении\s+",
        r"обработка\s*проведения\s+",
        r"full_name\s*=\s*",
        r"документ[аыу]?\s+",
    )
    out = p
    for pat in patterns:
        out = re.sub(pat, " ", out, flags=re.I)
    return re.sub(r"\s+", " ", out).strip(" ?!.,;:")


def _path_has_obj(path: str, obj: str) -> bool:
    """Case-insensitive: стем фразы ∈ Documents/<ObjectName>/..."""
    if not obj or not path:
        return False
    return obj.lower().replace("ё", "е") in path.lower().replace("ё", "е")


def _resolve_object_from_phrase(phrase: str) -> str:
    """
    «фраза пользователя» → object_name из Qdrant (любая CF).

    Универсально: token affinity + каталог object_name (name_match).
    БЕЗ доменных whitelist (реализац/корректир/ндс/…).
    """
    cleaned = _strip_movements_noise(phrase)
    if not cleaned or len(cleaned) < 3:
        return ""

    # 0) универсальный resolve по каталогу Qdrant
    try:
        from tools.name_match import (
            is_pascal_identifier,
            resolve_object_name,
        )

        got = resolve_object_name(cleaned)
        if got:
            return got
        # уже точное PascalCase имя
        m0 = re.search(r"\b([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9]{5,})\b", cleaned)
        if m0 and is_pascal_identifier(m0.group(1)) and not _is_stop_hint(m0.group(1)):
            cand = m0.group(1)
            try:
                chk = find_modules(cand, kind="", limit=3)
                if chk.get("modules"):
                    return cand
            except Exception:
                return cand
    except Exception:
        pass

    # fallback: find_modules + token_affinity (если name_match не сработал)
    try:
        from tools.name_match import (
            camel_from_tokens,
            phrase_tokens,
            token_affinity,
        )
    except Exception:
        return ""

    tokens = phrase_tokens(cleaned)
    if not tokens:
        return ""
    queries: list[str] = [cleaned, camel_from_tokens(tokens)]
    if tokens:
        queries.append(tokens[0][:1].upper() + tokens[0][1:])
    modules: list[dict[str, Any]] = []
    seen_p: set[str] = set()
    for q in queries:
        if not q or len(q) < 3:
            continue
        try:
            found = find_modules(q, kind="", limit=12)
        except Exception:
            found = {}
        for mod in found.get("modules") or []:
            p = (mod.get("path") or "").replace("\\", "/")
            if not p or p in seen_p:
                continue
            seen_p.add(p)
            modules.append(mod)

    best = ""
    best_sc = -1.0
    for mod in modules:
        path = (mod.get("path") or "").replace("\\", "/")
        on = (mod.get("object_name") or "").strip()
        if not on:
            parts = path.split("/")
            if len(parts) >= 2 and parts[0] in (
                "Documents",
                "Catalogs",
                "DataProcessors",
                "Reports",
            ):
                on = parts[1]
        if not on or _is_stop_hint(on):
            continue
        # только универсальный affinity токенов ↔ имя
        sc = token_affinity(tokens, on) * 1000.0
        low = path.lower()
        if "objectmodule" in low.replace("/", "") and "/forms/" not in low:
            sc += 5_000
        if path.startswith("Documents/"):
            sc += 2_000
        if path.startswith("DataProcessors/") or path.startswith("Reports/"):
            sc -= 3_000
        if sc > best_sc:
            best_sc = sc
            best = on
    return best


def _extract_object_hint(request: str, full_name: str) -> str:
    fn = (full_name or "").strip()
    if fn:
        # Документ.Имя → Имя; отбросить мусор «Документ.реализация»
        bare = fn.split(".")[-1] if "." in fn else fn
        # если bare — разговорное слово, дорезолвить
        if bare and (
            re.search(r"[A-ZА-ЯЁ].*[a-zа-яё].*[A-ZА-ЯЁ]", bare)
            or len(bare) >= 12
            or re.search(r"[a-zа-яё][A-ZА-ЯЁ]", bare)
        ):
            return bare
        # иначе resolve из request+fn
        resolved = _resolve_object_from_phrase(f"{bare} {request}")
        if resolved:
            return resolved
        return bare

    req = (request or "").strip()
    if not req:
        return ""

    # 1) dotted 1C name already in phrase
    m = re.search(
        r"\b(?:Документ|Справочник|Document|Catalog)\s*\.\s*"
        r"([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)",
        req,
        re.I,
    )
    if m:
        bare = m.group(1)
        if len(bare) >= 10 or re.search(r"[a-zа-яё][A-ZА-ЯЁ]", bare):
            return bare
        r0 = _resolve_object_from_phrase(bare)
        return r0 or bare

    # 2) готовое CamelCase из фразы
    for m in re.finditer(r"\b([A-ZА-ЯЁ][\wА-Яа-яЁё]{6,})\b", req):
        cand = m.group(1)
        if _is_stop_hint(cand):
            continue
        if re.search(r"[a-zа-яё]", cand) or len(cand) >= 10:
            return cand

    # 3) ГЛАВНОЕ: find_modules по очищенной фразе (до parse_nl!)
    resolved = _resolve_object_from_phrase(req)
    if resolved:
        return resolved

    # 4) parse_nl — только CamelCase-подобные
    parsed = parse_nl_query(req)
    on = (parsed.get("object_name") or "").strip()
    if on and not _is_stop_hint(on):
        if re.search(r"[A-ZА-ЯЁ].*[a-zа-яё].*[A-ZА-ЯЁ]", on) or len(on) >= 12:
            return on
        r1 = _resolve_object_from_phrase(on)
        if r1:
            return r1

    # 5) не возвращать «реализация» — только если resolve нашёл полное имя
    cleaned = _strip_movements_noise(req)
    content = [
        w
        for w in re.findall(r"[A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]{3,}", cleaned or req)
        if not _is_stop_hint(w)
    ]
    if content:
        r3 = _resolve_object_from_phrase(" ".join(content[:6]))
        if r3:
            return r3
    return ""


def _code_dir() -> Path | None:
    try:
        from tools.code_search import resolve_code_roots
        from vector.catalog import runtime_fs_allowed

        if not runtime_fs_allowed():  # runtime: только зеркало коллекции файлов Qdrant, не диск
            roots = resolve_code_roots().roots
            return roots[0] if roots else None
    except Exception:
        pass
    try:
        from config import load_config

        cfg = load_config()
        raw = (cfg.get("code_dir") or (cfg.get("paths") or {}).get("code_dir") or "").strip()
        if not raw:
            return None
        p = Path(raw)
        if not p.is_absolute():
            from config import ROOT

            p = ROOT / p
        return p if p.is_dir() else None
    except Exception:
        return None


def _load_register_records(obj: str, full_name: str = "") -> list[dict[str, str]]:
    """
    Полный список регистров документа из XML выгрузки (RegisterRecords).
    Это «может писать» (метаданные), не всегда «пишет всегда».
    """
    if not obj:
        return []
    root = _code_dir()
    if not root:
        return []
    fn = (full_name or "").strip()
    class_folder = "Documents"
    fl = fn.lower()
    if fl.startswith("документ"):
        class_folder = "Documents"
    elif fl.startswith("справочник"):
        class_folder = "Catalogs"
    candidates = [
        root / class_folder / f"{obj}.xml",
        root / "Documents" / f"{obj}.xml",
        root / "Catalogs" / f"{obj}.xml",
    ]
    text = ""
    used = ""
    for p in candidates:
        if p.is_file():
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                used = str(p)
                break
            except OSError:
                continue
    if not text:
        return []
    # только блок RegisterRecords
    block_m = re.search(
        r"<RegisterRecords>(.*?)</RegisterRecords>", text, re.I | re.S
    )
    block = block_m.group(1) if block_m else text
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in _REG_RECORDS_XML.finditer(block):
        kind_en = m.group(1)
        name = m.group(2)
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        kind_ru = _KIND_RU.get(kind_en.lower(), kind_en)
        out.append(
            {
                "name": name,
                "kind": kind_ru,
                "full": f"{kind_ru}.{name}",
                "meta_en": f"{kind_en}.{name}",
                "source": "RegisterRecords",
                "xml_path": used,
            }
        )
    return out


def _norm_reg_token(s: str) -> str:
    t = (s or "").lower().replace("ё", "е")
    # грубое снятие падежных хвостов для сопоставления Отразить* ↔ имя регистра
    for end in (
        "ую",
        "юю",
        "ое",
        "ее",
        "ые",
        "ие",
        "ая",
        "яя",
        "ов",
        "ев",
        "ей",
        "ам",
        "ям",
        "ах",
        "ях",
        "у",
        "ю",
        "а",
        "я",
        "е",
        "и",
        "ы",
    ):
        if len(t) > len(end) + 4 and t.endswith(end):
            t = t[: -len(end)]
            break
    return t


def _camel_parts(s: str) -> list[str]:
    return re.findall(
        r"[A-ZА-ЯЁ][a-zа-яё0-9]*|[a-zа-яё0-9]+", s or ""
    )


def _match_otrazit_to_register(
    method_suffix: str, reg_names: list[str]
) -> str | None:
    """Сопоставить Отразить<Suffix> с именем из RegisterRecords / известным списком."""
    if not method_suffix:
        return None
    # точное
    for r in reg_names:
        if r == method_suffix:
            return r
    # suffix содержит имя регистра (длинные имена первыми)
    for r in sorted(reg_names, key=len, reverse=True):
        if r and r in method_suffix:
            return r
        if method_suffix in r and len(method_suffix) >= 8:
            return r
    # по «стеблям» CamelCase: ВыручкуИСебестоимостьПродаж ≈ ВыручкаИСебестоимостьПродаж
    ms_parts = [_norm_reg_token(p) for p in _camel_parts(method_suffix)]
    ms_parts = [p for p in ms_parts if len(p) >= 3]
    best = None
    best_score = 0.0
    for r in reg_names:
        rp = [_norm_reg_token(p) for p in _camel_parts(r)]
        rp = [p for p in rp if len(p) >= 3]
        if not rp or not ms_parts:
            continue
        # доля совпадающих стеблей
        inter = 0
        for a in ms_parts:
            for b in rp:
                if a == b or a.startswith(b) or b.startswith(a):
                    inter += 1
                    break
        score = inter / max(len(ms_parts), len(rp))
        # бонус: одинаковое число частей
        if len(ms_parts) == len(rp):
            score += 0.15
        if score > best_score:
            best_score = score
            best = r
    if best and best_score >= 0.55:
        return best
    ns = _norm_reg_token(method_suffix)
    for r in reg_names:
        nr = _norm_reg_token(r)
        if ns and nr and (ns == nr or ns in nr or nr in ns) and min(len(ns), len(nr)) >= 8:
            return r
    return None


def _looks_like_register_name(reg: str) -> bool:
    """Имя регистра: не поле строки, не метод, обычно длинное CamelCase."""
    if not reg or len(reg) < 5:
        return False
    low = reg.lower().replace("ё", "е")
    if low in _DVIZH_SKIP:
        return False
    # одно слово-поле из 2–3 слогов часто ложное; имена регистров обычно длиннее
    if reg[0].islower():
        return False
    return True


def _reg_slot(
    registers: dict[str, dict[str, Any]],
    reg: str,
    action: str,
    line_no: int,
    snip: str,
    *,
    require_write_like: bool = False,
) -> None:
    if not _looks_like_register_name(reg):
        return
    if require_write_like and (action or "").lower() not in _WRITE_ACTIONS:
        # «ссылка» без записи — не создаём слот с нуля
        if reg not in registers:
            return
    slot = registers.setdefault(
        reg,
        {"name": reg, "actions": [], "lines": [], "snippets": []},
    )
    if action and action not in slot["actions"]:
        slot["actions"].append(action)
    if line_no and line_no not in slot["lines"]:
        slot["lines"].append(line_no)
    if snip and snip not in slot["snippets"] and len(slot["snippets"]) < 8:
        slot["snippets"].append(snip)


def _scan_module_text(text: str, source_path: str) -> dict[str, Any]:
    body = strip_context_header(text or "")
    lines = body.splitlines()

    posting_procs: list[dict[str, Any]] = []
    for m in _POSTING.finditer(body):
        line_no = body.count("\n", 0, m.start()) + 1
        posting_procs.append({"name": m.group(2), "line": line_no, "type": m.group(1)})

    registers: dict[str, dict[str, Any]] = {}

    # 1) классика: Движения.X.Добавить()
    for m in _DVIZH.finditer(body):
        line_no = body.count("\n", 0, m.start()) + 1
        snip = lines[line_no - 1].strip()[:200] if 0 < line_no <= len(lines) else ""
        _reg_slot(registers, m.group(1), m.group(2), line_no, snip)

    # 2) Добавить(Движения.X) — регистр кладут в массив наборов
    for m in _DVIZH_AS_ARG.finditer(body):
        line_no = body.count("\n", 0, m.start()) + 1
        snip = lines[line_no - 1].strip()[:200] if 0 < line_no <= len(lines) else ""
        _reg_slot(registers, m.group(1), "Добавить(набор)", line_no, snip)

    # 3) Движения.ИмяРегистра.Свойство — только если имя уже похоже на регистр
    #    и уже есть write-like, либо это длинное имя (не поле строки)
    for m in _DVIZH_ANY.finditer(body):
        reg = m.group(1)
        line_no = body.count("\n", 0, m.start()) + 1
        snip = lines[line_no - 1].strip()[:200] if 0 < line_no <= len(lines) else ""
        # дополняем уже найденные; новые — только «длинные» имена регистров
        if reg in registers:
            _reg_slot(registers, reg, "ссылка", line_no, snip)
        elif len(reg) >= 8 and _looks_like_register_name(reg):
            _reg_slot(registers, reg, "ссылка", line_no, snip)

    meta_regs: list[str] = []
    for m in _REG_META.finditer(body):
        full = f"{m.group(1)}.{m.group(2)}"
        if full not in meta_regs:
            meta_regs.append(full)

    # XxxСервер.ОтразитьИмя(…, Движения, …)
    posting_calls: list[dict[str, Any]] = []
    for m in _OTRAZIT_DVIZH.finditer(body):
        module = m.group(1)
        suffix = m.group(2)
        line_no = body.count("\n", 0, m.start()) + 1
        snip = lines[line_no - 1].strip()[:220] if 0 < line_no <= len(lines) else ""
        posting_calls.append(
            {
                "module": module,
                "method": f"Отразить{suffix}",
                "method_suffix": suffix,
                "line": line_no,
                "snippet": snip,
                "uses_Движения": True,
            }
        )

    # прямые Регистры*.X.Метод(
    direct_writes: list[dict[str, Any]] = []
    for m in _REG_DIRECT_WRITE.finditer(body):
        kind, name, method = m.group(1), m.group(2), m.group(3)
        ml = method.lower()
        if ml in (
            "создатьменеджерзаписи",
            "создатьнаборзаписей",
            "выбрать",
            "получить",
            "пустосссылка",
            "пустоссылка",
        ):
            continue
        if not any(
            x in ml
            for x in (
                "запис",
                "отраз",
                "добав",
                "обнов",
                "удал",
                "сформ",
                "заполн",
            )
        ):
            # эвристика: оставляем «активные» методы
            if ml.startswith("получить") or ml.startswith("найти"):
                continue
        line_no = body.count("\n", 0, m.start()) + 1
        snip = lines[line_no - 1].strip()[:220] if 0 < line_no <= len(lines) else ""
        direct_writes.append(
            {
                "full": f"{kind}.{name}",
                "kind": kind,
                "name": name,
                "method": method,
                "line": line_no,
                "snippet": snip,
            }
        )

    # поля, которые заполняют у движения (эвристика: Движение.Поле = )
    for reg, info in registers.items():
        fields: list[str] = []
        for ln in info["lines"][:5]:
            start = max(0, ln - 5)
            end = min(len(lines), ln + 45)
            window = "\n".join(lines[start:end])
            for fm in re.finditer(
                r"(?:Движение|Запись|Строка)\s*\.\s*([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)\s*=",
                window,
            ):
                f = fm.group(1)
                if f not in fields and f.lower() not in (
                    "добавить",
                    "записать",
                    "период",
                ):
                    fields.append(f)
            for fm in re.finditer(
                rf"Движения\s*\.\s*{re.escape(reg)}\s*\.\s*"
                r"([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)",
                window,
                re.I,
            ):
                f = fm.group(1)
                if f not in ("Добавить", "Записать", "Очистить") and f not in fields:
                    fields.append(f)
        info["fields_assigned"] = fields[:40]
        info["fields_assigned_total"] = len(fields)

    return {
        "source_path": source_path,
        "posting_procedures": posting_procs,
        "registers_via_Движения": list(registers.values()),
        "registers_mentioned_meta": meta_regs[:40],
        "registers_mentioned_meta_total": len(meta_regs),
        "posting_calls_Отразить": posting_calls,
        "registers_direct_write": direct_writes,
        "has_posting_handler": any(
            "проведения" in p["name"].lower()
            for p in posting_procs
        ),
        "writes_movements": len(registers) > 0 or len(posting_calls) > 0,
    }


def _canonical_object_paths(
    obj: str,
    full_name: str = "",
    prefer_folders: list[str] | None = None,
) -> list[str]:
    """Пути Ext-модулей по классу из full_name / find_modules / всем папкам с модулем.

    Класс — из METADATA_TYPES (Documents/Catalogs/…), не «всегда Документ».
    """
    if not obj:
        return []
    from backends.base import (
        OBJECT_MODULE_DUMP_FOLDERS,
        dump_folder_for_full_name_prefix,
        is_typed_full_name,
    )

    pref = full_name.split(".", 1)[0] if is_typed_full_name(full_name) else ""
    mapped = dump_folder_for_full_name_prefix(pref) if pref else ""
    if mapped:
        folders: list[str] = [mapped]
    else:
        prefer = [f for f in (prefer_folders or []) if f in OBJECT_MODULE_DUMP_FOLDERS]
        rest = [f for f in OBJECT_MODULE_DUMP_FOLDERS if f not in prefer]
        folders = prefer + rest
    files = (
        "Ext/ObjectModule.bsl",
        "Ext/ManagerModule.bsl",
        "Ext/RecordSetModule.bsl",
        "Ext/ValueManagerModule.bsl",
    )
    paths: list[str] = []
    for folder in folders:
        base = f"{folder}/{obj}"
        for rel in files:
            paths.append(f"{base}/{rel}")
    return paths


def analyze_object_movements(
    request: str = "",
    *,
    full_name: str = "",
    max_modules: int = 8,
) -> dict[str, Any]:
    """
    Универсально для любого объекта конфигурации:
    какие регистры затрагивает код (Движения.X / мета-имена).

    request — фраза пользователя целиком
      «по каким регистрам проводится документ …» (имя из фразы / resolve)
    full_name — если уже известен: Документ.X из ЭТОЙ базы
    """
    st = code_collection_ready()
    if not st.get("ready"):
        return {
            "ok": False,
            "error": st.get("error") or "Qdrant не готов",
            "hint": st.get("hint"),
            "source": "qdrant",
        }

    req = (request or "").strip()
    fn = (full_name or "").strip()
    obj = _extract_object_hint(req, fn)
    if not obj and not fn:
        return {
            "ok": False,
            "error": "Не удалось выделить объект. Укажите full_name из find_metadata "
            "этой базы или имя в фразе.",
            "hint": "analyze_object_movements(request=фраза) "
            "или full_name из find_metadata/resolve_object ЭТОЙ базы",
        }

    search = fn or f"модуль объекта {obj}"
    from backends.base import (
        dump_folder_for_full_name_prefix,
        is_typed_full_name,
        typed_full_name_from_path,
    )

    folder = ""
    if is_typed_full_name(fn):
        folder = dump_folder_for_full_name_prefix(fn.split(".", 1)[0])
    else:
        blob = f"{req} {fn}".lower().replace("ё", "е")
        if re.search(r"\bсправочник", blob):
            folder = "Catalogs"
        elif re.search(r"\bдокумент", blob):
            folder = "Documents"
        elif re.search(r"\bобработк", blob):
            folder = "DataProcessors"
        elif re.search(r"\bотч[её]т", blob):
            folder = "Reports"
    if folder and obj:
        search = f"{folder}/{obj}"
    # ObjectModule first
    found = find_modules(
        f"{obj} ObjectModule" if obj else search,
        kind="",
        limit=20,
    )
    modules = list(found.get("modules") or [])
    # fallback broader
    if obj:
        found2 = find_modules(obj, limit=20)
        seen_paths = {(m.get("path") or "").replace("\\", "/") for m in modules}
        for m in found2.get("modules") or []:
            p = (m.get("path") or "").replace("\\", "/")
            if p and p not in seen_paths:
                modules.append(m)
                seen_paths.add(p)

    # Канонические Ext-пути: класс из full_name или папок, которые уже нашёл find_modules
    if obj:
        seen_paths = {(m.get("path") or "").replace("\\", "/") for m in modules}
        prefer: list[str] = []
        for m in modules:
            f = (m.get("path") or "").replace("\\", "/").split("/")[0]
            if f and f not in prefer:
                prefer.append(f)
        hit_folder = ""
        for p in _canonical_object_paths(obj, fn, prefer_folders=prefer):
            folder_p = p.split("/")[0]
            if hit_folder and folder_p != hit_folder:
                continue
            if p in seen_paths:
                if not hit_folder:
                    hit_folder = folder_p
                continue
            if get_chunks_by_source_path(p):
                typed = fn if is_typed_full_name(fn) else typed_full_name_from_path(p, obj)
                modules.append(
                    {
                        "path": p,
                        "object_name": obj,
                        "kind": "object_module"
                        if "ObjectModule" in p
                        else (
                            "recordset_module"
                            if "RecordSetModule" in p
                            else "manager_module"
                        ),
                        "score": 50_000 if "ObjectModule" in p else 40_000,
                        "full_name_1c": typed,
                    }
                )
                seen_paths.add(p)
                hit_folder = folder_p

    obj_l = (obj or "").lower()

    # prefer «свой» ObjectModule/ManagerModule документа, штрафовать чужие
    def _mod_score(m: dict[str, Any]) -> float:
        sp = (m.get("path") or "").replace("\\", "/")
        sc = float(m.get("score") or 0)
        low = sp.lower().replace("ё", "е")
        own = bool(obj_l and _path_has_obj(sp, obj or ""))
        if own and "objectmodule" in low and "/forms/" not in low:
            sc += 50_000
        elif own and "managermodule" in low:
            sc += 20_000
        elif "objectmodule" in low:
            sc += 50  # чужой ObjectModule почти не бустим
        if "managermodule" in low and not own:
            sc -= 200
        if "/forms/" in low:
            sc -= 5_000
        if "commandmodule" in low:
            sc -= 8_000
        if "/templates/" in low:
            sc -= 8_000
        if not own and obj_l:
            # чужие объекты (СверткаИБ и т.п.)
            sc -= 15_000
        if own:
            sc += 500
        return sc

    modules = sorted(modules, key=_mod_score, reverse=True)[
        : max(1, min(max_modules, 12))
    ]

    # Сначала только «ядро» объекта: Ext/ObjectModule + Ext/ManagerModule
    # (формы/макеты засоряют alias'ами запросов и полями).
    def _is_core_module(path: str) -> bool:
        p = path.replace("\\", "/")
        if "/Forms/" in p or "/forms/" in p:
            return False
        if "/Templates/" in p or "/templates/" in p:
            return False
        if "/Commands/" in p:
            return False
        if obj and not _path_has_obj(p, obj):
            return False
        return "ObjectModule" in p or "ManagerModule" in p or "/Ext/" in p

    core = [m for m in modules if _is_core_module((m.get("path") or ""))]
    if core:
        modules = core[: max(2, min(max_modules, 4))]

    analyses: list[dict[str, Any]] = []
    all_regs: dict[str, dict[str, Any]] = {}

    def _merge_regs(path: str, reg_list: list[dict[str, Any]]) -> None:
        for reg in reg_list or []:
            name = reg["name"]
            if not _looks_like_register_name(name):
                continue
            acts = [a for a in (reg.get("actions") or [])]
            # пропускаем «только ссылка» без write, если уже нет write-слота
            write_like = any((a or "").lower() in _WRITE_ACTIONS for a in acts)
            if not write_like and name not in all_regs:
                # длинные имена из Добавить(Движения.X) уже write-like;
                # чистые «ссылка» — только как дополнение
                if acts == ["ссылка"] and len(name) < 10:
                    continue
            slot = all_regs.setdefault(
                name,
                {
                    "name": name,
                    "actions": [],
                    "modules": [],
                    "fields_assigned": [],
                    "snippets": [],
                },
            )
            for a in acts:
                if a not in slot["actions"]:
                    slot["actions"].append(a)
            if path not in slot["modules"]:
                slot["modules"].append(path)
            for f in reg.get("fields_assigned") or []:
                if f and f not in slot["fields_assigned"]:
                    slot["fields_assigned"].append(f)
            for s in reg.get("snippets") or []:
                if s not in slot["snippets"] and len(slot["snippets"]) < 6:
                    slot["snippets"].append(s)

    for m in modules:
        path = (m.get("path") or "").replace("\\", "/")
        chunks = get_chunks_by_source_path(path)
        if not chunks:
            continue
        text = assemble_module_text(chunks)
        scan = _scan_module_text(text, path)
        scan["module"] = {
            "path": path,
            "full_name_1c": m.get("full_name_1c"),
            "object_name": m.get("object_name") or obj,
            "kind": m.get("kind"),
            "score": m.get("score"),
        }
        # кусок ОбработкаПроведения
        for p in scan.get("posting_procedures") or []:
            if "проведения" in p["name"].lower() or p["name"].lower() == "обработкапроведения":
                rd = read_module(path, start_line=max(1, p["line"] - 2), max_lines=80)
                if rd.get("ok"):
                    scan["posting_code_preview"] = {
                        "start_line": rd.get("start_line"),
                        "end_line": rd.get("end_line"),
                        "text": (rd.get("text") or "")[:3500],
                    }
                break
        analyses.append(scan)
        _merge_regs(path, scan.get("registers_via_Движения") or [])

    writes = list(all_regs.values())
    has_handler = any(a.get("has_posting_handler") for a in analyses)
    writes_mov = any(a.get("writes_movements") for a in analyses)

    # --- hop: только если в своём ObjectModule нет ни handler, ни Движения ---
    hop_notes: list[str] = []
    own_has_signal = any(
        (a.get("has_posting_handler") or a.get("writes_movements"))
        and obj
        and _path_has_obj(a.get("source_path") or "", obj)
        for a in analyses
    )
    if not writes_mov and not own_has_signal and obj:
        try:
            from vector.search import search_code_vectors

            for q in (
                f"Движения. {obj}",
                f"ОбработкаПроведения {obj}",
                f"Проведение{obj}" if obj else "Движения.Добавить",
            ):
                if not q.strip():
                    continue
                sc = search_code_vectors(q, limit=8, match_mode="hybrid")
                for m in sc.get("matches") or []:
                    path = (m.get("path") or "").replace("\\", "/")
                    if not path or any(a.get("source_path") == path for a in analyses):
                        continue
                    # hop только если path или snippet явно про этот объект
                    blob = " ".join(
                        [
                            path,
                            str(m.get("object_name") or ""),
                            str(m.get("text") or "")[:500],
                            str(m.get("location") or ""),
                        ]
                    )
                    if obj and not _path_has_obj(blob, obj):
                        continue
                    chunks = get_chunks_by_source_path(path)
                    if not chunks:
                        continue
                    text = assemble_module_text(chunks)
                    if obj and not _path_has_obj(text[:8000], obj):
                        continue
                    scan = _scan_module_text(text, path)
                    # только реальные write-like регистры
                    write_regs = [
                        r
                        for r in (scan.get("registers_via_Движения") or [])
                        if any(
                            (a or "").lower() in _WRITE_ACTIONS
                            for a in (r.get("actions") or [])
                        )
                    ]
                    if not write_regs and not scan.get("has_posting_handler"):
                        continue
                    scan["module"] = {
                        "path": path,
                        "object_name": m.get("object_name"),
                        "hop": True,
                    }
                    analyses.append(scan)
                    hop_notes.append(path)
                    _merge_regs(path, write_regs)
                    if len(hop_notes) >= 4:
                        break
                if len(hop_notes) >= 4:
                    break
        except Exception:
            pass

    # --- метаданные RegisterRecords из XML выгрузки (полный список «может писать») ---
    reg_records = _load_register_records(obj or "", fn)
    reg_names_meta = [r["name"] for r in reg_records]

    # --- вызовы Отразить* / прямые Регистры*.X ---
    # Только ObjectModule (проведение). ManagerModule засоряет печатью/аналитикой.
    posting_calls: list[dict[str, Any]] = []
    direct_writes: list[dict[str, Any]] = []
    _SKIP_DIRECT_METHODS = {
        "местаучета",
        "именаполейколлекциипоумолчанию",
        "создатьключаналитики",
        "значениеключаналитики",
        "комплектпечатныхформ",
        "комплектыпечатипообъектам",
        "сформироватькомплектвнешнихпечатныхформ",
        "подготовитькомплектпечатныхформ",
        "добавитьпечатнуюформувкомплект",
        "заполнитьвколлекции",
    }
    _SKIP_DIRECT_REGS = {
        "настройкипечатиобъектов",
        "аналитикаучетаноменклатуры",
        "аналитикаучетапопартнерам",
        "аналитикаучетанаборов",
    }

    for a in analyses:
        spath = (a.get("source_path") or "").replace("\\", "/")
        is_object_mod = "ObjectModule" in spath and "/Forms/" not in spath
        # Отразить*(…,Движения) — из модуля с проведением / ObjectModule
        if not (is_object_mod or a.get("has_posting_handler")):
            continue
        for c in a.get("posting_calls_Отразить") or []:
            c2 = dict(c)
            c2["source_path"] = spath
            matched = _match_otrazit_to_register(
                c.get("method_suffix") or "",
                reg_names_meta or list(all_regs.keys()),
            )
            c2["matched_register"] = matched
            posting_calls.append(c2)
            if matched:
                slot = all_regs.setdefault(
                    matched,
                    {
                        "name": matched,
                        "actions": [],
                        "modules": [],
                        "fields_assigned": [],
                        "snippets": [],
                    },
                )
                act = f"Отразить←{c.get('module')}"
                if act not in slot["actions"]:
                    slot["actions"].append(act)
                if spath and spath not in slot["modules"]:
                    slot["modules"].append(spath)
                sn = c.get("snippet") or ""
                if sn and sn not in slot["snippets"]:
                    slot["snippets"].append(sn)
            # unmatched — только в posting_calls, без фейкового имени регистра
        if not is_object_mod:
            continue
        for d in a.get("registers_direct_write") or []:
            method_l = (d.get("method") or "").lower()
            name = d.get("name") or ""
            if method_l in _SKIP_DIRECT_METHODS:
                continue
            if name.lower() in _SKIP_DIRECT_REGS:
                continue
            d2 = dict(d)
            d2["source_path"] = spath
            direct_writes.append(d2)
            if name and _looks_like_register_name(name):
                slot = all_regs.setdefault(
                    name,
                    {
                        "name": name,
                        "actions": [],
                        "modules": [],
                        "fields_assigned": [],
                        "snippets": [],
                    },
                )
                act = f"{d.get('kind')}.{d.get('method')}"
                if act not in slot["actions"]:
                    slot["actions"].append(act)
                if spath and spath not in slot["modules"]:
                    slot["modules"].append(spath)

    # итоговые регистры из кода
    writes = []
    for reg in all_regs.values():
        acts = reg.get("actions") or []
        write_like = any(
            (a or "").lower() in _WRITE_ACTIONS
            or (a or "").startswith("Отразить")
            or "Регистр" in (a or "")
            for a in acts
        )
        if write_like or len(reg.get("name") or "") >= 8:
            writes.append(reg)
    if not writes:
        writes = list(all_regs.values())

    # объединить: метаданные + код
    unified: list[dict[str, Any]] = []
    seen_u: set[str] = set()
    for r in reg_records:
        name = r["name"]
        seen_u.add(name.lower())
        evidence = next((w for w in writes if w["name"] == name), None)
        unified.append(
            {
                "name": name,
                "kind": r.get("kind"),
                "full": r.get("full"),
                "in_metadata_RegisterRecords": True,
                "in_code": bool(evidence),
                "actions": (evidence or {}).get("actions") or [],
                "modules": (evidence or {}).get("modules") or [],
                "snippets": (evidence or {}).get("snippets") or [],
            }
        )
    for w in writes:
        if w["name"].lower() in seen_u:
            continue
        seen_u.add(w["name"].lower())
        unified.append(
            {
                "name": w["name"],
                "kind": None,
                "full": w["name"],
                "in_metadata_RegisterRecords": False,
                "in_code": True,
                "actions": w.get("actions") or [],
                "modules": w.get("modules") or [],
                "snippets": w.get("snippets") or [],
            }
        )

    writes_mov = len(writes) > 0 or len(posting_calls) > 0
    has_handler = any(a.get("has_posting_handler") for a in analyses)
    has_meta = len(reg_records) > 0

    # человекочитаемый итог
    if has_meta or writes_mov:
        parts = []
        if has_meta:
            parts.append(
                f"в метаданных RegisterRecords — {len(reg_records)} регистр(ов)"
            )
        if writes_mov:
            parts.append(
                f"в коде (Движения.*/Отразить*/Регистры*) — {len(writes)} подтверждённых"
            )
        if posting_calls:
            parts.append(f"вызовов Отразить*(…,Движения) — {len(posting_calls)}")
        summary = (
            f"Объект «{obj or fn}»: " + "; ".join(parts) + ". "
            "Полный список: registers_all[]. "
            "Только код: registers_from_code[]. "
            "Только метаданные: register_records_meta[]. "
            "НЕ выдумывай регистры из ManagerModule «по упоминанию»."
        )
        if hop_notes:
            summary += f" Hop-модули: {', '.join(hop_notes[:3])}."
        conclusion = "writes_registers" if (writes_mov or has_meta) else "no_posting_movements_found"
        if has_meta and not writes_mov and not has_handler:
            # метаданные есть, кода движений нет — всё же «может писать»
            conclusion = "register_records_meta_only"
    elif has_handler:
        summary = (
            f"Объект «{obj or fn}»: есть ОбработкаПроведения/заполнения, но явных "
            "Движения.*/Отразить*(…,Движения) не найдено. "
            "Рекомендуется: trace_calls / search_code по общим модулям проведения."
        )
        conclusion = "posting_handler_no_direct_movements"
    else:
        summary = (
            f"Объект «{obj or fn}»: в ObjectModule/ManagerModule не найдено "
            "ни ОбработкаПроведения, ни движений, ни RegisterRecords. "
            "Документ, вероятно, не формирует движений."
        )
        conclusion = "no_posting_movements_found"

    # Компактный ответ (цель < 5500 символов JSON), иначе LLM видит обрезку.
    names_full: list[str] = []
    if reg_records:
        for r in reg_records:
            nm = r.get("full") or r.get("name") or ""
            in_c = any(
                (w.get("name") or "").lower() == (r.get("name") or "").lower()
                for w in writes
            )
            names_full.append(f"{nm}{' ✓' if in_c else ''}")
    else:
        for u in unified:
            names_full.append(
                f"{u.get('full') or u.get('name')}"
                f"{' ✓' if u.get('in_code') else ''}"
            )

    posting_lines = [
        f"{c.get('module')}.{c.get('method')}→{c.get('matched_register') or '?'}"
        for c in posting_calls[:25]
    ]
    obj_mod = next(
        (
            a.get("source_path")
            for a in analyses
            if a.get("source_path") and "ObjectModule" in (a.get("source_path") or "")
        ),
        (analyses[0].get("source_path") if analyses else None),
    )

    return {
        "ok": True,
        "source": "qdrant+dump_xml" if has_meta else "qdrant",
        "full_name": fn
        if is_typed_full_name(fn)
        else (
            typed_full_name_from_path(str(obj_mod or ""), obj)
            or (fn or None)
        ),
        "object_hint": obj,
        "conclusion": conclusion,
        "summary": summary,
        "count": len(names_full),
        "object_module": obj_mod,
        # ЕДИНСТВЕННЫЙ полный список — перечислить все строки как есть
        "register_names_full": names_full,
        "posting_calls": posting_lines,
        "prefer": (
            f"Перечисли ВСЕ {len(names_full)} строк из register_names_full "
            "по порядку, без сокращений и без «и другие». "
            "✓ = подтверждено кодом проведения. "
            "posting_calls — кратко. Не выдумывай имена."
        ),
        "note": "get_object_structure НЕ содержит этот список.",
    }
