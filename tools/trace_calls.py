# -*- coding: utf-8 -*-
"""
Универсальный разбор цепочек вызовов BSL (без хардкода имён объектов).

Runtime: Qdrant only.
"""

from __future__ import annotations

import re
from typing import Any

from tools.code_search import find_modules
from vector.catalog import (
    assemble_module_text,
    code_collection_ready,
    get_chunks_by_source_path,
    meta_from_path,
    module_outline_from_qdrant,
    strip_context_header,
)
from vector.search import search_code_vectors

# Вызов: ИмяПроцедуры(  |  Модуль.Имя(  |  ОбщийМодуль.Имя(
_CALL_RE = re.compile(
    r"(?<![\wА-Яа-яЁё.])"
    r"((?:[A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*\s*\.\s*){0,3})"
    r"([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)\s*\(",
    re.M,
)
_PROC_START = re.compile(
    r"^\s*(Процедура|Функция|Procedure|Function)\s+"
    r"([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)",
    re.M | re.I,
)
_SKIP_CALLS = frozenset(
    {
        "если",
        "пока",
        "для",
        "новый",
        "new",
        "if",
        "while",
        "for",
        "возврат",
        "return",
        "не",
        "и",
        "или",
        "try",
        "except",
        "raise",
        "typeof",
        "type",
        "valueisfilled",
        "значенизаполнено",
        "strtemplate",
        "нстр",
        "нрег",
        "врег",
        "сокрлп",
        "формат",
        "строка",
        "число",
        "дата",
        "булево",
        "массив",
        "структура",
        "соответствие",
        "списокзначений",
        "запрос",
        "выбрать",
    }
)


def _norm(s: str) -> str:
    return (s or "").lower().replace("ё", "е")


def extract_calls_from_text(text: str, *, max_calls: int = 40) -> list[dict[str, Any]]:
    body = strip_context_header(text or "")
    # map line offsets
    lines = body.splitlines()
    calls: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in _CALL_RE.finditer(body):
        prefix = re.sub(r"\s+", "", m.group(1) or "")
        name = m.group(2) or ""
        nl = _norm(name)
        if nl in _SKIP_CALLS or len(name) < 3:
            continue
        # skip property-like very short after keywords
        key = f"{prefix}.{name}".lower()
        if key in seen:
            continue
        seen.add(key)
        line_no = body.count("\n", 0, m.start()) + 1
        snip = lines[line_no - 1].strip()[:180] if 0 < line_no <= len(lines) else ""
        calls.append(
            {
                "module_prefix": prefix.rstrip(".") if prefix else "",
                "name": name,
                "line": line_no,
                "snippet": snip,
            }
        )
        if len(calls) >= max_calls:
            break
    return calls


def extract_procedures(text: str) -> list[dict[str, Any]]:
    body = strip_context_header(text or "")
    out = []
    for m in _PROC_START.finditer(body):
        line_no = body.count("\n", 0, m.start()) + 1
        out.append({"type": m.group(1), "name": m.group(2), "line": line_no})
    return out


def _load_module_text(path: str) -> tuple[str, dict[str, Any]]:
    chunks = get_chunks_by_source_path(path)
    if not chunks:
        return "", {}
    return assemble_module_text(chunks), meta_from_path(path, chunks[0])


