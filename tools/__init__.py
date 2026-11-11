"""Универсальные инструменты 1С (независимы от конкретной конфигурации)."""

from tools.universal import (
    TOOL_DEFINITIONS,
    UniversalTools,
    clamp_limit,
    is_safe_select_query,
)

__all__ = [
    "TOOL_DEFINITIONS",
    "UniversalTools",
    "clamp_limit",
    "is_safe_select_query",
]
