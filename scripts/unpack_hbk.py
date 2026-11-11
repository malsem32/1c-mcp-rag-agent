# -*- coding: utf-8 -*-
"""
Распаковка файлов справки 1С (.hbk) и выгрузка в папку знаний.

Формат hbk содержит вложенные ZIP-записи (локальные заголовки PK).
Скрипт извлекает HTML/текст и кладёт в knowledge/справка_платформы/.

Запуск:
  python scripts/unpack_hbk.py
  python scripts/unpack_hbk.py --src D:\\rag_agent\\info --out D:\\rag_agent\\knowledge\\справка_платформы
"""

from __future__ import annotations

import argparse
import html
import re
import struct
import zlib
from pathlib import Path

# имя архива → папка на русском
BOOK_NAMES = {
    "shquery_ru": "язык_запросов",
    "shquery_root": "язык_запросов_корень",
    "shlang_ru": "встроенный_язык",
    "shlang_root": "встроенный_язык_корень",
    "shclang_ru": "язык_встроенный_доп",
    "shclang_root": "язык_встроенный_доп_корень",
    "shcntx_ru": "синтаксис_помощник",
    "shcntx_root": "синтаксис_помощник_корень",
}


def _safe_name(name: str) -> str | None:
    name = name.replace("\\", "/").split("/")[-1].strip()
    if not name or ".." in name:
        return None
    # отсечь мусор
    if len(name) > 120:
        return None
    if re.search(r"[\x00-\x1f]", name):
        return None
    return name


