# -*- coding: utf-8 -*-
"""
Панель управления RAG-агентом 1С (локальный веб-интерфейс, только stdlib).

  python panel/server.py            # http://127.0.0.1:8765 (Excel MCP раздаёт на 8766)
  python panel/server.py --port 9000 --no-browser

Правит config.yaml (комментарии сохраняются) и .env (секреты), запускает
индексацию выгрузки CF, проверяет Qdrant / 1С / MCP. Слушает только 127.0.0.1.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONFIG = ROOT / "config.yaml"
CONFIG_EXAMPLE = ROOT / "config.example.yaml"
ENV = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
PY = sys.executable
LOOPBACK = ("127.0.0.1", "localhost", "::1")
_TRUE = ("1", "true", "yes", "on")

# (ключ, подпись, тип, подсказка, варианты[, when])  тип: str|int|float|bool|select|secret|path
# ключ "a.b" → config.yaml; "ENV:NAME" → .env; when={"ключ": [значения]} — поле видно только при этих значениях
# group: main — основные настройки; adv — расширенные (новичку не нужны)
SECTIONS: list[dict] = [
    {"id": "onec", "group": "main", "title": "Подключение к 1С", "fields": [
        ("backend", "Режим работы с базой 1С", "select",
         "none — без базы: агент отвечает по коду и метаданным из индекса (достаточно для начала). "
         "http — ещё и данные базы через HTTP-сервис hs/ai (см. onec_http/). com — через COM-соединение (только Windows, медленнее)",
         ["none", "http", "com"]),
        ("http.base_url", "Адрес HTTP-сервиса 1С", "str", "Например http://localhost/ИмяБазы/hs/ai — публикация на веб-сервере", None, {"backend": ["http"]}),
        ("http.auth", "Авторизация", "select", "basic — логин и пароль пользователя 1С; none — публикация без авторизации", ["basic", "none"], {"backend": ["http"]}),
        ("http.username", "Пользователь 1С", "str", "пользователь информационной базы с правом на чтение", None, {"backend": ["http"], "http.auth": ["basic"]}),
        ("ENV:RAG_HTTP_PASSWORD", "Пароль 1С", "secret", "хранится в .env, в config.yaml не попадает", None, {"backend": ["http"], "http.auth": ["basic"]}),
        ("http.timeout", "Таймаут запроса, сек", "float", "по умолчанию 60; увеличьте для тяжёлых отчётов", None, {"backend": ["http"]}),
        ("com.connection_string", "Строка соединения COM", "str", 'Например File="D:\\Bases\\Base";Usr="Имя";Pwd="..."', None, {"backend": ["com"]}),
    ]},
    {"id": "qdrant", "group": "main", "title": "Хранилище индекса (Qdrant)", "fields": [
        ("qdrant.mode", "Где хранить индекс", "select",
         "url — Qdrant в Docker или на сервере (быстро, рекомендуется). local — встроенная папка без Docker "
         "(проще, но медленнее на крупных конфигурациях). После смены режима нужна индексация заново", ["url", "local"]),
        ("qdrant.url", "Адрес Qdrant", "str", "Docker на этом ПК: http://localhost:6333", None, {"qdrant.mode": ["url"]}),
        ("qdrant.path", "Папка индекса", "path", "относительно папки проекта; по умолчанию knowledge/qdrant_data", None, {"qdrant.mode": ["local"]}),
        ("ENV:QDRANT_API_KEY", "API-ключ Qdrant", "secret", "только для Qdrant Cloud или сервера с ключом; для Docker на этом ПК не нужен", None, {"qdrant.mode": ["url"]}),
        ("qdrant.search_mode", "Режим поиска", "select",
         "hybrid — по смыслу и по словам вместе (рекомендуется); semantic — только по смыслу; exact — только точное совпадение слов",
         ["hybrid", "semantic", "exact"]),
        ("qdrant.collection_code", "Коллекция кода", "str", "имя набора в Qdrant; меняйте, если держите несколько конфигураций", None),
        ("qdrant.collection_help", "Коллекция справки платформы", "str", "общая для всех конфигураций", None),
        ("qdrant.enabled", "Qdrant включён", "bool", "без Qdrant агент не видит код — не выключайте", None),
        ("qdrant.fallback_filesystem", "Читать выгрузку с диска, если индекс пуст", "bool", "аварийный режим, медленно; обычно выключено", None),
    ]},
    {"id": "mcp", "group": "main", "title": "MCP-сервер", "fields": [
        ("ENV:RAG_MCP_HOST", "Кому доступен сервер", "select",
         "127.0.0.1 — только этот ПК (Open WebUI в Docker Desktop тоже сюда, через host.docker.internal). "
         "0.0.0.0 — все компьютеры сети: обязательно задайте токен", ["127.0.0.1", "0.0.0.0"]),
        ("ENV:RAG_MCP_PORT", "Порт", "str", "по умолчанию 8100", None),
        ("ENV:RAG_MCP_TOKEN", "Токен доступа (Bearer)", "secret",
         "проще создать кнопкой «Сгенерировать токен» в «Первый запуск», шаг 4. Пустое поле — оставить текущий; "
         "чтобы отключить токен, удалите строку RAG_MCP_TOKEN из .env", None),
        ("ENV:RAG_UPLOAD_TOKEN", "Токен загрузки .epf из Open WebUI", "secret", "пусто — используется токен MCP", None),
        ("ENV:RAG_EXPORT_HTTP_PORT", "Порт раздачи Excel-файлов", "str", "по умолчанию 8766", None),
    ]},
    {"id": "ingest", "group": "adv", "title": "Выгрузка и нарезка", "fields": [
        ("paths.code_dir", "Папка выгрузки конфигурации", "path", "Конфигуратор → Конфигурация → Выгрузить конфигурацию в файлы", None),
        ("prepare.include_bsl", "Брать модули (.bsl)", "bool", "код процедур и функций; выключать незачем", None),
        ("prepare.xml_cards", "Карточки структуры объектов", "bool", "реквизиты, табличные части, типы, синонимы — компактно вместо сырого XML (рекомендуется)", None),
        ("prepare.ui_cards", "Карточки форм и макетов", "bool", "команды, обработчики, элементы форм; области, параметры и поля макетов/СКД", None),
        ("prepare.clean", "Авто-чистка кода", "bool", "убирает разделители, base64-блобы, пустые оболочки; отчёт: exports/prepare_report.json", None),
        ("prepare.card_chars", "Размер карточки, символов", "int", "мелкие карточки точнее находятся; по умолчанию 800", None),
        ("prepare.dedupe", "Убирать повторяющиеся фрагменты", "bool", "в основном одинаковые условия ограничения доступа ролей; экономит до половины индекса", None),
        ("paths.chunks_jsonl", "Файл нарезанных фрагментов (JSONL)", "path", "промежуточный файл между нарезкой и индексацией", None),
    ]},
    {"id": "models", "group": "adv", "title": "Модель поиска", "custom": True, "fields": [
        ("embedding.provider", "Провайдер", "str", "", None),
        ("embedding.model", "Модель", "str", "", None),
        ("prepare.max_chunk_chars", "Размер чанка, симв.", "int", "", None),
        ("prepare.chunk_overlap", "Перекрытие чанков", "int", "", None),
        ("embedding.passage_max_chars", "Макс. символов в эмбеддинг", "int", "", None),
        ("prepare.max_file_mb", "Макс. размер файла, МБ", "float", "", None),
    ]},
    {"id": "embedding", "group": "adv", "title": "Расчёт векторов", "fields": [
        ("embedding.batch_size", "Фрагментов за один проход", "int", "больше — быстрее, но больше памяти; по умолчанию 64", None),
        ("embedding.threads", "Потоки процессора", "int", "0 — все ядра", None),
        ("embedding.base_url", "Адрес API эмбеддингов", "str", "только если выбрана модель OpenAI (API) во вкладке «Модель поиска»", None, {"embedding.provider": ["openai"]}),
        ("ENV:RAG_EMBEDDING_API_KEY", "API-ключ эмбеддингов", "secret", "только для модели OpenAI (API)", None, {"embedding.provider": ["openai"]}),
    ]},
    {"id": "llm", "group": "adv", "title": "LLM для CLI", "fields": [
        ("llm.provider", "Провайдер", "select",
         "lmstudio и ollama — локально, ключ не нужен; остальные — облачные API, нужен ключ",
         ["lmstudio", "ollama", "openai", "openrouter", "gemini", "xai", "aigate"]),
        ("llm.model", "Модель", "str", "идентификатор модели у провайдера, например qwen2.5-14b-instruct (LM Studio) или openai/gpt-4o-mini (OpenRouter)", None),
        ("llm.base_url", "Адрес API", "str", "пусто — стандартный адрес провайдера (LM Studio: localhost:1234, Ollama: localhost:11434)", None),
        ("ENV:OPENROUTER_API_KEY", "Ключ OpenRouter", "secret", "", None, {"llm.provider": ["openrouter"]}),
        ("ENV:OPENAI_API_KEY", "Ключ OpenAI", "secret", "", None, {"llm.provider": ["openai"]}),
        ("ENV:GEMINI_API_KEY", "Ключ Gemini", "secret", "", None, {"llm.provider": ["gemini"]}),
        ("ENV:XAI_API_KEY", "Ключ xAI", "secret", "", None, {"llm.provider": ["xai"]}),
        ("ENV:AIGATE_API_KEY", "Ключ AIGate", "secret", "", None, {"llm.provider": ["aigate"]}),
        ("ENV:RAG_LLM_API_KEY", "Общий ключ LLM", "secret", "если задан — используется вместо ключа провайдера", None,
         {"llm.provider": ["openai", "openrouter", "gemini", "xai", "aigate"]}),
        ("llm.temperature", "Температура", "float", "0–0.2 — точные, повторяемые ответы (рекомендуется); выше — свободнее", None),
        ("llm.context_window", "Окно контекста модели, токены", "int", "сколько модель помнит; ставьте как у модели (часто 32000–128000)", None),
        ("llm.max_tokens", "Макс. длина ответа, токены", "int", "по умолчанию 8192", None),
        ("llm.max_tool_rounds", "Макс. обращений к инструментам за вопрос", "int", "защита от зацикливания; по умолчанию 10", None),
        ("llm.tool_result_max_chars", "Макс. размер результата инструмента, симв.", "int", "больше — подробнее, но быстрее заканчивается контекст", None),
    ]},
    {"id": "agent", "group": "adv", "title": "Агент", "fields": [
        ("agent.audience", "Для кого отвечает агент", "select", "developer — программист (код, пути, обработчики); user — пользователь (простым языком, без кода); both — по вопросу", ["both", "developer", "user"]),
        ("agent.read_only", "Только чтение", "bool", "агент не записывает в базу 1С и не меняет конфигурацию", None),
        ("agent.query_default_limit", "Строк в отчёте по умолчанию", "int", "если в вопросе не сказано, сколько строк нужно", None),
        ("agent.query_max_limit", "Макс. строк в отчёте", "int", "потолок, даже если попросили больше", None),
        ("agent.list_objects_cap", "Макс. объектов в списке метаданных", "int", "по умолчанию 200", None),
    ]},
]

ALLOWED = {f[0] for s in SECTIONS for f in s["fields"]}
KINDS = {f[0]: f[2] for s in SECTIONS for f in s["fields"]}


# ---------------------------------------------------------------- config.yaml
def _fmt(kind: str, v) -> str:
    if kind == "bool":
        return "true" if v in (True, "true", "True", 1, "1", "on") else "false"
    if kind in ("int", "float"):
        try:
            return str(int(float(v))) if kind == "int" else str(float(v))
        except (TypeError, ValueError):
            raise ValueError(f"нужно число: {v!r}")
    s = str(v if v is not None else "")
    if "\n" in s or "\r" in s:
        raise ValueError("многострочные значения не поддерживаются")
    return '"' + s.replace("\\", "/" if kind == "path" else "\\\\").replace('"', '\\"') + '"'


def patch_yaml(text: str, dotted: str, literal: str) -> str:
    """Заменить скаляр section.key (или top-level key), не трогая комментарии."""
    parts = dotted.split(".")
    lines = text.split("\n")
    i0, i1, indent = 0, len(lines), ""
    if len(parts) == 2:
        sec = re.compile(rf"^{re.escape(parts[0])}:\s*(#.*)?$")
        start = next((i for i, l in enumerate(lines) if sec.match(l)), None)
        if start is None:
            raise KeyError(f"нет секции {parts[0]} в config.yaml")
        i0 = start + 1
        i1 = next((i for i in range(i0, len(lines)) if lines[i] and not lines[i][0] in " #\t"), len(lines))
        indent = r"[ \t]+"
    key = parts[-1]
    pat = re.compile(rf'^({indent}{re.escape(key)}:[ \t]*)("(?:[^"\\]|\\.)*"|\'[^\']*\'|[^#\n]*?)([ \t]*#.*)?$')
    for i in range(i0, i1):
        m = pat.match(lines[i])
        if m:
            lines[i] = f"{m.group(1)}{literal}{m.group(3) or ''}"
            return "\n".join(lines)
    if len(parts) == 2:  # ключа нет — добавляем в конец секции
        lines.insert(i1, f"  {key}: {literal}")
        return "\n".join(lines)
    raise KeyError(f"нет ключа {dotted}")


def read_config() -> dict:
    import yaml
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}


def read_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if ENV.is_file():
        for ln in ENV.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, _, v = ln.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def patch_env(text: str, name: str, value: str) -> str:
    """Заменить активную строку NAME=; если её нет — раскомментировать; иначе дописать."""
    new = f"{name}={value}"
    for pat in (rf"^\s*{re.escape(name)}=.*$", rf"^#\s*{re.escape(name)}=.*$"):
        rx = re.compile(pat, re.M)
        if rx.search(text):
            return rx.sub(lambda _m: new, text, count=1)
    return text.rstrip("\n") + f"\n{new}\n"


def ensure_files() -> list[str]:
    """Первый запуск после клонирования: config.yaml и .env из шаблонов."""
    made = []
    for dst, src in ((CONFIG, CONFIG_EXAMPLE), (ENV, ENV_EXAMPLE)):
        if not dst.is_file() and src.is_file():
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            made.append(dst.name)
    return made


_DOCKER: dict = {"t": 0.0, "v": ""}


def docker_state() -> str:
    """missing — Docker не установлен; stopped — установлен, но не запущен; running."""
    import shutil
    if time.time() - _DOCKER["t"] < 15 and _DOCKER["v"]:
        return _DOCKER["v"]
    if not shutil.which("docker"):
        v = "missing"
    else:
        try:
            p = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=10)
            v = "running" if p.returncode == 0 else "stopped"
        except (OSError, subprocess.TimeoutExpired):
            v = "stopped"
    _DOCKER.update(t=time.time(), v=v)
    return v


_OVERVIEW_LOCK = threading.Lock()  # встроенный Qdrant нельзя открыть двумя процессами сразу


def overview() -> dict:
    with _OVERVIEW_LOCK:
        p = subprocess.run([PY, str(Path(__file__).parent / "checks.py"), "overview"], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
                           env=dict(os.environ, **read_env(), PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
    for line in reversed(p.stdout.splitlines()):
        if line.startswith("@@RESULT@@"):
            return json.loads(line[10:])
    return {"ok": False, "detail": (p.stderr or p.stdout or "нет ответа")[-400:]}


def setup_status() -> dict:
    """Сводка для мастера «Первый запуск»: выгрузка, Qdrant, индекс, MCP."""
    out = overview()
    env = read_env()
    out["docker"] = docker_state()
    out["mcp"] = {"running": MCP.running(), "host": mcp_host(), "port": mcp_port(),
                  "token": bool(env.get("RAG_MCP_TOKEN"))}
    return out


def get_state() -> dict:
    cfg, env = read_config(), read_env()
    values: dict = {}
    for s in SECTIONS:
        for key, _l, kind, *_ in s["fields"]:
            if key.startswith("ENV:"):
                values[key] = "" if kind == "secret" else env.get(key[4:], "")
                values[key + ":set"] = bool(env.get(key[4:]))
                continue
            cur = cfg
            for p in key.split("."):
                cur = cur.get(p) if isinstance(cur, dict) else None
            values[key] = cur if cur is not None else ""
    return {"sections": SECTIONS, "values": values}


def save_values(values: dict) -> list[str]:
    ctext = CONFIG.read_text(encoding="utf-8")
    etext = ENV.read_text(encoding="utf-8") if ENV.is_file() else ""
    changed: list[str] = []
    for key, v in values.items():
        if key not in ALLOWED:
            continue
        kind = KINDS[key]
        if key.startswith("ENV:"):
            if kind == "secret" and v in ("", None):
                continue  # пустое поле секрета = «не менять»
            etext = patch_env(etext, key[4:], str(v))
        else:
            ctext = patch_yaml(ctext, key, _fmt(kind, v))
        changed.append(key)
    if changed:
        import yaml
        yaml.safe_load(ctext)  # не пишем сломанный YAML
        bak = CONFIG.with_suffix(".yaml.bak")
        bak.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        CONFIG.write_text(ctext, encoding="utf-8")
        if etext:
            ENV.write_text(etext, encoding="utf-8")
    return changed


# ---------------------------------------------------------------- проверки
CHECK_NAMES = ["deps", "config", "dump", "chunks", "qdrant", "embedding", "vectors", "search", "onec", "llm", "mcp"]


def run_check(name: str) -> dict:
    """Проверка в отдельном процессе — всегда свежие config.yaml/.env."""
    if name not in CHECK_NAMES:
        return {"ok": False, "detail": "неизвестная проверка"}
    limit = 900 if name == "embedding" else 300  # первый раз модель скачивается (до 2 ГБ)
    try:
        p = subprocess.run([PY, str(Path(__file__).parent / "checks.py"), name], cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=limit,
                           env=dict(os.environ, **read_env(), PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": f"не уложилась в {limit} с",
                "hint": "Повторите проверку: при первом запуске могла скачиваться модель" if name == "embedding" else ""}
    for line in reversed(p.stdout.splitlines()):
        if line.startswith("@@RESULT@@"):
            return json.loads(line[10:])
    return {"ok": False, "detail": (p.stderr or p.stdout or "нет ответа")[-400:], "hint": ""}


def mcp_snippet() -> dict:
    env = read_env()
    port = mcp_port()
    host = mcp_host()
    return {
        "stdio": {"mcpServers": {"rag_agent": {"command": PY, "args": [str(ROOT / "main.py")]}}},
        "docker_url": f"http://host.docker.internal:{port}/mcp",
        "local_url": f"http://127.0.0.1:{port}/mcp",
        "lan": host not in LOOPBACK,
        "token": env.get("RAG_MCP_TOKEN") or "",
    }


def gen_token() -> str:
    import secrets

    token = secrets.token_urlsafe(32)
    etext = ENV.read_text(encoding="utf-8") if ENV.is_file() else ""
    ENV.write_text(patch_env(etext, "RAG_MCP_TOKEN", token), encoding="utf-8")
    return token


# ---------------------------------------------------------------- задачи
class Job:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.log: list[str] = []
        self.name = ""
        self.stages: list[str] = []
        self.step = 0
        self.started = 0.0
        self.active = False  # вся цепочка команд, а не только текущий процесс (между этапами proc уже завершён)
        self.progress: dict | None = None  # {done,total,label,t0} текущего этапа из строк «@@PROGRESS»
        self.errors: list[str] = []  # строки с ошибками — панель показывает их сразу, не дожидаясь конца
        self.cancel = False  # «Остановить» между этапами цепочки: следующий этап не запускается
        self.lock = threading.Lock()

    _ERR = re.compile(r"Traceback \(most recent|ОШИБКА|Ошибка|\bError\b|Exception|CRITICAL|не найден", re.I)

    def _line(self, line: str) -> None:
        if line.startswith("@@PROGRESS "):
            try:
                _, done, total, *label = line.split(" ", 3)
                p = self.progress
                if not p or p.get("step") != self.step:
                    p = self.progress = {"step": self.step, "t0": time.time()}
                p.update(done=int(done), total=int(total), label=" ".join(label), t=time.time())
            except ValueError:
                pass
            return
        self.log.append(line)
        del self.log[:-2000]
        if self._ERR.search(line) and len(self.errors) < 50:
            self.errors.append(line)

    def running(self) -> bool:
        return self.active

    def start(self, name: str, cmds: list[list[str]], stages: list[str] | None = None) -> None:
        with self.lock:
            if self.running():
                raise RuntimeError("уже выполняется задача: " + self.name)
            self.name, self.log, self.started = name, [], time.time()
            self.stages = stages or []  # подписи этапов (по одному на команду) для мастера
            self.step = 0
            self.progress, self.errors = None, []
            self.proc, self.cancel = None, False
            self.active = True
            threading.Thread(target=self._run, args=(cmds,), daemon=True).start()

    def _run(self, cmds: list[list[str]]) -> None:
        try:
            # свежий .env поверх окружения панели: дочерний load_dotenv не перезаписывает
            # ключи, уже попавшие в os.environ, — иначе процесс видит старые значения
            env = dict(os.environ, **read_env(), PYTHONUTF8="1", PYTHONIOENCODING="utf-8", RAG_PROGRESS="1")
            for cmd in cmds:
                if self.cancel:
                    self.log.append("Остановлено пользователем")
                    break
                self.step += 1
                self.log.append("▶ " + " ".join(cmd))
                self.proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
                for line in self.proc.stdout:  # type: ignore[union-attr]
                    self._line(line.rstrip())
                rc = self.proc.wait()
                self.log.append(f"◀ код выхода {rc}")
                if rc != 0 or self.cancel:
                    self.errors.append("Остановлено пользователем" if self.cancel
                                       else f"Этап {self.step} завершился с кодом {rc}")
                    break
            ok = not self.cancel and self.proc is not None and self.proc.returncode == 0
            self.log.append("=== ГОТОВО ===" if ok else "=== ОШИБКА ===")
        except Exception as exc:  # noqa: BLE001 — например, команда не найдена
            self.log.append(f"=== ОШИБКА === {exc}")
            self.errors.append(f"Не удалось запустить этап: {exc}")
        finally:
            self.active = False

    def stop(self, wait: float = 0) -> None:
        if not self.running():
            return
        self.cancel = True
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            if wait:
                try:
                    self.proc.wait(wait)  # type: ignore[union-attr]
                except subprocess.TimeoutExpired:
                    self.proc.kill()  # type: ignore[union-attr]


JOB = Job()
def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":  # os.kill(pid, 0) на Windows убивает процесс — спрашиваем через WinAPI
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class McpServer:
    """MCP по HTTP как самостоятельный процесс: переживает перезапуск панели.
    pid и лог лежат в exports/, панель при старте подхватывает уже работающий сервер."""

    def __init__(self) -> None:
        d = ROOT / "exports"
        self.pidfile, self.logfile = d / "mcp.pid", d / "mcp.log"

    def _pid(self) -> int:
        try:
            return int(self.pidfile.read_text().strip())
        except (OSError, ValueError):
            return 0

    def _port_open(self) -> bool:
        import socket
        try:
            with socket.create_connection(("127.0.0.1", int(mcp_port())), timeout=0.5):
                return True
        except (OSError, ValueError):
            return False

    def running(self) -> bool:
        # процесс жив (в т.ч. ещё грузит модель) или порт уже слушает (сервер запущен вручную)
        return _pid_alive(self._pid()) or self._port_open()

    @property
    def log(self) -> list[str]:
        try:
            return self.logfile.read_text(encoding="utf-8", errors="replace").splitlines()[-60:]
        except OSError:
            return []

    def start(self) -> None:
        if self.running():
            raise RuntimeError("MCP уже запущен")
        self.pidfile.parent.mkdir(exist_ok=True)
        env = dict(os.environ, **read_env(), PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        cmd = [PY, str(ROOT / "main.py"), "--transport", "http", "--host", mcp_host(), "--port", mcp_port()]
        flags = [{}]
        if os.name == "nt":
            # NO_WINDOW без DETACHED: лаунчер venv порождает настоящий python, и у лишённого консоли
            # родителя тот открывал бы себе новое видимое окно; скрытая консоль наследуется
            base = 0x00000200 | 0x08000000  # NEW_PROCESS_GROUP | NO_WINDOW
            # BREAKAWAY: иначе сервер остаётся в job-объекте терминала/приложения, из которого запущена панель,
            # и умирает, когда тот закрывается; если job не разрешает выход — запускаем без него
            flags = [{"creationflags": base | 0x01000000}, {"creationflags": base}]
        else:
            flags = [{"start_new_session": True}]
        for i, kw in enumerate(flags):
            try:
                with open(self.logfile, "wb") as lf:
                    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=lf,
                                            stderr=subprocess.STDOUT, **kw)
                break
            except OSError:
                if i == len(flags) - 1:
                    raise
        self.pidfile.write_text(str(proc.pid))

    def _listener_pid(self) -> int:
        """Кто слушает порт (сервер, запущенный вручную, без pid-файла). Только Windows."""
        if os.name != "nt":
            return 0
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
        for ln in out.splitlines():
            p = ln.split()
            if len(p) >= 5 and p[3] == "LISTENING" and p[1].endswith(":" + mcp_port()):
                return int(p[4])
        return 0

    def stop(self, wait: float = 0) -> None:
        pid = self._pid()
        if not _pid_alive(pid):
            pid = self._listener_pid()
        if _pid_alive(pid):
            if os.name == "nt":  # /T — вместе с дочерним python за лаунчером venv
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
            else:
                import signal
                try:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                except OSError:
                    pass
        try:
            self.pidfile.unlink()
        except OSError:
            pass
        end = time.time() + wait
        while wait and time.time() < end and self.running():
            time.sleep(0.2)


MCP = McpServer()  # долгоживущий MCP-сервер (HTTP), независим от задач индексации и от жизни панели


def mcp_port() -> str:
    return read_env().get("RAG_MCP_PORT") or "8100"


def mcp_host() -> str:
    return read_env().get("RAG_MCP_HOST") or "127.0.0.1"


def start_mcp() -> None:
    env = read_env()
    no_auth_ok = (env.get("RAG_MCP_ALLOW_NO_AUTH") or "").strip().lower() in _TRUE
    if mcp_host() not in LOOPBACK and not env.get("RAG_MCP_TOKEN") and not no_auth_ok:
        raise ValueError("Сервер открыт для всей сети (0.0.0.0), а токен не задан — без токена он не запустится. "
                         "Создайте токен («Первый запуск», шаг 4) или выберите 127.0.0.1 во вкладке «MCP-сервер».")
    MCP.start()


def restart_mcp_if_running() -> bool:
    """После сохранения настроек: MCP читает config/.env только при старте."""
    if not MCP.running():
        return False
    MCP.stop(wait=10)
    start_mcp()
    return True


def start_pip() -> None:
    JOB.start("pip", [[PY, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")]])


def start_ingest(o: dict) -> None:
    prep = [PY, str(ROOT / "scripts" / "prepare_config_for_qdrant.py"), "--quiet"]
    idx = [PY, str(ROOT / "scripts" / "index_qdrant.py")]
    if o.get("recreate"):
        idx.append("--recreate")
    elif o.get("full"):
        idx.append("--full")
    if o.get("code_only", True):
        idx.append("--code-only")
    cmds = ([] if o.get("skip_prepare") else [prep]) + [idx]
    JOB.start("ingest", cmds)


def start_reindex(o: dict) -> None:
    """Мастер: replace — нарезать новую выгрузку, затем пересоздать индекс (старый удаляется только после
    успешной нарезки); update — только изменённое; clear — только удалить индекс."""
    mode, with_help = o.get("mode"), bool(o.get("with_help"))
    if mode not in ("replace", "update", "clear"):
        raise ValueError("выберите, что сделать")
    checks = str(Path(__file__).parent / "checks.py")
    if JOB.running():
        raise RuntimeError("уже выполняется задача: " + JOB.name)
    # сначала все проверки, потом побочные эффекты (сохранение папки, остановка MCP)
    src = str(o.get("source") or "").strip()
    if mode != "clear":
        raw = str(src or (read_config().get("paths") or {}).get("code_dir") or "").strip()
        if not raw:
            raise ValueError("Не указана папка выгрузки конфигурации (шаг 1)")
        p = Path(raw)
        if not (p if p.is_absolute() else ROOT / p).is_dir():
            raise ValueError(f"Папка выгрузки не найдена: {p}")
        if src:
            save_values({"paths.code_dir": src})
    if (read_config().get("qdrant") or {}).get("mode", "local") == "local" and MCP.running():
        MCP.stop(wait=10)  # встроенный Qdrant открывается только одним процессом — иначе индексация упадёт
    if mode == "clear":
        return JOB.start("reindex", [[PY, checks, "drop"] + (["help"] if with_help else [])], ["Удаление индекса"])
    prep = [PY, str(ROOT / "scripts" / "prepare_config_for_qdrant.py"), "--quiet"]
    idx = [PY, str(ROOT / "scripts" / "index_qdrant.py")] + (["--recreate"] if mode == "replace" else [])
    if not with_help:
        idx.append("--code-only")
    JOB.start("reindex", [prep, idx, [PY, checks, "vectors"]], ["Нарезка выгрузки", "Индексация", "Проверка векторов"])


def start_tune(o: dict) -> None:
    cmd = [PY, str(ROOT / "scripts" / "tune_embedding.py"), "--models", str(o.get("models") or "minilm,mpnet"),
           "--chunk-sizes", str(o.get("sizes") or "800,1500,2800"), "--budget-hours", str(float(o.get("budget") or 8))]
    if o.get("source"):
        cmd += ["--source", str(o["source"])]
    JOB.start("tune", [cmd])


def apply_tune() -> list[str]:
    f = ROOT / "exports" / "tune_embedding.json"
    if not f.is_file():
        raise ValueError("Рекомендации ещё нет — сначала нажмите «Запустить подбор» и дождитесь окончания")
    rec = json.loads(f.read_text(encoding="utf-8")).get("recommended") or {}
    return save_values(rec)


def start_probe(name: str) -> None:
    from vector.models import BY_NAME
    if name not in BY_NAME or BY_NAME[name]["provider"] != "fastembed":
        raise ValueError("проверка окна доступна только для моделей fastembed из каталога")
    JOB.start("probe", [[PY, str(ROOT / "vector" / "models.py"), "probe", name]])


def start_eval(o: dict) -> None:
    cmd = [PY, str(ROOT / "scripts" / "eval_search.py"), "--n", str(int(o.get("n") or 25)), "--modes", "hybrid,semantic"]
    if o.get("classes"):
        cmd += ["--classes", str(o["classes"])]
    JOB.start("eval", [cmd])


def start_repair() -> None:
    JOB.start("repair", [[PY, str(ROOT / "scripts" / "index_qdrant.py"), "--repair-vectors"]])


def start_onboard(o: dict) -> None:
    if not o.get("source") or not o.get("name"):
        raise ValueError("укажите папку выгрузки и короткое имя")
    cmd = [PY, str(ROOT / "scripts" / "onboard_config.py"), "--source", str(o["source"]), "--name", str(o["name"])]
    if o.get("activate"):
        cmd.append("--activate")
    if o.get("limit"):
        cmd += ["--limit", str(int(o["limit"]))]
    JOB.start("onboard", [cmd])


def start_docker() -> None:
    JOB.start("docker", [["docker", "compose", "up", "-d", "qdrant"]])


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "RagPanel/1"

    def log_message(self, *a) -> None:  # тихо
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"))

    def _host_ok(self) -> bool:
        return (self.headers.get("Host") or "").split(":")[0] in ("127.0.0.1", "localhost")

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._send(403, b"forbidden")
        path = self.path.split("?")[0]
        if path.startswith("/api/") and self.headers.get("X-Panel") != "1":
            return self._send(403, b"forbidden")
        try:
            if path == "/":
                return self._send(200, (Path(__file__).parent / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/api/state":
                return self._json(get_state())
            if path == "/api/log":
                return self._json({"running": JOB.running(), "name": JOB.name, "log": JOB.log[-400:],
                                   "stages": JOB.stages, "step": JOB.step,
                                   "progress": JOB.progress, "errors": JOB.errors[-10:], "started": JOB.started,
                                   "mcp": {"running": MCP.running(), "host": mcp_host(), "port": mcp_port(),
                                           "log": MCP.log[-60:]}})
            if path == "/api/models":
                from vector.models import catalog
                return self._json({"models": catalog()})
            if path == "/api/setup":
                return self._json(setup_status())
            if path.startswith("/api/check/"):
                return self._json(run_check(path.rsplit("/", 1)[-1]))
            if path == "/api/mcp":
                return self._json(mcp_snippet())
            if path == "/api/reindex/status":
                return self._json(overview())
        except Exception as e:  # noqa: BLE001
            return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
        self._send(404, b"not found")

    def do_POST(self) -> None:  # noqa: N802
        # защита от CSRF: кастомный заголовок нельзя выставить кросс-доменно без preflight
        if not self._host_ok() or self.headers.get("X-Panel") != "1":
            return self._send(403, b"forbidden")
        n = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/save":
                changed = save_values(data.get("values") or {})
                try:
                    restarted, mcp_error = (restart_mcp_if_running() if changed else False), ""
                except Exception as e:  # noqa: BLE001 — настройки уже сохранены, сообщаем причину отдельно
                    restarted, mcp_error = False, str(e)
                return self._json({"ok": True, "changed": changed, "mcp_restarted": restarted, "mcp_error": mcp_error})
            if self.path == "/api/token/gen":
                token = gen_token()
                try:
                    restarted, mcp_error = restart_mcp_if_running(), ""
                except Exception as e:  # noqa: BLE001 — токен уже записан
                    restarted, mcp_error = False, str(e)
                return self._json({"ok": True, "token": token, "mcp_restarted": restarted, "mcp_error": mcp_error})
            if self.path == "/api/ingest":
                start_ingest(data)
                return self._json({"ok": True})
            if self.path == "/api/reindex":
                start_reindex(data)
                return self._json({"ok": True})
            if self.path == "/api/docker":
                start_docker()
                return self._json({"ok": True})
            if self.path == "/api/model/probe":
                start_probe(str(data.get("name") or ""))
                return self._json({"ok": True})
            if self.path == "/api/tune":
                start_tune(data)
                return self._json({"ok": True})
            if self.path == "/api/tune/apply":
                return self._json({"ok": True, "changed": apply_tune()})
            if self.path == "/api/onboard":
                start_onboard(data)
                return self._json({"ok": True})
            if self.path == "/api/eval":
                start_eval(data)
                return self._json({"ok": True})
            if self.path == "/api/repair":
                start_repair()
                return self._json({"ok": True})
            if self.path == "/api/pip":
                start_pip()
                return self._json({"ok": True})
            if self.path == "/api/mcp/start":
                start_mcp()
                return self._json({"ok": True})
            if self.path == "/api/mcp/stop":
                MCP.stop()
                return self._json({"ok": True})
            if self.path == "/api/stop":
                JOB.stop()
                return self._json({"ok": True})
        except Exception as e:  # noqa: BLE001
            return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 400)
        self._send(404, b"not found")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    for name in ensure_files():
        print(f"Создан {name} из шаблона")
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = f"http://127.0.0.1:{a.port}/"
    print(f"Панель: {url}  (Ctrl+C — выход)")
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass  # MCP-сервер живёт отдельно: остановка — кнопкой «Остановить MCP-сервер»
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
