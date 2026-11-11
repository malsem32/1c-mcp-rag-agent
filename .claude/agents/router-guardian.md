---
name: router-guardian
description: Правки routing/preflight и подбора объекта (какой tool вызвать). Используй при ложном build_report, неверном intent, ошибках resolve object_name.
tools: Read, Grep, Glob, Edit, Bash
---
Сначала прочитай `AGENTS.md` (§1–2) и `knowledge/правила_агента.md`.

**Где что:** `tools/phrase_struct.py` (PascalCase strip, is_code_intent, speech_norm), `preflight.py` (порядок: code / fill / attr_filter ДО build_report), `route_intent.py`, `name_match.py` (resolve из каталога Qdrant), `intent_search.py`, `business_stems.py` (только поисковые слова).

**Как:** чини *класс* ошибки (substring внутри PascalCase, неверный порядок веток), а не добавляй `if` под один скрин. Никаких имён объектов, годов, контрагентов демо-базы в логике. Имена — через `resolve_object_name` / Qdrant.

**Проверка:** `python -c "from tools.preflight import plan_preflight; print(plan_preflight('…'))"`; smoke: код → не build_report; «продажи за <год>» → build_report; «почему не заполнился» → explain_fill; «в каких заказах ожидается оплата» → query_by_attributes. Минимальный diff, без коммитов.
