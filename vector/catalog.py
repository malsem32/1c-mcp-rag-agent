# -*- coding: utf-8 -*-
"""
Каталог кода конфигурации **только из Qdrant**.

knowledge/код_конфигурации — только для scripts/prepare + index.
Runtime-агент (find_modules / read_code / outline / search) ходит сюда.
"""

from __future__ import annotations

import re
from typing import Any

from qdrant_client.http import models as qm

from config import load_config
from vector.client import collection_name, get_qdrant, qdrant_enabled
from vector.search import _collection_points, search_code_vectors

_PROC_RE = re.compile(
    r"^\s*(Процедура|Функция|Procedure|Function)\s+([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)",
    re.M | re.I,
)
_CONTEXT_END = "// --- КОНЕЦ КОНТЕКСТА ---"


def _cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    return cfg if cfg is not None else load_config()


def runtime_fs_allowed(cfg: dict[str, Any] | None = None) -> bool:
    """Можно ли читать knowledge/код_конфигурации в runtime (по умолчанию НЕТ)."""
    q = (_cfg(cfg).get("qdrant") or {})
    return bool(q.get("fallback_filesystem", False))


def code_collection_ready(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = _cfg(cfg)
    if not qdrant_enabled(cfg):
        return {
            "ok": False,
            "ready": False,
            "source": "qdrant",
            "error": "qdrant.enabled=false",
            "hint": "Включите qdrant в config.yaml и выполните index_qdrant.py",
        }
    try:
        client = get_qdrant(cfg)
        col = collection_name("code", cfg)
        n = _collection_points(client, col)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "ready": False,
            "source": "qdrant",
            "error": str(exc),
            "hint": "python scripts/index_qdrant.py --recreate",
        }
    if n <= 0:
        return {
            "ok": False,
            "ready": False,
            "source": "qdrant",
            "points": 0,
            "collection": col,
            "hint": (
                "Коллекция пуста. Выгрузка → prepare → index:\n"
                "  python scripts/prepare_config_for_qdrant.py\n"
                "  python scripts/index_qdrant.py --recreate\n"
                "Папка knowledge/код_конфигурации в runtime не читается."
            ),
        }
    return {
        "ok": True,
        "ready": True,
        "source": "qdrant",
        "points": n,
        "collection": col,
        "mode": (cfg.get("qdrant") or {}).get("mode") or "local",
        "note": (
            "Код только из Qdrant. "
            "knowledge/код_конфигурации — только для prepare/index."
        ),
    }


def strip_context_header(text: str) -> str:
    t = text or ""
    if _CONTEXT_END in t:
        return t.split(_CONTEXT_END, 1)[-1].lstrip("\n")
    return t


def meta_from_path(source_path: str, payload: dict[str, Any] | None = None) -> dict[str, str]:
    """Восстановить form_name / kind / full_name_1c из path + payload."""
    pl = payload or {}
    sp = (source_path or pl.get("source_path") or "").replace("\\", "/")
    parts = [p for p in sp.split("/") if p]
    object_name = str(pl.get("object_name") or "")
    form_name = str(pl.get("form_name") or "")
    class_en = str(pl.get("metadata_class_en") or (parts[0] if parts else ""))
    class_ru = str(pl.get("metadata_class") or "")
    if not object_name and len(parts) >= 2:
        object_name = parts[1]
    if not form_name and "Forms" in parts:
        i = parts.index("Forms")
        if i + 1 < len(parts):
            form_name = parts[i + 1]
    fname = parts[-1] if parts else ""
    low = fname.lower()
    module_role = "module"
    kind = "module"
    if "forms" in [p.lower() for p in parts]:
        module_role = "form"
        kind = "form_module"
    elif "objectmodule" in low:
        module_role = "object"
        kind = "object_module"
    elif "managermodule" in low:
        module_role = "manager"
        kind = "manager_module"
    elif "commandmodule" in low:
        module_role = "command"
        kind = "command_module"
    elif "recordsetmodule" in low:
        module_role = "recordset"
        kind = "recordset_module"
    elif class_en == "CommonModules":
        kind = "common_module"
    elif class_en == "CommonForms":
        kind = "common_form"
        form_name = form_name or object_name

    prefix_map = {
        "Documents": "Документ",
        "Catalogs": "Справочник",
        "DataProcessors": "Обработка",
        "Reports": "Отчет",
        "InformationRegisters": "РегистрСведений",
        "AccumulationRegisters": "РегистрНакопления",
        "CommonModules": "ОбщийМодуль",
        "CommonForms": "ОбщаяФорма",
    }
    prefix = prefix_map.get(class_en, class_ru or class_en)
    full_name_1c = str(pl.get("full_name_1c") or "")
    if not full_name_1c and object_name:
        if kind == "form_module" and form_name:
            full_name_1c = f"{prefix}.{object_name}.Форма.{form_name}"
        elif kind == "common_form":
            full_name_1c = f"ОбщаяФорма.{object_name}"
        elif kind == "common_module":
            full_name_1c = f"ОбщийМодуль.{object_name}"
        else:
            full_name_1c = f"{prefix}.{object_name}"

    return {
        "source_path": sp,
        "object_name": object_name,
        "form_name": form_name,
        "metadata_class": class_ru,
        "metadata_class_en": class_en,
        "kind": kind,
        "module_role": module_role,
        "full_name_1c": full_name_1c,
        "location": str(pl.get("location") or ""),
        "xml_tag": str(pl.get("xml_tag") or fname),
        "content_type": str(pl.get("content_type") or ""),
    }


