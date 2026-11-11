"""
MCP-сервер 1С — единая точка входа (stdio для Cursor/LM Studio, HTTP для Open WebUI).

  python main.py                          # stdio
  python main.py --transport http         # http://127.0.0.1:8100/mcp

HTTP: host/port — флаги или RAG_MCP_HOST / RAG_MCP_PORT; при RAG_MCP_TOKEN
нужен заголовок Authorization: Bearer <токен>.

Список tools генерируется из TOOL_DEFINITIONS (та же схема, что у LLM-агента),
вызов — через UniversalTools.dispatch (с ужатием результата под контекст).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import logging
import os
from typing import Any

logging.getLogger("mcp").setLevel(logging.ERROR)
logging.getLogger("fastmcp").setLevel(logging.ERROR)

from mcp_compat import patch_mcp_double_respond

patch_mcp_double_respond()

import anyio
from fastmcp import FastMCP
from fastmcp.tools import Tool

from backends import create_backend
from config import load_config
from tools.excel_export import export_base_url
from tools.universal import UniversalTools

config = load_config()
if config.get("backend") not in ("http", "none", "offline", "off"):
    config["backend"] = "http"  # MCP: только HTTP; COM здесь не поддерживается

backend = create_backend(config)
tools = UniversalTools(
    backend,
    read_only=True,
    default_limit=config["agent"]["query_default_limit"],
    max_limit=config["agent"]["query_max_limit"],
    list_objects_cap=config["agent"].get("list_objects_cap", 80),
    tool_result_max_chars=config["llm"].get("tool_result_max_chars", 6000),
    compact_results=True,
    code_dir=config.get("code_dir") or "",
)

_CTX = config["llm"].get("context_window", 32000)


def _warm_files_mirror() -> None:
    """Фоном развернуть выгрузку из коллекции файлов Qdrant, чтобы первый вопрос про формы не ждал."""
    try:
        from vector.files import ensure_mirror

        ensure_mirror()
    except Exception:  # noqa: BLE001
        pass


import threading  # noqa: E402

threading.Thread(target=_warm_files_mirror, daemon=True, name="files-mirror").start()

# Правила агента — из knowledge/правила_агента.md (не хардкод)
try:
    from kb.store import get_agent_rules

    _AGENT_INSTRUCTIONS = get_agent_rules(
        context_window=int(_CTX or 32000),
        export_base=export_base_url(),
    )
except Exception:
    _AGENT_INSTRUCTIONS = (
        f"Помощник по 1С. Контекст ~{_CTX}. "
        "Отчёты: build_report с фразой пользователя целиком. "
        "Не выдумывай имена документов. Отвечай по-русски."
    )

_AGENT_INSTRUCTIONS = (
    "ПРИОРИТЕТ 1: на ЛЮБОЙ вопрос пользователя первым вызывай ask_1c(request=фраза целиком) — "
    "он сам выберет нужный инструмент по структуре фразы и вернёт результат. "
    "Если ask_1c вернул routed=false — выбери tool из suggested_tools.\n"
    "ПРИОРИТЕТ 0: фраза с .epf/.erf/.cfe → единственный первый tool "
    "inspect_external_processing(path=имя файла, request=фраза). "
    "ЗАПРЕТ: Search/Read/Glob по .epf; запрет «файла нет/прикрепите ещё». "
    "Cursor бинарник не видит, этот tool — видит (проект, Desktop, Downloads).\n\n"
    + (_AGENT_INSTRUCTIONS or "")
)


def _mcp_auth() -> Any:
    """Bearer-токен для HTTP-транспорта (RAG_MCP_TOKEN). stdio его не использует."""
    token = (os.environ.get("RAG_MCP_TOKEN") or "").strip()
    if not token:
        return None
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

    return StaticTokenVerifier(tokens={token: {"client_id": "rag_agent", "scopes": []}})


mcp = FastMCP(
    name="1C-Manager-Agent",
    instructions=_AGENT_INSTRUCTIONS,
    auth=_mcp_auth(),
)


_TRUE = {"true", "1", "yes", "да", "on"}


def _coerce_args(arguments: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Модели шлют «10» / «true» строками — привести к типам из JSON-схемы."""
    props = (schema or {}).get("properties") or {}
    out = dict(arguments or {})
    for k, v in out.items():
        t = (props.get(k) or {}).get("type")
        if not isinstance(v, str):
            continue
        s = v.strip()
        try:
            if t == "integer":
                out[k] = int(float(s)) if s else 0
            elif t == "number":
                out[k] = float(s) if s else 0.0
            elif t == "boolean":
                out[k] = s.lower() in _TRUE
        except ValueError:
            pass
    return out


