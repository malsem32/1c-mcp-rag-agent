# -*- coding: utf-8 -*-
"""
Скан BSL на опасные конструкции (инъекции кода).

Универсально по любой выгрузке в Qdrant — без привязки к УТ/УПП.
"""

from __future__ import annotations

import re
from typing import Any

from vector.search import search_code_vectors

# Вызов платформенного Выполнить/Вычислить (не Запрос.Выполнить)
_DANGEROUS = (
    ("Выполнить(", r"(?<![\w.])Выполнить\s*\("),
    ("Вычислить(", r"(?<![\w.])Вычислить\s*\("),
    ("Execute(", r"(?<![\w.])Execute\s*\("),
    ("Eval(", r"(?<![\w.])Eval\s*\("),
)

_EXCHANGE_PATH = re.compile(
    r"(обмен|exchange|синхрон|synchron|конверт|conversion|dataexchange|"
    r"обменс|правиладляобмена|выполнениобмен)",
    re.I,
)

_SAFE_HINTS = re.compile(
    r"(Безопасн\w*Режим|SafeMode|ВыполнитьВБезопасном|ВычислитьВБезопасном)",
    re.I,
)


def is_injection_scan_question(phrase: str) -> bool:
    pl = (phrase or "").lower().replace("ё", "е")
    if not pl:
        return False
    return any(
        x in pl
        for x in (
            "инъекц",
            "уязвим",
            "выполнить(",
            "вычислить(",
            "execute(",
            "eval(",
            "безопасн",
            "инъекци",
            "code injection",
        )
    ) or (
        ("выполнить" in pl or "вычислить" in pl)
        and any(x in pl for x in ("обмен", "модул", "найди", "где", "оцен"))
    )


def _risk_for_snippet(text: str, kind: str) -> str:
    """high | medium | low."""
    t = text or ""
    # Запрос.Выполнить — не инъекция кода
    if re.search(r"Запрос\s*\.\s*Выполнить\s*\(|Query\s*\.\s*Execute\s*\(", t, re.I):
        return "not_code_exec"
    if _SAFE_HINTS.search(t):
        return "low"
    # литерал в кавычках сразу после (
    if re.search(
        rf"{re.escape(kind.rstrip('('))}\s*\(\s*[\"']",
        t,
        re.I,
    ):
        return "low"
    # СтрШаблон / СтрЗаменить в имени обработчика — динамика
    if re.search(r"СтрЗаменить|СтрШаблон|Подключаемый_", t):
        return "high"
    # переменная как аргумент
    if re.search(
        rf"{re.escape(kind.rstrip('('))}\s*\(\s*[A-Za-zА-Яа-яЁё_]",
        t,
        re.I,
    ):
        return "high"
    return "medium"


def scan_code_injection(
    *,
    scope: str = "exchange",
    limit: int = 40,
    include_query_execute: bool = False,
) -> dict[str, Any]:
    """
    scope: exchange | all
    """
    scope = (scope or "exchange").lower().strip()
    limit = max(5, min(int(limit or 40), 80))
    findings: list[dict[str, Any]] = []
    seen: set[str] = set()

    for label, _pat in _DANGEROUS:
        sc = search_code_vectors(label, limit=25, match_mode="exact")
        for m in sc.get("matches") or []:
            path = (m.get("path") or "").replace("\\", "/")
            text = m.get("text") or m.get("snippet") or ""
            risk = _risk_for_snippet(text, label)
            if risk == "not_code_exec" and not include_query_execute:
                continue
            # exclude Запрос.Выполнить by path-less filter on text again
            if "Запрос.Выполнить" in text.replace(" ", "") and not include_query_execute:
                continue
            is_ex = bool(_EXCHANGE_PATH.search(path))
            if scope in ("exchange", "обмен") and not is_ex:
                continue
            key = f"{path}:{label}:{hash(text[:80])}"
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                {
                    "kind": label.rstrip("("),
                    "path": path,
                    "object_name": m.get("object_name"),
                    "location": m.get("location"),
                    "exchange_related": is_ex,
                    "risk": risk if risk != "not_code_exec" else "low",
                    "snippet": text[:400].replace("\n", " "),
                    "score": m.get("score"),
                }
            )

    # sort: high first, exchange first
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(
        key=lambda x: (
            order.get(x.get("risk") or "medium", 9),
            0 if x.get("exchange_related") else 1,
            -(float(x.get("score") or 0)),
        )
    )
    findings = findings[:limit]

    high = sum(1 for f in findings if f.get("risk") == "high")
    med = sum(1 for f in findings if f.get("risk") == "medium")
    low = sum(1 for f in findings if f.get("risk") == "low")

    return {
        "ok": True,
        "scope": scope,
        "findings_count": len(findings),
        "risk_counts": {"high": high, "medium": med, "low": low},
        "findings": findings,
        "prefer": (
            "Перечисли path + risk. high = динамический код/подстановка — "
            "проверить источник строки. low = литерал или безопасный режим. "
            "Не путай Запрос.Выполнить() с Выполнить(строкаКода). "
            "Универсально: любая CF в Qdrant."
        ),
        "note": (
            "Эвристика, не полный SAST. "
            "scope=exchange — path с обмен/exchange/конверт; scope=all — вся выгрузка."
        ),
    }
