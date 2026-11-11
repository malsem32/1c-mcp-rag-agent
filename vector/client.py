# -*- coding: utf-8 -*-
"""Клиент Qdrant: local path (без Docker) или remote URL."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from config import ROOT, load_config


def _qdrant_section(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    c = cfg if cfg is not None else load_config()
    return dict(c.get("qdrant") or {})


@lru_cache(maxsize=1)
def get_qdrant_cached() -> Any:
    return _make_client(load_config())


# клиенты по явному cfg: ключ — секция qdrant (local-режим не терпит 2 клиента на одну папку)
_BY_SECTION: dict[str, Any] = {}


def get_qdrant(cfg: dict[str, Any] | None = None) -> Any:
    if cfg is None:
        return get_qdrant_cached()
    key = json.dumps(_qdrant_section(cfg), sort_keys=True, default=str)
    if key not in _BY_SECTION:
        _BY_SECTION[key] = _make_client(cfg)
    return _BY_SECTION[key]


def reset_qdrant_cache() -> None:
    get_qdrant_cached.cache_clear()
    _BY_SECTION.clear()


def _bypass_proxy_for_local(url: str) -> None:
    """Системный HTTP_PROXY без NO_PROXY уводит запросы к локальному Qdrant в прокси → 502."""
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if host not in ("localhost", "127.0.0.1", "::1", "host.docker.internal"):
        return
    for var in ("NO_PROXY", "no_proxy"):
        cur = [h.strip() for h in (os.environ.get(var) or "").split(",") if h.strip()]
        if host not in cur:
            os.environ[var] = ",".join(cur + [host])


def _make_client(cfg: dict[str, Any]) -> Any:
    from qdrant_client import QdrantClient

    q = _qdrant_section(cfg)
    mode = (q.get("mode") or "local").lower()
    # server 1.13 vs client 1.18 → warning; check_compatibility=False тише
    kw_compat: dict[str, Any] = {}
    try:
        # qdrant-client >=1.12
        import inspect

        if "check_compatibility" in inspect.signature(QdrantClient.__init__).parameters:
            kw_compat["check_compatibility"] = False
    except Exception:
        pass

    if mode in ("url", "remote", "http", "server"):
        url = (q.get("url") or "http://localhost:6333").rstrip("/")
        api_key = q.get("api_key") or None
        timeout = float(q.get("timeout") or 60)
        _bypass_proxy_for_local(url)
        return QdrantClient(
            url=url, api_key=api_key, timeout=timeout, **kw_compat
        )

    # local persistent storage (Windows-friendly, без Docker)
    path = q.get("path") or "knowledge/qdrant_data"
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    p.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(p), **kw_compat)


def collection_name(kind: str = "code", cfg: dict[str, Any] | None = None) -> str:
    q = _qdrant_section(cfg)
    if kind == "help":
        return q.get("collection_help") or "onec_help"
    return q.get("collection_code") or "onec_code"


def qdrant_enabled(cfg: dict[str, Any] | None = None) -> bool:
    q = _qdrant_section(cfg)
    return bool(q.get("enabled", True))


def qdrant_status(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg if cfg is not None else load_config()
    q = _qdrant_section(cfg)
    if not qdrant_enabled(cfg):
        return {"ok": False, "enabled": False, "hint": "qdrant.enabled=false в config.yaml"}
    try:
        client = get_qdrant(cfg)
        cols = []
        try:
            existing = {c.name for c in client.get_collections().collections}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "enabled": True, "error": str(exc)}

        for kind, key in (("code", "collection_code"), ("help", "collection_help"), ("files", "collection_files")):
            name = q.get(key) or f"onec_{kind}"
            info: dict[str, Any] = {"name": name, "exists": name in existing}
            if name in existing:
                try:
                    cinfo = client.get_collection(name)
                    info["points"] = getattr(cinfo, "points_count", None) or 0
                    info["status"] = str(getattr(cinfo, "status", ""))
                except Exception as exc:  # noqa: BLE001
                    info["error"] = str(exc)
            cols.append(info)
        return {
            "ok": True,
            "enabled": True,
            "mode": q.get("mode") or "local",
            "path": q.get("path") or "",
            "url": q.get("url") or "",
            "collections": cols,
            "prefer": "search_code / knowledge_search ходят в Qdrant, если points > 0",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "enabled": True,
            "error": str(exc),
            "hint": "pip install qdrant-client fastembed; python scripts/index_qdrant.py",
        }
