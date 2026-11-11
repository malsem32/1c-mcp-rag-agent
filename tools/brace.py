"""
Разбор внутреннего «скобочного» формата 1С ({1,{"ru","Текст"},…}) в дерево списков.

Строки "…" (кавычка внутри — "") → str; GUID/числа/прочее → str как есть.
Используется для обычных форм (Form.bin в выгрузке, …/form внутри .epf/.erf).
"""

from __future__ import annotations

import re
from typing import Any

_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class Str(str):
    """Строковый литерал из кавычек (отличаем "123" от числа 123)."""


def parse(text: str) -> list[Any]:
    """Весь текст → список верхнего уровня. Битый хвост (бинарные вставки) просто обрывает разбор."""
    s = text.lstrip("﻿")
    n = len(s)
    i = 0
    root: list[Any] = []
    stack: list[list[Any]] = [root]
    tok_start = -1

    def flush(end: int) -> None:
        nonlocal tok_start
        if tok_start >= 0:
            t = s[tok_start:end].strip()
            if t:
                stack[-1].append(t)
            tok_start = -1

    while i < n:
        c = s[i]
        if c == "{":
            flush(i)
            lst: list[Any] = []
            stack[-1].append(lst)
            stack.append(lst)
        elif c == "}":
            flush(i)
            if len(stack) > 1:
                stack.pop()
            else:
                break
        elif c == ",":
            flush(i)
        elif c == '"':
            flush(i)
            j = i + 1
            buf = []
            while j < n:
                if s[j] == '"':
                    if j + 1 < n and s[j + 1] == '"':
                        buf.append('"')
                        j += 2
                        continue
                    break
                buf.append(s[j])
                j += 1
            stack[-1].append(Str("".join(buf)))
            i = j
        elif c in "\r\n\t ":
            if tok_start >= 0 and c in "\r\n":
                flush(i)
        else:
            if tok_start < 0:
                tok_start = i
        i += 1
    flush(n)
    return root


def is_guid(x: Any) -> bool:
    return isinstance(x, str) and not isinstance(x, Str) and bool(_GUID.match(x))


def walk(node: Any):
    """Все списки дерева (в глубину, по порядку)."""
    if isinstance(node, list):
        yield node
        for ch in node:
            if isinstance(ch, list):
                yield from walk(ch)


def ru_text(node: Any) -> str:
    """Первый {"ru","Текст"} внутри узла (подпись/синоним)."""
    for lst in walk(node):
        # {"ru","ru",1} — настройка языка (поле табличного документа), не подпись
        if len(lst) >= 2 and lst[0] == "ru" and isinstance(lst[0], Str) and isinstance(lst[1], Str) and lst[1] != "ru":
            return str(lst[1])
    return ""


def to_int(x: Any, default: int = 0) -> int:
    try:
        return int(str(x))
    except (TypeError, ValueError):
        return default
