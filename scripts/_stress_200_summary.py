# -*- coding: utf-8 -*-
"""Сводка по exports/stress_200.jsonl → stdout + exports/stress_200_summary.json"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "exports" / "stress_200.jsonl"
recs = [json.loads(x) for x in src.read_text(encoding="utf-8").splitlines() if x.strip()]

EMPTY_MARK = '"_truncate_note": "Сильное ужатие'


def bad_pre(r):  # уверенный preflight, но не тот tool
    return r.get("preflight") and not r.get("route_ok")


by = defaultdict(list)
for r in recs:
    by[r["cat"]].append(r)

rows = []
tot = Counter()
for cat, rs in by.items():
    n = len(rs)
    c = Counter()
    for r in rs:
        c["route_ok"] += bool(r.get("route_ok"))
        c["preflight"] += bool(r.get("preflight"))
        c["wrong_preflight"] += bool(bad_pre(r))
        c["llm_decides"] += not r.get("route_tool")
        c["exec_err"] += bool(r.get("res_error") or r.get("exec_error"))
        c["timeout"] += r.get("exec_error") == "timeout"
        c["result_ok"] += bool(r.get("result_ok"))
        c["must_total"] += r.get("must_ok") is not None
        c["must_ok"] += r.get("must_ok") is True
        c["empty_compact"] += EMPTY_MARK in (r.get("payload_head") or "") and len(r.get("payload_head") or "") < 700
        c["clar"] += bool(r.get("need_clarification"))
        c["ms"] += int(r.get("exec_ms") or 0)
        c["slow"] += int(r.get("exec_ms") or 0) > 15000
        # «хороший итог» = верный tool (или LLM выберет) + результат ok + must ok/None + не пустое ужатие
        good = (
            (r.get("route_ok") or not r.get("route_tool"))
            and r.get("result_ok")
            and r.get("must_ok") is not False
            and EMPTY_MARK not in (r.get("payload_head") or "")[:700]
        )
        c["good"] += bool(good)
    tot.update(c)
    tot["n"] += n
    rows.append((cat, n, c))

rows.sort(key=lambda x: x[0])
hdr = f"{'cat':<8}{'n':>4}{'route':>7}{'wrongPF':>8}{'LLM':>5}{'resOK':>7}{'must':>8}{'empty':>6}{'err':>5}{'slow':>5}{'good':>6}{'avg_s':>7}"
print(hdr)
for cat, n, c in rows:
    print(
        f"{cat:<8}{n:>4}{c['route_ok']:>7}{c['wrong_preflight']:>8}{c['llm_decides']:>5}{c['result_ok']:>7}"
        f"{str(c['must_ok'])+'/'+str(c['must_total']):>8}{c['empty_compact']:>6}{c['exec_err']:>5}{c['slow']:>5}{c['good']:>6}{c['ms']/n/1000:>7.1f}"
    )
n = tot["n"]
print("-" * len(hdr))
print(
    f"{'ALL':<8}{n:>4}{tot['route_ok']:>7}{tot['wrong_preflight']:>8}{tot['llm_decides']:>5}{tot['result_ok']:>7}"
    f"{str(tot['must_ok'])+'/'+str(tot['must_total']):>8}{tot['empty_compact']:>6}{tot['exec_err']:>5}{tot['slow']:>5}{tot['good']:>6}{tot['ms']/n/1000:>7.1f}"
)
print("\nwrong preflight (tool → ожидалось):")
wp = Counter()
for r in recs:
    if bad_pre(r):
        wp[(r["route_tool"], "/".join(r["expect"][:2]))] += 1
for (g, e), k in wp.most_common(40):
    print(f"  {k:>2}× {g:<24} → {e}")
print("\nerrors:")
for r in recs:
    if r.get("res_error") or r.get("exec_error"):
        print(f"  #{r['id']} {r.get('exec_tool')}: {(r.get('res_error') or r.get('exec_error'))[:110]} | {r['q'][:60]}")
(ROOT / "exports" / "stress_200_summary.json").write_text(
    json.dumps({"totals": tot, "by_cat": {c: dict(x) for c, _, x in rows}}, ensure_ascii=False, indent=1),
    encoding="utf-8",
)