class DispatchTool(Tool):
    """Tool из TOOL_DEFINITIONS → UniversalTools.dispatch (в потоке, не блокирует loop)."""

    async def run(self, arguments: dict[str, Any]):  # type: ignore[override]
        args = _coerce_args(arguments, self.parameters)
        result = await anyio.to_thread.run_sync(tools.dispatch, self.name, args)
        if not isinstance(result, (dict, list, str)):
            result = {"result": result}
        return self.convert_result(result)


for _t in tools.openai_tools():
    _f = _t["function"]
    mcp.add_tool(
        DispatchTool(
            name=_f["name"],
            description=_f.get("description") or "",
            parameters=_f.get("parameters") or {"type": "object", "properties": {}},
        )
    )


# ---------------------------------------------------------------- MCP-only extras

# категория роутера → короткий список tools для модели, если уверенного маршрута нет
_ROUTE_TOOLS: dict[str, list[str]] = {
    "build_epf": ["build_external_processing", "describe_object", "inspect_code", "code_help"],
    "code": ["inspect_code", "proc_index", "trace_calls", "explain_fill", "explain_command", "search_code"],
    "data": ["build_report", "query_by_attributes", "document_by_number"],
    "structure": ["describe_object", "explain_command", "explain_config", "resolve_object"],
    "meta": ["resolve_object", "list_objects", "describe_object", "explain_config"],
    "help": ["code_help", "knowledge_search"],
    "access": ["access_overview"],
}


@mcp.tool
async def ask_1c(request: str) -> dict:
    """
    ВЫЗЫВАЙ ПЕРВЫМ на любой вопрос (фраза пользователя целиком).
    Сам определяет тип вопроса (данные / код / структура / формы / доступ / справка / номер документа / .epf)
    и вызывает нужный инструмент. Изменение данных («удали / проведи / поменяй») — вежливый отказ (read-only).
    ВЕРНЁТ: {routed, tool, reason, result} или {routed:false, suggested_tools}.
    """
    from tools.router import classify, compute_hints

    d = classify(request or "") or {}
    if d.get("route") == "refuse_readonly":
        return {"routed": True, "tool": None, "answer": d.get("reply"), "reason": d.get("reason")}
    tool = d.get("tool")
    if tool and float(d.get("confidence") or 0) >= 0.78:
        args = dict(d.get("args") or {})
        result = await anyio.to_thread.run_sync(tools.dispatch, tool, args)
        from tools.tool_umbrella import HIDDEN_ALIAS

        return {
            "routed": True,
            "tool": HIDDEN_ALIAS.get(str(tool), tool),
            "args": args,
            "reason": d.get("reason"),
            "result": result,
            "prefer": "Ответь пользователю по result. Если result.need_clarification — задай вопрос с options.",
        }
    hints = {k: v for k, v in compute_hints(request or "").items() if v}
    route = str(d.get("route") or "")
    suggested = list(_ROUTE_TOOLS.get(route, []))
    if hints.get("code") and not suggested:
        suggested = _ROUTE_TOOLS["code"]
    if hints.get("report") and not suggested:
        suggested = _ROUTE_TOOLS["data"]
    return {
        "routed": False,
        "category": route or None,
        "hints": hints,
        "suggested_tools": suggested or ["resolve_object", "inspect_code", "build_report"],
        "prefer": "Выбери один tool из suggested_tools и передай request=фраза целиком. Если вопрос неясен — уточни у пользователя.",
    }



