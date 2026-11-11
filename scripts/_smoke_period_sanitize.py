# -*- coding: utf-8 -*-
"""Smoke: period не = user-фраза; all-time; default years."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backends import create_backend
from config import load_config
from tools.period_parse import extract_period_spec, parse_periods
from tools.universal import UniversalTools


def main() -> int:
    t = UniversalTools(create_backend(load_config()))
    phrases = [
        "что продается а что нет",
        "что продается а что нет за все время",
        "abc по контрагентам",
        "кто вырос кто упал по продажам",
        "сравни продажи",
        "маржа по менеджерам",
        "продажи по контрагентам",
        "где заморожены деньги",
        "воронка продаж",
        "vip клиенты и отток",
    ]
    print("=== extract / parse ===")
    bad_extract = 0
    for q in phrases:
        e = extract_period_spec(q)
        p = parse_periods(q)
        ok = not p.get("need_clarification")
        # не демо-годы 2015/2016 в default (только если явно в фразе)
        if "2015" not in q and "2016" not in q and e in ("2015 и 2016", "2015", "2016"):
            ok = False
            bad_extract += 1
            print(f"  BAD demo-year {q!r} -> {e!r}")
            continue
        if not ok:
            bad_extract += 1
        print(f"  {'ok' if ok else 'BAD'} {q!r} -> {e!r}")

    print("=== live tools ===")
    checks = [
        ("abc", lambda: t.analyst_report(request="abc по контрагентам", mode="abc", limit=15)),
        (
            "variance",
            lambda: t.analyst_report(
                request="кто вырос кто упал по продажам", mode="variance", limit=15
            ),
        ),
        (
            "assortment",
            lambda: t.analyst_report(
                request="что продается а что нет", mode="assortment", limit=15
            ),
        ),
        (
            "all_time",
            lambda: t.analyst_report(
                request="что продается а что нет за все время",
                mode="assortment",
                limit=15,
            ),
        ),
        ("build", lambda: t.build_report(request="продажи по контрагентам", limit=15)),
        (
            "compare_noise",
            lambda: t.compare_periods(
                search_query="реализация", period="abc по контрагентам"
            ),
        ),
        ("margin", lambda: t.margin_report(request="маржа по менеджерам", limit=15)),
        (
            "report_builder",
            lambda: t.report_builder(
                request="продажи по контрагентам",
                to_excel=False,
                limit=15,
                skip_wizard=True,
            ),
        ),
        (
            "customers",
            lambda: t.analyst_report(
                request="vip клиенты и отток", mode="customers", limit=15
            ),
        ),
        (
            "reserves",
            lambda: t.analyst_report(
                request="где заморожены деньги", mode="reserves", limit=15
            ),
        ),
    ]
    bad_live = 0
    for name, fn in checks:
        r = fn()
        nc = r.get("need_clarification")
        about = r.get("about")
        rows = len(r.get("rows") or [])
        per = r.get("period_spec") or r.get("period")
        bad = bool(nc and about == "period")
        if bad:
            bad_live += 1
        print(
            f"  {'BAD' if bad else 'ok '} {name}: rows={rows} period={per!r} "
            f"q={str(r.get('question') or '')[:50]!r}"
        )

    print(f"RESULT extract_bad={bad_extract} live_period_ask={bad_live}")
    return 1 if (bad_extract or bad_live) else 0


if __name__ == "__main__":
    raise SystemExit(main())
