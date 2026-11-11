"""Активные пользователи / сеансы информационной базы 1С."""

from __future__ import annotations

from typing import Any


def list_active_sessions(
    backend: Any,
    *,
    user: str = "",
    application: str = "",
    unique_users: bool = False,
) -> dict[str, Any]:
    """
    Активные сеансы ИБ (кто сейчас в базе).
    Не путать со справочником пользователей (list_users) и ролями (list_roles).
    """
    filters: dict[str, Any] = {}
    if user:
        filters["user"] = user
    if application:
        filters["application"] = application
    if unique_users:
        filters["unique"] = True

    if hasattr(backend, "list_sessions"):
        data = backend.list_sessions(filters)
    else:
        return {
            "error": "Backend без list_sessions",
            "hint": "HTTP + GET /sessions",
        }

    if not isinstance(data, dict):
        return {"error": str(data)[:300]}
    if data.get("error"):
        data.setdefault(
            "hint",
            "Обновите Module.bsl (sessionsGET), переопубликуйте HTTP. "
            "Нужны права на просмотр активных пользователей.",
        )
        return data

    sessions = data.get("sessions") or []
    if not isinstance(sessions, list):
        sessions = []

    # cap for 32k
    max_show = 80
    truncated = len(sessions) > max_show

    return {
        "ok": True,
        "source": "infobase_sessions",
        "sessions_count": data.get("sessions_count", len(sessions)),
        "unique_users_count": data.get(
            "unique_users_count",
            len({str(s.get("user") or "") for s in sessions if isinstance(s, dict)}),
        ),
        "users": data.get("users")
        or sorted(
            {
                str(s.get("user") or "(пусто)")
                for s in sessions
                if isinstance(s, dict)
            }
        ),
        "sessions": sessions[:max_show],
        "by_user": data.get("by_user") or [],
        "by_application": data.get("by_application") or [],
        "filters": {
            "user": user or None,
            "application": application or None,
            "unique_users": unique_users,
        },
        "truncated": truncated,
        "prefer": (
            "Покажи unique_users_count, список users и 5–20 sessions "
            "(user, application, computer, started). "
            "пользователи справочника → list_users; роли → list_roles; "
            "это именно кто СЕЙЧАС в базе."
        ),
        "note": data.get("hint")
        or "Сеансы ПолучитьСеансыИнформационнойБазы (активные подключения).",
    }


def active_users_overview(backend: Any, request: str = "") -> dict[str, Any]:
    """ONE-SHOT по фразе «активные пользователи» / «кто в базе»."""
    pl = (request or "").lower().replace("ё", "е")
    unique = bool(
        "уник" in pl or "distinct" in pl or "список пользовател" in pl
    )
    user = ""
    app = ""
    # «пользователь Иван»
    import re

    m = re.search(
        r"пользовател\w*\s+[«\"]?([A-Za-zА-Яа-яЁё0-9_.\-@]+)[»\"]?",
        pl,
    )
    if m and m.group(1) not in ("сейчас", "базе", "системы", "активные"):
        user = m.group(1)
    if "тонк" in pl or "1cv8c" in pl:
        app = "1CV8C"
    elif "толст" in pl or re.search(r"\b1cv8\b", pl):
        app = "1CV8"
    elif "фон" in pl or "background" in pl or "регламент" in pl:
        app = "BackgroundJob"
    elif "http" in pl or "web" in pl:
        app = "WSConnection"

    out = list_active_sessions(
        backend, user=user, application=app, unique_users=unique
    )
    out["request"] = request
    out["routed"] = "list_active_sessions"
    return out
