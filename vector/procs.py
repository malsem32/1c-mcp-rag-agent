# -*- coding: utf-8 -*-
"""Коллекция onec_procs: 1 точка = 1 процедура/функция (имя, сигнатура, комментарий, тело).

Строится из config_chunks.jsonl (чанки модуля склеиваются обратно и режутся по процедурам),
в Qdrant кладётся в ОТДЕЛЬНУЮ коллекцию — onec_code/onec_help не трогаются.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Iterator

from qdrant_client.http import models as qm

from ingest.chunking import _split_by_procedures
from vector.client import get_qdrant, reset_qdrant_cache
from vector.embed import Embedder
from vector.index import _check_vectors, _retry, _verify_written, count_zero_vectors, proc_words

_CONTEXT_END = "// --- КОНЕЦ КОНТЕКСТА ---"
_DECL = re.compile(
    r"^[ \t]*(Процедура|Функция|Procedure|Function)[ \t]+([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)[ \t]*\(([^)]*)\)?([^\n]*)",
    re.I | re.M,
)
_CALL = re.compile(r"(?<![\wА-Яа-яЁё.])(?:([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)\s*\.\s*)?([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]{2,})\s*\(")
_SKIP = {"если", "пока", "для", "новый", "возврат", "не", "и", "или", "строка", "число", "запрос", "цел", "формат"}
BODY_MAX = 6000  # полный текст в payload; больше — обрезаем (эмбеддинг всё равно видит начало)
VERSION = "procs-v1"


def collection_procs(cfg: dict[str, Any] | None = None) -> str:
    from vector.client import _qdrant_section

    return _qdrant_section(cfg).get("collection_procs") or "onec_procs"


def _stitch(parts: list[str]) -> str:
    """Склейка чанков одного модуля: у чанков, разрезанных по размеру, есть overlap — срезаем его."""
    out = ""
    for p in parts:
        if not out:
            out = p
            continue
        k = 0
        for n in range(min(len(out), len(p), 220), 20, -1):
            if out.endswith(p[:n]):
                k = n
                break
        out += ("" if k else "\n") + p[k:]
    return out


def _module_role(path: str) -> str:
    low = path.lower().replace("\\", "/")
    name = low.rsplit("/", 1)[-1]
    if "managermodule" in name:
        return "manager"
    if "objectmodule" in name:
        return "object"
    if "commandmodule" in name:
        return "command"
    if "/forms/" in low:
        return "form"
    return "module"


def _fix_order(parts: list[tuple[int, str]]) -> list[str]:
    return [b for _, b in sorted(parts, key=lambda x: x[0])]


def _iter_modules_sorted(chunks_jsonl: Path) -> Iterator[tuple[dict[str, Any], str]]:
    cur_path, cur_pl, parts = None, None, []
    with chunks_jsonl.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            pl = d.get("payload") or {}
            if pl.get("content_type") != "Модуль 1С" or not d.get("text"):
                continue
            sp = pl.get("source_path") or ""
            if sp != cur_path:
                if parts:
                    yield cur_pl, _stitch(_fix_order(parts))
                cur_path, cur_pl, parts = sp, pl, []
            parts.append((pl.get("part") or 0, d["text"].split(_CONTEXT_END, 1)[-1].strip("\n")))
    if parts:
        yield cur_pl, _stitch(_fix_order(parts))


def _leading_comment(block: str) -> str:
    """(описание из // над процедурой, без директив)."""
    doc: list[str] = []
    for ln in block.split("\n"):
        s = ln.strip()
        if s.startswith("//"):
            doc.append(s.lstrip("/").strip())
        elif s.startswith("&") or not s:
            continue
        else:
            break
    return " ".join(x for x in doc if x)[:600]


def iter_procs(chunks_jsonl: Path) -> Iterator[dict[str, Any]]:
    for pl0, code in _iter_modules_sorted(chunks_jsonl):
        path = str(pl0.get("source_path") or "")
        role = _module_role(path)
        form = None
        mm = re.search(r"/Forms/([^/]+)/", path.replace("\\", "/"))
        if mm:
            form = mm.group(1)
        offset = 0
        for block in _split_by_procedures(code):
            line_start = code.count("\n", 0, max(code.find(block), 0)) + 1
            m = _DECL.search(block)
            if not m:
                continue  # шапка модуля без процедур (переменные, #Область) — не процедура
            kind, name, params, tail = m.group(1), m.group(2), m.group(3), m.group(4)
            sig = f"{kind} {name}({params.strip()}){(' ' + tail.strip()) if tail.strip() else ''}".strip()[:300]
            export = bool(re.search(r"\bЭкспорт\b|\bExport\b", sig, re.I))
            directive = ""
            for ln in block[: m.start()].split("\n"):
                if ln.strip().startswith("&"):
                    directive = ln.strip()
            doc = _leading_comment(block[: m.start()])
            body = block[m.start():]
            callees: list[str] = []
            for pref, nm in _CALL.findall(body[len(m.group(0)):]):
                if nm.lower() in _SKIP:
                    continue
                callees.append(f"{pref}.{nm}" if pref else nm)
            callees = list(dict.fromkeys(callees))[:400]  # индекс «кто кого вызывает»: 40 теряли вызовы у крупных процедур
            queries = len(re.findall(r"\bВЫБРАТЬ\b|\bSELECT\b", body, re.I))
            ident = f"{path}::{name}::{line_start}"
            yield {
                "id": ident,
                "proc_name": name,
                "kind": "function" if kind.lower() in ("функция", "function") else "procedure",
                "signature": sig,
                "export": export,
                "directive": directive,
                "doc": doc,
                "text": body[:BODY_MAX],
                "truncated": len(body) > BODY_MAX,
                "lines": body.count("\n") + 1,
                "queries": queries,
                "callees": callees,
                "words": proc_words([name]),
                "source_path": path,
                "object_name": pl0.get("object_name"),
                "metadata_class": pl0.get("metadata_class"),
                "module_role": role,
                "form_name": form,
                "line_start": line_start,
            }


def embed_text(p: dict[str, Any]) -> str:
    head = f"{p['signature']}"
    ctx = f"объект={p.get('object_name') or ''}; {p.get('module_role') or ''}"
    parts = [head, ctx]
    if p.get("doc"):
        parts.append(p["doc"])
    parts.append(p["text"][len(p["signature"]):][:700])
    return "\n".join(parts)


def _pid(ident: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"1c-rag-proc:{ident}"))


