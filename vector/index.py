# -*- coding: utf-8 -*-
"""Загрузка JSONL-чанков в Qdrant."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Iterator

from qdrant_client.http import models as qm

import re

from vector.client import collection_name, get_qdrant, reset_qdrant_cache
from vector.embed import EMBED_TEXT_VERSION, Embedder

_PROC_RE = re.compile(
    r"^\s*(?:Процедура|Функция|Procedure|Function)\s+([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)",
    re.M | re.I,
)
_CALL_RE = re.compile(
    r"(?<![\wА-Яа-яЁё.])(?:([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)\s*\.\s*)?"
    r"([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]{2,})\s*\(",
    re.M,
)
_DVIZH_RE = re.compile(
    r"Движения\s*\.\s*([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)\s*\.\s*"
    r"(Добавить|Записать|Очистить)",
    re.I,
)
_CONTEXT_END = "// --- КОНЕЦ КОНТЕКСТА ---"


_CAMEL_WORD = re.compile(r"[А-ЯЁA-Z]?[а-яёa-z0-9]+|[А-ЯЁA-Z]+(?![а-яё])")


def proc_words(names: list[str], limit: int = 700) -> str:
    """Имена процедур словами: ПолучитьКурсВалюты -> «получить курс валюты» (лексический мост речь ↔ идентификатор)."""
    words: list[str] = []
    for n in names:
        words.extend(w.lower() for w in _CAMEL_WORD.findall(n))
    return " ".join(dict.fromkeys(words))[:limit]


_IDENT = re.compile(r"[А-ЯЁA-Z][а-яёa-z0-9]*[а-яёa-z0-9][А-ЯЁA-Z][А-ЯЁа-яёA-Za-z0-9]*")


def card_words(text: str, limit: int = 1500) -> str:
    """Слова из CamelCase-идентификаторов карточки (параметры макета, элементы/команды форм, реквизиты)."""
    body = text.split(_CONTEXT_END, 1)[-1]
    return proc_words(list(dict.fromkeys(_IDENT.findall(body))), limit=limit)


_DEF_HEAD_RE = re.compile(r"^\s*(?:Процедура|Функция|Procedure|Function)\s+[\wА-Яа-яЁё]+", re.M | re.I)
_NEW_RE = re.compile(r"\b(?:Новый|New)\s+[\wА-Яа-яЁё.]+", re.I)
_CALL_SKIP = {
    "если", "иначеесли", "пока", "для", "новый", "возврат", "не", "и", "или", "строка", "число", "запрос",
    "if", "elseif", "while", "new", "return",
}


def extract_callees(body: str, limit: int = 60) -> list[str]:
    """Вызовы процедур/функций в теле: без собственных объявлений и без «Новый Тип(»."""
    clean = _NEW_RE.sub(" ", _DEF_HEAD_RE.sub(" ", body or ""))
    out: list[str] = []
    for pref, name in _CALL_RE.findall(clean):
        n = name.strip()
        if n.lower() in _CALL_SKIP or len(n) < 3:
            continue
        out.append(f"{pref}.{n}" if pref else n)
    return list(dict.fromkeys(out))[:limit]


def _enrich_code_payload(payload: dict[str, Any], text: str) -> dict[str, Any]:
    """proc_names / callees / registers — для hop-search без FS."""
    body = text or ""
    if _CONTEXT_END in body:
        body = body.split(_CONTEXT_END, 1)[-1]
    procs = list(dict.fromkeys(_PROC_RE.findall(body)))[:80]
    callees = extract_callees(body)
    regs = list(dict.fromkeys(m.group(1) for m in _DVIZH_RE.finditer(body)))[:40]
    payload["proc_names"] = procs
    payload["proc_names_str"] = " ".join(procs)
    payload["proc_words"] = (card_words(text) if str(payload.get("content_type") or "").startswith("Структура")
                             else proc_words(procs))
    payload["callees"] = callees
    payload["registers_written"] = regs
    return payload


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            oid = str(obj.get("id") or "")
            if oid.startswith("__meta"):
                continue
            if not obj.get("text"):
                continue
            obj["id"] = oid or _content_hash(obj["text"])
            yield obj


def _retry(fn: Any, *, tries: int = 8, base: float = 2.0, log: Any = None) -> Any:
    """Вызов Qdrant с повторами: обрыв соединения/таймаут не должны убивать многочасовую индексацию."""
    import time

    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if attempt == tries:
                raise
            wait = min(60.0, base * attempt)
            if log:
                log(f"  Qdrant: {type(exc).__name__}, повтор {attempt}/{tries - 1} через {wait:.0f} с")
            time.sleep(wait)


def _norm(vec: Any) -> float:
    return float(sum(float(x) * float(x) for x in vec) ** 0.5)


def _check_vectors(vectors: Any) -> None:
    """Эмбеддер не должен отдавать пустые/NaN-векторы — иначе поиск по смыслу молча не работает."""
    for i, v in enumerate(vectors):
        n = _norm(v)
        if not (n > 1e-6) or n != n:
            raise RuntimeError(f"Эмбеддер вернул пустой/некорректный вектор (позиция {i} в батче, норма={n})")


def _verify_written(client: Any, col: str, points: list[Any], *, log: Any = None, sample: int = 3) -> None:
    """Контрольное чтение: записанные векторы должны быть ненулевыми; иначе повторная запись, затем ошибка."""
    probe = points[:: max(1, len(points) // sample)][:sample]
    for attempt in range(3):
        got = _retry(lambda: client.retrieve(collection_name=col, ids=[p.id for p in probe],
                                             with_vectors=True, with_payload=False), log=log)
        if all(_norm(g.vector) > 1e-6 for g in got) and len(got) == len(probe):
            return
        if log:
            log(f"  ВНИМАНИЕ: Qdrant вернул нулевой вектор после записи (попытка {attempt + 1}/3), переписываю")
        _retry(lambda: client.upsert(collection_name=col, points=probe, wait=True), log=log)
    raise RuntimeError(f"Qdrant хранит нулевые векторы в {col} даже после перезаписи — индексация остановлена")


def count_zero_vectors(client: Any, col: str, page: int = 2000) -> tuple[int, int]:
    """(нулевых, всего) по всей коллекции — читает векторы страницами."""
    zero = total = 0
    off = None
    while True:
        pts, off = _retry(lambda: client.scroll(collection_name=col, limit=page, offset=off,
                                                with_vectors=True, with_payload=False))
        for p in pts:
            total += 1
            zero += _norm(p.vector) <= 1e-6
        if off is None:
            return zero, total


def backfill_payload(cfg: dict[str, Any], *, kind: str = "code", log: Any = None) -> dict[str, Any]:
    """Дописать proc_words в payload существующих точек (из proc_names) и создать индекс; векторы не трогаются."""
    reset_qdrant_cache()
    client = get_qdrant(cfg)
    col = collection_name(kind, cfg)
    try:
        client.create_payload_index(collection_name=col, field_name="proc_words", field_schema=qm.PayloadSchemaType.TEXT)
    except Exception:  # noqa: BLE001
        pass
    done = off_n = 0
    off = None
    while True:
        pts, off = _retry(lambda: client.scroll(collection_name=col, limit=500, offset=off,
                                                with_payload=["proc_names", "content_type", "text"], with_vectors=False), log=log)
        ops = []
        for p in pts:
            pl = p.payload
            if str(pl.get("content_type") or "").startswith("Структура"):
                words = card_words(pl.get("text") or "")
            elif pl.get("proc_names"):
                words = proc_words(list(pl["proc_names"]))
            else:
                continue
            upd: dict[str, Any] = {"proc_words": words}
            if pl.get("proc_names") and pl.get("content_type") != "Справка платформы":
                upd["callees"] = extract_callees(str(pl.get("text") or "").split(_CONTEXT_END, 1)[-1])
            ops.append(qm.SetPayloadOperation(set_payload=qm.SetPayload(payload=upd, points=[p.id])))
        if ops:
            _retry(lambda: client.batch_update_points(collection_name=col, update_operations=ops, wait=True), log=log)
        done += len(ops)
        off_n += len(pts)
        if log and off_n % 10000 < 500:
            log(f"  … обработано {off_n}, обновлено {done}")
        if off is None:
            return {"ok": True, "collection": col, "scanned": off_n, "updated": done}


def repair_vectors(cfg: dict[str, Any], *, kind: str = "code", batch_size: int = 64, log: Any = None) -> dict[str, Any]:
    """Найти точки с нулевым вектором и пересчитать только их (payload не трогается)."""
    reset_qdrant_cache()
    client = get_qdrant(cfg)
    emb = Embedder(cfg)
    col = collection_name(kind, cfg)
    scanned = zero = fixed = 0
    off = None
    batch: list[Any] = []

    def flush_fix() -> None:
        nonlocal fixed, batch
        if not batch:
            return
        texts = [emb.prepare_passage(p.payload.get("text") or "", procs=p.payload.get("proc_names") or []) for p in batch]
        vecs = emb.embed_batch(texts)
        _check_vectors(vecs)
        pv = [qm.PointVectors(id=p.id, vector=[float(x) for x in v]) for p, v in zip(batch, vecs)]
        _retry(lambda: client.update_vectors(collection_name=col, points=pv, wait=True), log=log)
        _verify_written(client, col, [qm.PointStruct(id=p.id, vector=v, payload={}) for p, v in zip(batch, vecs)], log=log)
        fixed += len(batch)
        batch = []
        if log and fixed % (batch_size * 20) == 0:
            log(f"  … исправлено {fixed}")

    while True:
        pts, off = _retry(lambda: client.scroll(collection_name=col, limit=500, offset=off,
                                                with_vectors=True, with_payload=True), log=log)
        for p in pts:
            scanned += 1
            if _norm(p.vector) <= 1e-6:
                zero += 1
                batch.append(p)
                if len(batch) >= batch_size:
                    flush_fix()
        if off is None:
            break
    flush_fix()
    return {"ok": True, "collection": col, "scanned": scanned, "zero_found": zero, "fixed": fixed}


def _content_hash(text: str) -> str:
    """Хэш текста + версия формата эмбеддинг-текста (смена формата → вектора пересчитаются)."""
    return hashlib.sha1(f"{EMBED_TEXT_VERSION}|{text}".encode("utf-8", errors="replace")).hexdigest()[:16]


def _procs_of(doc: dict[str, Any]) -> list[str]:
    if (doc.get("payload") or {}).get("content_type") == "Справка платформы":
        return []
    body = doc.get("text") or ""
    if _CONTEXT_END in body:
        body = body.split(_CONTEXT_END, 1)[-1]
    return list(dict.fromkeys(_PROC_RE.findall(body)))[:80]


def _point_id(doc_id: str) -> str:
    """Qdrant принимает UUID или int — детерминированный UUID из id строки."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"1c-rag:{doc_id}"))


def ensure_collection(
    client: Any,
    name: str,
    vector_size: int,
    *,
    recreate: bool = False,
) -> None:
    exists = False
    try:
        names = {c.name for c in client.get_collections().collections}
        exists = name in names
    except Exception:
        exists = False

    if exists and recreate:
        client.delete_collection(name)
        exists = False

    if not exists:
        client.create_collection(
            collection_name=name,
            vectors_config=qm.VectorParams(
                size=vector_size,
                distance=qm.Distance.COSINE,
            ),
        )
        # индексы payload для фильтров + lexical
        for field, schema in (
            ("metadata_class", qm.PayloadSchemaType.KEYWORD),
            ("object_name", qm.PayloadSchemaType.KEYWORD),
            ("content_type", qm.PayloadSchemaType.KEYWORD),
            ("form_name", qm.PayloadSchemaType.KEYWORD),
            ("module_role", qm.PayloadSchemaType.KEYWORD),
            ("source_path", qm.PayloadSchemaType.TEXT),
            ("location", qm.PayloadSchemaType.TEXT),
            ("text", qm.PayloadSchemaType.TEXT),
            ("proc_names_str", qm.PayloadSchemaType.TEXT),
            ("proc_words", qm.PayloadSchemaType.TEXT),
        ):
            try:
                client.create_payload_index(
                    collection_name=name,
                    field_name=field,
                    field_schema=schema,
                )
            except Exception:
                pass


def index_jsonl(
    jsonl_path: Path | str,
    *,
    cfg: dict[str, Any],
    kind: str = "code",
    recreate: bool = False,
    batch_size: int = 32,
    progress_every: int = 200,
    log: Any = None,
    incremental: bool = True,
    verify: bool = True,
    write_catalog: bool = True,
) -> dict[str, Any]:
    """
    Прочитать config_chunks.jsonl → embed → upsert.
    kind: code | help
    incremental: пропускать чанки, чей content_hash уже в коллекции (эмбеддинг — самое дорогое).
    """
    path = Path(jsonl_path)
    if not path.is_file():
        return {"ok": False, "error": f"Нет файла: {path}"}

    reset_qdrant_cache()
    client = get_qdrant(cfg)
    emb = Embedder(cfg)
    dim = emb.vector_size
    col = collection_name(kind, cfg)
    ensure_collection(client, col, dim, recreate=recreate)

    bs = max(1, int(batch_size or emb.batch_size or 32))
    total = 0
    upserted = 0
    skipped = 0
    batch_docs: list[dict[str, Any]] = []
    from ingest.progress import report as _progress

    with open(path, "rb") as _fh:  # строк в файле ≈ документов (первая — __meta__)
        n_docs = max(1, sum(1 for _ in _fh) - 1)

    names_rows: list[tuple[str, str]] = []
    seen_names: set[str] = set()

    def _unchanged(docs: list[dict[str, Any]]) -> set[str]:
        """id точек, у которых в Qdrant уже лежит тот же content_hash."""
        if not incremental or recreate:
            return set()
        try:
            got = _retry(lambda: client.retrieve(
                collection_name=col,
                ids=[_point_id(str(d["id"])) for d in docs],
                with_payload=["content_hash"],
                with_vectors=False,
            ), log=log)
        except Exception:
            return set()
        have = {str(r.id): (r.payload or {}).get("content_hash") for r in got}
        return {
            str(d["id"])
            for d in docs
            if have.get(_point_id(str(d["id"]))) == _content_hash(d.get("text") or "")
        }

    def flush() -> None:
        nonlocal upserted, skipped, batch_docs
        if not batch_docs:
            return
        same = _unchanged(batch_docs)
        if same:
            skipped += len(same)
            batch_docs = [d for d in batch_docs if str(d["id"]) not in same]
            if not batch_docs:
                return
        # полный text в payload; в модель — body без длинного header
        texts = [emb.prepare_passage(d.get("text") or "", procs=_procs_of(d)) for d in batch_docs]
        vectors = emb.embed_batch(texts)
        _check_vectors(vectors)
        points = []
        for d, vec in zip(batch_docs, vectors):
            doc_id = str(d["id"])
            payload = dict(d.get("payload") or {})
            full_text = d.get("text") or ""
            payload["text"] = full_text
            payload["doc_id"] = doc_id
            payload["content_hash"] = _content_hash(full_text)
            # доп. поля для runtime-каталога (find_modules / read без FS)
            sp = (payload.get("source_path") or "").replace("\\", "/")
            parts = [p for p in sp.split("/") if p]
            if "Forms" in parts:
                i = parts.index("Forms")
                if i + 1 < len(parts) and not payload.get("form_name"):
                    payload["form_name"] = parts[i + 1]
            if parts:
                low = parts[-1].lower()
                if "managermodule" in low:
                    payload["module_role"] = "manager"
                elif "objectmodule" in low:
                    payload["module_role"] = "object"
                elif "commandmodule" in low:
                    payload["module_role"] = "command"
                elif "forms" in [p.lower() for p in parts]:
                    payload["module_role"] = "form"
                else:
                    payload.setdefault("module_role", "module")
            if str(payload.get("content_type") or "").startswith("Структура"):
                payload["module_role"] = "structure"
            if (payload.get("content_type") or "") != "Справка платформы":
                _enrich_code_payload(payload, full_text)
            points.append(
                qm.PointStruct(
                    id=_point_id(doc_id),
                    vector=vec,
                    payload=payload,
                )
            )
        _retry(lambda: client.upsert(collection_name=col, points=points), log=log)
        _verify_written(client, col, points, log=log)
        upserted += len(points)
        batch_docs = []

    for doc in _iter_jsonl(path):
        total += 1
        batch_docs.append(doc)
        if kind == "code":
            try:
                from tools.name_match import _name_from_payload

                on, folder = _name_from_payload(doc.get("payload") or {})
                if on and on not in seen_names:
                    seen_names.add(on)
                    names_rows.append((on, folder))
            except Exception:
                pass
        if len(batch_docs) >= bs:
            flush()
            _progress(total, n_docs, "фрагментов")
            if log and total % progress_every < bs:
                log(f"  … read {total}, upserted {upserted}, unchanged {skipped}")

    flush()
    catalog_file = ""
    if kind == "code" and names_rows and write_catalog:
        try:
            from tools.name_match import write_object_name_catalog

            catalog_file = str(write_object_name_catalog(names_rows))
            if log:
                log(f"  object_names: {len(names_rows)} → {catalog_file}")
        except Exception as exc:
            if log:
                log(f"  object_names skip: {exc}")
    vec_zero, vec_total, repair_passes = 0, 0, 0
    if verify:
        # итоговая проверка: нулевые векторы = поиск по смыслу молча не работает; чиним сами, но не скрываем
        vec_zero, vec_total = count_zero_vectors(client, col)
        while vec_zero and repair_passes < 3:
            repair_passes += 1
            if log:
                log(f"  Проверка: нулевых векторов {vec_zero} из {vec_total} — ремонт, проход {repair_passes}/3")
            repair_vectors(cfg, kind=kind, batch_size=bs, log=log)
            vec_zero, vec_total = count_zero_vectors(client, col)
        if log:
            log(f"  Проверка векторов: нулевых {vec_zero} из {vec_total}")
    info = client.get_collection(col)
    points = int(getattr(info, "points_count", 0) or 0)
    return {
        "ok": vec_zero == 0,
        "vectors_zero": vec_zero,
        "vectors_total": vec_total,
        "repair_passes": repair_passes,
        "collection": col,
        "jsonl": str(path),
        "read": total,
        "upserted": upserted,
        "skipped_unchanged": skipped,
        "object_names": len(names_rows) if kind == "code" else 0,
        "object_names_jsonl": catalog_file,
        "points_in_collection": points,
        "vector_size": dim,
        "embedding": {
            "provider": emb.provider,
            "model": emb.model,
        },
    }
