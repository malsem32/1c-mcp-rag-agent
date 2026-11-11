# -*- coding: utf-8 -*-
"""Режим без подключения к 1С: код и структура из Qdrant, данные недоступны."""

from __future__ import annotations

from typing import Any

from backends.base import (
    METADATA_TYPES,
    OneCBackend,
    full_name_prefix_for_dump_folder,
    normalize_metadata_type,
)

_NO_DATA = {
    "error": "Нет подключения к 1С (режим «только Qdrant»): данные базы недоступны",
    "offline": True,
    "hint": "Доступны поиск и чтение кода, структура объектов и справка из индекса. "
    "Для данных включите подключение к 1С: Панель → «Подключение к 1С» → backend=http.",
}


def _catalog() -> list[tuple[str, str]]:
    """(имя объекта, папка выгрузки) из каталога, собранного при индексации."""
    from tools.name_match import object_names_jsonl_path

    rows: list[tuple[str, str]] = []
    path = object_names_jsonl_path()
    if not path.is_file():
        return rows
    import json

    for ln in path.read_text(encoding="utf-8").splitlines():
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        if o.get("object_name"):
            rows.append((str(o["object_name"]), str(o.get("dump_folder") or "")))
    return rows


class OfflineBackend(OneCBackend):
    def test_connection(self) -> dict[str, Any]:
        return {
            "ok": True,
            "mode": "qdrant_only",
            "note": "Подключение к 1С отключено; работает поиск по коду/метаданным в Qdrant",
        }

    def list_objects(self, metadata_type: str) -> dict[str, Any]:
        mt = normalize_metadata_type(metadata_type) or metadata_type
        meta = METADATA_TYPES.get(mt)
        if not meta:
            return {"error": f"Неизвестный класс метаданных: {metadata_type}", "offline": True}
        folder, prefix = meta.get("dump_folder"), meta["full_name_prefix"]
        names = sorted({n for n, f in _catalog() if f == folder})
        return {
            "type": mt,
            "source": "qdrant_catalog",
            "count": len(names),
            "objects": [{"name": n, "full_name": f"{prefix}.{n}"} for n in names],
        }

    def find_metadata(self, search_query: str = "", object_type: str = "Все", limit: int = 100) -> dict[str, Any]:
        q = (search_query or "").lower()
        mt = normalize_metadata_type(object_type) if object_type and object_type != "Все" else None
        want = METADATA_TYPES[mt]["dump_folder"] if mt in METADATA_TYPES else None
        res = []
        for n, f in _catalog():
            if want and f != want:
                continue
            prefix = full_name_prefix_for_dump_folder(f)
            if prefix and (not q or q in n.lower()):
                res.append({"name": n, "full_name": f"{prefix}.{n}", "synonym": ""})
            if len(res) >= limit:
                break
        return {"search_query": search_query, "object_type": object_type, "source": "qdrant_catalog",
                "found_count": len(res), "results": res}

    def get_object_structure(self, full_name: str) -> dict[str, Any]:
        return {**_NO_DATA, "error": "Структура реквизитов недоступна без 1С", "full_name": full_name,
                "hint": "Используйте inspect_code / read_code — XML-структура объекта есть в индексе кода"}

    def execute_query(self, query: str, limit: int = 50) -> dict[str, Any]:
        return dict(_NO_DATA)

    def query_event_log(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        return dict(_NO_DATA)

    def list_sessions(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        return dict(_NO_DATA)
