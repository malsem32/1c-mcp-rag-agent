"""
Справка по языку запросов 1С — контент из knowledge/query_syntax.yaml.
"""

from __future__ import annotations

from typing import Any


def _book() -> dict[str, Any]:
    from kb.store import get_query_syntax

    return get_query_syntax()


def list_topics() -> list[dict[str, str]]:
    secs = _book().get("sections") or {}
    return [
        {"id": key, "title": str((sec or {}).get("title") or key)}
        for key, sec in secs.items()
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
        title = str((sec or {}).get("title") or "").lower()
        if t in key or t in title:
            return key
    return None


def _enrich_examples_section(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("topic") != "examples":
        return result
    try:
        from tools.query_examples import get_examples

        book = get_examples("")
        result["scenarios"] = book.get("scenarios")
        result["examples"] = book.get("examples")
        result["rules"] = book.get("rules")
        result["next"] = "query_examples('document_period') или query_examples('sum_period')"
    except Exception:
        pass
    return result


def get_section(topic: str) -> dict[str, Any]:
    key = resolve_topic(topic)
    secs = _book().get("sections") or {}
    search_q = (topic or "").strip()
    platform: list[dict[str, Any]] = []
    if search_q:
        try:
            from kb.store import search_platform_help

            for r in search_platform_help(search_q, limit=5):
                platform.append(
                    {
                        "score": r.get("score"),
                        "title": r.get("title"),
                        "раздел": r.get("раздел"),
                        "path": r.get("path"),
                        "text": (r.get("text") or "")[:1800],
                    }
                )
        except Exception:
            pass

    if key is None or key not in secs:
        return {
            "error": f"Неизвестная тема: {topic}",
            "topics": [s["id"] for s in list_topics()],
            "справка_платформы": platform,
            "инструкция": (
                "Смотри справка_платформы (язык запросов / синтакс-помощник). "
                "Не выдумывай ANSI SQL."
            ),
            "hint": "Эталоны: query_examples('') или knowledge_search(фраза).",
        }
    sec = secs[key]
    result = {
        "topic": key,
        "title": sec["title"],
        "content": sec["content"],
        "example": sec.get("example") or "",
        "source": _book().get("source"),
        "справка_платформы": platform,
        "инструкция": (
            "Отвечай по content и справка_платформы. Язык запросов 1С, не ANSI SQL."
        ),
    }
    return _enrich_examples_section(result)


def topics_index() -> dict[str, Any]:
    fn_names: list[str] = []
    try:
        from kb.store import get_query_functions

        fn_names = list(get_query_functions().get("all_names") or [])
    except Exception:
        pass
    return {
        "topics": list_topics(),
        "usage": (
            "query_syntax_help(topic) — select|where|dates|document|join|functions|"
            "antipatterns|examples|oneline…; эталоны: query_examples(scenario). "
            "Аудит кода: audit_query_code. "
            "Каталог: knowledge/функции_запросов.yaml, антипаттерны_запросов.yaml"
        ),
        "recommended": [
            "functions",
            "antipatterns",
            "examples",
            "dates",
            "document",
            "join",
            "oneline",
            "errors",
        ],
        "query_functions_count": len(fn_names),
        "query_functions_sample": fn_names[:25],
        "note": (
            "Полный список функций конструктора 1С — topic=functions "
            "или get_query_functions."
        ),
    }


def get_functions_catalog(group: str = "") -> dict[str, Any]:
    """Полный/групповой каталог функций языка запросов."""
    from kb.store import get_query_functions

    book = get_query_functions()
    g = (group or "").strip().lower().replace("ё", "е")
    aliases = {
        "агрегат": "aggregate",
        "aggregate": "aggregate",
        "сумма": "aggregate",
        "строк": "string",
        "string": "string",
        "дат": "date",
        "date": "date",
        "числ": "number",
        "number": "number",
        "проч": "misc",
        "misc": "misc",
        "логик": "logic",
        "logic": "logic",
        "оператор": "operators",
        "выбор": "operators",
        "конструктор": "constructors",
        "датавремя": "constructors",
    }
    key = ""
    for a, k in aliases.items():
        if a in g or g == k:
            key = k
            break
    out: dict[str, Any] = {
        "source": book.get("source_note") or book.get("source"),
        "rules": book.get("rules"),
        "priority": book.get("priority"),
        "all_names": book.get("all_names"),
        "count": len(book.get("all_names") or []),
    }
    groups = book.get("groups") or {}
    if key and key in groups:
        out["group"] = key
        out["items"] = groups[key]
    else:
        out["groups"] = {k: v for k, v in groups.items()}
        out["hint"] = (
            "group=aggregate|string|date|number|misc|logic|operators|constructors"
        )
    return out
