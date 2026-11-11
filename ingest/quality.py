# -*- coding: utf-8 -*-
"""Автоматическая чистка кода перед векторизацией. Универсальные правила, без привязки к конфигурации.

clean_chunk_text — убирает шум ВНУТРИ текста (декоративные разделители, base64-блобы, хвосты строк).
reject_reason    — решает, что чанк не несёт информации (пустая оболочка, один заголовок области).
Что отброшено и почему — копится в DropReport и пишется в отчёт prepare.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

# строка-рамка вида //////////// или // ---------- или // ========== (только символы-разделители)
_SEPARATOR = re.compile(r"^[ \t]*(?://)?[ \t]*[/\-=*_#~+.]{6,}[ \t]*$")
_BASE64 = re.compile(r"[A-Za-z0-9+/]{100,}={0,2}")
_UUID_RUN = re.compile(r"(?:[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}[ ,;]*){4,}")
_DIRECTIVE = re.compile(r"^[ \t]*(?:#|&)")
_DECL_ONLY = re.compile(
    r"^[ \t]*(?:Процедура|Функция|Procedure|Function|КонецПроцедуры|КонецФункции|EndProcedure|EndFunction|Возврат|Return)\b[^\n]*$",
    re.I,
)


def clean_chunk_text(text: str) -> str:
    """Чистка шума внутри текста; смысловой код не трогает."""
    if not text:
        return ""
    out: list[str] = []
    for ln in text.split("\n"):
        if _SEPARATOR.match(ln):
            continue
        out.append(ln.rstrip())
    t = "\n".join(out)
    t = _BASE64.sub("«base64»", t)
    t = _UUID_RUN.sub("«список uuid» ", t)
    return re.sub(r"\n{2,}", "\n", t).strip()


def reject_reason(body: str, *, min_chars: int = 80) -> str | None:
    """Причина отбросить чанк или None. body — текст без контекстной шапки."""
    b = (body or "").strip()
    if len(b) < min_chars and not any(ch.isalpha() for ch in b[:5]):
        return "пустой"
    lines = [ln for ln in b.split("\n") if ln.strip()]
    if not lines:
        return "пустой"
    meaningful = [
        ln for ln in lines
        if not _DIRECTIVE.match(ln) and not ln.lstrip().startswith("//") and not _DECL_ONLY.match(ln)
    ]
    comment_chars = sum(len(ln.strip()) for ln in lines if ln.lstrip().startswith("//"))
    if not meaningful and comment_chars < min_chars:
        return "пустая оболочка (только директивы/объявления)"
    if len(b) < min_chars and not meaningful:
        return "слишком короткий"
    return None


class DropReport:
    """Статистика чистки: сколько и почему выброшено/очищено, с примерами путей."""

    def __init__(self, examples: int = 3) -> None:
        self.dropped: Counter = Counter()
        self.cleaned: Counter = Counter()
        self.examples: dict[str, list[str]] = defaultdict(list)
        self._n = examples

    def drop(self, reason: str, where: str) -> None:
        self.dropped[reason] += 1
        if len(self.examples[reason]) < self._n:
            self.examples[reason].append(where)

    def cleaned_chunk(self, before: str, after: str) -> None:
        if len(after) < len(before):
            self.cleaned["чанков очищено"] += 1
            self.cleaned["символов убрано"] += len(before) - len(after)

    def as_dict(self) -> dict:
        return {"dropped": dict(self.dropped), "cleaned": dict(self.cleaned), "examples": dict(self.examples)}
