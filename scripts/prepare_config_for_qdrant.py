# -*- coding: utf-8 -*-
"""
Подготовка выгрузки 1С → JSONL для Qdrant.

  python scripts/prepare_config_for_qdrant.py
  python scripts/index_qdrant.py

Пути, лимиты — из config.yaml (секции paths / qdrant / prepare).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from xml.etree.ElementTree import ParseError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import load_config  # noqa: E402
from ingest.chunking import (  # noqa: E402,F401
    clean_code_text,
    is_bsl_or_query,
    iter_pieces,
    split_large,
)
from ingest.quality import DropReport, clean_chunk_text, reject_reason  # noqa: E402
from ingest.skip_names import is_skipped_name  # noqa: E402
from metadata_paths import (  # noqa: E402
    localize_parts,
    relative_parts,
    translate_segment,
)

# ---------------------------------------------------------------------------
# Эвристики
# ---------------------------------------------------------------------------

QUERY_TAG_NAMES = frozenset(
    {"query", "querytext", "customquery", "querytexttemplate"}
)

SKIP_DIR_NAMES = frozenset(
    {".git", ".svn", "__pycache__", ".idea", "node_modules", "bin", "obj"}
)
SKIP_FILE_NAMES = frozenset(
    {"configdumpinfo.xml", "configuration.xml", "parentconfigurations.bin"}
)
SKIP_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".zip",
        ".bin",
        ".cf",
        ".html",
        ".htm",
        ".css",
        ".js",
        ".md",
        ".txt",
        ".pdf",
        ".xsd",
    }
)

SKIP_XML_TAGS = frozenset(
    {
        "comment",
        "content",
        "title",
        "tooltip",
        "synonym",
        "presentation",
        "extendedtooltip",
        "explanation",
        "description",
        "inputhint",
        "displayname",
        "event",
        "manualquery",
        "name",
        "id",
        "type",
        "value",
        "lang",
        "picture",
        "mdclass",
        "internalinfo",
        "properties",
        "childobjects",
    }
)



@dataclass
class CodeChunk:
    id: str
    text: str
    content_type: str
    metadata_class: str
    metadata_class_en: str
    object_name: str
    location: str
    source_path: str
    xml_tag: str
    char_count: int
    extra: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        payload = {
            "content_type": self.content_type,
            "metadata_class": self.metadata_class,
            "metadata_class_en": self.metadata_class_en,
            "object_name": self.object_name,
            "location": self.location,
            "source_path": self.source_path,
            "xml_tag": self.xml_tag,
            "char_count": self.char_count,
        }
        payload.update(self.extra)
        return {"id": self.id, "text": self.text, "payload": payload}


# Чистка: настраивается из main() (prepare.clean / prepare.min_body_chars)
REPORT = DropReport()
CLEAN = {"on": True, "min_chars": 80}


def strip_namespace(tag: str) -> str:
    if not tag:
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    if ":" in tag:
        return tag.rsplit(":", 1)[-1]
    return tag


def content_type_for_tag(tag: str) -> str:
    low = (tag or "").lower()
    if low in QUERY_TAG_NAMES or low.endswith("query") or "querytext" in low:
        return "Запрос 1С"
    return "Модуль 1С"


def build_context_header(
    *,
    content_type: str,
    metadata_class: str,
    object_name: str,
    location: str,
) -> str:
    return (
        "// --- НАЧАЛО КОНТЕКСТА ---\n"
        f"// Тип контента: {content_type}\n"
        f"// Класс метаданных: {metadata_class}\n"
        f"// Объект: {object_name or '—'}\n"
        f"// Расположение: {location}\n"
        "// --- КОНЕЦ КОНТЕКСТА ---\n"
    )


def make_id(source_path: str, tag: str, index: int) -> str:
    """Стабильный id по позиции (без тела): правка кода перезаписывает точку, а не плодит дубли."""
    h = hashlib.sha1(f"{source_path}|{tag}|{index}".encode("utf-8", errors="replace")).hexdigest()[:20]
    return f"1c_{h}"


def enrich(
    code: str,
    *,
    content_type: str,
    metadata_class: str,
    object_name: str,
    location: str,
) -> str:
    return (
        build_context_header(
            content_type=content_type,
            metadata_class=metadata_class,
            object_name=object_name,
            location=location,
        )
        + "\n"
        + code
    )


def _meta_for(path: Path, source_root: Path) -> tuple[str, str, str, str, str]:
    parts = relative_parts(path, source_root)
    class_en, class_ru, object_name, location = localize_parts(parts)
    source_path = "/".join(parts)
    return class_en, class_ru, object_name, location, source_path


def _chunks_from_body(
    cleaned: str,
    *,
    ctype: str,
    class_en: str,
    class_ru: str,
    object_name: str,
    location: str,
    source_path: str,
    tag: str,
    source_kind: str,
    max_chunk_chars: int,
    chunk_overlap: int = 200,
) -> list[CodeChunk]:
    out: list[CodeChunk] = []
    if CLEAN["on"]:
        before, cleaned = cleaned, clean_chunk_text(cleaned)
        REPORT.cleaned_chunk(before, cleaned)
    for i, piece in iter_pieces(cleaned, max_chunk_chars, chunk_overlap):
        if CLEAN["on"]:
            why = reject_reason(piece, min_chars=CLEAN["min_chars"])
            if why:
                REPORT.drop(why, source_path)
                continue
        loc = location if i == 0 else f"{location} [часть {i + 1}]"
        text = enrich(
            piece,
            content_type=ctype,
            metadata_class=class_ru,
            object_name=object_name,
            location=loc,
        )
        out.append(
            CodeChunk(
                id=make_id(source_path, tag, i),
                text=text,
                content_type=ctype,
                metadata_class=class_ru,
                metadata_class_en=class_en,
                object_name=object_name,
                location=loc,
                source_path=source_path,
                xml_tag=translate_segment(tag) if tag.endswith(".bsl") else tag,
                char_count=len(piece),
                extra={"source_kind": source_kind, "part": i},
            )
        )
    return out


def extract_from_xml(
    file_path: Path,
    source_root: Path,
    max_chunk_chars: int,
    *,
    chunk_overlap: int = 200,
) -> list[CodeChunk]:
    class_en, class_ru, object_name, location, source_path = _meta_for(
        file_path, source_root
    )
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
    except (ParseError, OSError, ET.ParseError):
        return _extract_xml_regex(
            file_path, source_root, max_chunk_chars, chunk_overlap=chunk_overlap
        )

    chunks: list[CodeChunk] = []
    index = 0
    for elem in root.iter():
        tag = strip_namespace(elem.tag)
        if not tag or tag.lower() in SKIP_XML_TAGS:
            continue
        if list(elem) and not (elem.text and elem.text.strip()):
            continue
        raw = (elem.text or "") if list(elem) else (elem.text or "")
        cleaned = clean_code_text(raw)
        if not is_bsl_or_query(cleaned):
            continue
        ctype = content_type_for_tag(tag)
        head = cleaned.lstrip().upper()[:40]
        if ctype != "Запрос 1С" and (
            head.startswith("ВЫБРАТЬ") or head.startswith("SELECT")
        ):
            ctype = "Запрос 1С"
        for ch in _chunks_from_body(
            cleaned,
            ctype=ctype,
            class_en=class_en,
            class_ru=class_ru,
            object_name=object_name,
            location=location,
            source_path=source_path,
            tag=tag,
            source_kind="xml",
            max_chunk_chars=max_chunk_chars,
            chunk_overlap=chunk_overlap,
        ):
            # unique ids when multiple tags
            ch.id = make_id(source_path, f"{tag}#{index}", ch.extra.get("part", 0))
            chunks.append(ch)
            index += 1
    return chunks


def _extract_xml_regex(
    file_path: Path,
    source_root: Path,
    max_chunk_chars: int,
    *,
    chunk_overlap: int = 200,
) -> list[CodeChunk]:
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    class_en, class_ru, object_name, location, source_path = _meta_for(
        file_path, source_root
    )
    pattern = re.compile(
        r"<(?:[\w.]+:)?(QueryText|Query|query|CustomQuery)\b[^>]*>(.*?)</(?:[\w.]+:)?\1\s*>",
        re.S | re.I,
    )
    chunks: list[CodeChunk] = []
    for i, m in enumerate(pattern.finditer(text)):
        tag = m.group(1)
        cleaned = clean_code_text(m.group(2), unescape_xml=True)
        if not is_bsl_or_query(cleaned):
            continue
        chunks.extend(
            _chunks_from_body(
                cleaned,
                ctype=content_type_for_tag(tag),
                class_en=class_en,
                class_ru=class_ru,
                object_name=object_name,
                location=location,
                source_path=source_path,
                tag=tag,
                source_kind="xml_regex",
                max_chunk_chars=max_chunk_chars,
                chunk_overlap=chunk_overlap,
            )
        )
    return chunks


def extract_from_bsl(
    file_path: Path,
    source_root: Path,
    max_chunk_chars: int,
    *,
    chunk_overlap: int = 200,
) -> list[CodeChunk]:
    try:
        raw = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    cleaned = clean_code_text(raw)
    if not is_bsl_or_query(cleaned):
        return []
    class_en, class_ru, object_name, location, source_path = _meta_for(
        file_path, source_root
    )
    head = cleaned.lstrip().upper()[:40]
    ctype = (
        "Запрос 1С"
        if head.startswith("ВЫБРАТЬ") or head.startswith("SELECT")
        else "Модуль 1С"
    )
    return _chunks_from_body(
        cleaned,
        ctype=ctype,
        class_en=class_en,
        class_ru=class_ru,
        object_name=object_name,
        location=location,
        source_path=source_path,
        tag=file_path.name,
        source_kind="bsl",
        max_chunk_chars=max_chunk_chars,
        chunk_overlap=chunk_overlap,
    )


def extract_cards(file_path: Path, source_root: Path, card_chars: int) -> list[CodeChunk]:
    """Карточки структуры объекта (реквизиты/ТЧ/типы/синонимы) из Class/Name.xml вместо сырого XML."""
    from backends.base import full_name_prefix_for_dump_folder
    from ingest.xml_cards import build_cards

    class_en, class_ru, object_name, location, source_path = _meta_for(file_path, source_root)
    parts = relative_parts(file_path, source_root)
    if len(parts) != 2:  # только корневой XML объекта: Documents/Имя.xml
        return []
    cards = build_cards(file_path, full_name_prefix_for_dump_folder(class_en) or class_ru, card_chars)
    return [
        CodeChunk(
            id=make_id(source_path, "card", i),
            text=enrich(card, content_type="Структура объекта", metadata_class=class_ru,
                        object_name=object_name, location=location if i == 0 else f"{location} [часть {i + 1}]"),
            content_type="Структура объекта",
            metadata_class=class_ru,
            metadata_class_en=class_en,
            object_name=object_name,
            location=location if i == 0 else f"{location} [часть {i + 1}]",
            source_path=source_path,
            xml_tag="Properties",
            char_count=len(card),
            extra={"source_kind": "xml_card", "part": i},
        )
        for i, card in enumerate(cards)
    ]


def _owner_label(parts: list[str], class_en: str, class_ru: str, kind: str, name: str, syn: str) -> str:
    from backends.base import full_name_prefix_for_dump_folder

    pre = full_name_prefix_for_dump_folder(class_en) or class_ru
    ttl = f" «{syn}»" if syn else ""
    if class_en in ("CommonForms", "CommonTemplates"):
        return f"{'Общая форма' if kind == 'form' else 'Общий макет'} {name}{ttl}"
    return f"{'Форма' if kind == 'form' else 'Макет'} {name}{ttl} объекта {pre}.{parts[1]}"


def extract_ui_cards(file_path: Path, source_root: Path, card_chars: int) -> list[CodeChunk]:
    """Карточки управляемых форм (Form.xml) и макетов (Template.xml: MXL, СКД)."""
    from ingest.ui_cards import form_cards, template_cards
    from ingest.xml_cards import meta_synonym

    parts = relative_parts(file_path, source_root)
    if len(parts) < 4 or parts[-2] != "Ext" or file_path.name not in ("Form.xml", "Template.xml"):
        return []
    is_form = file_path.name == "Form.xml"
    class_en, class_ru, object_name, location, source_path = _meta_for(file_path, source_root)
    name = parts[-3]
    syn = meta_synonym(file_path.parents[1].with_suffix(".xml"))  # .../Forms/Имя.xml с синонимом
    label = _owner_label(parts, class_en, class_ru, "form" if is_form else "template", name, syn)
    cards = (form_cards if is_form else template_cards)(file_path, label, card_chars)
    ctype = "Структура формы" if is_form else "Структура макета"
    out = []
    for i, card in enumerate(cards):
        loc = location if i == 0 else f"{location} [часть {i + 1}]"
        out.append(CodeChunk(
            id=make_id(source_path, "uicard", i),
            text=enrich(card, content_type=ctype, metadata_class=class_ru, object_name=object_name, location=loc),
            content_type=ctype, metadata_class=class_ru, metadata_class_en=class_en, object_name=object_name,
            location=loc, source_path=source_path, xml_tag="Form" if is_form else "Template", char_count=len(card),
            extra={"source_kind": "xml_card", "part": i, ("form_name" if is_form else "template_name"): name},
        ))
    return out


def extract_from_form_bin(
    file_path: Path,
    source_root: Path,
    max_chunk_chars: int,
    *,
    chunk_overlap: int = 200,
    ui_cards: bool = False,
    card_chars: int = 1200,
) -> list[CodeChunk]:
    """
    Обычная форма (Forms/<Ф>/Ext/Form.bin): модуль формы — под тем же путём, что у управляемой
    (…/Ext/Form/Module.bsl), чтобы search_code/proc_index/trace_calls видели код обычных форм;
    + карточка «Структура формы» (вкладки, элементы, кнопки → процедуры).
    """
    from tools.form_info import _unpack_bin, compact_form_layout, object_attributes
    from tools.ordinary_form import ordinary_form_layout

    try:
        form_text, module = _unpack_bin(file_path)
    except Exception as e:  # noqa: BLE001
        REPORT.drop(f"Form.bin не разобран: {e}", str(file_path))
        return []
    virt = file_path.parent / "Form" / "Module.bsl"
    out: list[CodeChunk] = []
    cleaned = clean_code_text(module) if module else ""
    if cleaned and is_bsl_or_query(cleaned):
        class_en, class_ru, object_name, location, source_path = _meta_for(virt, source_root)
        out += _chunks_from_body(
            cleaned, ctype="Модуль 1С", class_en=class_en, class_ru=class_ru, object_name=object_name,
            location=location, source_path=source_path, tag="Module.bsl", source_kind="bsl",
            max_chunk_chars=max_chunk_chars, chunk_overlap=chunk_overlap,
        )
    if ui_cards and form_text:
        from ingest.xml_cards import meta_synonym

        parts = relative_parts(file_path, source_root)
        if len(parts) >= 4:
            class_en, class_ru, object_name, location, source_path = _meta_for(file_path.parent / "Form.xml", source_root)
            name = parts[-3]
            obj_dir = file_path.parents[2].parent  # Catalogs/X
            attrs = {k: v.get("synonym", "") for k, v in object_attributes(obj_dir).items()}
            from ingest.ui_cards import code_ui_control
            from ingest.xml_cards import pack_lines

            lay = compact_form_layout(ordinary_form_layout(form_text, module, attrs), max_chars=10**7)
            label = _owner_label(parts, class_en, class_ru, "form", name, meta_synonym(file_path.parents[1].with_suffix(".xml")))
            lines = []
            # каждый элемент — своя строка: pack_lines режет длинные строки по запятой и рвал список полей посередине
            for tb in lay.get("tabs") or []:
                lines.append(f"Вкладка «{tb['tab']}»" + (" (скрыта)" if tb.get("hidden") else "") + ":")
                lines += ["  " + it for it in tb["items"]]
            if lay.get("outside_tabs"):  # у большинства обычных форм все элементы вне вкладок — раньше они не попадали в индекс
                lines.append("Элементы формы:")
                lines += ["  " + it for it in lay["outside_tabs"]]
            if lay.get("buttons"):
                lines.append("Кнопки: " + "; ".join(lay["buttons"]))
            if lay.get("form_events"):
                lines.append("События формы: " + ", ".join(map(str, lay["form_events"])))
            if lay.get("form_props"):
                lines.append("Свойства формы: " + ", ".join(f"{k}={v}" for k, v in lay["form_props"].items()))
            ctl = code_ui_control(module)
            if ctl:
                lines.append("Видимость/доступность элементов меняется в коде: " + "; ".join(ctl))
            # шапка в каждом куске, без обрезки: раньше резалось по символам и терялось всё после 6 кусков
            for i, piece in enumerate(pack_lines(f"{label} (обычная форма)", lines, card_chars) if lines else []):
                loc = location if i == 0 else f"{location} [часть {i + 1}]"
                out.append(CodeChunk(
                    id=make_id(source_path, "uicard", i),
                    text=enrich(piece, content_type="Структура формы", metadata_class=class_ru,
                                object_name=object_name, location=loc),
                    content_type="Структура формы", metadata_class=class_ru, metadata_class_en=class_en,
                    object_name=object_name, location=loc, source_path=source_path, xml_tag="Form",
                    char_count=len(piece), extra={"source_kind": "xml_card", "part": i, "form_name": name},
                ))
    return out


def iter_files(
    source_root: Path, *, include_bsl: bool, max_file_bytes: int
) -> Iterator[Path]:
    import os

    for dirpath, dirnames, filenames in os.walk(source_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith(".")]
        # помеченные к удалению (prepare.skip_name_prefixes, del_…): папка объекта/формы — целиком
        for d in [d for d in dirnames if is_skipped_name(d)]:
            dirnames.remove(d)
            REPORT.drop("помечен к удалению (префикс имени)", str(Path(dirpath, d).relative_to(source_root)))
        for name in filenames:
            low = name.lower()
            if low in SKIP_FILE_NAMES:
                continue
            if is_skipped_name(name.split(".", 1)[0]):
                REPORT.drop("помечен к удалению (префикс имени)", str(Path(dirpath, name).relative_to(source_root)))
                continue
            path = Path(dirpath) / name
            suf = path.suffix.lower()
            if suf in SKIP_SUFFIXES and low != "form.bin":
                continue
            if suf == ".xml":
                pass
            elif include_bsl and (suf == ".bsl" or low.endswith("module.bsl")):
                pass
            elif include_bsl and low == "form.bin":
                pass  # обычная форма: модуль и раскладка внутри контейнера
            else:
                if suf in SKIP_SUFFIXES:
                    continue
                continue
            try:
                if path.stat().st_size > max_file_bytes:
                    continue
            except OSError:
                continue
            yield path


def process_file(
    path: Path,
    source_root: Path,
    max_chunk_chars: int,
    *,
    chunk_overlap: int = 200,
    xml_cards: bool = False,
    ui_cards: bool = False,
    card_chars: int = 800,
) -> list[CodeChunk]:
    low = path.name.lower()
    if low == "form.bin":
        return extract_from_form_bin(path, source_root, max_chunk_chars, chunk_overlap=chunk_overlap,
                                     ui_cards=ui_cards, card_chars=max(card_chars, 1200))
    if path.suffix.lower() == ".bsl" or low.endswith("module.bsl"):
        return extract_from_bsl(
            path, source_root, max_chunk_chars, chunk_overlap=chunk_overlap
        )
    if path.suffix.lower() == ".xml":
        cards = extract_cards(path, source_root, card_chars) if xml_cards else []
        if ui_cards:
            cards += extract_ui_cards(path, source_root, max(card_chars, 1200))
        return cards + extract_from_xml(
            path, source_root, max_chunk_chars, chunk_overlap=chunk_overlap
        )
    return []


def main(argv: list[str] | None = None) -> int:
    from tools.name_match import _name_from_payload

    cfg = load_config()
    paths = cfg.get("paths") or {}
    prep = cfg.get("prepare") or {}
    qcfg = cfg.get("qdrant") or {}

    default_source = paths.get("code_dir") or str(ROOT / "knowledge" / "код_конфигурации")
    default_out = (
        qcfg.get("chunks_jsonl")
        or paths.get("chunks_jsonl")
        or str(ROOT / "knowledge" / "qdrant_ready" / "config_chunks.jsonl")
    )

    ap = argparse.ArgumentParser(description="Выгрузка 1С → JSONL для Qdrant")
    ap.add_argument("--source", type=Path, default=Path(default_source))
    ap.add_argument("--output", type=Path, default=Path(default_out))
    ap.add_argument("--no-bsl", action="store_true")
    ap.add_argument("--max-file-mb", type=float, default=float(prep.get("max_file_mb") or 8))
    ap.add_argument(
        "--max-chunk-chars",
        type=int,
        default=int(prep.get("max_chunk_chars") or 1500),
    )
    ap.add_argument(
        "--chunk-overlap",
        type=int,
        default=int(prep.get("chunk_overlap") or 200),
    )
    ap.add_argument("--no-cards", action="store_true", help="Не строить карточки структуры объектов из XML")
    ap.add_argument("--card-chars", type=int, default=int(prep.get("card_chars") or 800))
    ap.add_argument("--no-clean", action="store_true", help="Не чистить код (разделители, base64) и не выбрасывать пустые оболочки")
    ap.add_argument("--no-dedupe", action="store_true", help="Не убирать повторяющиеся чанки")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    source = args.source.expanduser()
    if not source.is_absolute():
        source = (ROOT / source).resolve()
    output = args.output.expanduser()
    if not output.is_absolute():
        output = (ROOT / output).resolve()

    if not source.is_dir():
        print(f"Ошибка: нет каталога выгрузки: {source}", file=sys.stderr)
        print("Укажите paths.code_dir в config.yaml", file=sys.stderr)
        return 1

    max_bytes = int(max(0.5, args.max_file_mb) * 1024 * 1024)
    include_bsl = not args.no_bsl
    max_chunk = int(args.max_chunk_chars)
    chunk_overlap = max(0, int(args.chunk_overlap))
    xml_cards = bool(prep.get("xml_cards", True)) and not args.no_cards
    ui_cards = bool(prep.get("ui_cards", True)) and not args.no_cards
    CLEAN["on"] = bool(prep.get("clean", True)) and not args.no_clean
    CLEAN["min_chars"] = int(prep.get("min_body_chars") or 80)
    dedupe = bool(prep.get("dedupe", True)) and not args.no_dedupe

    if not args.quiet:
        print(f"Источник: {source}", file=sys.stderr)
        print(f"Выход:    {output}", file=sys.stderr)
        print(
            f"BSL={include_bsl}, max_file={args.max_file_mb}MB, "
            f"chunk={max_chunk}, overlap={chunk_overlap}",
            file=sys.stderr,
        )

    stats = {
        "files_scanned": 0,
        "files_with_code": 0,
        "chunks": 0,
        "modules": 0,
        "queries": 0,
        "errors": 0,
        "cards": 0,
        "duplicates_skipped": 0,
    }
    first_id: dict[str, str] = {}  # хэш тела → id первого чанка
    dups: dict[str, dict] = {}  # id первого чанка → {count, objects}
    object_rows: list[tuple[str, str]] = []
    seen_objects: set[str] = set()
    meta = {
        "source_root": str(source),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "qdrant_rag",
        "script": "scripts/prepare_config_for_qdrant.py",
        "max_chunk_chars": max_chunk,
        "chunk_overlap": chunk_overlap,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        f.write(
            json.dumps({"id": "__meta__", "text": "", "payload": meta}, ensure_ascii=False)
            + "\n"
        )
        from ingest.progress import report as _progress

        all_files = list(iter_files(source, include_bsl=include_bsl, max_file_bytes=max_bytes))
        n_files = min(len(all_files), args.limit) if args.limit else len(all_files)
        _progress(0, n_files, "файлов", force=True)
        for path in all_files:
            stats["files_scanned"] += 1
            if args.limit and stats["files_scanned"] > args.limit:
                break
            _progress(stats["files_scanned"], n_files, "файлов")
            if not args.quiet and stats["files_scanned"] % 3000 == 0:
                print(
                    f"… files={stats['files_scanned']} chunks={stats['chunks']}",
                    file=sys.stderr,
                )
            try:
                chunks = process_file(
                    path, source, max_chunk, chunk_overlap=chunk_overlap,
                    xml_cards=xml_cards, ui_cards=ui_cards, card_chars=args.card_chars,
                )
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                print(f"ОШИБКА файл {path}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                continue
            if not chunks:
                continue
            stats["files_with_code"] += 1
            for ch in chunks:
                # дубли склеиваем только у XML-источников (RLS-шаблоны Rights.xml и т.п.); модули .bsl — всегда отдельно по объектам
                if dedupe and ch.extra.get("source_kind") in ("xml", "xml_regex"):
                    key = hashlib.md5(" ".join(ch.text.split("КОНЕЦ КОНТЕКСТА ---", 1)[-1].split()).encode("utf-8", "replace")).hexdigest()
                    fid = first_id.setdefault(key, ch.id)
                    if fid != ch.id:
                        d = dups.setdefault(fid, {"count": 0, "objects": []})
                        d["count"] += 1
                        if ch.object_name and ch.object_name not in d["objects"] and len(d["objects"]) < 20:
                            d["objects"].append(ch.object_name)
                        stats["duplicates_skipped"] += 1
                        continue
                stats["chunks"] += 1
                if ch.content_type.startswith("Структура"):
                    stats["cards"] += 1
                if ch.content_type == "Запрос 1С":
                    stats["queries"] += 1
                elif not ch.content_type.startswith("Структура"):
                    stats["modules"] += 1
                rec = ch.to_record()
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                on, folder = _name_from_payload(rec.get("payload") or {})
                if on and on not in seen_objects:
                    seen_objects.add(on)
                    object_rows.append((on, folder))

        meta["stats"] = stats
        f.write(
            json.dumps(
                {"id": "__meta_stats__", "text": "", "payload": meta},
                ensure_ascii=False,
            )
            + "\n"
        )

    if dups:
        tmp = output.with_suffix(".tmp")
        with output.open(encoding="utf-8") as src_f, tmp.open("w", encoding="utf-8") as dst_f:
            for ln in src_f:
                if '"id": "1c_' in ln[:20]:
                    rec = json.loads(ln)
                    d = dups.get(rec["id"])
                    if d:
                        rec["payload"]["dup_count"] = d["count"]
                        rec["payload"]["also_in"] = d["objects"]
                        ln = json.dumps(rec, ensure_ascii=False) + "\n"
                dst_f.write(ln)
        tmp.replace(output)

    # отчёт о чистке: что выброшено/очищено/склеено и почему (для контроля при смене выгрузки)
    top_dups = sorted(dups.items(), key=lambda kv: -kv[1]["count"])[:5]
    report = {**REPORT.as_dict(), "stats": stats, "top_duplicate_groups": [
        {"first_chunk": fid, "copies": d["count"], "objects": d["objects"][:5]} for fid, d in top_dups]}
    (ROOT / "exports").mkdir(exist_ok=True)
    (ROOT / "exports" / "prepare_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.quiet:
        print("Чистка: " + (", ".join(f"{k}: {v}" for k, v in {**REPORT.dropped, **REPORT.cleaned}.items()) or "нечего чистить")
              + f"; дублей склеено: {stats['duplicates_skipped']} (подробно: exports/prepare_report.json)", file=sys.stderr)

    names_file = ""
    if object_rows and not args.limit:  # пробный --limit не должен затирать полный каталог
        try:
            from tools.name_match import write_object_name_catalog

            custom = output.resolve() != Path(default_out).expanduser().resolve() if Path(default_out).is_absolute()                 else output.resolve() != (ROOT / default_out).resolve()
            names_file = str(write_object_name_catalog(
                object_rows, path=output.with_name(output.stem + ".object_names.jsonl") if custom else None))
        except Exception as exc:
            if not args.quiet:
                print(f"object_names skip: {exc}", file=sys.stderr)
        # каталог объектов (имя + класс + синоним + RegisterRecords) для резолвера имён и «регистр → документы»
        if not locals().get("custom", False):
            try:
                from tools.object_catalog import build_catalog_from_dump

                cat_file = build_catalog_from_dump(Path(args.source))
                if not args.quiet:
                    print(f"object_catalog → {cat_file}", file=sys.stderr)
            except Exception as exc:
                if not args.quiet:
                    print(f"object_catalog skip: {exc}", file=sys.stderr)

    if not args.quiet:
        extra = f"\n  object_names: {len(object_rows)} → {names_file}" if names_file else ""
        print(
            f"Готово: {stats['chunks']} чанков → {output}\n"
            f"  файлов: {stats['files_scanned']} (с кодом {stats['files_with_code']})\n"
            f"  модули: {stats['modules']}, запросы: {stats['queries']}, карточки: {stats['cards']}, "
            f"дублей пропущено: {stats['duplicates_skipped']}, err={stats['errors']}"
            f"{extra}\n"
            f"Дальше: python scripts/index_qdrant.py",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
