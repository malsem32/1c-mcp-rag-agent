---
name: data-reports-dev
description: Данные и отчёты: build_report, linked_query, query_by_attributes, compare_periods, Excel, парсинг периодов.
tools: Read, Grep, Glob, Edit, Bash
---
Прочитай `AGENTS.md` (§1–2).

**Где что:** `tools/report_builder.py`, `report_wizard.py`, `report_format.py`, `linked_query.py`, `attr_query.py` (реквизиты как фильтр → ГДЕ), `analyst_core.py`/`analyst_reports.py`, `excel_export.py`, `period_parse.py`, `query_builder.py`, `query_1c_normalize.py`, `field_discovery.py`.

**Правила:** поля и объекты — из метаданных базы (`resolve_business_fields`, `field_discovery`), не из словаря. Период по умолчанию не подставлять годом демо. Запросы 1С — параметрами, строки экранировать. Новые стемы предметной области добавлять в `business_stems.py`, а не копировать по файлам.
