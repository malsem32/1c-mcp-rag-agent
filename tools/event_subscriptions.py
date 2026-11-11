"""Подписки на события из выгрузки EventSubscriptions/*.xml — любая CF."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tools.meta_dump import dump_folder_path

_EVENT_PASCAL = frozenset(
    {
        "BeforeWrite",
        "OnWrite",
        "BeforeDelete",
        "OnDelete",
        "Processing",
        "Filling",
        "FillCheckProcessing",
        "OnCopy",
        "OnSetNewNumber",
        "ПередЗаписью",
        "ПриЗаписи",
        "ПередУдалением",
        "ПриУдалении",
        "ОбработкаПроведения",
        "ПриПроведении",
        "ПриКопировании",
    }
)
_EVENT_SPEECH: tuple[tuple[str, str], ...] = (
    (r"передзапис|beforewrite", "BeforeWrite"),
    (r"призапис|onwrite", "OnWrite"),
    (r"передудален|beforedelete", "BeforeDelete"),
    (r"приудален|ondelete", "BeforeDelete"),
    (r"обработкапроверкизаполнен|fillcheckprocessing", "FillCheckProcessing"),
    (r"обработказаполнен|filling(?![a-z])", "Filling"),
    (r"обработкапроведен|напроведен|припроведен|(?<![a-z])processing(?![a-z])", "Processing"),
    (r"прикопирован|oncopy", "OnCopy"),
    (r"приустановкеновогономера|onsetnewnumber", "OnSetNewNumber"),
    (r"обработкапроведения|posting", "Processing"),
)


def event_from_phrase(phrase: str) -> str:
    compact = re.sub(r"[\s_\-]+", "", (phrase or "").lower().replace("ё", "е"))
    hits: list[str] = []
    for rx, en in _EVENT_SPEECH:
        if re.search(rx, compact) and en not in hits:
            hits.append(en)
    return hits[0] if len(hits) == 1 else ""


def _synonym(text: str) -> str:
    m = re.search(
        r"<v8:lang>ru</v8:lang>\s*<v8:content>([^<]+)</v8:content>",
        text,
    )
    if m:
        return m.group(1).strip()
    m = re.search(r"<v8:content>([^<]+)</v8:content>", text)
    return m.group(1).strip() if m else ""


def _tag_text(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>([^<]*)</{tag}>", text)
    return (m.group(1) or "").strip() if m else ""


def _parse_cfg_type(raw: str) -> tuple[str, str]:
    s = (raw or "").strip().replace("cfg:", "")
    if "." in s:
        kind, name = s.split(".", 1)
        return kind.strip(), name.strip()
    return s, ""


def _parse_subscription(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if "<EventSubscription" not in text and "<Name>" not in text:
        return None
    name = _tag_text(text, "Name") or path.stem
    event = _tag_text(text, "Event")
    handler = _tag_text(text, "Handler")
    sources: list[dict[str, str]] = []
    for raw in re.findall(r"<v8:Type>([^<]+)</v8:Type>", text):
        kind, obj = _parse_cfg_type(raw)
        if not obj:
            continue
        sources.append({"kind": kind, "object_name": obj, "type": raw.strip()})
    handler_module, handler_proc = "", ""
    parts = [p for p in handler.split(".") if p]
    if len(parts) >= 3 and parts[0].lower() in ("commonmodule", "общиймодуль"):
        handler_module = parts[1]
        handler_proc = parts[-1]
    elif len(parts) >= 2:
        handler_module = parts[-2]
        handler_proc = parts[-1]
    return {
        "name": name,
        "full_name": f"ПодпискаНаСобытие.{name}",
        "synonym": _synonym(text),
        "event": event,
        "handler": handler,
        "handler_module": handler_module,
        "handler_proc": handler_proc,
        "sources": sources,
        "source_count": len(sources),
        "path": str(path).replace("\\", "/"),
    }


def _object_hint(request: str, object_name: str, full_name: str) -> str:
    bare = (object_name or "").strip() or (full_name or "").split(".")[-1].strip()
    if bare:
        return bare
    try:
        from tools.name_match import (
            pascal_candidates_in_text,
            resolve_object_name,
        )

        for p in pascal_candidates_in_text(request or ""):
            if p in _EVENT_PASCAL or event_from_phrase(p):
                continue
            if len(p) >= 4:
                return p
        leftover = re.sub(
            r"подписк\w*|событ\w*|кто|подписан|перед\s*запис\w*|при\s*запис\w*|"
            r"проведен\w*|обработк\w*|event|subscription|на\s+",
            " ",
            request or "",
            flags=re.I,
        )
        if not re.search(r"[A-Za-zА-ЯЁа-яё]{5,}", leftover):
            return ""
        return resolve_object_name(request or "") or ""
    except Exception:
        return ""


def explain_subscriptions(
    request: str = "",
    *,
    object_name: str = "",
    full_name: str = "",
    event: str = "",
    limit: int = 40,
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    Кто подписан на событие объекта (BeforeWrite / Processing / …).
    Имена объектов — из XML этой выгрузки, не из другой CF.
    """
    root = dump_folder_path("EventSubscriptions", code_dir)
    if not root:
        return {
            "ok": False,
            "error": "Нет EventSubscriptions/ в выгрузке (paths.code_dir)",
            "hint": "Выгрузите конфигурацию в файлы.",
            "subscriptions": [],
        }

    want_event = (event or "").strip() or event_from_phrase(request)
    hint = _object_hint(request, object_name, full_name)
    hint_l = hint.lower().replace("ё", "е")
    limit = max(1, min(int(limit or 40), 120))

    hits: list[dict[str, Any]] = []
    scanned = 0
    try:
        files = sorted(root.glob("*.xml"), key=lambda p: p.name.lower())
    except OSError as e:
        return {"ok": False, "error": str(e), "subscriptions": []}

    for path in files:
        scanned += 1
        rec = _parse_subscription(path)
        if not rec:
            continue
        if want_event and (rec.get("event") or "") != want_event:
            continue
        if hint_l:
            src = rec.get("sources") or []
            if not any(
                str(s.get("object_name") or "").lower().replace("ё", "е") == hint_l
                for s in src
            ):
                blob = f"{rec.get('name')} {rec.get('synonym')} {rec.get('handler')}"
                if hint_l not in blob.lower().replace("ё", "е"):
                    continue
            rec = dict(rec)
            rec["matched_object"] = hint
        hits.append(rec)
        if len(hits) >= limit:
            break

    return {
        "ok": True,
        "event": want_event or None,
        "object_name": hint or None,
        "count": len(hits),
        "scanned": scanned,
        "subscriptions": hits,
        "source": "config_dump EventSubscriptions/*.xml",
        "truncated": len(hits) >= limit,
        "prefer": (
            "Перечисли name, event, handler_module.handler_proc и 2–3 source object_name. "
            "Не выдумывай подписки вне списка. Тело обработчика — read_code / inspect_code "
            f"по CommonModules/{'{handler_module}'}/Ext/Module.bsl."
        ),
        "note": (
            "Это метаданные подписок выгрузки, не runtime. "
            "Пустой список — в этой CF нет совпадений по событию/объекту."
        ),
    }
