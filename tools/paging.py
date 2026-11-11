# -*- coding: utf-8 -*-
"""
Лимит и постраничная выдача для tools.

Модель сама задаёт limit (сколько за раз) и offset/page (следующая порция),
если в первых N нет нужного — offset=N, limit=N снова.
"""

from __future__ import annotations

import json
from typing import Any, Sequence, TypeVar

T = TypeVar("T")

# Глобальные разумные границы (модель выбирает внутри)
DEFAULT_LIMIT = 25
MAX_LIMIT = 200
MAX_OFFSET = 50_000


def clamp_limit(
    limit: int | None,
    *,
    default: int = DEFAULT_LIMIT,
    maximum: int = MAX_LIMIT,
    minimum: int = 1,
) -> int:
    try:
        value = int(limit) if limit is not None else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def resolve_offset(
    *,
    offset: int | None = None,
    page: int | None = None,
    limit: int = DEFAULT_LIMIT,
) -> int:
    """
    offset — с 0; page — с 1 (если задан page, он главнее).
    """
    if page is not None:
        try:
            p = int(page)
        except (TypeError, ValueError):
            p = 1
        p = max(1, p)
        return min((p - 1) * max(1, limit), MAX_OFFSET)
    try:
        off = int(offset or 0)
    except (TypeError, ValueError):
        off = 0
    return max(0, min(off, MAX_OFFSET))


def fit_count(
    items: Sequence[Any],
    offset: int,
    budget_chars: int,
    *,
    minimum: int = 5,
    maximum: int = MAX_LIMIT,
) -> int:
    """Сколько элементов с позиции offset помещается в budget_chars JSON (но не меньше minimum)."""
    n, size = 0, 0
    for it in items[offset : offset + maximum]:
        sz = len(json.dumps(it, ensure_ascii=False, default=str)) + 2
        if n >= minimum and size + sz > budget_chars:
            break
        size += sz
        n += 1
    return max(1, n)


def slice_page(
    items: Sequence[T],
    *,
    limit: int | None = None,
    offset: int | None = None,
    page: int | None = None,
    default_limit: int = DEFAULT_LIMIT,
    max_limit: int = MAX_LIMIT,
    fit_chars: int | None = None,
) -> tuple[list[T], dict[str, Any]]:
    """
    Вернуть срез items и meta для модели:
      limit, offset, page, returned, total, has_more, next_offset, next_page

    fit_chars — если модель не задала limit, страница набирается по размеру (символы JSON), а не по счётчику:
    общий ужиматель потом ничего не режет, и next_offset не пропускает строки.
    """
    auto = bool(fit_chars) and not (limit or 0) and not page
    if auto:
        off0 = resolve_offset(offset=offset, page=None, limit=default_limit)
        lim = fit_count(items, off0, int(fit_chars), maximum=max_limit)
        off = off0
    else:
        lim = clamp_limit(limit, default=default_limit, maximum=max_limit)
        off = resolve_offset(offset=offset, page=page, limit=lim)
    total = len(items)
    chunk = list(items[off : off + lim])
    returned = len(chunk)
    next_off = off + returned
    has_more = next_off < total
    page_num = (off // lim) + 1 if lim else 1
    meta = {
        "auto_limit": auto,  # размер страницы подобран по бюджету; в следующем вызове limit указывать не нужно
        "limit": lim,
        "offset": off,
        "page": page_num,
        "returned": returned,
        "total": total,
        "has_more": has_more,
        "next_offset": next_off if has_more else None,
        "next_page": (page_num + 1) if has_more else None,
        "prefer_paging": (
            "Модель сама выбирает limit (сколько за раз). "
            "Если нужного нет в этой порции и has_more=true — "
            f"повтори tool с offset={next_off if has_more else off} "
            f"(или page={page_num + 1 if has_more else page_num}) и тем же limit. "
            "Не выдумывай строки за пределами total."
        ),
    }
    return chunk, meta


def attach_paging(
    out: dict[str, Any],
    meta: dict[str, Any],
    *,
    tool: str | None = None,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Вложить paging в ответ tool (не ломая плоские поля).
    tool + args — готовый вызов следующей страницы (next_call): те же аргументы и новый offset, без пересборки моделью.
    """
    out = dict(out)
    out["paging"] = meta
    if meta.get("has_more") and tool:
        nxt = {k: v for k, v in (args or {}).items() if v not in (None, "", 0, False) and k not in ("page", "offset")}
        if not meta.get("auto_limit"):
            nxt["limit"] = meta.get("limit")
        nxt["offset"] = meta.get("next_offset")
        out["next_call"] = {"tool": tool, "args": nxt}
    # дубли плоских полей — удобно модели
    for k in (
        "limit",
        "offset",
        "page",
        "returned",
        "total",
        "has_more",
        "next_offset",
        "next_page",
    ):
        if k in meta:
            out[k] = meta[k]
    if meta.get("has_more"):
        out["note_paging"] = (
            f"Показано {meta.get('returned')} из {meta.get('total')}. "
            f"Следующая порция: offset={meta.get('next_offset')} "
            f"limit={meta.get('limit')} (или page={meta.get('next_page')})."
        )
    return out


def fetch_limit_for_offset(
    limit: int,
    offset: int,
    *,
    max_fetch: int = 500,
) -> int:
    """
    Для 1С без OFFSET: запросить ПЕРВЫЕ (offset+limit), потом срезать в Python.
    """
    need = max(0, int(offset)) + max(1, int(limit))
    return max(1, min(need, max_fetch))
