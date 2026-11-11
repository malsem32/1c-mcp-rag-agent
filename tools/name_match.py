# -*- coding: utf-8 -*-
"""
Универсальное сопоставление фразы пользователя с именами объектов CF.

БЕЗ доменных whitelist («реализац», «корректир», «ндс», …) и БЕЗ full_name из другой базы.
Истина — object_name / source_path из Qdrant (индекс текущей выгрузки).

Использовать: resolve, trace_calls, inspect_code, movements.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

# служебные слова фразы (не стемы бизнеса!)
_STOP = frozenset(
    {
        "документ",
        "документа",
        "документе",
        "документы",
        "справочник",
        "справочника",
        "модуль",
        "модуля",
        "модуле",
        "объект",
        "объекта",
        "процедура",
        "процедуры",
        "функция",
        "функции",
        "форма",
        "формы",
        "код",
        "как",
        "работает",
        "отследи",
        "отследить",
        "проследи",
        "проследить",
        "покажи",
        "найди",
        "разбери",
        "проанализируй",
        "этот",
        "эта",
        "этой",
        "это",
        "есть",
        "какие",
        "какой",
        "где",
        "что",
        "для",
        "при",
        "без",
        "или",
        "и",
        "на",
        "по",
        "из",
        "в",
        "с",
        "за",
        "от",
        "до",
        "все",
        "весь",
        "нужно",
        "можно",
        "хочу",
        "пожалуйста",
    }
)

# платформенные/технические PascalCase — не имена метаданных
_PASCAL_STOP = frozenset(
    {
        "Документ",
        "Справочник",
        "Процедура",
        "Функция",
        "Обработка",
        "Модуль",
        "Document",
        "Catalog",
        "ObjectModule",
        "ManagerModule",
        "CommonModule",
        "ОбработкаПроведения",
        "ОбработкаЗаполнения",
        "ПередЗаписью",
        "ПриЗаписи",
    }
)


def norm(s: str) -> str:
    return (s or "").lower().replace("ё", "е")


def phrase_tokens(phrase: str, *, min_len: int = 3) -> list[str]:
    """Значимые токены фразы (без стоп-слов маршрутизации)."""
    pl = norm(phrase)
    toks = re.findall(r"[а-яa-z0-9]{%d,}" % max(2, min_len), pl)
    out: list[str] = []
    seen: set[str] = set()
    for t in toks:
        if t in _STOP or len(t) < min_len:
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def camel_parts(name: str) -> list[str]:
    """РасчетСебестоимостиТоваров → ['Расчет', 'Себестоимости', 'Товаров']."""
    s = (name or "").strip()
    if not s:
        return []
    return [
        p
        for p in re.findall(
            r"[A-ZА-ЯЁ][a-zа-яё0-9_]*|[A-ZА-ЯЁ]+(?![a-zа-яё])|[a-zа-яё0-9]+",
            s,
        )
        if p
    ]


def is_pascal_identifier(s: str) -> bool:
    """Структурно похож на имя объекта 1С (не доменный whitelist)."""
    s = (s or "").strip()
    if len(s) < 5 or s in _PASCAL_STOP:
        return False
    if not re.match(r"^[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_]*$", s):
        return False
    # CamelCase / длинный идентификатор
    if re.search(r"[a-zа-яё][A-ZА-ЯЁ]", s) or len(s) >= 10:
        return True
    # одно слово Title case ≥ 6
    return len(s) >= 6 and s[0].isupper()


def pascal_candidates_in_text(text: str, *, exclude: set[str] | None = None) -> list[str]:
    """Все PascalCase-токены из текста (процедуры, имена объектов)."""
    ex = exclude or set()
    found: list[str] = []
    seen: set[str] = set()
    for m in re.findall(r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_]{4,}", text or ""):
        if m in ex or m in _PASCAL_STOP or m in seen:
            continue
        if not is_pascal_identifier(m):
            continue
        seen.add(m)
        found.append(m)
    # длинные первыми (имя процедуры vs короткое)
    found.sort(key=len, reverse=True)
    return found


def _tok_hits_name(tok: str, ol: str, parts_l: list[str]) -> float:
    """Очки за один токен фразы против имени (0 если мимо)."""
    if len(tok) < 3:
        return 0.0
    if tok in ol:
        return 12.0 + min(len(tok), 12) * 0.5
    pref = tok[: max(4, min(6, len(tok)))]
    if len(pref) >= 4 and pref in ol:
        return 8.0
    for p in parts_l:
        if not p or len(p) < 3:
            continue
        # общий префикс ≥4
        n = 0
        for a, b in zip(tok, p):
            if a == b:
                n += 1
            else:
                break
        if n >= 4:
            return 6.0 + n * 0.3
        if tok in p or p in tok:
            return 5.0
    return 0.0


def token_affinity(tokens: list[str], object_name: str) -> float:
    """
    Насколько object_name покрывает токены фразы (0..∞).
    Только морфология строк — без «реализац/корректир» списков.
    """
    if not tokens or not object_name:
        return 0.0
    ol = norm(object_name)
    parts_l = [norm(p) for p in camel_parts(object_name)]
    score = 0.0
    hits = 0
    for tok in tokens:
        h = _tok_hits_name(tok, ol, parts_l)
        if h > 0:
            score += h
            hits += 1
    if hits == 0:
        return 0.0
    # главный токен (первый значимый) ОБЯЗАН попасть — иначе чужой объект
    stem0 = tokens[0]
    if _tok_hits_name(stem0, ol, parts_l) <= 0:
        return 0.0
    # фраза из 2+ слов, а совпал только 1 токен — слабый матч (не угадывать)
    if len(tokens) >= 2 and hits < 2:
        score *= 0.25
    # бонус за покрытие нескольких токенов
    score += hits * hits * 4.0
    # покрытие доли токенов фразы
    score += (hits / max(len(tokens), 1)) * 10.0
    # штраф: в имени много «лишних» camel-частей
    if parts_l and hits < len(parts_l):
        orphan = 0
        for p in parts_l:
            if len(p) < 4:
                continue
            if not any(_tok_hits_name(t, p, [p]) > 0 for t in tokens):
                orphan += 1
        score -= orphan * 3.0
    # при равных hits — более короткое имя (Документ, не ДобавлениеИзменение…)
    score += max(0, 40 - len(object_name)) * 0.08
    return score


def camel_from_tokens(tokens: list[str], *, max_parts: int = 6) -> str:
    """корректировка + вида + деятельности → КорректировкаВидаДеятельности."""
    if not tokens:
        return ""
    parts = []
    for t in tokens[:max_parts]:
        if not t:
            continue
        parts.append(t[:1].upper() + t[1:])
    return "".join(parts)


# Классы метаданных выгрузки (EN folders) — не Roles/CommandGroups
_META_FOLDERS = (
    "Documents",
    "Catalogs",
    "DataProcessors",
    "Reports",
    "InformationRegisters",
    "AccumulationRegisters",
    "CommonModules",
    "Enums",
    "ChartsOfCharacteristicTypes",
    "BusinessProcesses",
    "Tasks",
    "ExchangePlans",
    "DocumentJournals",
    "Constants",
)


_SKIP_OBJECT_SEGMENTS = frozenset(
    {"Ext", "Forms", "Templates", "Commands", "Help", "Flowcharts"}
)


def object_names_jsonl_path(*, cfg: dict[str, Any] | None = None) -> Path:
    """Sidecar рядом с config_chunks.jsonl — каталог имён текущей CF."""
    try:
        from config import load_config

        c = cfg if cfg is not None else load_config()
        raw = (c.get("paths") or {}).get("object_names_jsonl") or ""
        if raw:
            return Path(raw)
        chunks = (c.get("paths") or {}).get("chunks_jsonl") or ""
        if chunks:
            return Path(chunks).with_name("object_names.jsonl")
    except Exception:
        pass
    return Path("knowledge/qdrant_ready/object_names.jsonl")


def _name_from_payload(pl: dict[str, Any]) -> tuple[str, str]:
    """(object_name, dump_folder) из payload чанка. Пусто — пропуск."""
    sp = str(pl.get("source_path") or "").replace("\\", "/")
    parts = [p for p in sp.split("/") if p]
    folder = parts[0] if parts else ""
    on = str(pl.get("object_name") or "").strip()
    if not on and len(parts) >= 2:
        on = parts[1].strip()
    if on in _SKIP_OBJECT_SEGMENTS or len(on) < 3:
        return "", ""
    if folder and folder not in _META_FOLDERS:
        return "", ""
    return on, folder


def write_object_name_catalog(
    rows: Iterable[dict[str, str] | str | tuple[str, str]],
    *,
    path: Path | None = None,
) -> Path:
    """Записать уникальные object_name текущей выгрузки (не имена другой CF)."""
    by: dict[str, str] = {}
    for row in rows:
        name, folder = "", ""
        if isinstance(row, str):
            name = row.strip()
        elif isinstance(row, tuple) and row:
            name = str(row[0] or "").strip()
            folder = str(row[1] or "").strip() if len(row) > 1 else ""
        elif isinstance(row, dict):
            name = str(row.get("object_name") or row.get("n") or "").strip()
            folder = str(row.get("dump_folder") or row.get("f") or "").strip()
        if len(name) < 3 or name in _SKIP_OBJECT_SEGMENTS:
            continue
        if name not in by or (folder and not by[name]):
            by[name] = folder
    out = path or object_names_jsonl_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for name in sorted(by):
            rec: dict[str, str] = {"object_name": name}
            if by[name]:
                rec["dump_folder"] = by[name]
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    invalidate_object_name_catalog()
    return out


def invalidate_object_name_catalog() -> None:
    object_name_catalog.cache_clear()


def _load_sidecar(path: Path, *, max_names: int) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            name = ""
            if line.startswith("{"):
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    name = str(rec.get("object_name") or rec.get("n") or "").strip()
            else:
                name = line
            if len(name) < 3 or name in seen or name in _SKIP_OBJECT_SEGMENTS:
                continue
            seen.add(name)
            names.append(name)
            if len(names) >= max_names:
                break
    return tuple(names)


def _catalog_from_chunks_jsonl(*, max_names: int) -> tuple[str, ...]:
    try:
        from config import load_config

        chunks = Path((load_config().get("paths") or {}).get("chunks_jsonl") or "")
    except Exception:
        return tuple()
    if not chunks.is_file():
        return tuple()
    names: list[str] = []
    seen: set[str] = set()
    folders: dict[str, str] = {}
    try:
        with chunks.open(encoding="utf-8") as f:
            for line in f:
                if '"object_name"' not in line and '"source_path"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if str(obj.get("id") or "").startswith("__meta"):
                    continue
                on, folder = _name_from_payload(obj.get("payload") or {})
                if not on or on in seen:
                    continue
                seen.add(on)
                names.append(on)
                if folder:
                    folders[on] = folder
                if len(names) >= max_names:
                    break
    except OSError:
        return tuple()
    if names:
        try:
            write_object_name_catalog(
                [(n, folders.get(n, "")) for n in names]
            )
        except Exception:
            pass
    return tuple(sorted(names))


def name_in_object_catalog(name: str) -> bool:
    """PascalCase ∈ каталоге object_name текущей CF (Qdrant). Без доменных стемов."""
    n = (name or "").strip()
    if len(n) < 3:
        return False
    names = object_name_catalog()
    if not names:
        return False
    if n in names:
        return True
    nl = n.lower().replace("ё", "е")
    return any((x or "").lower().replace("ё", "е") == nl for x in names)


@lru_cache(maxsize=2)
def object_name_catalog(*, max_names: int = 12000) -> tuple[str, ...]:
    """
    Уникальные object_name текущей CF.
    1) sidecar object_names.jsonl (пишется на prepare/index)
    2) один проход config_chunks.jsonl → sidecar
    3) fallback: scroll Qdrant (медленно, только если нет файлов)
    """
    cap = max(100, int(max_names or 12000))
    side = object_names_jsonl_path()
    if side.is_file():
        try:
            loaded = _load_sidecar(side, max_names=cap)
            if loaded:
                return loaded
        except OSError:
            pass

    from_chunks = _catalog_from_chunks_jsonl(max_names=cap)
    if from_chunks:
        return from_chunks

    return _catalog_from_qdrant_scroll(max_names=cap)


def _catalog_from_qdrant_scroll(*, max_names: int) -> tuple[str, ...]:
    try:
        from vector.catalog import (
            _scroll_payload,
            collection_name,
            get_qdrant,
        )
        import qdrant_client.http.models as qm
    except Exception:
        return tuple()

    names: set[str] = set()
    folders: dict[str, str] = {}
    try:
        client = get_qdrant()
        col = collection_name("code")
        deep = {
            "Documents",
            "Catalogs",
            "CommonModules",
            "AccumulationRegisters",
            "InformationRegisters",
        }
        for folder in _META_FOLDERS:
            if len(names) >= max_names:
                break
            pages = 100 if folder in deep else 20
            max_rec = 12000 if folder in deep else 2000
            try:
                rows = _scroll_payload(
                    client,
                    col,
                    scroll_filter=qm.Filter(
                        must=[
                            qm.FieldCondition(
                                key="source_path",
                                match=qm.MatchText(text=folder),
                            )
                        ]
                    ),
                    limit=max_rec,
                    max_pages=pages,
                )
            except Exception:
                continue
            for pl in rows:
                on, fld = _name_from_payload(pl)
                if not on:
                    continue
                names.add(on)
                if fld:
                    folders[on] = fld
                if len(names) >= max_names:
                    break
    except Exception:
        return tuple()
    ordered = tuple(sorted(names))
    if ordered:
        try:
            write_object_name_catalog([(n, folders.get(n, "")) for n in ordered])
        except Exception:
            pass
    return ordered


def _resolve_score(tokens: list[str], object_name: str, phrase: str) -> float:
    """Affinity + универсальные штрафы (не доменные списки)."""
    sc = token_affinity(tokens, object_name)
    if sc <= 0:
        return 0.0
    ol = norm(object_name)
    pl = norm(phrase)
    # роли/права «ДобавлениеИзменениеX» — длинные, часто ловят чужие стемы
    if ol.startswith("добавлениеизменение") or ol.startswith("чтение"):
        if not any(t in pl for t in ("добавлен", "изменен", "права", "рол")):
            sc *= 0.15
    # имя сильно длиннее покрытия токенов — подозрительно
    parts = camel_parts(object_name)
    if parts and len(parts) > len(tokens) + 2:
        sc *= 0.75
    return sc


def resolve_object_full_name(phrase: str, *, strict: bool = False) -> str:
    """
    Фраза → full_name (Класс.Имя) из каталога выгрузки; пусто если неуверенно.
    strict=True — только точное имя из фразы или «слово класса + уверенный матч»
    (для мест, где ошибка объекта дороже уточнения).
    """
    try:
        from tools.meta_class import detect_meta_class
        from tools.object_catalog import resolve_full_name

        rc = resolve_full_name(phrase)
        cands = rc.get("candidates") or []
        if not cands:
            return ""
        if strict:
            if rc.get("by") in ("exact_name", "glued_words"):
                return str(rc["full_name"])
            if rc.get("confident") and detect_meta_class(phrase)[1]:
                return str(rc["full_name"])
            return ""
        if rc.get("confident") or float(cands[0].get("score") or 0) >= 60:
            return str(rc["full_name"])
    except Exception:
        pass
    return ""


def resolve_object_name(
    phrase: str,
    *,
    prefer_classes: tuple[str, ...] = ("Documents", "Catalogs", "DataProcessors"),
    min_score: float = 10.0,
) -> str:
    """
    Фраза → object_name из каталога Qdrant (лучшее token_affinity).
    Пусто, если ничего убедительного.
    """
    # каталог выгрузки (имя + класс + синоним): класс речи — фильтр, точное имя — первым
    try:
        from tools.object_catalog import resolve_full_name

        rc = resolve_full_name(phrase)
        cands = rc.get("candidates") or []
        if cands and (rc.get("confident") or float(cands[0].get("score") or 0) >= 60):
            return str(rc["full_name"]).split(".", 1)[-1]
    except Exception:
        pass

    tokens = phrase_tokens(phrase)
    if not tokens:
        for p in pascal_candidates_in_text(phrase):
            return p
        return ""

    camel = camel_from_tokens(tokens)
    catalog = object_name_catalog()
    cat_set = set(catalog)

    if camel and camel in cat_set:
        return camel

    # prefix: КорректировкаВидаДеятельности ∈ КорректировкаВидаДеятельностиНДС
    if camel:
        prefixed = [
            on
            for on in catalog
            if (on.startswith(camel) or camel.startswith(on))
            and len(on) >= 8
            and len(camel) >= 6
        ]
        if len(prefixed) == 1:
            return prefixed[0]
        if prefixed:
            # самый короткий точный (специфичный, но не раздутый ролью)
            prefixed.sort(key=lambda n: (len(n), n))
            # предпочесть Documents-like: без ДобавлениеИзменение
            non_role = [
                n
                for n in prefixed
                if not n.startswith("ДобавлениеИзменение") and not n.startswith("Чтение")
            ]
            return (non_role or prefixed)[0]

    best = ""
    best_sc = -1.0
    for on in catalog:
        sc = _resolve_score(tokens, on, phrase)
        if sc > best_sc:
            best_sc = sc
            best = on

    if best and best_sc >= min_score:
        return best

    if camel:
        try:
            from vector.catalog import get_chunks_by_source_path

            for folder in prefer_classes:
                for role in ("Ext/ObjectModule.bsl", "Ext/ManagerModule.bsl"):
                    if get_chunks_by_source_path(f"{folder}/{camel}/{role}"):
                        return camel
            # prefix path probe: Documents/{camel}*/ — через каталог уже
        except Exception:
            pass

    for p in pascal_candidates_in_text(phrase):
        if p in cat_set:
            return p
        try:
            from vector.catalog import get_chunks_by_source_path

            for folder in prefer_classes:
                if get_chunks_by_source_path(f"{folder}/{p}/Ext/ObjectModule.bsl"):
                    return p
        except Exception:
            pass

    return best if best_sc >= min_score * 0.7 else ""

