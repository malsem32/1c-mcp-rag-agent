# Полный прогон: выгрузка 1С → Qdrant

Один раз после **новой** выгрузки конфигурации (или когда обновили `knowledge/код_конфигурации`).

## Что делает

```
knowledge/код_конфигурации     ← ТОЛЬКО выгрузка (агент сюда НЕ ходит)
        │
        ▼  prepare_config_for_qdrant.py
knowledge/qdrant_ready/*.jsonl
        │
        ▼  index_qdrant.py
knowledge/qdrant_data          ← runtime: find_modules / search_code / read_code
        │
        ▼  agent.py / main.py (MCP)
```

В `config.yaml`:
- `qdrant.fallback_filesystem: false` — запрет чтения dump в runtime  
- `qdrant.mode: url` + Docker (`docker compose up -d` / `scripts/start_qdrant_docker.ps1`)  
- `qdrant.search_mode: hybrid` — exact + semantic  

После Docker **обязательно** переиндексировать (коллекции на сервере пустые):

```powershell
.\scripts\start_qdrant_docker.ps1
python scripts\index_qdrant.py --recreate
```

Настройки — **`config.yaml`** (секции `paths`, `qdrant`, `embedding`, `prepare`).  
Секреты — **`.env`**.

---

## Полный прогон (копировать целиком)

```powershell
cd D:\rag_agent
.\.venv\Scripts\Activate.ps1

# зависимости (если ещё не ставили)
pip install -r requirements.txt

# 1) Извлечь весь BSL + запросы 1С → JSONL (долго на большой CF)
python scripts/prepare_config_for_qdrant.py

# 2) Эмбеддинги + загрузка в Qdrant (пересоздать коллекции)
python scripts/index_qdrant.py --recreate

# 3) Проверка
python scripts/verify_qdrant.py
python scripts/index_qdrant.py --status
```

Либо одним скриптом:

```powershell
python scripts/full_qdrant_pipeline.py
```

Флаги:

```powershell
python scripts/full_qdrant_pipeline.py              # prepare + code + help
python scripts/full_qdrant_pipeline.py --code-only  # без справки платформы
python scripts/full_qdrant_pipeline.py --skip-prepare  # только index (JSONL уже есть)
```

---

## По шагам

### 0. Выгрузка 1С

Конфигуратор → **Конфигурация → Выгрузить конфигурацию в файлы**  
Кладёте результат в:

```
D:\rag_agent\knowledge\код_конфигурации
```

Путь задан в `config.yaml` → `paths.code_dir`.

### 1. Prepare (чанки)

```powershell
python scripts/prepare_config_for_qdrant.py
```

Результат: `knowledge/qdrant_ready/config_chunks.jsonl`  
В начале каждого куска — русский контекст (`Документы → … → Формы → …`).

### 2. Index (Qdrant)

```powershell
python scripts/index_qdrant.py --recreate
```

- `--recreate` — с нуля (нужно после смены модели/полной пересборки)
- `--code-only` — только код конфигурации
- `--help-only` — только справка платформы (`knowledge/справка_платформы`)
- Docker **не нужен** (`qdrant.mode: local`)

### 3. Проверка

```powershell
python scripts/verify_qdrant.py
```

Ожидается: `qdrant_ready: true`, `source: qdrant` у `search_code`.

### 4. Агент

```powershell
python cli.py "есть ли индекс? code_status"
python cli.py "найди в коде ТекстЗапроса"
python cli.py "как объявить процедуру в BSL"
```

MCP:

```powershell
python main.py
```

---

## Сколько ждать (факт на этой базе)

| Этап | Факт |
|------|------|
| prepare | **~4 мин** → **79 228** чанков |
| index code | **~4 ч** (local Qdrant, MiniLM) → **79 228** points |
| index help | **~25 мин** → **6 607** points |

Не прерывайте `index` на середине — лучше дождаться или `--recreate` заново.  
При >20k points Qdrant local предупреждает — для скорости позже можно `mode: url` + Docker.

---

## Частые проблемы

| Симптом | Что делать |
|---------|------------|
| `Нет каталога выгрузки` | Проверить `paths.code_dir` и папку `knowledge/код_конфигурации` |
| `Коллекция пуста` | Сначала prepare, потом index |
| `source: files` / grep | Индекс пуст или `qdrant.enabled: false` |
| Мало hits | Неполный prepare/index — полный `--recreate` |
| Сменили embedding.model | Обязательно `index_qdrant.py --recreate` |

---

## Файлы рядом

| Файл | Роль |
|------|------|
| `full_qdrant_pipeline.py` | prepare + index одной командой |
| `prepare_config_for_qdrant.py` | выгрузка → JSONL |
| `index_qdrant.py` | JSONL → Qdrant |
| `verify_qdrant.py` | smoke-проверка поиска |
| `ПОЛНЫЙ_ПРОГОН_QDRANT.md` | эта инструкция |
