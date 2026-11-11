# -*- coding: utf-8 -*-
"""Проверки работоспособности. Запуск: python panel/checks.py <имя>  → JSON в последней строке stdout.

Выполняется отдельным процессом, чтобы каждый раз читать свежие config.yaml/.env.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

R = lambda ok, detail="", hint="": {"ok": bool(ok), "detail": detail, "hint": hint}  # noqa: E731


def _code_dir() -> Path | None:
    """Папка выгрузки, как её задал пользователь (config.yaml / RAG_CODE_DIR).
    load_config() подставляет knowledge/код_конфигурации по умолчанию — здесь это не годится."""
    import yaml
    from config import load_dotenv
    load_dotenv()
    raw = os.environ.get("RAG_CODE_DIR") or ""
    if not raw:
        try:
            cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
        except OSError:
            cfg = {}
        raw = str((cfg.get("paths") or {}).get("code_dir") or cfg.get("code_dir") or "")
    raw = raw.strip()
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_absolute() else ROOT / p


def deps():
    import importlib
    miss = []
    for m in ("fastmcp", "qdrant_client", "fastembed", "openai", "yaml", "openpyxl", "httpx"):
        try:
            importlib.import_module(m)
        except Exception:  # noqa: BLE001
            miss.append(m)
    v = sys.version.split()[0]
    return R(not miss, f"Python {v}" + (f"; не хватает: {', '.join(miss)}" if miss else "; все пакеты на месте"),
             "Кнопка «Установить зависимости»" if miss else "")


def config():
    from config import load_config
    cfg = load_config()
    miss = [k for k in ("http", "qdrant", "embedding", "llm", "agent") if not cfg.get(k)]
    return R(not miss, f"backend={cfg.get('backend')}, qdrant={cfg['qdrant'].get('mode')}, "
                       f"llm={cfg['llm'].get('provider')}/{cfg['llm'].get('model')}",
             f"нет секций: {miss}" if miss else "")


def dump():
    p = _code_dir()
    if p is None:
        return R(False, "папка выгрузки не задана", "«Первый запуск», шаг 1")
    if not p.is_dir():
        return R(False, f"нет папки {p}", "«Первый запуск», шаг 1 — укажите папку выгрузки")
    bsl = xml = 0
    for _d, _s, files in os.walk(p):
        for f in files:
            bsl += f.lower().endswith(".bsl")
            xml += f.lower().endswith(".xml")
        if bsl + xml > 300000:
            break
    cfg_xml = (p / "Configuration.xml").is_file()
    return R(bsl + xml > 0, f"{p}: .bsl={bsl}, .xml={xml}, Configuration.xml={'да' if cfg_xml else 'нет'}",
             "" if bsl + xml else "В папке нет файлов выгрузки")


def chunks():
    """Пробная нарезка: 1 реальный модуль → чанки (без записи на диск)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import prepare_config_for_qdrant as P
    from config import load_config
    cfg = load_config()
    src = _code_dir()
    if src is None:
        return R(False, "пропущено: папка выгрузки не задана", "«Первый запуск», шаг 1")
    mx = int((cfg.get("prepare") or {}).get("max_chunk_chars") or 1500)
    best: tuple[int, Path, list] | None = None
    for i, p in enumerate(P.iter_files(src, include_bsl=True, max_file_bytes=4_000_000)):
        if p.suffix.lower() != ".bsl":
            continue
        ch = P.process_file(p, src, mx)
        if best is None or len(ch) > best[0]:
            best = (len(ch), p, ch)
        if len(ch) >= 3 or i > 400:
            break
    if not best or not best[0]:
        return R(False, "не нашёл модуль .bsl для пробной нарезки", "Проверьте папку выгрузки")
    _n, p, ch = best
    return R(True, f"{p.name}: {len(ch)} чанков, размеры {[c.char_count for c in ch][:6]} (лимит {mx})")


def _qdrant_error(cfg: dict, st: dict) -> tuple[str, str]:
    """Понятный текст вместо сырого исключения клиента Qdrant."""
    q = cfg.get("qdrant") or {}
    raw = st.get("error") or st.get("hint") or "недоступен"
    if st.get("enabled") is False:
        return "Qdrant выключен в настройках", "Вкладка «Хранилище индекса» → «Qdrant включён»"
    if (q.get("mode") or "local") == "local":
        if "already accessed" in raw or "locked" in raw.lower():
            return ("Папка индекса занята другим процессом (MCP-сервер или индексация)",
                    "Встроенный режим не даёт работать двум процессам сразу: остановите MCP-сервер и повторите")
        return f"Встроенный Qdrant не открылся: {raw[:200]}", "Проверьте папку индекса во вкладке «Хранилище индекса»"
    url = q.get("url") or "http://localhost:6333"
    return (f"Qdrant не отвечает по адресу {url}",
            "Запустите Qdrant в Docker («Первый запуск», шаг 2) или выберите встроенный режим без Docker. "
            f"Техническая причина: {raw[:160]}")


