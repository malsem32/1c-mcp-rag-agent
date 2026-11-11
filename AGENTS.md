# Инструкция для AI-агента (читать ПЕРЕД любой правкой)

Проект: **RAG-агент 1С** (`D:\rag_agent`).  
Стенд пользователя может быть **любой** CF (УТ / УПП / ERP / БП / кастом / BY).  
Цель: надёжный помощник по **коду**, **метаданным** и **данным** текущей базы — без привязки к демо-УТ.

---

## 0. Вложение / путь `.epf` `.erf` `.cfe` — сам выбирай tool

Пользователь **не должен** писать «используй tool». Если в сообщении или во вложении есть `.epf`/`.erf`/`.cfe` (в т.ч. «Форма для СПОТ.epf», скрепка в чате):

1. **Сразу** MCP `inspect_external_processing(path=имя файла, request=фраза целиком)`.
2. **Не** Read / Glob / Search / open_resource — они не разбирают бинарник 1С и врут «файла нет».
3. **Не** проси XML, «прикрепите ещё раз», «нет среди переданных файлов».
4. Имени файла достаточно (проект / Desktop / Downloads ищет сам tool).
5. Если MCP недоступен — Shell:  
   `D:\rag_agent\.venv\Scripts\python.exe` + `inspect_external_processing`.

---

## 0.1. Сборка `.epf` / `.erf` — скиллы, вести журнал

Собираешь или чинишь обработку/отчёт (`epf_build.py`, `erf_build.py`, `ordinary_form.py`, шаблоны `tools/data/*_tpl`):

1. **Сначала** скилл `.claude/skills/epf-build` (контейнер, формы, проверка); для отчёта ещё `.claude/skills/erf-build`.
2. **1С:Предприятие сам не запускаю**: собираю, проверяю без клиента (смоук, обратный разбор, выгрузка Конфигуратором) и отдаю файл с чек-листом. Владелец проверяет и присылает скриншоты или ошибки — по ним дорабатываю.
3. **В конце задачи** — новое найденное (формат, грабли платформы, что не сработало, ошибка владельца с причиной, его пожелание) дописать в «Журнал находок» нужного скилла с датой и пометкой [владелец]/[выгрузка]/[гипотеза]; опровергнутое — исправить в тексте скилла. Нового нет — не трогать.

---

## 0. Перед тем как писать код — чеклист (обязательно)

1. **Прочитал этот файл** (и при необходимости `knowledge/правила_агента.md`).
2. **Понял intent пользователя** — код? данные? отчёт? права?  
3. **Не добавляю hardcode** имён объектов/документов/контрагентов/годов демо.
4. **Ищу существующее** (`name_match`, `phrase_struct`, `preflight`, `business_stems`) — не пложу параллельные костыли.
5. **Правка минимальна** — только то, что нужно для задачи.
6. **После правки** — smoke-test на 2–3 фразах (код + данные + негатив).

Если задача неоднозначна — **спроси**, не угадывай CF.

---

## 1. Жёсткий запрет: hardcode CF

### Нельзя

| Плохо | Почему |
|--------|--------|
| `Документ.РеализацияТоваровУслуж` / `Documents/ЗаказКлиента/...` в runtime-логике | Другая CF сломается |
| Whitelist стемов: `корректир`, `реализац`, `ндс`, `поступлен` для **выбора объекта** | Не универсально |
| «Если есть „продаж“ и „заполн“ — не отчёт» | Костыль; лечится структурой фразы |
| Демо: Ассоль, Сказка, годы 2015 «по умолчанию» как данные | Не данные пользователя |
| `ПроведениеСерверУТ` как единственное имя | В другой CF будет `ПроведениеСервер` / ERP / … |

### Можно

| Хорошо | Где |
|--------|-----|
| Имена из **Qdrant / find_metadata / resolve** текущей CF | `name_match`, tools |
| **Структура фразы**: PascalCase + вопрос → код | `phrase_struct.py` |
| Маркеры data **только в «речи»** (PascalCase вырезан) | `speech_norm` |
| Морфология CamelCase: отрезать хвост частей | `object_name_variants` |
| Платформа: `ОбработкаПроведения`, `Запрос.Текст`, `Движения.` | не CF |
| `business_stems` (продажа/закупка) — только **поисковые слова** для ranking metadata, **не** full_name | `business_stems.py` |
| Паттерны инфраструктуры: `проведениесервер*`, `общегоназначения*` | не бренд УТ |

