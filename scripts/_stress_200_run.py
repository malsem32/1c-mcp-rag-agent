# -*- coding: utf-8 -*-
"""
Прогон 200+ вопросов без LLM: routing (classify) → выполнение tool на живой базе → проверки.

  python scripts/_stress_200_run.py                 # всё
  python scripts/_stress_200_run.py --cat code,fill  # только категории
  python scripts/_stress_200_run.py --route-only

Результат: exports/stress_200.jsonl (+ сводка в stdout).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import importlib  # noqa: E402

OUT = ROOT / "exports" / "stress_200.jsonl"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat", default="")
    ap.add_argument("--ids", default="")
    ap.add_argument("--route-only", action="store_true")
    ap.add_argument("--timeout", type=float, default=150.0)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--cases", default="_stress_200_cases", help="модуль с CASES (напр. _holdout_cases)")
    a = ap.parse_args()
    CASES = importlib.import_module(a.cases).CASES

    from config import load_config
    from backends import create_backend
    from tools.universal import UniversalTools
    from tools.router import classify, compute_hints
    from tools.preflight import preflight_result_ok
    from context_budget import compact_json

    cfg = load_config()
    tools = None
    if not a.route_only:
        tools = UniversalTools(
            create_backend(cfg),
            read_only=True,
            default_limit=cfg["agent"]["query_default_limit"],
            max_limit=cfg["agent"]["query_max_limit"],
            list_objects_cap=cfg["agent"].get("list_objects_cap", 80),
            tool_result_max_chars=cfg["llm"].get("tool_result_max_chars", 8000),
            compact_results=True,
            code_dir=cfg.get("code_dir") or "",
        )

    cats = {c for c in a.cat.split(",") if c}
    ids = {int(x) for x in a.ids.split(",") if x}
    cases = [c for c in CASES if (not cats or c["cat"] in cats) and (not ids or c["id"] in ids)]
    out = open(a.out, "w", encoding="utf-8")
    pool = cf.ThreadPoolExecutor(max_workers=1)

    for c in cases:
        q = c["q"]
        rec = dict(c)
        t0 = time.time()
        try:
            d = classify(q) or {}
        except Exception as e:
            d = {"error": repr(e)}
        rec["route_ms"] = int((time.time() - t0) * 1000)
        rec["route_tool"] = d.get("tool")
        rec["route_conf"] = d.get("confidence")
        rec["route_reason"] = d.get("reason")
        rec["route_args"] = d.get("args")
        try:
            rec["hints"] = {k: v for k, v in compute_hints(q).items() if v}
        except Exception as e:
            rec["hints"] = {"error": repr(e)}
        pre = bool(d.get("tool")) and float(d.get("confidence") or 0) >= 0.78
        rec["preflight"] = pre
        exp = c["expect"]
        if "NONE" in exp and not d.get("tool"):
            rec["route_ok"] = True
        else:
            rec["route_ok"] = bool(d.get("tool") in exp)

        if tools is not None:
            if pre:
                tool, args = d["tool"], dict(d.get("args") or {})
            elif exp[0] != "NONE":
                tool = exp[0]
                args = {"request": q, "search_query": q, "query": q, "phrase": q}
            else:
                tool = None
            rec["exec_tool"] = tool
            rec["exec_by"] = "preflight" if pre else "expected"
            if tool:
                t1 = time.time()
                fut = pool.submit(tools.dispatch, tool, args)
                try:
                    res = fut.result(timeout=a.timeout)
                    rec["exec_error"] = None
                except cf.TimeoutError:
                    res = {"error": f"TIMEOUT>{a.timeout}s"}
                    rec["exec_error"] = "timeout"
                    # пул занят зависшим вызовом — новый
                    pool = cf.ThreadPoolExecutor(max_workers=1)
                except Exception as e:
                    res = {"error": repr(e)}
                    rec["exec_error"] = repr(e)[:300]
                rec["exec_ms"] = int((time.time() - t1) * 1000)
                try:
                    raw_full = json.dumps(res, ensure_ascii=False, default=str)
                except Exception:
                    raw_full = str(res)
                payload = compact_json(res, cfg["llm"].get("tool_result_max_chars", 8000), tool_name=tool)
                rec["raw_chars"] = len(raw_full)
                rec["payload_chars"] = len(payload)
                rec["result_ok"] = preflight_result_ok(tool, res)
                if isinstance(res, dict):
                    rec["res_keys"] = list(res.keys())[:30]
                    rec["res_error"] = str(res.get("error") or "")[:300] or None
                    rec["need_clarification"] = bool(res.get("need_clarification"))
                low = payload.lower()
                rec["must_hit"] = {m: (m.lower() in low) for m in c["must"]}
                rec["must_ok"] = all(rec["must_hit"].values()) if c["must"] else None
                rec["payload_head"] = payload[:2500]
        out.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        out.flush()
        print(
            f"#{c['id']:>3} {c['cat']:<7} route={'OK ' if rec['route_ok'] else 'BAD'} "
            f"{str(rec['route_tool']):<26} conf={rec['route_conf']} "
            + (
                f"exec={rec.get('exec_tool')} {rec.get('exec_ms')}ms ok={rec.get('result_ok')} must={rec.get('must_ok')} "
                f"pl={rec.get('payload_chars')} err={(rec.get('res_error') or '')[:60]!r}"
                if tools is not None
                else ""
            )
            + f" | {q[:70]}",
            flush=True,
        )
    out.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