def qdrant():
    from config import load_config
    from vector.client import collection_name, qdrant_status, reset_qdrant_cache
    cfg = load_config()
    reset_qdrant_cache()
    st = qdrant_status(cfg)
    if not st.get("ok"):
        return R(False, *_qdrant_error(cfg, st))
    cols = {c["name"]: c.get("points", 0) for c in st["collections"]}
    code = collection_name("code", cfg)
    detail = f"{st.get('mode')}: " + ", ".join(f"{n}={p}" for n, p in cols.items())
    if not cols.get(code):
        return R(False, detail, "Индекс кода пуст — «Первый запуск», шаг 3")
    return R(True, detail)


def embedding():
    from config import load_config
    from vector.embed import Embedder
    from vector.client import get_qdrant, collection_name
    cfg = load_config()
    t = time.time()
    e = Embedder(cfg)
    dim = len(e.embed_query("тест"))
    detail = f"{e.provider}/{e.model}: dim={dim}, {time.time() - t:.1f} с"
    try:
        info = get_qdrant(cfg).get_collection(collection_name("code", cfg))
        cd = info.config.params.vectors.size
        if cd != dim:
            return R(False, detail + f"; в коллекции dim={cd}", "Модель сменили — индексация с «Пересоздать коллекцию»")
    except Exception:  # noqa: BLE001
        pass
    return R(True, detail)


def vectors():
    """Векторы реально ненулевые и смысловой поиск даёт осмысленные оценки (лексика может маскировать поломку)."""
    import numpy as np
    from config import load_config
    from vector.client import collection_name, get_qdrant
    from vector.search import search_code_vectors
    cfg = load_config()
    col = collection_name("code", cfg)
    c = get_qdrant(cfg)
    pts, _ = c.scroll(col, limit=200, with_vectors=True, with_payload=False)
    if not pts:
        return R(False, f"коллекция {col} пуста", "Запустите индексацию")
    zero = sum(float(np.linalg.norm(p.vector)) < 1e-6 for p in pts)
    if zero:
        return R(False, f"{col}: нулевых векторов {zero} из {len(pts)} — смысловой поиск не работает",
                 "Индексация с «Пересоздать коллекцию» (или собрать новую коллекцию и переключить)")
    r = search_code_vectors("получить курс валюты на дату", limit=3, match_mode="semantic", cfg=cfg)
    top = max([m.get("score", 0) for m in r.get("matches") or []] or [0])
    return R(top >= 0.15, f"{col}: векторы ненулевые ({len(pts)}/{len(pts)}), лучший смысловой score={top:.2f}",
             "" if top >= 0.15 else "Оценки близки к нулю — проверьте, что модель эмбеддингов та же, что при индексации")


def search():
    from tools.code_search import search_code
    r = search_code("ТекстЗапроса", max_matches=3)
    ms = r.get("matches") or []
    if not ms:
        return R(False, f"source={r.get('source')}, совпадений нет", "Индекс пуст или не совпал с моделью — запустите индексацию")
    return R(r.get("source") == "qdrant", f"source={r.get('source')}, найдено {len(ms)}: {ms[0].get('location')}",
             "" if r.get("source") == "qdrant" else "Поиск идёт не через Qdrant")


