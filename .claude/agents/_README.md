# Агенты проекта (RAG-агент 1С)

Перед работой любой агент читает `AGENTS.md` (корень). Главное правило:
**full_name / path объектов — только из Qdrant/tools текущей базы; routing — по структуре фразы, не по словарю предметной области.**

| Агент | Зона |
|---|---|
| `router-guardian` | routing/preflight, phrase_struct, name_match |
| `code-analysis-dev` | inspect_code, trace_calls, movements, explain_fill, epf |
| `data-reports-dev` | build_report, linked_query, attr_query, excel_export, periods |
| `infra-dev` | Qdrant/ingest/backends/MCP/panel/config |
| `hardcode-auditor` | read-only аудит хардкода CF и мёртвого кода |
