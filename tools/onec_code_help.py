# -*- coding: utf-8 -*-
"""
Справка по BSL / коду 1С:
- короткие темы из knowledge/справка_по_коду.yaml;
- всегда дополняется статьями синтакс-помощника (справка_платформы).
"""

from __future__ import annotations

from typing import Any


def _book() -> dict[str, Any]:
    from kb.store import get_code_help_book

    return get_code_help_book()


def list_topics() -> list[dict[str, str]]:
    secs = _book().get("sections") or {}
    return [
        {"id": k, "title": str((v or {}).get("title") or k)} for k, v in secs.items()
    ]


def resolve_topic(topic: str) -> str | None:
    t = (topic or "").strip().lower().replace("ё", "е")
    if not t:
        return None
    secs = _book().get("sections") or {}
    aliases = _book().get("aliases") or {}
    if t in secs:
        return t
    if t in aliases:
        return aliases[t]
    for alias, key in aliases.items():
        if alias in t or t in alias:
            return key
    for key, sec in secs.items():
        if t in key or t in str((sec or {}).get("title") or "").lower():
            return key
    return None


def _topic_from_query(query: str) -> str | None:
    q = query.lower().replace("ё", "е")
    rules = [
        (("http", "json", "сервис", "rest", "api", "веб"), "веб"),
        (("запрос", "выбрать", "select", "из ", "где ", "соединен", "сумма"), "запрос"),
        (("выборк", "следующий", "выгрузить"), "выборка"),
        (("регистр", "остатк", "оборот", "срез"), "регистр"),
        (("форм", "наклиенте", "насервере"), "форма"),
        (("ошибк", "исключен", "try"), "ошибка"),
        (("модул", "экспорт", "процедур", "функци"), "модуль"),
        (("файл", "выгруз", "excel"), "выгрузка"),
        (("ссылк", "получитьобъект", "менеджер"), "объект"),
        (
            (
                "цикл",
                "если",
                "пока",
                "для кажд",
                "конеццикла",
                "прервать",
                "продолжить",
                "bsl",
                "синтаксис",
                "оператор",
                "массив",
                "структур",
                "соответств",
                "таблицазначен",
                "тип",
                "неопределен",
            ),
            "код",
        ),
    ]
    for keys, topic in rules:
        if any(k in q for k in keys):
            return topic
    return None


def _platform_articles(search_q: str, *, limit: int = 6) -> list[dict[str, Any]]:
    """Статьи синтакс-помощника / встроенного языка / языка запросов."""
    q = (search_q or "").strip()
    if not q:
        return []
    try:
        from kb.store import search_platform_help

        rows = search_platform_help(q, limit=limit)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "score": r.get("score"),
                "title": r.get("title"),
                "раздел": r.get("раздел"),
                "path": r.get("path"),
                "text": (r.get("text") or "")[:1800],
            }
        )
    return out


def get_code_help(topic: str = "", query: str = "") -> dict[str, Any]:
    q = (query or "").strip()
    t = (topic or "").strip()

    if not t and not q:
        return {
            "topics": list_topics(),
            "usage": (
                "code_help(query='фраза пользователя') — шпаргалка + синтакс-помощник. "
                "Либо knowledge_search(query=та же фраза)."
            ),
            "note": (
                "На ЛЮБОЙ вопрос про код/синтаксис/методы 1С вызывайте tool. "
                "Не выдумывайте FOR/WHILE — только русский BSL из справки."
            ),
        }

    if not t and q:
        t = _topic_from_query(q) or "код"

    key = resolve_topic(t)
    secs = _book().get("sections") or {}

    # строка для поиска в синтакс-помощнике: фраза пользователя важнее topic
    search_q = q or t
    if key and key in secs and not q:
        search_q = str(secs[key].get("title") or key)

    platform = _platform_articles(search_q, limit=6)

    out: dict[str, Any] = {
        "user_query": q or t,
        "справка_платформы": platform,
        "инструкция": (
            "Отвечай в первую очередь по справка_платформы (синтакс-помощник). "
            "Дополнительно можно опереться на content/example. "
            "Синтаксис BSL — русский (Для/Пока/Если), не английский FOR/WHILE/END. "
            "Если статей нет — не выдумывай, скажи что в справке не найдено."
        ),
    }

    if key and key in secs:
        sec = secs[key]
        out["topic"] = key
        out["title"] = sec["title"]
        out["content"] = sec["content"]
        out["example"] = sec.get("example") or ""
        out["source"] = _book().get("source")
    else:
        out["topic"] = None
        out["title"] = "Синтакс-помощник"
        out["content"] = ""
        out["example"] = ""
        out["note_yaml"] = f"Тема yaml не найдена: {t}; смотри справка_платформы"

    # инструкции ИТС / стандарты / регламенты — агент сам уточняет по ним поведение платформы, настройки, правила
    try:
        from vector.search import search_docs

        docs = search_docs(search_q, limit=3)
    except Exception:  # noqa: BLE001
        docs = []
    if docs:
        out["документация"] = docs
        out["инструкция"] += (
            " Поле документация — разделы инструкций ИТС/стандартов: опирайся на них, когда вопрос про "
            "настройку, порядок действий, стандарт или поведение платформы; называй источник (doc, title)."
        )

    if not platform:
        out["hint"] = (
            "В синтакс-помощнике пусто по этой фразе — "
            "попробуй knowledge_search с другими словами (имя метода, оператора)."
        )

    return out