@mcp.tool
def knowledge_search(query: str, limit: int = 5, scope: str = "all") -> dict:
    """
    ГЛАВНЫЙ поиск по синтакс-помощнику и папке знаний.

    scope="docs" — только инструкции ИТС / стандарты разработки / регламенты: вызывай сам, когда нужно
    уточнить настройку, порядок действий, стандарт или поведение платформы (items[].doc — источник).

    КОГДА (всегда до ответа, фраза целиком): любой вопрос про устройство 1С —
    код, синтаксис, операторы, методы, свойства, типы, ВЫБРАТЬ/СУММА, «расскажи про…»,
    «как работает…», «что такое…», «как написать…». Не ограничиваться одной темой.

    НЕ для отчётов/цифр по базе → build_report.
    Ответ строить по items[].text (статьи справки_платформы). BSL — русский.
    """
    from kb.store import search_knowledge

    if (scope or "").lower() == "docs":
        from vector.search import search_docs

        items = search_docs(query, limit=max(1, min(int(limit or 5), 10)))
        return {"query": query, "scope": "docs", "count": len(items), "items": items,
                **({} if items else {"note": "В инструкциях ИТС ничего не найдено (или документация не проиндексирована)."})}
    return tools._maybe_compact(search_knowledge(query, limit=limit), tool_name="knowledge_search")


@mcp.tool
def reload_knowledge() -> dict:
    """
    Перечитать файлы из папки знаний после ваших правок
    (без полного перезапуска, если процесс тот же).
    """
    from kb.store import (
        knowledge_dir,
        list_knowledge_files,
        reload_knowledge,
    )

    reload_knowledge()
    files = list_knowledge_files()
    return {
        "ok": True,
        "папка_знаний": str(knowledge_dir()),
        "файлы": files.get("files"),
        "примечание": "Знания перечитаны.",
    }


@mcp.tool
def open_export(
    path: str = "",
    filename: str = "",
) -> dict:
    """
    КОГДА: пользователь не может скачать Excel / «открой файл».
    Открывает файл или папку exports в Проводнике (только файлы из exports/).
    path — полный путь из tool result; filename — имя из exports/.
    ВЕРНЁТ: {ok, opened, download_url?}.
    """
    from tools.excel_export import (
        DEFAULT_EXPORT_DIR,
        enrich_export_meta,
        open_in_explorer,
        start_export_http_server,
    )

    export_dir = DEFAULT_EXPORT_DIR.resolve()
    p = (path or "").strip()
    if not p and filename:
        p = str(export_dir / Path(filename).name)
    if not p:
        # открыть папку exports
        export_dir.mkdir(parents=True, exist_ok=True)
        opened = open_in_explorer(export_dir)
        base = start_export_http_server()
        return {
            "ok": bool(opened.get("ok")),
            "folder": str(export_dir),
            "download_base": base,
            "how_to_open": (
                f"Папка отчётов: {export_dir}. "
                f"Скачивание: {base}/имя_файла.xlsx"
            ),
            "prefer": "Покажи folder. Не выдумывай example.com.",
        }
    target = Path(p).resolve()
    if not target.exists() and filename:
        target = (export_dir / Path(filename).name).resolve()
    # только файлы из exports/ — иначе tool копирует в «Загрузки» что угодно (.env и т.п.)
    if not target.is_relative_to(export_dir) or not target.is_file():
        return {
            "error": f"Файл не найден в папке отчётов: {p}",
            "folder": str(export_dir),
            "hint": "Укажи path из build_report/export_* или filename из exports/",
        }
    meta = enrich_export_meta(
        target,
        copy_to_downloads=True,
        open_folder=True,
        ensure_http=True,
    )
    return {"ok": True, **meta}


