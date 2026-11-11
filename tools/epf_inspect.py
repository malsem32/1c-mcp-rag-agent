# -*- coding: utf-8 -*-
"""
Разбор внешней обработки/отчёта 1С (.epf / .erf / .cfe) и XML-выгрузки.

Не Qdrant и не metadata текущей CF: источник истины — скинутый файл.
Контейнер 1С: OLE Compound (CFB) + zlib-документы + вложенные контейнеры.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
import struct
import time
import zlib
from pathlib import Path
from typing import Any

from tools.epf_template import MXL_PREFIX, mxl_text, parse_mxl
from tools.epf_dcs import (
    _all_datasets,
    dcs_overview,
    dcs_summary,
    extract_dcs_all,
    lint_dcs,
    metadata_refs,
    split_xml_docs,
)

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ENDOFCHAIN = 0xFFFFFFFE
_FREESECT = 0xFFFFFFFF
_MAX_BYTES = 8 * 1024 * 1024
_MAX_TEXTS = 120
_MAX_DEPTH = 10

_EXT = {".epf", ".erf", ".cfe"}

# последний успешно разобранный файл (follow-up без повторного path)
_CACHE: dict[str, Any] = {"key": "", "parsed": None}

_PATH_RE = re.compile(
    r'(?:[A-Za-z]:[\\/][^"\'<>|*\n]+\.(?:epf|erf|cfe)'
    r'|(?:/|\\\\)[^"\'<>|*\n]+\.(?:epf|erf|cfe))',
    re.I,
)
_WIN_QUOTED = re.compile(
    r'"([^"]+\.(?:epf|erf|cfe))"',
    re.I,
)
_V8_EMPTY = 0x7FFFFFFF
_V8_MAGIC = b"\xff\xff\xff\x7f"


_NAME_RE = re.compile(
    r"([^\\/:\"<>|\n]{1,180}\.(?:epf|erf|cfe))",
    re.I,
)


def extract_external_path(phrase: str) -> str:
    """Путь или имя файла (.epf/.erf/.cfe), в т.ч. с пробелами и кириллицей."""
    raw = (phrase or "").strip()
    if not raw:
        return ""
    m = _WIN_QUOTED.search(raw)
    if m:
        return m.group(1).strip()
    m = _PATH_RE.search(raw)
    if m:
        return m.group(0).strip().rstrip(".,;")
    m = _NAME_RE.search(raw)
    if m:
        return m.group(1).strip().rstrip(".,;")
    return ""


_INNER_RE = re.compile(r"^(?P<file>.+?\.(?:epf|erf|cfe))[\\/](?P<inner>.+)$", re.I)


def split_external_path(spec: str) -> tuple[str, str]:
    """«ВыпускСО.epf/<uuid>.0/module» → («ВыпускСО.epf», «<uuid>.0/module»): файл + часть внутри него."""
    s = (spec or "").strip().strip('"')
    m = _INNER_RE.match(s)
    if not m:
        return s, ""
    return m.group("file").strip(), m.group("inner").strip().strip("/\\")


def _norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", Path((s or "").replace("\\", "/")).name).strip().lower().replace("ё", "е")


_FRESH_CHECKED: dict[str, float] = {}
_FRESH_TTL = 20.0


def _is_bare_name(spec: str) -> bool:
    """Имя без каталога («ВыпускСО.epf») — так модель называет вложение чата."""
    s = (spec or "").replace("\\", "/")
    return "/" not in s and ":" not in s


def _newer_attachment(name: str, local: Path | None) -> tuple[Path | None, str]:
    """Вложение Open WebUI, если оно новее локальной копии (прикрепили исправленную версию)."""
    from tools.owui_import import fetch_from_open_webui, is_configured

    if not is_configured():
        return None, ""
    key = _norm_name(name)
    now = time.time()
    if local is not None and now - _FRESH_CHECKED.get(key, 0) < _FRESH_TTL:
        return None, ""
    _FRESH_CHECKED[key] = now
    try:
        mtime = local.stat().st_mtime if local is not None and local.is_file() else 0.0
    except OSError:
        mtime = 0.0
    return fetch_from_open_webui(name, newer_than=mtime)


def locate_external(spec: str) -> tuple[Path | None, str]:
    """
    Файл для разбора: полный путь — как есть; голое имя — свежее из (локальная копия, вложение OWUI);
    пустое — самое свежее вложение чата.
    """
    spec = (spec or "").strip().strip('"')
    local = resolve_external_file(spec) if spec else None
    if spec and not _is_bare_name(spec) and local is not None:
        return local, ""
    name = Path(spec.replace("\\", "/")).name if spec else ""
    fresh, err = _newer_attachment(name, local)
    if fresh is not None:
        return fresh, ""
    return local, ("" if local is not None else err)


def _cached_matches(file_spec: str) -> bool:
    parsed = _CACHE.get("parsed") or {}
    name = _norm_name(file_spec)
    if not (parsed and name and _norm_name(str(parsed.get("file_name") or "")) == name):
        return False
    # в чат могли прикрепить новую версию с тем же именем
    if _is_bare_name(file_spec):
        fresh, _ = _newer_attachment(name, Path(str(parsed.get("file") or "")))
        if fresh is not None:
            return False
    return True


def ensure_external_loaded(file_spec: str) -> tuple[str, str]:
    """
    Найти файл локально (диск, uploads/) или забрать из вложений Open WebUI и разобрать в кэш.
    Возвращает (локальный_путь, ошибка). Если файл уже в кэше — повторно не разбирает.
    """
    file_spec, _ = split_external_path(file_spec)
    if _cached_matches(file_spec):
        return str(_CACHE["parsed"].get("file") or file_spec), ""
    found, err = locate_external(file_spec)
    if found is None:
        return "", err or f"Файл не найден: {file_spec}"
    parsed = parse_external_file(str(found))
    if not parsed.get("ok"):
        return "", str(parsed.get("error") or "Не удалось разобрать файл")
    return str(found), ""


def _search_dirs() -> list[Path]:
    home = Path.home()
    root = Path(__file__).resolve().parents[1]
    dirs = [
        Path.cwd(),
        root,
        root / "uploads",
        home / "Desktop",
        home / "OneDrive" / "Desktop",
        home / "Downloads",
        home / "Загрузки",
        home / "Documents",
        home / "Документы",
    ]
    out: list[Path] = []
    seen: set[str] = set()
    for d in dirs:
        try:
            r = str(d.resolve()) if d.exists() else ""
        except OSError:
            continue
        if r and r not in seen:
            seen.add(r)
            out.append(Path(r))
    return out


def resolve_external_file(spec: str) -> Path | None:
    """
    Найти .epf по полному пути или по имени (Desktop, Downloads, корень проекта).
    Cursor Read/Glob бинарники часто «не видит» — опираемся на Path.is_file().
    """
    spec = (spec or "").strip().strip('"')
    if not spec:
        return None
    p = Path(spec)
    try:
        if p.is_file():
            return p.resolve()
        if p.is_dir():
            return p.resolve()
    except OSError:
        pass
    name = p.name
    if not name:
        return None
    # «разбери Форма для СПОТ.epf» — имя с пробелами внутри фразы: перебираем суффиксы по словам
    words = name.split()
    if len(words) > 1:
        for i in range(len(words)):
            cand_name = " ".join(words[i:])
            found = _find_in_dirs(cand_name)
            if found is not None:
                return found
        return None
    return _find_in_dirs(name)


def _find_in_dirs(name: str) -> Path | None:
    low = name.lower()
    stem = Path(name).stem.lower()
    for d in _search_dirs():
        cand = d / name
        try:
            if cand.is_file() or cand.is_dir():
                return cand.resolve()
        except OSError:
            continue
        try:
            for f in d.iterdir():
                if not f.is_file():
                    continue
                if f.name.lower() == low:
                    return f.resolve()
                if f.suffix.lower() in _EXT and stem and stem in f.stem.lower():
                    return f.resolve()
        except OSError:
            continue
    # загрузки фильтра Open WebUI: uploads/<хеш>/Имя.epf — берём самую свежую
    try:
        up = Path(__file__).resolve().parents[1] / "uploads"
        hits = [f for f in up.glob("*/*") if f.is_file() and f.name.lower() == low]
        if hits:
            return max(hits, key=lambda f: f.stat().st_mtime).resolve()
    except OSError:
        pass
    return None


def is_external_file_question(phrase: str, *, has_cached: bool | None = None) -> bool:
    """Пользователь скинул .epf/.erf или явно спрашивает про приложенный файл."""
    raw = (phrase or "").strip()
    if not raw:
        return False
    if extract_external_path(raw):
        return True
    pl = raw.lower().replace("ё", "е")
    if re.search(r"\.(epf|erf|cfe)\b", pl):
        return True
    cached = bool(_CACHE.get("parsed")) if has_cached is None else bool(has_cached)
    if cached and any(
        x in pl
        for x in (
            "этой обработк",
            "эта обработк",
            "эту обработк",
            "этот отчет",
            "этого отчета",
            "этот файл",
            "по файлу",
            "во внешней",
            "скинут",
            "приложенн",  # «приложенный файл», но не «приложения» (клиентские приложения 1С)
            "приложил",
            "прикрепл",
            "epf",
            "erf",
        )
    ):
        return True
    # «вложенный запрос» — термин 1С, не вложение чата: только «вложенная обработка/файл», «во вложении»
    if re.search(r"вложенн\w*\s+(?:\w+\s+)?(?:обработк|отчет|файл)|во вложени", pl) or (
        any(x in pl for x in ("скинул", "приложенн", "приложил", "аттач", "прикрепл"))
        and any(x in pl for x in ("обработк", "отчет", "файл"))
    ):
        return True
    return False


def _looks_text(s: str) -> bool:
    if len(s) < 8:
        return False
    bad = sum(1 for c in s[:4000] if ord(c) < 9)
    return bad < 8 and (
        "Процедура" in s
        or "Функция" in s
        or "Procedure" in s
        or "Function" in s
        or "<" in s[:200]
        and "xml" in s[:200].lower()
        or "ExternalDataProcessor" in s
        or "ExternalReport" in s
        or "ВЫБРАТЬ" in s
        or "Выбрать" in s
        or (s.lstrip("\ufeff").startswith("{") and "\n{" in s[:400])
    )


def _decode_bytes(data: bytes) -> str | None:
    if not data or len(data) < 4:
        return None
    # UTF-16 LE BOM / typical 1C
    if data[:2] == b"\xff\xfe":
        try:
            s = data.decode("utf-16-le")
            return s if _looks_text(s) or len(s) > 40 else None
        except Exception:
            return None
    for enc in ("utf-8-sig", "utf-16-le", "utf-16-be", "cp1251"):
        try:
            s = data.decode(enc)
        except UnicodeDecodeError as e:
            # обычная форма: текст {…} в UTF-8, а в конце вшит бинарный контейнер (картинки/модуль) —
            # берём текстовую часть, иначе теряется вся раскладка формы (кнопки, поля, подписи)
            if enc != "utf-8-sig" or e.start < 200:
                continue
            s = data[: e.start].decode(enc, errors="ignore")
            if _looks_text(s):
                return s
            continue
        except Exception:
            continue
        if _looks_text(s):
            return s
        # XML без ключевых слов
        t = s.lstrip()
        if t.startswith("<?xml") or t.startswith("<MetaDataObject") or t.startswith(
            "<document"
        ):
            return s
    return None


def _inflate(data: bytes) -> bytes | None:
    if not data or len(data) < 4:
        return None
    candidates = [data]
    if len(data) > 8:
        candidates.append(data[8:])
    if len(data) > 4:
        candidates.append(data[4:])
    for chunk in candidates:
        if len(chunk) < 2:
            continue
        if chunk[:2] in (b"\x78\x9c", b"\x78\x01", b"\x78\xda", b"\x78\x5e"):
            try:
                out = zlib.decompress(chunk)
                if out and out != data:
                    return out
            except Exception:
                pass
        try:
            out = zlib.decompress(chunk, -15)
            if out and out != data:
                return out
        except Exception:
            pass
    return None


def _sector(data: bytes, idx: int, ssize: int) -> bytes:
    off = 512 + idx * ssize
    return data[off : off + ssize]


def _fat_chain(fat: list[int], start: int, limit: int = 1_000_000) -> list[int]:
    out: list[int] = []
    cur = start
    seen: set[int] = set()
    while cur < len(fat) and cur not in (_ENDOFCHAIN, _FREESECT) and cur < 0xFFFFFFFA:
        if cur in seen or len(out) >= limit:
            break
        seen.add(cur)
        out.append(cur)
        cur = fat[cur]
    return out


def _cfb_streams(data: bytes) -> list[tuple[str, bytes]]:
    """Потоки OLE Compound File (stdlib, без olefile)."""
    if len(data) < 512 or data[:8] != OLE_MAGIC:
        return []
    sector_shift = struct.unpack_from("<H", data, 0x1E)[0]
    ssize = 1 << sector_shift
    mini_shift = struct.unpack_from("<H", data, 0x20)[0]
    mini_size = 1 << mini_shift
    dir_first = struct.unpack_from("<I", data, 0x30)[0]
    mini_cutoff = struct.unpack_from("<I", data, 0x38)[0]
    mini_fat_first = struct.unpack_from("<I", data, 0x3C)[0]
    difat_first = struct.unpack_from("<I", data, 0x44)[0]
    num_difat = struct.unpack_from("<I", data, 0x48)[0]

    fat_ids: list[int] = list(struct.unpack_from("<109I", data, 0x4C))
    # DIFAT
    cur = difat_first
    for _ in range(min(num_difat, 1000)):
        if cur >= 0xFFFFFFFA:
            break
        sec = _sector(data, cur, ssize)
        n = ssize // 4
        vals = struct.unpack_from(f"<{n}I", sec)
        fat_ids.extend(vals[:-1])
        cur = vals[-1]

    fat: list[int] = []
    for sid in fat_ids:
        if sid >= 0xFFFFFFFA:
            continue
        sec = _sector(data, sid, ssize)
        n = len(sec) // 4
        fat.extend(struct.unpack_from(f"<{n}I", sec))

    def read_chain(start: int, size: int, *, mini: bool = False) -> bytes:
        if size <= 0:
            return b""
        if mini:
            chain = _fat_chain(mini_fat, start, limit=20000)
            buf = b"".join(_mini_sector(i) for i in chain)
            return buf[:size]
        chain = _fat_chain(fat, start, limit=20000)
        buf = b"".join(_sector(data, i, ssize) for i in chain)
        return buf[:size]

    dir_chain = _fat_chain(fat, dir_first, limit=4000)
    dir_raw = b"".join(_sector(data, i, ssize) for i in dir_chain)

    entries: list[dict[str, Any]] = []
    for i in range(0, len(dir_raw) - 127, 128):
        rec = dir_raw[i : i + 128]
        name_len = struct.unpack_from("<H", rec, 64)[0]
        if name_len < 2:
            name = ""
        else:
            name = rec[: min(64, name_len - 2)].decode("utf-16-le", errors="replace")
        etype = rec[66]
        start = struct.unpack_from("<I", rec, 116)[0]
        size = struct.unpack_from("<Q", rec, 120)[0]
        entries.append(
            {"name": name.strip("\x00"), "type": etype, "start": start, "size": size}
        )

    root = next((e for e in entries if e["type"] == 5), None)
    mini_fat: list[int] = []
    mini_container = b""
    if root and mini_fat_first < 0xFFFFFFFA:
        mchain = _fat_chain(fat, mini_fat_first, limit=2000)
        mraw = b"".join(_sector(data, i, ssize) for i in mchain)
        n = len(mraw) // 4
        mini_fat = list(struct.unpack_from(f"<{n}I", mraw))
        mini_container = read_chain(root["start"], int(root["size"]), mini=False)

    def _mini_sector(idx: int) -> bytes:
        off = idx * mini_size
        return mini_container[off : off + mini_size]

    out: list[tuple[str, bytes]] = []
    for e in entries:
        if e["type"] != 2 or e["size"] <= 0:
            continue
        try:
            if e["size"] < mini_cutoff and mini_fat:
                blob = read_chain(e["start"], int(e["size"]), mini=True)
            else:
                blob = read_chain(e["start"], int(e["size"]), mini=False)
        except Exception:
            continue
        if blob:
            out.append((e["name"] or "stream", blob))
    return out


def _is_v8_container(data: bytes) -> bool:
    return (
        len(data) >= 18
        and data[:4] == _V8_MAGIC
        and data[16:18] == b"\r\n"
    )


def _v8_parse_hdr(data: bytes, off: int) -> tuple[int, int, int, int] | None:
    """Заголовок блока: CRLF + размер документа / размер страницы / следующая страница в hex."""
    if off < 0 or off + 31 > len(data) or data[off : off + 2] != b"\r\n":
        return None
    try:
        size = int(data[off + 2 : off + 10], 16)
        page = int(data[off + 11 : off + 19], 16)
        nxt = int(data[off + 20 : off + 28], 16)
    except ValueError:
        return None
    return size, page, nxt, off + 31


def _v8_read_block(data: bytes, off: int) -> bytes:
    """Документ контейнера: цепочка страниц; размер документа задан в первой странице, у каждой — свой размер страницы."""
    chunks: list[bytes] = []
    seen: set[int] = set()
    total, got = -1, 0
    while off != _V8_EMPTY and off not in seen and 0 <= off < len(data):
        seen.add(off)
        h = _v8_parse_hdr(data, off)
        if not h:
            break
        size, page, nxt, start = h
        if total < 0:
            total = max(0, size)
        take = min(max(0, page), total - got) if page else total - got
        chunks.append(data[start : min(len(data), start + take)])
        got += take
        if got >= total:
            break
        off = nxt
    return b"".join(chunks)


def _v8_elem_name(hdr: bytes) -> str:
    if len(hdr) < 20:
        return ""
    return hdr[20:].decode("utf-16-le", errors="replace").split("\x00")[0].strip()


def _v8_unpack_files(data: bytes) -> list[tuple[str, bytes]]:
    """Файлы контейнера 1С 8.x (не OLE): тройки header/body/0x7fffffff."""
    if not _is_v8_container(data):
        return []
    toc = _v8_read_block(data, 16)
    if len(toc) < 12:
        return []
    n = len(toc) // 4
    vals = struct.unpack_from(f"<{n}I", toc)
    out: list[tuple[str, bytes]] = []
    for i in range(0, len(vals) - 2, 3):
        ha, ba = int(vals[i]), int(vals[i + 1])
        hdr = _v8_read_block(data, ha)
        body = _v8_read_block(data, ba)
        inf = _inflate(body)
        blob = inf if inf else body
        name = _v8_elem_name(hdr) or f"elem_{i // 3}"
        if blob:
            out.append((name, blob))
    return out


def _walk_blob(
    data: bytes,
    name: str,
    *,
    depth: int,
    acc: list[tuple[str, str]],
    budget: list[int],
) -> None:
    if depth > _MAX_DEPTH or budget[0] <= 0 or len(acc) >= _MAX_TEXTS:
        return
    if not data:
        return
    budget[0] -= min(len(data), 64)

    if _is_v8_container(data):
        for sname, blob in _v8_unpack_files(data):
            rel = f"{name}/{sname}" if name else sname
            _walk_blob(blob, rel, depth=depth + 1, acc=acc, budget=budget)
        return

    if data[:8] == OLE_MAGIC:
        for sname, blob in _cfb_streams(data):
            _walk_blob(
                blob,
                f"{name}/{sname}" if name else sname,
                depth=depth + 1,
                acc=acc,
                budget=budget,
            )
        return

    # бинарный префикс + один или несколько склеенных XML (схема СКД + настройки)
    if b"<?xml" in data[:4096] and not data.lstrip(b"\xef\xbb\xbf")[:5] == b"<?xml":
        parts = split_xml_docs(data)
        if parts:
            for root, text in parts:
                acc.append((f"{name}/{root}" if name else root, text))
            return
    elif data.count(b"<?xml") > 1:
        parts = split_xml_docs(data)
        for root, text in parts:
            acc.append((f"{name}/{root}" if name else root, text))
        return

    mxl = mxl_text(data)
    if mxl:
        acc.append((name or "template", mxl))
        return

    text = _decode_bytes(data)
    if text:
        acc.append((name or "module", text))
        return

    inf = _inflate(data)
    if inf and inf != data:
        _walk_blob(inf, name, depth=depth + 1, acc=acc, budget=budget)
        return

    # вложенный OLE после мусорного префикса
    pos = data.find(OLE_MAGIC)
    if pos > 0:
        _walk_blob(
            data[pos:], name, depth=depth + 1, acc=acc, budget=budget
        )


def _xml_tag_names(xml: str, tag: str) -> list[str]:
    names: list[str] = []
    for m in re.finditer(
        rf"<{tag}\b[\s\S]*?<Name>([^<]+)</Name>", xml, re.I
    ):
        n = (m.group(1) or "").strip()
        if n and n not in names:
            names.append(n)
    return names


def _xml_simple_name(xml: str) -> str:
    m = re.search(r"<Name>([^<]+)</Name>", xml)
    return (m.group(1) or "").strip() if m else ""


def _xml_synonym(xml: str) -> str:
    m = re.search(
        r"<Synonym>[\s\S]*?<v8:content>([^<]+)</v8:content>", xml, re.I
    )
    return (m.group(1) or "").strip() if m else ""


# классы коллекций «Формы» и «Макеты» в основном потоке метаданных: обработка / отчёт
_MD_FORMS = ("d5b0e5ed-256d-401c-9c36-f630cafd8a62", "a3b368c0-29e2-11d6-a3c7-0050bae0a776")
_MD_TEMPLATES = ("3daea016-69b7-4ed4-9453-127911372fe6",)


def _typed_collections(texts: list[tuple[str, str]]) -> dict[str, list[str]] | None:
    """Формы и макеты по структуре метаданных: {класс,N,uuid…} → «Имя (Синоним)» из заголовка части uuid."""
    head = re.compile(r'\{[01],0,([0-9a-f-]{36})\},"([^"]+)",\s*\{1,"ru","([^"]*)"\}')
    meta = next(
        (b for r, b in texts if re.search(r"/[0-9a-f-]{36}$", r.replace("\\", "/")) and any(a in b for a in _MD_ATTRS)),
        None,
    )
    if meta is None:
        return None
    labels: dict[str, str] = {}
    for r, b in texts:
        leaf = r.replace("\\", "/").rsplit("/", 1)[-1]
        if len(leaf) == 36 and "/" in r:
            m = head.search(b[:600])
            if m and m.group(1) == leaf:
                labels[leaf] = m.group(2) if not m.group(3) or m.group(3) == m.group(2) else f"{m.group(2)} ({m.group(3)})"
    out: dict[str, list[str]] = {}
    for key, classes in (("forms", _MD_FORMS), ("templates", _MD_TEMPLATES)):
        m = next((x for cls in classes for x in [re.search(rf"\{{{cls},(\d+)((?:,[0-9a-f-]{{36}})*)\s*\}}", meta)] if x), None)
        if not m:
            return None
        out[key] = [labels[u] for u in re.findall(r"[0-9a-f-]{36}", m.group(2)) if u in labels]
    return out


def _uuid_labels(texts: list[tuple[str, str]]) -> dict[str, tuple[str, str]]:
    """uuid формы/макета → (Имя, Синоним) из заголовочной части «Файл/<uuid>»."""
    head = re.compile(r'\{[01],0,([0-9a-f-]{36})\},"([^"]+)",\s*\{1,"ru","([^"]*)"\}')
    labels: dict[str, tuple[str, str]] = {}
    for r, b in texts:
        leaf = r.replace("\\", "/").rsplit("/", 1)[-1]
        if len(leaf) == 36 and not b.startswith(MXL_PREFIX):
            m = head.search(b[:600])
            if m and m.group(1) == leaf:
                labels[leaf] = (m.group(2), m.group(3))
    return labels


# вид макета — второе число в заголовке «{1,{2,<вид>,{3,{1,0,uuid},"Имя"…»
_TPL_KIND = {"0": "табличный документ", "1": "двоичные данные", "2": "ActiveDocument", "3": "HTML-документ",
             "4": "текстовый документ", "5": "географическая схема", "6": "схема компоновки данных",
             "7": "макет оформления компоновки", "8": "графическая схема"}


def _templates_detail(texts: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Макеты табличного документа: имя, области (подписи, параметры), все параметры."""
    labels = _uuid_labels(texts)
    out: list[dict[str, Any]] = []
    for r, b in texts:
        if not b.startswith(MXL_PREFIX):
            continue
        leaf = r.replace("\\", "/").rsplit("/", 1)[-1]
        uid = leaf[:-2] if leaf.endswith(".0") else leaf
        nm, syn = labels.get(uid, (uid, ""))
        info = parse_mxl(b)
        if info:
            out.append({"name": nm, "synonym": syn if syn != nm else "", "path": r, **info})
    # остальные макеты (СКД, текст, двоичные…) и пустые табличные — вид и признак «пустой», а не тишина
    done = {t["name"] for t in out}
    bodies = {m.group(1) for r, _b in texts for m in [re.search(r"([0-9a-f-]{36})\.0(?:/|$)", r)] if m}
    for r, b in texts:
        m = re.match(r"\s*\{1,\s*\{2,(\d+),", b.lstrip("﻿"))
        leaf = r.replace("\\", "/").rsplit("/", 1)[-1]
        if not m or len(leaf) != 36 or leaf not in labels or labels[leaf][0] in done:
            continue
        nm, syn = labels[leaf]
        empty = leaf not in bodies
        out.append({"name": nm, "synonym": syn if syn != nm else "", "path": r, "kind": _TPL_KIND.get(m.group(1), m.group(1)),
                    "empty": empty, "rows": 0, "areas": [], "parameters": []})
    return out


