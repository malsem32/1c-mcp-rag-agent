"""Регламентные задания из выгрузки ScheduledJobs/ — любая CF."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tools.meta_dump import dump_folder_path


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


def _parse_handler(method: str) -> tuple[str, str]:
    parts = [p for p in (method or "").split(".") if p]
    if len(parts) >= 3 and parts[0].lower() in ("commonmodule", "общиймодуль"):
        return parts[1], parts[-1]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return "", method


def _parse_schedule(path: Path) -> dict[str, str]:
    sched = path.parent / path.stem / "Ext" / "Schedule.xml"
    if not sched.is_file():
        return {}
    try:
        text = sched.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    m = re.search(r"<Schedule\b([^>]*)>", text)
    if not m:
        return {"path": str(sched).replace("\\", "/")}
    attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
    out = {k: v for k, v in attrs.items() if v and v not in ("0001-01-01", "00:00:00")}
    wd = re.search(r"<ent:WeekDays>([^<]+)</ent:WeekDays>", text)
    mo = re.search(r"<ent:Months>([^<]+)</ent:Months>", text)
    if wd:
        out["week_days"] = wd.group(1).strip()
    if mo:
        out["months"] = mo.group(1).strip()
    return out


def _parse_job(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if "<ScheduledJob" not in text:
        return None
    name = _tag_text(text, "Name") or path.stem
    method = _tag_text(text, "MethodName")
    module, proc = _parse_handler(method)
    use = _tag_text(text, "Use").lower() == "true"
    return {
        "name": name,
        "full_name": f"РегламентноеЗадание.{name}",
        "synonym": _synonym(text),
        "method": method,
        "handler_module": module,
        "handler_proc": proc,
        "use_in_dump": use,
        "predefined": _tag_text(text, "Predefined").lower() == "true",
        "restart_count": _tag_text(text, "RestartCountOnFailure"),
        "schedule": _parse_schedule(path),
        "path": str(path).replace("\\", "/"),
    }


def describe_scheduled_job(
    request: str = "",
    *,
    name: str = "",
    limit: int = 30,
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Метод и расписание регл. заданий этой выгрузки (не runtime ИБ)."""
    root = dump_folder_path("ScheduledJobs", code_dir)
    if not root:
        return {
            "ok": False,
            "error": "Нет ScheduledJobs/ в выгрузке (paths.code_dir)",
            "hint": "Выгрузите конфигурацию в файлы.",
            "jobs": [],
        }

    want = (name or "").strip()
    q = (request or "").lower().replace("ё", "е")
    q = re.sub(
        r"регламентн\w*\s+задан\w*|scheduled\s*jobs?|какое|какой|метод|расписан\w*",
        " ",
        q,
    )
    from tools.phrase_struct import name_filter_tokens

    toks = name_filter_tokens(q, speech_fallback=True)
    if not want:
        try:
            from tools.name_match import pascal_candidates_in_text

            cands = pascal_candidates_in_text(request or "")
            if len(cands) == 1:
                want = cands[0]
        except Exception:
            pass

    limit = max(1, min(int(limit or 30), 80))
    hits: list[dict[str, Any]] = []
    scanned = 0
    try:
        files = sorted(root.glob("*.xml"), key=lambda p: p.name.lower())
    except OSError as e:
        return {"ok": False, "error": str(e), "jobs": []}

    want_l = want.lower().replace("ё", "е")
    for path in files:
        scanned += 1
        rec = _parse_job(path)
        if not rec:
            continue
        blob = (
            f"{rec.get('name')} {rec.get('synonym')} {rec.get('method')}"
        ).lower().replace("ё", "е")
        if want_l:
            if want_l not in blob and rec.get("name", "").lower().replace("ё", "е") != want_l:
                continue
        elif toks and not any(t in blob for t in toks):
            continue
        hits.append(rec)
        if len(hits) >= limit:
            break
    if not hits and toks and not want_l:
        # слова речи не совпали ни с одним заданием — отдаём весь перечень, а не «0»
        for path in files:
            rec = _parse_job(path)
            if rec:
                hits.append(rec)
            if len(hits) >= limit:
                break

    return {
        "ok": True,
        "query": (name or request or "").strip() or None,
        "count": len(hits),
        "scanned": scanned,
        "jobs": hits,
        "source": "config_dump ScheduledJobs/*.xml + Ext/Schedule.xml",
        "truncated": len(hits) >= limit,
        "prefer": (
            "Покажи name, synonym, handler_module.handler_proc, use_in_dump, schedule.BeginTime. "
            "use_in_dump — флаг метаданных, не факт что задание включено в этой ИБ. "
            "Тело метода — inspect_code / read_code по общему модулю из handler_module."
        ),
        "note": "Имена только из этой выгрузки.",
    }