def ensure_procs_collection(client: Any, name: str, dim: int, *, recreate: bool = False) -> None:
    names = {c.name for c in client.get_collections().collections}
    if name in names and recreate:
        client.delete_collection(name)
        names.discard(name)
    if name in names:
        return
    client.create_collection(collection_name=name, vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE))
    for field, schema in (
        ("proc_name", qm.PayloadSchemaType.KEYWORD),
        ("object_name", qm.PayloadSchemaType.KEYWORD),
        ("module_role", qm.PayloadSchemaType.KEYWORD),
        ("metadata_class", qm.PayloadSchemaType.KEYWORD),
        ("export", qm.PayloadSchemaType.BOOL),
        ("source_path", qm.PayloadSchemaType.TEXT),
        ("words", qm.PayloadSchemaType.TEXT),
        ("doc", qm.PayloadSchemaType.TEXT),
        ("text", qm.PayloadSchemaType.TEXT),
    ):
        try:
            client.create_payload_index(collection_name=name, field_name=field, field_schema=schema)
        except Exception:  # noqa: BLE001
            pass


def index_procs(cfg: dict[str, Any], chunks_jsonl: Path | str, *, recreate: bool = False, batch_size: int = 64,
                log: Any = None, limit: int = 0) -> dict[str, Any]:
    path = Path(chunks_jsonl)
    if not path.is_file():
        return {"ok": False, "error": f"Нет файла: {path}"}
    reset_qdrant_cache()
    client = get_qdrant(cfg)
    emb = Embedder(cfg)
    col = collection_procs(cfg)
    ensure_procs_collection(client, col, emb.vector_size, recreate=recreate)

    batch: list[dict[str, Any]] = []
    total = upserted = skipped = 0

    def flush() -> None:
        nonlocal batch, upserted, skipped
        if not batch:
            return
        ids = [_pid(p["id"]) for p in batch]
        have: dict[str, Any] = {}
        if not recreate:
            try:
                got = _retry(lambda: client.retrieve(collection_name=col, ids=ids, with_payload=["ver", "text_len"], with_vectors=False), log=log)
                have = {str(r.id): r.payload or {} for r in got}
            except Exception:  # noqa: BLE001
                have = {}
        todo = [p for p, i in zip(batch, ids) if have.get(i, {}).get("ver") != VERSION + str(len(p["text"]))]
        skipped += len(batch) - len(todo)
        if todo:
            vecs = emb.embed_batch([emb.prepare_passage(embed_text(p), max_chars=1100) for p in todo])
            _check_vectors(vecs)
            pts = [
                qm.PointStruct(id=_pid(p["id"]), vector=v, payload={**p, "ver": VERSION + str(len(p["text"]))})
                for p, v in zip(todo, vecs)
            ]
            _retry(lambda: client.upsert(collection_name=col, points=pts), log=log)
            _verify_written(client, col, pts, log=log)
            upserted += len(pts)
        batch = []

    for p in iter_procs(path):
        total += 1
        batch.append(p)
        if len(batch) >= batch_size:
            flush()
            if log and total % (batch_size * 20) < batch_size:
                log(f"  … процедур {total}, записано {upserted}, без изменений {skipped}")
        if limit and total >= limit:
            break
    flush()
    zero, n = count_zero_vectors(client, col)
    return {"ok": zero == 0, "collection": col, "procs_read": total, "upserted": upserted, "skipped": skipped,
            "points": n, "zero_vectors": zero}


