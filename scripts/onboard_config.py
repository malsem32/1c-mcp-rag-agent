# -*- coding: utf-8 -*-
"""
Подключение НОВОЙ конфигурации в отдельную коллекцию Qdrant, с проверкой и замером качества.
Рабочую конфигурацию не трогает, пока не указан --activate.

  python scripts/onboard_config.py --source D:\\dumps\\upp14 --name upp14
  python scripts/onboard_config.py --source D:\\dumps\\upp14 --name upp14 --limit 400 --eval-n 10   # быстрая проба
  python scripts/onboard_config.py --source D:\\dumps\\upp14 --name upp14 --activate

Шаги: 1) нарезка с чисткой → knowledge/qdrant_ready/<name>/chunks.jsonl
      2) индексация в коллекцию onec_code_<name> (проверка и ремонт нулевых векторов)
      3) индекс процедур в onec_procs_<name>
      4) оценка качества поиска (eval_search) на вопросах, построенных из этой выгрузки
      итог: exports/onboard_<name>.json; при --activate — переключение config.yaml
      (коллекции кода и процедур, путь выгрузки, чанки; каталоги имён/объектов — рядом с чанками)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
if str(ROOT) not in sys.path:  # panel / rag_agent импортируются из корня проекта
    sys.path.insert(0, str(ROOT))


def run(step: str, cmd: list[str]) -> int:
    print(f"\n=== {step} ===\n  {' '.join(cmd)}", flush=True)
    t = time.time()
    rc = subprocess.run(cmd, cwd=ROOT).returncode
    print(f"--- {step}: код {rc}, {(time.time() - t) / 60:.1f} мин", flush=True)
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, required=True, help="папка выгрузки конфигурации")
    ap.add_argument("--name", required=True, help="короткое имя (латиница/цифры) — войдёт в имя коллекции")
    ap.add_argument("--limit", type=int, default=0, help="только N первых файлов (быстрая проба)")
    ap.add_argument("--eval-n", type=int, default=25, help="вопросов каждого типа в оценке")
    ap.add_argument("--activate", action="store_true", help="после успеха сделать эту конфигурацию рабочей")
    ap.add_argument("--recreate", action="store_true", help="пересоздать коллекцию")
    a = ap.parse_args()

    name = re.sub(r"[^a-z0-9_]+", "_", a.name.lower()).strip("_")
    if not name:
        print("Некорректное --name", file=sys.stderr)
        return 2
    source = a.source if a.source.is_absolute() else (ROOT / a.source)
    if not source.is_dir():
        print(f"Нет папки выгрузки: {source}", file=sys.stderr)
        return 2
    col = f"onec_code_{name}"
    col_procs = f"onec_procs_{name}"
    work = ROOT / "knowledge" / "qdrant_ready" / name
    work.mkdir(parents=True, exist_ok=True)
    chunks = work / "chunks.jsonl"
    report: dict = {"name": name, "source": str(source), "collection": col, "steps": {}}

    prep = [PY, str(ROOT / "scripts" / "prepare_config_for_qdrant.py"), "--source", str(source), "--output", str(chunks)]
    if a.limit:
        prep += ["--limit", str(a.limit)]
    rc = run("1/4 нарезка и чистка", prep)
    report["steps"]["prepare"] = rc
    if rc:
        return _finish(report, name, rc)

    idx = [PY, str(ROOT / "scripts" / "index_qdrant.py"), "--code-only", "--code-jsonl", str(chunks),
           "--collection-code", col, "--no-catalog"]
    if a.recreate:
        idx.append("--recreate")
    rc = run("2/4 индексация и проверка векторов", idx)
    report["steps"]["index"] = rc
    if rc:
        print("Индексация завершилась ошибкой (в т. ч. из-за нулевых векторов) — оценку пропускаю.", file=sys.stderr)
        return _finish(report, name, rc)

    procs = [PY, str(ROOT / "scripts" / "index_procs.py"), "--chunks", str(chunks), "--collection", col_procs]
    if a.recreate:
        procs.append("--recreate")
    rc = run("3/4 индекс процедур", procs)
    report["steps"]["procs"] = rc
    if rc:
        return _finish(report, name, rc)

    out = ROOT / "exports" / f"eval_{name}.json"
    rc = run("4/4 оценка качества поиска", [PY, str(ROOT / "scripts" / "eval_search.py"), "--source", str(source),
                                            "--collection", col, "--n", str(a.eval_n), "--modes", "hybrid,semantic",
                                            "--out", str(out)] + (["--indexed-only"] if a.limit else []))
    report["steps"]["eval"] = rc
    if out.is_file():
        rows = json.loads(out.read_text(encoding="utf-8")).get("rows", [])
        report["quality_hybrid_hit10"] = {r["type"]: round(r["hit10"], 2) for r in rows if r["mode"] == "hybrid"}

    if a.activate and not any(report["steps"].values()):
        from panel.server import patch_yaml  # правка config.yaml с сохранением комментариев

        cfg = ROOT / "config.yaml"
        text = cfg.read_text(encoding="utf-8")
        # каталоги имён/объектов — там, где их ищет runtime: рядом с chunks_jsonl
        names = chunks.with_name(chunks.stem + ".object_names.jsonl")
        if names.is_file():
            (work / "object_names.jsonl").write_bytes(names.read_bytes())
        from tools.object_catalog import build_catalog_from_dump

        build_catalog_from_dump(source, out=work / "object_catalog.jsonl")
        rel = '"' + str(chunks.relative_to(ROOT)).replace("\\", "/") + '"'
        text = patch_yaml(text, "qdrant.collection_code", f'"{col}"')
        text = patch_yaml(text, "qdrant.collection_procs", f'"{col_procs}"')
        text = patch_yaml(text, "qdrant.chunks_jsonl", rel)
        text = patch_yaml(text, "paths.code_dir", '"' + str(source).replace("\\", "/") + '"')
        text = patch_yaml(text, "paths.chunks_jsonl", rel)
        cfg.write_text(text, encoding="utf-8")
        report["activated"] = True
        print(f"\nКонфигурация активирована: коллекция {col}. Перезапустите MCP-сервер.", flush=True)
    return _finish(report, name, 0 if not any(report["steps"].values()) else 1)


def _finish(report: dict, name: str, rc: int) -> int:
    p = ROOT / "exports" / f"onboard_{name}.json"
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nИтог: {p}\n{json.dumps(report.get('quality_hybrid_hit10', {}), ensure_ascii=False)}", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
