# HTTP-сервис 1С для агента (`hs/ai`)

Код: **Module.bsl** — единственный runtime-файл расширения.

## Установка (кратко)

1. Создайте HTTP-сервис в расширении конфигурации, корневой URL: `ai`.
2. Вставьте/обновите `Module.bsl`.
3. Опубликуйте базу (веб-сервер 1С).
4. В `config.yaml`:  
   `http.base_url: "http://localhost/<публикация>/hs/ai"`
5. Проверка: `GET …/hs/ai/ping` или `python onec_http/test_http_api.py`

Подробности и troubleshooting: `knowledge/config/http_setup.md`.

## Методы

- `GET list|object|info|search`
- `POST query` — JSON `{"query":"ВЫБРАТЬ …","limit":50}`
- `GET sessions` — активные сеансы ИБ (`?user=&app=&unique=1`)
- `POST eventlog` — журнал регистрации:
  ```json
  {
    "limit": 50,
    "date_from": "2024-01-01T00:00:00",
    "date_to": "2024-01-31T23:59:59",
    "level": "Error",
    "user": "",
    "event": "",
    "q": "подстрока"
  }
  ```
  Нужны права пользователя публикации на **журнал регистрации**.

Запросы данных — язык 1С, одной строкой.
