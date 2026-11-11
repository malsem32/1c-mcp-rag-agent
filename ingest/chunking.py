# -*- coding: utf-8 -*-
"""Нарезка кода 1С на чанки для векторизации (чистые функции, без I/O)."""

from __future__ import annotations

import re
from typing import Iterator

_WB = r"(?<![0-9A-Za-zА-Яа-яЁё_])"

BSL_STRONG = (
    "процедура", "конецпроцедуры", "функция", "конецфункции", "выбрать",
    "procedure", "endfunction", "endprocedure", "function", "select",
    "конецесли", "конеццикла", "попытка", "исключение",
)
BSL_WEAK = ("если", "тогда", "возврат", "из", "где", "from", "where")

MIN_CODE_CHARS = 12


def _kw_re(words: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(
        _WB + "(?:" + "|".join(re.escape(k) for k in words) + ")"
        + r"(?![0-9A-Za-zА-Яа-яЁё_])",
        re.I,
    )


_STRONG_RE = _kw_re(BSL_STRONG)
_WEAK_RE = _kw_re(BSL_WEAK)
_BLANK_RE = re.compile(r"\n\s*\n+")
_WS_LINE_RE = re.compile(r"[ \t]+\n")
_PROC_LINE_RE = re.compile(r"^[ \t]*(?:Процедура|Функция|Procedure|Function)\s+", re.M | re.I)
_ATTACHED_RE = re.compile(r"^[ \t]*(?:&|//)")  # директива/комментарий над процедурой

_XML_ENTITIES = (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&"))


def clean_code_text(text: str, *, unescape_xml: bool = False) -> str:
    """Нормализация текста. unescape_xml — только для сырого XML (ET уже декодирует сам)."""
    if not text:
        return ""
    t = text.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    if unescape_xml:
        for src, dst in _XML_ENTITIES:  # &amp; последним — без двойной раскодировки
            t = t.replace(src, dst)
    t = _WS_LINE_RE.sub("\n", t)
    t = _BLANK_RE.sub("\n", t)
    return t.strip()


def is_bsl_or_query(text: str) -> bool:
    if not text:
        return False
    cleaned = text.strip()
    if len(cleaned) < MIN_CODE_CHARS:
        return False
    semi = cleaned.count(";")
    if semi > 2:
        return True
    if (
        "\n" not in cleaned and semi == 0 and " " not in cleaned
        and "\t" not in cleaned and len(cleaned) < 80
    ):
        return False
    if _STRONG_RE.search(cleaned):
        return True
    weak = _WEAK_RE.findall(cleaned)
    return len(weak) >= 2 or (bool(weak) and semi >= 1)


def _split_by_procedures(code: str) -> list[str]:
    """Границы по Процедура/Функция; директивы (&НаСервере) и комментарии остаются с процедурой."""
    lines = code.split("\n")
    starts: list[int] = []
    for i, ln in enumerate(lines):
        if _PROC_LINE_RE.match(ln):
            j = i
            while j > 0 and _ATTACHED_RE.match(lines[j - 1]):
                j -= 1
            if not starts or j > starts[-1]:
                starts.append(j)
    if not starts:
        return [code]
    bounds = ([0] if starts[0] > 0 else []) + starts + [len(lines)]
    parts = ["\n".join(lines[a:b]) for a, b in zip(bounds, bounds[1:])]
    return [p for p in parts if p.strip()]


def _hard_split(code: str, max_chars: int, overlap: int) -> list[str]:
    """Режем по размеру, предпочитая границу строки; overlap — хвост предыдущего куска."""
    out: list[str] = []
    i, n = 0, len(code)
    while i < n:
        end = min(i + max_chars, n)
        if end < n:
            nl = code.rfind("\n", i + max_chars // 2, end)
            if nl > i:
                end = nl + 1
        out.append(code[i:end])
        if end >= n:
            break
        # overlap — с начала строки, иначе следующий кусок начинается с обрывка слова
        nl = code.rfind("\n", i, end - overlap) if overlap else -1
        i = nl + 1 if nl >= i and nl + 1 < end else end
    return out


def split_large(code: str, max_chars: int, *, overlap: int = 200) -> list[str]:
    """Большие модули — по процедурам (жадная упаковка), гигантские процедуры — по строкам."""
    if max_chars <= 0 or len(code) <= max_chars:
        return [code]
    ov = max(0, min(int(overlap or 0), max_chars // 3))
    # на очень больших файлах overlap урезаем — иначе взрыв числа чанков
    if len(code) >= 80_000:
        ov = min(ov, 80)
    chunks: list[str] = []
    buf = ""
    for p in _split_by_procedures(code):
        if len(p) > max_chars:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(_hard_split(p, max_chars, min(ov, 80)))
        elif buf and len(buf) + len(p) + 1 > max_chars:
            chunks.append(buf)
            buf = p
        else:
            buf = f"{buf}\n{p}" if buf else p
    if buf:
        chunks.append(buf)
    return chunks or [code]


def iter_pieces(code: str, max_chars: int, overlap: int = 200) -> Iterator[tuple[int, str]]:
    for i, piece in enumerate(p.strip() for p in split_large(code, max_chars, overlap=overlap)):
        if piece:
            yield i, piece
