# -*- coding: utf-8 -*-
"""
Анализ управляемых блокировок 1С по коду конфигурации (Qdrant).

Что МОЖЕТ:
  - найти где создаётся БлокировкаДанных / УстановитьБлокировку / ЗаблокироватьДанные*
  - режим (Исключительный / Разделяемый), пространства (Документ./Регистр…)
  - сгруппировать по модулям/документам, подсказать «горячие» места
  - связать с проведением (ObjectModule + ОбработкаПроведения)

Что НЕ МОЖЕТ (без ТЖ/кластера/консоли):
  - live «кто кого ждёт» в runtime
  - граф ожиданий, deadlock monitor, % времени wait
  - блокировки СУБД вне управляемого режима
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Callable

# Поисковые запросы (RU + EN API)
_LOCK_QUERIES: tuple[str, ...] = (
    "БлокировкаДанных",
    "Новый БлокировкаДанных",
    "УстановитьБлокировку",
    "Блокировка.Добавить",
    "РежимБлокировкиДанных",
    "ЗаблокироватьДанныеДляРедактирования",
    "РазблокироватьДанныеДляРедактирования",
    "DataLock",
    "LockDataForEdit",
    "ДЛЯ ИЗМЕНЕНИЯ",
    "ДЛЯОБНОВЛЕНИЯ",
)

_SPACE_RE = re.compile(
    r'Блокировка\.Добавить\s*\(\s*["\']([^"\']+)["\']'
    r'|Добавить\s*\(\s*["\']((?:Документ|Справочник|Регистр)[^"\']+)["\']',
    re.I,
)
_MODE_RE = re.compile(
    r"РежимБлокировкиДанных\.(Исключительный|Разделяемый|Exclusive|Shared)",
    re.I,
)
_VALUE_RE = re.compile(
    r'УстановитьЗначение\s*\(\s*["\']([^"\']+)["\']',
    re.I,
)
_BEGIN_TX = re.compile(r"НачатьТранзакцию|BeginTransaction", re.I)
_SET_LOCK = re.compile(r"УстановитьБлокировку\s*\(|\.Lock\s*\(", re.I)


def is_locks_or_contention_question(phrase: str) -> bool:
    pl = (phrase or "").lower().replace("ё", "е")
    if not pl:
        return False
    markers = (
        "блокировк",
        "управляем",
        "datalock",
        "lock data",
        r"взаимн\w*\s+блокир",
        "deadlock",
        r"ожидан\w*\s+блокир",
        r"конфликт\w*\s+блокир",
        r"таймаут\w*\s+блокир",
        "кто блокир",
        "где блокир",
        "как блокир",
        r"тормоз\w*.*блок",
        "блок.*тормоз",
        r"завис\w*.*провед",
        r"долг\w*\s+провед",
        "ожидание на блокир",
        "managed lock",
        r"исключительн\w*\s+блокир",
        r"разделяем\w*\s+блокир",
        "заблокироватьданные",
    )
    if any(re.search(m, pl) for m in markers):
        return True
    if "блокир" in pl and any(
        x in pl for x in ("где", "как", "модул", "провед", "регистр", "документ", "анализ")
    ):
        return True
    # «почему тормозит проведение» — частично locks + heavy posting
    if re.search(r"тормоз|медленн|долг\w+\s+проводи", pl) and re.search(
        r"провед|документ|регистр|запис", pl
    ):
        return True
    return False


def _parse_snippet(snippet: str) -> dict[str, Any]:
    sn = snippet or ""
    spaces = []
    for m in _SPACE_RE.finditer(sn):
        sp = m.group(1) or m.group(2)
        if sp:
            spaces.append(sp)
    modes = [m.group(1) for m in _MODE_RE.finditer(sn)]
    fields = [m.group(1) for m in _VALUE_RE.finditer(sn)]
    return {
        "spaces": spaces,
        "modes": modes,
        "lock_fields": fields,
        "has_transaction": bool(_BEGIN_TX.search(sn)),
        "has_set_lock": bool(_SET_LOCK.search(sn)),
        "for_update": bool(re.search(r"ДЛЯ\s*ИЗМЕНЕНИЯ|FOR\s+UPDATE", sn, re.I)),
    }


def _owner_hit(m: dict[str, Any]) -> str:
    path = str(m.get("path") or "")
    obj = str(m.get("object_name") or "")
    if obj:
        return obj
    parts = path.replace("\\", "/").split("/")
    for i, p in enumerate(parts):
        if p in (
            "Documents",
            "Catalogs",
            "CommonModules",
            "DataProcessors",
            "Reports",
            "InformationRegisters",
            "AccumulationRegisters",
        ) and i + 1 < len(parts):
            return parts[i + 1]
    return path or "?"


def analyze_managed_locks(
    request: str = "",
    *,
    object_hint: str = "",
    max_hits: int = 40,
    search_code_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Скан кода на управляемые блокировки.
    search_code_fn(query, max_matches=..., context_lines=...) → {matches:[]}
    """
    req = (request or "").strip()
    hint = (object_hint or "").strip()
    lim = max(8, min(int(max_hits or 40), 80))

    if search_code_fn is None:
        return {
            "ok": False,
            "error": "search_code недоступен (Qdrant?)",
            "hint": "Включите qdrant и индекс кода",
        }

    queries = list(_LOCK_QUERIES)
    # стемы из фразы / hint
    if hint:
        queries = [f"{hint} БлокировкаДанных", f"{hint} УстановитьБлокировку"] + queries
    stems = re.findall(r"[а-яa-zё]{4,}", (req + " " + hint).lower().replace("ё", "е"))
    for s in stems[:5]:
        if s not in (
            "блокир",
            "где",
            "как",
            "тормоз",
            "провед",
            "управля",
            "документ",
        ):
            queries.insert(0, f"{s} БлокировкаДанных")

    seen_ids: set[str] = set()
    raw_hits: list[dict[str, Any]] = []
    for q in queries[:14]:
        try:
            res = search_code_fn(q, max_matches=min(12, lim), context_lines=3)
        except TypeError:
            try:
                res = search_code_fn(query=q, max_matches=min(12, lim), context_lines=3)
            except Exception:
                continue
        except Exception:
            continue
        if not isinstance(res, dict):
            continue
        for m in res.get("matches") or []:
            if not isinstance(m, dict):
                continue
            mid = str(m.get("id") or m.get("path") or "") + "|" + str(m.get("snippet") or "")[:80]
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
            raw_hits.append(m)
        if len(raw_hits) >= lim:
            break

    # filter noise: Rights.xml etc.
    hits: list[dict[str, Any]] = []
    for m in raw_hits:
        path = str(m.get("path") or "")
        sn = str(m.get("snippet") or "")
        if path.endswith(".xml") and "Rights" in path:
            continue
        if not any(
            k in sn
            for k in (
                "Блокировка",
                "Lock",
                "УстановитьБлокировку",
                "Заблокировать",
                "ДЛЯ ИЗМЕНЕНИЯ",
                "РежимБлокировки",
            )
        ) and "Блокировка" not in path:
            # keep if query was strong
            if "БлокировкаДанных" not in sn and "DataLock" not in sn:
                continue
        parsed = _parse_snippet(sn)
        owner = _owner_hit(m)
        role = str(m.get("module_role") or "")
        hits.append(
            {
                "path": path,
                "object": owner,
                "module_role": role,
                "location": m.get("location") or "",
                "score": m.get("score"),
                "snippet": sn[:500],
                "spaces": parsed["spaces"],
                "modes": parsed["modes"],
                "lock_fields": parsed["lock_fields"],
                "has_transaction": parsed["has_transaction"],
                "has_set_lock": parsed["has_set_lock"],
                "for_update": parsed["for_update"],
                "procs": (m.get("proc_names") or [])[:6],
            }
        )

    # aggregate
    by_object: dict[str, list[dict]] = defaultdict(list)
    space_cnt: Counter[str] = Counter()
    mode_cnt: Counter[str] = Counter()
    exclusive_paths: list[str] = []
    for h in hits:
        by_object[h["object"]].append(h)
        for s in h["spaces"]:
            space_cnt[s] += 1
        for md in h["modes"]:
            mode_cnt[md] += 1
            if md.lower().startswith("исключ") or md.lower().startswith("excl"):
                exclusive_paths.append(h["path"])

    hot_objects = sorted(
        by_object.items(), key=lambda kv: (-len(kv[1]), kv[0])
    )[:15]
    hot_spaces = space_cnt.most_common(20)

    advice: list[str] = []
    if exclusive_paths:
        advice.append(
            "Есть ИСКЛЮЧИТЕЛЬНЫЕ блокировки — при параллельном проведении "
            "документов по тем же измерениям возможны ожидания. Смотри exclusive_hits."
        )
    if any(h.get("for_update") for h in hits):
        advice.append(
            "В запросах встречается ДЛЯ ИЗМЕНЕНИЯ — блокировка на уровне СУБД "
            "на время транзакции; держите транзакцию короткой."
        )
    if hot_spaces:
        top_sp = ", ".join(s for s, _ in hot_spaces[:5])
        advice.append(
            f"Чаще всего блокируют пространства: {top_sp}. "
            "Сузьте измерения (УстановитьЗначение), не блокируйте «весь» регистр."
        )
    advice.extend(
        [
            "Runtime «кто ждёт кого»: Технологический журнал (TLOCK/TTIMEOUT), "
            "консоль кластера, монитор СУБД — HTTP read-only агента это не отдаёт.",
            "Медленное проведение: analyze_object_movements + search_code(Отразить) — "
            "много регистров/запросов в цикле часто «тормозит» сильнее блокировок.",
            "Повторный анализ объекта: analyze_locks(request=…, object_hint=ИмяДокумента).",
        ]
    )

    summary_parts = [
        f"Найдено вхождений блокировок в коде: {len(hits)} "
        f"(уникальных модулей/объектов: {len(by_object)})."
    ]
    if hot_objects:
        top_o = ", ".join(f"{o}({len(hs)})" for o, hs in hot_objects[:6])
        summary_parts.append(f"Топ объектов: {top_o}.")
    if mode_cnt:
        summary_parts.append(
            "Режимы: " + ", ".join(f"{k}={v}" for k, v in mode_cnt.most_common())
        )

    return {
        "ok": True,
        "request": req,
        "object_hint": hint or None,
        "summary": " ".join(summary_parts),
        "hits_count": len(hits),
        "objects_count": len(by_object),
        "hot_objects": [
            {
                "object": o,
                "hits": len(hs),
                "paths": list({h["path"] for h in hs})[:5],
                "spaces": list(
                    {s for h in hs for s in (h.get("spaces") or [])}
                )[:8],
                "modes": list({m for h in hs for m in (h.get("modes") or [])}),
            }
            for o, hs in hot_objects
        ],
        "hot_spaces": [{"space": s, "count": c} for s, c in hot_spaces],
        "mode_counts": dict(mode_cnt),
        "exclusive_hits": list(dict.fromkeys(exclusive_paths))[:15],
        "exclusive_hits_total": len(set(exclusive_paths)),
        "hits": hits[:lim],
        "advice": advice,
        "limits": {
            "static_code_only": True,
            "no_runtime_wait_graph": True,
            "need_for_runtime": [
                "Технологический журнал (события TLOCK, TTIMEOUT, TDEADLOCK)",
                "Консоль администрирования кластера 1С",
                "Монитор блокировок СУБД (MS SQL/PostgreSQL)",
            ],
        },
        "prefer": (
            "1) Покажи summary + hot_objects + hot_spaces. "
            "2) Для конкретного документа: object_hint + analyze_object_movements. "
            "3) Runtime-ожидания — ТЖ/кластер, не выдумывай live-сессии. "
            "4) Имена пространств — из hits.spaces (эта CF)."
        ),
        "related_tools": [
            "analyze_object_movements(request=документ)",
            "search_code(query='БлокировкаДанных')",
            "analyze_event_log(request='блокировка') — если есть таймауты в ЖР",
            "trace_calls / explain_fill — проведение/заполнение",
        ],
    }
