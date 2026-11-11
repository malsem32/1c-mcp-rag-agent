# -*- coding: utf-8 -*-
"""
Почистить справку платформы:
- удалить мусор;
- из структур 1С вытащить русские имена;
- переименовать файлы по-русски;
- индекс.json для поиска.

python scripts/clean_platform_help.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELP = ROOT / "knowledge" / "справка_платформы"

CYR = re.compile(r"[а-яА-ЯёЁ]")
RE_STRUCT_RU = re.compile(
    r'\{\s*"ru"\s*,\s*\d+\s*,\s*\d+\s*,\s*""\s*,\s*"([^"]*)"\s*\}', re.I
)


def safe_slug(title: str, fallback: str) -> str:
    t = (title or "").strip().replace("\xa0", " ")
    # «Массив.Добавить (Array.Add)» → «Массив.Добавить»
    if "(" in t and CYR.search(t):
        t = t.split("(")[0].strip()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", t)
    t = re.sub(r"[()]", "", t)
    t = re.sub(r"\s+", "_", t.strip(" ._"))
    if not t or len(t) < 2 or re.fullmatch(r"[\d_]+", t):
        t = re.sub(r'[<>:"/\\|?*]', "_", fallback)[:80]
    return t[:100]


def is_struct(text: str) -> bool:
    """Сырая бинарная/структурная карточка 1С без HTML-текста."""
    if RE_STRUCT_RU.search(text) and text.count("{") >= 3 and text.count("<html") + text.count("<HTML") == 0:
        return True
    t = text.lstrip()
    return t.startswith("{1,") or bool(re.match(r"^# [^\n]+\n+\{1,", text))


def title_from_md(text: str) -> str:
    for line in text.splitlines()[:20]:
        line = line.strip()
        if line.startswith("#"):
            t = re.sub(r"\s+", " ", line.lstrip("#").strip().replace("\xa0", " "))
            if t and t not in ("•",) and not re.fullmatch(r"\d+", t):
                return t[:120]
    return ""


def is_empty_card(text: str) -> bool:
    """Пустая карточка: только имя/раздел/«Карточка…» без описания."""
    if "Карточка из синтаксис-помощника 1С" in text:
        return True
    return False


def is_garbage(stem: str, text: str) -> bool:
    if stem.startswith("__"):
        return True
    if len(text.strip()) < 40:
        return True
    if "�" in text[:300]:
        return True
    # Сырые структуры 1С без HTML — без полезного описания, не сохраняем.
    if is_struct(text):
        return True
    if is_empty_card(text):
        return True
    if re.fullmatch(r"\d+", stem) and not CYR.search(text):
        return True
    if not CYR.search(text):
        return True
    return False


def clean_dir(folder: Path) -> dict:
    stats = {"kept": 0, "deleted": 0, "renamed": 0, "rows": []}
    if not folder.is_dir():
        return stats

    # сначала собрать всё, потом писать (чтобы rename не конфликтовал)
    planned: list[tuple[Path, str, str]] = []  # old_path, new_text, title

    for path in list(folder.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            path.unlink(missing_ok=True)
            stats["deleted"] += 1
            continue

        if is_garbage(path.stem, raw):
            path.unlink(missing_ok=True)
            stats["deleted"] += 1
            continue

        text = raw
        text = re.sub(
            r"^Источник:.*$", f"Раздел: {folder.name}", text, count=1, flags=re.M
        )
        text = re.sub(r"\n•\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        title0 = title_from_md(text) or path.stem
        if not re.search(r"^#\s+", text, re.M):
            text = f"# {title0}\n\n{text}"
        else:
            text = re.sub(r"^#\s+.+$", f"# {title0}", text, count=1, flags=re.M)

        title = title_from_md(text) or path.stem
        planned.append((path, text.strip() + "\n", title))

    # записать во временные, потом заменить
    used: set[str] = set()
    for old_path, text, title in planned:
        slug = safe_slug(title, old_path.stem)
        base = slug
        n = 2
        while base.lower() in used:
            base = f"{slug}_{n}"
            n += 1
        used.add(base.lower())

        new_path = folder / f"{base}.md"
        if new_path != old_path and new_path.exists():
            # освободить имя
            try:
                new_path.unlink()
            except OSError:
                base = f"{slug}_{n}"
                new_path = folder / f"{base}.md"

        try:
            if old_path != new_path and old_path.exists():
                old_path.unlink()
                stats["renamed"] += 1
            new_path.write_text(text, encoding="utf-8")
        except OSError as e:
            print("  err", old_path.name, e)
            continue

        title = title_from_md(text) or base
        search = f"{title} {base.replace('_', ' ')}"
        if "(" in title:
            search += " " + title.split("(")[0]
            m = re.search(r"\(([^)]+)\)", title)
            if m:
                search += " " + m.group(1).replace(".", " ")

        stats["kept"] += 1
        stats["rows"].append(
            {
                "path": f"{folder.name}/{new_path.name}",
                "name": new_path.stem.lower().replace("ё", "е"),
                "title": title,
                "section": folder.name,
                "search": search.lower().replace("ё", "е"),
            }
        )

    return stats


def main() -> int:
    if not HELP.is_dir():
        print(f"Нет папки: {HELP}")
        return 1

    all_rows: list[dict] = []
    print(f"Чистим: {HELP}\n")

    for sub in sorted(HELP.iterdir()):
        if not sub.is_dir():
            continue
        print(f"=== {sub.name}")
        st = clean_dir(sub)
        print(
            f"  осталось: {st['kept']}, удалено: {st['deleted']}, "
            f"переименовано: {st['renamed']}"
        )
        all_rows.extend(st["rows"])

    # Полный индекс с нормальным search — scripts/rebuild_help_index.py
    import subprocess
    import sys

    print("\nПересборка индекса…")
    rc = subprocess.call(
        [sys.executable, str(ROOT / "scripts" / "rebuild_help_index.py")]
    )
    print("Готово." if rc == 0 else f"Индекс: ошибка {rc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
