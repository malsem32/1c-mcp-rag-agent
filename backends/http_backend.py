"""Бэкенд через HTTP-сервис 1С (расширение /hs/ai)."""

from __future__ import annotations

import threading
from typing import Any

import httpx

from backends.base import OneCBackend


class HttpBackend(OneCBackend):
    """
    Универсальный доступ через HTTP API расширения 1С.

    Ожидаемые эндпоинты:
      GET  /list?type=Справочники|Документы|...
      GET  /object?name=Справочник.<ИмяИзFindMetadata>
      POST /query  body: {"query": "...", "limit": 50}
      GET  /info   (опционально — информация о базе)
      GET  /search?q=...&type=...  (опционально)
    """

    def __init__(self, http_config: dict[str, Any]) -> None:
        self.base_url = (http_config.get("base_url") or "").rstrip("/")
        self.timeout = float(http_config.get("timeout") or 30.0)
        self.auth_mode = (http_config.get("auth") or "none").lower()
        self.username = http_config.get("username") or ""
        self.password = http_config.get("password") or ""
        self._client: httpx.Client | None = None
        self._client_lock = threading.Lock()

    def _client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(self.timeout),
            "limits": httpx.Limits(
                max_keepalive_connections=8,
                max_connections=16,
                keepalive_expiry=30.0,
            ),
        }
        if self.auth_mode == "basic" and self.username:
            kwargs["auth"] = (self.username, self.password)
        return kwargs

    def _get_client(self) -> httpx.Client:
        with self._client_lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.Client(**self._client_kwargs())
            return self._client

    def close(self) -> None:
        with self._client_lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
                self._client = None

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            client = self._get_client()
            response = client.request(method, url, **kwargs)
            response.encoding = "utf-8"
            response.raise_for_status()
            if not response.content:
                return {"ok": True}
            return response.json()
        except httpx.HTTPStatusError as e:
            text = (e.response.text or "")[:800]
            # Платформа 1С: «Обработчик запроса не найден» — метод не привязан в конфигураторе
            if "обработчик запроса не найден" in text.lower():
                return {
                    "error": f"HTTP {e.response.status_code}: обработчик не найден ({method} /{path})",
                    "hint": (
                        "В конфигураторе HTTP-сервис AI → шаблон «query» → метод POST → "
                        "обработчик queryPOST (или шаблон * → POST → ОбработчикPOST). "
                        "Затем переопубликовать. GET list/object уже работают — не хватает только POST."
                    ),
                    "detail": text.strip()[:200],
                }
            try:
                data = e.response.json()
                if isinstance(data, dict):
                    data.setdefault("error", f"HTTP {e.response.status_code}")
                    return data
            except Exception:
                pass
            return {
                "error": f"HTTP {e.response.status_code}",
                "detail": text[:500],
            }
        except Exception as e:
            if isinstance(e, (httpx.TransportError, httpx.TimeoutException)):
                self.close()
            return {"error": str(e)}

    def test_connection(self) -> dict[str, Any]:
        # /info — опциональный; fallback на list
        info = self._request("GET", "info")
        if "error" not in info:
            info.setdefault("status", "ok")
            info.setdefault("backend", "http")
            info.setdefault("base_url", self.base_url)
            return info

        probe = self._request("GET", "list", params={"type": "Справочники"})
        if "error" in probe:
            return {
                "status": "error",
                "backend": "http",
                "base_url": self.base_url,
                "error": probe.get("error"),
                "detail": probe.get("detail"),
            }
        return {
            "status": "ok",
            "backend": "http",
            "base_url": self.base_url,
            "note": "Эндпоинт /info недоступен; /list отвечает",
            "sample_count": len(probe) if isinstance(probe, list) else probe.get("count"),
        }

    # Соответствие класса метаданных → префикс в языке запросов 1С
    _TYPE_PREFIX = {
        k: v["full_name_prefix"]
        for k, v in __import__(
            "backends.base", fromlist=["METADATA_TYPES"]
        ).METADATA_TYPES.items()
    }

    def _normalize_list_payload(self, data: Any) -> dict[str, list[str]]:
        """
        Приводит ответ /list к { 'Справочники': ['Имя', ...], ... }.

        Варианты API:
          - { "Справочники": [...], "Документы": [...], "hint": "..." }
          - { "objects": [...] } / { "items": [...] }
          - [ "Имя1", "Имя2" ]
        """
        if isinstance(data, list):
            return {"_flat": [str(x) for x in data]}
        if not isinstance(data, dict) or data.get("error"):
            return {}

        result: dict[str, list[str]] = {}
        for key, val in data.items():
            if key in ("hint", "type", "error", "detail", "note", "count"):
                continue
            if key in self._TYPE_PREFIX and isinstance(val, list):
                # сохраняем dict (name/synonym/full_name) или строки
                result[key] = val
            elif key in ("objects", "items", "data") and isinstance(val, list):
                result["_flat"] = val
        return result

    def list_objects(self, metadata_type: str) -> dict[str, Any]:
        data = self._request("GET", "list", params={"type": metadata_type})
        if isinstance(data, dict) and data.get("error"):
            return data

        grouped = self._normalize_list_payload(data)
        if not grouped:
            if isinstance(data, dict):
                data.setdefault("type", metadata_type)
                return data
            return {"error": "Неожиданный формат /list", "raw": str(data)[:300]}

        # Если API вернул все классы сразу — берём нужный
        names: list[str] = []
        if metadata_type in grouped:
            names = grouped[metadata_type]
        elif "_flat" in grouped:
            names = grouped["_flat"]
        else:
            # тип не найден, но есть другие ключи
            return {
                "error": f"В ответе /list нет ключа «{metadata_type}»",
                "available_keys": list(grouped.keys()),
            }

        prefix = self._TYPE_PREFIX.get(metadata_type, metadata_type.rstrip("и"))
        objects: list[dict[str, Any]] = []
        for n in names:
            if isinstance(n, dict):
                short = str(n.get("name") or n.get("Имя") or n.get("Name") or "")
                synonym = str(
                    n.get("synonym") or n.get("Синоним") or n.get("Synonym") or ""
                )
                full_name = str(
                    n.get("full_name")
                    or n.get("FullName")
                    or (f"{prefix}.{short}" if short else "")
                )
                objects.append(
                    {"name": short, "full_name": full_name, "synonym": synonym}
                )
            else:
                s = str(n)
                objects.append(
                    {
                        "name": s if "." not in s else s.split(".", 1)[1],
                        "full_name": s if "." in s else f"{prefix}.{s}",
                        "synonym": "",
                    }
                )
        return {
            "type": metadata_type,
            "count": len(objects),
            "objects": objects,
        }

    def find_metadata(
        self,
        search_query: str = "",
        object_type: str = "Все",
        limit: int = 100,
    ) -> dict[str, Any]:
        # Если на стороне 1С есть /search — используем
        data = self._request(
            "GET",
            "search",
            params={"q": search_query, "type": object_type, "limit": limit},
        )
        if (
            isinstance(data, dict)
            and "error" not in data
            and "results" in data
            and not data.get("error")
        ):
            return data
        if isinstance(data, list) and data:
            return {
                "search_query": search_query,
                "object_type": object_type,
                "found_count": len(data),
                "results": data,
            }

        # rich=1 — name+synonym+full_name (универсально для любой конфигурации)
        raw = self._request(
            "GET",
            "list",
            params={"type": object_type or "Все", "rich": "1"},
        )
        if isinstance(raw, dict) and raw.get("error"):
            # fallback без rich (старый HTTP-сервис)
            raw = self._request("GET", "list", params={"type": object_type or "Все"})
        if isinstance(raw, dict) and raw.get("error"):
            return raw

        grouped = self._normalize_list_payload(raw)
        if object_type and object_type != "Все":
            types_to_scan = [object_type]
        else:
            types_to_scan = [k for k in grouped.keys() if k != "_flat"] or list(
                self._TYPE_PREFIX.keys()
            )

        results: list[dict[str, Any]] = []
        search_lower = (search_query or "").lower()

        for mtype in types_to_scan:
            if mtype == "_flat":
                continue
            if mtype in grouped:
                names = grouped[mtype]
            elif "_flat" in grouped and object_type == mtype:
                names = grouped["_flat"]
            else:
                # дозапрос конкретного типа (на случай если первый list без ключей)
                listed = self.list_objects(mtype)
                if listed.get("error"):
                    continue
                names = [
                    o.get("name", "") if isinstance(o, dict) else str(o)
                    for o in listed.get("objects") or []
                ]

            prefix = self._TYPE_PREFIX.get(mtype, mtype)
            for n in names:
                synonym = ""
                if isinstance(n, dict):
                    short = str(
                        n.get("name") or n.get("Имя") or n.get("Name") or ""
                    )
                    synonym = str(
                        n.get("synonym") or n.get("Синоним") or n.get("Synonym") or ""
                    )
                    full_name = str(
                        n.get("full_name")
                        or n.get("FullName")
                        or (f"{prefix}.{short}" if short else "")
                    )
                    if not short and full_name and "." in full_name:
                        short = full_name.split(".", 1)[1]
                else:
                    name = str(n)
                    if "." in name:
                        full_name = name
                        short = name.split(".", 1)[1]
                    else:
                        short = name
                        full_name = f"{prefix}.{name}"

                if (
                    not search_lower
                    or search_lower in short.lower()
                    or search_lower in full_name.lower()
                    or search_lower in synonym.lower()
                ):
                    results.append(
                        {
                            "full_name": full_name,
                            "name": short,
                            "synonym": synonym,
                            "type": mtype,
                        }
                    )
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break

        out: dict[str, Any] = {
            "search_query": search_query,
            "object_type": object_type,
            "found_count": len(results),
            "results": results,
            "source": "http_list_filter",
        }
        if len(results) >= limit:
            out["note"] = f"Показано максимум {limit}. Уточните запрос."
        return out

    def get_object_structure(self, full_name: str) -> dict[str, Any]:
        if not full_name or "." not in full_name:
            return {
                "error": "Нужно полное имя вида Тип.Имя",
                "example": "Справочник.<ИмяИзFindMetadata>",
            }
        # Разные варианты, т.к. HTTP-сервисы 1С по-разному читают параметры
        attempts = [
            ("GET", "object", {"name": full_name}),
            ("GET", "object", {"Name": full_name}),
            ("GET", "metadata", {"name": full_name}),
            ("GET", "list", {"name": full_name}),
        ]
        last: dict[str, Any] = {}
        for method, path, params in attempts:
            data = self._request(method, path, params=params)
            last = data if isinstance(data, dict) else {"raw": data}
            if not isinstance(data, dict):
                continue
            if data.get("error"):
                # «не передан name» — пробуем следующий способ
                err = str(data.get("error", "")).lower()
                if "не передан" in err or "not found" in err or "404" in err:
                    continue
                return data
            # успех: есть реквизиты / full_name / name объекта
            if any(
                k in data
                for k in (
                    "attributes",
                    "Attributes",
                    "Реквизиты",
                    "full_name",
                    "tabular_sections",
                    "fields",
                )
            ):
                return data
            # list-ответ со всеми классами — не структура
            if "Справочники" in data and "Документы" in data:
                continue
        last = last if last else {"error": "Не удалось получить структуру"}
        last.setdefault(
            "hint",
            "На стороне 1С почините GET object?name=Тип.Имя (см. onec_http/Module.bsl). "
            "Менеджерские инструменты могут работать на стандартных полях без структуры.",
        )
        return last

    def execute_query(self, query: str, limit: int = 50, params: dict[str, Any] | None = None) -> dict[str, Any]:
        import json

        payload: dict[str, Any] = {"query": query, "limit": int(limit)}
        if params:
            # HTTP-сервис ≥ этой версии Module.bsl: даты ISO, {"type":"Справочник.X","uuid":…}, перечисления
            payload["params"] = params
        data = self._request(
            "POST",
            "query",
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        if isinstance(data, dict) and data.get("error"):
            err_l = str(data.get("error", "")).lower()
            if "обработчик" not in err_l:
                data.setdefault(
                    "hint",
                    "Ошибка /query. Проверьте модуль onec_http/Module.bsl и права на выполнение запросов.",
                )
            data.setdefault("sent_query", query[:300])
        return data

    def query_event_log(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST /eventlog — журнал регистрации (ВыгрузитьЖурналРегистрации)."""
        import json

        payload = dict(filters or {})
        data = self._request(
            "POST",
            "eventlog",
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        if isinstance(data, dict) and data.get("error"):
            err_l = str(data.get("error", "")).lower()
            if "обработчик" in err_l or "не найден" in err_l or "404" in err_l:
                data.setdefault(
                    "hint",
                    "Нет POST /eventlog. Обновите onec_http/Module.bsl (eventlogPOST) "
                    "и переопубликуйте HTTP-сервис. Права: просмотр журнала регистрации.",
                )
        return data if isinstance(data, dict) else {"error": str(data)[:300]}

    def list_sessions(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET /sessions — активные сеансы ИБ (ПолучитьСеансыИнформационнойБазы)."""
        params: dict[str, Any] = {}
        f = filters or {}
        if f.get("user"):
            params["user"] = f["user"]
        if f.get("application") or f.get("app"):
            params["app"] = f.get("application") or f.get("app")
        if f.get("unique") or f.get("distinct_users"):
            params["unique"] = "1"
        data = self._request("GET", "sessions", params=params or None)
        if isinstance(data, dict) and data.get("error"):
            err_l = str(data.get("error", "")).lower()
            if "обработчик" in err_l or "не найден" in err_l or "404" in err_l:
                data.setdefault(
                    "hint",
                    "Нет GET /sessions. Обновите onec_http/Module.bsl (sessionsGET) "
                    "и переопубликуйте. Права: просмотр активных пользователей / администрирование.",
                )
        return data if isinstance(data, dict) else {"error": str(data)[:300]}
