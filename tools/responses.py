"""
Узкие ответы инструментов: каждый tool отдаёт только свой тип данных.

Не смешиваем: метаданные ≠ строки запроса ≠ роли полей.
"""

from __future__ import annotations

from typing import Any


def err(message: str, hint: str | None = None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"error": message}
    if hint:
        out["hint"] = hint
    out.update(extra)
    return out


def need_clarification(
    question: str,
    *,
    about: str = "",
    options: list[Any] | None = None,
    missing: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """
    Агент НЕ должен угадывать — показать вопрос пользователю и ждать ответа.
    """
    out: dict[str, Any] = {
        "need_clarification": True,
        "question": question,
        "action_for_agent": (
            "ОСТАНОВИСЬ. Не вызывай data-tools. Спроси пользователя этим question "
            "(можно переформулировать по-русски). Дождись ответа, затем продолжи."
        ),
    }
    if about:
        out["about"] = about
    if options:
        out["options"] = options
    if missing:
        out["missing"] = missing
    out.update(extra)
    return out


def only_rows(result: Any, *, max_rows: int | None = None) -> dict[str, Any]:
    """Ответ data-tools: только строки (+ count/truncated)."""
    if not isinstance(result, dict):
        return err(str(result))
    if result.get("error"):
        return err(str(result["error"]), hint=result.get("hint"))

    # важно: [] (пустой список) — валидный ответ «0 строк», не error
    if "data" in result:
        rows = result.get("data")
    elif "rows" in result:
        rows = result.get("rows")
    elif "items" in result:
        rows = result.get("items")
    else:
        rows = None
    if rows is None:
        # иногда 1С кладёт агрегаты без data
        slim = {
            k: result[k]
            for k in ("Сумма", "Количество", "КоличествоДокументов", "sum", "count")
            if k in result
        }
        if slim:
            return slim
        return err("Пустой ответ 1С", hint=str(list(result.keys())[:12]))

    if not isinstance(rows, list):
        return {"value": rows}

    if max_rows is not None and len(rows) > max_rows:
        return {
            "rows": rows[:max_rows],
            "count": len(rows),
            "shown": max_rows,
            "truncated": True,
        }
    out: dict[str, Any] = {"rows": rows, "count": len(rows)}
    if result.get("truncated"):
        out["truncated"] = True
    return out


def slim_meta_item(item: dict[str, Any]) -> dict[str, str]:
    full = str(item.get("full_name") or "")
    name = str(item.get("name") or "")
    synonym = str(item.get("synonym") or "")
    mtype = str(item.get("type") or "")
    out: dict[str, str] = {}
    if full:
        out["full_name"] = full
    elif name:
        out["full_name"] = name
    if synonym and synonym != name and synonym != full:
        out["synonym"] = synonym
    if mtype:
        out["type"] = mtype
    return out


def slim_field(item: dict[str, Any] | Any) -> dict[str, str]:
    """Поле structure: name + type (+ synonym/kind) — тип всегда, иначе модели неясно."""
    if not isinstance(item, dict):
        return {"name": str(item), "type": "", "тип": ""}
    name = str(item.get("name") or item.get("Имя") or "")
    syn = str(item.get("synonym") or item.get("Синоним") or "").strip()
    typ = str(
        item.get("type")
        or item.get("Тип")
        or item.get("тип")
        or item.get("type_str")
        or ""
    ).strip()
    kind = str(item.get("kind") or item.get("Вид") or "").strip()
    # не обрезать слишком рано — ссылочные типы длинные
    if len(typ) > 160:
        typ = typ[:157] + "…"
    out: dict[str, str] = {
        "name": name,
        "type": typ,
        "тип": typ,  # дубль: модели иногда смотрят русские ключи
    }
    if syn and syn != name:
        out["synonym"] = syn
        out["синоним"] = syn
    if kind:
        out["kind"] = kind
    ts = item.get("tabular_section") or item.get("ts")
    if ts:
        out["ts"] = str(ts)
    return out


def pick_rows_keys(rows: list[Any], max_cols: int = 12) -> list[Any]:
    """Ограничить число колонок в строках (если 1С вернула слишком широкую выборку)."""
    if not rows or not isinstance(rows[0], dict):
        return rows
    keys = list(rows[0].keys())
    if len(keys) <= max_cols:
        return rows
    keep = keys[:max_cols]
    return [{k: r.get(k) for k in keep} for r in rows if isinstance(r, dict)]
