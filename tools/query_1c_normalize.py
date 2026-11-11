"""
Приведение «почти SQL» / смешанного синтаксиса к языку запросов 1С.
"""

from __future__ import annotations

import calendar
import re
from typing import Any


def _iso_to_datavremya(iso: str, *, end_of_day: bool = False) -> str:
    """
    '2015-02-01' | '2015-02' | '2015' → ДАТАВРЕМЯ(...)
    """
    s = (iso or "").strip().strip("'\"")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        y, m, d = map(int, s.split("-"))
        if end_of_day:
            return f"ДАТАВРЕМЯ({y},{m},{d},23,59,59)"
        return f"ДАТАВРЕМЯ({y},{m},{d},0,0,0)"
    if re.fullmatch(r"\d{4}-\d{2}", s):
        y, m = map(int, s.split("-"))
        if end_of_day:
            last = calendar.monthrange(y, m)[1]
            return f"ДАТАВРЕМЯ({y},{m},{last},23,59,59)"
        return f"ДАТАВРЕМЯ({y},{m},1,0,0,0)"
    if re.fullmatch(r"\d{4}", s):
        y = int(s)
        if end_of_day:
            return f"ДАТАВРЕМЯ({y},12,31,23,59,59)"
        return f"ДАТАВРЕМЯ({y},1,1,0,0,0)"
    return iso


def looks_like_ansi_sql(query: str) -> bool:
    q = query or ""
    return bool(
        re.search(
            r"\b(SELECT|FROM|WHERE|BETWEEN|JOIN|GROUP\s+BY|ORDER\s+BY)\b",
            q,
            re.I,
        )
    )


def has_select_star(query: str) -> bool:
    q = query or ""
    return bool(
        re.search(r"\bSELECT\s+(\*|TOP\s+\d+\s+\*)", q, re.I)
        or re.search(r"\bВЫБРАТЬ\s+(ПЕРВЫЕ\s+\d+\s+)?\*", q, re.I)
    )


def extract_from_table(query: str) -> str | None:
    m = re.search(
        r"\b(?:ИЗ|FROM)\s+([A-Za-zА-Яа-яЁё0-9_\.]+)",
        query or "",
        re.I,
    )
    return m.group(1) if m else None


# Универсальный эталон: Документ.<Имя> и поля — из metadata/structure ТЕКУЩЕЙ базы
EXAMPLE_1C_DOCUMENT_PERIOD = (
    "ВЫБРАТЬ ПЕРВЫЕ 500 Т.Номер КАК Номер, Т.Дата КАК Дата, "
    "ПРЕДСТАВЛЕНИЕ(Т.<ПолеКонтрагентаИзStructure>) КАК Контрагент, "
    "Т.<ПолеСуммыИзStructure> КАК Сумма "
    "ИЗ Документ.<ИмяИзFindMetadata> КАК Т "
    "ГДЕ Т.Дата МЕЖДУ ДАТАВРЕМЯ(г,м,д,0,0,0) И ДАТАВРЕМЯ(г,м,д,23,59,59) "
    "И Т.Проведен = ИСТИНА УПОРЯДОЧИТЬ ПО Т.Дата"
)

# Дата: 2015-02-01 | 2015-02 | 2015 (в кавычках или без)
_DATE = r"['\"]?(\d{4}(?:-\d{2}(?:-\d{2})?)?)['\"]?"


def _replace_between_dates(q: str) -> tuple[str, bool]:
    """BETWEEN / МЕЖДУ с датами → МЕЖДУ ДАТАВРЕМЯ … И ДАТАВРЕМЯ …"""
    changed = False

    def _between(m: re.Match[str]) -> str:
        nonlocal changed
        changed = True
        d1, d2 = m.group(1), m.group(2)
        return (
            f"МЕЖДУ {_iso_to_datavremya(d1)} "
            f"И {_iso_to_datavremya(d2, end_of_day=True)}"
        )

    # BETWEEN x AND/И y  (английский или смешанный)
    q2 = re.sub(
        rf"\bBETWEEN\s+{_DATE}\s+(?:AND|И)\s+{_DATE}",
        _between,
        q,
        flags=re.IGNORECASE,
    )
    # МЕЖДУ '2015-02' И '2015-02'  (ещё не ДАТАВРЕМЯ)
    q2 = re.sub(
        rf"\bМЕЖДУ\s+{_DATE}\s+И\s+{_DATE}",
        _between,
        q2,
        flags=re.IGNORECASE,
    )
    return q2, changed


