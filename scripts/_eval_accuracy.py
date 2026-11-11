# -*- coding: utf-8 -*-
"""
Eval точности: routing (preflight) + retrieval (Qdrant) + лёгкий tool-smoke.

Типовые роли:
  - менеджер: отчёты, остатки, долги, несколько регистров, excel
  - программист: fill, locks, slow query, анализ модуля, movements, errors

  python scripts/_eval_accuracy.py
  python scripts/_eval_accuracy.py --with-tools   # live 1C tools (медленнее)
  python scripts/_eval_accuracy.py --with-llm     # полный agent.run (дорого)

Результат: exports/eval_accuracy.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Кейсы: ожидаем tool / intent, не hardcode имён CF
# ---------------------------------------------------------------------------

CASES: list[dict[str, Any]] = [
    # --- менеджер / данные ---
    {
        "id": "mgr_sales_report",
        "role": "manager",
        "q": "продажи по контрагентам за прошлый год в excel",
        "expect_tools": ("report_builder", "build_report", "compare_periods"),
        "kind": "data",
    },
    {
        "id": "mgr_debt",
        "role": "manager",
        "q": "задолженность клиентов на сейчас",
        "expect_tools": ("linked_query",),
        "kind": "data",
    },
    {
        "id": "mgr_stock",
        "role": "manager",
        "q": "сколько товаров осталось на складах",
        "expect_tools": ("linked_query",),
        "kind": "data",
    },
    {
        "id": "mgr_compare",
        "role": "manager",
        "q": "сравни продажи за 2015 и 2016 по контрагентам",
        "expect_tools": ("compare_periods",),
        "kind": "data",
    },
    {
        "id": "mgr_margin",
        "role": "manager",
        "q": "маржа по менеджерам за прошлый год",
        "expect_tools": ("margin_report",),
        "kind": "data",
    },
    {
        "id": "mgr_multi_reg",
        "role": "manager",
        "q": "обороты расчетов с клиентами помесячно за прошлый год",
        "expect_tools": ("linked_query", "report_builder", "build_report"),
        "kind": "data",
    },
    {
        "id": "mgr_chart",
        "role": "manager",
        "q": "отчет с графиком продажи по месяцам",
        "expect_tools": ("report_builder", "build_report"),
        "kind": "data",
    },
    # --- программист / код ---
    {
        "id": "dev_fill",
        "role": "dev",
        "q": "почему не заполнился контрагент в документе",
        "expect_tools": ("explain_fill",),
        "kind": "code",
        "search_needles": ("ОбработкаЗаполнения", "Заполнить", "Контрагент"),
    },
    {
        "id": "dev_locks",
        "role": "dev",
        "q": "почему возникают блокировки при проведении",
        "expect_tools": ("analyze_locks", "recommend_locks", "analyze_object_movements"),
        "kind": "code",
        "search_needles": ("Блокировка", "Заблокировать", "УправляемыеБлокировки"),
    },
    {
        "id": "dev_slow_query",
        "role": "dev",
        "q": "что тормозит запрос в цикле по номенклатуре",
        "expect_tools": ("audit_query_code", "inspect_code", "code_help", "search_code"),
        "kind": "code",
        "search_needles": ("Для Каждого", "Запрос", "ВЫБРАТЬ"),
    },
    {
        "id": "dev_movements",
        "role": "dev",
        "q": "по каким регистрам проводится документ реализация",
        "expect_tools": ("analyze_object_movements",),
        "kind": "code",
    },
    {
        "id": "dev_trace",
        "role": "dev",
        "q": "расскажи логику процедуры ОбработкаПроведения",
        "expect_tools": ("trace_calls", "inspect_code"),
        "kind": "code",
        "search_needles": ("ОбработкаПроведения",),
    },
    {
        "id": "dev_module_weak",
        "role": "dev",
        "q": "проанализируй модуль найди слабые места запрос в цикле",
        "expect_tools": ("inspect_code", "audit_query_code", "analyze_code_security"),
        "kind": "code",
        "search_needles": ("Запрос", "Для Каждого"),
    },
    {
        "id": "dev_error_log",
        "role": "dev",
        "q": "что за ошибка в журнале регистрации",
        "expect_tools": ("analyze_event_log",),
        "kind": "ops",
    },
    {
        "id": "dev_structure",
        "role": "dev",
        "q": "какие реквизиты есть у документа",
        "expect_tools": ("describe_object", "resolve_object"),
        "kind": "meta",
    },
    {
        "id": "dev_neg_code_not_report",
        "role": "dev",
        "q": "как работает УсловияПродаж расскажи логику",
        "expect_tools": ("trace_calls", "inspect_code"),
        "forbid_tools": ("report_builder", "build_report", "linked_query"),
        "kind": "code",
        "search_needles": ("УсловияПродаж",),
    },
]


def _ok_tool(plan: dict[str, Any] | None, case: dict[str, Any]) -> tuple[bool, str]:
    if plan is None:
        # LLM сам — ок только если нет жёсткого expect
        if case.get("expect_tools"):
            return False, "no_preflight"
        return True, "llm"
    tool = plan.get("tool") or ""
    forbid = case.get("forbid_tools") or ()
    if tool in forbid:
        return False, f"forbidden:{tool}"
    expect = case.get("expect_tools") or ()
    if expect and tool not in expect:
        return False, f"got:{tool}"
    return True, tool


def eval_preflight() -> list[dict[str, Any]]:
    from tools.preflight import plan_preflight

    rows = []
    for c in CASES:
        plan = plan_preflight(c["q"])
        ok, detail = _ok_tool(plan, c)
        rows.append(
            {
                "id": c["id"],
                "role": c["role"],
                "q": c["q"],
                "ok": ok,
                "tool": (plan or {}).get("tool"),
                "confidence": (plan or {}).get("confidence"),
                "detail": detail,
                "reason": (plan or {}).get("reason"),
            }
        )
    return rows


def eval_retrieval(limit: int = 8) -> list[dict[str, Any]]:
    from config import load_config
    from vector.search import search_code_vectors

    cfg = load_config()
    rows = []
    for c in CASES:
        needles = c.get("search_needles") or ()
        if not needles:
            continue
        t0 = time.time()
        try:
            res = search_code_vectors(c["q"], limit=limit, cfg=cfg)
        except Exception as exc:  # noqa: BLE001
            rows.append(
                {
                    "id": c["id"],
                    "q": c["q"],
                    "ok": False,
                    "error": str(exc),
                    "sec": round(time.time() - t0, 2),
                }
            )
            continue
        matches = res.get("matches") or []
        blob = "\n".join(
            f"{m.get('path','')} {m.get('snippet','')} {' '.join(m.get('proc_names') or [])}"
            for m in matches
        ).lower()
        hit = any(n.lower() in blob for n in needles)
        rows.append(
            {
                "id": c["id"],
                "q": c["q"],
                "ok": bool(res.get("ok")) and hit,
                "hit_needle": hit,
                "count": len(matches),
                "mode": res.get("match_mode"),
                "lexical_count": res.get("lexical_count"),
                "semantic_count": res.get("semantic_count"),
                "top_path": (matches[0].get("path") if matches else ""),
                "top_kind": (matches[0].get("match_kind") if matches else ""),
                "error": res.get("error") or "",
                "sec": round(time.time() - t0, 2),
            }
        )
    return rows


def eval_tools_smoke() -> list[dict[str, Any]]:
    from backends import create_backend
    from config import load_config
    from tools.preflight import plan_preflight, run_preflight
    from tools.universal import UniversalTools

    cfg = load_config()
    tools = UniversalTools(create_backend(cfg), read_only=True)

    def disp(name: str, args: dict[str, Any]) -> Any:
        return tools.dispatch(name, args)

    subset = [
        "почему не заполнился контрагент в документе",
        "по каким регистрам проводится документ",
        "задолженность клиентов на сейчас",
        "продажи по контрагентам за прошлый год",
        "что тормозит запрос",
        "почему возникают блокировки",
    ]
    rows = []
    for q in subset:
        plan = plan_preflight(q)
        t0 = time.time()
        pf = run_preflight(q, disp, min_confidence=0.78)
        dt = round(time.time() - t0, 2)
        if not pf:
            rows.append(
                {
                    "q": q,
                    "plan": (plan or {}).get("tool"),
                    "ok": plan is None,
                    "note": "no_auto_preflight",
                    "sec": dt,
                }
            )
            continue
        r = pf["result"] if isinstance(pf["result"], dict) else {}
        rows.append(
            {
                "q": q,
                "tool": pf.get("tool"),
                "ok": bool(pf.get("ok")),
                "error": str(r.get("error") or "")[:120],
                "sec": dt,
            }
        )
    return rows


def eval_llm(limit: int = 6) -> list[dict[str, Any]]:
    from agent.loop import build_agent_from_config
    from backends import create_backend
    from config import load_config
    from tools.universal import UniversalTools

    cfg = load_config()
    tools = UniversalTools(
        create_backend(cfg),
        read_only=True,
        default_limit=cfg["agent"]["query_default_limit"],
        max_limit=cfg["agent"]["query_max_limit"],
        list_objects_cap=cfg["agent"].get("list_objects_cap", 80),
        tool_result_max_chars=cfg["llm"].get("tool_result_max_chars", 6000),
        compact_results=True,
    )
    agent = build_agent_from_config(cfg, tools)
    pick = [c for c in CASES if c["id"] in {
        "mgr_debt",
        "mgr_sales_report",
        "dev_fill",
        "dev_movements",
        "dev_locks",
        "dev_slow_query",
        "dev_neg_code_not_report",
        "dev_module_weak",
    }][:limit]
    rows = []
    for c in pick:
        t0 = time.time()
        try:
            ans = agent.run(c["q"], verbose=False)
            err = ""
        except Exception as exc:  # noqa: BLE001
            ans = ""
            err = str(exc)
        rows.append(
            {
                "id": c["id"],
                "q": c["q"],
                "ok": bool(ans) and not err,
                "sec": round(time.time() - t0, 1),
                "error": err[:200],
                "answer_head": (ans or "")[:600],
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-tools", action="store_true")
    ap.add_argument("--with-llm", action="store_true")
    ap.add_argument("--llm-limit", type=int, default=6)
    args = ap.parse_args(argv)

    out: dict[str, Any] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S")}

    print("=== preflight routing ===", flush=True)
    pf = eval_preflight()
    out["preflight"] = pf
    ok_n = sum(1 for r in pf if r["ok"])
    print(f"  {ok_n}/{len(pf)} OK", flush=True)
    for r in pf:
        mark = "OK" if r["ok"] else "FAIL"
        print(
            f"  [{mark}] {r['id']:28} -> {r.get('tool') or 'llm':28} {r.get('detail')}",
            flush=True,
        )

    print("\n=== retrieval (Qdrant) ===", flush=True)
    ret = eval_retrieval()
    out["retrieval"] = ret
    if ret:
        ok_r = sum(1 for r in ret if r["ok"])
        print(f"  {ok_r}/{len(ret)} OK (needle in top hits)", flush=True)
        for r in ret:
            mark = "OK" if r["ok"] else "FAIL"
            print(
                f"  [{mark}] {r['id']:28} n={r.get('count')} "
                f"lex={r.get('lexical_count')} sem={r.get('semantic_count')} "
                f"{(r.get('top_path') or r.get('error') or '')[:70]}",
                flush=True,
            )
    else:
        print("  (нет кейсов с search_needles)", flush=True)

    if args.with_tools:
        print("\n=== tool smoke ===", flush=True)
        ts = eval_tools_smoke()
        out["tools"] = ts
        for r in ts:
            mark = "OK" if r.get("ok") else "FAIL"
            print(
                f"  [{mark}] {r.get('tool') or r.get('plan') or '?':28} "
                f"{r['q'][:40]} err={r.get('error') or r.get('note') or ''}",
                flush=True,
            )

    if args.with_llm:
        print("\n=== llm agent ===", flush=True)
        llm = eval_llm(limit=args.llm_limit)
        out["llm"] = llm
        for r in llm:
            mark = "OK" if r.get("ok") else "FAIL"
            print(f"  [{mark}] {r['id']} ({r['sec']}s)", flush=True)
            print(f"    {(r.get('answer_head') or r.get('error') or '')[:200]}", flush=True)

    path = ROOT / "exports" / "eval_accuracy.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nSAVED", path, flush=True)

    fails = sum(1 for r in pf if not r["ok"]) + sum(1 for r in ret if not r.get("ok"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
