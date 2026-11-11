# -*- coding: utf-8 -*-
"""E2E: data → last_report → «в excel» без мастера."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    from backends import create_backend
    from config import load_config
    from tools.dialog_context import (
        export_snapshot_to_excel,
        has_reusable_report,
        snapshot_from_result,
        wants_export_existing,
    )
    from tools.universal import UniversalTools

    assert wants_export_existing("запихни его в отчет")
    assert wants_export_existing("в excel")

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
    # 1) data
    data = t.compare_periods(
        search_query="продажи",
        period="2016",
        group_by="контрагент",
        metrics="sum,count",
    )
    assert data.get("ok") or data.get("rows"), data.get("error") or data
    snap = snapshot_from_result(
        "compare_periods", data, user_text="продажи 2016", args={}
    )
    assert snap and has_reusable_report(snap), snap
    t._last_report_snapshot = snap
    print("snap rows", len(snap.get("rows") or []), "fn", snap.get("full_name"))

    # 2) reuse export
    out = t.report_builder(request="запихни в отчет", skip_wizard=True, to_excel=True)
    print(
        "reuse",
        {
            k: out.get(k)
            for k in (
                "ok",
                "from_last_report",
                "path",
                "wizard",
                "need_clarification",
            )
        },
    )
    assert not out.get("wizard"), out
    assert out.get("path") or out.get("from_last_report") or out.get("ok"), out
    if out.get("path"):
        assert Path(out["path"]).exists()
    # direct export
    e = export_snapshot_to_excel(snap, phrase="в excel pie")
    assert e.get("ok") and e.get("path")
    print("OK e2e excel reuse", out.get("path") or e.get("path"))


if __name__ == "__main__":
    main()
