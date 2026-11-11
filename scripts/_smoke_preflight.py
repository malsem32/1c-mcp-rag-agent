# -*- coding: utf-8 -*-
"""Smoke: preflight routing + resolve ranking + live tools."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backends import create_backend
from config import load_config
from tools.preflight import plan_preflight, run_preflight
from tools.intent_search import rank_items_by_phrase
from tools.universal import UniversalTools


def main() -> int:
    phrases = [
        "задолженность клиентов на конец 2015",
        "маржа по менеджерам за 2015",
        "обороты расчетов с клиентами помесячно за 2015",
        "по каким регистрам проводится реализация",
        "продажи по контрагентам за 2015",
        "какие роли могут удалять справочники",
    ]
    print("=== plan_preflight ===")
    for p in phrases:
        plan = plan_preflight(p)
        if plan:
            print(f"  [{plan['confidence']:.2f}] {plan['tool']:28} | {p[:50]}")
        else:
            print(f"  [--] {'(llm)':28} | {p[:50]}")

    # rank debt candidates
    print("\n=== rank debt-like items ===")
    # плейсхолдеры (не имена конкретной CF)
    fake = [
        {"full_name": "РегистрНакопления.ТестЗаказы", "score": 50},
        {"full_name": "РегистрНакопления.ТестРасчетыСКлиентами", "score": 50},
        {"full_name": "Документ.ТестовыйДокументПродаж", "score": 50},
        {"full_name": "Отчет.ТестЗадолженность", "score": 50},
    ]
    ranked = rank_items_by_phrase(fake, "задолженность клиентов на конец 2015")
    for it in ranked:
        print(f"  {it.get('_phrase_score') or it.get('score'):6} {it['full_name']}")

    cfg = load_config()
    t = UniversalTools(create_backend(cfg), read_only=True)

    def disp(name, args):
        return t.dispatch(name, args)

    print("\n=== run_preflight live ===")
    for p in (
        "задолженность клиентов на конец 2015",
        "маржа по менеджерам за 2015",
        "по каким регистрам проводится реализация товаров",
    ):
        pf = run_preflight(p, disp, min_confidence=0.78)
        if not pf:
            print("  SKIP", p)
            continue
        r = pf["result"] if isinstance(pf["result"], dict) else {}
        rows = r.get("rows") or []
        regs = r.get("registers") or r.get("register_names_full") or []
        print(
            f"  {pf['tool']:28} ok={pf['ok']} rows={len(rows) if isinstance(rows, list) else '-'} "
            f"regs={len(regs) if isinstance(regs, list) else '-'} "
            f"err={str(r.get('error') or '')[:60]}"
        )
        if pf["tool"] == "linked_query":
            print("    objects", (r.get("objects") or [])[:3])
        if pf["tool"] == "margin_report":
            print("    full_name", r.get("full_name"))
        if pf["tool"] == "analyze_object_movements":
            names = r.get("register_names_full") or [
                x.get("name") for x in (r.get("registers") or [])[:5] if isinstance(x, dict)
            ]
            print("    registers", names[:8] if isinstance(names, list) else names)

    print("\n=== resolve_object debt ===")
    ro = t.resolve_object("задолженность клиентов", limit=8)
    print("  top", ro.get("top") or (ro.get("items") or [{}])[0].get("full_name"))
    print("  ambiguous", ro.get("need_clarification"), "count", ro.get("count"))
    for it in (ro.get("items") or [])[:5]:
        print(f"    {it.get('score')} {it.get('full_name')}")

    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