def _internal_named_objects(texts: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Имена из внутреннего формата 1С: },\"Имя\", {1,\"ru\",\"Синоним\"."""
    forms: list[str] = []
    templates: list[str] = []
    other: list[str] = []
    typed = _typed_collections(texts)
    pat = re.compile(
        r'\},"([^"]+)",\s*\{1,"ru","([^"]*)"',
    )
    for _rel, body in texts:
        if "Процедура" in body or "Функция" in body:
            continue
        t = body.lstrip("\ufeff")
        if not t.startswith("{"):
            continue
        for name, syn in pat.findall(body):
            label = name if not syn or syn == name else f"{name} ({syn})"
            lump = f"{name} {syn}".lower()
            if typed is not None:
                # классификация по структуре: подстрока «форм» ловит «проформа», «горизонтальная форма»
                if label not in typed["forms"] and label not in typed["templates"] and label not in other:
                    other.append(label)
            elif "форм" in lump:
                if label not in forms:
                    forms.append(label)
            elif "макет" in lump or name.startswith("СПОТ") or "спот" in lump:
                if label not in templates:
                    templates.append(label)
            elif label not in other:
                other.append(label)
    if typed is not None:
        forms, templates = typed["forms"], typed["templates"]
    return {"forms": forms, "templates": templates, "other": other}