def _resolve_call_target(
    call: dict[str, Any],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    prefix = call.get("module_prefix") or ""
    name = call.get("name") or ""
    # точное объявление по payload.proc_names (быстро и без «похожих» модулей)
    try:
        from tools.proc_index import find_definitions

        defs = find_definitions(name, prefix, limit=limit) if name else []
    except Exception:
        defs = []
    if defs:
        return [
            {
                "path": d["path"],
                "object_name": d.get("object_name"),
                "called_name": name,
                "module_prefix": prefix,
                "signature": d.get("signature"),
                "resolved": True,
            }
            for d in defs[:limit]
        ]
    search = f"{prefix} {name}".strip() if prefix else name
    # prefer CommonModules if no prefix
    found = find_modules(search, limit=limit)
    mods = list(found.get("modules") or [])
    # filter by procedure name in outline if possible
    resolved = []
    for m in mods[:limit]:
        path = m.get("path") or ""
        item = {
            "path": path,
            "full_name_1c": m.get("full_name_1c"),
            "object_name": m.get("object_name"),
            "kind": m.get("kind"),
            "score": m.get("score"),
            "called_name": name,
            "module_prefix": prefix,
        }
        if path:
            ol = module_outline_from_qdrant(path, max_symbols=80)
            syms = ol.get("symbols") or []
            hit = [s for s in syms if _norm(s.get("name") or "") == _norm(name)]
            if hit:
                item["procedure_line"] = hit[0].get("line")
                item["resolved"] = True
            else:
                item["resolved"] = False
        resolved.append(item)
    return resolved


def trace_calls(
    request: str = "",
    *,
    path: str = "",
    procedure: str = "",
    depth: int = 2,
    max_edges: int = 30,
    max_calls_per_node: int = 20,
) -> dict[str, Any]:
    """
    Построить цепочку вызовов от модуля/процедуры.

    Универсально: любой path из Qdrant или request-фраза.
    """
    st = code_collection_ready()
    if not st.get("ready"):
        return {"ok": False, "error": st.get("error") or st.get("hint"), "source": "qdrant"}

    depth = max(1, min(int(depth or 2), 3))
    max_edges = max(5, min(int(max_edges or 30), 60))
    start_path = (path or "").replace("\\", "/").strip()
    proc = (procedure or "").strip()
    req = (request or "").strip()

    # процедура названа явно → стартовый модуль = место её объявления (proc_names), а не векторный поиск
    if not start_path:
        try:
            from tools.proc_index import find_definitions
            from tools.router_detect import _proc_candidate

            cand = proc
            if not cand:
                cand, _mod = _proc_candidate(req)
            defs = find_definitions(cand, "", limit=40) if cand else []
            if defs:
                pick = None
                if len(defs) > 1:
                    from tools.name_match import resolve_object_full_name

                    leaf = (resolve_object_full_name(req.replace(cand, " ")) or "").split(".")[-1]
                    if leaf:
                        own = [d for d in defs if f"/{leaf}/" in d["path"]]
                        # объектный модуль раньше формы
                        own.sort(key=lambda d: ("/Forms/" in d["path"], d["path"]))
                        pick = own[0] if own else None
                if pick is None and (len(defs) == 1 or defs[0]["path"].startswith("CommonModules/")):
                    pick = defs[0]
                if pick is not None:
                    start_path = pick["path"]
                    proc = cand
        except Exception:
            pass

    if not start_path and req:
        # try find module
        fm = find_modules(req, limit=15)
        mods = list(fm.get("modules") or [])

        # ОбработкаПроведения / проведение документа → ObjectModule, не Manager
        want_posting = bool(
            re.search(
                r"обработкапроведения|проведен|posting",
                (req + " " + proc).lower().replace("ё", "е"),
            )
        )
        # «отследи процедуру / как работает» — ObjectModule документа
        want_proc_trace = bool(
            re.search(
                r"отслед|прослед|как\s+работает|заполнитьдокумент|наосновании",
                (req + " " + proc).lower().replace("ё", "е"),
            )
        )
        if want_posting and not proc:
            proc = "ОбработкаПроведения"

        # длинный PascalCase = имя процедуры (не «Документ»)
        if not proc:
            ids = re.findall(r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_]{6,}", req)
            skip = {
                "Документ",
                "Справочник",
                "Процедура",
                "Функция",
                "Обработка",
                "Модуль",
                "Document",
                "Catalog",
            }
            ids = [i for i in ids if i not in skip]
            if ids:
                proc = max(ids, key=len)

        def _path_score(m: dict[str, Any]) -> float:
            sp = (m.get("path") or "").replace("\\", "/")
            sc = float(m.get("score") or 0)
            low = sp.lower()
            if want_posting or want_proc_trace:
                if "objectmodule" in low and "/forms/" not in low:
                    sc += 100_000
                if "managermodule" in low:
                    sc -= 50_000
                if "/forms/" in low:
                    sc -= 80_000
            # имя объекта из фразы в path
            for t in re.findall(r"[A-Za-zА-Яа-яЁё_]{5,}", req):
                if t.lower() in (
                    "документ",
                    "проведение",
                    "обработка",
                    "цепочка",
                    "процедура",
                    "функция",
                    "отследи",
                ):
                    continue
                if t in sp:
                    sc += 5_000
            return sc

        if mods:
            mods = sorted(mods, key=_path_score, reverse=True)
            start_path = (mods[0].get("path") or "").replace("\\", "/")

        # resolve document leaf from phrase (корректировка вида деятельности)
        obj_hint = ""
        mdoc = re.search(
            r"(?:Документ|Document)\s*\.\s*([A-Za-zА-Яа-яЁё_]\w*)",
            req,
            re.I,
        )
        if mdoc:
            obj_hint = mdoc.group(1)
        # «в документе <фраза>» — основной источник имени объекта
        doc_phrase = ""
        mdoc_ru = re.search(
            r"в\s+документ[еу]?\s+(.+?)(?:,|;|\.|$|отслед|прослед|как\s+работает)",
            req,
            re.I | re.S,
        )
        if mdoc_ru:
            doc_phrase = (mdoc_ru.group(1) or "").strip(" \t,.;")
        if not obj_hint:
            try:
                from tools.object_movements import (
                    _resolve_object_from_phrase,
                )

                if doc_phrase:
                    obj_hint = _resolve_object_from_phrase(doc_phrase) or ""
                # убрать имя процедуры из фразы, оставить документ
                cleaned = req
                if proc:
                    cleaned = cleaned.replace(proc, " ")
                cleaned = re.sub(
                    r"\b(отследи|отследить|проследи|как\s+работает|эта\s+процедура|"
                    r"процедур\w*|функци\w*|в\s+документ\w*)\b",
                    " ",
                    cleaned,
                    flags=re.I,
                )
                if not obj_hint:
                    obj_hint = _resolve_object_from_phrase(cleaned) or ""
                if not obj_hint:
                    obj_hint = _resolve_object_from_phrase(req) or ""
            except Exception:
                obj_hint = ""
        if not obj_hint:
            # PascalCase из фразы: структурно (is_pascal), не доменный whitelist
            try:
                from tools.name_match import (
                    is_pascal_identifier,
                    pascal_candidates_in_text,
                    resolve_object_name,
                )

                if doc_phrase:
                    obj_hint = resolve_object_name(doc_phrase) or obj_hint
                if not obj_hint:
                    ex = {proc} if proc else set()
                    for t in pascal_candidates_in_text(req, exclude=ex):
                        if t.lower() in ("обработкапроведения", "проведения"):
                            continue
                        if is_pascal_identifier(t) and (not proc or t != proc):
                            # предпочесть не-имя процедуры (короче / другое)
                            if proc and len(t) >= len(proc) - 2:
                                continue
                            obj_hint = t
                            break
            except Exception:
                pass

        # канонический ObjectModule документа (posting ИЛИ trace процедуры)
        if obj_hint and (want_posting or want_proc_trace or proc):
            from vector.catalog import get_chunks_by_source_path

            for folder in ("Documents", "Catalogs", "DataProcessors"):
                canon = f"{folder}/{obj_hint}/Ext/ObjectModule.bsl"
                if get_chunks_by_source_path(canon):
                    start_path = canon
                    if want_posting:
                        proc = proc or "ОбработкаПроведения"
                    break
            # если resolve дал full_name leaf wrong — find_modules по hint
            if start_path and obj_hint and obj_hint not in start_path:
                fm2 = find_modules(obj_hint, limit=8)
                for m in fm2.get("modules") or []:
                    sp = (m.get("path") or "").replace("\\", "/")
                    if "ObjectModule" in sp and "/Forms/" not in sp:
                        start_path = sp
                        break

        # процедура должна быть В start_path; иначе outline/search scoped
        if proc and start_path:
            text0, _meta0 = _load_module_text(start_path)
            names0 = {_norm(p["name"]) for p in extract_procedures(text0 or "")}
            if _norm(proc) not in names0:
                # search only among modules of same object folder
                obj_folder = ""
                parts = start_path.replace("\\", "/").split("/")
                if len(parts) >= 2:
                    obj_folder = f"{parts[0]}/{parts[1]}/"
                found_local = False
                if obj_folder:
                    fm3 = find_modules(parts[1] if len(parts) > 1 else proc, limit=12)
                    for m in fm3.get("modules") or []:
                        sp = (m.get("path") or "").replace("\\", "/")
                        if not sp.endswith(".bsl") or obj_folder not in sp:
                            continue
                        t2, _ = _load_module_text(sp)
                        n2 = {_norm(p["name"]) for p in extract_procedures(t2 or "")}
                        if _norm(proc) in n2:
                            start_path = sp
                            found_local = True
                            break
                if not found_local:
                    # 1) кандидаты Documents/*/ObjectModule где есть процедура + affinity к фразе
                    phrase_toks = re.findall(
                        r"[а-яa-zё]{4,}",
                        (doc_phrase or req).lower().replace("ё", "е"),
                    )
                    phrase_toks = [
                        t
                        for t in phrase_toks
                        if t
                        not in (
                            "документ",
                            "процедура",
                            "функция",
                            "отследи",
                            "работает",
                            "эта",
                        )
                    ]
                    # find_modules по procedure name часто 0 — ищем по object_hint / camel
                    candidates: list[str] = []
                    if obj_hint:
                        candidates.append(
                            f"Documents/{obj_hint}/Ext/ObjectModule.bsl"
                        )
                    for qtry in (
                        [obj_hint] if obj_hint else []
                    ) + ([doc_phrase] if doc_phrase else []) + [proc[:24]]:
                        if not qtry:
                            continue
                        try:
                            fm4 = find_modules(str(qtry), limit=10)
                        except Exception:
                            fm4 = {}
                        for m in fm4.get("modules") or []:
                            sp = (m.get("path") or "").replace("\\", "/")
                            if sp.endswith(".bsl"):
                                candidates.append(sp)
                    # vector as last
                    sc = search_code_vectors(
                        f"Процедура {proc}", limit=12, match_mode="hybrid"
                    )
                    for m in sc.get("matches") or []:
                        sp = (m.get("path") or "").replace("\\", "/")
                        if sp:
                            candidates.append(sp)

                    def _aff(sp: str) -> float:
                        low = sp.lower().replace("ё", "е")
                        s = 0.0
                        if obj_hint and obj_hint.lower() in low:
                            s += 500
                        if "objectmodule" in low.replace("/", ""):
                            s += 100
                        for t in phrase_toks:
                            if t[:5] in low or t in low:
                                s += 80
                        return s

                    best_sp = ""
                    best_a = -1e9
                    for sp in dict.fromkeys(candidates):
                        t2, _ = _load_module_text(sp)
                        n2 = {_norm(p["name"]) for p in extract_procedures(t2 or "")}
                        if _norm(proc) not in n2:
                            continue
                        a = _aff(sp)
                        if a > best_a:
                            best_a = a
                            best_sp = sp
                    if best_sp:
                        start_path = best_sp

        if not proc:
            m = re.search(
                r"(процедур[аыуе]\s+|функци[яи]\s+)?"
                r"([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]{3,})",
                req,
                re.I,
            )
            if m:
                cand = m.group(2)
                if _norm(cand) not in (
                    "документ",
                    "модуль",
                    "объект",
                    "проведение",
                    "заполнения",
                    "цепочка",
                    "вызовов",
                ):
                    proc = cand

    if not start_path:
        # last resort: exact search for procedure
        if proc:
            sc = search_code_vectors(
                f"Процедура {proc}", limit=8, match_mode="hybrid"
            )
            ranked = sorted(
                sc.get("matches") or [],
                key=lambda m: (
                    100 if "ObjectModule" in (m.get("path") or "") else 0,
                    float(m.get("score") or 0),
                ),
                reverse=True,
            )
            for m in ranked:
                if m.get("path"):
                    start_path = m["path"]
                    break
        if not start_path:
            return {
                "ok": False,
                "error": "Не найден стартовый модуль. Укажите path= или request с именем объекта/процедуры.",
                "source": "qdrant",
            }

    # clamp depth for speed
    if re.search(r"обработкапроведения|проведен", (req + proc).lower().replace("ё", "е")):
        depth = min(depth, 2)
        max_edges = min(max_edges, 40)
        max_calls_per_node = min(max_calls_per_node, 25)

    edges: list[dict[str, Any]] = []
    nodes: dict[str, dict[str, Any]] = {}
    queue: list[tuple[str, str, int]] = [(start_path, proc, 0)]  # path, focus_proc, depth
    visited: set[str] = set()

    while queue and len(edges) < max_edges:
        cur_path, focus, d = queue.pop(0)
        key = f"{cur_path}|{focus}|{d}"
        if key in visited:
            continue
        visited.add(key)

        text, meta = _load_module_text(cur_path)
        if not text:
            continue
        nodes[cur_path] = {
            "path": cur_path,
            "full_name_1c": meta.get("full_name_1c"),
            "object_name": meta.get("object_name"),
            "kind": meta.get("kind"),
            "procedures": [p["name"] for p in extract_procedures(text)[:40]],
            "procedures_total": len(extract_procedures(text)),
        }

        # if focus procedure — slice body roughly between this and next proc
        body = text
        if focus:
            procs = extract_procedures(text)
            start_line = None
            end_line = None
            for i, p in enumerate(procs):
                if _norm(p["name"]) == _norm(focus):
                    start_line = p["line"]
                    if i + 1 < len(procs):
                        end_line = procs[i + 1]["line"] - 1
                    break
            if start_line:
                lines = text.splitlines()
                end = end_line or len(lines)
                body = "\n".join(lines[start_line - 1 : end])

        calls = extract_calls_from_text(body, max_calls=max_calls_per_node)
        for call in calls:
            targets = _resolve_call_target(call, limit=2)
            edge = {
                "from_path": cur_path,
                "from_procedure": focus or "(module)",
                "call": call,
                "targets": targets,
                "depth": d,
            }
            edges.append(edge)
            if d + 1 < depth:
                for t in targets:
                    if t.get("path") and t.get("resolved"):
                        queue.append(
                            (t["path"], call["name"], d + 1)
                        )
            if len(edges) >= max_edges:
                break

    # summary tree
    summary_lines = [
        f"Старт: {start_path}" + (f" :: {proc}" if proc else ""),
        f"Узлов: {len(nodes)}, рёбер: {len(edges)}, depth≤{depth}",
    ]
    if len(edges) > 15:
        summary_lines.append(f"  … показано 15 из {len(edges)} рёбер; полный список — edges")
    for e in edges[:15]:
        tg = e["targets"][0] if e.get("targets") else {}
        summary_lines.append(
            f"  [d{e['depth']}] {e['from_procedure']} → "
            f"{e['call'].get('module_prefix','')}.{e['call'].get('name')} "
            f"@ {tg.get('path') or '?'} L{tg.get('procedure_line') or e['call'].get('line')}"
        )

    return {
        "ok": True,
        "source": "qdrant",
        "start_path": start_path,
        "start_procedure": proc or None,
        "depth": depth,
        "nodes": list(nodes.values()),
        "edges": edges,
        "edges_count": len(edges),
        "summary_lines": summary_lines,
        "prefer": (
            "Покажи пользователю summary_lines и ключевые edges "
            "(from → call → target path/line). "
            "Для тела: read_code(path, start_line=procedure_line). "
            "Не выдумывай вызовы вне edges."
        ),
        "note": "Универсальный BSL call-graph (эвристика, не AST). Глубина ограничена budget.",
    }
