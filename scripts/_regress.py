# -*- coding: utf-8 -*-
"""
Регресс после правок роутера / индексации: один запуск — один вердикт.

  python scripts/_regress.py                 # route-only (калибровка + hold-out) + резолвер
  python scripts/_regress.py --vector        # + векторная оценка кода (нужен Qdrant)
  python scripts/_regress.py --update        # принять текущие цифры как эталон

Эталон: exports/regress_baseline.json. Провал — метрика хуже эталона (или ниже пола).
Возвращает exit 1 при провале, чтобы можно было повесить на CI / после индексации.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
BASE = ROOT / "exports" / "regress_baseline.json"

# Абсолютные полы: ниже — провал, даже если эталона ещё нет.
FLOORS = {
    "calib.route_ok_pct": 97.0,
    "holdout.route_ok_pct": 95.0,
    "calib.wrong_preflight": None,  # «меньше — лучше», пола нет
    "holdout.wrong_preflight": None,
    "resolver.hit1_pct": 85.0,
}
LOWER_IS_BETTER = {"calib.wrong_preflight", "holdout.wrong_preflight"}


def _run(args: list[str]) -> str:
    p = subprocess.run([PY, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (p.stdout or "") + (p.stderr or "")


def route_metrics(cases_module: str, tag: str) -> dict:
    out = ROOT / "exports" / f"regress_route_{tag}.jsonl"
    _run(["scripts/_stress_200_run.py", "--route-only", "--cases", cases_module, "--out", str(out)])
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    n = len(rows) or 1
    ok = sum(bool(r["route_ok"]) for r in rows)
    return {
        "n": len(rows),
        "route_ok": ok,
        "route_ok_pct": round(100 * ok / n, 2),
        "wrong_preflight": sum(bool(r["preflight"]) and not r["route_ok"] for r in rows),
        "_bad": [(r["id"], r["q"][:80], r["route_tool"]) for r in rows if not r["route_ok"]],
    }


def resolver_metrics() -> dict:
    txt = _run(["scripts/_resolver_eval.py"])
    m = re.search(r"hit@1\s+(\d+)/(\d+)", txt)
    if not m:
        return {"error": txt[-300:]}
    h, n = int(m.group(1)), int(m.group(2))
    return {"n": n, "hit1": h, "hit1_pct": round(100 * h / n, 2), "_bad": re.findall(r"^BAD (.+)$", txt, re.M)[:15]}


def vector_metrics() -> dict:
    txt = _run(["scripts/_stress_vector_eval.py", "--modes", "hybrid,exact"])
    res = {}
    for mode, h1, hk, mrr in re.findall(r"^(\w+)\s+hit@1=([\d.]+) hit@\d+=([\d.]+) MRR=([\d.]+)", txt, re.M):
        res[mode] = {"hit1": float(h1), "hitk": float(hk), "mrr": float(mrr)}
    return res or {"error": txt[-300:]}


def flatten(d: dict, pre: str = "") -> dict:
    out = {}
    for k, v in d.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict):
            out.update(flatten(v, f"{pre}{k}."))
        elif isinstance(v, (int, float)):
            out[f"{pre}{k}"] = v
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vector", action="store_true")
    ap.add_argument("--update", action="store_true")
    a = ap.parse_args()

    cur = {
        "calib": route_metrics("_stress_200_cases", "calib"),
        "holdout": route_metrics("_holdout_cases", "holdout"),
        "resolver": resolver_metrics(),
    }
    if a.vector:
        cur["vector"] = vector_metrics()

    flat = flatten(cur)
    base = flatten(json.loads(BASE.read_text(encoding="utf-8"))) if BASE.exists() else {}
    fails: list[str] = []

    for key, floor in FLOORS.items():
        v = flat.get(key)
        if v is not None and floor is not None and v < floor:
            fails.append(f"{key}={v} < пола {floor}")
    for key, v in flat.items():
        if key not in base or key.endswith(".n") or ".route_ok" == key[-9:] or key.endswith(".hit1") or key.endswith("ms"):
            continue
        b = base[key]
        worse = v > b if key in LOWER_IS_BETTER else v < b - (0.5 if "pct" in key else 0.03 if key.startswith("vector") else 0)
        if worse:
            fails.append(f"{key}: было {b}, стало {v}")

    print("=== regress ===")
    for key in sorted(flat):
        if key.endswith(("route_ok_pct", "wrong_preflight", "hit1_pct")) or key.startswith("vector"):
            print(f"  {key:<32} {flat[key]:>8}   (эталон {base.get(key, '—')})")
    for tag in ("calib", "holdout"):
        for i, q, t in cur[tag]["_bad"][:10]:
            print(f"  мимо [{tag}] #{i} {q} -> {t}")
    for q in cur["resolver"].get("_bad", []):
        print(f"  резолвер BAD {q}")

    if a.update:
        BASE.write_text(json.dumps(cur, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print("эталон обновлён:", BASE)
        return 0
    if fails:
        print("\nПРОВАЛ:")
        for f in fails:
            print("  -", f)
        return 1
    print("\nOK" + ("" if base else " (эталона нет — запусти с --update)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
