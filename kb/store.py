"""
Загрузка папки знаний (русский, простой).
Поддерживает новые русские имена файлов и старые латинские (если остались).
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_KNOWLEDGE = _PROJECT_ROOT / "knowledge"

# русские ключи действий → внутренние (для кода)
_ACTION_KEY_MAP = {
    "продажа": "sell",
    "закупка": "buy",
    "оплата": "pay",
    "заказ": "order",
    "счет": "invoice",
    "счёт": "invoice",
    "возврат": "return",
    "накладная": "waybill",
    "склад": "stock",
    "оборот": "turn",
    "услуга": "service",
    "товар": "goods",
    "клиент": "client",
    "поставщик": "vendor",
    # если уже английские
    "sell": "sell",
    "buy": "buy",
    "pay": "pay",
    "order": "order",
    "invoice": "invoice",
    "return": "return",
    "waybill": "waybill",
    "stock": "stock",
    "turn": "turn",
    "service": "service",
    "goods": "goods",
    "client": "client",
    "vendor": "vendor",
}


def knowledge_dir() -> Path:
    env = (os.environ.get("RAG_KNOWLEDGE_DIR") or "").strip()
    if env:
        return Path(env)
    try:
        from config import load_config

        cfg = load_config()
        kd = (cfg.get("knowledge_dir") or cfg.get("paths", {}).get("knowledge_dir") or "").strip()
        if kd:
            return Path(kd)
    except Exception:
        pass
    return _DEFAULT_KNOWLEDGE


def _first_existing(*names: str) -> Path | None:
    root = knowledge_dir()
    for n in names:
        p = root / n
        if p.is_file():
            return p
    return None


def _read_yaml(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def _read_text(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def reload_knowledge() -> None:
    for fn in (
        get_business_vocab,
        get_field_roles,
        get_agent_rules,
        get_query_syntax,
        get_query_functions,
        get_query_antipatterns,
        get_code_help_book,
        get_query_examples_book,
        get_manager_help,
        _load_chunks,
        _platform_index,
    ):
        try:
            fn.cache_clear()  # type: ignore[attr-defined]
        except Exception:
            pass


def _as_str_list(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x) for x in v]


@lru_cache(maxsize=1)
def get_business_vocab() -> dict[str, Any]:
    raw = _read_yaml(
        _first_existing("словарь_бизнеса.yaml", "business_vocab.yaml")
    )
    intents_in = raw.get("намерения") or raw.get("intents") or {}
    actions_in = raw.get("действия") or raw.get("actions") or {}
    boosts_in = raw.get("усиления") or raw.get("phrase_boosts") or []
    special_in = raw.get("узкие_документы") or raw.get("specialized_docs") or []

    intents = {
        str(k): _as_str_list(v)
        for k, v in intents_in.items()
        if isinstance(v, list)
    }

    actions: dict[str, list[str]] = {}
    for k, v in actions_in.items():
        if not isinstance(v, list):
            continue
        key = _ACTION_KEY_MAP.get(str(k), str(k))
        actions[key] = _as_str_list(v)

    phrase_boosts: list[tuple[tuple[str, ...], tuple[str, ...], int]] = []
    for item in boosts_in:
        if not isinstance(item, dict):
            continue
        phrase = tuple(
            _as_str_list(item.get("фраза") or item.get("phrase"))
        )
        name = tuple(_as_str_list(item.get("имя") or item.get("name")))
        score = int(item.get("баллы") or item.get("score") or 0)
        if phrase and name:
            phrase_boosts.append((phrase, name, score))

    specialized: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for item in special_in:
        if not isinstance(item, dict):
            continue
        specialized.append(
            (
                tuple(_as_str_list(item.get("имя") or item.get("name"))),
                tuple(_as_str_list(item.get("фраза") or item.get("phrase"))),
            )
        )

    return {
        "intents": intents,
        "actions": actions,
        "phrase_boosts": phrase_boosts,
        "specialized_docs": specialized,
        "doc_action_words": _as_str_list(
            raw.get("слова_действий_документа") or raw.get("doc_action_words")
        ),
        "noise_substrings": _as_str_list(
            raw.get("шум_в_именах") or raw.get("noise_substrings")
        ),
        "source": str(
            _first_existing("словарь_бизнеса.yaml", "business_vocab.yaml")
            or knowledge_dir() / "словарь_бизнеса.yaml"
        ),
    }


@lru_cache(maxsize=1)
def get_field_roles() -> dict[str, Any]:
    raw = _read_yaml(
        _first_existing("реквизиты_1с.yaml", "field_roles.yaml")
    )
    roles: dict[str, list[str]] = {}
    descriptions: dict[str, str] = {}
    query_examples: dict[str, str] = {}

    # русское имя реквизита в yaml → внутренний ключ программы
    ru_rek_to_prog = {
        "номер": "number",
        "дата": "date",
        "проведен": "posted",
        "пометка_удаления": "deletion_mark",
        "сумма": "amount",
        "контрагент": "counterparty",
        "организация": "organization",
        "склад": "warehouse",
        "договор": "contract",
        "валюта": "currency",
        "ответственный": "responsible",
        "комментарий": "comment",
        "статус": "status",
        "код": "code",
        "наименование": "description",
        "артикул": "article",
        "инн": "inn",
        "кпп": "kpp",
        "это_группа": "is_folder",
        "номенклатура": "nomenclature",
        "количество": "quantity",
        "цена": "price",
        "подразделение": "department",
        "сделка": "deal",
        "ндс": "vat",
        "оплата": "payment",
        "ожидаемая_дата": "expected_date",
    }
    rek = raw.get("реквизиты") or raw.get("roles") or {}
    if isinstance(rek, dict):
        for key, val in rek.items():
            if isinstance(val, dict):
                prog_key = ru_rek_to_prog.get(str(key), str(val.get("ключ") or key))
                hints = val.get("искать_по") or val.get("hints") or []
                roles[prog_key] = _as_str_list(hints)
                if val.get("что_это"):
                    descriptions[prog_key] = str(val["что_это"])
                if val.get("в_запросе"):
                    query_examples[prog_key] = str(val["в_запросе"])
            elif isinstance(val, list):
                prog_key = ru_rek_to_prog.get(str(key), str(key))
                roles[prog_key] = _as_str_list(val)

    # порядок шапки: русские имена → ключи программы
    header_raw = (
        raw.get("шапка_документа")
        or raw.get("document_header_roles")
        or []
    )
    ru_to_prog = {
        "номер": "number",
        "дата": "date",
        "проведен": "posted",
        "организация": "organization",
        "контрагент": "counterparty",
        "договор": "contract",
        "склад": "warehouse",
        "сумма": "amount",
        "валюта": "currency",
        "ответственный": "responsible",
        "комментарий": "comment",
        "статус": "status",
    }
    header: list[str] = []
    for x in header_raw:
        s = str(x)
        header.append(ru_to_prog.get(s, s))

    return {
        "roles": roles,
        "document_header_roles": header,
        "descriptions": descriptions,
        "query_examples": query_examples,
        "source": str(
            _first_existing("реквизиты_1с.yaml", "field_roles.yaml")
            or knowledge_dir() / "реквизиты_1с.yaml"
        ),
    }


@lru_cache(maxsize=1)
def get_agent_rules(*, context_window: int = 32000, export_base: str = "", full: bool | None = None) -> str:
    """
    Правила runtime-агента. По умолчанию — краткие (правила_агента_кратко.md, ~4 тыс. симв.):
    роутер/preflight уже выбирает tool, длинная таблица только съедает контекст 27B-модели.
    agent.rules: full в config.yaml (или full=True) — полный правила_агента.md.
    """
    if full is None:
        try:
            from config import load_config

            full = str((load_config().get("agent") or {}).get("rules") or "short").lower() == "full"
        except Exception:  # noqa: BLE001
            full = False
    names = ("правила_агента.md", "agent_rules.md") if full else ("правила_агента_кратко.md", "правила_агента.md", "agent_rules.md")
    text = _read_text(_first_existing(*names))
    if not text.strip():
        text = (
            "Помощник по 1С. Отчёты: build_report с фразой целиком. "
            "Не выдумывай имена документов. Отвечай по-русски."
        )
    text = text.replace("{размер_контекста}", str(context_window))
    text = text.replace("{context_window}", str(context_window))  # совместимость
    if not export_base:
        from tools.excel_export import export_base_url

        export_base = export_base_url()
    base = export_base
    text = text.replace("{ссылка_на_файлы}", base)
    text = text.replace("{export_base}", base)
    return (text.strip() + _audience_block()).strip()


def _audience_block() -> str:
    """Режим ответа из config.yaml (agent.audience): developer | user | both."""
    try:
        from config import load_config

        audience = str((load_config().get("agent") or {}).get("audience") or "both").lower()
    except Exception:  # noqa: BLE001
        audience = "both"
    dev = _read_text(_first_existing("режим_программист.md")).strip()
    usr = _read_text(_first_existing("режим_пользователь.md")).strip()
    sep = chr(10) * 2
    if audience == "developer":
        return sep + dev if dev else ""
    if audience == "user":
        return sep + usr if usr else ""
    both = (
        sep + "## Режим ответа: определяй по вопросу" + chr(10)
        + "Вопрос про код, модули, обработчики, запросы, блокировки — отвечай как программисту. "
        + "Вопрос «где найти», «как сделать», «что значит поле» — отвечай как пользователю."
    )
    return both + (sep + dev if dev else "") + (sep + usr if usr else "")


def _normalize_sections(raw: dict[str, Any]) -> dict[str, Any]:
    """разделы/sections + синонимы → sections с title/content/example."""
    secs_in = raw.get("разделы") or raw.get("sections") or {}
    aliases_in = raw.get("синонимы_тем") or raw.get("aliases") or {}
    sections: dict[str, dict[str, str]] = {}
    for k, v in secs_in.items():
        if not isinstance(v, dict):
            continue
        sections[str(k)] = {
            "title": str(v.get("заголовок") or v.get("title") or k),
            "content": str(v.get("текст") or v.get("content") or ""),
            "example": str(v.get("пример") or v.get("example") or ""),
        }
    aliases = {str(k): str(v) for k, v in aliases_in.items()}
    return {"sections": sections, "aliases": aliases}


@lru_cache(maxsize=1)
def get_query_functions() -> dict[str, Any]:
    """
    Полный каталог функций конструктора запросов 1С.
    knowledge/функции_запросов.yaml
    """
    raw = _read_yaml(
        _first_existing("функции_запросов.yaml", "query_functions.yaml")
    )
    if not raw:
        return {"source": "", "groups": {}, "all_names": []}
    groups: dict[str, list[Any]] = {}
    all_names: list[str] = []
    key_map = {
        "агрегатные": "aggregate",
        "строки": "string",
        "даты": "date",
        "числа": "number",
        "прочие_функции": "misc",
        "арифметика": "arithmetic",
        "логика": "logic",
        "прочие_операторы": "operators",
        "конструкторы": "constructors",
        "aggregate": "aggregate",
        "string": "string",
        "date": "date",
        "number": "number",
        "misc": "misc",
    }
    for rk, en in key_map.items():
        if rk not in raw:
            continue
        val = raw[rk]
        groups[en] = val if isinstance(val, list) else val
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict) and item.get("имя"):
                    all_names.append(str(item["имя"]))
                elif isinstance(item, str):
                    all_names.append(item)
    # nested logic lists
    logic = raw.get("логика") or {}
    if isinstance(logic, dict):
        groups["logic"] = logic
        for sp in logic.get("спец") or []:
            if isinstance(sp, dict) and sp.get("имя"):
                all_names.append(str(sp["имя"]))
    # unique preserve order
    seen: set[str] = set()
    uniq = []
    for n in all_names:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return {
        "source": str(
            _first_existing("функции_запросов.yaml", "query_functions.yaml") or ""
        ),
        "source_note": raw.get("источник") or "",
        "groups": groups,
        "priority": raw.get("приоритет_builders") or raw.get("priority") or {},
        "rules": raw.get("правила") or raw.get("rules") or [],
        "all_names": uniq,
        "raw": raw,
    }


@lru_cache(maxsize=1)
def get_query_antipatterns() -> dict[str, Any]:
    """
    Каталог антипаттернов запросов 1С для аудита кода.
    knowledge/антипаттерны_запросов.yaml
    """
    path = _first_existing(
        "антипаттерны_запросов.yaml",
        "query_antipatterns.yaml",
    )
    raw = _read_yaml(path)
    if not raw:
        return {
            "source": str(path or ""),
            "patterns": [],
            "how_better": [],
            "response_rules": [],
        }
    patterns_in = raw.get("антипаттерны") or raw.get("antipatterns") or []
    patterns: list[dict[str, Any]] = []
    for item in patterns_in:
        if not isinstance(item, dict):
            continue
        patterns.append(
            {
                "id": str(item.get("id") or ""),
                "severity": str(item.get("severity") or "medium"),
                "title": str(item.get("title") or item.get("заголовок") or ""),
                "match_scope": str(
                    item.get("match_scope") or item.get("область") or "query"
                ),
                "detect": item.get("detect") or item.get("детект") or [],
                "detect_all": item.get("detect_all") or item.get("детект_все") or [],
                "detect_none": item.get("detect_none")
                or item.get("детект_нет")
                or [],
                "detect_code": str(
                    item.get("detect_code") or item.get("детект_код") or ""
                ),
                "bad": str(item.get("плохо") or item.get("bad") or ""),
                "better": str(item.get("лучше") or item.get("better") or ""),
                "why": str(item.get("почему") or item.get("why") or ""),
                "recommendation": str(
                    item.get("рекомендация") or item.get("recommendation") or ""
                ),
            }
        )
    how = raw.get("как_лучше") or raw.get("how_better") or []
    rules = raw.get("правила_ответа") or raw.get("response_rules") or []
    return {
        "source": str(path or ""),
        "source_note": raw.get("источник") or raw.get("source") or "",
        "version": raw.get("версия") or raw.get("version"),
        "patterns": patterns,
        "how_better": [str(x) for x in how] if isinstance(how, list) else [],
        "response_rules": [str(x) for x in rules] if isinstance(rules, list) else [],
        "raw": raw,
    }


@lru_cache(maxsize=1)
def get_query_syntax() -> dict[str, Any]:
    raw = _read_yaml(
        _first_existing("синтаксис_запросов.yaml", "query_syntax.yaml")
    )
    norm = _normalize_sections(raw)
    # английские алиасы → русские разделы (если модель передаст select)
    extra = {
        "select": "выбрать",
        "from": "из",
        "where": "где",
        "dates": "даты",
        "join": "соединение",
        "group": "группировка",
        "document": "документ",
        "catalog": "справочник",
        "errors": "ошибки",
        "examples": "примеры",
        "oneline": "одна_строка",
        "overview": "обзор",
        "virtual": "регистры",
        "presentation": "представление",
        "functions": "функции",
        "функции": "функции",
        "aggregate": "функции",
        "string": "функции",
        "date_fn": "функции",
    }
    for a, t in extra.items():
        if t in norm["sections"] and a not in norm["aliases"]:
            norm["aliases"][a] = t
        elif t == "функции":
            # раздел может добавиться ниже
            norm["aliases"][a] = "функции"
    path = _first_existing("синтаксис_запросов.yaml", "query_syntax.yaml")
    norm["source"] = str(path or "")
    # вложить каталог функций (полный список из конструктора)
    try:
        fn = get_query_functions()
        if fn.get("all_names"):
            if "функции" not in norm["sections"]:
                norm["sections"]["функции"] = {
                    "title": "Функции языка запросов (конструктор)",
                    "content": (
                        "Полный список функций 1С из конструктора запросов. "
                        "Агрегаты: СУММА, МИНИМУМ, МАКСИМУМ, СРЕДНЕЕ, КОЛИЧЕСТВО, "
                        "КОЛИЧЕСТВО(РАЗЛИЧНЫЕ). "
                        "Строки: СТРОКА, ЛЕВ, ПРАВ, ВРЕГ, НРЕГ, ПОДСТРОКА, СОКРЛП, СТРНАЙТИ, СТРЗАМЕНИТЬ. "
                        "Даты: ГОД, КВАРТАЛ, МЕСЯЦ, ДЕНЬ, НАЧАЛОПЕРИОДА, КОНЕЦПЕРИОДА, "
                        "ДОБАВИТЬКДАТЕ, РАЗНОСТЬДАТ, … "
                        "Прочие: ЕСТЬNULL, ПРЕДСТАВЛЕНИЕ, ПРЕДСТАВЛЕНИЕССЫЛКИ, ТИПЗНАЧЕНИЯ, "
                        "УНИКАЛЬНЫЙИДЕНТИФИКАТОР. "
                        "Операторы: ВЫБОР…КОНЕЦ, ВЫРАЗИТЬ, ДАТАВРЕМЯ, ЗНАЧЕНИЕ, ТИП. "
                        "Не ANSI SQL. Имена полей — из structure."
                    ),
                    "example": (
                        "ВЫБРАТЬ ПЕРВЫЕ 10 ПРЕДСТАВЛЕНИЕ(Т.<СсылкаИзStructure>) КАК Ссылка, "
                        "ЕСТЬNULL(Т.<СуммаИзStructure>, 0) КАК Сумма, "
                        "НАЧАЛОПЕРИОДА(Т.Дата, МЕСЯЦ) КАК Мес "
                        "ИЗ Документ.<ИмяИзFindMetadata> КАК Т "
                        "ГДЕ Т.Дата МЕЖДУ ДАТАВРЕМЯ(<г>,1,1,0,0,0) И ДАТАВРЕМЯ(<г>,12,31,23,59,59)"
                    ),
                }
            norm["query_functions"] = {
                "all_names": fn.get("all_names"),
                "priority": fn.get("priority"),
                "rules": fn.get("rules"),
                "source": fn.get("source_note") or fn.get("source"),
            }
    except Exception:
        pass
    return norm


@lru_cache(maxsize=1)
def get_code_help_book() -> dict[str, Any]:
    raw = _read_yaml(
        _first_existing("справка_по_коду.yaml", "code_help.yaml")
    )
    norm = _normalize_sections(raw)
    extra = {
        "bsl": "код",
        "query": "запрос",
        "selection": "выборка",
        "object": "объект",
        "register": "регистр",
        "form": "форма",
        "http": "http",
        "error": "ошибка",
        "module": "модуль",
        "export": "выгрузка",
        "best": "практика",
        "overview": "обзор",
    }
    for a, t in extra.items():
        if t in norm["sections"] and a not in norm["aliases"]:
            norm["aliases"][a] = t
    path = _first_existing("справка_по_коду.yaml", "code_help.yaml")
    norm["source"] = str(path or "")
    return norm


@lru_cache(maxsize=1)
def get_query_examples_book() -> dict[str, Any]:
    raw = _read_yaml(
        _first_existing("примеры_запросов.yaml", "query_examples.yaml")
    )
    # поля по умолчанию: русские → для шаблонов
    fields_ru = raw.get("поля_по_умолчанию") or raw.get("default_roles") or {}
    # совместимость: number/date для старого fill
    ru_to_en = {
        "номер": "number",
        "дата": "date",
        "контрагент": "counterparty",
        "сумма": "amount",
        "проведен": "posted",
        "код": "code",
        "наименование": "name",
        "пометка_удаления": "deletion",
        "номенклатура": "line_item",
        "количество": "qty",
        "сумма_строки": "line_amount",
        "табличная_часть": "tabular",
    }
    default_roles: dict[str, str] = {}
    for k, v in fields_ru.items():
        sk, sv = str(k), str(v)
        default_roles[sk] = sv
        en = ru_to_en.get(sk, sk)
        default_roles[en] = sv

    # шаблоны
    cookbook_in = raw.get("шаблоны") or raw.get("cookbook") or []
    cookbook: list[dict[str, Any]] = []
    for item in cookbook_in:
        if not isinstance(item, dict):
            continue
        tpl = str(
            item.get("шаблон")
            or item.get("query_template")
            or item.get("query")
            or ""
        )
        # русские плейсхолдеры → английские для fill_template
        for ru, en in {
            "номер": "number",
            "дата": "date",
            "контрагент": "counterparty",
            "сумма": "amount",
            "проведен": "posted",
            "код": "code",
            "наименование": "name",
            "пометка_удаления": "deletion",
            "номенклатура": "line_item",
            "количество": "qty",
            "сумма_строки": "line_amount",
            "табличная_часть": "tabular",
            "документ": "document",
            "справочник": "catalog",
        }.items():
            tpl = tpl.replace("{{" + ru + "}}", "{{" + en + "}}")
            tpl = tpl.replace("{" + ru + "}", "{" + en + "}")
        tpl = re.sub(r"\s+", " ", tpl).strip()
        cookbook.append(
            {
                "id": str(item.get("ид") or item.get("id") or ""),
                "scenario": str(
                    item.get("ид") or item.get("scenario") or item.get("id") or ""
                ),
                "when": str(item.get("когда") or item.get("when") or ""),
                "user": str(item.get("фраза") or item.get("user") or ""),
                "query_template": tpl,
                "note": str(item.get("примечание") or item.get("note") or ""),
                "prefer_tool": "execute_query",
                "bad": _as_str_list(item.get("не_делать") or item.get("bad")),
            }
        )

    rules = _as_str_list(raw.get("правила") or raw.get("rules"))
    return {
        "default_roles": default_roles,
        "cookbook": cookbook,
        "rules": rules,
        "source": str(
            _first_existing("примеры_запросов.yaml", "query_examples.yaml") or ""
        ),
    }


@lru_cache(maxsize=1)
def get_manager_help() -> dict[str, Any]:
    raw = _read_yaml(
        _first_existing("карта_помощника.yaml", "manager_help.yaml")
    )
    if not raw:
        return {
            "principle": "Отчёты: build_report с фразой пользователя.",
            "pipeline": ["build_report"],
        }
    # нормализовать русские ключи → то, что ждёт код/модель (можно оставить русские)
    out: dict[str, Any] = {
        "principle": str(raw.get("главное") or raw.get("principle") or ""),
        "clarify_when": _as_str_list(
            raw.get("когда_спросить") or raw.get("clarify_when")
        ),
        "universal_rule": _as_str_list(raw.get("правила") or raw.get("universal_rule")),
        "pipeline": _as_str_list(raw.get("шаги") or raw.get("pipeline")),
        "scenarios": [],
        "tools_by_layer": raw.get("tools_by_layer") or {},
        "object_kinds": _as_str_list(raw.get("object_kinds")),
    }
    for sit in raw.get("ситуации") or raw.get("scenarios") or []:
        if not isinstance(sit, dict):
            continue
        out["scenarios"].append(
            {
                "when": str(sit.get("когда") or sit.get("when") or ""),
                "steps": _as_str_list(sit.get("шаги") or sit.get("steps")),
                "do_not": str(sit.get("не_делать") or sit.get("do_not") or ""),
            }
        )
    return out


# ----- mini-RAG -----


def _split_md_chunks(path: Path, text: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    parts = re.split(r"(?m)^(#{1,3}\s+.+)$", text)
    if len(parts) == 1:
        body = text.strip()
        if body:
            chunks.append(
                {"id": path.name, "title": path.stem, "path": str(path), "text": body}
            )
        return chunks
    if parts[0].strip():
        chunks.append(
            {
                "id": f"{path.name}:intro",
                "title": path.stem,
                "path": str(path),
                "text": parts[0].strip(),
            }
        )
    i = 1
    while i < len(parts):
        heading = parts[i].strip().lstrip("#").strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if body or heading:
            chunks.append(
                {
                    "id": f"{path.name}:{heading[:40]}",
                    "title": heading or path.stem,
                    "path": str(path),
                    "text": f"{heading}\n\n{body}".strip(),
                }
            )
        i += 2
    return chunks


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """md с блоком `---\nkey: value\n---` в начале → (метаданные, текст без блока)."""
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for ln in m.group(1).splitlines():
        k, sep, v = ln.partition(":")
        if sep and k.strip():
            meta[k.strip().lower()] = v.strip()
    return meta, text[m.end():]


# множитель score для документации с priority: low (должен совпадать с vector/search.py)
_PRIORITY_WEIGHT = {"low": 0.85}


def _is_platform_help(path: Path) -> bool:
    return "справка_платформы" in path.parts


@lru_cache(maxsize=1)
def _load_chunks() -> tuple[dict[str, Any], ...]:
    """
    Чанки для поиска. Большую справку платформы (десятки тысяч файлов)
    не грузим целиком — только README и небольшие разделы.
    """
    root = knowledge_dir()
    if not root.is_dir():
        return ()
    chunks: list[dict[str, Any]] = []
    # размер папок справки платформы — один раз
    platform_big: set[str] = set()
    plat = root / "справка_платформы"
    if plat.is_dir():
        for sub in plat.iterdir():
            if not sub.is_dir():
                continue
            try:
                n_md = sum(1 for _ in sub.glob("*.md"))
            except OSError:
                n_md = 9999
            if n_md > 300:
                platform_big.add(str(sub.resolve()))

    for path in sorted(root.rglob("*.md")):
        try:
            if _is_platform_help(path):
                if path.name == "README.md":
                    pass
                elif str(path.parent.resolve()) in platform_big:
                    # огромный синтаксис-помощник — только через search_platform_help
                    continue
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "�" in text[:200] and len(text) < 500:
            continue
        meta, text = _split_frontmatter(text)
        part = _split_md_chunks(path, text)
        if meta.get("priority"):
            for ch in part:
                ch["priority"] = meta["priority"].lower()
        chunks.extend(part)
    for yml in sorted(root.rglob("*.yaml")):
        try:
            text = yml.read_text(encoding="utf-8")
        except OSError:
            continue
        data = yaml.safe_load(text) or {}
        if isinstance(data, dict) and isinstance(
            data.get("разделы") or data.get("sections"), dict
        ):
            secs = data.get("разделы") or data.get("sections") or {}
            for sk, sec in secs.items():
                if not isinstance(sec, dict):
                    continue
                body = (
                    f"{sec.get('заголовок') or sec.get('title') or sk}\n"
                    f"{sec.get('текст') or sec.get('content') or ''}\n"
                    f"{sec.get('пример') or sec.get('example') or ''}"
                )
                chunks.append(
                    {
                        "id": f"{yml.name}:{sk}",
                        "title": str(
                            sec.get("заголовок") or sec.get("title") or sk
                        ),
                        "path": str(yml),
                        "text": body[:3000],
                    }
                )
        else:
            chunks.append(
                {
                    "id": yml.name,
                    "title": yml.stem,
                    "path": str(yml),
                    "text": f"{yml.stem}\n{text[:3500]}",
                }
            )
    return tuple(chunks)


def _tokenize(text: str) -> set[str]:
    return {
        t
        for t in re.findall(
            r"[a-zA-Zа-яА-ЯёЁ]{3,}", (text or "").lower().replace("ё", "е")
        )
    }


@lru_cache(maxsize=1)
def _platform_index() -> tuple[dict[str, str], ...]:
    """
    Индекс из knowledge/справка_платформы/индекс.json
    (python scripts/rebuild_help_index.py).
    """
    import json

    root = knowledge_dir() / "справка_платформы"
    idx_path = root / "индекс.json"
    if idx_path.is_file():
        try:
            data = json.loads(idx_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return tuple(x for x in data if isinstance(x, dict))
        except Exception:
            pass
    # запасной вариант — только маленькие разделы
    rows: list[dict[str, str]] = []
    if not root.is_dir():
        return ()
    for sub in root.iterdir():
        if not sub.is_dir():
            continue
        files = list(sub.glob("*.md"))
        if len(files) > 500:
            continue
        for path in files:
            if path.name == "README.md":
                continue
            rows.append(
                {
                    "path": str(path.relative_to(root)).replace("\\", "/"),
                    "name": path.stem.lower(),
                    "title": path.stem,
                    "section": sub.name,
                }
            )
    return tuple(rows)


def search_platform_help(query: str, *, limit: int = 8) -> list[dict[str, Any]]:
    """
    Поиск по распакованной справке платформы (hbk → md).
    Использует индекс.json; читает только лучшие файлы.
    """
    root = knowledge_dir() / "справка_платформы"
    q = (query or "").lower().replace("ё", "е")
    q_tokens = [t for t in re.findall(r"[a-zа-яё0-9]{3,}", q)]
    if not q_tokens:
        return []

    # бытовые формулировки → термины индекса («циклы» → статьи Для/Пока)
    _SYN: dict[str, tuple[str, ...]] = {
        "цикл": ("пока", "каждого"),
        "циклы": ("пока", "каждого"),
        "условия": ("если", "тогда", "иначе"),
        "условие": ("если", "тогда"),
        "массивы": ("массив",),
        "процедуры": ("процедура", "функция"),
        "функции": ("функция", "процедура"),
    }
    extra: list[str] = []
    for t in list(q_tokens):
        for s in _SYN.get(t, ()):
            if s not in q_tokens and s not in extra:
                extra.append(s)
    q_tokens = q_tokens + extra
    cycle_query = any(t in ("цикл", "циклы") for t in q_tokens) or (
        "цикл" in q
    )

    # слова про язык запросов — выше поднимать раздел язык_запросов
    query_topic = any(
        t in q
        for t in (
            "выбрать",
            "сумма",
            "количество",
            "группир",
            "соединен",
            "запрос",
            "подобно",
            "датавремя",
        )
    )
    bsl_topic = any(
        t in q
        for t in ("цикл", "циклы", "пока", "если", "процедур", "функци", "оператор")
    )

    def _word_hit(hay: str, word: str) -> bool:
        """Совпадение целого слова, не куска (сумма ≠ хешсумма)."""
        if not word or not hay:
            return False
        if re.search(rf"(?<![a-zа-яё0-9]){re.escape(word)}(?![a-zа-яё0-9])", hay):
            return True
        return False

    scored: list[tuple[float, dict[str, str]]] = []
    for row in _platform_index():
        name = str(row.get("name") or "").lower().replace("ё", "е")
        title = str(row.get("title") or "").lower().replace("ё", "е")
        search = str(row.get("search") or f"{name} {title}").lower().replace(
            "ё", "е"
        )
        section = str(row.get("section") or "")
        score = 0.0
        for t in q_tokens:
            if _word_hit(title, t):
                score += 5.0
            elif _word_hit(search, t) or _word_hit(name, t):
                score += 2.5
            elif t in title:  # вхождение как часть — слабо
                score += 0.4
            elif len(t) >= 5 and t[:5] in name:
                score += 0.5
        # «циклы» → статьи операторов (даже если слово «цикл» не в title)
        if cycle_query and section == "встроенный_язык":
            if re.match(r"^для каждого\b", title):
                score += 14.0
            elif re.match(r"^пока\b", title):
                score += 14.0
            elif re.match(r"^для\s*\(", title) or title.startswith("для (for)"):
                score += 14.0
        if score <= 0:
            continue
        if all(_word_hit(title, t) for t in q_tokens):
            score += 6.0
        if query_topic and section == "язык_запросов":
            score += 5.0
        if section == "синтаксис_помощник" and query_topic:
            score -= 2.0
        if bsl_topic and section == "встроенный_язык":
            score += 4.0
        if cycle_query and section == "синтаксис_помощник":
            score -= 4.0
        # пустые шаблоны «Показать( );»
        if re.search(r"\(\s*[;,]\s*\)|\(\s*\)\s*;", title):
            score -= 3.0
        scored.append((score, row))

    scored = [(sc, row) for sc, row in scored if sc > 0.5]
    scored.sort(key=lambda x: (-x[0], len(str(x[1].get("path") or ""))))
    items: list[dict[str, Any]] = []
    for sc, row in scored[: max(1, min(limit * 3, 40))]:
        rel = str(row.get("path") or "")
        path = root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "�" in text[:100] and len(text) < 400:
            continue
        low = text[:2500].lower().replace("ё", "е")
        for t in q_tokens:
            if t in low:
                sc += 0.8
        title = str(row.get("title") or path.stem)
        m = re.search(r"^#\s+(.+)$", text, re.M)
        if m:
            title = m.group(1).strip()[:120]
        items.append(
            {
                "score": round(sc, 2),
                "title": title,
                "path": str(path),
                "id": path.name,
                "text": text[:2000],
                "раздел": str(row.get("section") or path.parent.name),
            }
        )
    items.sort(key=lambda x: -float(x.get("score") or 0))
    return items[:limit]


def search_knowledge(query: str, *, limit: int = 5) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        return {"items": [], "count": 0, "hint": "Укажите, что искать"}

    # 1) Qdrant help (семантика)
    try:
        from config import load_config
        from vector.search import search_help_vectors

        cfg = load_config()
        if (cfg.get("qdrant") or {}).get("enabled", True):
            vec = search_help_vectors(q, limit=limit, cfg=cfg)
            if vec.get("ok") and vec.get("hits"):
                items = [
                    {
                        "score": h.get("score"),
                        "title": h.get("title"),
                        "path": h.get("path"),
                        "id": h.get("id"),
                        "text": h.get("text"),
                        "source": "qdrant",
                        **({"doc": h["doc"]} if h.get("doc") else {}),
                    }
                    for h in vec["hits"]
                ]
                return {
                    "query": q,
                    "count": len(items),
                    "items": items,
                    "source": "qdrant",
                    "collection": vec.get("collection"),
                    "папка_знаний": str(knowledge_dir()),
                    "note": "Справка из Qdrant (collection_help). Код конфигурации — search_code.",
                }
    except Exception:
        pass

    # 2) fallback: локальные md + индекс справки
    q_tokens = _tokenize(q)
    if not q_tokens:
        return {"items": [], "count": 0}

    scored: list[tuple[float, dict[str, Any]]] = []
    for ch in _load_chunks():
        text = str(ch.get("text") or "")
        t_tokens = _tokenize(text)
        inter = q_tokens & t_tokens
        score = 0.0
        if inter:
            score = len(inter) * 2.0 + len(inter) / max(len(q_tokens), 1)
        else:
            blob = text.lower().replace("ё", "е")
            soft = sum(1 for qt in q_tokens if len(qt) >= 4 and qt in blob)
            if soft == 0:
                continue
            score = soft * 0.5
        title = str(ch.get("title") or "").lower()
        for qt in q_tokens:
            if qt in title:
                score += 1.5
        score *= _PRIORITY_WEIGHT.get(str(ch.get("priority") or ""), 1.0)
        scored.append((score, ch))

    scored.sort(key=lambda x: -x[0])
    items = []
    for sc, ch in scored[: max(1, min(limit, 20))]:
        items.append(
            {
                "score": round(sc, 2),
                "title": ch.get("title"),
                "path": ch.get("path"),
                "id": ch.get("id"),
                "text": (ch.get("text") or "")[:1500],
                "source": "files",
            }
        )

    plat = search_platform_help(q, limit=max(3, limit))
    seen = {str(i.get("path")) for i in items}
    for p in plat:
        if str(p.get("path")) in seen:
            continue
        p = dict(p)
        p["source"] = "platform_index"
        items.append(p)
        seen.add(str(p.get("path")))
    items.sort(key=lambda x: -float(x.get("score") or 0))
    items = items[: max(1, min(limit + 3, 20))]

    return {
        "query": q,
        "count": len(items),
        "items": items,
        "source": "files",
        "папка_знаний": str(knowledge_dir()),
        "note": (
            "Файловый fallback. Для Qdrant: python scripts/index_qdrant.py. "
            "Справка: knowledge/справка_платформы"
        ),
    }


def list_knowledge_files() -> dict[str, Any]:
    root = knowledge_dir()
    files = []
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix.lower() in (".md", ".yaml", ".yml", ".txt"):
                files.append(str(p.relative_to(root)).replace("\\", "/"))
    return {"папка_знаний": str(root), "файлы": files, "count": len(files)}
