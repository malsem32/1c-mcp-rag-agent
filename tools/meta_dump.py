"""Список объектов метаданных из выгрузки конфигурации (fallback к HTTP)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from backends.base import METADATA_TYPES, normalize_metadata_type
from tools.code_search import PROJECT_ROOT, resolve_code_roots


def dump_folder_path(
    folder: str,
    code_dir: str | Path | None = None,
) -> Path | None:
    """
    Папка выгрузки (EventSubscriptions/, ScheduledJobs/, Roles/).
    Как Roles: прямой путь, не Qdrant. Пусто, если выгрузки нет.
    """
    folder = (folder or "").strip().strip("/\\")
    if not folder:
        return None
    try:
        from vector.catalog import runtime_fs_allowed

        if not runtime_fs_allowed():  # runtime: только зеркало коллекции файлов Qdrant, не диск
            p = next((r / folder for r in resolve_code_roots().roots), None)
            return p if p is not None and p.is_dir() else None
    except Exception:
        pass
    candidates: list[Path] = []
    if code_dir:
        candidates.append(Path(code_dir) / folder)
    try:
        from config import load_config

        cfg = load_config()
        cd = str((cfg.get("paths") or {}).get("code_dir") or cfg.get("code_dir") or "")
        if cd:
            candidates.append(Path(cd) / folder)
    except Exception:
        pass
    candidates.append(PROJECT_ROOT / "knowledge" / "код_конфигурации" / folder)
    seen: set[str] = set()
    for p in candidates:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        try:
            if p.is_dir():
                return p
        except OSError:
            continue
    return None


def dump_object_path(
    full_name: str,
    code_dir: str | Path | None = None,
) -> Path | None:
    """Documents/Имя из full_name Документ.Имя. Прямой dump, не Qdrant-only."""
    raw = (full_name or "").strip()
    if "." not in raw:
        return None
    pref, rest = raw.split(".", 1)
    obj = rest.split(".")[0].strip()
    if not obj:
        return None
    from backends.base import dump_folder_for_full_name_prefix

    folder = dump_folder_for_full_name_prefix(pref)
    if not folder:
        return None
    root = dump_folder_path(folder, code_dir)
    if not root:
        return None
    p = root / obj
    try:
        return p if p.is_dir() else None
    except OSError:
        return None


def list_from_dump(
    metadata_type: str,
    *,
    search: str = "",
    limit: int = 200,
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    Перечислить объекты класса из dump (Catalogs/, Subsystems/, …).
    Подсистемы: вложенные Subsystems/ → full_name Подсистема.A.B
    """
    mtype = normalize_metadata_type(metadata_type) or metadata_type
    meta = METADATA_TYPES.get(mtype)
    if not meta:
        return {
            "error": f"Неизвестный metadata_type: {metadata_type}",
            "hint": ", ".join(METADATA_TYPES.keys()),
        }

    folder = meta.get("dump_folder") or ""
    prefix = meta.get("full_name_prefix") or mtype.rstrip("и")
    cr = resolve_code_roots(code_dir)
    if not cr.roots or not folder:
        return {
            "ok": False,
            "type": mtype,
            "objects": [],
            "count": 0,
            "source": "dump",
            "hint": "Нет выгрузки или dump_folder. Укажите RAG_CODE_DIR / knowledge/код_конфигурации.",
        }

    names: list[dict[str, str]] = []
    q = (search or "").strip().lower().replace("ё", "е")

    for root in cr.roots:
        base = root / folder
        if not base.is_dir():
            continue
        if mtype == "Подсистемы":
            names.extend(_walk_subsystems(base, prefix=""))
        else:
            for child in sorted(base.iterdir(), key=lambda p: p.name.lower()):
                if child.is_dir() and not child.name.startswith("."):
                    nm = child.name
                    full = f"{prefix}.{nm}"
                    names.append({"name": nm, "full_name": full, "synonym": ""})
                elif child.is_file() and child.suffix.lower() == ".xml":
                    # Roles/Name.xml рядом с Roles/Name/
                    nm = child.stem
                    if any(x["name"] == nm for x in names):
                        continue
                    names.append(
                        {"name": nm, "full_name": f"{prefix}.{nm}", "synonym": ""}
                    )

    # unique by full_name
    seen: set[str] = set()
    uniq: list[dict[str, str]] = []
    for it in names:
        fn = it["full_name"]
        if fn in seen:
            continue
        seen.add(fn)
        if q:
            blob = f"{it['name']} {fn} {it.get('synonym','')}".lower().replace("ё", "е")
            if q not in blob and not all(
                t in blob for t in re.findall(r"[a-zа-яё0-9]{3,}", q)
            ):
                toks = re.findall(r"[a-zа-яё0-9]{3,}", q)
                if toks and not any(t in blob for t in toks):
                    continue
        uniq.append(it)

    lim = max(1, min(int(limit or 200), 2000))
    return {
        "ok": True,
        "type": mtype,
        "count": len(uniq),
        "objects": uniq[:lim],
        "source": "config_dump",
        "dump_folder": folder,
        "truncated": len(uniq) > lim,
        "prefer": (
            f"full_name вида {prefix}.Имя. "
            "Для ролей с правами — list_roles / find_roles_for_object; "
            "для кода — find_modules / inspect_code."
        ),
    }


def _walk_subsystems(base: Path, prefix: str = "") -> list[dict[str, str]]:
    """Subsystems/A + Subsystems/A/Subsystems/B → Подсистема.A, Подсистема.A.B"""
    out: list[dict[str, str]] = []
    for child in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        name = child.name
        if name in ("Ext",):
            continue
        path_name = f"{prefix}.{name}" if prefix else name
        out.append(
            {
                "name": path_name,
                "full_name": f"Подсистема.{path_name}",
                "synonym": "",
            }
        )
        nested = child / "Subsystems"
        if nested.is_dir():
            out.extend(_walk_subsystems(nested, prefix=path_name))
    return out
