# -*- coding: utf-8 -*-
"""
Структурные сигналы фразы (универсально, без доменных whitelist).

Зачем: «ЗаполнитьУсловияПродаж» содержит подстроку «продаж» → ложный data-route.
Решение: маркеры отчёта/кода ищем только в «речи» (PascalCase вырезан).
"""

from __future__ import annotations

import re

# платформенные слова — не имена объектов/процедур CF
_PASCAL_STOP = frozenset(
    {
        "Документ",
        "Справочник",
        "Процедура",
        "Функция",
        "Обработка",
        "Модуль",
        "Регистр",
        "Отчет",
        "Отчёт",
        "Document",
        "Catalog",
        "ObjectModule",
        "ManagerModule",
    }
)

# вопрос про устройство кода / логики (глаголы и роли, не имена объектов CF)
_CODE_ASK = re.compile(
    r"(логик|в\s+код[еу]|процедур|функци|модул|отслед|прослед|"
    r"как\s+работа|как\s+устроен|разбер|объясн|расскаж|"
    r"откуда\s+данн|текст\s+запрос|скд|что\s+делает|"
    r"цепочк\w*\s+вызов)",
    re.I,
)

_DOC_CTX = re.compile(
    r"в\s+документ|в\s+справочник|в\s+модул|в\s+обработк|модул\w*\s+объект",
    re.I,
)

# явная трассировка вызовов (не «логика объекта»)
_TRACE_ASK = re.compile(
    r"(отслед|прослед|цепочк\w*\s+вызов|кто\s+вызыв|trace_calls|путь\s+вызов)",
    re.I,
)


def strip_pascal_identifiers(text: str) -> str:
    """
    Убрать CamelCase/PascalCase идентификаторы из фразы.
    «ЗаполнитьУсловияПродаж в документе» → «  в документе»
    """
    return re.sub(r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_]{3,}", " ", text or "")


def speech_norm(text: str) -> str:
    """Речь пользователя без идентификаторов CF, lower, ё→е."""
    s = strip_pascal_identifiers(text)
    s = re.sub(r"\s+", " ", s).strip().lower().replace("ё", "е")
    return s


def has_pascal_identifier(text: str) -> bool:
    """Есть ли в фразе CamelCase-имя (процедура/объект), не одно слово Title."""
    for m in re.finditer(r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_]{4,}", text or ""):
        tok = m.group(0)
        if tok in _PASCAL_STOP:
            continue
        # CamelCase: XxxYyy или длинный идентификатор
        if re.search(r"[a-zа-яё][A-ZА-ЯЁ]", tok) or len(tok) >= 10:
            return True
    return False


def longest_pascal_identifier(text: str) -> str:
    """Самый длинный PascalCase-токен (кандидат имени процедуры/метода)."""
    best = ""
    for m in re.finditer(r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_]{4,}", text or ""):
        tok = m.group(0)
        if tok in _PASCAL_STOP:
            continue
        if re.search(r"[a-zа-яё][A-ZА-ЯЁ]", tok) or len(tok) >= 10:
            if len(tok) > len(best):
                best = tok
    return best


def is_trace_intent(text: str) -> bool:
    """Проследи / цепочка вызовов — не имя объекта как procedure=."""
    speech = speech_norm(text)
    if not speech:
        return False
    return bool(_TRACE_ASK.search(speech))


def is_code_intent(text: str) -> bool:
    """
    Универсально: вопрос про код/логику, а не про цифры отчёта.

    Сигналы (структура, не CF):
      - речь содержит «логик / процедур / как работает / расскажи…»
      - и/или PascalCase-идентификатор + контекст документа + вопрос
    """
    raw = (text or "").strip()
    if not raw:
        return False
    speech = speech_norm(raw)
    if _CODE_ASK.search(speech):
        # «логика заполнения» / «как работает» — код даже без PascalCase
        if has_pascal_identifier(raw):
            return True
        if re.search(r"логик|процедур|функци|модул|в\s+код|отслед|прослед|скд", speech):
            return True
    # идентификатор + «в документе» + вопрос (как/расскажи/что делает)
    if has_pascal_identifier(raw) and _DOC_CTX.search(speech):
        if re.search(r"(как|логик|расскаж|объясн|разбер|что\s+дела|отслед|прослед)", speech):
            return True
    return False


def marker_in_speech(speech: str, markers: tuple[str, ...] | list[str]) -> bool:
    """
    Маркер только в «речи» (без PascalCase).
    Для коротких маркеров (≥4) — вхождение; для очень коротких — word-ish.
    """
    if not speech:
        return False
    for m in markers:
        if not m:
            continue
        if len(m) >= 4:
            if m in speech:
                return True
        else:
            if re.search(rf"(^|[\s,.;:!?]){re.escape(m)}([\s,.;:!?]|$)", speech):
                return True
    return False


_FILTER_STOP = frozenset(
    "какие какой какая какое есть ли все всех список покажи перечисли выведи опубликованы опубликован "
    "конфигурации конфигурация базе базы программе системе нас наши что где для это они там".split()
)


def name_filter_tokens(request: str, *, speech_fallback: bool = False) -> list[str]:
    """
    Токены фильтра для dump-tools: только идентификаторы (CamelCase/«в кавычках»), не вся фраза.
    «какие http сервисы есть в конфигурации» → [] (= без фильтра, всё).
    speech_fallback=True — если имён нет, вернуть содержательные слова речи без служебных.
    """
    raw = request or ""
    out: list[str] = []
    for m in re.finditer(r"[«\"']([^«»\"']{2,80})[»\"']", raw):
        out.append(m.group(1).strip().lower().replace("ё", "е"))
    for m in re.finditer(r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_]{3,}", raw):
        tok = m.group(0)
        if tok in _PASCAL_STOP:
            continue
        if re.search(r"[a-zа-яё][A-ZА-ЯЁ]", tok) or (m.start() > 0 and len(tok) >= 4):
            out.append(tok.lower().replace("ё", "е"))
    if out or not speech_fallback:
        return out
    return [
        w
        for w in re.findall(r"[a-zа-яё0-9_]{3,}", speech_norm(raw))
        if w not in _FILTER_STOP
    ]
