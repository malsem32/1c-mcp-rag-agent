"""
Забрать вложенный в чат Open WebUI файл (.epf/.erf/.cfe) в uploads/.

Open WebUI хранит вложения у себя (в Docker), MCP-сервер их не видит.
Берём оригинальные байты через API Open WebUI служебным ключом — поэтому
работает для любого пользователя чата, а не только для владельца ключа.

Настройки (.env):
  OWUI_URL      — адрес Open WebUI, по умолчанию http://localhost:3000
  OWUI_API_KEY  — ключ администратора (Settings → Account → API Keys)
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Any

import httpx

_EXT = (".epf", ".erf", ".cfe")
# допускаем лишний суффикс (Имя.erf.txt) — обход типа image/* у .erf
_NAME_RE = re.compile(r"^(?P<base>.+\.(?:epf|erf|cfe))(?:\.[A-Za-z0-9]{1,5})?$", re.I)
_ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> tuple[str, str]:
    url = (os.environ.get("OWUI_URL") or "http://localhost:3000").rstrip("/")
    return url, (os.environ.get("OWUI_API_KEY") or "").strip()


def is_configured() -> bool:
    return bool(_cfg()[1])


def _norm(s: str) -> str:
    # «Счет  счет.epf» (двойной пробел) == «Счет счет.epf», как его обычно пишет модель
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s or "")).strip().lower().replace("ё", "е")


def _created(f: dict[str, Any]) -> float:
    return float(f.get("created_at") or f.get("updated_at") or 0)


def _file_name(f: dict[str, Any]) -> str:
    meta = f.get("meta") or {}
    return str(f.get("filename") or meta.get("name") or "")


def _real_name(name: str) -> str:
    """«Имя.erf.txt» → «Имя.erf»."""
    m = _NAME_RE.match(Path(name).name)
    return m.group("base") if m else Path(name).name


def _list_files(client: httpx.Client) -> list[dict[str, Any]]:
    """Все доступные ключу файлы; сначала поиск по расширениям, затем общий список."""
    out: dict[str, dict[str, Any]] = {}
    for ext in _EXT:
        r = client.get("/api/v1/files/search", params={"filename": f"*{ext}*", "content": "false"})
        if r.status_code == 200 and isinstance(r.json(), list):
            for f in r.json():
                out[str(f.get("id"))] = f
    if not out:
        r = client.get("/api/v1/files/", params={"content": "false"})
        if r.status_code == 200 and isinstance(r.json(), list):
            for f in r.json():
                out[str(f.get("id"))] = f
    return [f for f in out.values() if _NAME_RE.match(_file_name(f))]


def _pick(files: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    if not name:
        return files
    want = _norm(_real_name(name))
    want_stem = _norm(Path(want).stem)
    exact = [f for f in files if _norm(_real_name(_file_name(f))) == want]
    return exact or [f for f in files if want_stem and want_stem in _norm(_real_name(_file_name(f)))]


def latest_attachment(name: str = "") -> dict[str, Any] | None:
    """Метаданные самого свежего вложения (по имени или любого .epf/.erf/.cfe), без скачивания."""
    if not is_configured():
        return None
    url, key = _cfg()
    try:
        with httpx.Client(base_url=url, headers={"Authorization": f"Bearer {key}"}, timeout=10.0) as client:
            files = _pick(_list_files(client), name)
    except httpx.HTTPError:
        return None
    return max(files, key=_created) if files else None


def fetch_from_open_webui(name: str = "", *, newer_than: float = 0.0) -> tuple[Path | None, str]:
    """
    Скачать в uploads/ файл по имени (или самый свежий .epf/.erf/.cfe, если имя пустое).
    newer_than — mtime локальной копии: качать, только если вложение новее (иначе (None, "")).
    Возвращает (путь, сообщение_об_ошибке).
    """
    if not is_configured():
        return None, "Не задан OWUI_API_KEY в .env (ключ Open WebUI) — вложения чата недоступны."
    url, key = _cfg()
    try:
        with httpx.Client(
            base_url=url, headers={"Authorization": f"Bearer {key}"}, timeout=30.0
        ) as client:
            files = _pick(_list_files(client), name)
            if not files:
                return None, "Файл не найден среди вложений Open WebUI."
            f = max(files, key=_created)
            if newer_than and _created(f) <= newer_than:
                return None, ""
            fname = re.sub(r'[\\/:*?"<>|]', "_", _real_name(_file_name(f))) or "attachment.epf"
            dest_dir = _ROOT / "uploads"
            dest_dir.mkdir(exist_ok=True)
            dest = dest_dir / fname
            # уже скачан (тот же размер и не старше вложения) — не качаем повторно
            size = (f.get("meta") or {}).get("size")
            if dest.is_file() and size and dest.stat().st_size == size and dest.stat().st_mtime >= _created(f):
                return dest.resolve(), ""
            r = client.get(f"/api/v1/files/{f['id']}/content")
            if r.status_code != 200:
                return None, f"Open WebUI вернул {r.status_code} при скачивании файла."
            dest.write_bytes(r.content)
            return dest.resolve(), ""
    except httpx.HTTPError as e:
        return None, f"Нет связи с Open WebUI ({url}): {e}"