def _scroll_payload(
    client: Any,
    col: str,
    *,
    scroll_filter: Any = None,
    limit: int = 64,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    offset = None
    pages = 0
    while pages < max_pages and len(out) < limit:
        pages += 1
        records, offset = client.scroll(
            collection_name=col,
            scroll_filter=scroll_filter,
            limit=min(64, limit - len(out)),
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for rec in records or []:
            pl = dict(getattr(rec, "payload", None) or {})
            pl["_id"] = str(getattr(rec, "id", ""))
            out.append(pl)
        if offset is None:
            break
    return out


_CHUNKS_CACHE: dict[tuple[str, str], tuple[dict[str, Any], ...]] = {}


def get_chunks_by_source_path(
    source_path: str,
    *,
    cfg: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Все части модуля с данным source_path (чанки prepare)."""
    cfg = _cfg(cfg)
    path = (source_path or "").replace("\\", "/").strip()
    if not path:
        return []
    key = (collection_name("code", cfg), path)
    hit = _CHUNKS_CACHE.get(key)
    if hit is not None:
        return list(hit)
    client = get_qdrant(cfg)
    col = collection_name("code", cfg)
    # source_path проиндексирован как TEXT: MatchValue по нему = полный скан (0.3–0.7 с).
    # MatchText по всем токенам пути идёт через индекс; точное равенство проверяем в Python.
    indexed_ok = False
    try:
        cand = _scroll_payload(
            client,
            col,
            scroll_filter=qm.Filter(
                must=[qm.FieldCondition(key="source_path", match=qm.MatchText(text=path.replace("/", " ").replace(".", " ")))]
            ),
            limit=400,
        )
        indexed_ok = True
        exact = [c for c in cand if (c.get("source_path") or "").replace("\\", "/") == path]
        if not exact:
            # такого модуля нет — не делаем полный скан; кэшируем «пусто»
            _CHUNKS_CACHE[key] = tuple()
            return []
        if exact:
            exact = sorted(exact, key=lambda c: int(c.get("part") or 0))
            if len(_CHUNKS_CACHE) > 400:
                _CHUNKS_CACHE.clear()
            _CHUNKS_CACHE[key] = tuple(exact)
            return exact
    except Exception:
        pass
    # exact (медленный путь — если текстовый индекс недоступен)
    chunks = _scroll_payload(
        client,
        col,
        scroll_filter=qm.Filter(
            must=[
                qm.FieldCondition(
                    key="source_path",
                    match=qm.MatchValue(value=path),
                )
            ]
        ),
        limit=80,
    )
    if chunks:
        return sorted(chunks, key=lambda c: int(c.get("part") or 0))
    # suffix / contains via scroll-all is expensive — try MatchText
    try:
        chunks = _scroll_payload(
            client,
            col,
            scroll_filter=qm.Filter(
                must=[
                    qm.FieldCondition(
                        key="source_path",
                        match=qm.MatchText(text=path.split("/")[-1]),
                    )
                ]
            ),
            limit=80,
        )
        chunks = [
            c
            for c in chunks
            if (c.get("source_path") or "").replace("\\", "/") == path
            or path in (c.get("source_path") or "").replace("\\", "/")
        ]
        return sorted(chunks, key=lambda c: int(c.get("part") or 0))
    except Exception:
        return []


def assemble_module_text(chunks: list[dict[str, Any]]) -> str:
    if not chunks:
        return ""
    parts = sorted(chunks, key=lambda c: int(c.get("part") or 0))
    bodies = [b for b in (strip_context_header(c.get("text") or "") for c in parts) if b.strip()]
    if not bodies:
        return ""
    # склейка частей (prepare режет по процедурам); внутри гигантской процедуры — без overlap
    out = bodies[0]
    for b in bodies[1:]:
        k = _overlap_len(out, b)
        out = out + b[k:] if k else out + "\n\n" + b
    return out


_PROC_END_RE = re.compile(r"(КонецПроцедуры|КонецФункции|EndProcedure|EndFunction)\s*$", re.I)


def _overlap_len(prev: str, nxt: str) -> int:
    """Длина начала nxt, повторяющего хвост prev (overlap hard-split; может начинаться с середины строки)."""
    if _PROC_END_RE.search(prev):
        return 0  # стык между процедурами — overlap не бывает
    tail = prev.rstrip()
    best, pos = 0, nxt.find("\n")
    while 0 < pos <= 600:
        seg = nxt[:pos].strip()
        if len(seg) >= 15 and tail.endswith(seg):
            best = pos
        pos = nxt.find("\n", pos + 1)
    return best


def read_module(
    source_path: str = "",
    *,
    start_line: int = 1,
    max_lines: int = 80,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    st = code_collection_ready(cfg)
    if not st.get("ready"):
        return {**st, "error": st.get("error") or st.get("hint")}

    path = (source_path or "").replace("\\", "/").strip()
    if not path:
        return {"ok": False, "error": "Нужен path (source_path из find_modules / search_code)"}

    chunks = get_chunks_by_source_path(path, cfg=cfg)
    if not chunks:
        # semantic fallback: search by path string
        vec = search_code_vectors(path, limit=15, cfg=cfg)
        for m in vec.get("matches") or []:
            sp = (m.get("path") or "").replace("\\", "/")
            if sp == path or path.endswith(sp) or sp.endswith(path):
                chunks = get_chunks_by_source_path(sp, cfg=cfg)
                path = sp
                break
        if not chunks and vec.get("matches"):
            # take best match path
            sp = (vec["matches"][0].get("path") or "").replace("\\", "/")
            chunks = get_chunks_by_source_path(sp, cfg=cfg)
            if chunks:
                path = sp

    if not chunks:
        return {
            "ok": False,
            "error": f"В Qdrant нет path: {source_path}",
            "hint": "find_modules / search_code → path из matches. Переиндексируйте dump.",
            "source": "qdrant",
        }

    full = assemble_module_text(chunks)
    lines = full.splitlines()
    start = max(1, int(start_line or 1))
    max_lines = max(1, min(int(max_lines or 80), 200))
    end = min(len(lines), start + max_lines - 1)
    window = lines[start - 1 : end]
    meta = meta_from_path(path, chunks[0])
    return {
        "ok": True,
        "source": "qdrant",
        "path": path,
        "rel_path": path,
        "full_name_1c": meta.get("full_name_1c"),
        "form_name": meta.get("form_name"),
        "object_name": meta.get("object_name"),
        "kind": meta.get("kind"),
        "start_line": start,
        "end_line": end,
        "total_lines": len(lines),
        "lines": [
            {"line": start + i, "text": ln[:500]}
            for i, ln in enumerate(window)
        ],
        "text": "\n".join(window),
        "truncated": end < len(lines),
        "chunks_parts": len(chunks),
        "prefer": "Не читай весь модуль; outline → окна start_line.",
    }


def module_outline_from_qdrant(
    source_path: str = "",
    *,
    max_symbols: int = 200,
    name_filter: str = "",
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    st = code_collection_ready(cfg)
    if not st.get("ready"):
        return {**st, "error": st.get("error") or st.get("hint")}

    path = (source_path or "").replace("\\", "/").strip()
    chunks = get_chunks_by_source_path(path, cfg=cfg) if path else []
    if not chunks and path:
        # try as module name search
        found = find_modules_qdrant(path, limit=3, cfg=cfg)
        mods = found.get("modules") or []
        if mods:
            path = mods[0]["path"]
            chunks = get_chunks_by_source_path(path, cfg=cfg)

    if not chunks:
        return {
            "ok": False,
            "error": f"Модуль не найден в Qdrant: {source_path}",
            "source": "qdrant",
        }

    full = assemble_module_text(chunks)
    all_symbols: list[dict[str, Any]] = []
    for m in _PROC_RE.finditer(full):
        line_no = full.count("\n", 0, m.start()) + 1
        all_symbols.append(
            {
                "line": line_no,
                "type": "Процедура" if m.group(1).lower().startswith("п") or m.group(1).lower() == "procedure" else "Функция",
                "name": m.group(2),
            }
        )

    # filter by name (печать / макет / …) — иначе «голова» модуля без печати
    nf = (name_filter or "").strip().lower().replace("ё", "е")
    if nf:
        tokens = [t for t in re.findall(r"[a-zа-яё0-9_]+", nf) if len(t) >= 3]
        filtered = []
        for s in all_symbols:
            nm = str(s.get("name") or "").lower().replace("ё", "е")
            if any(t in nm for t in tokens):
                filtered.append(s)
        symbols = filtered if filtered else all_symbols
    else:
        symbols = all_symbols

    cap = max(20, min(int(max_symbols or 200), 400))
    truncated = len(symbols) > cap
    # приоритет: печать/макет/запрос в начале выдачи
    def _prio(s: dict[str, Any]) -> int:
        n = str(s.get("name") or "").lower()
        if any(x in n for x in ("печат", "макет", "накладн", "пф_", "print")):
            return 0
        if any(x in n for x in ("запрос", "текст", "query")):
            return 1
        return 2

    symbols_sorted = sorted(symbols, key=lambda s: (_prio(s), int(s.get("line") or 0)))
    out_syms = symbols_sorted[:cap]

    meta = meta_from_path(path, chunks[0])
    total = full.count("\n") + (1 if full else 0)
    return {
        "ok": True,
        "source": "qdrant",
        "path": path,
        "rel_path": path,
        "kind": meta.get("kind"),
        "full_name_1c": meta.get("full_name_1c"),
        "form_name": meta.get("form_name"),
        "object_name": meta.get("object_name"),
        "total_lines_est": total,
        "symbols": out_syms,
        "symbols_count": len(out_syms),
        "symbols_total": len(all_symbols),
        "name_filter": name_filter or None,
        "truncated": truncated or len(all_symbols) > cap,
        "file_truncated": False,
        "prefer": (
            "symbols: name+line. Печать/макеты — в начале списка. "
            "read_code(path, start_line=line, max_lines=80). "
            f"Всего процедур в модуле: {len(all_symbols)}."
        ),
    }


def find_modules_qdrant(
    search: str = "",
    *,
    kind: str = "",
    limit: int = 20,
    cfg: dict[str, Any] | None = None,
    form_name: str = "",
    object_name: str = "",
    want_form_module: bool = False,
) -> dict[str, Any]:
    """Поиск модулей по Qdrant (семантика + ранжирование payload)."""
    cfg = _cfg(cfg)
    st = code_collection_ready(cfg)
    if not st.get("ready"):
        return {**st, "modules": [], "count": 0}

    q = (search or "").strip()
    if not q and not object_name:
        return {
            "ok": False,
            "error": "Пустой search",
            "modules": [],
            "count": 0,
            "source": "qdrant",
        }

    query = q or object_name
    if form_name and form_name.lower() not in query.lower():
        query = f"{query} {form_name}"
    if want_form_module and "форм" not in query.lower():
        query = f"модуль формы {query}"

    lim = max(1, min(int(limit or 20), 40))
    want_form = want_form_module or (kind or "").lower() in ("form", "form_module")

    # --- 1) payload-поиск по имени объекта (точнее семантики для find_modules) ---
    by_path: dict[str, dict[str, Any]] = {}
    client = get_qdrant(cfg)
    col = collection_name("code", cfg)

    name_keys: list[str] = []
    if object_name:
        name_keys.append(object_name)
    for tok in re.findall(r"[A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]{3,}", q):
        if tok.lower() in (
            "модуль",
            "форма",
            "формы",
            "документ",
            "документа",
            "справочник",
            "обработка",
        ):
            continue
        if tok.lower().startswith("форма") and len(tok) > 5:
            continue
        name_keys.append(tok)
    name_keys = list(dict.fromkeys(name_keys))

    def _add_from_payload(pl: dict[str, Any], base: float) -> None:
        sp = (pl.get("source_path") or "").replace("\\", "/")
        if not sp:
            return
        meta = meta_from_path(sp, pl)
        sc = _rank_module(
            meta,
            search=q,
            kind=kind,
            form_name=form_name,
            object_name=object_name or "",
            want_form=want_form,
            base_score=base,
        )
        prev = by_path.get(sp)
        if prev is None or sc > prev["score"]:
            by_path[sp] = {
                "path": sp,
                "rel_path": sp,
                "kind": meta["kind"],
                "kind_ru": meta["kind"],
                "object_name": meta["object_name"],
                "form_name": meta["form_name"],
                "full_name_1c": meta["full_name_1c"],
                "location": meta["location"],
                "metadata_class": meta["metadata_class"],
                "module_role": meta["module_role"],
                "score": sc,
                "source": "qdrant",
            }

    for key in name_keys[:5]:
        try:
            # exact object_name
            for pl in _scroll_payload(
                client,
                col,
                scroll_filter=qm.Filter(
                    must=[
                        qm.FieldCondition(
                            key="object_name",
                            match=qm.MatchValue(value=key),
                        )
                    ]
                ),
                limit=40,
                max_pages=3,
            ):
                _add_from_payload(pl, base=80.0)
        except Exception:
            pass
        try:
            # path contains object folder name
            for pl in _scroll_payload(
                client,
                col,
                scroll_filter=qm.Filter(
                    must=[
                        qm.FieldCondition(
                            key="source_path",
                            match=qm.MatchText(text=key),
                        )
                    ]
                ),
                limit=40,
                max_pages=3,
            ):
                sp = (pl.get("source_path") or "").replace("\\", "/")
                if key.lower() not in sp.lower() and key.lower() not in (
                    pl.get("object_name") or ""
                ).lower():
                    continue
                _add_from_payload(pl, base=70.0)
        except Exception:
            pass

    # --- 2) семантика (дополнение) ---
    vec = search_code_vectors(query, limit=min(80, lim * 4), cfg=cfg)
    if vec.get("ok"):
        for m in vec.get("matches") or []:
            sp = (m.get("path") or "").replace("\\", "/")
            if not sp:
                continue
            meta = meta_from_path(
                sp,
                {
                    "object_name": m.get("object_name"),
                    "metadata_class": m.get("metadata_class"),
                    "location": m.get("location"),
                    "content_type": m.get("content_type"),
                    "xml_tag": m.get("xml_tag"),
                },
            )
            score = float(m.get("score") or 0) * 100
            sc = _rank_module(
                meta,
                search=q,
                kind=kind,
                form_name=form_name,
                object_name=object_name or "",
                want_form=want_form,
                base_score=score,
            )
            prev = by_path.get(sp)
            if prev is None or sc > prev["score"]:
                by_path[sp] = {
                    "path": sp,
                    "rel_path": sp,
                    "kind": meta["kind"],
                    "kind_ru": meta["kind"],
                    "object_name": meta["object_name"],
                    "form_name": meta["form_name"],
                    "full_name_1c": meta["full_name_1c"],
                    "location": meta["location"],
                    "metadata_class": meta["metadata_class"],
                    "module_role": meta["module_role"],
                    "score": sc,
                    "source": "qdrant",
                    "vector_score": m.get("score"),
                }

    if not by_path:
        return {
            "ok": False,
            "error": vec.get("error") if not vec.get("ok") else f"Ничего не найдено: {q}",
            "modules": [],
            "count": 0,
            "source": "qdrant",
            "hint": vec.get("hint")
            or "Проверьте индекс; search=фраза с именем объекта целиком",
        }

    items = sorted(by_path.values(), key=lambda x: (-x["score"], x["path"]))
    # filter kind form
    if want_form_module or (kind or "").lower() in ("form", "form_module"):
        only_forms = [x for x in items if x.get("kind") == "form_module"]
        if only_forms:
            items = only_forms

    # жёсткий фильтр по имени объекта (не отдавать чужие ФормаДокумента)
    obj_keys = [k.lower().replace("ё", "е") for k in name_keys]
    if obj_keys:
        filtered = []
        for it in items:
            blob = (
                f"{it.get('path','')} {it.get('object_name','')} "
                f"{it.get('full_name_1c','')} {it.get('location','')}"
            ).lower().replace("ё", "е")
            if any(k in blob for k in obj_keys):
                filtered.append(it)
        items = filtered  # пустой список = честный «не нашли», не чужие модули

    items = items[:lim]
    return {
        "ok": True,
        "source": "qdrant",
        "search": search,
        "count": len(items),
        "modules": items,
        "collection": st.get("collection"),
        "points": st.get("points"),
        "prefer": (
            "path из modules[].path → module_outline / read_code. "
            "Код только из Qdrant, не из код_конфигурации."
        ),
    }


def _rank_module(
    meta: dict[str, str],
    *,
    search: str,
    kind: str,
    form_name: str,
    object_name: str,
    want_form: bool,
    base_score: float,
) -> float:
    sc = float(base_score)
    blob = (
        f"{meta.get('source_path','')} {meta.get('object_name','')} "
        f"{meta.get('form_name','')} {meta.get('full_name_1c','')} "
        f"{meta.get('location','')}"
    ).lower().replace("ё", "е")
    s_low = (search or "").lower().replace("ё", "е")
    on = (object_name or "").lower().replace("ё", "е")
    fn = (form_name or "").lower().replace("ё", "е")
    mf = (meta.get("form_name") or "").lower().replace("ё", "е")
    mo = (meta.get("object_name") or "").lower().replace("ё", "е")

    if on and on in mo:
        sc += 40
    if on and on in blob:
        sc += 15
    # tokens from search
    for tok in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9_]{4,}", s_low):
        if tok in ("модуль", "форма", "формы", "документ", "документа"):
            continue
        if tok in blob:
            sc += 12
        if tok in mo:
            sc += 20
        if tok in mf:
            sc += 25

    if fn:
        if mf == fn or fn in mf or mf in fn:
            sc += 120
        elif meta.get("kind") == "form_module" and mf:
            sc -= 80

    if want_form:
        if meta.get("kind") == "form_module":
            sc += 35
        else:
            sc -= 50
        if not fn:
            if mf in ("формадокумента", "формаэлемента"):
                sc += 50
            elif mf == "формасписка":
                sc += 10
            elif mf == "формавыбора":
                sc -= 25

    if (kind or "").lower() in ("form", "form_module") and meta.get("kind") != "form_module":
        sc -= 30

    return sc


def search_code_qdrant_only(
    query: str,
    *,
    module_filter: str = "",
    max_matches: int = 12,
    match_mode: str = "",
    regex: bool = False,
    case_sensitive: bool = False,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = _cfg(cfg)
    st = code_collection_ready(cfg)
    if not st.get("ready"):
        return {
            "ok": False,
            "error": st.get("error") or "Qdrant не готов",
            "matches": [],
            "count": 0,
            "source": "qdrant",
            "hint": st.get("hint"),
        }
    vec = search_code_vectors(
        query,
        limit=max_matches,
        module_filter=module_filter or "",
        match_mode=match_mode or "",
        regex=regex,
        case_sensitive=case_sensitive,
        cfg=cfg,
    )
    if not vec.get("ok"):
        return {
            "ok": False,
            "source": "qdrant",
            "query": query,
            "matches": [],
            "count": 0,
            "error": vec.get("error"),
            "hint": vec.get("hint")
            or "python scripts/index_qdrant.py --recreate (Docker: mode=url)",
        }
    return {
        "ok": True,
        "source": "qdrant",
        "query": query,
        "module_filter": module_filter or "",
        "match_mode": vec.get("match_mode"),
        "matches": vec.get("matches") or [],
        "count": len(vec.get("matches") or []),
        "collection": vec.get("collection"),
        "lexical_count": vec.get("lexical_count"),
        "semantic_count": vec.get("semantic_count"),
        "prefer": vec.get("prefer"),
    }
