# -*- coding: utf-8 -*-
"""Qdrant-слой: эмбеддинги, индекс, семантический поиск по конфигурации 1С.

Импорты ленивые: отсутствие qdrant_client не должно ломать data-tools
(compare_periods / build_report / report_builder).
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "get_qdrant",
    "qdrant_status",
    "search_code_vectors",
    "search_help_vectors",
    "code_collection_ready",
    "find_modules_qdrant",
    "read_module",
    "runtime_fs_allowed",
]


def __getattr__(name: str) -> Any:
    if name in ("get_qdrant", "qdrant_status"):
        from vector.client import get_qdrant, qdrant_status

        return {"get_qdrant": get_qdrant, "qdrant_status": qdrant_status}[name]
    if name in ("search_code_vectors", "search_help_vectors"):
        from vector.search import search_code_vectors, search_help_vectors

        return {
            "search_code_vectors": search_code_vectors,
            "search_help_vectors": search_help_vectors,
        }[name]
    if name in (
        "code_collection_ready",
        "find_modules_qdrant",
        "read_module",
        "runtime_fs_allowed",
    ):
        from vector import catalog as cat

        return getattr(cat, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
