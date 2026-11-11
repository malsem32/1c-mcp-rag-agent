"""Бэкенд через COM (V8x.COMConnector) — файловая или серверная база."""

from __future__ import annotations

import threading
from typing import Any

from backends.base import METADATA_TYPES, OneCBackend

_thread_local = threading.local()


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return "<unreadable>"


class ComBackend(OneCBackend):
    """
    Универсальный COM-доступ к любой конфигурации 1С.
    Не содержит hardcode имён документов/справочников.
    """

    def __init__(self, com_config: dict[str, Any]) -> None:
        self.connection_string = com_config.get("connection_string") or ""
        self.prog_id = com_config.get("prog_id") or "V83.COMConnector"
        if not self.connection_string.strip():
            raise ValueError(
                "COM: не задана connection_string. "
                "Укажите в config.yaml или RAG_COM_CONNECTION_STRING."
            )

    def _get_connection(self) -> Any:
        try:
            import pythoncom
            from win32com.client import Dispatch
        except ImportError as e:
            raise RuntimeError(
                "Для COM-бэкенда нужны pywin32 и установленная платформа 1С: "
                f"{e}"
            ) from e

        pythoncom.CoInitialize()
        if getattr(_thread_local, "connection", None) is None:
            connector = Dispatch(self.prog_id)
            _thread_local.connection = connector.Connect(self.connection_string)
        return _thread_local.connection

    def _value_table_to_dicts(self, value_table: Any, limit: int) -> tuple[list[dict], bool]:
        if value_table is None:
            return [], False
        try:
            count = value_table.Count()
        except Exception:
            return [], False
        if count == 0:
            return [], False

        columns: list[str] = []
        for i in range(value_table.Columns.Count()):
            columns.append(_safe_str(value_table.Columns.Get(i).Name))

        rows: list[dict[str, Any]] = []
        truncated = False
        for i in range(count):
            if len(rows) >= limit:
                truncated = True
                break
            row = value_table.Get(i)
            row_dict: dict[str, Any] = {}
            for col in columns:
                try:
                    value = row.Get(col)
                    row_dict[col] = None if value is None else _safe_str(value)
                except Exception:
                    row_dict[col] = "<ошибка чтения>"
            rows.append(row_dict)
        return rows, truncated

    def test_connection(self) -> dict[str, Any]:
        try:
            conn = self._get_connection()
            meta = conn.Metadata
            return {
                "status": "ok",
                "backend": "com",
                "config_name": _safe_str(getattr(meta, "Name", "")),
                "config_synonym": _safe_str(getattr(meta, "Synonym", "")),
                "config_version": _safe_str(getattr(meta, "Version", "")),
            }
        except Exception as e:
            return {"status": "error", "backend": "com", "error": str(e)}

    def _iter_collection(self, collection: Any):
        if collection is None:
            return
        try:
            count = collection.Count()
        except Exception:
            return
        for i in range(count):
            try:
                yield collection.Get(i)
            except Exception:
                continue

    def _collection_for_type(self, conn: Any, metadata_type: str) -> Any | None:
        info = METADATA_TYPES.get(metadata_type)
        if not info:
            return None
        meta = conn.Metadata
        attr = info["com_attr"]
        if hasattr(meta, attr):
            return getattr(meta, attr)
        # fallback русские имена
        ru = info.get("com_ru")
        if ru and hasattr(meta, ru):
            return getattr(meta, ru)
        return None

    def list_objects(self, metadata_type: str) -> dict[str, Any]:
        try:
            if metadata_type not in METADATA_TYPES:
                return {
                    "error": f"Неизвестный тип: {metadata_type}",
                    "allowed": list(METADATA_TYPES.keys()),
                }
            conn = self._get_connection()
            collection = self._collection_for_type(conn, metadata_type)
            if collection is None:
                return {"error": f"Коллекция {metadata_type} недоступна в этой базе"}

            prefix = METADATA_TYPES[metadata_type]["full_name_prefix"]
            objects = []
            for item in self._iter_collection(collection):
                name = _safe_str(item.Name)
                objects.append(
                    {
                        "full_name": f"{prefix}.{name}",
                        "name": name,
                        "synonym": _safe_str(getattr(item, "Synonym", "")),
                    }
                )
            return {"type": metadata_type, "count": len(objects), "objects": objects}
        except Exception as e:
            return {"error": str(e)}

    def find_metadata(
        self,
        search_query: str = "",
        object_type: str = "Все",
        limit: int = 100,
    ) -> dict[str, Any]:
        try:
            conn = self._get_connection()
            results: list[dict[str, Any]] = []
            search_lower = (search_query or "").lower()

            if object_type and object_type != "Все":
                types = [object_type] if object_type in METADATA_TYPES else []
                if not types:
                    return {
                        "error": f"Неизвестный object_type: {object_type}",
                        "allowed": ["Все", *METADATA_TYPES.keys()],
                    }
            else:
                types = list(METADATA_TYPES.keys())

            for mtype in types:
                collection = self._collection_for_type(conn, mtype)
                if collection is None:
                    continue
                prefix = METADATA_TYPES[mtype]["full_name_prefix"]
                for item in self._iter_collection(collection):
                    name = _safe_str(item.Name)
                    synonym = _safe_str(getattr(item, "Synonym", ""))
                    if (
                        not search_lower
                        or search_lower in name.lower()
                        or search_lower in synonym.lower()
                    ):
                        results.append(
                            {
                                "full_name": f"{prefix}.{name}",
                                "name": name,
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
            }
            if len(results) >= limit:
                out["note"] = f"Показано максимум {limit}. Уточните запрос."
            return out
        except Exception as e:
            return {"error": str(e)}

    def _add_attrs(self, conn: Any, collection: Any, target: list[dict], kind: str = "attribute") -> None:
        if collection is None:
            return
        try:
            count = collection.Count()
        except Exception:
            return
        for i in range(count):
            try:
                attr = collection.Get(i)
                entry: dict[str, Any] = {
                    "kind": kind,
                    "name": _safe_str(attr.Name),
                    "synonym": _safe_str(getattr(attr, "Synonym", "")),
                }
                if hasattr(attr, "Type"):
                    try:
                        entry["type"] = _safe_str(conn.String(attr.Type))
                    except Exception:
                        entry["type"] = _safe_str(attr.Type)
                target.append(entry)
            except Exception:
                continue

    def get_object_structure(self, full_name: str) -> dict[str, Any]:
        try:
            if not full_name or "." not in full_name:
                return {
                    "error": "Нужно полное имя вида Тип.Имя",
                    "example": "Справочник.<ИмяИзFindMetadata>",
                }
            conn = self._get_connection()
            meta = conn.Metadata.FindByFullName(full_name)
            if meta is None:
                return {
                    "error": f"Объект '{full_name}' не найден",
                    "hint": "Вызовите find_metadata для поиска точного имени",
                }

            info: dict[str, Any] = {
                "full_name": _safe_str(meta.FullName()),
                "name": _safe_str(meta.Name),
                "synonym": _safe_str(getattr(meta, "Synonym", "")),
                "comment": _safe_str(getattr(meta, "Comment", "")),
                "attributes": [],
                "tabular_sections": [],
                "dimensions": [],
                "resources": [],
            }

            if hasattr(meta, "StandardAttributes"):
                self._add_attrs(conn, meta.StandardAttributes, info["attributes"], "standard")
            if hasattr(meta, "Attributes"):
                self._add_attrs(conn, meta.Attributes, info["attributes"], "attribute")
            if hasattr(meta, "Dimensions"):
                self._add_attrs(conn, meta.Dimensions, info["dimensions"], "dimension")
            if hasattr(meta, "Resources"):
                self._add_attrs(conn, meta.Resources, info["resources"], "resource")

            if hasattr(meta, "TabularSections"):
                try:
                    ts_count = meta.TabularSections.Count()
                except Exception:
                    ts_count = 0
                for i in range(ts_count):
                    try:
                        tab = meta.TabularSections.Get(i)
                        ts_info = {
                            "name": _safe_str(tab.Name),
                            "synonym": _safe_str(getattr(tab, "Synonym", "")),
                            "attributes": [],
                        }
                        if hasattr(tab, "Attributes"):
                            self._add_attrs(
                                conn, tab.Attributes, ts_info["attributes"], "tabular_attribute"
                            )
                        info["tabular_sections"].append(ts_info)
                    except Exception:
                        continue

            # Значения перечисления
            if full_name.startswith("Перечисление.") and hasattr(meta, "EnumValues"):
                values = []
                for item in self._iter_collection(meta.EnumValues):
                    values.append(
                        {
                            "name": _safe_str(item.Name),
                            "synonym": _safe_str(getattr(item, "Synonym", "")),
                        }
                    )
                info["enum_values"] = values

            return info
        except Exception as e:
            return {"error": str(e)}

    def execute_query(self, query: str, limit: int = 50) -> dict[str, Any]:
        try:
            conn = self._get_connection()
            q = conn.NewObject("Query")
            q.Text = query
            result = q.Execute().Unload()
            data, truncated = self._value_table_to_dicts(result, limit)
            return {
                "rows_count": len(data),
                "truncated": truncated,
                "limit": limit,
                "data": data,
            }
        except Exception as e:
            return {"error": f"Ошибка выполнения запроса: {e}"}
