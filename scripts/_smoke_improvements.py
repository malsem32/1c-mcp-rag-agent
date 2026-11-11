# -*- coding: utf-8 -*-
"""Smoke checks after A+B improvements."""
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
from tools.explain_fill import (
    is_debt_unpaid_question,
    is_posting_error_question,
    is_status_question,
)
from tools.object_detail import load_enum_values
from tools.query_1c_normalize import normalize_to_1c_query
from tools.trace_calls import trace_calls
from tools.universal import UniversalTools
from vector.search import search_code_vectors


def main() -> int:
    print("=== unit ===")
    for q in [
        "ВЫБРАТЬ 1 ГДЕ Д.Дата МЕЖДУ &НачалоПериода И &КонецПериода",
        "ВЫБРАТЬ 1 ГДЕ Д.Дата МЕЖДУ '20150101000000' И '20151231235959'",
    ]:
        r = normalize_to_1c_query(q)
        print("NORM:", (r.get("query") or "")[:140])
        print("  warn:", r.get("warnings"))

    print("debt", is_debt_unpaid_question("неоплаченные счета на оплату клиенту"))
    print("status", is_status_question("какие статусы у реализации товаров и услуг"))
    print("post", is_posting_error_question("почему не проводится реализация"))
    print("enum", load_enum_values("СтатусыРеализацийТоваровУслуг"))

    cfg = load_config()
    backend = create_backend(cfg)
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
    # full_name только из metadata стенда (стемы, не hardcode CF)
    from tools.business_stems import sales_search

    meta = tools.find_metadata(sales_search(), "Документы", 5)
    items = meta.get("items") or meta.get("results") or []
    sales_fn = ""
    for it in items:
        if isinstance(it, dict) and str(it.get("full_name") or "").startswith("Документ."):
            sales_fn = str(it["full_name"])
            break
    print("resolved sales_fn", sales_fn or "(none)")
    if sales_fn:
        st = tools.get_object_structure(sales_fn)
        print("status_fields", st.get("status_fields"))
        print("enum_values_for_status", st.get("enum_values_for_status"))
        print("keys", list(st.keys())[:10])

    print("=== search usages (инфраструктура склада, стем) ===")
    sc = search_code_vectors("ОтразитьТовары", limit=8, match_mode="hybrid")
    for m in (sc.get("matches") or [])[:5]:
        print(f"  {m.get('score'):.1f} {m.get('path')}")

    print("=== trace posting ===")
    t0 = time.time()
    tr = trace_calls(
        request=f"цепочка ОбработкаПроведения {sales_fn or 'документ продаж'}",
        procedure="ОбработкаПроведения",
        depth=2,
        max_edges=25,
    )
    print("start_path", tr.get("start_path"), "sec", round(time.time() - t0, 1))
    print("edges", tr.get("edges_count"), "ok", tr.get("ok"))
    for line in (tr.get("summary_lines") or [])[:8]:
        print(" ", line)

    print("=== agent Qs ===")
    agent = build_agent_from_config(cfg, tools)
    # вопросы без hardcode full_name CF — разговорно + resolve на стенде
    qs = [
        ("M", "Какие статусы бывают у документа продаж/реализации?"),
        ("M", "Покажи неоплаченные или с задолженностью счета на оплату — данные из базы"),
        ("M", "Почему может не проводиться документ продаж? Какие проверки и отказы в коде?"),
        ("M", "Почему в реализации могут записаться не те поля / не тот склад при заполнении из заказа?"),
        ("M", "Сколько реализаций проведено?"),
        ("D", "Где в коде отражают товары на складах при проведении? Основные path"),
        ("D", f"Цепочка вызовов ОбработкаПроведения {sales_fn or 'документа продаж'} — кратко"),
        ("D", "Где в коде есть БлокироватьДляИзменения или управляемые блокировки при проведении?"),
        ("D", "Что тяжёлого в проведении документа продаж: Отразить* и узкие места"),
        ("D", "Есть ли в ОбработкаПроведения продаж запросы в цикле?"),
    ]
    out = []
    for i, (role, q) in enumerate(qs, 1):
        print(f"\n----- {i}/{len(qs)} [{role}] -----")
        print("Q:", q)
        t0 = time.time()
        try:
            ans = agent.run(q, verbose=True)
        except Exception as e:
            ans = f"ERROR: {e}"
        dt = time.time() - t0
        print(f"A ({dt:.0f}s):", (ans or "")[:1200])
        out.append({"n": i, "role": role, "q": q, "sec": round(dt, 1), "a": ans or ""})

    path = ROOT / "exports" / "agent_smoke_qa2.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SAVED", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