### Правило одной строки

> **full_name и path объектов — только из tools/Qdrant этой базы; routing — по структуре фразы, не по словарю предметной области.**

---

## 2. Архитектура routing (не путать)

| Intent | Сигнал | Tool |
|--------|--------|------|
| **Данные / отчёт** | «сколько / сумма / excel / список» в **речи** | `build_report`, `linked_query`, `compare_periods` |
| **Реквизиты как данные** | «в каких X …», «какие X не проведены», статус/пустое поле **как фильтр списка** | `query_by_attributes` (structure → ГДЕ). Не агрегат |
| **Код / логика** | PascalCase + «логик/расскажи/как/модул» **или** `is_code_intent` | `inspect_code`, `trace_calls`, `read_code` |
| **Внешний файл** | скинули `.epf` / `.erf` / `.cfe` | `inspect_external_processing` (не Qdrant) |
| **Заполнение поля** | «не заполнился / пусто / почему» в речи | `explain_fill` |
| **Движения** | «регистры / проведение / движения» | `analyze_object_movements` |
| **Структура** | «реквизиты / формы / макеты» | `describe_object`, `get_object_structure` |
| **Справка платформы** | синтаксис BSL без объекта CF | `code_help` |

**Preflight** (`preflight.py`): высокий confidence → один tool.  
Порядок важен: **code / fill / attr_filter ДО `build_report`**.

Ложный `build_report` из-за подстроки в имени процедуры (`…Продаж`) — лечить через **`phrase_struct.speech_norm`**, не новым if «продаж+заполн».

---

## 2.1. Структура проекта (код — в корне, пакета `rag_agent/` больше нет)

```text
D:\rag_agent\
  main.py, cli.py            MCP-сервер (stdio/http) и CLI-агент (бывший agent.py)
  config.py, config.yaml     конфиг
  context_budget.py, session_memory.py, mcp_compat.py, metadata_paths.py
  tools/                     tools агента (router, preflight, phrase_struct, name_match …)
  vector/                    Qdrant: client, search, catalog, embed
  ingest/  backends/  agent/ индексация; доступ к 1С (http/com); цикл LLM
  kb/                        store.py — чтение knowledge/ (код)
  knowledge/                 ДАННЫЕ: правила, справка платформы, документация ИТС (не код)
  scripts/  panel/  onec_http/  exports/  uploads/  info/
```

Импорты — от корня: `from tools.preflight import plan_preflight`, `from kb.store import get_agent_rules`.

## 3. Ключевые модули (не дублировать)

