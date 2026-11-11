# -*- coding: utf-8 -*-
"""Объекты/реквизиты/формы, помеченные к удалению префиксом имени (по умолчанию del_), в индекс не идут.

Префиксы — config.yaml → prepare.skip_name_prefixes (регистр не важен; [] — выключить).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_DEFAULT = ("del_",)


@lru_cache(maxsize=1)
def prefixes() -> tuple[str, ...]:
    try:
        from config import load_config

        raw = (load_config().get("prepare") or {}).get("skip_name_prefixes", _DEFAULT)
    except Exception:  # noqa: BLE001
        raw = _DEFAULT
    if isinstance(raw, str):
        raw = [raw]
    return tuple(str(p).strip().lower() for p in (raw or ()) if str(p).strip())


def is_skipped_name(name: str) -> bool:
    """Имя метаданного (без расширения) начинается с префикса удаления."""
    pre = prefixes()
    return bool(pre) and (name or "").strip().lower().startswith(pre)


def is_skipped_path(path: Path | str) -> bool:
    """Любой сегмент пути (объект, форма, макет, команда) помечен к удалению: Catalogs/del_X.xml, …/Forms/del_Ф/…"""
    return any(is_skipped_name(part.split(".", 1)[0]) for part in Path(path).parts)
