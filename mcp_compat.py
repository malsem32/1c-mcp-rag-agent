"""Совместимость MCP SDK: cancel/timeout не должен ронять весь сервер."""

from __future__ import annotations

from typing import Any


def apply_mcp_agent_allowlist(mcp: Any) -> None:
    """
    MCP list_tools = схема LLM (openai_tools), без алиасов HIDDEN_FROM_AGENT.
    Dispatch / preflight по скрытым именам не трогаем.
    MCP-only extras (test_connection, open_export) остаются видимыми.
    """
    from tools.universal import HIDDEN_FROM_AGENT

    mcp.disable(names=set(HIDDEN_FROM_AGENT), components={"tool"})


def patch_mcp_double_respond() -> None:
    """
    Cursor по idle-timeout отменяет tool и SDK шлёт ответ.
    Когда tool всё же заканчивается — второй respond() даёт
    AssertionError: Request already responded to и убивает TaskGroup.
    """
    from mcp.shared.session import RequestResponder

    orig = RequestResponder.respond
    if getattr(orig, "_rag_idempotent", False):
        return

    async def respond(self, response):  # type: ignore[no-untyped-def]
        if getattr(self, "_completed", False):
            return
        try:
            return await orig(self, response)
        except AssertionError:
            return

    respond._rag_idempotent = True  # type: ignore[attr-defined]
    RequestResponder.respond = respond  # type: ignore[method-assign]
