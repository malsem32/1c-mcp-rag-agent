# -*- coding: utf-8 -*-
"""
Полный прогон: prepare → index Qdrant.

  python scripts/full_qdrant_pipeline.py
  python scripts/full_qdrant_pipeline.py --code-only
  python scripts/full_qdrant_pipeline.py --skip-prepare

См. scripts/ПОЛНЫЙ_ПРОГОН_QDRANT.md
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def run(cmd: list[str], *, step: str) -> int:
    print("\n" + "=" * 60, flush=True)
    print(f"▶ {step}", flush=True)
    print(" ", " ".join(cmd), flush=True)
    print("=" * 60, flush=True)
    t0 = time.time()
    p = subprocess.run(cmd, cwd=str(ROOT))
    dt = time.time() - t0
    print(f"◀ {step}: exit={p.returncode}, {dt / 60:.1f} мин", flush=True)
    return int(p.returncode)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Полный прогон prepare + index Qdrant")
    ap.add_argument("--skip-prepare", action="store_true", help="Только index")
    ap.add_argument("--code-only", action="store_true", help="Без справки платформы")
    ap.add_argument("--help-only", action="store_true", help="Только справка (нужен index)")
    ap.add_argument("--recreate", action="store_true", help="Пересоздать коллекции (по умолчанию — инкрементально)")
    ap.add_argument("--skip-verify", action="store_true")
    args = ap.parse_args(argv)

    t_all = time.time()
    print(f"Python: {PY}", flush=True)
    print(f"Root:   {ROOT}", flush=True)
    print("Инструкция: scripts/ПОЛНЫЙ_ПРОГОН_QDRANT.md", flush=True)

    if not args.skip_prepare and not args.help_only:
        rc = run(
            [PY, str(ROOT / "scripts" / "prepare_config_for_qdrant.py")],
            step="1/3 prepare_config_for_qdrant",
        )
        if rc != 0:
            print("ОШИБКА prepare — index не запускаем", file=sys.stderr, flush=True)
            return rc

    index_cmd = [PY, str(ROOT / "scripts" / "index_qdrant.py")]
    if args.recreate:
        index_cmd.append("--recreate")
    if args.code_only:
        index_cmd.append("--code-only")
    if args.help_only:
        index_cmd.append("--help-only")

    rc = run(index_cmd, step="2/3 index_qdrant")
    if rc != 0:
        # PowerShell часто даёт 1 из‑за stderr-warning — проверим status отдельно
        print("index вернул ненулевой код — проверяем status…", flush=True)

    if not args.skip_verify:
        rc2 = run(
            [PY, str(ROOT / "scripts" / "verify_qdrant.py")],
            step="3/3 verify_qdrant",
        )
    else:
        rc2 = 0

    rc3 = run(
        [PY, str(ROOT / "scripts" / "index_qdrant.py"), "--status"],
        step="status",
    )

    total = (time.time() - t_all) / 60
    print("\n" + "=" * 60, flush=True)
    print(f"ГОТОВО за {total:.1f} мин", flush=True)
    print("Дальше:", flush=True)
    print('  python cli.py "найди в коде ТекстЗапроса"', flush=True)
    print("=" * 60, flush=True)
    # успех если verify ок (или skip) — index warnings не валят прогон
    return 0 if rc2 == 0 or args.skip_verify else rc2


if __name__ == "__main__":
    raise SystemExit(main())
