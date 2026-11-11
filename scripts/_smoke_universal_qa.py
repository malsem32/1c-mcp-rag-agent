# -*- coding: utf-8 -*-
"""
Универсальный QA-прогон: resolve + preflight + tools (и опционально LLM).

python scripts/_smoke_universal_qa.py
python scripts/_smoke_universal_qa.py --with-llm
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

from backends import create_backend
from config import load_config
from tools.preflight import plan_preflight, run_preflight
from tools.universal import UniversalTools


# (id, category, question, how_to_eval)
# eval: resolve | preflight | tool:name | agent
CASES: list[tuple[str, str, str, str]] = [
    # --- resolve / meta (разные домены) ---
    ("R01", "meta", "найди документ реализация", "resolve"),
    ("R02", "meta", "справочник номенклатура", "resolve"),
    ("R03", "meta", "спецификация номенклатуры", "resolve"),
    ("R04", "meta", "супертарикул", "resolve"),
    ("R05", "meta", "заявка на перевозку", "resolve"),
    ("R06", "meta", "заказ клиента", "resolve"),
    ("R07", "meta", "счет на оплату покупателю", "resolve"),
    ("R08", "meta", "поступление товаров", "resolve"),
    # --- data / reports ---
    ("D01", "data", "продажи по контрагентам за 2015", "preflight"),
    ("D02", "data", "задолженность клиентов на конец 2015", "preflight"),
    ("D03", "data", "маржа по менеджерам за 2015", "preflight"),
    ("D04", "data", "обороты расчетов с клиентами помесячно за 2015", "preflight"),
    ("D05", "data", "список номенклатуры", "preflight"),
    ("D06", "data", "сколько реализаций за 2015", "preflight"),
    # --- structure / status ---
    ("S01", "structure", "реквизиты документа реализация", "tool:resolve_then_structure"),
    ("S02", "structure", "какие статусы у заказа клиента", "tool:resolve_then_structure"),
    # --- movements ---
    ("M01", "movements", "по каким регистрам проводится реализация товаров", "preflight"),
    ("M02", "movements", "какие движения делает поступление", "preflight"),
    # --- fill / why ---
    ("F01", "fill", "почему не заполнился контрагент в реализации", "preflight"),
    ("F02", "fill", "как заполняется склад в поступлении", "tool:explain_fill"),
    # --- code ---
    ("C01", "code", "откуда данные в форме документа реализация", "preflight"),
    # --- locks ---
    ("L01", "locks", "куда ставить управляемые блокировки для реализации", "preflight"),
    ("L02", "locks", "где в коде блокировки данных", "preflight"),
    # --- access ---
    ("A01", "access", "какие роли могут удалять справочники", "preflight"),
    # --- help ---
    ("H01", "help", "как написать ВЫБРАТЬ с группировкой", "tool:query_syntax"),
]


def _ok_resolve(r: dict) -> tuple[bool, str]:
    if r.get("error") and not r.get("items") and not r.get("options"):
        return False, str(r.get("error"))[:100]
    items = r.get("items") or r.get("options") or []
    top = r.get("top") or r.get("full_name") or (items[0] if items else None)
    if isinstance(top, dict):
        top = top.get("full_name")
    if r.get("need_clarification") and items:
        return True, f"options={len(items)} top={top}"
    if top or items:
        return True, f"top={top} n={len(items) if isinstance(items, list) else 1}"
    return False, "empty"


def _ok_tool_result(tool: str, r: Any) -> tuple[bool, str]:
    if not isinstance(r, dict):
        return False, type(r).__name__
    if r.get("error") and not any(
        r.get(k)
        for k in (
            "rows",
            "items",
            "registers",
            "recommendations",
            "hits",
            "matches",
            "roles",
            "manager_summary",
            "summary",
            "content",
            "example",
        )
    ):
        return False, str(r.get("error"))[:120]
    if tool in ("build_report", "linked_query", "margin_report"):
        rows = r.get("rows")
        n = len(rows) if isinstance(rows, list) else 0
        # 0 rows still "works" if query ok
        if r.get("ok") is False and n == 0 and r.get("error"):
            return False, str(r.get("error"))[:100]
        return True, f"rows={n} fn={r.get('full_name') or r.get('plan_role') or ''}"
    if tool == "analyze_object_movements":
        regs = r.get("register_names_full") or r.get("registers") or []
        n = len(regs) if isinstance(regs, list) else 0
        return n > 0 or bool(r.get("summary")), f"regs={n} {r.get('conclusion')}"
    if tool == "recommend_managed_locks":
        rec = r.get("recommendations") or []
        return bool(r.get("ok")), f"rec={len(rec)}"
    if tool == "analyze_locks":
        return bool(r.get("ok")), f"hits={r.get('hits_count')}"
    if tool == "explain_fill":
        hits = r.get("hits") or r.get("matches") or r.get("findings") or []
        return True, f"hits={len(hits) if isinstance(hits, list) else '?'} err={str(r.get('error') or '')[:40]}"
    if tool == "list_roles_by_right":
        roles = r.get("roles") or []
        return bool(r.get("ok")), f"roles={len(roles)}"
    if tool in ("inspect_code", "search_code"):
        return not r.get("error") or bool(r.get("matches") or r.get("modules")), str(
            list(r.keys())[:6]
        )
    if tool == "query_syntax_help":
        return bool(r.get("content") or r.get("example") or r.get("all_names")), "syntax"
    if tool == "get_object_structure":
        return bool(
            r.get("fields")
            or r.get("attributes")
            or r.get("dimension_names")
            or r.get("status_fields")
            or not r.get("error")
        ), f"fields~{len(r.get('fields') or r.get('attributes') or [])}"
    return bool(r.get("ok") is not False), "ok"


def run_case(t: UniversalTools, cid: str, cat: str, q: str, mode: str) -> dict[str, Any]:
    t0 = time.time()
    row: dict[str, Any] = {
        "id": cid,
        "category": cat,
        "question": q,
        "mode": mode,
        "ok": False,
        "detail": "",
        "ms": 0,
    }
    try:
        if mode == "resolve":
            r = t.resolve_object(q, "Все", 8)
            ok, det = _ok_resolve(r if isinstance(r, dict) else {})
            row["ok"] = ok
            row["detail"] = det
            row["tool"] = "resolve_object"
        elif mode == "preflight":
            plan = plan_preflight(q)
            row["plan"] = (plan or {}).get("tool")
            if not plan:
                # fallback resolve
                r = t.resolve_object(q, "Все", 6)
                ok, det = _ok_resolve(r if isinstance(r, dict) else {})
                row["ok"] = ok
                row["detail"] = f"no_preflight; resolve {det}"
                row["tool"] = "resolve_object"
            else:
                pf = run_preflight(q, lambda n, a: t.dispatch(n, a), min_confidence=0.75)
                if not pf:
                    row["ok"] = False
                    row["detail"] = "preflight planned but not run"
                else:
                    ok, det = _ok_tool_result(pf["tool"], pf.get("result"))
                    row["ok"] = ok and bool(pf.get("ok") or ok)
                    row["detail"] = f"{pf['tool']}: {det}"
                    row["tool"] = pf["tool"]
        elif mode == "tool:explain_fill":
            r = t.explain_fill(request=q)
            ok, det = _ok_tool_result("explain_fill", r)
            row["ok"] = ok
            row["detail"] = det
            row["tool"] = "explain_fill"
        elif mode == "tool:query_syntax":
            r = t.query_syntax_help("группировка")
            ok, det = _ok_tool_result("query_syntax_help", r)
            row["ok"] = ok
            row["detail"] = det
            row["tool"] = "query_syntax_help"
        elif mode == "tool:resolve_then_structure":
            r = t.resolve_object(q, "Все", 6)
            ok, det = _ok_resolve(r if isinstance(r, dict) else {})
            if not ok:
                row["ok"] = False
                row["detail"] = f"resolve fail: {det}"
            else:
                fn = r.get("top") or r.get("full_name")
                if not fn and r.get("items"):
                    fn = (r["items"][0] or {}).get("full_name")
                if r.get("need_clarification") and r.get("options"):
                    # pick first document if possible
                    for o in r.get("options") or []:
                        if str(o).startswith("Документ."):
                            fn = o
                            break
                    if not fn:
                        fn = (r.get("options") or [None])[0]
                if not fn:
                    row["ok"] = False
                    row["detail"] = "no full_name"
                else:
                    st = t.get_object_structure(str(fn))
                    ok2, det2 = _ok_tool_result("get_object_structure", st)
                    row["ok"] = ok2
                    row["detail"] = f"{fn} | {det2}"
                    row["tool"] = "get_object_structure"
        else:
            row["detail"] = f"unknown mode {mode}"
    except Exception as e:
        row["ok"] = False
        row["detail"] = f"EXC {e}"
    row["ms"] = int((time.time() - t0) * 1000)
    return row


def run_llm_sample(config: dict, tools: UniversalTools, questions: list[str]) -> list[dict]:
    from agent.loop import build_agent_from_config

    agent = build_agent_from_config(config, tools)
    out = []
    for q in questions:
        t0 = time.time()
        try:
            ans = agent.run(q, verbose=False)
            ok = bool(ans) and "Ошибка LLM" not in (ans or "") and len(ans or "") > 40
            out.append(
                {
                    "question": q,
                    "ok": ok,
                    "chars": len(ans or ""),
                    "preview": (ans or "")[:220].replace("\n", " "),
                    "ms": int((time.time() - t0) * 1000),
                }
            )
        except Exception as e:
            out.append(
                {
                    "question": q,
                    "ok": False,
                    "chars": 0,
                    "preview": str(e)[:200],
                    "ms": int((time.time() - t0) * 1000),
                }
            )
        agent.reset()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-llm", action="store_true")
    ap.add_argument("--out", default="exports/universal_qa_smoke.json")
    args = ap.parse_args()

    cfg = load_config()
    t = UniversalTools(
        create_backend(cfg),
        read_only=True,
        default_limit=25,
        max_limit=80,
        code_dir=cfg.get("code_dir") or "",
    )

    # connection
    conn = t.test_connection() if hasattr(t, "test_connection") else {}
    print("=== CONNECTION ===")
    print(conn if isinstance(conn, dict) else conn)

    print("\n=== CAPABILITIES (routes) ===")
    caps = [
        "meta/resolve — любые Документ/Справочник/Регистр по фразе",
        "data — build_report / linked_query / margin_report",
        "structure — реквизиты, ТЧ, статусы",
        "movements — регистры проведения",
        "fill — почему не заполнился / как заполняется",
        "code — inspect/search modules, СКД",
        "locks — analyze + recommend managed",
        "access — роли/права",
        "help — синтаксис запросов/BSL",
    ]
    for c in caps:
        print(" •", c)

    print(f"\n=== TOOL/PREFLIGHT CASES ({len(CASES)}) ===")
    results = []
    for cid, cat, q, mode in CASES:
        row = run_case(t, cid, cat, q, mode)
        results.append(row)
        mark = "OK " if row["ok"] else "FAIL"
        print(f"  {mark} {cid} [{cat:10}] {row.get('tool') or mode:22} {row['ms']:5}ms | {row['detail'][:90]}")
        print(f"       Q: {q[:70]}")

    by_cat: dict[str, list] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r)
    print("\n=== BY CATEGORY ===")
    for cat, rows in sorted(by_cat.items()):
        ok = sum(1 for x in rows if x["ok"])
        print(f"  {cat:12} {ok}/{len(rows)}")

    ok_n = sum(1 for x in results if x["ok"])
    print(f"\nTOTAL tools: {ok_n}/{len(results)} OK")

    llm_results = []
    if args.with_llm:
        print("\n=== LLM AGENT SAMPLE ===")
        sample_q = [
            "Сколько реализаций за 2015?",
            "По каким регистрам проводится реализация?",
            "Почему может не заполниться контрагент?",
            "Найди спецификацию номенклатуры",
            "Куда ставить управляемые блокировки для реализации?",
        ]
        llm_results = run_llm_sample(cfg, t, sample_q)
        for x in llm_results:
            mark = "OK " if x["ok"] else "FAIL"
            print(f"  {mark} {x['ms']:5}ms | {x['question'][:50]}")
            print(f"       {x['preview'][:160]}")

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "connection": conn if isinstance(conn, dict) else str(conn),
        "tools": results,
        "tools_ok": ok_n,
        "tools_total": len(results),
        "llm": llm_results,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")
    return 0 if ok_n >= len(results) * 0.7 else 1


if __name__ == "__main__":
    raise SystemExit(main())