def _internal_meta(texts: list[tuple[str, str]]) -> dict[str, Any]:
    """Внутренний формат 1С: версия, внешние ссылки (copyinfo), форма."""
    out: dict[str, Any] = {"platform": "", "cf_refs": [], "form": {}}
    for rel, body in texts:
        leaf = rel.rsplit("/", 1)[-1]
        t = body.lstrip("﻿")
        if leaf == "version":
            m = re.search(r"\{(\d+),\d+,\s*\{(\d+),", t)
            if m:
                out["platform"] = f"формат {m.group(1)}, совместимость {m.group(2)}"
        elif leaf == "copyinfo":
            # объекты CF, на которые ссылается файл (типы реквизитов/параметров)
            for _cls, nm in re.findall(r"\{([0-9a-f-]{36}),\"([^\"]+)\"\}", t):
                if nm not in out["cf_refs"]:
                    out["cf_refs"].append(nm)
        elif leaf == "form" or leaf.endswith("/form"):
            caps = re.findall(r'\{"ru","([^"]+)"\}', t)
            items = re.findall(r'\{\d+,"([A-Za-zА-Яа-яЁё_][\w]*)",', t)
            out["form"] = {
                "path": rel,
                "type": "managed" if "<Form" in t[:300] or t.startswith("{4,") else "ordinary",
                "captions": list(dict.fromkeys(caps))[:60],
                "elements": list(dict.fromkeys(items))[:120],
                "data_path": (re.search(r'"(\w+Объект)"', t) or [None, ""])[1],
            }
    return out


_FORM_HDR = re.compile(r'\{[01],0,([0-9a-f-]{36})\},"([^"]+)",\s*\{1,"ru","([^"]*)"\}')
# действие кнопки обычной формы: {7,<uuid>,1,<тип>,{3,"Процедура",{1,"Процедура",{1,1,{"ru","Подпись"}}
_FORM_ACTION = re.compile(
    r'\{7,[0-9a-f-]{36},\d+,[0-9a-f-]{36},\s*\{3,"([A-Za-zА-Яа-яЁё_]\w*)",\s*\{1,"\1",\s*\{1,1,\s*\{"ru","([^"]*)"\}'
)
# пункт подменю/кнопка: {8,"Имя",0,1,{1,1,{"ru","Подпись"}} — обработчик, только если Имя — процедура модуля
_FORM_ITEM = re.compile(r'\{8,"([A-Za-zА-Яа-яЁё_]\w*)",\d+,\d+,\s*\{1,1,\s*\{"ru","([^"]*)"\}')
_FORM_CAPTION = re.compile(r'\{"ru","([^"]{2,120})"\}')
# не текст на экране: имена элементов по умолчанию и описания обработчиков («… при изменении»)
_TECH_CAPTION = re.compile(
    r"^(Страница|Действие|Разделитель|Панель|Надпись|Поле|Кнопка|Форма|КоманднаяПанель)\d*$|^<|>$"
    r"|(при изменении|при смене страницы|при открытии|перед закрытием|перед открытием|нажатие|при выводе строки"
    r"|перед удалением|перед началом добавления|при активизации строки|обработка выбора)$",
    re.I,
)