| Модуль | Назначение |
|--------|------------|
| `tools/phrase_struct.py` | PascalCase strip, `is_code_intent`, маркеры в речи |
| `tools/name_match.py` | resolve object_name из каталога Qdrant + token affinity |
| `tools/router.py` | **единый роутер**: `classify` (один порядок правил), `compute_hints` (need_* для loop) |
| `tools/router_detect.py` | высокоточные детекторы ДО цепочки `router._rules` (номер документа, мутации → отказ, справка платформы, формы, СКД, вызовы, блокировки, доступ…). Новый класс ошибки роутинга — сюда, отдельной функцией `_det_*` |
| `tools/meta_class.py` | класс метаданных из речи («регистр сведений», «отчёт») → тип/префикс; слова класса — фильтр, а не токен имени |
| `tools/object_catalog.py` | каталог объектов выгрузки (имя, класс, синоним, RegisterRecords) → `resolve_full_name` (top-k + confident), `documents_writing_register` |
| `tools/tool_subsets.py` | подмножество tools для LLM по категории вопроса |
| `tools/tool_umbrella.py` | **видимые модели tools (~23, `VISIBLE`) и зонтики**: узкий tool скрыт, вызов зонтика с фразой роутер перенаправляет в него (`UMBRELLAS`); `HIDDEN_ALIAS` переписывает имена скрытых tools в текстах для модели. Новый узкий tool — в dispatch + в `UMBRELLAS` подходящего зонтика, не в видимый список |
| `tools/tool_descriptions.py` | краткие описания видимых tools: что делает + 2–3 примера фраз + «не для» |
| `tools/form_command.py` | `explain_command`: кнопка/поле формы → команда → обработчик (Qdrant) → причины недоступности/невидимости |
| `tools/proc_index.py` | `find_definitions` / `find_call_sites` — точные фильтры Qdrant по `proc_names`/`text` (не векторный поиск) |
| `tools/preflight.py` | авто-tool (обёртка над `classify` + run/nudge) |
| `tools/universal.py` | фасад `UniversalTools`; реализация — `tools/uni/*` (mixin'ы по темам) |
| `tools/attr_query.py` | список по реквизитам шапки (`query_by_attributes`) |
| `tools/report_format.py` | `is_manager_report_question`, table_preview |
| `tools/code_search.py` | inspect / outline / risk / query_functions |
| `tools/epf_inspect.py` | внешняя обработка `.epf`/`.erf` (`inspect_external_processing`) |
| `tools/excel_export.py` | красивые xlsx + графики |
| `knowledge/правила_агента.md` | system rules **runtime** LLM-агента 1С |
| `kb/store.py` → `get_agent_rules` | грузится в instructions MCP (код; данные — в `knowledge/`) |

Runtime 1С-агента: **код только из Qdrant**, не FS `knowledge/код_конфигурации` (если `fallback_filesystem=false`).

---

## 4. Стиль правок

- **Минимальный diff** — не рефакторить «заодно» полрепы.
- **Не плодить** markdown/README без запроса пользователя.
- **Не** коммитить/пушить без явной просьбы.
- Комментарии в коде — коротко, по делу; без простыней.
- Сообщения пользователю — **по-русски**, ясно, без воды.
- После routing-фиксов: smoke  
  - код: `…Процедура… логику…` → **не** `build_report`  
  - данные: `продажи за 2015` → `build_report`  
  - fill: `почему не заполнился …` → `explain_fill`
  - attr: `в каких заказах ожидается оплата` → **query_by_attributes**

---

## 5. Типичные ловушки (уже были)

1. **Substring**: `УсловияПродаж` → маркер `продаж` → отчёт.  
   → `speech_norm` / не матчить внутри PascalCase.
2. **«Уже исправлено / разработчик вынес»** — агент read-only, CF не менял.  
   → факт as-is + «Рекомендация (НЕ применено)».
3. **ManagerModule-only** — запросы часто в CommonModules.  
   → multi-module + `name_match` / stem variants.
4. **Доменный whitelist** вместо каталога Qdrant.  
   → `resolve_object_name`.
5. **Спрашивать «хотите посмотреть?»** вместо tool.  
   → fake progress ban + сразу tools.

---

## 6. Когда сомневаешься

```
Универсально ли это на УПП 1.4 без «ЗаказКлиента» / «РеализацияТоваровУслуг»?
Если нет — перепиши через Qdrant + структуру фразы.
```

Не добавляй «ещё один if под этот скрин».  
Ищи **класс ошибки** (substring, неверный preflight order, тонкий ObjectModule) и чини класс.

---

## 7. Быстрые команды проверки

```text
python -c "from tools.preflight import plan_preflight; print(plan_preflight('…'))"
python -c "from tools.name_match import resolve_object_name; print(resolve_object_name('…'))"
python scripts/_audit_hardcode.py   # exports/hardcode_audit.txt
python scripts/_stress_200_run.py --route-only                          # калибровочный набор (248)
python scripts/_stress_200_run.py --route-only --cases _holdout_cases   # hold-out (251) — НЕ подгонять под него
python scripts/_route_diff.py exports/stress_route_new.jsonl            # что сломалось/починилось
python scripts/_resolver_eval.py                                        # резолвер имён объектов
```

---

**Конец инструкции.**  
Если этот файл конфликтует с явной просьбой пользователя в чате — приоритет у **явной просьбы в чате**, затем этот файл.
