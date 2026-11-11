---
name: infra-dev
description: Инфраструктура: Qdrant, индексация/ingest, backends (http/com), MCP-сервер, panel, config.yaml, цикл LLM-агента.
tools: Read, Grep, Glob, Edit, Bash
---
Прочитай `AGENTS.md` и `ИНСТРУКЦИЯ_РАЗВЕРТЫВАНИЕ.md`.

**Где что:** `vector/*` (client/search/catalog/embed), `ingest/*`, `backends/*`, `agent/loop.py`, `context_budget.py`, `knowledge/store.py`, `main.py` (единый MCP: stdio/http), `scripts/mcp_http.py`, `panel/server.py` (только 127.0.0.1, Host + X-Panel), `config.yaml`, `scripts/index_qdrant.py`, `prepare_config_for_qdrant.py`.

**Правила:** секреты — только `.env`; в `config.yaml` пустые. Не оставлять реальные логины/URL демо в конфиге по умолчанию. Скрипты запускать с `PYTHONPATH=.` и `PYTHONUTF8=1`. MCP по HTTP по умолчанию слушает 127.0.0.1; 0.0.0.0 — только с `RAG_MCP_TOKEN` (иначе отказ старта).