def onec():
    import base64
    import urllib.request
    from config import load_config
    cfg = load_config()
    if cfg.get("backend") in ("none", "offline", "off"):
        return R(True, "1С отключена — режим «только Qdrant» (данные базы недоступны, код и метаданные из индекса)")
    if cfg.get("backend") != "http":
        return R(True, f"backend={cfg.get('backend')} — HTTP-проверка не нужна")
    h = cfg["http"]
    req = urllib.request.Request(h["base_url"].rstrip("/"))
    if h.get("auth") == "basic":
        req.add_header("Authorization", "Basic " + base64.b64encode(
            f'{h.get("username", "")}:{h.get("password", "")}'.encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return R(True, f"{h['base_url']} → HTTP {r.status}")
    except Exception as ex:  # noqa: BLE001
        code = getattr(ex, "code", None)
        if code in (401, 403):
            return R(False, f"{h['base_url']} → HTTP {code}", "Неверный логин/пароль 1С")
        if code:
            return R(True, f"{h['base_url']} отвечает (HTTP {code})")
        return R(False, f"{h['base_url']}: {ex}", "Проверьте публикацию hs/ai и адрес")


def llm():
    import urllib.request
    from config import load_config
    cfg = load_config()["llm"]
    base = (cfg.get("base_url") or "").rstrip("/")
    if not base:
        return R(False, "base_url не определён", "Выберите провайдера LLM")
    req = urllib.request.Request(base + "/models")
    if cfg.get("api_key"):
        req.add_header("Authorization", "Bearer " + cfg["api_key"])
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return R(True, f"{base} → HTTP {r.status}, модель {cfg.get('model')}")
    except Exception as ex:  # noqa: BLE001
        code = getattr(ex, "code", None)
        if code in (401, 403):
            return R(False, f"{base} → HTTP {code}", "Неверный или пустой API-ключ")
        return R(False, f"{base}: {ex}", "Сервис недоступен (LM Studio/Ollama запущены?)")


def _mcp_http_probe() -> str:
    """Живой HTTP-сервер: initialize на /mcp (с токеном из .env)."""
    import urllib.error
    import urllib.request

    port = os.environ.get("RAG_MCP_PORT") or "8100"
    url = f"http://127.0.0.1:{port}/mcp"
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "panel", "version": "1"}}}).encode()
    hdr = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    token = os.environ.get("RAG_MCP_TOKEN") or ""
    if token:
        hdr["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=hdr), timeout=5) as r:
            return f"HTTP {url} отвечает ({r.status})"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return f"HTTP {url}: 401 — токен в .env не совпадает с токеном запущенного сервера (перезапустите MCP)"
        return f"HTTP {url}: {e.code}"
    except OSError:
        return f"HTTP-сервер на :{port} не запущен (кнопка «Запустить MCP-сервер»)"


def mcp():
    import asyncio

    from config import load_dotenv

    load_dotenv()
    from main import mcp as app
    tools = asyncio.run(app.list_tools())
    n = len(tools)
    if not n:
        return R(False, "импорт ок, список tools получить не удалось")
    live = _mcp_http_probe()
    if "не запущен" in live:  # для Claude Desktop/Cursor (stdio) HTTP-сервер не нужен — не ошибка
        out = R(True, f"tools: {n}; {live}", "Нужен только для Open WebUI и других HTTP-клиентов")
        out["warn"] = True
        return out
    ok_live = "отвечает" in live
    return R(ok_live, f"tools: {n}; {live}", "" if ok_live else "Проверьте RAG_MCP_TOKEN и перезапустите MCP")


def overview():
    """Сводка для мастера переиндексации: Qdrant, папка выгрузки, модель."""
    from config import load_config
    from vector.client import collection_name, qdrant_status, reset_qdrant_cache
    cfg = load_config()
    reset_qdrant_cache()
    p = _code_dir()
    is_set = p is not None
    p = p or ROOT
    out = R(True)
    st = qdrant_status(cfg)
    if not st.get("ok"):
        st["error"], st["hint"] = _qdrant_error(cfg, st)
    out.update(qdrant=st,
               dump={"path": str(p) if is_set else "", "set": is_set,
                     "exists": is_set and p.is_dir(), "configuration_xml": is_set and (p / "Configuration.xml").is_file()},
               code_collection=collection_name("code", cfg), qdrant_mode=(cfg.get("qdrant") or {}).get("mode") or "local",
               model=(cfg.get("embedding") or {}).get("model") or "")
    return out


def drop():
    """Удалить коллекцию кода (и справки, если передано `help`) из Qdrant."""
    from config import load_config
    from vector.client import collection_name, get_qdrant, reset_qdrant_cache
    cfg = load_config()
    reset_qdrant_cache()
    client = get_qdrant(cfg)
    names = [collection_name("code", cfg)] + ([collection_name("help", cfg)] if "help" in sys.argv[2:] else [])
    existing = {c.name for c in client.get_collections().collections}
    gone = [n for n in names if n in existing]
    for n in gone:
        client.delete_collection(n)
    reset_qdrant_cache()
    return R(True, ("Удалено: " + ", ".join(gone)) if gone else "Удалять нечего: коллекций уже нет")


CHECKS = {f.__name__: f for f in (deps, config, dump, chunks, qdrant, embedding, vectors, search, onec, llm, mcp,
                                  overview, drop)}

if __name__ == "__main__":
    name = sys.argv[1]
    try:
        out = CHECKS[name]()
    except Exception as ex:  # noqa: BLE001
        out = R(False, f"{type(ex).__name__}: {ex}")
    if name in ("drop", "vectors"):  # запуск как этап задачи: понятная строка и код выхода
        print(("✔ " if out["ok"] else "✖ ") + out["detail"] + (f"\n→ {out['hint']}" if out.get("hint") else ""))
    print("\n@@RESULT@@" + json.dumps(out, ensure_ascii=False))
    if name in ("drop", "vectors") and not out["ok"]:
        sys.exit(1)