def extract_zip_locals(data: bytes, out_dir: Path) -> list[Path]:
    """Извлечь файлы по локальным заголовкам ZIP внутри hbk."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    pos = 0
    seen: set[str] = set()

    while True:
        i = data.find(b"PK\x03\x04", pos)
        if i < 0:
            break
        if i + 30 > len(data):
            break
        try:
            (
                _ver,
                _flag,
                method,
                _mtime,
                _mdate,
                _crc,
                csize,
                _usize,
                nlen,
                elen,
            ) = struct.unpack_from("<HHHHHIIIHH", data, i + 4)
        except struct.error:
            pos = i + 4
            continue

        if nlen == 0 or nlen > 200:
            pos = i + 4
            continue

        raw_name = data[i + 30 : i + 30 + nlen]
        try:
            fname = raw_name.decode("utf-8")
        except UnicodeDecodeError:
            fname = raw_name.decode("cp1251", errors="replace")

        safe = _safe_name(fname)
        if not safe:
            pos = i + 4
            continue

        data_start = i + 30 + nlen + elen
        if data_start > len(data):
            pos = i + 4
            continue

        # размер сжатых данных
        if 0 < csize < 50_000_000 and data_start + csize <= len(data):
            payload = data[data_start : data_start + csize]
            next_pos = data_start + csize
        else:
            j = data.find(b"PK\x03\x04", i + 4)
            if j < 0:
                j = min(len(data), data_start + 5_000_000)
            payload = data[data_start:j]
            next_pos = j

        content: bytes | None = None
        if method == 0:
            content = payload
        else:
            for wbits in (-15, 15, zlib.MAX_WBITS):
                try:
                    content = zlib.decompress(payload, wbits)
                    break
                except Exception:
                    continue

        if content is None or len(content) < 10:
            pos = i + 4
            continue

        # уникальное имя
        base = safe
        if base in seen:
            stem = Path(base).stem
            suf = Path(base).suffix or ".bin"
            k = 2
            while f"{stem}_{k}{suf}" in seen:
                k += 1
            base = f"{stem}_{k}{suf}"
        seen.add(base)

        path = out_dir / base
        try:
            path.write_bytes(content)
            written.append(path)
        except OSError:
            pos = next_pos if next_pos > i else i + 4
            continue

        pos = next_pos if next_pos > i else i + 4

    return written


def html_to_text(raw: bytes | str) -> str:
    if isinstance(raw, bytes):
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        for enc in ("utf-8", "cp1251", "utf-16"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                text = raw.decode("utf-8", errors="replace")
    else:
        text = raw

    # убрать script/style
    text = re.sub(
        r"(?is)<(script|style)[^>]*>.*?</\1>",
        " ",
        text,
    )
    # заголовки
    text = re.sub(r"(?is)<h1[^>]*>", "\n# ", text)
    text = re.sub(r"(?is)<h2[^>]*>", "\n## ", text)
    text = re.sub(r"(?is)<h3[^>]*>", "\n### ", text)
    text = re.sub(r"(?is)</h[1-6]>", "\n", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)<p[^>]*>", "\n", text)
    text = re.sub(r"(?is)<li[^>]*>", "\n• ", text)
    text = re.sub(r"(?is)<pre[^>]*>", "\n", text)
    text = re.sub(r"(?is)</pre>", "\n", text)
    # всё остальное — выкинуть теги
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ").replace("\r", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def export_book(hbk_path: Path, raw_dir: Path, text_dir: Path) -> dict:
    data = hbk_path.read_bytes()
    raw_files = extract_zip_locals(data, raw_dir)
    md_count = 0
    text_dir.mkdir(parents=True, exist_ok=True)

    for f in raw_files:
        low = f.suffix.lower()
        if low in (".html", ".htm", ".st", ""):
            try:
                body = html_to_text(f.read_bytes())
            except Exception:
                continue
            if len(body) < 40:
                continue
            # имя файла
            stem = f.stem
            # русское имя из первого заголовка
            m = re.search(r"^#\s+(.+)$", body, re.M)
            title = m.group(1).strip() if m else stem
            title = re.sub(r'[<>:"/\\|?*]', "_", title)[:80]
            out_name = f"{stem}.md"
            out = text_dir / out_name
            out.write_text(
                f"# {title}\n\n"
                f"Источник: {hbk_path.name} / {f.name}\n\n"
                f"{body}\n",
                encoding="utf-8",
            )
            md_count += 1
        elif low in (".gif", ".png", ".jpg", ".jpeg"):
            # картинки не копируем в знания
            continue

    return {
        "hbk": hbk_path.name,
        "raw_files": len(raw_files),
        "md_files": md_count,
        "text_dir": str(text_dir),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Распаковка hbk → папка знаний")
    ap.add_argument(
        "--src",
        default="",
        help="Папка с .hbk (по умолчанию: info рядом с проектом)",
    )
    ap.add_argument(
        "--out",
        default="",
        help="Куда выгрузить текст (по умолчанию: knowledge/справка_платформы)",
    )
    ap.add_argument(
        "--only-ru",
        action="store_true",
        default=True,
        help="Только *_ru.hbk (русская справка)",
    )
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    src = Path(args.src) if args.src else root / "info"
    out = Path(args.out) if args.out else root / "knowledge" / "справка_платформы"
    raw_root = src / "unpack"

    if not src.is_dir():
        print(f"Нет папки: {src}")
        return 1

    hbks = sorted(src.glob("*.hbk"))
    if args.only_ru:
        hbks = [p for p in hbks if p.stem.endswith("_ru") or "_ru" in p.stem]

    if not hbks:
        # если only_ru пусто — все
        hbks = sorted(src.glob("*.hbk"))

    print(f"Найдено hbk: {len(hbks)} в {src}")
    results = []
    for hbk in hbks:
        book = BOOK_NAMES.get(hbk.stem, hbk.stem)
        raw_dir = raw_root / hbk.stem
        text_dir = out / book
        print(f"\n=== {hbk.name} → {text_dir}")
        info = export_book(hbk, raw_dir, text_dir)
        results.append(info)
        print(f"  сырых файлов: {info['raw_files']}, текстов: {info['md_files']}")

    # индекс для быстрого поиска
    out.mkdir(parents=True, exist_ok=True)
    import json

    index_rows: list[dict[str, str]] = []
    total_md = 0
    for info in results:
        book_dir = Path(info["text_dir"])
        book = book_dir.name
        n = 0
        for md in book_dir.glob("*.md"):
            if md.name == "README.md":
                continue
            try:
                head = md.read_text(encoding="utf-8", errors="replace")[:400]
            except OSError:
                continue
            if "�" in head and len(head) < 200:
                continue
            title = md.stem
            m = re.search(r"^#\s+(.+)$", head, re.M)
            if m:
                title = m.group(1).strip()[:120]
            index_rows.append(
                {
                    "path": str(md.relative_to(out)).replace("\\", "/"),
                    "name": md.stem.lower(),
                    "title": title,
                    "section": book,
                }
            )
            n += 1
        total_md += n
        info["md_files"] = n

    (out / "индекс.json").write_text(
        json.dumps(index_rows, ensure_ascii=False),
        encoding="utf-8",
    )

    index_lines = [
        "# Справка платформы 1С",
        "",
        "Распаковано из файлов .hbk (встроенная справка платформы).",
        "",
        "## Разделы",
        "",
    ]
    for info in results:
        book = Path(info["text_dir"]).name
        index_lines.append(
            f"- **{book}** — статей: {info['md_files']} (из {info['hbk']})"
        )
    index_lines += [
        "",
        f"Всего статей: {total_md}",
        "",
        "Обновить: python scripts/unpack_hbk.py",
        "",
        "Поиск: knowledge_search (или файл индекс.json)",
        "",
    ]
    (out / "README.md").write_text("\n".join(index_lines), encoding="utf-8")

    print(f"\nГотово. Тексты: {out}")
    print(f"Всего md: {total_md}")
    print(f"Индекс: {out / 'индекс.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
