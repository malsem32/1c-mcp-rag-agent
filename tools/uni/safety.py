# -*- coding: utf-8 -*-
"""Безопасность запросов: только ВЫБРАТЬ, без изменяющих конструкций."""

from __future__ import annotations

import re


_UNSAFE_PATTERNS = re.compile(
    r"\b("
    r"УНИЧТОЖИТЬ|DESTROY|"
    r"ВЫПОЛНИТЬ|EXECUTE|"
    r"УДАЛИТЬ|DELETE|"
    r"ИЗМЕНИТЬ|UPDATE|"
    r"ДОБАВИТЬ|INSERT|"
    r"СОЗДАТЬ|CREATE|"
    r"DROP|TRUNCATE"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)


_SELECT_START = re.compile(
    r"^\s*(ВЫБРАТЬ|SELECT)\b",
    re.IGNORECASE | re.UNICODE,
)


def _strip_query_comments(query: str) -> str:
    """Снять // и /* */ вне строковых литералов (для safety-check)."""
    out: list[str] = []
    i = 0
    n = len(query)
    in_str = ""
    while i < n:
        ch = query[i]
        if in_str:
            out.append(ch)
            if ch == in_str:
                in_str = ""
            elif ch == '"' and in_str == '"':
                if i + 1 < n and query[i + 1] == '"':
                    out.append(query[i + 1])
                    i += 2
                    continue
            i += 1
            continue
        if ch in ('"', "'"):
            in_str = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and query[i + 1] == "/":
            while i < n and query[i] != "\n":
                i += 1
            out.append(" ")
            continue
        if ch == "/" and i + 1 < n and query[i + 1] == "*":
            i += 2
            while i + 1 < n and not (query[i] == "*" and query[i + 1] == "/"):
                i += 1
            i = min(n, i + 2)
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def is_safe_select_query(query: str) -> tuple[bool, str]:
    if not query or not query.strip():
        return False, "Пустой запрос"
    stripped = _strip_query_comments(query)
    if not _SELECT_START.search(stripped):
        return False, "Разрешены только запросы ВЫБРАТЬ / SELECT"
    scrubbed = re.sub(r'"[^"]*"', '""', stripped)
    scrubbed = re.sub(r"'[^']*'", "''", scrubbed)
    m = _UNSAFE_PATTERNS.search(scrubbed)
    if m:
        return False, f"Запрещённая конструкция в read-only режиме: {m.group(1)}"
    return True, ""


def clamp_limit(limit: int | None, default: int = 50, maximum: int = 300) -> int:
    """Совместимость: делегирует в paging (модель сама выбирает limit)."""
    from tools.paging import clamp_limit as _cl

    return _cl(limit, default=default or 25, maximum=maximum or 200)