def search_procs(query: str, *, limit: int = 10, cfg: dict[str, Any] | None = None, export_only: bool = False,
                 object_name: str = "") -> list[dict[str, Any]]:
    """Смысловой поиск по процедурам + лексика по имени/описанию (RRF)."""
    from config import load_config

    cfg = cfg or load_config()
    client = get_qdrant(cfg)
    col = collection_procs(cfg)
    must = []
    if export_only:
        must.append(qm.FieldCondition(key="export", match=qm.MatchValue(value=True)))
    if object_name:
        must.append(qm.FieldCondition(key="object_name", match=qm.MatchValue(value=object_name)))
    flt = qm.Filter(must=must) if must else None
    vec = Embedder(cfg).embed_query(query)
    sem = client.query_points(collection_name=col, query=vec, limit=limit * 3, query_filter=flt, with_payload=True).points
    words = [w for w in re.findall(r"[А-Яа-яЁёA-Za-z0-9]{4,}", query.lower())][:6]
    lex: list[Any] = []
    for w in words:
        for field in ("words", "doc"):
            try:
                pts, _ = client.scroll(
                    collection_name=col,
                    scroll_filter=qm.Filter(must=must + [qm.FieldCondition(key=field, match=qm.MatchText(text=w[:6]))]),
                    limit=limit * 2, with_payload=True, with_vectors=False,
                )
                lex.extend(pts)
            except Exception:  # noqa: BLE001
                pass
    score: dict[str, float] = {}
    byid: dict[str, Any] = {}
    for rank, p in enumerate(sem):
        score[str(p.id)] = score.get(str(p.id), 0) + 1 / (60 + rank)
        byid[str(p.id)] = p
    seen_lex: dict[str, int] = {}
    for p in lex:
        seen_lex[str(p.id)] = seen_lex.get(str(p.id), 0) + 1
        byid.setdefault(str(p.id), p)
    for rank, (pid, _) in enumerate(sorted(seen_lex.items(), key=lambda x: -x[1])):
        score[pid] = score.get(pid, 0) + 1 / (60 + rank)
    out = []
    for pid, s in sorted(score.items(), key=lambda x: -x[1])[:limit]:
        pl = dict(byid[pid].payload or {})
        pl.pop("text", None)
        pl["score"] = round(s, 4)
        out.append(pl)
    return out
