# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    from tools.preflight import plan_preflight

    for p in (
        "отчет с графиком продажи 2016",
        "мастер excel по шагам",
        "остатки в excel",
        "сравни 2015 и 2016 в excel",
    ):
        pf = plan_preflight(p)
        print(
            p,
            "=>",
            (pf or {}).get("tool"),
            (pf or {}).get("args", {}).get("skip_wizard"),
            (pf or {}).get("reason"),
        )

    from backends import create_backend
    from config import load_config
    from tools.universal import UniversalTools

    cfg = load_config()
    t = UniversalTools(
        create_backend(cfg),
        read_only=True,
        default_limit=25,
        max_limit=80,
        list_objects_cap=80,
        tool_result_max_chars=8000,
        compact_results=True,
        code_dir=cfg.get("code_dir") or "",
    )
    r = t.report_builder(
        request="отчет с графиком продажи 2016",
        skip_wizard=True,
        to_excel=True,
    )
    print(
        "auto",
        {
            k: r.get(k)
            for k in (
                "ok",
                "path",
                "wizard",
                "need_clarification",
                "auto_build",
                "full_name",
                "count",
                "charts_count",
                "error",
                "excel_error",
                "note",
            )
        },
    )
    if r.get("path"):
        print("FILE", r["path"], Path(r["path"]).exists())
    assert not r.get("wizard"), "must not start wizard"
    # either file or data or clear clarification
    assert r.get("path") or r.get("rows") or r.get("need_clarification") or r.get(
        "ok"
    )
    print("SMOKE AUTO OK")


if __name__ == "__main__":
    main()
