"""
Универсальные HTTP-инструменты 1С для AI-агента (LM Studio).

Метаданные → реквизиты → execute_query (язык запросов 1С).
Данные не обёрнуты в отдельные tools — только ВЫБРАТЬ.

Фасад: реализация — tools/uni/* (mixin'ы по темам).
Имена ниже реэкспортируются для main.py, loop.py, mcp_compat, scripts, tests.
"""

from __future__ import annotations

from tools.uni.access import AccessMixin
from tools.uni.base import BaseMixin
from tools.uni.catalog import (
    HIDDEN_FROM_AGENT,
    TOOL_DEFINITIONS,
    _HIDDEN_FROM_LLM,
    _manager_help,
    _system_prompt_ru,
    _tool,
    agent_visible_tool_names,
    is_agent_visible_tool,
)
from tools.uni.code import CodeMixin
from tools.uni.compare import CompareMixin
from tools.uni.dump import DumpMixin
from tools.uni.export import ExportMixin
from tools.uni.help import HelpMixin
from tools.uni.linked import LinkedMixin
from tools.uni.metadata import MetadataMixin
from tools.uni.query import QueryMixin
from tools.uni.reports import ReportsMixin
from tools.uni.safety import (
    _SELECT_START,
    _UNSAFE_PATTERNS,
    _strip_query_comments,
    clamp_limit,
    is_safe_select_query,
)
from tools.uni.structure import StructureMixin, _phrase_has_object_for_describe

__all__ = [
    "UniversalTools",
    "HIDDEN_FROM_AGENT",
    "TOOL_DEFINITIONS",
    "_HIDDEN_FROM_LLM",
    "_SELECT_START",
    "_UNSAFE_PATTERNS",
    "_manager_help",
    "_phrase_has_object_for_describe",
    "_strip_query_comments",
    "_system_prompt_ru",
    "_tool",
    "agent_visible_tool_names",
    "clamp_limit",
    "is_agent_visible_tool",
    "is_safe_select_query",
]


class UniversalTools(
    BaseMixin,
    HelpMixin,
    CodeMixin,
    DumpMixin,
    AccessMixin,
    LinkedMixin,
    ReportsMixin,
    CompareMixin,
    MetadataMixin,
    StructureMixin,
    QueryMixin,
    ExportMixin,
):
    """Все tools агента 1С; dispatch/openai_tools — в BaseMixin."""
