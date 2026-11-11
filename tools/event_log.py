"""
Журнал регистрации 1С: выборка + простой анализ.

Источник: HTTP POST /eventlog (onec_http/Module.bsl → ВыгрузитьЖурналРегистрации).
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Any


def _parse_relative_period(text: str) -> tuple[str | None, str | None]:
    """«за сегодня», «за 7 дней», «вчера» → date_from, date_to ISO."""
    pl = (text or "").lower().replace("ё", "е")
    now = datetime.now()
    end = now.replace(microsecond=0)
    start: datetime | None = None

    if re.search(r"\bсегодня\b", pl):
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif re.search(r"\bвчера\b", pl):
        d = now - timedelta(days=1)
        start = d.replace(hour=0, minute=0, second=0, microsecond=0)
        end = d.replace(hour=23, minute=59, second=59, microsecond=0)
    else:
        m = re.search(r"\bза\s+(\d{1,3})\s*(дн|день|дня|дней|час)", pl)
        if m:
            n = int(m.group(1))
            if "час" in m.group(2):
                start = now - timedelta(hours=n)
            else:
                start = now - timedelta(days=n)
        elif re.search(r"\b(недел|7\s*дн)", pl):
            start = now - timedelta(days=7)
        elif re.search(r"\b(месяц|30\s*дн)", pl):
            start = now - timedelta(days=30)

    if start is None:
        return None, None
    return start.strftime("%Y-%m-%dT%H:%M:%S"), end.strftime("%Y-%m-%dT%H:%M:%S")


def _level_from_text(text: str) -> str:
    pl = (text or "").lower().replace("ё", "е")
    if re.search(r"\b(ошибк|error|err)\b", pl):
        return "Error"
    if re.search(r"\b(предупред|warning|warn)\b", pl):
        return "Warning"
    if re.search(r"\b(информ|info)\b", pl):
        return "Information"
    if re.search(r"\b(примечан|note)\b", pl):
        return "Note"
    return ""


def query_event_log(
    backend: Any,
    *,
    date_from: str = "",
    date_to: str = "",
    level: str = "",
    user: str = "",
    event: str = "",
    application: str = "",
    comment: str = "",
    metadata: str = "",
    search: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """
    Выборка из журнала регистрации через HTTP /eventlog.
    """
    lim = max(1, min(int(limit or 50), 500))
    payload: dict[str, Any] = {"limit": lim}
    if date_from:
        payload["date_from"] = date_from
    if date_to:
        payload["date_to"] = date_to
    if level:
        payload["level"] = level
    if user:
        payload["user"] = user
    if event:
        payload["event"] = event
    if application:
        payload["application"] = application
    if comment:
        payload["comment"] = comment
    if metadata:
        payload["metadata"] = metadata
    if search:
        payload["q"] = search

    # HttpBackend: generic request
    if hasattr(backend, "_request"):
        import json

        data = backend._request(
            "POST",
            "eventlog",
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
    else:
        return {
            "error": "Backend не поддерживает eventlog",
            "hint": "Нужен HTTP backend + Module.bsl eventlogPOST",
        }

    if not isinstance(data, dict):
        return {"error": "Некорректный ответ eventlog", "raw": str(data)[:300]}

    if data.get("error"):
        data.setdefault(
            "hint",
            "Обновите onec_http/Module.bsl (eventlogPOST), переопубликуйте HTTP-сервис. "
            "Пользователю публикации нужны права на журнал регистрации.",
        )
        return data

    rows = data.get("rows") or data.get("data") or []
    if not isinstance(rows, list):
        rows = []

    # compact rows for 32k: keep unified fields first
    compact_rows = []
    for r in rows[:lim]:
        if not isinstance(r, dict):
            continue
        compact_rows.append(
            {
                "date": r.get("date") or r.get("Date") or r.get("Дата"),
                "level": r.get("level") or r.get("Level") or r.get("Уровень"),
                "user": r.get("user") or r.get("User") or r.get("Пользователь"),
                "event": r.get("event") or r.get("Event") or r.get("Событие"),
                "comment": r.get("comment") or r.get("Comment") or r.get("Комментарий"),
                "metadata": r.get("metadata") or r.get("Metadata") or r.get("Метаданные"),
                "application": r.get("application")
                or r.get("ApplicationName")
                or r.get("ИмяПриложения"),
            }
        )

    out = {
        "ok": True,
        "source": "event_log",
        "date_from": data.get("date_from"),
        "date_to": data.get("date_to"),
        "rows_count": len(compact_rows),
        "raw_count": data.get("raw_count"),
        "truncated": data.get("truncated"),
        "limit": lim,
        "rows": compact_rows,
        "by_level": data.get("by_level") or [],
        "by_user": data.get("by_user") or [],
        "by_event": data.get("by_event") or [],
        "filters": {k: v for k, v in payload.items() if k != "limit"},
        "prefer": (
            "Покажи by_level / by_event / by_user и 5–15 строк rows "
            "(date, level, user, event, comment). "
            "Для углубления: analyze_event_log или снова query_event_log с level=Error."
        ),
        "note": (
            "Журнал регистрации платформы 1С (не регистр сведений). "
            "Нужен обновлённый Module.bsl + права на ЖР."
        ),
    }
    return out


def analyze_event_log(
    backend: Any,
    *,
    request: str = "",
    date_from: str = "",
    date_to: str = "",
    level: str = "",
    user: str = "",
    search: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    """
    ONE-SHOT: разобрать фразу про журнал + выборка + сводка.
    «ошибки за сегодня», «журнал регистрации за неделю», «что писал пользователь X».
    """
    raw = (request or "").strip()
    pl = raw.lower().replace("ё", "е")

    # period from phrase
    if not date_from and not date_to:
        df, dt = _parse_relative_period(raw)
        if df:
            date_from = df
        if dt:
            date_to = dt

    if not level:
        level = _level_from_text(raw)

    # user from phrase: «пользователь Иван»
    if not user:
        m = re.search(
            r"пользовател\w*\s+[«\"]?([A-Za-zА-Яа-яЁё0-9_.\-@]+)[»\"]?",
            pl,
        )
        if m:
            user = m.group(1)

    # free text search
    q = (search or "").strip()
    if not q and raw:
        # strip journal noise words
        noise = {
            "журнал",
            "регистрации",
            "регистрация",
            "покажи",
            "посмотри",
            "ошибки",
            "ошибка",
            "сегодня",
            "вчера",
            "неделя",
            "неделю",
            "месяц",
            "за",
            "дней",
            "дня",
            "день",
            "анализ",
            "проанализируй",
            "лог",
            "логи",
            "eventlog",
        }
        toks = [
            t
            for t in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9_\.]{3,}", pl)
            if t not in noise and not t.isdigit()
        ]
        # keep only if looks like event/module name
        if toks and not user:
            q = " ".join(toks[:4])

    data = query_event_log(
        backend,
        date_from=date_from,
        date_to=date_to,
        level=level,
        user=user,
        search=q,
        limit=limit or 100,
    )
    if data.get("error"):
        return data

    rows = data.get("rows") or []
    # extra local analysis
    err_rows = [
        r
        for r in rows
        if "ошиб" in str(r.get("level") or "").lower()
        or "error" in str(r.get("level") or "").lower()
    ]
    comments = Counter()
    for r in rows:
        c = str(r.get("comment") or "").strip()
        if c:
            comments[c[:120]] += 1

    summary = {
        "total_shown": len(rows),
        "errors_shown": len(err_rows),
        "top_comments": [
            {"comment": c, "count": n} for c, n in comments.most_common(8)
        ],
        "period": {"from": data.get("date_from"), "to": data.get("date_to")},
        "applied_filters": {
            "level": level or None,
            "user": user or None,
            "search": q or None,
        },
    }

    return {
        "ok": True,
        "request": raw,
        "summary": summary,
        "by_level": data.get("by_level"),
        "by_user": data.get("by_user"),
        "by_event": data.get("by_event"),
        "rows": rows[:40],
        "rows_shown_of": len(rows),
        "rows_count": data.get("rows_count"),
        "truncated": data.get("truncated"),
        "prefer": (
            "1) summary: период, сколько ошибок. "
            "2) by_level / by_event / by_user. "
            "3) несколько характерных rows (date, user, event, comment). "
            "Не выдумывай записи вне rows."
        ),
        "source": "event_log",
        "hint": data.get("hint") or data.get("note"),
    }
