"""
Справочник платформенных типов реквизитов (uuid → имя) по эталонной внешней обработке.

В обработке создаются реквизиты, названные именем типа (ПостроительОтчета, ТаблицаЗначений,
ВыборкаИзРезультатаЗапроса, ХранилищеЗначения …), у каждого — ровно один этот тип. Скрипт читает
реквизиты, берёт неопознанный uuid типа и дописывает пару в tools/data/platform_types.json.

    python scripts/platform_types_map.py <эталон.epf> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import epf_inspect as E  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "tools" / "data" / "platform_types.json"


def collect(epf: str) -> tuple[dict[str, str], list[str]]:
    r = E.parse_external_file(epf)
    if not r.get("ok"):
        raise SystemExit(f"не разобран: {r.get('error')}")
    st = E.external_structure()
    found: dict[str, str] = {}
    skipped: list[str] = []
    items = list(st["attributes"]) + [c for t in st["tabular_sections"] for c in t["columns"]]
    for a in items:
        ids = a.get("type_ids") or []
        if len(ids) == 1:
            found[ids[0]] = a["name"]
        elif ids:
            skipped.append(f'{a["name"]}: составной тип ({len(ids)} неопознанных)')
    return found, skipped


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("epf")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    found, skipped = collect(a.epf)
    cur = json.loads(OUT.read_text("utf-8")) if OUT.exists() else {}
    new = {k: v for k, v in found.items() if cur.get(k) != v}
    for k, v in found.items():
        print(("+ " if k in new else "= ") + f"{v}: {k}")
    for s in skipped:
        print("! пропущен " + s)
    if new and not a.dry_run:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({**cur, **new}, ensure_ascii=False, indent=1, sort_keys=True), "utf-8")
        print(f"записано {len(new)} → {OUT}")


if __name__ == "__main__":
    main()
