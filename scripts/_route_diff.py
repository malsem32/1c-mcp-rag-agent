# -*- coding: utf-8 -*-
"""Сравнение routing: baseline vs новый прогон (--route-only). python scripts/_route_diff.py [new.jsonl] [base.jsonl]"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
new = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "exports" / "stress_route_new.jsonl"
base = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "exports" / "baseline" / "stress_200.jsonl"
b = {json.loads(l)["id"]: json.loads(l) for l in base.read_text(encoding="utf-8").splitlines() if l.strip()} if base.exists() else {}
n = {json.loads(l)["id"]: json.loads(l) for l in new.read_text(encoding="utf-8").splitlines() if l.strip()}
print(
    "base", sum(r["route_ok"] for r in b.values()) if b else "-",
    "new", sum(r["route_ok"] for r in n.values()), "of", len(n),
    "wrongPF", sum(bool(r["preflight"]) and not r["route_ok"] for r in n.values()),
    "LLM", sum(not r["route_tool"] for r in n.values()),
)
for i, r in n.items():
    if not r["route_ok"]:
        mark = "BROKE" if b.get(i, {}).get("route_ok") else "bad  "
        print(mark, i, r["q"][:85], "|", r["route_tool"], (r.get("route_reason") or "")[:50], "exp", r["expect"][:3])
