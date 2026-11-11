"""Безопасная сборка текстов запросов 1С (только ВЫБРАТЬ)."""

from __future__ import annotations

import re
from datetime import datetime


# Код элемента: 00-0000001, 000000001, UUID-подобные хвосты
_ITEM_CODE_RE = re.compile(
    r"^("
    r"\d{2}-\d+"  # 00-0000001
    r"|\d{6,}"  # длинный числовой код
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"  # uuid
    r")$",
    re.IGNORECASE,
)


def parse_date(value: str) -> datetime:
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y.%m.%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(
        f"Неверная дата «{value}». Используйте YYYY-MM-DD или ДД.ММ.ГГГГ"
    )


def to_datavremya(value: str, *, end_of_day: bool = False) -> str:
    dt = parse_date(value)
    if end_of_day:
        return f"ДАТАВРЕМЯ({dt.year},{dt.month},{dt.day},23,59,59)"
    return f"ДАТАВРЕМЯ({dt.year},{dt.month},{dt.day},0,0,0)"


def normalize_table_name(full_name: str) -> str:
    """
    Приводит имя к виду для языка запросов / метаданных:
      Справочник.<ИмяИзFindMetadata>
      Документ.<ИмяИзFindMetadata>
      Документ.X.ТЧ  (ТЧ — если не код элемента)

    Срезает коды элементов, которые модели часто дописывают:
      Справочник.X.00-0000001 → Справочник.X
    """
    name = (full_name or "").strip()
    if not name:
        raise ValueError("Пустое имя объекта")

    # убрать пробелы вокруг точек
    name = re.sub(r"\s*\.\s*", ".", name)

    replacements = {
        "Справочники.": "Справочник.",
        "Документы.": "Документ.",
        "РегистрыСведений.": "РегистрСведений.",
        "РегистрыНакопления.": "РегистрНакопления.",
        "Перечисления.": "Перечисление.",
        "Catalog.": "Справочник.",
        "Document.": "Документ.",
        "InformationRegister.": "РегистрСведений.",
        "AccumulationRegister.": "РегистрНакопления.",
    }
    for src, dst in replacements.items():
        if name.startswith(src):
            name = dst + name[len(src) :]
            break

    if "." not in name:
        raise ValueError(
            f"Укажите полное имя с типом, например Справочник.{name} или Документ.{name}"
        )

    parts = name.split(".")
    # Срезать хвостовые коды элементов: Тип.Имя.00-0000001
    while len(parts) > 2 and _ITEM_CODE_RE.match(parts[-1] or ""):
        parts.pop()
    # Справочник.* — для метаданных/запросов к шапке достаточно Тип.Имя
    # (третий сегмент-код уже срезан; «Товары» у справочника редко, оставляем если буквы)
    if len(parts) > 2 and parts[0] in (
        "Справочник",
        "Перечисление",
        "ПланВидовХарактеристик",
        "ПланСчетов",
    ):
        # если третий кусок похож на код/мусор с цифрами — срезать
        if re.search(r"\d", parts[2]) or _ITEM_CODE_RE.match(parts[2]):
            parts = parts[:2]

    name = ".".join(parts)
    return name


def normalize_metadata_name(full_name: str) -> str:
    """
    Имя объекта метаданных для /object и FindByFullName: строго Тип.Имя
    (без кода элемента и без ТЧ).
    """
    name = normalize_table_name(full_name)
    parts = name.split(".")
    if len(parts) < 2:
        raise ValueError(f"Нужно Тип.Имя, получено: {full_name}")
    return f"{parts[0]}.{parts[1]}"


# Короткие имена без «Документ.» — типичная ошибка модели
_BARE_NAME_PREFIXES = (
    "Документ",
    "Справочник",
    "РегистрСведений",
    "РегистрНакопления",
    "Перечисление",
    "ПланСчетов",
    "ПланВидовХарактеристик",
)


def try_prefix_bare_name(name: str) -> list[str]:
    """
    «произвольноеИмя» → кандидаты Документ.… / Справочник.… / Регистр…
    «документ.Счет…» уже нормализуется в normalize_table_name.
    """
    n = (name or "").strip()
    if not n or "." in n:
        return [n] if n else []
    # PascalCase / кириллица без префикса
    return [f"{p}.{n}" for p in _BARE_NAME_PREFIXES[:4]]


def table_kind(full_name: str) -> str:
    return normalize_table_name(full_name).split(".", 1)[0]


def is_document(full_name: str) -> bool:
    return table_kind(full_name) == "Документ"


def is_catalog(full_name: str) -> bool:
    return table_kind(full_name) == "Справочник"