# Плейсхолдеры из справки/примеров — модель копирует их в execute_query
_PLACEHOLDER_TABLE = re.compile(
    r"(?i)"
    r"(?:Документ|Справочник|Регистр(?:Сведений|Накопления)?)\."
    r"(?:"
    r"Имя(?:Документа|Объекта|Справочника|Регистра|ТЧ|ТабличнойЧасти)|"
    r"Name(?:Of)?Document|"
    r"<[^>]+>"  # Документ.<из find_metadata>
    r")"
)
_PLACEHOLDER_ANY = re.compile(
    r"(?i)\b(?:ИмяДокумента|ИмяОбъекта|ИмяТЧ|ИмяТабличнойЧасти|"
    r"ИмяСправочника|ИмяРегистра|YourDocumentName)\b"
)


def find_query_placeholders(query: str) -> list[str]:
    """Найти выдуманные/шаблонные имена таблиц в тексте запроса."""
    q = query or ""
    found: list[str] = []
    for m in _PLACEHOLDER_TABLE.finditer(q):
        found.append(m.group(0))
    for m in _PLACEHOLDER_ANY.finditer(q):
        t = m.group(0)
        if t not in found:
            found.append(t)
    return found


def unescape_and_flatten(query: str) -> str:
    """
    Привести текст запроса к одной строке для 1С.

    Модели иногда вставляют в параметр query служебные escape-последовательности
    вместо обычных пробелов — из‑за этого 1С ругается на синтаксис у «ПЕРВЫЕ N».
    Здесь они снимаются; в инструкциях агенту эти escape не показывают.
    """
    q = (query or "").strip()
    if not q:
        return q

    # снять удвоенные и обычные escape (внутренне, не для отображения модели)
    for old, new in (
        ("\\\\r\\\\n", "\n"),
        ("\\\\n", "\n"),
        ("\\\\t", "\t"),
        ("\\\\r", "\r"),
        ("\\r\\n", "\n"),
        ("\\n", "\n"),
        ("\\t", "\t"),
        ("\\r", "\r"),
    ):
        if old in q:
            q = q.replace(old, new)

    q = q.replace("\t", " ")
    q = re.sub(r"[\r\n\u2028\u2029]+", " ", q)
    q = re.sub(r" {2,}", " ", q)
    return q.strip()


