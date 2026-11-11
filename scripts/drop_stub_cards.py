# -*- coding: utf-8 -*-
"""
Удалить пустые карточки синтаксис-помощника:
только заголовок + Раздел + «Имя в платформе» / «По-русски» +
«Карточка из синтаксис-помощника 1С» — без описания.

Также удалить сырые структуры 1С без нормального текста.
Пересобрать индекс.json.

python scripts/drop_stub_cards.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELP = ROOT / "knowledge" / "справка_платформы"

CYR = re.compile(r"[а-яА-ЯёЁ]")
STUB_PHRASE = "Карточка из синтаксис-помощника 1С"
RE_RU = re.compile(
    r'\{\s*"ru"\s*,\s*\d+\s*,\s*\d+\s*,\s*""\s*,\s*"([^"]*)"\s*\}', re.I
)
RE_EN = re.compile(
    r'\{\s*"en"\s*,\s*\d+\s*,\s*\d+\s*,\s*""\s*,\s*"([^"]*)"\s*\}', re.I
)

META_PREFIXES = (
    "Раздел:",
    "Источник:",
    "Имя в платформе:",
    "По-русски:",
    "English:",
    "EN:",
)


def body_lines(text: str) -> list[str]:
    out: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        if any(s.startswith(p) for p in META_PREFIXES):
            continue
        if s == STUB_PHRASE or s == STUB_PHRASE + ".":
            continue
        # markdown list bullets that are only the platform name line leftovers
        out.append(s)
    return out


def is_raw_struct(text: str) -> bool:
    if text.count("<html") + text.count("<HTML") > 0:
        return False
    if text.count("{") < 4:
        return False
    if RE_RU.search(text) or RE_EN.search(text):
        return True
    t = text.lstrip()
    if t.startswith("{1,") or re.match(r"^# [^\n]+\n+\{1,", text):
        return True
    return False


# Шаблоны «тела», которые не несут описания (сигнатуры/обломки конвертера)
_EMPTY_BODY_RE = re.compile(
    r"^(?:"
    r"КонецПроцедуры|EndProcedure|КонецФункции|EndFunction|"
    r"Async\s*\(\s*\)\s*;?|"
    r"Показать\s*\(\s*\)\s*;?|"
    r"[A-Za-z_][A-Za-z0-9_]*\s*\(\s*\)\s*;?"
    r")$",
    re.I,
)


def is_useless_stub(text: str) -> bool:
    """Нет полезного тела: только имя/раздел/шаблон или сырая структура."""
    if is_raw_struct(text):
        return True
    # Все карточки, помеченные конвертером структур — без HTML-описания.
    if STUB_PHRASE in text:
        return True
    body = body_lines(text)
    if not body:
        return True
    joined = " ".join(body)
    # только пунктуация / скобки / числа
    if not CYR.search(joined) and not re.search(r"[A-Za-z]{3,}", joined):
        return True
    # тело целиком — обломки структуры
    if body and all(
        re.fullmatch(r"[\{\}\[\]\(\),;:=\s\d\"'A-Za-z._-]+", ln) for ln in body
    ) and text.count("{") >= 2:
        return True
    # только КонецПроцедуры / Async() / пустые вызовы
    if body and all(_EMPTY_BODY_RE.match(ln) for ln in body):
        return True
    return False


def title_from_md(text: str, fallback: str) -> str:
    for line in text.splitlines()[:25]:
        line = line.strip()
        if line.startswith("#"):
            t = re.sub(r"\s+", " ", line.lstrip("#").strip().replace("\xa0", " "))
            if t and t not in ("•",) and not re.fullmatch(r"\d+", t):
                return t[:120]
    return fallback[:120]


def rebuild_index() -> int:
    """Полная пересборка — scripts/rebuild_help_index.py."""
    import subprocess
    import sys

    script = Path(__file__).resolve().parent / "rebuild_help_index.py"
    rc = subprocess.call([sys.executable, str(script)])
    if rc != 0:
        return 0
    idx = HELP / "индекс.json"
    if not idx.is_file():
        return 0
    try:
        data = json.loads(idx.read_text(encoding="utf-8"))
        return len(data) if isinstance(data, list) else 0
    except Exception:
        return 0


def main() -> int:
    if not HELP.is_dir():
        print(f"Нет папки: {HELP}")
        return 1

    deleted = 0
    kept = 0
    by_del: dict[str, int] = {}
    samples: list[str] = []

    for path in list(HELP.rglob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            path.unlink(missing_ok=True)
            deleted += 1
            continue

        if is_useless_stub(text):
            sec = path.parent.name
            by_del[sec] = by_del.get(sec, 0) + 1
            if len(samples) < 12:
                samples.append(str(path.relative_to(HELP)))
            path.unlink(missing_ok=True)
            deleted += 1
        else:
            kept += 1

    print(f"Удалено пустых карточек: {deleted}")
    print(f"Осталось статей: {kept}")
    print("По разделам (удалено):")
    for sec, n in sorted(by_del.items(), key=lambda x: -x[1]):
        print(f"  {sec}: {n}")
    if samples:
        print("Примеры удалённых:")
        for s in samples:
            print(f"  - {s}")

    n = rebuild_index()
    print(f"Индекс: {n}")
    print("Готово.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
