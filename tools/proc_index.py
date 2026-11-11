"""Индекс процедур: объявление (proc_names) и вызовы в Qdrant — точными фильтрами, не векторным поиском."""

from __future__ import annotations

import re
from typing import Any


def _resolve_proc(request: str, procedure: str, module: str) -> tuple[str, str]:
    proc = (procedure or "").strip()
    mod = (module or "").strip()
    if not proc:
        m = re.search(r"([A-ZА-ЯЁ][\wА-Яа-яЁё]{2,})\.([A-ZА-ЯЁ][\wА-Яа-яЁё]{3,})", request or "")
        if m:
            mod, proc = m.group(1), m.group(2)
    if not proc:
        try:
            from tools.router_detect import _proc_candidate

            proc, mod2 = _proc_candidate(request or "")
            mod = mod or mod2
        except Exception:
            proc = ""
    if not proc:
        try:
            from tools.name_match import pascal_candidates_in_text

            cands = pascal_candidates_in_text(request or "")
            proc = cands[0] if cands else ""
        except Exception:
            proc = ""
    if not proc:
        m = re.search(
            r"(?:процедур[аыуе]|функци[яие]|procedure|function)\s+([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)",
            request or "",
            re.I,
        )
        if m:
            proc = m.group(1)
    if "." in proc and not mod:
        mod, _, proc = proc.partition(".")
    return proc, mod


def _line_of(text: str, pattern: re.Pattern[str]) -> int | None:
    m = pattern.search(text or "")
    if not m:
        return None
    return (text or "").count("\n", 0, m.start()) + 1


def find_definitions(proc: str, module: str = "", *, limit: int = 20) -> list[dict[str, Any]]:
    """Где объявлена процедура/функция: payload.proc_names (точно), фильтр Qdrant MatchText."""
    from qdrant_client.http import models as qm

    from vector.catalog import _scroll_payload, collection_name, get_qdrant

    client = get_qdrant()
    col = collection_name("code")
    must = [qm.FieldCondition(key="proc_names_str", match=qm.MatchText(text=proc))]
    if module:
        must.append(qm.FieldCondition(key="source_path", match=qm.MatchText(text=module)))
    rows = _scroll_payload(client, col, scroll_filter=qm.Filter(must=must), limit=200)
    pat = re.compile(rf"(?im)^\s*(?:Процедура|Функция|Procedure|Function)\s+{re.escape(proc)}\s*\(")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pl in rows:
        names = pl.get("proc_names") or []
        if isinstance(names, str):
            names = names.split()
        if not any(str(n).lower() == proc.lower() for n in names):
            continue
        path = str(pl.get("source_path") or "").replace("\\", "/")
        if module and f"/{module}/" not in f"/{path}" and module.lower() not in path.lower():
            continue
        if path in seen:
            continue
        seen.add(path)
        text = str(pl.get("text") or "")
        m = pat.search(text)
        sig = ""
        if m:
            end = text.find("\n", m.start())
            sig = text[m.start(): end if end > 0 else m.start() + 200].strip()[:200]
        out.append(
            {
                "path": path,
                "object_name": pl.get("object_name"),
                "module_role": pl.get("module_role"),
                "line_in_chunk": _line_of(text, pat),
                "part": pl.get("part"),
                "signature": sig,
                "export": bool(re.search(r"\)\s*Экспорт", sig, re.I)),
            }
        )
        if len(out) >= limit:
            break
    # общий модуль раньше форм/объектов (там обычно «настоящая» реализация)
    out.sort(key=lambda d: (0 if d["path"].startswith("CommonModules/") else 1, d["path"]))
    return out


