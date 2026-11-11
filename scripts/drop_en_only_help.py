# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from pathlib import Path

HELP = Path(__file__).resolve().parents[1] / "knowledge" / "справка_платформы"
CYR = re.compile(r"[а-яА-ЯёЁ]")


def main() -> None:
    deleted = 0
    for p in list(HELP.rglob("*.md")):
        if p.name == "README.md":
            continue
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", p.stem):
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        if not CYR.search(t):
            p.unlink(missing_ok=True)
            deleted += 1
    print("deleted en-only", deleted)

    rows = []
    for sub in sorted(HELP.iterdir()):
        if not sub.is_dir():
            continue
        for md in sub.glob("*.md"):
            if md.name == "README.md":
                continue
            text = md.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^#\s+(.+)$", text, re.M)
            title = (m.group(1).strip() if m else md.stem)[:120]
            if not CYR.search(title + text[:500]):
                continue
            search = title + " " + md.stem.replace("_", " ")
            if "(" in title:
                search += " " + title.split("(")[0]
            rows.append(
                {
                    "path": sub.name + "/" + md.name,
                    "name": md.stem.lower().replace("ё", "е"),
                    "title": title,
                    "section": sub.name,
                    "search": search.lower().replace("ё", "е"),
                }
            )

    (HELP / "индекс.json").write_text(
        json.dumps(rows, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    by: dict[str, int] = {}
    for r in rows:
        by[r["section"]] = by.get(r["section"], 0) + 1
    print("index", len(rows), by)

    lines = [
        "# Справка платформы 1С",
        "",
        "Статьи на русском. Имена файлов — по теме.",
        "Поиск: knowledge_search",
        "",
        "## Разделы",
        "",
    ]
    for sec, n in sorted(by.items()):
        lines.append(f"- **{sec}** — {n} статей")
    lines += [
        "",
        f"Всего: {len(rows)}",
        "",
        "Обновить: python scripts/unpack_hbk.py",
        "Почистить: python scripts/clean_platform_help.py",
        "",
    ]
    (HELP / "README.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
