# -*- coding: utf-8 -*-
"""
End-to-end: реальная LLM + tools на подмножестве вопросов из _stress_200_cases.

  python scripts/_stress_e2e_llm.py --ids 1,36,85 [--model z-ai/glm-5.2:free]
Результат: exports/stress_e2e.jsonl
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from _stress_200_cases import CASES  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    ap.add_argument("--model", default="")
    ap.add_argument("--out", default=str(ROOT / "exports" / "stress_e2e.jsonl"))
    a = ap.parse_args()
    from config import load_config
    from backends import create_backend
    from tools.universal import UniversalTools
    from agent.loop import build_agent_from_config

    cfg = load_config()
    if a.model:
        cfg["llm"]["model"] = a.model
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
    ids = [int(x) for x in a.ids.split(",") if x]
    by_id = {c["id"]: c for c in CASES}
    with open(a.out, "a", encoding="utf-8") as out:
        for i in ids:
            c = by_id[i]
            agent = build_agent_from_config(cfg, tools)
            err = io.StringIO()
            t0 = time.time()
            with contextlib.redirect_stderr(err):
                try:
                    ans = agent.run(c["q"], verbose=True)
                except Exception as e:  # noqa: BLE001
                    ans = f"EXC {e!r}"
            log = err.getvalue()
            tools_called = [ln.split("(", 1)[0].replace("[tool] ", "") for ln in log.splitlines() if ln.startswith("[tool] ")]
            pre = [ln for ln in log.splitlines() if ln.startswith("[preflight]")]
            nudges = [ln for ln in log.splitlines() if "nudge" in ln]
            ctx_chars = sum(len(json.dumps(m, ensure_ascii=False)) for m in agent.messages)
            rec = {
                "id": i, "cat": c["cat"], "q": c["q"], "sec": round(time.time() - t0, 1),
                "preflight": pre, "tools": tools_called, "nudges": nudges,
                "ctx_chars": ctx_chars, "answer": ans,
            }
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            print(f"#{i} {rec['sec']}s tools={tools_called} pre={pre[:1]} nudges={len(nudges)}\n  Q: {c['q']}\n  A: {ans[:600]}\n", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