def normalize_to_1c_query(query: str) -> dict[str, Any]:
    original = (query or "").strip()
    if not original:
        return {
            "query": original,
            "converted": False,
            "warnings": [],
            "error": "Пустой запрос",
        }

    original = unescape_and_flatten(original)

    placeholders = find_query_placeholders(original)
    if placeholders:
        return {
            "query": original,
            "converted": False,
            "warnings": [],
            "error": (
                f"В запросе шаблонное/выдуманное имя: {placeholders[0]}. "
                "Так нельзя — это не таблица базы."
            ),
            "hint": (
                "Для отчёта: build_report(request='фраза пользователя целиком'). "
                "Для ручного ВЫБРАТЬ: quick_find → full_name → get_object_structure → "
                "подставь РЕАЛЬНОЕ имя из find_metadata ЭТОЙ базы (Документ.X / …). "
                "Никогда не копируй ИмяДокумента / <…> из справки."
            ),
            "prefer_tool": "build_report",
            "placeholders": placeholders,
        }

    if has_select_star(original):
        return {
            "query": original,
            "converted": False,
            "warnings": [],
            "error": "Нельзя SELECT * / ВЫБРАТЬ * — перечисли поля из get_object_structure",
            "hint": (
                "Для отчёта: build_report(request=…). "
                "Или: quick_find → get_object_structure → query_examples(object_name=full_name)."
            ),
        }

    q = original
    warnings: list[str] = []
    converted = False

    if looks_like_ansi_sql(q) or re.search(r"\bBETWEEN\b", q, re.I):
        converted = True
        if looks_like_ansi_sql(q):
            warnings.append(
                "Смешанный/ANSI SQL приведён к языку запросов 1С "
                "(ВЫБРАТЬ/ИЗ/ГДЕ/МЕЖДУ ДАТАВРЕМЯ)."
            )

        replacements = [
            (r"\bSELECT\s+DISTINCT\b", "ВЫБРАТЬ РАЗЛИЧНЫЕ"),
            (r"\bSELECT\s+TOP\s+(\d+)\b", r"ВЫБРАТЬ ПЕРВЫЕ \1"),
            (r"\bSELECT\b", "ВЫБРАТЬ"),
            (r"\bFROM\b", "ИЗ"),
            (r"\bWHERE\b", "ГДЕ"),
            (r"\bINNER\s+JOIN\b", "ВНУТРЕННЕЕ СОЕДИНЕНИЕ"),
            (r"\bLEFT\s+OUTER\s+JOIN\b", "ЛЕВОЕ СОЕДИНЕНИЕ"),
            (r"\bLEFT\s+JOIN\b", "ЛЕВОЕ СОЕДИНЕНИЕ"),
            (r"\bRIGHT\s+JOIN\b", "ПРАВОЕ СОЕДИНЕНИЕ"),
            (r"\bFULL\s+JOIN\b", "ПОЛНОЕ СОЕДИНЕНИЕ"),
            (r"\bJOIN\b", "ВНУТРЕННЕЕ СОЕДИНЕНИЕ"),
            (r"\bGROUP\s+BY\b", "СГРУППИРОВАТЬ ПО"),
            (r"\bORDER\s+BY\b", "УПОРЯДОЧИТЬ ПО"),
            (r"\bHAVING\b", "ИМЕЮЩИЕ"),
            (r"\bASC\b", "ВОЗР"),
            (r"\bDESC\b", "УБЫВ"),
            (r"\bAS\b", "КАК"),
            (r"\bTRUE\b", "ИСТИНА"),
            (r"\bFALSE\b", "ЛОЖЬ"),
            (r"\bIS\s+NULL\b", "ЕСТЬ NULL"),
            (r"\bCOUNT\s*\(", "КОЛИЧЕСТВО("),
            (r"\bSUM\s*\(", "СУММА("),
            (r"\bMAX\s*\(", "МАКСИМУМ("),
            (r"\bMIN\s*\(", "МИНИМУМ("),
            (r"\bAVG\s*\(", "СРЕДНЕЕ("),
            (r"\bAND\b", "И"),
            (r"\bOR\b", "ИЛИ"),
            (r"\bNOT\b", "НЕ"),
            (r"\bON\b", "ПО"),
        ]
        for pat, repl in replacements:
            q = re.sub(pat, repl, q, flags=re.IGNORECASE)

    # BETWEEN / МЕЖДУ с датами — всегда (в т.ч. '2015-02')
    q, bet = _replace_between_dates(q)
    if bet:
        converted = True
        warnings.append("BETWEEN/'YYYY-MM' → МЕЖДУ ДАТАВРЕМЯ(начало) И ДАТАВРЕМЯ(конец дня)")

    # Компактные даты 1С: '20150101000000' / '20150101' → ДАТАВРЕМЯ
    def _compact_dt(m: re.Match[str]) -> str:
        s = m.group(1)
        y, mo, d = int(s[0:4]), int(s[4:6]), int(s[6:8])
        if len(s) >= 14:
            hh, mm, ss = int(s[8:10]), int(s[10:12]), int(s[12:14])
        else:
            hh = mm = ss = 0
        return f"ДАТАВРЕМЯ({y},{mo},{d},{hh},{mm},{ss})"

    new_q2 = re.sub(
        r"['\"](20\d{2}[01]\d[0-3]\d(?:[0-2]\d[0-5]\d[0-5]\d)?)['\"]",
        _compact_dt,
        q,
    )
    if new_q2 != q:
        q = new_q2
        converted = True
        warnings.append("Компактная дата 'YYYYMMDD…' → ДАТАВРЕМЯ(...)")

    # &НачалоПериода / &КонецПериода — год из запроса или прошлый полный
    year_m = re.search(r"\b((?:19|20)\d{2})\b", q)
    if year_m:
        year = int(year_m.group(1))
    else:
        from datetime import datetime as _dt

        year = max(1990, _dt.now().year - 1)
    if re.search(
        r"&НачалоПериода|&КонецПериода|&StartDate|&EndDate|&НачалоГода|&КонецГода",
        q,
        re.I,
    ):
        q = re.sub(
            r"&НачалоПериода|&StartDate|&НачалоГода",
            f"ДАТАВРЕМЯ({year},1,1,0,0,0)",
            q,
            flags=re.I,
        )
        q = re.sub(
            r"&КонецПериода|&EndDate|&КонецГода",
            f"ДАТАВРЕМЯ({year},12,31,23,59,59)",
            q,
            flags=re.I,
        )
        converted = True
        warnings.append(
            f"Параметры &Начало/Конец → ДАТАВРЕМЯ за {year} "
            "(если нужен другой год — укажите явно)"
        )

    # Одиночные сравнения с датой
    def _cmp_date(m: re.Match[str]) -> str:
        op, iso = m.group(1), m.group(2)
        if op in ("<=", "<"):
            return f"{op} {_iso_to_datavremya(iso, end_of_day=True)}"
        return f"{op} {_iso_to_datavremya(iso, end_of_day=False)}"

    new_q = re.sub(
        rf"(>=|<=|<>|=|>|<)\s*{_DATE}",
        _cmp_date,
        q,
    )
    if new_q != q:
        q = new_q
        converted = True
        warnings.append("Сравнение с датой → ДАТАВРЕМЯ(...)")

    # ПЕРВЫЕ — корректный порядок: ВЫБРАТЬ [РАЗРЕШЕННЫЕ] [РАЗЛИЧНЫЕ] ПЕРВЫЕ N …
    # Не ломать «ВЫБРАТЬ РАЗРЕШЕННЫЕ …» → «ВЫБРАТЬ ПЕРВЫЕ 500 РАЗРЕШЕННЫЕ …»
    if re.match(r"^\s*ВЫБРАТЬ\b", q, re.I) and not re.search(
        r"^\s*ВЫБРАТЬ\s+(?:РАЗРЕШЕННЫЕ\s+|РАЗЛИЧНЫЕ\s+)*ПЕРВЫЕ\b",
        q,
        re.I,
    ):
        m_head = re.match(
            r"^(\s*ВЫБРАТЬ)((?:\s+РАЗРЕШЕННЫЕ)?(?:\s+РАЗЛИЧНЫЕ)?)\b",
            q,
            re.I,
        )
        if m_head:
            prefix = m_head.group(1) + (m_head.group(2) or "")
            q = prefix + " ПЕРВЫЕ 500" + q[m_head.end() :]
        else:
            q = re.sub(
                r"^\s*ВЫБРАТЬ\b",
                "ВЫБРАТЬ ПЕРВЫЕ 500",
                q,
                count=1,
                flags=re.IGNORECASE,
            )
        warnings.append("Добавлено ПЕРВЫЕ 500")

    # Таблица Тип.Имя
    table = extract_from_table(q)
    if table and "." not in table:
        return {
            "query": q.strip(),
            "converted": converted,
            "warnings": warnings,
            "error": (
                f"Неверное имя таблицы «{table}». "
                "Нужно full_name: Документ.X или Справочник.Y из find_metadata"
            ),
            "hint": EXAMPLE_1C_DOCUMENT_PERIOD,
            "bad_table": table,
        }

    # Остался BETWEEN — не смогли разобрать даты
    if re.search(r"\bBETWEEN\b", q, re.I):
        return {
            "query": q.strip(),
            "converted": converted,
            "warnings": warnings,
            "error": (
                "Не удалось преобразовать BETWEEN. "
                "Пиши: Т.Дата МЕЖДУ ДАТАВРЕМЯ(г,м,д,0,0,0) И ДАТАВРЕМЯ(г,м,д,23,59,59)"
            ),
            "hint": EXAMPLE_1C_DOCUMENT_PERIOD,
            "original_query": original[:400],
        }

    # SELECT/FROM/WHERE без BETWEEN
    if re.search(r"\b(SELECT|FROM|WHERE)\b", q, re.I):
        return {
            "query": q.strip(),
            "converted": converted,
            "warnings": warnings,
            "error": "В запросе остался ANSI SQL — используй только синтаксис 1С",
            "hint": EXAMPLE_1C_DOCUMENT_PERIOD,
            "original_query": original[:400],
        }

    return {
        "query": q.strip(),
        "converted": converted,
        "warnings": warnings,
    }

