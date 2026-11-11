# -*- coding: utf-8 -*-
"""Прогон всех scripts/_smoke_*.py — сводка PASS/FAIL."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = sorted(ROOT.joinpath("scripts").glob("_smoke_*.py"))
# не гонять себя
SCRIPTS = [p for p in SCRIPTS if p.name != "_run_all_smokes.py"]


def run_one(path: Path, timeout: int = 180) -> dict:
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        # truncate
        if len(out) > 4000:
            out = out[:2000] + "\n…\n" + out[-1500:]
        return {
            "name": path.name,
            "ok": proc.returncode == 0,
            "code": proc.returncode,
            "sec": round(time.time() - t0, 1),
            "tail": out.strip()[-800:] if out.strip() else "(empty)",
        }
    except subprocess.TimeoutExpired:
        return {
            "name": path.name,
            "ok": False,
            "code": -1,
            "sec": timeout,
            "tail": f"TIMEOUT after {timeout}s",
        }
    except Exception as e:
        return {
            "name": path.name,
            "ok": False,
            "code": -2,
            "sec": round(time.time() - t0, 1),
            "tail": str(e),
        }


def main() -> int:
    print(f"ROOT={ROOT}")
    print(f"smokes={len(SCRIPTS)}")
    results = []
    for p in SCRIPTS:
        print(f"\n=== RUN {p.name} ===", flush=True)
        r = run_one(p, timeout=240)
        results.append(r)
        status = "PASS" if r["ok"] else "FAIL"
        print(f"[{status}] {r['name']} ({r['sec']}s) code={r['code']}", flush=True)
        if not r["ok"]:
            print(r["tail"][:600], flush=True)

    passed = sum(1 for r in results if r["ok"])
    failed = [r for r in results if not r["ok"]]
    print("\n" + "=" * 60)
    print(f"SUMMARY: {passed}/{len(results)} PASS, {len(failed)} FAIL")
    for r in results:
        print(f"  {'OK' if r['ok'] else 'XX'}  {r['name']:40s}  {r['sec']:>6}s")
    if failed:
        print("\nFAILURES:")
        for r in failed:
            print(f"\n--- {r['name']} ---")
            print(r["tail"][:1200])
    # write json summary
    import json

    outp = ROOT / "exports" / "smoke_all_summary.json"
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nWrote {outp}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
