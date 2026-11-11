# -*- coding: utf-8 -*-
"""Проверка улучшенных отчётов."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.loop import build_agent_from_config
from backends import create_backend
from config import load_config
from tools.report_format import is_manager_report_question
from tools.universal import UniversalTools


def main() -> int:
    cfg = load_config()
    backend = create_backend(cfg)
    print("1C:", backend.test_connection())
    tools = UniversalTools(
        backend,
        read_only=True,
        default_limit=25,
        max_limit=80,
        list_objects_cap=80,
        tool_result_max_chars=6000,
        compact_results=True,
        code_dir=cfg.get("code_dir") or "",
    )

    print("\n=== unit is_manager_report ===")
    for q in (
        "продажи по контрагентам за 2015",
        "где в коде проведение",
        "задолженность клиентов",
    ):
        print(q, "->", is_manager_report_question(q))

    print("\n=== build_report count 2015 ===")
    r = tools.build_report(request="сколько реализаций товаров и услуг проведено за 2015")
    print("fn", r.get("full_name"), "ms", r.get("manager_summary"))
    print("rows sample", (r.get("rows") or [])[:2])

    print("\n=== build_report top/group ===")
    r = tools.build_report(
        request="продажи по контрагентам за 2015 топ суммы",
        limit=10,
    )
    print("fn", r.get("full_name"), "summary", r.get("manager_summary"))
    print("n rows", len(r.get("rows") or []), "retry", r.get("retry_reason"))

    print("\n=== build_report debt -> linked ===")
    r = tools.build_report(request="задолженность клиентов на конец 2015")
    print("route", r.get("route"), "objects", r.get("objects"))
    print("summary", r.get("manager_summary"))
    print("n", r.get("row_count"), "err", r.get("error") or (r.get("execute_result") or {}).get("error"))

    print("\n=== execute_query phrase blocked ===")
    r = tools.execute_query("продажи за 2015 по контрагентам")
    print(r.get("error"), r.get("prefer_tool"))

    print("\n=== agent smoke (if LLM up) ===")
    agent = build_agent_from_config(cfg, tools)
    qs = [
        "сколько реализаций за 2015 год",
        "топ контрагентов по сумме реализаций за 2015",
        "задолженность клиентов остатки",
        "остатки товаров на складах",
    ]
    out = []
    for i, q in enumerate(qs, 1):
        print(f"\n--- agent {i}: {q} ---")
        t0 = time.time()
        try:
            ans = agent.run(q, verbose=True)
        except Exception as e:
            ans = f"ERROR: {e}"
        print(f"({time.time()-t0:.0f}s)", (ans or "")[:900])
        out.append({"q": q, "a": ans or "", "sec": round(time.time() - t0, 1)})

    path = ROOT / "exports" / "agent_reports_smoke.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SAVED", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
