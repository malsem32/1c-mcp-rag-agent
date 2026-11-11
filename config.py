# -*- coding: utf-8 -*-
"""Загрузка config.yaml + секреты из .env."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = ROOT / "config.yaml"


def _deep_get(data: dict, *keys: str, default: Any = None) -> Any:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def _as_path(value: str | Path | None, *, default_rel: str = "") -> str:
    raw = (str(value).strip() if value else "") or default_rel
    if not raw:
        return ""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = ROOT / p
    return str(p)


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """YAML — источник правды; .env — только секреты и редкие overrides."""
    load_dotenv()
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw: dict[str, Any] = {}
    if cfg_path.is_file():
        with cfg_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    # --- backend / http / com ---
    backend = (_env("RAG_BACKEND") or raw.get("backend") or "http").lower()

    http = {
        "base_url": _env("RAG_HTTP_BASE_URL")
        or _deep_get(raw, "http", "base_url", default="http://localhost/hs/ai"),
        "timeout": float(
            _env("RAG_HTTP_TIMEOUT") or _deep_get(raw, "http", "timeout", default=60.0)
        ),
        "auth": (
            _env("RAG_HTTP_AUTH") or _deep_get(raw, "http", "auth", default="none")
        ).lower(),
        "username": _env("RAG_HTTP_USERNAME")
        or _deep_get(raw, "http", "username", default="")
        or "",
        "password": _env("RAG_HTTP_PASSWORD")
        or _deep_get(raw, "http", "password", default="")
        or "",
    }

    com = {
        "connection_string": _env("RAG_COM_CONNECTION_STRING")
        or _deep_get(raw, "com", "connection_string", default="")
        or "",
        "prog_id": _env("RAG_COM_PROG_ID")
        or _deep_get(raw, "com", "prog_id", default="V83.COMConnector"),
    }

    # --- paths ---
    paths_raw = dict(raw.get("paths") or {})
    knowledge_dir = _as_path(
        _env("RAG_KNOWLEDGE_DIR") or paths_raw.get("knowledge_dir"),
        default_rel="knowledge",
    )
    code_dir = _as_path(
        _env("RAG_CODE_DIR")
        or paths_raw.get("code_dir")
        or raw.get("code_dir")
        or "",
        default_rel="knowledge/код_конфигурации",
    )
    chunks_jsonl = _as_path(
        paths_raw.get("chunks_jsonl") or "knowledge/qdrant_ready/config_chunks.jsonl",
        default_rel="knowledge/qdrant_ready/config_chunks.jsonl",
    )
    help_jsonl = _as_path(
        paths_raw.get("help_jsonl") or "knowledge/qdrant_ready/help_chunks.jsonl",
        default_rel="knowledge/qdrant_ready/help_chunks.jsonl",
    )
    object_names_jsonl = _as_path(
        paths_raw.get("object_names_jsonl")
        or str(Path(chunks_jsonl).with_name("object_names.jsonl")),
        default_rel="knowledge/qdrant_ready/object_names.jsonl",
    )
    exports_dir = _as_path(
        paths_raw.get("exports_dir") or "exports",
        default_rel="exports",
    )
    paths = {
        "knowledge_dir": knowledge_dir,
        "code_dir": code_dir,
        "chunks_jsonl": chunks_jsonl,
        "help_jsonl": help_jsonl,
        "object_names_jsonl": object_names_jsonl,
        "exports_dir": exports_dir,
    }

    # --- qdrant ---
    q_raw = dict(raw.get("qdrant") or {})
    q_path = q_raw.get("path") or "knowledge/qdrant_data"
    if not Path(q_path).is_absolute():
        q_path_abs = str((ROOT / q_path).resolve())
    else:
        q_path_abs = q_path
    qdrant = {
        "enabled": bool(q_raw.get("enabled", True)),
        "mode": (q_raw.get("mode") or "url").lower(),
        "path": q_path_abs,
        "url": q_raw.get("url") or "http://localhost:6333",
        "api_key": _env("QDRANT_API_KEY") or q_raw.get("api_key") or "",
        "timeout": float(q_raw.get("timeout") or 120),
        "collection_code": q_raw.get("collection_code") or "onec_code",
        "collection_help": q_raw.get("collection_help") or "onec_help",
        "chunks_jsonl": _as_path(
            q_raw.get("chunks_jsonl") or chunks_jsonl,
            default_rel="knowledge/qdrant_ready/config_chunks.jsonl",
        ),
        "help_jsonl": _as_path(
            q_raw.get("help_jsonl") or help_jsonl,
            default_rel="knowledge/qdrant_ready/help_chunks.jsonl",
        ),
        # default false: runtime не ходит в dump
        "fallback_filesystem": bool(q_raw.get("fallback_filesystem", False)),
        "search_mode": (q_raw.get("search_mode") or "hybrid").lower(),
        "lexical_scan_limit": int(q_raw.get("lexical_scan_limit") or 8000),
        "rrf_k": int(q_raw.get("rrf_k") or 60),
        "light_rerank": bool(q_raw.get("light_rerank", True)),
    }

    # --- embedding ---
    e_raw = dict(raw.get("embedding") or {})
    embedding = {
        "provider": (e_raw.get("provider") or "fastembed").lower(),
        "model": e_raw.get("model")
        or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "batch_size": int(e_raw.get("batch_size") or 64),
        "vector_size": e_raw.get("vector_size"),
        "passage_max_chars": int(e_raw.get("passage_max_chars") or 1400),
        "threads": int(e_raw.get("threads") or 0),
        "base_url": e_raw.get("base_url") or "",
        "api_key": _env("RAG_EMBEDDING_API_KEY") or e_raw.get("api_key") or "",
    }

    # --- prepare ---
    p_raw = dict(raw.get("prepare") or {})
    prepare = {
        "max_file_mb": float(p_raw.get("max_file_mb") or 8),
        "max_chunk_chars": int(p_raw.get("max_chunk_chars") or 2800),
        "chunk_overlap": int(p_raw.get("chunk_overlap") or 150),
        "include_bsl": bool(p_raw.get("include_bsl", True)),
        "xml_cards": bool(p_raw.get("xml_cards", True)),
        "ui_cards": bool(p_raw.get("ui_cards", True)),
        "card_chars": int(p_raw.get("card_chars") or 800),
        "dedupe": bool(p_raw.get("dedupe", True)),
        "clean": bool(p_raw.get("clean", True)),
        "min_body_chars": int(p_raw.get("min_body_chars") or 80),
    }

    # --- llm ---
    providers = dict(raw.get("providers") or {})
    provider_name = (
        _env("RAG_LLM_PROVIDER") or _deep_get(raw, "llm", "provider", default="lmstudio")
    ).lower()
    preset = providers.get(provider_name, {})

    api_key = _env("RAG_LLM_API_KEY") or _deep_get(raw, "llm", "api_key", default="") or ""
    if api_key.strip().lower() in ("lm-studio", "ollama", "not-needed", "none"):
        if preset.get("api_key_env") or provider_name not in ("lmstudio", "ollama"):
            api_key = ""
    if not api_key and preset.get("api_key_env"):
        api_key = os.environ.get(preset["api_key_env"], "") or ""
    if not api_key:
        api_key = preset.get("api_key", "not-needed")

    base_url = (
        _env("RAG_LLM_BASE_URL")
        or _deep_get(raw, "llm", "base_url", default="")
        or preset.get("base_url", "http://localhost:1234/v1")
    )

    llm = {
        "provider": provider_name,
        "model": _env("RAG_LLM_MODEL")
        or _deep_get(raw, "llm", "model", default="qwen3-1.7b"),
        "base_url": str(base_url).rstrip("/"),
        "api_key": api_key,
        "temperature": float(
            _env("RAG_LLM_TEMPERATURE")
            or _deep_get(raw, "llm", "temperature", default=0.1)
        ),
        "context_window": int(
            _env("RAG_LLM_CONTEXT_WINDOW")
            or _deep_get(raw, "llm", "context_window", default=32000)
        ),
        "max_tokens": int(
            _env("RAG_LLM_MAX_TOKENS") or _deep_get(raw, "llm", "max_tokens", default=4096)
        ),
        "max_tool_rounds": int(
            _env("RAG_LLM_MAX_TOOL_ROUNDS")
            or _deep_get(raw, "llm", "max_tool_rounds", default=16)
        ),
        "tool_result_max_chars": int(
            _env("RAG_LLM_TOOL_RESULT_MAX_CHARS")
            or _deep_get(raw, "llm", "tool_result_max_chars", default=8000)
        ),
        "history_max_messages": int(
            _env("RAG_LLM_HISTORY_MAX_MESSAGES")
            or _deep_get(raw, "llm", "history_max_messages", default=40)
        ),
        "history_max_turns": int(
            _env("RAG_LLM_HISTORY_MAX_TURNS")
            or _deep_get(raw, "llm", "history_max_turns", default=6)
        ),
        "keep_recent_tool_full": int(
            _env("RAG_LLM_KEEP_RECENT_TOOL_FULL")
            or _deep_get(raw, "llm", "keep_recent_tool_full", default=2)
        ),
        "old_tool_chars": int(
            _env("RAG_LLM_OLD_TOOL_CHARS")
            or _deep_get(raw, "llm", "old_tool_chars", default=700)
        ),
        "diary_max_chars": int(
            _env("RAG_LLM_DIARY_MAX_CHARS")
            or _deep_get(raw, "llm", "diary_max_chars", default=1400)
        ),
        "diary_trigger_turns": int(
            _env("RAG_LLM_DIARY_TRIGGER_TURNS")
            or _deep_get(raw, "llm", "diary_trigger_turns", default=4)
        ),
        "diary_pressure": float(
            _env("RAG_LLM_DIARY_PRESSURE")
            or _deep_get(raw, "llm", "diary_pressure", default=0.72)
        ),
    }

    agent = {
        "language": _deep_get(raw, "agent", "language", default="ru"),
        "read_only": bool(_deep_get(raw, "agent", "read_only", default=True)),
        "query_default_limit": int(
            _deep_get(raw, "agent", "query_default_limit", default=25)
        ),
        "query_max_limit": int(_deep_get(raw, "agent", "query_max_limit", default=80)),
        "list_objects_cap": int(
            _deep_get(raw, "agent", "list_objects_cap", default=80)
        ),
        # кому отвечает агент: developer | user | both (см. knowledge/режим_*.md)
        "audience": str(_deep_get(raw, "agent", "audience", default="both") or "both").lower(),
    }

    return {
        "backend": backend,
        "http": http,
        "com": com,
        "paths": paths,
        "knowledge_dir": knowledge_dir,
        "code_dir": code_dir,
        "qdrant": qdrant,
        "embedding": embedding,
        "prepare": prepare,
        "llm": llm,
        "agent": agent,
        "providers": providers,
        "config_path": str(cfg_path),
    }
