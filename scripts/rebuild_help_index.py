# -*- coding: utf-8 -*-
"""
Пересобрать knowledge/справка_платформы/индекс.json

- только существующие .md с полезным текстом;
- path с «/»;
- title из заголовка;
- search: уникальные слова (RU + EN из скобок, части имени, синтаксис);
- без пустых карточек и без битых путей.

python scripts/rebuild_help_index.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELP = ROOT / "knowledge" / "справка_платформы"

CYR = re.compile(r"[а-яА-ЯёЁ]")
WORD = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]{2,}")
STUB = "Карточка из синтаксис-помощника 1С"
RE_TITLE = re.compile(r"^#\s+(.+)$", re.M)
RE_EN_PAREN = re.compile(r"\(([^)]+)\)")
RE_SYNTAX = re.compile(
    r"Синтаксис\s*:\s*(.+)$", re.M | re.I
)


def title_from(text: str, fallback: str) -> str:
    m = RE_TITLE.search(text)
    if m:
        t = re.sub(r"\s+", " ", m.group(1).strip().replace("\xa0", " "))
        if t and t not in ("•",) and not re.fullmatch(r"\d+", t):
            return t[:140]
    return fallback[:140]


def is_junk(text: str) -> bool:
    if len(text.strip()) < 60:
        return True
    if STUB in text:
        return True
    if "�" in text[:200] and len(text) < 500:
        return True
    # сырая структура 1С
    if text.count("{") >= 5 and ('"ru"' in text or '"en"' in text) and "<html" not in text.lower():
        return True
    if not CYR.search(text) and not re.search(r"[A-Za-z]{3,}", text):
        return True
    # только мета без описания
    body = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith(("Раздел:", "Источник:", "Имя в платформе:", "По-русски:")):
            continue
        body.append(s)
    if not body:
        return True
    joined = " ".join(body)
    if len(joined) < 30:
        return True
    return False


def split_ident(s: str) -> list[str]:
    """Массив.Добавить / Array.Add / base_name → куски для поиска.

    Латиницу режем по camelCase (HashSum → Hash, Sum).
    Кириллицу НЕ режем (ХешСумма остаётся целым — иначе «сумма» ловит хеш).
    """
    s = (s or "").replace("ё", "е").replace("Ё", "Е")
    parts: list[str] = []
    for chunk in re.split(r"[.\s_/\\-]+", s):
        chunk = chunk.strip("()[]<>\"',;:")
        if not chunk:
            continue
        parts.append(chunk)
        # только латиница: ArrayAdd / HashSum
        if re.fullmatch(r"[A-Za-z0-9]+", chunk) and re.search(r"[A-Z]", chunk):
            for bit in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", chunk):
                if bit and bit != chunk:
                    parts.append(bit)
    return parts


def build_search(title: str, stem: str, section: str, text: str) -> str:
    bag: list[str] = []

    def add(s: str) -> None:
        s = (s or "").strip()
        if s:
            bag.append(s)

    add(title)
    add(stem.replace("_", " ").replace(".", " "))
    for p in split_ident(title) + split_ident(stem):
        add(p)

    # EN / RU из скобок: «Массив.Добавить (Array.Add)»
    for m in RE_EN_PAREN.finditer(title):
        inner = m.group(1).strip()
        add(inner)
        for p in split_ident(inner):
            add(p)

    # левая часть до скобок
    if "(" in title:
        left = title.split("(")[0].strip()
        add(left)
        for p in split_ident(left):
            add(p)

    # строка «Синтаксис: Добавить(<Значение>)»
    sm = RE_SYNTAX.search(text[:2500])
    if sm:
        syn = re.sub(r"<[^>]+>", " ", sm.group(1))
        syn = re.sub(r"[()\[\].,;:]", " ", syn)
        add(syn)

    # раздел как слово
    add(section.replace("_", " "))

    # нормализация + уникальность с сохранением порядка
    seen: set[str] = set()
    out: list[str] = []
    for raw in bag:
        low = raw.lower().replace("ё", "е")
        for w in WORD.findall(low):
            if len(w) < 2:
                continue
            if w in seen:
                continue
            # отсечь односимвольный шум уже выше; чистые числа длиной 1-2
            if re.fullmatch(r"\d+", w) and len(w) < 3:
                continue
            seen.add(w)
            out.append(w)
    return " ".join(out)


def build_rows() -> tuple[list[dict], int]:
    rows: list[dict] = []
    skipped = 0

    for sub in sorted(HELP.iterdir()):
        if not sub.is_dir():
            continue
        for md in sorted(sub.glob("*.md")):
            if md.name.lower() == "readme.md":
                continue
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                skipped += 1
                continue
            if is_junk(text):
                skipped += 1
                continue

            title = title_from(text, md.stem)
            # без кириллицы в title+начале — пропускаем, кроме известных EN-тем
            head = (title + " " + text[:400]).lower()
            if not CYR.search(title + text[:800]):
                # оставить только если это известный тех. термин с описанием
                if not re.search(r"описание|синтаксис|доступн", text[:1500], re.I):
                    skipped += 1
                    continue

            rel = f"{sub.name}/{md.name}"
            name = md.stem.lower().replace("ё", "е")
            search = build_search(title, md.stem, sub.name, text)

            rows.append(
                {
                    "path": rel,
                    "name": name,
                    "title": title,
                    "section": sub.name,
                    "search": search,
                }
            )

    # дедуп по path
    seen: set[str] = set()
    uniq: list[dict] = []
    for r in rows:
        if r["path"] in seen:
            continue
        seen.add(r["path"])
        uniq.append(r)

    uniq.sort(key=lambda r: (r["section"], r["name"], r["path"]))
    return uniq, skipped


def write_readme(rows: list[dict]) -> None:
    by: dict[str, int] = {}
    for r in rows:
        by[r["section"]] = by.get(r["section"], 0) + 1

    lines = [
        "# Справка платформы 1С",
        "",
        "Статьи на русском. Имена файлов — по теме (по-русски).",
        "Поиск: knowledge_search (поле search в индекс.json).",
        "",
        "Пустые карточки (только имя без описания) не индексируются.",
        "",
        "## Разделы",
        "",
    ]
    for sec, n in sorted(by.items()):
        lines.append(f"- **{sec}** — {n} статей")
    lines += [
        "",
        f"Всего в индексе: {len(rows)}",
        "",
        "Обновить: python scripts/unpack_hbk.py",
        "Почистить: python scripts/clean_platform_help.py",
        "Убрать пустые: python scripts/drop_stub_cards.py",
        "Индекс: python scripts/rebuild_help_index.py",
        "",
    ]
    (HELP / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    if not HELP.is_dir():
        print(f"Нет папки: {HELP}")
        return 1

    rows, skipped = build_rows()
    idx = HELP / "индекс.json"
    idx.write_text(
        json.dumps(rows, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    write_readme(rows)

    by: dict[str, int] = {}
    for r in rows:
        by[r["section"]] = by.get(r["section"], 0) + 1

    # быстрая проверка целостности
    missing = sum(1 for r in rows if not (HELP / r["path"]).is_file())
    avg_search = sum(len(r["search"]) for r in rows) / max(1, len(rows))

    print(f"Индекс: {len(rows)} записей (пропущено как мусор: {skipped})")
    print(f"Битых path: {missing}")
    print(f"Средняя длина search: {avg_search:.0f} симв.")
    for sec, n in sorted(by.items()):
        print(f"  {sec}: {n}")

    # примеры
    for needle in ("сумма", "массив добавить", "выбрать"):
        hits = [r for r in rows if all(t in r["search"] for t in needle.split())][:3]
        print(f"probe «{needle}» → {len(hits)}+", [h["title"][:50] for h in hits])

    print("Готово:", idx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