@mcp.custom_route("/upload", methods=["POST"])
async def upload_external_file(request):  # type: ignore[no-untyped-def]
    """
    Приём .epf/.erf/.cfe из фильтра Open WebUI (scripts/owui_filter_external_files.py).
    Тело запроса — сырые байты, имя в X-Filename (urlencoded), токен в Authorization: Bearer.
    Токен: RAG_UPLOAD_TOKEN, иначе RAG_MCP_TOKEN.
    """
    import hmac
    import re as _re
    from urllib.parse import unquote

    from starlette.responses import JSONResponse

    token = (os.environ.get("RAG_UPLOAD_TOKEN") or os.environ.get("RAG_MCP_TOKEN") or "").strip()
    got = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not token or not hmac.compare_digest(got, token):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    name = Path(unquote(request.headers.get("x-filename", ""))).name
    name = _re.sub(r'[\\/:*?"<>|]', "_", name)
    if not _re.search(r"\.(epf|erf|cfe)$", name, _re.I):
        return JSONResponse({"ok": False, "error": "only .epf/.erf/.cfe"}, status_code=400)
    data = await request.body()
    if not data or len(data) > 100 * 1024 * 1024:
        return JSONResponse({"ok": False, "error": "empty or too large"}, status_code=400)
    # подпапка по хешу содержимого: одинаковые имена от разных пользователей не затирают друг друга
    import hashlib

    dest_dir = ROOT / "uploads" / hashlib.sha256(data).hexdigest()[:12]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    dest.write_bytes(data)
    return JSONResponse({"ok": True, "name": name, "path": str(dest.resolve()), "size": len(data)})


_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _warmup() -> None:
    """Прогрев в фоне: модель эмбеддингов и каталог объектов, чтобы первый tool-вызов не ждал холодного старта."""
    import time

    t0 = time.time()
    try:
        from tools.object_catalog import load_catalog

        load_catalog()
    except Exception as e:
        print(f"warmup catalog: {e}", file=sys.stderr)
    try:
        from vector.embed import Embedder

        Embedder(config).embed_query("прогрев")
    except Exception as e:
        print(f"warmup embed: {e}", file=sys.stderr)
    print(f"warmup done in {time.time() - t0:.1f}s", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="MCP-сервер RAG-агента 1С")
    ap.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    ap.add_argument("--host", default=os.environ.get("RAG_MCP_HOST") or "127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("RAG_MCP_PORT") or 8100))
    a = ap.parse_args(argv)

    # без баннера: иначе FastMCP ходит на PyPI (DNS на Windows может висеть минутами),
    # а вывод в stdout ломает handshake Cursor → «Connecting…».
    os.environ.setdefault("FASTMCP_SHOW_SERVER_BANNER", "false")
    os.environ.setdefault("FASTMCP_CHECK_FOR_UPDATES", "off")
    try:
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

    import threading

    threading.Thread(target=_warmup, name="warmup", daemon=True).start()

    if a.transport == "stdio":
        mcp.run(transport="stdio", show_banner=False)
        return 0

    no_auth_ok = (os.environ.get("RAG_MCP_ALLOW_NO_AUTH") or "").strip().lower() in _TRUE
    if a.host not in _LOOPBACK and mcp.auth is None and not no_auth_ok:
        print(
            f"ОТКАЗ: host={a.host} открывает MCP в сеть без авторизации. "
            "Задайте RAG_MCP_TOKEN в .env (панель → «Сгенерировать»), используйте 127.0.0.1 "
            "или осознанно RAG_MCP_ALLOW_NO_AUTH=1 (доступ ограничьте файрволом).",
            file=sys.stderr,
        )
        return 2
    auth_note = "Bearer-токен обязателен" if mcp.auth else "без токена"
    print(f"MCP HTTP: http://{a.host}:{a.port}/mcp ({auth_note})", file=sys.stderr)
    # без сессий: после перезапуска сервера клиенты со старым Mcp-Session-Id получали 404,
    # а Codex не переподключается сам. Ответ — обычный JSON, без SSE.
    mcp.run(transport="http", host=a.host, port=a.port, show_banner=False,
            stateless_http=True, json_response=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
