# -*- coding: utf-8 -*-
"""Коллекция onec_files: вся выгрузка конфигурации в Qdrant, 1 точка = 1 файл (zlib+base64, без векторов).

Индексация: index_files(cfg, code_dir) — неизменённые файлы (sha1) пропускаются, удалённые из выгрузки вычищаются.
Runtime: ensure_mirror() разворачивает коллекцию в локальное зеркало knowledge/qdrant_mirror/<коллекция>
(докачивает только изменённое по манифесту). Зеркало — производное от Qdrant, его можно удалить в любой момент;
через него tools форм/метаданных/ролей читают Form.xml, Form.bin, <Объект>.xml, Template.xml как из выгрузки.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
import uuid
import zlib
from pathlib import Path
from typing import Any

from qdrant_client.http import models as qm

from config import ROOT
from vector.client import _qdrant_section, get_qdrant

VERSION = "files-v1"
_MANIFEST_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "onec_files/__manifest__"))
# копия конфигурации поставщика (.cf) — бинарник, ни один tool его не читает
_SKIP_DIRS = {"ParentConfigurations"}
_BATCH_BYTES = 12 * 1024 * 1024  # запрос к Qdrant по умолчанию ≤ 32 МБ
_RECHECK_SEC = 60


def collection_files(cfg: dict[str, Any] | None = None) -> str:
    return _qdrant_section(cfg).get("collection_files") or "onec_files"


def _pid(rel: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "onec_files/" + rel))


def _iter_dump(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            p = Path(dirpath) / fn
            yield p, p.relative_to(root).as_posix()


def index_files(cfg: dict[str, Any], code_dir: str | Path, *, recreate: bool = False, log: Any = None) -> dict[str, Any]:
    from vector.index import _retry

    root = Path(code_dir)
    if not root.is_dir():
        return {"ok": False, "error": f"Нет выгрузки: {root}"}
    client = get_qdrant(cfg)
    col = collection_files(cfg)
    if recreate and client.collection_exists(col):
        client.delete_collection(col)
    if not client.collection_exists(col):
        client.create_collection(col, vectors_config={})

    have: dict[str, str] = {}  # id -> sha
    if not recreate:
        off = None
        while True:
            pts, off = client.scroll(col, limit=2000, offset=off, with_payload=["sha"], with_vectors=False)
            have.update({str(p.id): (p.payload or {}).get("sha", "") for p in pts})
            if off is None:
                break

    seen: set[str] = set()
    batch: list[qm.PointStruct] = []
    size = files = upserted = 0
    raw_total = packed_total = 0

    def flush() -> None:
        nonlocal batch, size, upserted
        if batch:
            pts = batch
            _retry(lambda: client.upsert(collection_name=col, points=pts), log=log)
            upserted += len(pts)
        batch, size = [], 0

    for p, rel in _iter_dump(root):
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        files += 1
        pid, sha = _pid(rel), hashlib.sha1(raw).hexdigest()
        seen.add(pid)
        raw_total += len(raw)
        if have.get(pid) == sha:
            continue
        data = base64.b64encode(zlib.compress(raw, 6)).decode("ascii")
        packed_total += len(data)
        if batch and size + len(data) > _BATCH_BYTES:
            flush()
        batch.append(qm.PointStruct(id=pid, vector={}, payload={"path": rel, "sha": sha, "size": len(raw), "data": data}))
        size += len(data)
        if log and files % 2000 == 0:
            log(f"  … файлов {files}, записано {upserted + len(batch)}")
    flush()

    stale = [i for i in have if i not in seen and i != _MANIFEST_ID]
    for i in range(0, len(stale), 1000):
        client.delete(col, points_selector=qm.PointIdsList(points=stale[i:i + 1000]))
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    client.upsert(col, points=[qm.PointStruct(id=_MANIFEST_ID, vector={}, payload={"manifest": True, "ver": VERSION, "stamp": stamp, "files": files})])
    return {"ok": True, "collection": col, "files": files, "upserted": upserted, "deleted": len(stale),
            "raw_mb": round(raw_total / 2**20, 1), "packed_mb_written": round(packed_total / 2**20, 1), "stamp": stamp}


# --- runtime: локальное зеркало ---

_lock = threading.Lock()
_state: dict[str, Any] = {"root": None, "checked": 0.0}


def mirror_root(cfg: dict[str, Any] | None = None) -> Path:
    return ROOT / "knowledge" / "qdrant_mirror" / collection_files(cfg)


def _sync(client: Any, col: str, root: Path, stamp: str, log: Any = None) -> None:
    idx_path = root / ".mirror.json"
    try:
        local = json.loads(idx_path.read_text(encoding="utf-8")).get("files") or {}
    except (OSError, ValueError):
        local = {}
    remote: dict[str, tuple[str, str]] = {}  # path -> (id, sha)
    off = None
    while True:
        pts, off = client.scroll(col, limit=2000, offset=off, with_payload=["path", "sha"], with_vectors=False)
        for p in pts:
            pl = p.payload or {}
            if pl.get("path"):
                remote[pl["path"]] = (str(p.id), pl.get("sha", ""))
        if off is None:
            break
    todo = [i for path, (i, sha) in remote.items() if local.get(path) != sha or not (root / path).is_file()]
    for k in range(0, len(todo), 32):
        for p in client.retrieve(col, ids=todo[k:k + 32], with_payload=["path", "data"], with_vectors=False):
            pl = p.payload or {}
            dst = root / pl["path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(zlib.decompress(base64.b64decode(pl["data"])))
        if log and k % 3200 == 0:
            log(f"  … зеркало: {k}/{len(todo)}")
    for path in set(local) - set(remote):
        try:
            (root / path).unlink()
        except OSError:
            pass
    root.mkdir(parents=True, exist_ok=True)
    idx_path.write_text(json.dumps({"stamp": stamp, "files": {k: v[1] for k, v in remote.items()}}, ensure_ascii=False), encoding="utf-8")


def ensure_mirror(cfg: dict[str, Any] | None = None, *, log: Any = None) -> Path | None:
    """Корень зеркала выгрузки из Qdrant; None — коллекции файлов нет (не проиндексирована)."""
    now = time.time()
    if _state["root"] is not None and now - _state["checked"] < _RECHECK_SEC:
        return _state["root"]
    with _lock:
        if _state["root"] is not None and time.time() - _state["checked"] < _RECHECK_SEC:
            return _state["root"]
        try:
            client = get_qdrant(cfg)
            col = collection_files(cfg)
            got = client.retrieve(col, ids=[_MANIFEST_ID], with_payload=True, with_vectors=False)
        except Exception:  # noqa: BLE001 — нет коллекции / Qdrant недоступен
            return _state["root"]
        if not got:
            return None
        stamp = (got[0].payload or {}).get("stamp", "")
        root = mirror_root(cfg)
        try:
            cur = json.loads((root / ".mirror.json").read_text(encoding="utf-8")).get("stamp")
        except (OSError, ValueError):
            cur = None
        if cur != stamp:
            _sync(client, col, root, stamp, log=log)
        _state.update(root=root, checked=time.time())
        return root