def _forms_ui(texts: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """
    Интерфейс каждой формы из её описания (…/<uuid>.0/form): подписи элементов в порядке появления,
    кнопки (подпись → процедура модуля), обработчики событий полей. Нужен для инструкций пользователю.
    """
    names: dict[str, tuple[str, str]] = {}
    for rel, body in texts:
        m = _FORM_HDR.search(body[:600])
        if m and "/" in rel and not rel.rsplit("/", 1)[-1].endswith(("form", "module")):
            names[m.group(1)] = (m.group(2), m.group(3))
    by_uuid = {rel.split("/")[-2][:36]: (rel, body) for rel, body in texts if rel.endswith(".0/module")}
    out: list[dict[str, Any]] = []
    for rel, body in texts:
        if not rel.endswith(".0/form"):
            continue
        uuid = rel.split("/")[-2][:36]
        name, syn = names.get(uuid, ("", ""))
        mod_rel, mod = by_uuid.get(uuid, ("", ""))
        procs = re.findall(r"(?im)^[ 	]*(?:Процедура|Функция|Procedure|Function)[ 	]+([A-Za-zА-Яа-яЁё_]\w*)", mod)
        actions, seen = [], set()
        pset = set(procs)
        for h, cap in _FORM_ACTION.findall(body) + _FORM_ITEM.findall(body):
            if h in pset and h not in seen:
                seen.add(h)
                actions.append({"caption": cap, "handler": h})
        acted = {a["handler"] for a in actions}
        events = [p for p in procs if p not in acted and f'"{p}"' in body]
        caps = []
        for c in _FORM_CAPTION.findall(body):
            c = c.strip()
            # служебные подписи вида «Командная панель ТЧ…» — имена элементов, не текст на экране
            if c and c not in caps and not c.startswith("Командная панель") and not _TECH_CAPTION.search(c):
                caps.append(c)
        item: dict[str, Any] = {
            "form": name or uuid,
            "synonym": syn,
            "form_path": rel,
            "module_path": mod_rel or None,
            "type": "managed" if "<Form" in body[:300] else "ordinary",
            "buttons": actions[:60],
            "event_handlers": events[:60],
            "captions": caps[:150],
        }
        if item["type"] == "ordinary":
            # полный разбор обычной формы (как у форм конфигурации): вкладки, элементы, настоящие подписи кнопок
            try:
                from tools.form_info import compact_form_layout
                from tools.ordinary_form import ordinary_form_layout

                lay = ordinary_form_layout(body, mod)
                btns, seen_h = [], set()
                for b in lay.get("buttons") or []:
                    h = b.get("handler")
                    if h and h not in seen_h:
                        seen_h.add(h)
                        btns.append({"caption": b.get("caption") or h, "handler": h})
                if btns:
                    item["buttons"] = btns[:60]
                std = [b.get("caption") for b in lay.get("buttons") or [] if b.get("standard") and b.get("caption")]
                if std:
                    item["standard_buttons"] = list(dict.fromkeys(std))[:20]
                ev = [h for el in lay.get("elements") or [] for h in (el.get("events") or {}).values()]
                item["event_handlers"] = list(dict.fromkeys(ev + list(lay.get("form_events") or [])))[:60]
                cl = compact_form_layout(lay, max_chars=3500)
                item["tabs"] = cl.get("tabs") or []
                # у большинства обычных форм вкладок нет — все поля здесь (раньше терялись)
                if cl.get("outside_tabs"):
                    item["elements"] = cl["outside_tabs"]
            except Exception:  # noqa: BLE001 — разбор формы не должен ронять разбор файла
                pass
        out.append(item)
    return out


_REF_TYPES = {
    "CatalogRef": "Справочник",
    "DocumentRef": "Документ",
    "EnumRef": "Перечисление",
    "ChartOfCharacteristicTypesRef": "ПланВидовХарактеристик",
    "ChartOfAccountsRef": "ПланСчетов",
    "ChartOfCalculationTypesRef": "ПланВидовРасчета",
    "BusinessProcessRef": "БизнесПроцесс",
    "TaskRef": "Задача",
    "ExchangePlanRef": "ПланОбмена",
}


def _ref_type_ru(t: str) -> str:
    """Платформенный тип xml (EnumRef.X) → Перечисление.X."""
    head, _, tail = (t or "").partition(".")
    return f"{_REF_TYPES[head]}.{tail}" if head in _REF_TYPES and tail else ""


def _internal_synonym(texts: list[tuple[str, str]], name: str) -> str:
    if not name:
        return ""
    pat = re.compile(rf'"{re.escape(name)}",\s*\{{1,"ru","([^"]*)"\}}')
    for _rel, body in texts:
        m = pat.search(body)
        if m:
            return m.group(1)
    return ""


def _kind_from_texts(texts: list[tuple[str, str]]) -> str:
    blob = " ".join(t[:800] for _, t in texts[:15])
    if "ExternalReport" in blob or "ExternalDataProcessorReport" in blob:
        return "ExternalReport"
    if "ConfigurationExtension" in blob or ".cfe" in blob.lower():
        return "ConfigurationExtension"
    if "ExternalDataProcessor" in blob:
        return "ExternalDataProcessor"
    return "external_file"


def _procs(bsl: str) -> list[str]:
    out: list[str] = []
    for m in re.finditer(
        r"(?:Процедура|Функция|Procedure|Function)\s+([A-Za-zА-Яа-яЁё_][\w]*)",
        bsl,
    ):
        n = m.group(1)
        if n not in out:
            out.append(n)
        if len(out) >= 80:
            break
    return out


def _queries(bsl: str, rel: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    lines = bsl.splitlines()
    for i, line in enumerate(lines, 1):
        if "ВЫБРАТЬ" in line or "Выбрать" in line or "SELECT" in line.upper():
            snippet = line.strip()[:240]
            hits.append({"path": rel, "line": i, "snippet": snippet})
            if len(hits) >= 30:
                break
    return hits


def _search_texts(
    texts: list[tuple[str, str]], query: str, *, limit: int = 12
) -> list[dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []
    toks = [t for t in re.findall(r"[A-Za-zА-Яа-яЁё0-9_]{3,}", q) if t.lower() not in {
        "как", "что", "где", "этот", "эта", "обработка", "внешняя", "расскажи",
        "логика", "работает", "файл", "почему",
    }]
    if not toks:
        toks = [q]
    hits: list[dict[str, Any]] = []
    for rel, body in texts:
        low = body.lower()
        if not any(t.lower() in low for t in toks):
            continue
        lines = body.splitlines()
        for i, line in enumerate(lines, 1):
            ll = line.lower()
            if any(t.lower() in ll for t in toks):
                hits.append(
                    {
                        "path": rel,
                        "line": i,
                        "snippet": line.strip()[:280],
                    }
                )
                if len(hits) >= limit:
                    return hits
    return hits


def _parse_folder(root: Path) -> list[tuple[str, str]]:
    acc: list[tuple[str, str]] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in {".bsl", ".xml", ".txt", ".json"}:
            continue
        if p.stat().st_size > _MAX_BYTES:
            continue
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        text = _decode_bytes(raw) or raw.decode("utf-8", errors="replace")
        rel = str(p.relative_to(root)).replace("\\", "/")
        acc.append((rel, text))
        if len(acc) >= _MAX_TEXTS:
            break
    return acc


def _summarize(parsed: dict[str, Any]) -> str:
    kind = parsed.get("kind") or "файл"
    name = parsed.get("name") or parsed.get("file_name") or ""
    nmod = len(parsed.get("modules") or [])
    nattr = len(parsed.get("attributes") or [])
    nform = len(parsed.get("forms") or [])
    nq = len(parsed.get("queries") or [])
    bits = [f"{kind}" + (f" «{name}»" if name else "")]
    bits.append(f"модулей: {nmod}")
    if nattr:
        bits.append(f"реквизитов: {nattr}")
    if nform:
        bits.append(f"форм: {nform}")
    if nq:
        bits.append(f"фрагментов запросов: {nq}")
    if parsed.get("dcs"):
        bits.append(dcs_summary(parsed["dcs"]))
        if parsed.get("dcs_extra"):
            bits.append("ещё СКД: " + ", ".join(x["template"] or "?" for x in parsed["dcs_extra"]))
        bits.append(f"находок анализа: {len(parsed.get('dcs_findings') or [])}")
    return "; ".join(bits)


def parse_external_file(path: str) -> dict[str, Any]:
    raw_path = (path or "").strip().strip('"')
    found = resolve_external_file(raw_path)
    p = found if found is not None else Path(raw_path)
    if not p.exists():
        searched = [str(d) for d in _search_dirs()]
        return {
            "ok": False,
            "error": f"Файл не найден: {path}",
            "looked_in": searched,
            "hint": (
                "Cursor Search/Read бинарный .epf часто не показывает. "
                "Положи файл в папку проекта или на Рабочий стол и вызови "
                "inspect_external_processing(path='Имя.epf'). "
                "Не пиши «файла нет», пока tool не вернул ok=false."
            ),
        }

    key = ""
    texts: list[tuple[str, str]] = []
    if p.is_dir():
        texts = _parse_folder(p)
        key = f"dir:{p.resolve()}"
    else:
        raw = p.read_bytes()
        if len(raw) > 40 * 1024 * 1024:
            return {"ok": False, "error": "Файл слишком большой (>40 МБ)"}
        key = hashlib.sha256(raw).hexdigest()
        if _CACHE.get("key") == key and isinstance(_CACHE.get("parsed"), dict):
            cached = dict(_CACHE["parsed"])
            cached["from_cache"] = True
            return cached
        budget = [_MAX_BYTES]
        _walk_blob(raw, p.name, depth=0, acc=texts, budget=budget)
        if not texts:
            t = _decode_bytes(raw)
            if t:
                texts = [(p.name, t)]

    if not texts:
        return {
            "ok": False,
            "error": "Не удалось извлечь текст/модули из файла",
            "hint": (
                "Это должен быть .epf/.erf/.cfe платформы 8.x или папка "
                "«сохранить как файлы». Не запускаю обработку в 1С."
            ),
            "file": str(p.resolve()),
        }

    modules: list[dict[str, Any]] = []
    xml_blob = ""
    queries: list[dict[str, Any]] = []
    dcs_all = extract_dcs_all(texts)
    dcs = dcs_all[0][1] if dcs_all else None
    dcs_findings: list[dict[str, Any]] = []
    dcs_extra: list[dict[str, Any]] = []
    tpl_names = {u: n for u, (n, _s) in _uuid_labels(texts).items()}
    for i, (grp, sch) in enumerate(dcs_all):
        # главная схема — «СКД/…» (как раньше), остальные — «СКД <Макет>/…»: не теряются и не смешиваются
        tname = tpl_names.get(grp.rsplit("/", 1)[-1][:36], "")
        pre = "СКД" if i == 0 else f"СКД {tname or i + 1}"
        fs = lint_dcs(sch)
        if i:
            for f in fs:
                f["template"] = tname
            ov_x = dcs_overview(sch)
            if ov_x:
                texts.append((f"{pre}/описание", ov_x))
            dcs_extra.append({"template": tname, "summary": dcs_summary(sch), "overview_path": f"{pre}/описание"})
        dcs_findings += fs
        for ds in _all_datasets(sch):
            if (ds.get("query") or "").strip():
                rel_q = f"{pre}/{ds['name']}/запрос"
                texts.append((rel_q, ds["query"]))
                queries.extend(_queries(ds["query"], rel_q))
    for rel, body in texts:
        if body.startswith(MXL_PREFIX):
            continue
        is_bsl = (
            "Процедура" in body
            or "Функция" in body
            or "Procedure" in body
            or rel.lower().endswith(".bsl")
        )
        is_xml = body.lstrip().startswith("<?xml") or "<MetaDataObject" in body[:400]
        if is_xml:
            xml_blob += "\n" + body[:80_000]
        if is_bsl:
            procs = _procs(body)
            modules.append(
                {
                    "path": rel,
                    "proc_count": len(procs),
                    "proc_names": procs[:40],
                    "char_count": len(body),
                    "preview": "\n".join(body.splitlines()[:12])[:900],
                }
            )
            queries.extend(_queries(body, rel))

    name = _xml_simple_name(xml_blob)
    internal = _internal_named_objects(texts)
    meta = _internal_meta(texts)
    syn = _xml_synonym(xml_blob) or _internal_synonym(texts, name or p.stem)
    overview = dcs_overview(dcs) if dcs else ""
    if overview:
        texts.append(("СКД/описание", overview))
    own = {name or p.stem} | {
        x.split(" (")[0] for k in ("forms", "templates", "other") for x in internal.get(k) or []
    }
    ref_src = [
        b for r, b in texts
        if not b.startswith(MXL_PREFIX) and (r.startswith("СКД") or "Процедура" in b or "Функция" in b)
    ]
    if dcs:
        ref_src += [p_.get("value") or "" for p_ in dcs.get("parameters") or []]
        ref_src += [
            _ref_type_ru(t) for p_ in dcs.get("parameters") or [] for t in p_.get("types") or []
        ]
    cf_refs = metadata_refs(ref_src)
    for nm in meta.get("cf_refs") or []:
        if nm not in own and not any(r.endswith("." + nm) for r in cf_refs):
            cf_refs.append(nm)
    kind = _kind_from_texts(texts)
    if p.suffix.lower() == ".erf":
        kind = "ExternalReport"
    elif p.suffix.lower() == ".epf":
        kind = "ExternalDataProcessor"
    elif p.suffix.lower() == ".cfe":
        kind = "ConfigurationExtension"

    forms_ui = _forms_ui(texts)
    templates_detail = _templates_detail(texts)
    parsed: dict[str, Any] = {
        "ok": True,
        "source": "external_file",
        "kind": kind,
        "name": name or p.stem,
        "synonym": syn,
        "file": str(p.resolve()),
        "file_name": p.name,
        "modules": modules,
        "attributes": _xml_tag_names(xml_blob, "Attribute"),
        "tabular_sections": _xml_tag_names(xml_blob, "TabularSection"),
        "forms": _xml_tag_names(xml_blob, "Form") or internal.get("forms")
        or [f"{u['form']} ({u['synonym']})" if u.get("synonym") and u["synonym"] != u["form"] else u["form"]
            for u in forms_ui],
        "templates": _xml_tag_names(xml_blob, "Template")
        or internal.get("templates") or [],
        "queries": queries[:30],
        "platform": meta.get("platform") or "",
        "form": meta.get("form") or {},
        "forms_ui": forms_ui,
        "templates_detail": templates_detail,
        "cf_refs": cf_refs,
        "parts": [
            {"path": r, "lines": len(b.splitlines()), "chars": len(b)} for r, b in texts
        ],
        "dcs_overview": overview,
        "dcs": dcs,
        "dcs_findings": dcs_findings,
        "dcs_extra": dcs_extra,
        "module_count": len(modules),
        "extracted_parts": len(texts),
        "from_cache": False,
        "_texts": texts,
    }
    if not parsed["attributes"] and not parsed["tabular_sections"]:
        # внутренний формат (не XML): реквизиты, ТЧ и настоящее имя объекта — из потока метаданных
        try:
            st = external_structure(parsed)
        except Exception:  # noqa: BLE001 — структура не должна ронять разбор файла
            st = {}
        parsed["attributes"] = [a["name"] for a in st.get("attributes") or []]
        parsed["tabular_sections"] = [t["name"] for t in st.get("tabular_sections") or []]
        if st.get("name") and not name:
            parsed["name"] = st["name"]
            parsed["synonym"] = parsed["synonym"] or st.get("synonym") or ""
    try:
        stx = structure_text(external_structure(parsed))
    except Exception:  # noqa: BLE001
        stx = ""
    tpl_txt = templates_text(templates_detail)
    if tpl_txt:
        texts.append((TEMPLATES_PART, tpl_txt))
        parsed["parts"].append({"path": TEMPLATES_PART, "lines": len(tpl_txt.splitlines()), "chars": len(tpl_txt)})
    if stx:
        texts.append((STRUCTURE_PART, stx))
        parsed["parts"].append({"path": STRUCTURE_PART, "lines": len(stx.splitlines()), "chars": len(stx)})
    parsed["compatibility"] = check_cf_compatibility(cf_refs, meta.get("form") or {})
    parsed["summary"] = _summarize(parsed)
    slim = {k: v for k, v in parsed.items() if k != "_texts"}
    _CACHE["key"] = key
    _CACHE["parsed"] = parsed
    return slim | {"_has_texts": True}


# ---------------------------------------------------------------- структура (внутренний формат)
# классы коллекций в основном потоке метаданных обработки/отчёта
_MD_ATTR = "ec6bb5e5-b7a8-4d75-bec9-658107a699cf"
_MD_TS = "2bcef0d1-0981-11d6-b9b8-0050bae0a95d"
_MD_TS_ATTR = "5d24a9d1-098e-11d6-b9b8-0050bae0a95d"
# у отчёта (.erf) те же коллекции под другими классами
_MD_ATTR_REPORT = "7e7123e0-29e2-11d6-a3c7-0050bae0a776"
_MD_TS_REPORT = "b077d780-29e2-11d6-a3c7-0050bae0a776"
_MD_ATTRS = (_MD_ATTR, _MD_ATTR_REPORT)
_MD_TSS = (_MD_TS, _MD_TS_REPORT)
# колонки ТЧ: обработка; ТЧ объектов конфигурации (класс у отчёта не подтверждён — ниже есть структурный fallback)
_MD_TS_ATTRS = (_MD_TS_ATTR, "888744e1-b616-11d4-9436-004095e12fc7")
# класс объекта CF в copyinfo → префикс полного имени
_MD_CLASS_RU = {
    "cf4abea6-37b2-11d4-940f-008048da11f9": "Справочник",
    "061d872a-5787-460e-95ac-ed74ea3a3e84": "Документ",
    "f6a80749-5ad7-400b-8519-39dc5dff2542": "Перечисление",
    "82a1b659-b220-4d94-a9bd-14d757b95a48": "ПланВидовХарактеристик",
    "238e7e88-3c5f-48b2-8a3b-81ebbecb20ed": "ПланСчетов",
    "30b100d6-b29f-47ac-aec7-cb8ca8a54767": "ПланВидовРасчета",
    "13134201-f60b-11d5-a3c7-0050bae0a776": "РегистрСведений",
    "b64d9a40-1642-11d6-a3c7-0050bae0a776": "РегистрНакопления",
    "857c4a91-e5f4-4fac-86ec-787626f1c108": "ПланОбмена",
    "fcd3404e-1523-48ce-9bc0-ecdb822684a1": "БизнесПроцесс",
    "3e63355c-1378-4953-be9b-1deb5fb6bec5": "Задача",
}
# платформенные типы, которые встречаются в реквизитах обработок
_PLATFORM_TYPES = {
    "acf6192e-81ca-46ef-93a6-5a6968b78663": "ТаблицаЗначений",
    "e603c0f2-92fb-4d47-8f38-a44a381cf235": "ДеревоЗначений",
}


def _brace_header(node: Any) -> tuple[str, str, str] | None:
    """(uuid, Имя, Синоним) из первого {3,{1,0,uuid},"Имя",{1,"ru","Синоним"},…} внутри узла."""
    from tools.brace import Str, is_guid, walk

    for lst in walk(node):
        # «3,{1,0,uuid}» — формат 8.3, «0,{0,0,uuid}» — формат с версией 216
        if (len(lst) >= 4 and lst[0] in ("3", "0") and isinstance(lst[1], list) and len(lst[1]) == 3
                and lst[1][1] == "0" and is_guid(lst[1][2]) and isinstance(lst[2], Str)):
            syn = ""
            if isinstance(lst[3], list) and len(lst[3]) >= 3 and lst[3][1] == "ru":
                syn = str(lst[3][2])
            return str(lst[1][2]), str(lst[2]), syn
    return None


@functools.lru_cache(maxsize=1)
def _platform_type_table() -> dict[str, str]:
    """uuid платформенного типа → имя (data/platform_types.json строит scripts/platform_types_map.py по эталонной обработке)."""
    try:
        raw = json.loads((Path(__file__).parent / "data" / "platform_types.json").read_text("utf-8"))
    except (OSError, ValueError):
        return dict(_PLATFORM_TYPES)
    return {**_PLATFORM_TYPES, **{k: v for k, v in raw.items() if isinstance(v, str) and v}}


def _brace_type(node: Any, refs: dict[str, str], unknown: list[str] | None = None) -> str:
    """Тип реквизита из {"Pattern",{"S",50},{"#",uuid},…} (составной — через «, »); неопознанные uuid — в unknown."""
    from tools.brace import walk

    pat = next((x for x in walk(node) if x and x[0] == "Pattern"), None)
    if not pat:
        return ""
    if len(pat) == 1:
        return "Произвольный"
    out: list[str] = []
    for t in pat[1:]:
        if not isinstance(t, list) or not t:
            continue
        k = str(t[0])
        if k == "S":
            n = str(t[1]) if len(t) > 1 else "0"
            out.append(f"Строка({n})" if n not in ("0", "") else "Строка")
        elif k == "N":
            out.append(f"Число({t[1]},{t[2]})" if len(t) > 2 else "Число")
        elif k == "D":
            q = str(t[1]) if len(t) > 1 else ""
            out.append({"D": "Дата", "T": "Время"}.get(q, "ДатаВремя"))
        elif k == "B":
            out.append("Булево")
        elif k == "#" and len(t) > 1:
            u = str(t[1])
            nm = refs.get(u) or _platform_type_table().get(u) or ""
            if not nm:
                if unknown is not None:
                    unknown.append(u)
                nm = f"Тип({u[:8]})"  # платформенный тип без расшифровки — виден идентификатор, а не пропуск
            out.append(nm)
    out = [x for x in out if x]
    return ", ".join(dict.fromkeys(out))


def _copyinfo_refs(texts: list[tuple[str, str]], own: dict[str, str]) -> dict[str, str]:
    """uuid типа → «Справочник.Номенклатура» (copyinfo: объект CF + карта тип→объект)."""
    body = next((b for r, b in texts if r.rsplit("/", 1)[-1] == "copyinfo"), "")
    objs = {
        u: (f"{_MD_CLASS_RU[c]}.{n}" if c in _MD_CLASS_RU else n)
        for u, c, n in re.findall(r'\{([0-9a-f-]{36}),\1,1,\s*\{([0-9a-f-]{36}),"([^"]+)"\}', body)
    }
    refs: dict[str, str] = {}
    for tu, ou, _flag in re.findall(r"\{([0-9a-f-]{36}),([0-9a-f-]{36}),([01])\}", body):
        nm = objs.get(ou) or own.get(ou)
        if nm:
            refs[tu] = nm
    return refs


def external_structure(parsed: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Реквизиты и табличные части внешней обработки/отчёта из основного потока метаданных
    (внутренний формат: часть «Файл.epf/<uuid>» без суффикса). Кэшируется в parsed["structure"].
    """
    from tools.brace import parse

    parsed = parsed if parsed is not None else (_CACHE.get("parsed") or {})
    if isinstance(parsed.get("structure"), dict):
        return parsed["structure"]
    texts = [(r.replace("\\", "/"), b) for r, b in parsed.get("_texts") or []]
    uuid_leaf = re.compile(r"/[0-9a-f-]{36}$")
    cands = [(r, b) for r, b in texts if uuid_leaf.search(r) and any(c in b for c in _MD_ATTRS + _MD_TSS)]
    out: dict[str, Any] = {"name": "", "synonym": "", "attributes": [], "tabular_sections": []}
    if not cands:
        parsed["structure"] = out
        return out
    rel, body = max(cands, key=lambda x: len(x[1]))
    try:
        tree = parse(body)
    except Exception:
        parsed["structure"] = out
        return out
    hdr = _brace_header(tree)
    if hdr:
        out["name"], out["synonym"] = hdr[1], hdr[2]
        # сразу за заголовком объекта — uuid основной формы
        m = re.search(rf'"{re.escape(hdr[1])}",\s*\{{1,"ru","[^"]*"\}}[^{{}}]*\}}\s*\}},([0-9a-f-]{{36}}),', body)
        if m:
            out["default_form_uuid"] = m.group(1)
    from tools.brace import is_guid, walk

    def coll(node: Any, classes: tuple[str, ...]) -> list[Any]:
        """Элементы коллекции {cls,N,эл1,…,элN}, только непосредственно в node (без вложенных ТЧ)."""
        for lst in node if isinstance(node, list) else []:
            if isinstance(lst, list) and len(lst) >= 2 and lst[0] in classes:
                return [x for x in lst[2:] if isinstance(x, list)]
        return []

    def ts_columns(ts: Any) -> list[Any]:
        """Колонки ТЧ: известный класс коллекции, иначе — первая коллекция {guid,N,…} из N элементов с заголовком."""
        inner = next((x for x in walk(ts) if any(isinstance(y, list) and y and y[0] in _MD_TS_ATTRS for y in x)), [])
        if inner:
            return coll(inner, _MD_TS_ATTRS)
        for lst in walk(ts):
            if (len(lst) >= 3 and is_guid(lst[0]) and str(lst[1]).isdigit() and int(lst[1]) == len(lst) - 2
                    and all(isinstance(x, list) and _brace_header(x) for x in lst[2:])):
                return list(lst[2:])
        return []

    # первый уровень объекта: список, где есть коллекции реквизитов/ТЧ
    top = next((x for x in walk(tree) if any(isinstance(y, list) and y and y[0] in _MD_ATTRS + _MD_TSS for y in x)), [])
    own: dict[str, str] = {}
    raw_attrs = coll(top, _MD_ATTRS)
    raw_ts = coll(top, _MD_TSS)
    for it in raw_ts:
        h = _brace_header(it)
        if h:
            own[h[0]] = f"ТабличнаяЧасть.{h[1]}"
    refs = _copyinfo_refs(texts, own)
    for it in raw_attrs:
        h = _brace_header(it)
        if h:
            unk: list[str] = []
            out["attributes"].append({"name": h[1], "synonym": h[2], "type": _brace_type(it, refs, unk),
                                      **({"type_ids": unk} if unk else {})})
    for it in raw_ts:
        h = _brace_header(it)
        if not h:
            continue
        cols = []
        for c in ts_columns(it):
            ch = _brace_header(c)
            if ch:
                unk = []
                cols.append({"name": ch[1], "synonym": ch[2], "type": _brace_type(c, refs, unk),
                             **({"type_ids": unk} if unk else {})})
        out["tabular_sections"].append({"name": h[1], "synonym": h[2], "columns": cols})
    out["source_part"] = rel
    parsed["structure"] = out
    return out


def _attr_brief(a: dict[str, Any]) -> str:
    """«Имя «Синоним»: Тип» — синоним опущен, если совпадает с именем."""
    syn = a.get("synonym") or ""
    nm = a["name"] + (f" «{syn}»" if syn and syn.replace(" ", "").lower() != a["name"].lower() else "")
    return nm + (f": {a['type']}" if a.get("type") else "")


TEMPLATES_PART = "Макеты/описание"


def templates_text(tds: list[dict[str, Any]]) -> str:
    """Все макеты целиком: области (строки), параметры и подписи — без сокращений (для read_code/search_code)."""
    lines: list[str] = []
    for t in tds:
        syn = f" «{t['synonym']}»" if t.get("synonym") else ""
        if t.get("kind"):
            lines.append(f"Макет {t['name']}{syn}: {t['kind']}" + (", пустой (содержимого нет)" if t.get("empty") else ""))
            continue
        lines.append(f"Макет {t['name']}{syn}: строк {t.get('rows', 0)}, областей {len(t['areas'])}")
        for a in t["areas"]:
            head = f"  Область «{a['name']}» (строки {a['row_from']}–{a['row_to']})"
            if a.get("parameters"):
                head += ". Параметры: " + ", ".join(a["parameters"])
            if a.get("texts"):
                head += ". Подписи: " + " | ".join(a["texts"])
            lines.append(head)
        if t.get("parameters"):
            lines.append("  Все параметры макета: " + ", ".join(t["parameters"]))
    return chr(10).join(lines)


STRUCTURE_PART = "Структура/реквизиты"
_STRUCTURE_INLINE = 4000  # символов структуры в самом ответе; остальное — постранично через read_code(path=STRUCTURE_PART)


def structure_text(st: dict[str, Any]) -> str:
    """Реквизиты и табличные части целиком, одна запись на строку (для read_code/search_code)."""
    lines = [f"Реквизит {_attr_brief(a)}" for a in st.get("attributes") or []]
    for t in st.get("tabular_sections") or []:
        syn = f" «{t['synonym']}»" if t.get("synonym") else ""
        lines.append(f"Табличная часть {t['name']}{syn} ({len(t['columns'])} колонок)")
        lines += [f"  Колонка {t['name']}.{_attr_brief(c)}" for c in t["columns"]]
    return chr(10).join(lines)


def structure_inline(st: dict[str, Any], budget: int = _STRUCTURE_INLINE) -> dict[str, Any]:
    """Структура для ответа в пределах budget; что не влезло — явно посчитано и отсылает к read_code, не пропадает молча."""
    attrs = [_attr_brief(a) for a in st.get("attributes") or []]
    tss = st.get("tabular_sections") or []
    used = sum(len(a) + 4 for a in attrs)
    shown_attrs = attrs
    if used > budget:
        acc, shown_attrs = 0, []
        for a in attrs:
            if acc + len(a) + 4 > budget:
                break
            shown_attrs.append(a)
            acc += len(a) + 4
        used = acc
    ts_out: list[dict[str, Any]] = []
    hidden_cols = 0
    line_no = len(attrs) + 1  # строка заголовка ТЧ в structure_text (нумерация read_code с 1)
    for t in tss:
        line_no_t, line_no = line_no, line_no + 1 + len(t["columns"])
        cols_all = [_attr_brief(c) for c in t["columns"]]
        room = max(0, budget - used)
        cols, acc = [], 0
        for c in cols_all:
            if acc + len(c) + 4 > room:
                break
            cols.append(c)
            acc += len(c) + 4
        used += acc + 40
        hidden_cols += len(cols_all) - len(cols)
        ts_out.append({"name": t["name"], "synonym": t.get("synonym") or "", "columns_total": len(cols_all),
                       "columns": cols, **({"read_from_line": line_no_t} if len(cols) < len(cols_all) else {})})
    out: dict[str, Any] = {"attributes": shown_attrs, "attributes_total": len(attrs), "tabular_sections": ts_out}
    if len(shown_attrs) < len(attrs) or hidden_cols:
        out["structure_note"] = (
            f"Показано не всё: реквизитов {len(shown_attrs)} из {len(attrs)}, колонок ТЧ скрыто {hidden_cols}. "
            f"Остальное целиком: read_code(path='{STRUCTURE_PART}', start_line=1) окнами по 200 строк."
        )
    return out


def read_external_module(
    path: str, start_line: int = 1, max_lines: int = 80
) -> dict[str, Any] | None:
    """Окно строк модуля из разобранного .epf/.erf/.cfe (кэш). None — путь не внешний."""
    p = (path or "").replace("\\", "/").strip().strip('"')
    if not p:
        return None

    def _find() -> tuple[str, str | None, dict[str, Any]]:
        parsed_ = _CACHE.get("parsed") or {}
        for r, b in parsed_.get("_texts") or []:
            rn = r.replace("\\", "/")
            if rn == p or rn.endswith(p) or p.endswith(rn):
                return rn, b, parsed_
        return p, None, parsed_

    rel, body, parsed = _find()
    file_spec, inner = split_external_path(p)
    if body is None and inner and not _cached_matches(file_spec):
        # кэш пуст/другой файл (рестарт сервера) — подгрузить файл с диска или из Open WebUI
        if ensure_external_loaded(file_spec)[0]:
            rel, body, parsed = _find()
    texts: list[tuple[str, str]] = list(parsed.get("_texts") or [])
    if body is None:
        if re.search(r"\.(epf|erf|cfe)(/|$)", p, flags=re.I) or p.startswith("СКД/"):
            return {
                "ok": False,
                "source": "external_file",
                "error": f"В разобранном внешнем файле нет модуля: {path}",
                "hint": "Вызови inspect_external_processing(path=файл) и возьми modules[].path. "
                "Не ищи в Qdrant — это не CF.",
                "available": [r for r, _ in texts][:40],
            }
        return None
    lines = body.splitlines()
    start = max(1, int(start_line or 1))
    n = max(1, min(int(max_lines or 80), 200))
    end = min(len(lines), start + n - 1)
    return {
        "ok": True,
        "source": "external_file",
        "path": rel,
        "file": parsed.get("file"),
        "start_line": start,
        "end_line": end,
        "total_lines": len(lines),
        "lines": [{"line": start + i, "text": ln[:500]} for i, ln in enumerate(lines[start - 1 : end])],
        "truncated": end < len(lines),
        **({"next_start_line": end + 1,
            "next_call": {"tool": "read_code", "args": {"path": rel, "start_line": end + 1, "max_lines": n}},
            "not_all_shown": f"Показаны строки {start}–{end} из {len(lines)}: продолжение — read_code со start_line={end + 1}."}
           if end < len(lines) else {}),
    }


_OVERVIEW_CAP = 3200


def _compact_result(parsed: dict[str, Any]) -> dict[str, Any]:
    """Ответ tool в бюджет контекста: находки и сводка — первыми, полный текст — через parts."""
    out: dict[str, Any] = {
        k: parsed.get(k)
        for k in ("ok", "source", "kind", "name", "synonym", "file_name", "platform", "summary")
    }
    out["dcs_findings"] = [
        {
            "severity": f["severity"],
            "code": f["code"],
            "line": f.get("line") or None,
            "message": f["message"][:420],
            "snippet": f.get("snippet", "")[:140],
        }
        for f in parsed.get("dcs_findings") or []
    ]
    ov = parsed.get("dcs_overview") or ""
    if len(ov) > _OVERVIEW_CAP:
        ov = ov[:_OVERVIEW_CAP] + "\n…(обрезано; полностью: read_code(path='СКД/описание'))"
    if ov:
        out["dcs_overview"] = ov
    if parsed.get("dcs_extra"):  # другие СКД файла: кратко, подробно — read_code(path=overview_path)
        out["dcs_extra"] = parsed["dcs_extra"]
    for k in ("modules", "attributes", "tabular_sections", "forms", "templates", "cf_refs"):
        if parsed.get(k):
            out[k] = parsed[k]
    cached = _CACHE.get("parsed") or {}
    if cached.get("file_name") == parsed.get("file_name") and (parsed.get("attributes") or parsed.get("tabular_sections")):
        si = structure_inline(external_structure(cached))
        if si["attributes"] or si["tabular_sections"]:  # у XML-выгрузки внутренней структуры нет — остаются имена из XML
            out.update(si)
    if parsed.get("forms_ui"):
        out["forms_ui"] = [
            {
                "form": f["form"], "synonym": f["synonym"], "type": f["type"],
                "buttons": [f'{b["caption"]} → {b["handler"]}' for b in f["buttons"]][:25],
                "event_handlers": f["event_handlers"][:15],
                "captions": f["captions"][:40],
                "form_path": f["form_path"],
            }
            for f in parsed["forms_ui"]
        ]
    if parsed.get("templates_detail"):
        out["templates_detail"] = [
            {
                "template": t["name"], "synonym": t["synonym"], "path": t["path"],
                **({"kind": t["kind"], "empty": t["empty"]} if t.get("kind") else {}),
                "areas": [
                    {"area": a["name"], "rows": f'{a["row_from"]}-{a["row_to"]}',
                     "parameters": a["parameters"][:30], "texts": a["texts"][:8]}
                    for a in t["areas"]
                ],
                "parameters": t["parameters"],
            }
            for t in parsed["templates_detail"]
        ]
    form = parsed.get("form") or {}
    if form:
        out["form"] = {
            "type": form.get("type"),
            "data_path": form.get("data_path"),
            "captions": (form.get("captions") or [])[:25],
            "elements": (form.get("elements") or [])[:40],
        }
    out["parts"] = [
        p["path"].split("/", 1)[-1] if "/" in p["path"] else p["path"]
        for p in parsed.get("parts") or []
    ]
    if not parsed.get("dcs"):
        out["queries"] = (parsed.get("queries") or [])[:15]
    out["from_cache"] = parsed.get("from_cache", False)
    return out


def check_cf_compatibility(cf_refs: list[str], form: dict[str, Any]) -> dict[str, Any]:
    """
    Подходит ли внешняя обработка к ЭТОЙ базе: объекты метаданных из её кода/запросов
    сверяются с каталогом выгрузки текущей CF. «Нет в базе» — явный признак обработки от другой конфигурации.
    """
    try:
        from tools.object_catalog import catalog_by_full_name, load_catalog
    except Exception:
        return {}
    cat = catalog_by_full_name()
    if not cat:
        return {"checked": False, "note": "Каталог объектов текущей CF не найден (нет выгрузки)."}
    by_name = {}
    for r in load_catalog():
        by_name.setdefault(str(r.get("name") or "").lower(), []).append(r["full_name"])
    present, missing, unknown = [], [], []
    for ref in cf_refs or []:
        ref = str(ref)
        if "." in ref:
            cls, nm = ref.split(".", 1)
            # «ДокументСсылка.X» / «Документы.X» → «Документ.X»
            cls = re.sub(r"(Ссылка|Объект|Менеджер|Выборка|Список)$", "", cls)
            cls = {"Документы": "Документ", "Справочники": "Справочник", "РегистрыСведений": "РегистрСведений",
                   "РегистрыНакопления": "РегистрНакопления", "Перечисления": "Перечисление"}.get(cls, cls)
            fn = f"{cls}.{nm}"
            (present if fn in cat else missing).append(fn)
        else:
            # голое имя (из кода): есть ли объект с таким именем в любом классе
            if ref.lower() in by_name:
                present.append(by_name[ref.lower()][0])
            elif re.search(r"[a-zа-яё][A-ZА-ЯЁ]", ref):
                unknown.append(ref)
    form_type = str((form or {}).get("type") or "")
    verdict = "ok"
    if missing:
        verdict = "other_configuration" if len(missing) >= max(1, len(present) // 3) else "partially_missing"
    return {
        "checked": True,
        "verdict": verdict,
        "present": present[:40],
        "missing_in_this_base": missing[:40],
        "unresolved_names": unknown[:20],
        "form_type": form_type or None,
        "note": (
            "missing_in_this_base — объекты, на которые ссылается обработка, но которых нет в текущей конфигурации: "
            "при запуске будут ошибки «Тип не определён / Поле не найдено». "
            + ("Форма обычная (не управляемая) — в тонком/веб-клиенте не откроется. " if form_type == "ordinary" else "")
        ),
    }


def inspect_external_processing(
    path: str = "",
    request: str = "",
    max_hits: int = 12,
) -> dict[str, Any]:
    """
    Разобрать скинутую внешнюю обработку/отчёт и ответить по её коду.

    path — .epf/.erf/.cfe или папка выгрузки.
    request — вопрос («как работает», «где запрос», «какие реквизиты»).
    Не ходит в Qdrant CF. Не выполняет обработку в базе.
    """
    raw_req = (request or "").strip()
    full_spec = (path or "").strip().strip('"') or extract_external_path(raw_req)
    # «Файл.epf/<uuid>.0/module» — часть внутри уже разобранного файла
    pth, inner = split_external_path(full_spec)
    found = None
    if pth and _cached_matches(pth):
        found = Path(str(_CACHE["parsed"].get("file") or pth))
    owui_err = ""
    if found is None and pth:
        # локальная копия или вложение чата Open WebUI — что новее
        found, owui_err = locate_external(pth)
    if found is None and not pth:
        # имени нет: свежее вложение чата, иначе последний разобранный файл
        cached_file = str((_CACHE.get("parsed") or {}).get("file") or "")
        found, owui_err = _newer_attachment("", Path(cached_file) if cached_file else None)
        if found is None and cached_file:
            pth = cached_file
            found = resolve_external_file(pth)
    if found is not None:
        pth = str(found)
    elif pth and owui_err:
        return {"ok": False, "error": f"Файл «{pth}» не найден. {owui_err}"}
    if not pth:
        return {
            "ok": False,
            "error": "Нет пути к файлу",
            "looked_in": [str(d) for d in _search_dirs()],
            "hint": (
                "Вложи .epf в чат или укажи имя файла. "
                "Tool сам ищет в папке проекта, uploads/, Desktop, Downloads. "
                "inspect_external_processing(path='Форма для СПОТ.epf', request=…)."
            ),
            "prefer_tool": "inspect_external_processing",
        }

    parsed = parse_external_file(pth)
    if not parsed.get("ok"):
        return parsed

    full = _CACHE.get("parsed") if _CACHE.get("parsed") else parsed
    texts: list[tuple[str, str]] = []
    if isinstance(full, dict):
        texts = list(full.get("_texts") or [])

    q = raw_req
    # не искать по самому пути
    for s in (full_spec, pth):
        if s and q:
            q = q.replace(s, " ").strip()
    q = re.sub(r"\.(epf|erf|cfe)\b", " ", q, flags=re.I).strip()

    if inner and texts:
        inn = inner.replace("\\", "/").lower()
        focused = [(r, b) for r, b in texts if r.replace("\\", "/").lower().endswith(inn) or inn in r.lower()]
        if focused:
            texts = focused

    hits: list[dict[str, Any]] = []
    if q and texts:
        hits = _search_texts(texts, q, limit=max(4, min(int(max_hits or 12), 24)))

    if inner.endswith("form"):
        ui = [f for f in (full or {}).get("forms_ui") or [] if f["form_path"].endswith(inner) or inner.endswith(f["form_path"])]
        if ui and ui[0].get("type") == "ordinary" and _CONTROLS_Q.search(raw_req):
            ctl = _controls_answer(full or parsed, ui[0], raw_req)
            if ctl:
                return ctl
        if ui:
            return _fit_budget({"ok": True, "source": "external_file", "file_name": parsed.get("file_name"),
                                "form_ui": dict(ui[0]),
                                "action_for_agent": "Интерфейс формы: buttons (подпись на экране → процедура), "
                                "captions (подписи полей/страниц/колонок по порядку), event_handlers. "
                                "Тело процедуры — proc_index(procedure=…) → read_code."})

    if inner and texts:
        # вопрос по конкретной части файла — без общего обзора, только эта часть
        focus = {
            "ok": True,
            "source": "external_file",
            "file_name": parsed.get("file_name"),
            "parts": [r for r, _ in texts],
            "procedures": [
                {"line": h["line"], "signature": h["snippet"][:160]}
                for h in _search_texts(texts, "Процедура Функция", limit=400)
                if re.match(r"(?i)\s*(Процедура|Функция)\s", h["snippet"])
            ][:60],
            "hits": [{**h, "snippet": h["snippet"][:200]} for h in hits],
            "action_for_agent": "Тело — read_code(path=parts[0], start_line=line, max_lines≤200). "
            "Вызовы/объявления в файле — proc_index(procedure=Имя).",
        }
        if not hits and q:
            focus["note"] = "Совпадений с фразой в этой части нет — смотри procedures."
        return _fit_budget(focus)

    out = _compact_result(parsed)
    if _UI_INTENT.search(raw_req.lower()) and isinstance(full, dict) and full.get("forms_ui"):
        # инструкция пользователю: что на экране важнее обзора запросов/СКД
        forms = sorted(full["forms_ui"], key=lambda f: (f["form"] not in ("ФормаОсновная", "Форма"), -len(f["buttons"])))
        main, rest = forms[0], forms[1:]
        out["forms_ui"] = [
            {**{k: main[k] for k in ("form", "synonym", "type", "form_path", "event_handlers")},
             "main": True,
             # вкладки с полями точнее плоского списка подписей; подписи — только если вкладок нет
             **({"tabs": main["tabs"]} if main.get("tabs") else {} if main.get("elements") else {"captions": main["captions"]}),
             **({"elements": main["elements"]} if main.get("elements") else {}),
             **({"standard_buttons": main["standard_buttons"]} if main.get("standard_buttons") else {}),
             "buttons": [f'{b["caption"]} → {b["handler"]}' for b in main["buttons"]]}
        ] + [
            # остальные формы кратко; подробно — inspect_external_processing(path=form_path)
            {"form": f["form"], "synonym": f["synonym"], "form_path": f["form_path"],
             "buttons": [b["caption"] for b in f["buttons"]][:20]}
            for f in rest
        ]
        for k in ("queries", "cf_refs", "templates"):
            out.pop(k, None)
        if out.get("dcs_overview"):
            out["dcs_overview"] = out["dcs_overview"][:600] + "\n…(полностью: read_code(path='СКД/описание'))"
    comp = (full or {}).get("compatibility") if isinstance(full, dict) else None
    if comp is None:
        comp = check_cf_compatibility(list(parsed.get("cf_refs") or []), parsed.get("form") or {})
    if comp:
        out["compatibility"] = comp
    out["hits"] = [{**h, "snippet": h["snippet"][:160]} for h in hits]
    out["action_for_agent"] = (
        "Отвечай ТОЛЬКО по этому файлу. forms_ui — интерфейс форм (buttons: подпись на экране → процедура; "
        "captions — подписи полей/страниц/колонок): по нему пиши инструкцию, не спрашивай пользователя, что на экране; "
        "подробно одна форма — inspect_external_processing(path=form_path). dcs_findings — уже найденные ошибки СКД/запроса "
        "(объясни, начиная с error); dcs_overview — поля/роли/ресурсы/параметры/отборы/"
        "структура/шаги пакета. Полный текст: read_code(path=parts[i]), напр. "
        "«СКД/описание», «СКД/<набор>/запрос». Модулей у .erf может не быть — норма. "
        "Не Qdrant/CF, не правил файл; рекомендация — «НЕ применено»."
    )
    if not hits and q:
        out["note"] = "Точных совпадений с фразой нет — отвечай по dcs_findings/dcs_overview/modules."
    keep = {k: out[k] for k in ("attributes", "attributes_total", "tabular_sections", "structure_note") if k in out}
    res = _fit_budget(out)
    res.update(keep)  # структура уже уложена в свой бюджет с пометкой «показано N из M»: общий ужиматель её не режет молча
    return res


_UI_INTENT = re.compile(r"инструкц|как пользоват|как работать|кнопк|интерфейс|на экране|форм[аеуы]|шаг|свод|руководств|памятк")


_CONTROL_TYPES = ("Флажок", "ПолеВвода", "ПолеВыбора", "Переключатель", "ПолеСписка")
# вопрос «что делает флажок / какие варианты / инструкция» — нужны подробности элементов, а не скелет формы
_CONTROLS_Q = re.compile(r"флаж|чекбокс|галоч|переключ|вариант|выбор|инструкц|настро|пол[еяюей]\b|что\s+делает", re.I)


def _proc_body(mod_lines: list[str], proc: str) -> tuple[int, int]:
    """(начало, конец) процедуры в модуле, 0-based; (-1, -1) — не найдена."""
    decl = re.compile(rf"^\s*(?:Процедура|Функция|Procedure|Function)\s+{re.escape(proc)}\s*\(", re.I)
    for i, ln in enumerate(mod_lines):
        if decl.match(ln):
            for j in range(i + 1, len(mod_lines)):
                if re.match(r"^\s*(КонецПроцедуры|КонецФункции|EndProcedure|EndFunction)\b", mod_lines[j], re.I):
                    return i, j
            return i, len(mod_lines) - 1
    return -1, -1


def form_controls(form_text: str, mod: str, tab: str = "") -> list[dict[str, Any]]:
    """
    Поля и флажки обычной формы для инструкции пользователю: вкладка, подпись, варианты выбора
    (СписокВыбора.Добавить в модуле), что выполняется при изменении и где значение читается в коде.
    """
    from tools.ordinary_form import ordinary_form_layout

    lay = ordinary_form_layout(form_text, mod)
    lines = (mod or "").splitlines()
    out: list[dict[str, Any]] = []
    for el in lay.get("elements") or []:
        if el.get("type") not in _CONTROL_TYPES:
            continue
        page = str(el.get("page") or "")
        if tab and tab.lower() not in page.lower():
            continue
        name = el["name"]
        item: dict[str, Any] = {"tab": page or None, "caption": el.get("caption") or name, "name": name, "type": el["type"]}
        if el.get("value_type"):
            item["value_type"] = el["value_type"]
        ch_re = re.compile(rf"\b{re.escape(name)}\.СписокВыбора\.Добавить\(\s*([^,;]+?)\s*(?:,\s*\"([^\"]*)\")?\s*\)", re.I)
        choices = []
        for ln in lines:
            m = ch_re.search(ln)
            if m:
                val = m.group(2) or m.group(1).split(".")[-1].strip()
                if val and val not in choices:
                    choices.append(val)
        if choices:
            item["choices"] = choices[:15]
        skip: set[int] = set()
        handler = next(iter((el.get("events") or {}).values()), "")
        if handler:
            a, b = _proc_body(lines, handler)
            if a >= 0:
                skip = set(range(a, b + 1))
                body = [f"{k + 1}: {lines[k].strip()}" for k in range(a + 1, b) if lines[k].strip()]
                item["on_change"] = body[:8]
        # где значение читается в остальном коде (условия расчёта и т.п.)
        use_re = re.compile(rf"(?<![\wА-Яа-яЁё]){re.escape(name)}(?![\wА-Яа-яЁё])")
        used = [k for k, ln in enumerate(lines) if k not in skip and use_re.search(ln)
                and not ln.strip().startswith("//") and "СписокВыбора" not in ln]
        used.sort(key=lambda k: 0 if re.search(r"\bЕсли\b|\bИначеЕсли\b", lines[k]) else 1)
        if used:
            item["used_in"] = [f"{k + 1}: {lines[k].strip()[:200]}" for k in used[:4]]
            item["used_total"] = len(used)
        out.append(item)
    return out


def _controls_answer(parsed: dict[str, Any], ui: dict[str, Any], request: str, limit: int = 7600) -> dict[str, Any] | None:
    """Ответ «поля и флажки формы» в бюджет tool; вкладка из запроса — только она, иначе по порядку с продолжением."""
    import json

    texts = {r.replace("\\", "/"): b for r, b in parsed.get("_texts") or []}
    form_text = texts.get(str(ui.get("form_path") or "").replace("\\", "/"), "")
    mod = texts.get(str(ui.get("module_path") or "").replace("\\", "/"), "")
    if not form_text:
        return None
    tabs = [str(t.get("tab") or "") for t in ui.get("tabs") or [] if t.get("tab")]
    want = next((t for t in sorted(tabs, key=len, reverse=True) if t.lower() in request.lower()), "")
    try:
        items = form_controls(form_text, mod, want)
    except Exception:  # noqa: BLE001
        return None
    if not items:
        return None
    m_off = re.search(r"(?:начиная\s+)?с\s+(\d+)\b", request, re.I)
    start = max(0, int(m_off.group(1)) - 1) if m_off else 0
    total = len(items)
    items = items[start:]
    out: dict[str, Any] = {
        "ok": True, "source": "external_file", "file_name": parsed.get("file_name"), "form": ui.get("form"),
        "tab_filter": want or None, "controls": [dict(it) for it in items], "controls_total": total,
        "from": start + 1,
        "action_for_agent": (
            "Поля и флажки формы по вкладкам. choices — варианты выбора (из кода формы); on_change — что выполняется "
            "при изменении; used_in — где значение читается в расчёте. Описывай пользователю только по этим фактам; "
            "непонятную строку — read_code(path=module_path, start_line=номер)."
        ),
        "module_path": ui.get("module_path"),
    }

    def size() -> int:
        return len(json.dumps(out, ensure_ascii=False, default=str))

    for n_body, n_used in ((5, 3), (3, 2), (2, 1)):
        if size() <= limit:
            break
        for it in out["controls"]:
            if "on_change" in it:
                it["on_change"] = it["on_change"][:n_body]
            if "used_in" in it:
                it["used_in"] = it["used_in"][:n_used]
    while size() > limit and len(out["controls"]) > 1:
        out["controls"].pop()
    shown = len(out["controls"])
    if shown < len(items):
        nxt = start + shown + 1
        rest = list(dict.fromkeys(str(it.get("tab") or "") for it in items[shown:]))
        out["more"] = (f"Показаны {start + 1}–{start + shown} из {total}. Продолжение — тот же вызов, в конце request "
                       f"добавь «с {nxt}»" + ("" if want else "; или назови вкладку: " + ", ".join(t for t in rest if t)))
    return out


def _fit_budget(out: dict[str, Any], limit: int = 7600) -> dict[str, Any]:
    """Ужать до бюджета tool (config tool_result_max_chars), сохранив находки."""
    import json

    def size() -> int:
        return len(json.dumps(out, ensure_ascii=False, default=str))

    note = "\n…(обрезано; полностью: read_code(path='СКД/описание'))"
    steps = (
        ("tpl", 1), ("hits", 6), ("overview", 2200), ("tpl", 2), ("msg", 300), ("hits", 3), ("tpl", 3),
        ("overview", 1400), ("msg", 200), ("form", 0), ("previews", 0), ("parts", 0),
        ("modules", 0), ("compat", 0), ("findings", 25), ("ui", 20), ("ui", 8), ("hits", 0), ("overview", 700),
        ("msg", 120), ("findings", 10), ("ui", 0),
    )
    cut: list[str] = []  # что ужато ради контекста — модель должна знать, что это не всё
    for kind, n in steps:
        if size() <= limit:
            break
        if kind in ("tpl", "ui", "modules", "previews", "parts", "form") and kind not in cut:
            cut.append(kind)
        if kind == "hits":
            if "hits" in out:
                out["hits"] = out["hits"][:n]
        elif kind == "overview" and out.get("dcs_overview"):
            ov = out["dcs_overview"].split(note)[0]
            if len(ov) > n:
                out["dcs_overview"] = ov[:n] + note
        elif kind == "msg":
            for f in out.get("dcs_findings") or []:
                f["message"] = f["message"][: (560 if f["severity"] == "error" else n)]
                f["snippet"] = f["snippet"][:80]
        elif kind == "findings" and len(out.get("dcs_findings") or []) > n:
            # ошибки СКД — первыми; остальное считаем, но не перечисляем
            fs = out["dcs_findings"]
            order = {"error": 0, "warning": 1}
            out.setdefault("dcs_findings_total", len(fs))
            out["dcs_findings"] = sorted(fs, key=lambda f: order.get(f.get("severity"), 2))[:n]
        elif kind == "tpl" and out.get("templates_detail"):
            # макеты: подробности по областям — первыми под нож; имена и параметры остаются, полностью — read_code(path=…)
            for t in out["templates_detail"]:
                if n == 1:
                    for a in t["areas"]:
                        a["parameters"], a["texts"] = a["parameters"][:8], a["texts"][:2]
                    t["parameters"] = t["parameters"][:20]
                elif n == 2:
                    for a in t["areas"]:
                        a.pop("texts", None)
                        a["parameters"] = a["parameters"][:4]
                    t["parameters"] = t["parameters"][:10]
                else:
                    t["areas"] = [a["area"] for a in t["areas"]]
                    t.pop("parameters", None)
        elif kind == "form":
            out.pop("form", None)
        elif kind == "ui":
            for f in (out.get("forms_ui") or []) + ([out["form_ui"]] if out.get("form_ui") else []):
                f["captions"] = (f.get("captions") or [])[:n]
                f["event_handlers"] = (f.get("event_handlers") or [])[: n // 2]
                for t in f.get("tabs") or []:
                    t["items"] = (t.get("items") or [])[: max(3, n // 2)]
                if f.get("elements"):
                    f["elements"] = f["elements"][: max(3, n // 2)]
                if n == 0:
                    f["buttons"] = (f.get("buttons") or [])[:10]
        elif kind == "modules" and out.get("modules"):
            # имена процедур есть в forms_ui/procedures; модулям оставляем путь и размер
            out["modules"] = [{"path": m.get("path"), "proc_count": m.get("proc_count")} for m in out["modules"]]
        elif kind == "compat" and isinstance(out.get("compatibility"), dict):
            c = out["compatibility"]
            out["compatibility"] = {"verdict": c.get("verdict"),
                                    "missing_in_this_base": (c.get("missing_in_this_base") or [])[:10]}
        elif kind == "previews":
            for m in out.get("modules") or []:
                m.pop("preview", None)
                m["proc_names"] = (m.get("proc_names") or [])[:10]
        elif kind == "parts":
            if "parts" in out:
                out["parts"] = [p for p in out["parts"] if p.endswith(("/module", "/form"))][:20]
            for k, n_ in (("queries", 5), ("procedures", 30), ("cf_refs", 15)):
                if k in out:
                    out[k] = out[k][:n_]
            if isinstance(out.get("compatibility"), dict):
                c = out["compatibility"]
                for k in ("present", "unresolved_names"):
                    c.pop(k, None)
                c["missing_in_this_base"] = (c.get("missing_in_this_base") or [])[:15]

    if size() > limit:
        # последний предохранитель: общий ужиматель списков/строк
        from tools.epf_tools import _fit

        out = _fit(out, limit)
        cut.append("lists")
    if cut:
        names = {"tpl": "макеты (templates_detail: области/параметры)", "ui": "интерфейс форм (forms_ui)",
                 "modules": "имена процедур модулей", "previews": "превью модулей", "parts": "список частей файла",
                 "form": "раскладка основной формы", "lists": "длинные списки"}
        out["not_all_shown"] = (
            "Ответ сокращён ТОЛЬКО для экономии контекста: " + ", ".join(names.get(k, k) for k in cut)
            + ". Это не всё: полный текст любой части — read_code(path=parts[i].path / 'Структура/реквизиты' / "
            "'Макеты/описание' (все макеты: области, параметры, подписи) / 'СКД/описание'), форма целиком — inspect_external_processing(path=form_path). "
            "Нужного нет среди показанного — открой следующую часть, а не отвечай «нет»."
        )
    return out
