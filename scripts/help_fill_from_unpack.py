# -*- coding: utf-8 -*-
"""
Дозаполнить knowledge/справка_платформы статьями из распакованной справки платформы (info/unpack/*/*.html).

Раньше в индекс попала только часть синтакс-помощника (~6 тыс. из ~25 тыс. статей):
не было, например, «СтрНайти», «МенеджерФоновыхЗаданий.Выполнить». Скрипт добавляет
НЕДОСТАЮЩИЕ статьи (существующие .md не трогает) в том же формате.

  python scripts/help_fill_from_unpack.py            # дописать .md
  python scripts/help_fill_from_unpack.py --dry-run  # только посчитать
  python scripts/index_qdrant.py --help-only         # затем проиндексировать (инкрементально)

Источник — локальная справка установленной платформы пользователя (.hbk → unpack_hbk.py).
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from unpack_hbk import BOOK_NAMES, html_to_text  # noqa: E402

HELP = ROOT / "knowledge" / "справка_платформы"
UNPACK = ROOT / "info" / "unpack"
CYR = re.compile(r"[а-яА-ЯёЁ]")
RE_PAGETITLE = re.compile(r'<h1 class="V8SH_pagetitle">(.*?)</h1>', re.S | re.I)


def slug(title: str) -> str:
    t = re.sub(r"<[^>]+>", "", title or "").strip().replace("\xa0", " ")
    if " (" in t and CYR.search(t.split(" (")[0]):
        t = t.split(" (")[0]
    t = re.sub(r'[<>:"/\\|?*\s]+', "_", t).strip("._")
    return t[:150]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--books", default="shcntx_ru,shlang_ru,shquery_ru,shclang_ru")
    a = ap.parse_args()

    total_new = 0
    for book in [b.strip() for b in a.books.split(",") if b.strip()]:
        src = UNPACK / book
        if not src.is_dir():
            print(f"нет {src}", file=sys.stderr)
            continue
        dst = HELP / BOOK_NAMES.get(book, book)
        existing = {p.stem.lower() for p in dst.glob("*.md")} if dst.is_dir() else set()
        new = 0
        for f in sorted(src.glob("*.html")):
            try:
                raw = f.read_bytes()
            except OSError:
                continue
            txt = raw.decode("utf-8-sig", errors="replace")
            m = RE_PAGETITLE.search(txt)
            if not m:
                continue
            title = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip())
            name = slug(title)
            if not name or not CYR.search(name) or name.lower() in existing:
                continue
            body = html_to_text(raw)
            if len(body) < 120:
                continue
            existing.add(name.lower())
            new += 1
            if a.dry_run:
                continue
            dst.mkdir(parents=True, exist_ok=True)
            (dst / f"{name}.md").write_text(
                f"# {title}\n\nРаздел: {dst.name}\n\n{body}\n", encoding="utf-8"
            )
        print(f"{book}: новых статей {new} → {dst}", file=sys.stderr)
        total_new += new
    print(f"Всего новых: {total_new}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
