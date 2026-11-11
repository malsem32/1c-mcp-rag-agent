# -*- coding: utf-8 -*-
"""
Индексация JSONL (код + справка) в Qdrant.

  # 1) подготовка чанков
  python scripts/prepare_config_for_qdrant.py

  # 2) загрузка в Qdrant (local path из config.yaml)
  python scripts/index_qdrant.py
  python scripts/index_qdrant.py --recreate
  python scripts/index_qdrant.py --code-only
  python scripts/index_qdrant.py --help-only

  # 3) проверка
  python scripts/index_qdrant.py --status
  python cli.py "где в коде ТекстЗапроса динамического списка"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import load_config  # noqa: E402
from vector.client import qdrant_status, reset_qdrant_cache  # noqa: E402
from vector.index import index_jsonl  # noqa: E402


def _prepare_help_jsonl(cfg: dict, out_path: Path, *, limit: int = 0) -> Path | None:
    """Собрать md-справку платформы в JSONL для коллекции help."""
    knowledge = Path(cfg.get("paths", {}).get("knowledge_dir") or ROOT / "knowledge")
    if not knowledge.is_absolute():
        knowledge = ROOT / knowledge
    help_root = knowledge / "справка_платформы"
    if not help_root.is_dir():
        return None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out_path.open("w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "id": "__meta__",
                    "text": "",
                    "payload": {"purpose": "platform_help"},
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        for md in help_root.rglob("*.md"):
            if md.name.lower() == "readme.md":
                continue
            try:
                text = md.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if len(text) < 80:
                continue
            rel = md.relative_to(knowledge).as_posix()
            title = text.splitlines()[0].lstrip("# ").strip()[:140] if text else md.stem
            body = re.sub(r"\n\s*\n+", "\n", text).strip()
            if len(body) > 8000:
                body = body[:8000] + "\n…"
            doc = {
                "id": "help_" + rel,
                "text": f"# {title}\n\n{body}",
                "payload": {
                    "content_type": "Справка платформы",
                    "title": title,
                    "source_path": rel,
                    "char_count": len(body),
                },
            }
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
            n += 1
            if limit and n >= limit:
                break
        n += _append_user_docs(knowledge, f)
    if n == 0:
        return None
    print(f"Справка: {n} статей → {out_path}", file=sys.stderr)
    return out_path


def _doc_text(path: Path) -> str:
    """md/txt/html/pdf → текст. PDF — только если установлен pypdf."""
    suf = path.suffix.lower()
    if suf in (".md", ".txt"):
        return path.read_text(encoding="utf-8", errors="replace")
    if suf in (".html", ".htm"):
        sys.path.insert(0, str(ROOT / "scripts"))
        from unpack_hbk import html_to_text

        return html_to_text(path.read_bytes())
    if suf == ".pdf":
        try:
            from pypdf import PdfReader
        except Exception:
            print(f"  пропуск {path.name}: pip install pypdf", file=sys.stderr)
            return ""
        try:
            return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(path)).pages)
        except Exception as e:  # noqa: BLE001
            print(f"  пропуск {path.name}: {e}", file=sys.stderr)
            return ""
    return ""


def _split_by_headings(text: str, max_chars: int = 1600, overlap: int = 300) -> list[tuple[str, str]]:
    """
    Разрезать документ на разделы по заголовкам, длинные — по абзацам с перекрытием.
    max_chars ≈ окно эмбеддера (512 токенов ≈ 1.6–2 тыс. символов): хвост длиннее в вектор не попадал.
    «1.2 Название» — заголовок только в документах без markdown-заголовков (txt/pdf): в md это шаги инструкции.
    """
    md = bool(re.search(r"(?m)^\s*#{1,3}\s+\S", text))
    parts: list[tuple[str, str]] = []
    title, buf = "", []
    for ln in text.splitlines():
        if re.match(r"^\s*#{1,3}\s+\S", ln) or (
                not md and re.match(r"^\s*\d+(?:\.\d+){0,3}\.?\s+[А-ЯЁA-Z].{3,80}$", ln) and not ln.rstrip().endswith((";", ",", ":"))):
            if buf:
                parts.append((title, "\n".join(buf).strip()))
            title, buf = ln.strip("# ").strip(), [ln]
        else:
            buf.append(ln)
    if buf:
        parts.append((title, "\n".join(buf).strip()))
    out: list[tuple[str, str]] = []
    for t, body in parts:
        while len(body) > max_chars:
            cut = body.rfind("\n\n", 0, max_chars)
            cut = cut if cut > max_chars // 3 else (body.rfind("\n", 0, max_chars) if body.rfind("\n", 0, max_chars) > max_chars // 3 else max_chars)
            out.append((t, body[:cut].strip()))
            # перекрытие: последний абзац куска повторяется в начале следующего — шаг инструкции не рвётся
            tail = body[:cut].rstrip()
            back = tail.rfind("\n\n", max(0, len(tail) - overlap))
            body = (tail[back:].strip() + "\n\n" if back > 0 else "") + body[cut:].strip()
        if len(body) >= 80:
            out.append((t, body))
    return out


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """md с блоком `---\\nkey: value\\n---` в начале → (метаданные, текст без блока)."""
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for ln in m.group(1).splitlines():
        k, sep, v = ln.partition(":")
        if sep and k.strip():
            meta[k.strip().lower()] = v.strip()
    return meta, text[m.end():]


def _append_user_docs(knowledge: Path, f) -> int:
    """
    knowledge/документация/** — ваши материалы: выгруженные статьи ИТС (по подписке),
    стандарты разработки, регламенты, инструкции по доработкам. Индексируются вместе со справкой.
    """
    root = knowledge / "документация"
    if not root.is_dir():
        return 0
    n = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.lower() == "readme.md":
            continue
        if path.suffix.lower() not in (".md", ".txt", ".html", ".htm", ".pdf"):
            continue
        try:
            text = _doc_text(path)
        except OSError:
            continue
        meta, text = _split_frontmatter(text or "")
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
        if len(text) < 80:
            continue
        rel = path.relative_to(knowledge).as_posix()
        # priority из frontmatter (high | normal | low): low понижается в поиске справки
        priority = (meta.get("priority") or "normal").lower()
        doc_title = (text.splitlines()[0].lstrip("# ").strip() or path.stem)[:140]
        for i, (sec, body) in enumerate(_split_by_headings(text)):
            title = f"{doc_title} — {sec}"[:200] if sec and sec != doc_title else doc_title
            # строка заголовка раздела уже в «# title» — второй раз она только съедает окно эмбеддера
            body = re.sub(r"\A\s*#{1,3}\s+[^\n]*\n*", "", body).strip() or body
            f.write(
                json.dumps(
                    {
                        "id": f"doc_{rel}#{i}",
                        "text": f"# {title}\n\n{body}",
                        "payload": {
                            "content_type": "Документация",
                            "title": title,
                            "source_path": rel,
                            "section": i,
                            "priority": priority,
                            # откуда статья (frontmatter source: «ИТС … Руководство разработчика») — для ссылки в ответе
                            **({"doc_source": meta["source"]} if meta.get("source") else {}),
                            "char_count": len(body),
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            n += 1
    if n:
        print(f"Документация: {n} разделов из {root}", file=sys.stderr)
    return n


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()
    paths = cfg.get("paths") or {}
    qcfg = cfg.get("qdrant") or {}
    emb = cfg.get("embedding") or {}

    default_code = (
        qcfg.get("chunks_jsonl")
        or paths.get("chunks_jsonl")
        or "knowledge/qdrant_ready/config_chunks.jsonl"
    )
    default_help = (
        qcfg.get("help_jsonl")
        or paths.get("help_jsonl")
        or "knowledge/qdrant_ready/help_chunks.jsonl"
    )

    ap = argparse.ArgumentParser(description="Индекс JSONL → Qdrant")
    ap.add_argument("--code-jsonl", type=Path, default=Path(default_code))
    ap.add_argument("--help-jsonl", type=Path, default=Path(default_help))
    ap.add_argument("--recreate", action="store_true", help="Пересоздать коллекции")
    ap.add_argument("--code-only", action="store_true")
    ap.add_argument("--help-only", action="store_true")
    ap.add_argument("--full", action="store_true", help="Не пропускать неизменённые чанки (пересчитать всё)")
    ap.add_argument("--collection-code", default="", help="Имя коллекции кода (вместо qdrant.collection_code) — для сборки нового индекса рядом со старым")
    ap.add_argument("--repair-vectors", action="store_true", help="Найти точки с нулевым вектором и пересчитать только их")
    ap.add_argument("--backfill-payload", action="store_true", help="Дописать proc_words в payload уже загруженных точек (без пересчёта векторов)")
    ap.add_argument("--no-catalog", action="store_true", help="Не перезаписывать общий каталог имён объектов (при подключении другой конфигурации)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--batch-size", type=int, default=int(emb.get("batch_size") or 32))
    ap.add_argument("--skip-help-build", action="store_true")
    ap.add_argument("--files-only", action="store_true", help="Только файлы выгрузки (коллекция qdrant.collection_files)")
    ap.add_argument("--no-files", action="store_true", help="Не загружать файлы выгрузки")
    ap.add_argument("--code-dir", default="", help="Папка выгрузки (вместо paths.code_dir)")
    args = ap.parse_args(argv)

    if args.collection_code:
        cfg.setdefault("qdrant", {})["collection_code"] = args.collection_code

    if args.status:
        st = qdrant_status(cfg)
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0 if st.get("ok") else 1

    if args.backfill_payload:
        from vector.index import backfill_payload

        r = backfill_payload(cfg, kind="code", log=lambda m: print(m, file=sys.stderr, flush=True))
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    if args.repair_vectors:
        from vector.index import repair_vectors

        r = repair_vectors(cfg, kind="help" if args.help_only else "code", batch_size=args.batch_size,
                           log=lambda m: print(m, file=sys.stderr, flush=True))
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    if not (cfg.get("qdrant") or {}).get("enabled", True):
        print("qdrant.enabled=false в config.yaml — включите индексацию", file=sys.stderr)
        return 1

    reset_qdrant_cache()
    results = []

    do_code = not args.help_only and not args.files_only
    do_help = not args.code_only and not args.files_only
    do_files = (args.files_only or do_code) and not args.no_files

    if do_code:
        code_path = args.code_jsonl
        if not code_path.is_absolute():
            code_path = ROOT / code_path
        if not code_path.is_file():
            print(
                f"Нет {code_path}\nСначала: python scripts/prepare_config_for_qdrant.py",
                file=sys.stderr,
            )
            return 1
        print(f"Индексация кода: {code_path}", file=sys.stderr)
        r = index_jsonl(
            code_path,
            cfg=cfg,
            kind="code",
            recreate=args.recreate,
            batch_size=args.batch_size,
            incremental=not args.full,
            write_catalog=not args.no_catalog,
            log=lambda m: print(m, file=sys.stderr),
        )
        print(json.dumps(r, ensure_ascii=False, indent=2))
        results.append(r)
        if not r.get("ok"):
            return 1

    if do_files:
        from vector.files import index_files

        code_dir = args.code_dir or paths.get("code_dir") or cfg.get("code_dir") or ""
        print(f"Файлы выгрузки → Qdrant: {code_dir}", file=sys.stderr)
        r = index_files(cfg, code_dir, recreate=args.recreate, log=lambda m: print(m, file=sys.stderr))
        print(json.dumps(r, ensure_ascii=False, indent=2))
        results.append(r)
        if not r.get("ok"):
            return 1

    if do_help:
        help_path = args.help_jsonl
        if not help_path.is_absolute():
            help_path = ROOT / help_path
        if not args.skip_help_build or not help_path.is_file():
            built = _prepare_help_jsonl(cfg, help_path)
            if built is None:
                print("Справка платформы не найдена — skip help", file=sys.stderr)
                do_help = False
        if do_help and help_path.is_file():
            print(f"Индексация справки: {help_path}", file=sys.stderr)
            r = index_jsonl(
                help_path,
                cfg=cfg,
                kind="help",
                recreate=args.recreate,
                batch_size=args.batch_size,
                incremental=not args.full,
                log=lambda m: print(m, file=sys.stderr),
            )
            print(json.dumps(r, ensure_ascii=False, indent=2))
            results.append(r)

    print("\n=== STATUS ===", file=sys.stderr)
    print(json.dumps(qdrant_status(cfg), ensure_ascii=False, indent=2))
    print(
        "\nПроверка агентом:\n"
        '  python cli.py "найди модуль где ТекстЗапроса"\n'
        '  python cli.py "как в BSL объявить процедуру"',
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
