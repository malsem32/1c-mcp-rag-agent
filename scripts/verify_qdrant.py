# -*- coding: utf-8 -*-
"""Быстрая проверка: Qdrant + search_code + (опц.) agent tools."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from config import load_config
    from tools.code_search import code_status, search_code
    from vector.client import qdrant_status

    cfg = load_config()
    print("=== config ===")
    print("code_dir:", cfg.get("code_dir"))
    print("qdrant.mode:", (cfg.get("qdrant") or {}).get("mode"))
    print("embedding:", (cfg.get("embedding") or {}).get("model"))

    print("\n=== qdrant_status ===")
    print(json.dumps(qdrant_status(cfg), ensure_ascii=False, indent=2))

    print("\n=== code_status ===")
    st = code_status()
    print(
        json.dumps(
            {
                "ready": st.get("ready"),
                "qdrant_ready": st.get("qdrant_ready"),
                "qdrant_points": st.get("qdrant_points"),
                "roots": st.get("roots"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    print("\n=== search_code('ТекстЗапроса') ===")
    r = search_code("ТекстЗапроса", max_matches=3)
    print("source:", r.get("source"), "count:", r.get("count") or len(r.get("matches") or []))
    for m in (r.get("matches") or [])[:3]:
        print(f"  [{m.get('score')}] {m.get('location')}")
        sn = (m.get("snippet") or "").replace("\n", " ")[:140]
        print(f"    {sn}")

    if not st.get("qdrant_ready"):
        print(
            "\n⚠ Индекс пуст. Запустите:\n"
            "  python scripts/prepare_config_for_qdrant.py\n"
            "  python scripts/index_qdrant.py --recreate",
            file=sys.stderr,
        )
        return 1

    print("\nOK — можно: python cli.py \"где в коде ТекстЗапроса\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
