# -*- coding: utf-8 -*-
"""
Высокоточные детекторы intent — идут ДО основной цепочки router._rules.

Только структура фразы и платформенная лексика (класс метаданных, «форма», «запрос»,
«регистр», «блокировка», номер документа, текст ошибки). Имён объектов CF здесь нет:
имя, если нужно, берётся из PascalCase самой фразы или из каталога Qdrant (name_match).

Каждый детектор = класс ошибки из стресс-теста, а не конкретная фраза:
  - мутирующий императив → отказ read-only;
  - номер документа → document_by_number;
  - «как … в запросе / синтаксис функции» → справка платформы, не inspect_code;
  - формы/кнопки, подписки, СКД, трассировка, блокировки, доступ — раньше «мета» и «данных»;
  - «где хранится / каким документом оформляется» → поиск объекта, не отчёт;
  - «почему подставилось / не перенеслось / пустой» → explain_fill;
  - «где в коде / как рассчитывается / что происходит при» → inspect_code.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from tools.meta_class import detect_meta_class, strip_class_words

# ---------- общие признаки ----------

_CAMEL = re.compile(r"[A-ZА-ЯЁ][a-zа-яё0-9_]+(?:[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_]*)+")
_DOTTED = re.compile(r"([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_]{2,})\.([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_]{2,})")
# номер документа: префикс-буквы + цифры с дефисом, «№ …», или длинный цифровой с ведущими нулями
_DOCNUM = re.compile(
    r"(?:№\s*([A-Za-zА-Яа-яЁё0-9][\w-]{2,}))|"
    r"(?<![\w-])([A-Za-zА-Яа-яЁё]{1,6}\d{0,4}-\d{3,})(?![\w-])|"
    r"(?<![\w-])(0{2,}\d{2,})(?![\w-])",
)

_OBJ_CLASS = re.compile(
    r"(?<![а-яё])(документ\w*|справочник\w*|регистр\w*|перечислен\w*|отчет\w*|отчёт\w*|"
    r"обработк\w*|констант\w*|подсистем\w*|план\w*\s+(?:видов|счетов|обмен))",
)


def _low(s: str) -> str:
    return (s or "").lower().replace("ё", "е")


def _speech(raw: str) -> str:
    """Речь без CamelCase-идентификаторов (одиночные слова с заглавной остаются — это речь)."""
    s = _CAMEL.sub(" ", raw or "")
    s = _DOTTED.sub(" ", s)
    return re.sub(r"\s+", " ", _low(s)).strip()


def camel_names(raw: str) -> list[str]:
    out: list[str] = []
    for m in re.finditer(r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_]{3,}", raw or ""):
        tok = m.group(0)
        if re.search(r"[a-zа-яё][A-ZА-ЯЁ]", tok):
            out.append(tok)
    return out


def _proc_candidate(raw: str) -> tuple[str, str]:
    """(процедура, модуль) из фразы: «Модуль.Процедура» или CamelCase, не являющийся объектом каталога."""
    try:
        from tools.name_match import name_in_object_catalog
    except Exception:  # pragma: no cover
        name_in_object_catalog = lambda _n: False  # noqa: E731
    m = _DOTTED.search(raw or "")
    if m and re.search(r"[a-zа-яё][A-ZА-ЯЁ]", m.group(2)):
        # «Модуль.Процедура» — только если модуль есть в каталоге этой CF (иначе метод платформы)
        try:
            if name_in_object_catalog(m.group(1)):
                return m.group(2), m.group(1)
        except Exception:
            pass
        return "", ""
    names = camel_names(raw)
    if not names:
        return "", ""
    for n in names:
        try:
            if not name_in_object_catalog(n):
                return n, ""
        except Exception:
            return n, ""
    return "", ""


def extract_doc_number(raw: str) -> str:
    m = _DOCNUM.search(raw or "")
    if not m:
        return ""
    return next((g for g in m.groups() if g), "")


def _d(tool: str | None, args: dict[str, Any], conf: float, reason: str, fmt: str = "facts", **extra: Any) -> dict[str, Any]:
    out = {"tool": tool, "args": args, "confidence": conf, "reason": f"[detect] {reason}", "format": fmt}
    out.update(extra)
    return out


# ---------- детекторы ----------

_MUTATE = re.compile(
    r"^(?:пожалуйста\s+|ну\s+|быстро\s+|срочно\s+)?(?:можешь\s+|сможешь\s+)?"
    r"(удали|удалить|измени|изменить|поменяй|поменять|перепроведи|проведи|распроведи|отмени\s+проведени|"
    r"создай|создать|добавь|добавить|запиши|записать|установи|пометь|сними\s+пометк|очисти|обнули|"
    r"загрузи\s+в\s+базу|исправь\s+данн|заполни\s+и\s+запиши|перенумеруй)\b"
)


_BUILD_VERB = re.compile(
    r"^(?:пожалуйста\s+|ну\s+|можешь\s+|сможешь\s+|давай\s+|нужно\s+|надо\s+)*"
    r"(?:создай|создать|сделай|сделать|напиши|написать|сформируй|сформировать|сгенерируй|сгенерировать|"
    r"собери|собрать|разработай|разработать|подготовь|подготовить|составь|нужна|нужен|хочу)\b"
)
_BUILD_OBJ = re.compile(r"(?:внешн\w*\s+)?обработк\w*|\bepf\b|эпф|\bэ\.?п\.?ф\b|внешн\w*\s+отчет\w*|\berf\b|эрф|\bэ\.?р\.?ф\b")


def _det_build_epf(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    """«сделай / напиши обработку (epf) / внешний отчёт (erf), которая …» — собрать файл, а не менять базу."""
    m = _BUILD_VERB.search(sp)
    if not m or not _BUILD_OBJ.search(sp[m.end():m.end() + 60]):
        return None
    return _d(
        None,
        {},
        0.95,
        "сборка внешней обработки (.epf) / отчёта (.erf)",
        "manager",
        route="build_epf",
    )


def _det_mutation(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    if not _MUTATE.search(sp):
        return None
    # «создай отчёт / добавь колонку в excel» — это про выгрузку, не изменение базы
    if re.search(r"отчет|excel|эксел|выгруз|график|диаграм|таблиц|запрос\w*\s+для|код|процедур|функци|пример", sp):
        return None
    return _d(
        None,
        {},
        0.99,
        "изменение данных — агент только читает",
        "manager",
        route="refuse_readonly",
        reply=(
            "Я работаю только на чтение и не меняю данные в базе. "
            "Могу показать, какие объекты попадут под действие (список/отбор), "
            "или подсказать, где это делается в программе."
        ),
    )


def _det_docnum(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    num = extract_doc_number(raw)
    if not num:
        return None
    # даты «2016-01-01», телефоны и т.п. — не номер
    if re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", num):
        return None
    if re.search(r"ошибк|код\b|процедур|функци", sp):
        return None
    args: dict[str, Any] = {"request": raw, "number": num, "limit": 5}
    if ctx.startswith("Документ."):
        args["full_name"] = ctx
    return _d("document_by_number", args, 0.93, f"номер документа {num}", "manager")


_HELP_Q = re.compile(r"(?:^|\s)как\b.{0,40}\bв\s+(?:тексте\s+)?запрос(?:е|ах)\b|\bв\s+(?:тексте\s+)?запрос(?:е|ах)\b.{0,30}\bкак\b|язык\w*\s+запрос")
_HELP_BSL = re.compile(
    r"синтаксис\s+(?:функци|метод|оператор|процедур|конструкц)|"
    r"^как\s+(?:в\s+1с\s+)?(?:сделать|написать|вызвать|создать|запустить|использовать|объявить|обойти|прочитать|записать)\b"
)


def _det_platform_help(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    # «в конфигурации / у нас / в документе X» — это вопрос про CF, не про платформу
    cf_ctx = re.search(r"в\s+(?:нашей\s+)?конфигурац|у\s+нас|в\s+базе|в\s+модул|в\s+коде", sp) or _OBJ_CLASS.search(sp)
    if _HELP_Q.search(pl) and not re.search(r"найди|покажи\s+запрос|в\s+цикл|антипаттерн|тормоз", sp):
        if cf_ctx and re.search(r"какой|где|найди", sp):
            return None
        topic = ""
        for key, pat in (
            ("соединение", r"соединени|join"),
            ("группировка", r"группир|сгруппир|итог"),
            ("регистры", r"остатк|срез|виртуальн|оборот"),
            ("даты", r"период|дат|месяц"),
            ("примеры", r"объединит|union|временн"),
        ):
            if re.search(pat, sp):
                topic = key
                break
        return _d("query_syntax_help", {"topic": topic}, 0.93, "как написать в языке запросов")
    if re.search(r"^(?:а\s+)?(?:чем\s+отлича\w*|в\s+чем\s+разница|разница\s+между)", sp) and not _OBJ_CLASS.search(sp):
        return _d("code_help", {"query": raw}, 0.88, "теория платформы")
    if re.search(r"виртуальн\w*\s+таблиц|срез\w*\s+последн\w*|срез\w*\s+первых", sp) and re.search(
        r"что\s+такое|как\s|зачем|синтаксис|параметр", sp
    ):
        return _d("query_syntax_help", {"topic": "регистры"}, 0.9, "виртуальные таблицы запросов")
    if _HELP_BSL.search(sp) and not (cf_ctx and not re.search(r"&на|фонов|длительн|синтаксис", pl)):
        return _d("code_help", {"query": raw}, 0.9, "справка встроенного языка / платформы")
    return None


def _det_query_audit(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    if re.search(
        r"запрос\w*\s+в\s+цикл|в\s+цикле\s+запрос|антипаттерн|через\s+точку|"
        r"плох\w*\s+(?:написан|код)|качеств\w*\s+код|ревью|code\s*review|"
        r"неоптимальн\w*\s+запрос",
        sp,
    ):
        return _d("audit_query_code", {"request": raw, "max_findings": 40}, 0.92, "аудит качества кода/запросов")
    if re.search(r"тормоз|медленн|долго\s|долгое|долгий|зависает|подвисает", sp) and not re.search(
        r"ждут|блокир|ожидани", sp
    ):
        if re.search(r"отчет", sp) and not camel_names(raw):
            return None  # «отчёт долго формируется» без имени — пусть решает LLM/уточнение
        return _d("audit_query_code", {"request": raw, "max_findings": 40}, 0.86, "медленно → аудит запросов/кода")
    return None


def _det_security(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    if re.search(r"(?<![а-яё])выполнить(?![а-яё]).{0,20}(?<![а-яё])вычислить(?![а-яё])|вычислить.{0,20}выполнить|"
                 r"выполнить\s*\(|вычислить\s*\(|инъекц|небезопасн\w*\s+код|опасн\w*\s+код", pl):
        scope = "exchange" if re.search(r"обмен", sp) else "all"
        return _d("scan_code_injection", {"scope": scope, "limit": 40}, 0.9, "опасные конструкции в коде")
    return None


def _det_locks(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    if re.search(r"чем\s+отлича|что\s+такое|разница\s+между", sp):
        return None  # теория — справка платформы
    try:
        from tools.recommend_locks import is_lock_migration_question

        if is_lock_migration_question(raw):
            return None  # recommend_managed_locks — существующее правило
    except Exception:
        pass
    if re.search(
        r"внедр|перевест\w*\s+на\s+управля|рекоменд\w*\s+блокир|(?:ставить|поставить|добавить)\s+блокир|"
        r"блокировк\w*\s+(?:нужно\s+|надо\s+|стоит\s+)?(?:поставить|ставить|добавить)",
        sp,
    ):
        return _d("recommend_managed_locks", {"request": raw, "max_registers": 15}, 0.9, "какие управляемые блокировки добавить")
    if re.search(
        r"не\s+удалось\s+заблокир|блокировк|блокирует|заблокирован|друг\s+друга\s+ждут|ждут\s+друг\s+друга|"
        r"взаимоблок|deadlock|конфликт\w*\s+блокир|ожидани\w*\s+(?:на\s+)?блокир|таймаут\s+блокир|"
        r"(?:пользовател\w*|все|люди)\s+ждут|ждут\s+при\s+проведени|(?:база|1с)\s+(?:виснет|зависает)\s+при\s+проведени",
        pl,
    ):
        args: dict[str, Any] = {"request": raw, "max_hits": 40}
        names = camel_names(raw)
        if names:
            args["object_hint"] = names[-1]
        elif ctx:
            args["object_hint"] = ctx.split(".")[-1]
        return _d("analyze_locks", args, 0.92, "блокировки / ожидания")
    return None


def _det_subscriptions(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    if re.search(r"подписан\w*\s+на|подписк\w*\s+(?:на\s+)?событ|кто\s+подписан|какие\s+подписк", pl):
        return _d("explain_subscriptions", {"request": raw, "limit": 40}, 0.93, "подписки на события")
    return None


def _det_skd(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    if re.search(
        r"(?<![а-яё])скд(?![а-яё])|схем\w*\s+компоновк|набор\w*\s+данных|вычисляем\w*\s+пол|"
        r"откуда\s+(?:берутся\s+|бер[её]тся\s+)?данные\s+в\s+отчет|текст\s+запроса\s+отчет",
        pl,
    ):
        return _d("read_skd_query", {"request": raw}, 0.92, "схема компоновки отчёта")
    return None


def _det_calls(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    trace = re.search(r"прослед|отслед|цепочк\w*\s+вызов|(?<![a-z])trace(?![a-z])|дерево\s+вызов|граф\s+вызов", pl)
    callers = re.search(
        r"кто\s+(?:ее\s+|его\s+)?(?:использует|вызывает)|откуда\s+(?:ее\s+|его\s+)?вызыва|где\s+(?:ее\s+|его\s+)?(?:используется|вызывается)|"
        r"где\s+объявлен|где\s+определен|go\s*to\s*definition|где\s+описан\w*\s+(?:процедур|функци)",
        pl,
    )
    if not (trace or callers):
        return None
    # «где используется общий реквизит / определяемый тип / подписка» — dump-правила, не вызовы кода
    if re.search(r"общ\w+\s+реквизит|определяем\w*\s+тип|подписк|план\w*\s+обмен|регламентн", pl):
        return None
    proc, module = _proc_candidate(raw)
    if trace:
        args = {"request": raw, "depth": 2}
        if proc:
            args["procedure"] = proc
        return _d("trace_calls", args, 0.92, "трассировка вызовов")
    if not proc:
        # «где вызывается расчёт себестоимости» — имени процедуры нет: искать по смыслу в коде
        return _d("inspect_code", {"request": raw, "max_hits": 12}, 0.86, "вызовы по описанию (без имени процедуры)")
    args = {"request": raw, "procedure": proc, "limit": 20}
    if module:
        args["module"] = module
    return _d("proc_index", args, 0.93, f"объявление и вызовы {proc}")


def _det_form(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    form = re.search(r"(?<![а-яё])форм(?:а|ы|е|у|ой|ами|ах)?(?![а-яё])", sp) or re.search(r"форма\w*(?:документа|списка|элемента|выбора)", _low(raw))
    button = re.search(r"кнопк|(?<![а-яё])команд\w*\s+форм", sp)
    if not (form or button):
        return None
    if re.search(r"сформир|печатн|макет", sp):
        return None
    args: dict[str, Any] = {"request": raw, "limit": 80}
    if ctx:
        args["full_name"] = ctx
    # конкретная кнопка/поле: «что делает кнопка X», «почему кнопка/поле X недоступно/не видно»
    named = bool(re.search(r"[«\"']|(?<!^)(?<![.!?]\s)[A-ZА-ЯЁ][а-яё]", raw[1:]))
    if named and (
        re.search(r"кнопк", sp)
        or re.search(r"(?:пол[еяю]|колонк\w*|реквизит\w*|флажок)\b.{0,40}(?:недоступн|не\s+видн|не\s+виден|не\s+видна|скрыт|серое|серая|не\s+активн|не\s+редактир|нельзя)", sp)
        or re.search(r"(?:недоступн|не\s+видн|не\s+виден|скрыт|не\s+редактир)\w*\s+(?:пол[еяю]|колонк\w*)", sp)
    ):
        a2: dict[str, Any] = {"request": raw}
        if ctx:
            a2["full_name"] = ctx
        return _d("explain_command", a2, 0.92, "кнопка/поле формы: обработчик и доступность")
    if re.search(r"элемент|колонк|не\s+видно|не\s+видна|не\s+виден|скрыт|недоступн|серая|серое|не\s+активн|поля\s+на\s+форм|что\s+на\s+форм", sp):
        a2: dict[str, Any] = {"request": raw}
        if ctx:
            a2["full_name"] = ctx
        return _d("describe_form", a2, 0.9, "элементы/видимость формы")
    if button or re.search(
        r"событи|обработчик|при\s+открыт|при\s+создан|при\s+изменен|при\s+закрыт|приизменении|приоткрытии|"
        r"команд|что\s+выполняется|что\s+происходит",
        pl,
    ):
        return _d("form_event_map", args, 0.91, "события/команды формы")
    return None


def _det_print(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    if re.search(r"не\s+печата|не\s+выводится\s+на\s+печат|в\s+печатн\w*\s+форм\w*\s+(?:нет|не)", sp):
        a: dict[str, Any] = {"request": raw, "limit": 80}
        if ctx:
            a["full_name"] = ctx
        return _d("print_form_bind", a, 0.88, "печатная форма: что выводится")
    return None


def _det_access(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    if re.search(r"список\s+пользовател|какие\s+пользовател\w*\s+(?:есть|в\s+базе)|пользовател\w*\s+базы|все\s+пользовател", sp):
        return _d("list_users", {"limit": 100}, 0.9, "пользователи ИБ")
    if re.search(
        r"нет\s+доступа|доступ\w*\s+(?:к|на)\s|прав\w*\s+(?:у|на|к)\s|(?<![а-яё])рол(?:ь|и|ей)(?![а-яё])|"
        r"кто\s+может\s+(?:провод|создав|видеть|смотреть|изменя|удаля|открыва|редактир)|"
        r"(?:недостаточно|нарушение)\s+прав|"
        r"почему\s+\w+\s+не\s+(?:может|могу|можем)\s+(?:открыть|увидеть|посмотреть|зайти|войти)",
        sp,
    ):
        if re.search(r"(?<![а-яё])rls(?![а-яё])|ограничен\w*\s+(?:доступа\s+)?на\s+уровне|шаблон\w*\s+ограничен", pl):
            return None  # explain_rls — существующее правило
        # «какие роли могут удалять справочники» — роли по праву на класс (list_roles_by_right)
        if re.search(r"рол\w*\s+(?:могут|может|имеют\s+право)\s+(?:удаля|изменя|редактир|помеча|интерактивно)", sp) and not camel_names(raw):
            return None
        return _d("access_overview", {"request": raw}, 0.9, "доступ/роли")
    return None


_LIST_TAIL = r"\s*(?:есть|имеются|существуют)?\s*(?:в\s+(?:конфигурац\w*|базе|программ\w*|системе))?\s*(?:и\s+какие)?\s*\??\s*$"


def _is_class_listing(sp: str) -> bool:
    """Во фразе кроме слов класса и «какие/сколько/есть ли/в базе» ничего нет."""
    if not re.match(r"^(?:а\s+)?(?:сколько|какие|список|перечисли|покажи|есть\s+ли|выведи)", sp):
        return False
    rest = strip_class_words(sp)
    rest = re.sub(
        r"(?<![а-яё])(?:а|сколько|всего|какие|вообще|список|всех|все|перечисли|покажи|выведи|есть|ли|имеются|"
        r"существуют|в|конфигурации|конфигурация|базе|программе|системе|и|у|нас|наши|там)(?![а-яё])",
        " ",
        rest,
    )
    return not re.sub(r"[\s?.!,]+", "", rest)


def _det_meta(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    mtype, _prefix = detect_meta_class(raw)
    # dump-специализированные (план обмена, общие реквизиты, HTTP, …) — существующие правила
    if mtype in ("ПланыОбмена", "ОбщиеРеквизиты", "ОпределяемыеТипы", "HTTPСервисы", "ПодпискиНаСобытия", "РегламентныеЗадания"):
        return None
    # перечень объектов класса: «какие регистры накопления есть в базе», «сколько всего документов»
    if mtype and _is_class_listing(sp) and not camel_names(raw):
        return _d("list_objects", {"metadata_type": mtype, "limit": 200}, 0.9, f"перечень {mtype}")
    # свойства объекта метаданных (не данные): «владельцы справочника», «периодичность регистра», «остатки или обороты»,
    # «предопределённые счета» — describe_object отдаёт properties[] / predefined[]
    if re.search(
        r"владел\w*|периодичност\w*|предопредел\w*|вид\w*\s+регистр\w*|остатк\w*\s+или\s+оборот|оборот\w*\s+или\s+остатк|"
        r"иерархическ\w*\s+ли|есть\s+ли\s+иерархи|вид\w*\s+иерархи|подчин\w*\s+(?:ли\s+)?регистратор|оперативн\w*\s+проведени|"
        r"проводится\s+ли|разрешено\s+ли\s+проведени|длин\w*\s+(?:кода|номера|наименовани)|нумераци\w*|виды\s+субконто|"
        r"как\w*\s+план\w*\s+(?:счетов|видов\s+расч)",
        sp,
    ) and (mtype or camel_names(raw) or ctx) and not re.search(r"\bсколько\b|\bсумм\w*|\bза\s+(?:\d{4}|период|месяц|год)", sp):
        args = {"request": raw}
        if ctx and not camel_names(raw) and not mtype:
            args["full_name"] = ctx
        return _d("describe_object", args, 0.9, "свойства объекта метаданных")
    # описание конкретного объекта
    if re.search(
        r"^(?:а\s+)?(?:опиши|описание|что\s+за|что\s+такое|что\s+хранится\s+в|для\s+чего\s+(?:нужен|нужна|нужно)|"
        r"зачем\s+нужен|состав\s+|структур\w*\s+)|"
        r"значени\w*\s+(?:у\s+)?перечислен|какие\s+(?:статусы|значения|варианты|состояния)\s+(?:бывают|есть|может|у)|какие\s+бывают\s+(?:статусы|значения|варианты|состояния)|"
        r"состав\s+подсистем|что\s+входит\s+в",
        sp,
    ) and (mtype or camel_names(raw) or re.search(r"статус|значени|вариант", sp)):
        args: dict[str, Any] = {"request": raw}
        if ctx and not camel_names(raw) and not mtype:
            args["full_name"] = ctx
        return _d("describe_object", args, 0.9, "описание объекта метаданных")
    # обратный вопрос: «какие документы делают движения по регистру X»
    if re.search(r"какие\s+документ\w*\s+(?:делают|дела\w+|пишут|формируют|двигают)\s+(?:движени|по\s+регистр|в\s+регистр)", sp):
        return _d("describe_object", {"request": raw}, 0.88, "регистраторы регистра")
    return None


def _det_movements(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    if re.search(
        r"в\s+как\w+\s+регистр\w*\s+(?:пишет|двигает|делает|формирует)|"
        r"(?:все\s+)?регистр\w*\s+(?:документа|у\s+документа)|какие\s+регистры\s+(?:двигает|пишет|затрагивает)|"
        r"что\s+меняется\s+в\s+учет|какие\s+движени|движени\w*\s+(?:документа|при\s+проведени)|"
        r"(?:провод\w*|документ\w*)\s+без\s+движени|не\s+(?:уменьшил|увеличил|изменил)\w*\s+остат|после\s+проведени\w*\s+не\s|"
        r"что\s+делает\s+проведени|"
        r"куда\s+пишет\s+при\s+проведени",
        sp,
    ):
        if re.search(r"блокир|оптимиз|тормоз", sp):
            return None
        return _d("analyze_object_movements", {"request": raw}, 0.9, "движения документа по регистрам")
    return None


def _det_find(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    if re.search(r"в\s+коде|процедур|функци|модул\w*\s+(?:объекта|менеджера|формы)", sp) and not re.search(r"найди\s+общ\w+\s+модул", sp):
        return None
    if re.search(
        r"где\s+(?:в\s+(?:программе|1с|базе|системе)\s+)?(?:хранится|хранятся|храним|завести|заводить|создать|оформ\w*|"
        r"делать|делается|ввести|вводить|найти|посмотреть|смотреть|настраива\w*|указыва\w*)|"
        r"(?:каким|какой|какие)\s+документ\w*\s+(?:оформля\w*|для|нужен|отража\w*|провод\w*)|"
        r"в\s+каком\s+(?:документе|справочнике|регистре|отчете)|"
        r"какой\s+(?:документ|отчет|справочник|регистр|обработк\w*)\s+(?:для|показывает|отвечает|нужен|отража\w*)|"
        r"как\s+называется\s+(?:документ|отчет|справочник|регистр|обработк)|"
        r"(?:^|\s)найди\s+(?:объект|регистр|справочник|документ|отчет|обработк|перечислени|общ\w+\s+модул|констант)|"
        r"какие\s+(?:отчеты|обработки|документы|справочники|регистры)\s+(?:есть\s+)?(?:по|для|связан\w*|про|о)\s",
        sp,
    ):
        return _d("resolve_object", {"request": raw, "object_type": "Все", "limit": 12}, 0.86, "поиск объекта по бизнес-фразе")
    if re.search(r"^где\s+в\s+(?:1с|программе|базе|системе)\s+[а-яё\s]{3,60}\??$", sp):
        return _d("resolve_object", {"request": raw, "object_type": "Все", "limit": 12}, 0.85, "поиск объекта по бизнес-фразе")
    if re.search(r"^[а-яё\s]{3,40}\s+где\s+(?:делать|оформлять|смотреть|найти)\s*\??$", sp):
        return _d("resolve_object", {"request": raw, "object_type": "Все", "limit": 12}, 0.85, "поиск объекта по бизнес-фразе")
    return None


def _det_fill(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    if re.search(r"в\s+коде|в\s+конфигурац\w*\s+(?:получа|рассчит|определ)", sp):
        return None
    why = re.search(r"(?:^|\s)(?:почему|отчего|из-за\s+чего|с\s+чего)\b", sp)
    if why and re.search(
        r"подставил|подставля|не\s+перен[её]с|не\s+перенесл|не\s+пересчита|не\s+заполн|не\s+подтянул|"
        r"пуст(?:ой|ое|ая|ые|ым)|не\s+то\s+значение|другое\s+значение|а\s+не\s|сбрасывается|обнуляется",
        sp,
    ):
        return _d("explain_fill", {"request": raw}, 0.9, "почему значение поля такое / пустое")
    if re.search(r"как\s+заполняется|откуда\s+(?:берется|подставляется|берут)\s+(?:значени|реквизит|поле)|чем\s+заполняется", sp):
        return _d("explain_fill", {"request": raw}, 0.9, "как заполняется поле")
    return None


def _det_code(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    if re.search(
        r"где\s+в\s+код|в\s+коде|"
        r"как\s+(?:рассчитыва\w*|вычисля\w*|считается|определя\w*|формиру\w*|получа\w*|устроен\w*|реализован\w*)|"
        r"где\s+(?:задается|задаются|проверяется|проверяются|рассчитыва\w*|формиру\w*|определя\w*|обрабатыва\w*|устанавлива\w*)|"
        r"что\s+происходит\s+(?:при|когда|после)|делени\w*\s+на\s+(?:ноль|0)|"
        r"отрицательн\w*\s+остат\w*\s+(?:после|при)\s+проведени|в\s+как\w+\s+(?:процедур|модул)|"
        r"в\s+конфигурации\s+(?:получа|рассчит|определ)",
        sp,
    ) and not re.search(r"(?<![а-яё])(?:сколько|сумма\s+продаж|выручк)", sp):
        return _d("inspect_code", {"request": raw, "max_hits": 12}, 0.88, "логика в коде конфигурации")
    # класс «неочевидная ошибка в отчёте/учёте»: задваивается / не сходится / отрицательный
    if re.search(r"задваива|задвоени|дублиру\w*\s+(?:строк|сумм)", sp) or (
        re.search(r"почему|из-за\s+чего|отчего", sp)
        and re.search(r"не\s+сходится|не\s+совпада|отлича\w*\s+от|отрицательн|расхожд", sp)
    ):
        return _d("inspect_code", {"request": raw, "max_hits": 12}, 0.86, "расхождение/задвоение — разбор логики")
    # текст ошибки платформы/конфигурации
    if re.search(r"ошибк\w*\s*[:'\"«]|ошибк\w*\s+[A-ZА-ЯЁ]|пишет\s+(?:ошибку\s+)?[\"'«]|текст\s+ошибк|исключени\w*\s*[:'\"«]", raw):
        if re.search(r"не\s+хватает|недостаточно|превышен|остатк", sp):
            return None  # контроль остатков/лимитов — explain_fill/posting существующее правило
        return _d("inspect_code", {"request": raw, "max_hits": 12}, 0.87, "текст ошибки — поиск места в коде")
    return None


def _det_ops(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    if re.search(
        r"кто\s+(?:изменил|удалил|создал|провел|правил|менял|поменял)|история\s+изменени|"
        r"что\s+было\s+в\s+(?:лог|журнал)|(?<![а-яё])лог\w*\s+(?:за|вчера|сегодня|по\s+пользовател)|"
        r"журнал\w*\s+(?:за|вчера|сегодня)",
        sp,
    ):
        return _d("analyze_event_log", {"request": raw}, 0.9, "журнал регистрации")
    if re.search(r"новеньк|с\s+чего\s+начать|что\s+ты\s+умеешь|чем\s+(?:ты\s+)?можешь\s+помочь|как\s+тобой\s+пользоваться", sp):
        return _d("agent_capabilities", {"short": False}, 0.92, "что умеет агент")
    return None


def _det_posting_msg(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    """«при проведении пишет недостаточно / не заполнено поле» — сообщение проверки при проведении."""
    if re.search(r"провод|проведени|записат|записи", sp) and re.search(
        r"(?:пишет|выдает|ругается|сообщени\w*|ошибк\w*)[^.]{0,40}(?:недостаточно|не\s+хватает|не\s+заполнен|превышен|не\s+указан)", sp
    ):
        return _d("explain_fill", {"request": raw}, 0.88, "сообщение проверки при проведении/записи")
    return None


def _det_sessions_jobs(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    if re.search(r"кто\s+(?:сейчас\s+)?(?:работает|сидит)\s+в\s+(?:базе|1с|программе)|активн\w+\s+сеанс|список\s+сеанс", sp):
        return _d("list_active_sessions", {}, 0.92, "активные сеансы", "manager")
    if re.search(r"регламентн\w*\s+задани", pl) and not _is_class_listing(sp):
        return _d("describe_scheduled_job", {"request": raw, "limit": 30}, 0.9, "регламентное задание")
    return None


def _det_export_list(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    """«выгрузи номенклатуру / список контрагентов в excel» — список элементов справочника/документов."""
    m = re.search(
        r"^(?:выгрузи|выгрузить|скачай|экспортируй|сохрани|сделай)\s+(?:мне\s+)?(?:весь\s+|всю\s+|все\s+|список\s+|перечень\s+)?"
        r"([а-яё ]{3,40}?)\s+в\s+(?:excel|эксель|ексель|xlsx|файл)|"
        r"^(?:список|перечень)\s+([а-яё ]{3,40}?)\s+в\s+(?:excel|эксель|ексель|xlsx)",
        sp,
    )
    if not m:
        return None
    what = (m.group(1) or m.group(2) or "").strip()
    if re.search(r"продаж|выручк|остат|долг|оборот|за\s+\d{4}|период|месяц|год|отчет", what + " " + sp):
        return None  # это отчёт, не список элементов
    try:
        from tools.object_catalog import resolve_data_object

        rd = resolve_data_object(what)
    except Exception:
        return None
    fn = rd.get("full_name") or ""
    if not fn.startswith(("Справочник.", "Документ.", "ПланВидовХарактеристик.")):
        return None
    return _d("export_object_excel", {"full_name": fn, "mode": "list", "limit": 2000}, 0.9, f"выгрузка списка {fn}", "manager")


def _det_data(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    if re.search(r"кто\s+(?:нам\s+)?(?:долж|не\s+заплатил|не\s+оплатил)|должник|дебитор|кому\s+(?:мы\s+)?должны|кредитор", sp):
        return _d("linked_query", {"request": raw, "execute": True, "limit": 40}, 0.9, "задолженность", "manager")
    if re.search(
        r"не\s+продавал|не\s+было\s+продаж|перестал\w*\s+(?:покупать|брать|заказывать)|без\s+движени\w*|залежал\w*|неликвид\w*|"
        r"плохо\s+прода\w*|давно\s+(?:ничего\s+)?не\s+(?:покупал|заказывал|брал)\w*|"
        r"(?:лучш|худш|самы\w+\s+(?:крупн|выгодн))\w*\s+(?:поставщик|клиент|покупател|товар|менеджер)|отток\s+клиент",
        sp,
    ):
        return _d("analyst_report", {"request": raw, "limit": 80}, 0.88, "аналитика клиентов/ассортимента", "manager")
    if re.search(r"самы\w+\s+(?:прибыльн|выгодн|маржинальн|убыточн)|рентабельн", sp):
        return _d("margin_report", {"request": raw, "period": "", "limit": 40}, 0.88, "прибыльность", "manager")
    if re.search(
        r"(?:больше|меньше)\s+всех\s+(?:купил|продал|заказал|потратил)|проданн\w+\s+(?:единиц|товар|штук)|"
        r"самы\w+\s+(?:продаваем|популярн|ходов)|сколько\s+(?:штук|единиц)\s+продал",
        sp,
    ):
        return _d("build_report", {"request": raw}, 0.86, "продажи (агрегат)", "manager")
    if re.search(r"(?:текущ\w*|действующ\w*|актуальн\w*)\s+цен|прайс", sp):
        return _d("linked_query", {"request": raw, "execute": True, "limit": 40}, 0.86, "срез цен", "manager")
    if re.search(r"вчера|сегодня|на\s+этой\s+неделе|за\s+(?:эту|прошлую)\s+неделю|за\s+прошлый\s+месяц|за\s+этот\s+месяц", sp) and re.search(
        r"продал|продаж|выручк|отгрузил|отгрузк|заказ|оплат|поступил", sp
    ):
        return _d("build_report", {"request": raw}, 0.86, "данные за относительный период", "manager")
    return None


def _det_attr(raw: str, pl: str, sp: str, ctx: str) -> dict | None:
    if re.search(r"помечен\w*\s+на\s+удалени|с\s+пометкой\s+удалени", sp) and not re.search(r"удали|как\s", sp):
        return _d("query_by_attributes", {"request": raw, "limit": 50}, 0.9, "список по пометке удаления", "manager")
    if re.search(r"(?:у\s+котор\w+|где|в\s+котор\w+)\s+(?:не\s+заполнен\w*|не\s+указан\w*|пуст\w*)|в\s+статусе|со\s+статусом", sp) and not re.search(
        r"почему|отчего|как\s", sp
    ):
        return _d("query_by_attributes", {"request": raw, "limit": 50}, 0.9, "список по значению/пустоте реквизита", "manager")
    # «какие <документы> клиента «Имя»» — список по значению реквизита
    if re.search(r"^(?:какие|все|покажи)\s+[а-яё]+\s+(?:клиента|контрагента|поставщика|партнера|покупателя)\s+\S", sp) and re.search(
        r"(?:клиента|контрагента|поставщика|партнера|покупателя)\s+[«\"']?[А-ЯЁA-Z]", raw
    ):
        return _d("query_by_attributes", {"request": raw, "limit": 50}, 0.88, "список по значению реквизита", "manager")
    return None


# порядок = приоритет (специфичное → общее)
DETECTORS: tuple[Callable[[str, str, str, str], dict | None], ...] = (
    _det_build_epf,
    _det_mutation,
    _det_docnum,
    _det_sessions_jobs,
    _det_platform_help,
    _det_security,
    _det_query_audit,
    _det_locks,
    _det_subscriptions,
    _det_skd,
    _det_calls,
    _det_print,
    _det_form,
    _det_access,
    _det_ops,
    _det_meta,
    _det_movements,
    _det_posting_msg,
    _det_fill,
    _det_code,
    _det_find,
    _det_export_list,
    _det_data,
    _det_attr,
)


def detect(phrase: str, *, context_full_name: str = "") -> dict[str, Any] | None:
    raw = (phrase or "").strip()
    if len(raw) < 3:
        return None
    pl = _low(raw)
    sp = _speech(raw)
    for det in DETECTORS:
        try:
            d = det(raw, pl, sp, context_full_name or "")
        except Exception:
            d = None
        if d:
            return d
    return None