def find_call_sites(proc: str, module: str = "", *, limit: int = 20, scan: int = 1500) -> tuple[list[dict[str, Any]], int]:
    """Места вызова: MatchText(text, proc) + regex «[Модуль.]Имя(» вне строки объявления."""
    from qdrant_client.http import models as qm

    from vector.catalog import _scroll_payload, collection_name, get_qdrant

    client = get_qdrant()
    col = collection_name("code")
    rows = _scroll_payload(
        client,
        col,
        scroll_filter=qm.Filter(must=[qm.FieldCondition(key="text", match=qm.MatchText(text=proc))]),
        limit=scan,
        max_pages=max(20, scan // 64 + 1),
    )
    call = re.compile(
        (rf"{re.escape(module)}\s*\.\s*" if module else r"(?<![\wА-Яа-яЁё])(?:[\wА-Яа-яЁё]+\s*\.\s*)?")
        + rf"{re.escape(proc)}\s*\(",
        re.I,
    )
    decl = re.compile(rf"(?i)^\s*(?:Процедура|Функция|Procedure|Function)\s+{re.escape(proc)}\s*\(")
    sites: list[dict[str, Any]] = []
    total = 0
    for pl in rows:
        text = str(pl.get("text") or "")
        path = str(pl.get("source_path") or "").replace("\\", "/")
        for i, ln in enumerate(text.splitlines(), 1):
            if decl.search(ln) or not call.search(ln):
                continue
            s = ln.strip()
            if s.startswith("//"):
                continue
            total += 1
            if len(sites) < limit:
                sites.append(
                    {
                        "path": path,
                        "object_name": pl.get("object_name"),
                        "line_in_chunk": i,
                        "part": pl.get("part"),
                        "snippet": s[:240],
                    }
                )
    sites.sort(key=lambda d: d["path"])
    return sites, total


def proc_index(
    request: str = "",
    *,
    procedure: str = "",
    module: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    proc, mod = _resolve_proc(request, procedure, module)
    if re.search(r"\.(epf|erf|cfe)\b", module or "", re.I):
        # модуль — внешний файл: отвечаем только по нему, в CF не уходим
        from tools.epf_inspect import _CACHE, ensure_external_loaded, split_external_path

        _, err = ensure_external_loaded(split_external_path(module)[0])
        if err:
            return {"ok": False, "source": "external_file", "error": err}
        fname = (_CACHE.get("parsed") or {}).get("file_name")
        return external_procs(request, proc, limit=limit) or external_procs(f"{request} {proc}", "", limit=limit) or {
            "ok": True, "source": "external_file", "file": fname, "procedure": proc or None, "definitions": [],
            "note": f"В «{fname}» нет такой процедуры. Список процедур — proc_index(module=\"{fname}\") без request; "
                    f"текст — search_code(module_filter=\"{fname}\", query=…).",
        }
    ext = external_procs(request, proc, limit=limit)
    if ext and (not proc or ext.get("definitions") or not (procedure or "").strip()):
        # имя явно не задано (взято из фразы) — слова совпали с процедурами файла
        # разобрана внешняя обработка (.epf/.erf) и процедура есть в ней — отвечаем по файлу, не по CF
        return ext
    if not proc and (request or "").strip():
        found = semantic_procs(request, limit=limit)
        if found:
            return found
    if not proc:
        return {
            "ok": False,
            "need_clarification": True,
            "question": "Какую процедуру/функцию найти? Напишите её имя (например, ИмяМодуля.ИмяПроцедуры).",
            "error": "Нужно имя процедуры (PascalCase) из этой CF.",
        }
    lim = max(1, min(int(limit or 20), 40))
    try:
        defs = find_definitions(proc, mod, limit=lim)
        if not defs and mod:
            # модуль указан неточно — ищем без него
            defs = find_definitions(proc, "", limit=lim)
        callers, total = find_call_sites(proc, mod, limit=lim)
    except Exception as e:
        return {"ok": False, "error": f"Qdrant недоступен: {e}", "procedure": proc}

    out: dict[str, Any] = {
        "ok": True,
        "procedure": proc,
        "module": mod or None,
        "definitions": defs,
        "definitions_count": len(defs),
        "callers": callers,
        "callers_total": total,
        "prefer": (
            "definitions — где объявлена (path + signature, Экспорт). callers — места вызова "
            "(показаны первые, всего callers_total). Тело — read_code(path=…)."
        ),
        "note": "Точный фильтр по payload Qdrant этой выгрузки (proc_names / text).",
    }
    if not defs:
        similar = similar_procedure_names(proc)
        if similar:
            out["similar_names"] = similar
            out["need_clarification"] = True
            out["question"] = f"Процедуры «{proc}» в конфигурации нет. Возможно, имелась в виду одна из этих?"
            out["options"] = [f"{x['name']} ({x['path']})" for x in similar[:6]]
        out["note"] = (
            f"Объявление «{proc}» в индексе не найдено: это может быть метод платформы, "
            "процедура из расширения или опечатка в имени."
        )
    return out


def external_procs(request: str = "", proc: str = "", *, limit: int = 20) -> dict[str, Any] | None:
    """Процедуры последней разобранной внешней обработки (кэш epf_inspect). None — файла нет."""
    try:
        from tools.epf_inspect import _CACHE
    except Exception:
        return None
    parsed = _CACHE.get("parsed") or {}
    texts = parsed.get("_texts") or []
    if not texts:
        return None
    decl = re.compile(r"(?im)^\s*(?:Процедура|Функция|Procedure|Function)\s+([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)\s*\(")
    toks = [t.lower()[:6] for t in re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", request or "")]
    lim = max(1, min(int(limit or 20), 80))
    defs: list[dict[str, Any]] = []
    callers: list[dict[str, Any]] = []
    listing: list[dict[str, Any]] = []
    call = re.compile(rf"(?<![\wА-Яа-яЁё]){re.escape(proc)}\s*\(", re.I) if proc else None
    for rel, body in texts:
        lines = body.splitlines()
        for i, ln in enumerate(lines, 1):
            m = decl.match(ln)
            if m:
                name = m.group(1)
                row = {"path": rel, "line": i, "name": name, "signature": ln.strip()[:200]}
                if proc and name.lower() == proc.lower():
                    defs.append(row)
                elif not proc and (not toks or any(t in name.lower() for t in toks)):
                    listing.append(row)
                continue
            if call and call.search(ln) and not ln.strip().startswith("//") and len(callers) < lim:
                callers.append({"path": rel, "line": i, "snippet": ln.strip()[:240]})
    base = {
        "ok": True,
        "source": "external_file",
        "file": parsed.get("file_name"),
        "note": "Поиск по коду разобранной внешней обработки (не CF). Тело — read_code(path=…, start_line=line).",
    }
    if proc:
        if not defs:
            # «Материалы» из фразы — не имя процедуры, а слово: ищем по частям имён
            return external_procs(f"{request} {proc}", "", limit=limit) if not callers else None
        return base | {"procedure": proc, "definitions": defs[:lim], "callers": callers, "callers_total": len(callers)}
    if not listing:
        # слова запроса не совпали с именами процедур файла — вопрос, видимо, про CF
        return None
    return base | {"procedures": listing[:lim], "procedures_total": len(listing)}


def similar_procedure_names(proc: str, *, limit: int = 8) -> list[dict[str, str]]:
    """Похожие имена процедур: общие CamelCase-части (≥2) с искомым именем, по payload.proc_names."""
    from qdrant_client.http import models as qm

    from vector.catalog import _scroll_payload, collection_name, get_qdrant

    parts = [p.lower() for p in re.findall(r"[A-ZА-ЯЁ][a-zа-яё0-9]+", proc or "") if len(p) >= 4]
    if len(parts) < 2:
        return []
    client = get_qdrant()
    col = collection_name("code")
    # самая редкая (длинная) часть — для выборки; остальные — для отбора
    key = max(parts, key=len)
    try:
        rows = _scroll_payload(
            client,
            col,
            scroll_filter=qm.Filter(must=[qm.FieldCondition(key="proc_words", match=qm.MatchText(text=key))]),
            limit=600,
            max_pages=12,
        )
    except Exception:
        return []
    seen: dict[str, dict[str, str]] = {}
    for pl in rows:
        names = pl.get("proc_names") or []
        if isinstance(names, str):
            names = names.split()
        for n in names:
            nl = str(n).lower()
            hit = sum(1 for p in parts if p[:5] in nl)
            if hit >= 2 and n not in seen:
                seen[n] = {"name": str(n), "path": str(pl.get("source_path") or ""), "_hit": hit}
    ranked = sorted(seen.values(), key=lambda x: (-x["_hit"], len(x["name"])))
    return [{"name": x["name"], "path": x["path"]} for x in ranked[:limit]]


def semantic_procs(request: str, *, limit: int = 10) -> dict[str, Any] | None:
    """Имени нет — ищем процедуру по смыслу в коллекции onec_procs (1 точка = 1 процедура)."""
    try:
        from vector.procs import search_procs

        hits = search_procs(request, limit=max(1, min(int(limit or 10), 15)))
    except Exception:
        return None
    if not hits:
        return None
    keep = ("proc_name", "signature", "export", "doc", "source_path", "object_name", "module_role", "line_start", "lines", "score")
    return {
        "ok": True,
        "mode": "semantic",
        "matches": [{k: h.get(k) for k in keep if h.get(k) not in (None, "")} for h in hits],
        "prefer": "matches — процедуры по смыслу запроса (path + signature). Тело — read_code(path=…); вызовы — proc_index(procedure=Имя).",
        "note": "Смысловой поиск по коллекции onec_procs этой выгрузки; имя процедуры во фразе не найдено.",
    }
