# -*- coding: utf-8 -*-
"""
Советник по ВНЕДРЕНИЮ управляемых блокировок (managed locks).

Сценарий: в конфигурации автоматические блокировки / locks ещё не стоят
(или стоят точечно) — агент подсказывает:
  - где ставить (ОбработкаПроведения, запись регистров/документов);
  - что блокировать (пространства из движений/структуры);
  - режим (обычно Исключительный перед записью остатков/расчётов);
  - шаблон BSL + чеклист миграции.

Не live-мониторинг. Имена — только из movements/structure/Qdrant этой CF.
"""

from __future__ import annotations

import re
from typing import Any, Callable


def is_lock_migration_question(phrase: str) -> bool:
    """«куда ставить управляемые», «внедрить блокировки», «что исправить»."""
    pl = (phrase or "").lower().replace("ё", "е")
    if not pl:
        return False
    # «где в коде уже / что блокирует» — это analyze_locks, не миграция
    if re.search(
        r"где\s+в\s+коде|уже\s+сто(ит|ят)|найти\s+блокир|скан\w*\s+блокир|"
        r"где\s+сто(ит|ят)\s+управляем",
        pl,
    ):
        return False
    if re.search(
        r"(внедр|перевест|переход|миграц|включ|"
        r"где\s+ставить|куда\s+ставить|поставить|добавить\s+блокир|"
        r"как\s+сделать|рекоменд|подскаж|исправить|доработать|"
        r"что\s+исправ|что\s+нужно\s+исправ)",
        pl,
    ) and re.search(r"блокир|управляем|managed\s*lock|datalock", pl):
        return True
    # «managed locks при проведении / для документа»
    if re.search(r"managed\s*locks?", pl) and re.search(
        r"(ставить|провед|документ|реализ|куда|где|рекоменд)", pl
    ):
        return True
    if re.search(
        r"управляем\w*\s+блокир|режим\w*\s+управления\s+блокиров",
        pl,
    ) and re.search(
        r"(внедр|ставить|провед|документ|рекоменд|шаблон|миграц|перевест)",
        pl,
    ):
        return True
    if "автоматическ" in pl and "блокир" in pl and re.search(
        r"управляем|перевест|замен", pl
    ):
        return True
    return False


def _bare_name(full_name: str) -> str:
    fn = (full_name or "").strip()
    if "." in fn:
        return fn.split(".", 1)[1]
    return fn


_TYPED_NAME = re.compile(
    r"\b(Документ|Справочник|Обработка|Отчет|Отчёт|РегистрНакопления|"
    r"РегистрСведений|РегистрБухгалтерии|РегистрРасчета|"
    r"БизнесПроцесс|Задача|ПланОбмена|ПланСчетов|"
    r"ПланВидовХарактеристик|ПланВидовРасчета|"
    r"Document|Catalog)\s*\.\s*"
    r"([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)",
    re.I,
)

_SPEECH_TYPE = re.compile(
    r"\b(документ[аеу]?|справочник[аеу]?|обработк[аиуе]|отч[её]т[аеу]?)\s+"
    r"([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_]{3,})",
    re.I,
)


def _speech_word_to_prefix(word: str) -> str:
    w = (word or "").lower().replace("ё", "е")
    if w.startswith("документ"):
        return "Документ"
    if w.startswith("справочник"):
        return "Справочник"
    if w.startswith("обработк"):
        return "Обработка"
    if w.startswith("отчет"):
        return "Отчет"
    return ""


def _extract_object_hint(request: str, full_name: str = "") -> str:
    """Класс.Имя из аргумента / фразы пользователя. Без угадывания Документ."""
    from backends.base import is_typed_full_name

    fn = (full_name or "").strip()
    if is_typed_full_name(fn):
        return fn
    pl = (request or "").strip()
    m = _TYPED_NAME.search(pl)
    if m:
        pref, name = m.group(1), m.group(2)
        plw = pref.lower()
        if plw == "document":
            pref = "Документ"
        elif plw == "catalog":
            pref = "Справочник"
        pref = pref.replace("Отчёт", "Отчет")
        if name.lower() not in ("документ", "справочник", "проведения", "проведение"):
            return f"{pref}.{name}"
    m2 = _SPEECH_TYPE.search(pl)
    if m2:
        pref = _speech_word_to_prefix(m2.group(1))
        name = m2.group(2)
        if pref and name.lower() not in ("документ", "проведения", "проведение"):
            return f"{pref}.{name}"
    m3 = re.search(r"\b([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9]{4,})\b", pl)
    if m3 and m3.group(1).lower() not in (
        "документ",
        "регистр",
        "блокировка",
        "справочник",
        "обработка",
    ):
        return m3.group(1)
    return ""


def _full_name_from_resolve_result(ro: Any) -> str:
    from backends.base import is_typed_full_name

    if not isinstance(ro, dict):
        return ""
    for key in ("full_name", "top"):
        v = ro.get(key)
        if isinstance(v, dict):
            v = v.get("full_name") or ""
        s = str(v or "").strip()
        if is_typed_full_name(s):
            return s
    for o in ro.get("options") or []:
        if isinstance(o, dict):
            s = str(o.get("full_name") or "").strip()
        else:
            s = str(o or "").strip()
        if is_typed_full_name(s):
            return s
    return ""


def _template_bsl(
    spaces: list[dict[str, Any]],
    *,
    exclusive: bool = True,
) -> str:
    """Шаблон фрагмента — имена пространств из анализа, не hardcode CF."""
    mode = "Исключительный" if exclusive else "Разделяемый"
    lines = [
        "// --- управляемая блокировка (шаблон агента) ---",
        "НачатьТранзакцию();",
        "Попытка",
        "\tБлокировка = Новый БлокировкаДанных;",
    ]
    for sp in spaces[:12]:
        space = sp.get("space") or sp.get("name") or ""
        if not space:
            continue
        # в 1С: Блокировка.Добавить("РегистрНакопления.X")
        lines.append(f'\tЭлементБлокировки = Блокировка.Добавить("{space}");')
        lines.append(f"\tЭлементБлокировки.Режим = РежимБлокировкиДанных.{mode};")
        dims = sp.get("dimensions") or sp.get("lock_fields") or []
        for d in dims[:6]:
            # значение — из объекта проведения
            lines.append(
                f'\t// ЭлементБлокировки.УстановитьЗначение("{d}", <из объекта/ТЧ>);'
            )
    lines.extend(
        [
            "\tБлокировка.Заблокировать();",
            "\t// … чтение остатков / формирование Движений / Записать …",
            "\tЗафиксироватьТранзакцию();",
            "Исключение",
            "\tОтменитьТранзакцию();",
            "\tВызватьИсключение;",
            "КонецПопытки;",
        ]
    )
    return "\n".join(lines)


def _priority_for_register(name: str) -> int:
    """Выше = важнее блокировать при проведении (остатки/расчёты)."""
    n = (name or "").lower().replace("ё", "е")
    sc = 10
    for k, w in (
        ("расчет", 50),
        ("взаиморасчет", 50),
        ("товар", 40),
        ("склад", 40),
        ("остат", 45),
        ("себестоим", 35),
        ("парт", 30),
        ("свободн", 35),
        ("заказ", 25),
        ("серия", 15),
        ("ндс", 10),
        ("парти", 20),
    ):
        if k in n:
            sc += w
    if n.startswith("регистрсведений"):
        sc -= 5
    return sc


def recommend_managed_locks(
    request: str = "",
    *,
    full_name: str = "",
    max_registers: int = 15,
    movements_fn: Callable[..., dict[str, Any]] | None = None,
    locks_fn: Callable[..., dict[str, Any]] | None = None,
    structure_fn: Callable[[str], dict[str, Any]] | None = None,
    resolve_fn: Callable[..., dict[str, Any]] | None = None,
    search_code_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Рекомендации по внедрению управляемых блокировок для документа/сценария.
    """
    req = (request or "").strip()
    fn = (full_name or "").strip()
    hint = _extract_object_hint(req, fn)

    from backends.base import is_typed_full_name

    if not is_typed_full_name(fn) and resolve_fn:
        try:
            q = req if (" " in req or not hint) else hint
            ro = resolve_fn(q)
            picked = _full_name_from_resolve_result(ro)
            if picked:
                fn = picked
        except Exception:
            pass
    if not fn and is_typed_full_name(hint):
        fn = hint
    # не выдумывать Документ.{hint} — класс только из resolve / фразы пользователя

    bare = _bare_name(fn) if fn else (hint.split(".")[-1] if hint else "")

    # --- existing locks ---
    existing: dict[str, Any] = {}
    if locks_fn:
        try:
            existing = locks_fn(
                request=req or f"блокировки {bare}",
                object_hint=bare,
                max_hits=30,
            )
        except TypeError:
            try:
                existing = locks_fn(request=req, object_hint=bare)
            except Exception as e:
                existing = {"error": str(e)}
        except Exception as e:
            existing = {"error": str(e)}

    # --- movements / registers written ---
    movements: dict[str, Any] = {}
    if movements_fn and (fn or bare or req):
        try:
            movements = movements_fn(
                request=req or f"регистры проведения {bare}",
                full_name=fn or "",
            )
        except TypeError:
            movements = movements_fn(request=req, full_name=fn)
        except Exception as e:
            movements = {"error": str(e)}

    reg_names: list[str] = []
    for key in (
        "register_names_full",
        "register_names",
        "registers_all",
    ):
        val = movements.get(key)
        if isinstance(val, list) and val:
            for x in val:
                if isinstance(x, str):
                    reg_names.append(x)
                elif isinstance(x, dict) and x.get("name"):
                    n = str(x["name"])
                    if not n.startswith("Регистр"):
                        # may be bare
                        reg_names.append(n)
                    else:
                        reg_names.append(n)
            break
    if not reg_names:
        for r in movements.get("registers") or []:
            if isinstance(r, dict):
                n = r.get("full_name") or r.get("name") or ""
                if n:
                    reg_names.append(str(n))
            elif isinstance(r, str):
                reg_names.append(r)

    # normalize to Регистр* if possible from meta list
    norm_regs: list[str] = []
    seen: set[str] = set()
    for n in reg_names:
        n = str(n).strip()
        # strip checkmarks from display
        n = n.replace(" ✓", "").replace("✓", "").strip()
        if not n or n in seen:
            continue
        seen.add(n)
        if not n.startswith("Регистр") and not n.startswith("Документ"):
            # keep bare for later
            pass
        norm_regs.append(n)

    # sort by business priority
    norm_regs.sort(key=lambda x: -_priority_for_register(x))

    # если movements пуст, но analyze_locks нашёл spaces — подставить их
    if not norm_regs and isinstance(existing, dict):
        for h in existing.get("hits") or []:
            if not isinstance(h, dict):
                continue
            for s in h.get("spaces") or []:
                sn = str(s).strip()
                if sn and sn not in seen:
                    seen.add(sn)
                    norm_regs.append(sn)
        for sp in existing.get("hot_spaces") or []:
            if isinstance(sp, dict):
                sn = str(sp.get("space") or "").strip()
            else:
                sn = str(sp).strip()
            if sn and sn not in seen:
                seen.add(sn)
                norm_regs.append(sn)
        # также из snippet'ов БлокировкаДанных.*
        for h in existing.get("hits") or []:
            snip = str((h or {}).get("snippet") or (h or {}).get("text") or "")
            for m in re.finditer(
                r"Регистр(?:Накопления|Сведений)\.([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)",
                snip,
            ):
                full = f"РегистрНакопления.{m.group(1)}"
                if "Сведений" in m.group(0):
                    full = f"РегистрСведений.{m.group(1)}"
                if full not in seen:
                    seen.add(full)
                    norm_regs.append(full)

    # dimensions from structure for top regs
    recommendations: list[dict[str, Any]] = []
    # если всё ещё пусто — generic рекомендации по документу
    if not norm_regs and (fn or bare):
        recommendations.append(
            {
                "space": fn or "",
                "priority": 60,
                "mode": "Исключительный",
                "dimensions_suggest": [],
                "when": "В ОбработкаПроведения до чтения остатков/расчётов",
                "why": (
                    "movements не нашли регистры — блокируйте объект "
                    "(full_name из resolve этой CF) и измерения по structure"
                    if fn
                    else "нет typed full_name — сначала resolve_object этой базы"
                ),
                "already_has_lock_in_code": bool(
                    (existing.get("hits") if isinstance(existing, dict) else None) or []
                ),
                "action": "analyze_object_movements + structure → точечные пространства",
            }
        )

    for reg in norm_regs[: max(3, min(int(max_registers or 15), 25))]:
        space = reg
        if is_typed_full_name(space) or str(space).startswith("Регистр"):
            candidates = [space]
        else:
            # голое имя регистра — оба класса платформы, не «Документ.»
            candidates = [
                f"РегистрНакопления.{reg}",
                f"РегистрСведений.{reg}",
            ]

        dims: list[str] = []
        resolved_space = space
        if structure_fn:
            for cand in candidates:
                try:
                    st = structure_fn(cand)
                except Exception:
                    st = {}
                if not isinstance(st, dict) or st.get("error"):
                    continue
                resolved_space = cand
                dims = list(st.get("dimension_names") or [])
                if not dims:
                    dims = [
                        d.get("name")
                        for d in (st.get("dimensions") or [])
                        if isinstance(d, dict) and d.get("name")
                    ]
                break

        prio = _priority_for_register(resolved_space)
        why = []
        if prio >= 50:
            why.append("расчёты/взаиморасчёты — типичный конфликт при параллельном проведении")
        elif prio >= 40:
            why.append("товары/склады/остатки — нужна блокировка до чтения остатка и записи")
        elif prio >= 30:
            why.append("связан с запасами/заказами — желательна точечная блокировка")
        else:
            why.append("движение при проведении — блокировать при конкурентной записи")

        # already locked?
        already = False
        for h in (existing.get("hits") or []) if isinstance(existing, dict) else []:
            for s in h.get("spaces") or []:
                if resolved_space in str(s) or reg in str(s):
                    already = True
        for sp in (existing.get("hot_spaces") or []) if isinstance(existing, dict) else []:
            if resolved_space in str(sp.get("space") or ""):
                already = True

        recommendations.append(
            {
                "space": resolved_space,
                "priority": prio,
                "mode": "Исключительный",
                "dimensions_suggest": dims[:8],
                "when": "Перед чтением остатков/расчётов и записью движений в той же транзакции",
                "why": why[0],
                "already_has_lock_in_code": already,
                "action": (
                    "Проверить/дополнить существующую блокировку"
                    if already
                    else "ДОБАВИТЬ БлокировкаДанных + УстановитьЗначение по измерениям"
                ),
            }
        )

    recommendations.sort(key=lambda r: -int(r.get("priority") or 0))

    # places in code to touch
    places = [
        {
            "place": "ObjectModule объекта → ОбработкаПроведения",
            "what": (
                "Обернуть критичный участок: НачатьТранзакцию → Блокировка.Заблокировать "
                "→ чтение/движения → ЗафиксироватьТранзакцию"
            ),
            "priority": 100,
        },
        {
            "place": "Перед запросом остатков (.Остатки) в проведении",
            "what": "Исключительная блокировка измерений, по которым читаете остаток",
            "priority": 90,
        },
        {
            "place": "Общие модули *Сервер.Отразить* (если движения там)",
            "what": "Либо блокировка в вызывающем проведении, либо в точке записи набора",
            "priority": 70,
        },
        {
            "place": "Запись справочников/документов из форм (если конкурентно)",
            "what": "ЗаблокироватьДанныеДляРедактирования / БлокировкаДанных по ссылке",
            "priority": 40,
        },
    ]

    checklist = [
        "В конфигураторе: режим управления блокировкой данных → Управляемый "
        "(для объектов, которые переводите; или конфигурация целиком — по политике).",
        "Все записи остаточных/расчётных регистров — внутри транзакции с БлокировкаДанных.",
        "Блокировать МИНИМАЛЬНЫЙ набор измерений (Организация, Склад, Номенклатура… из structure), "
        "не «весь регистр».",
        "Порядок блокировок одинаковый во всех местах (снижает deadlock).",
        "Не держать транзакцию на интерактив/HTTP/долгие вызовы.",
        "Прогнать параллельное проведение 2+ сеансов по одним измерениям (тест).",
        "При ошибках: ТЖ TLOCK/TTIMEOUT после включения managed.",
    ]

    spaces_for_tpl = [
        {
            "space": r["space"],
            "dimensions": r.get("dimensions_suggest") or [],
        }
        for r in recommendations[:8]
        if not r.get("already_has_lock_in_code")
    ]
    if not spaces_for_tpl and recommendations:
        spaces_for_tpl = [
            {
                "space": r["space"],
                "dimensions": r.get("dimensions_suggest") or [],
            }
            for r in recommendations[:5]
        ]

    template = _template_bsl(spaces_for_tpl, exclusive=True)

    # code scan: posting without lock keywords
    posting_risk = None
    if search_code_fn and bare:
        try:
            sc = search_code_fn(
                query=f"ОбработкаПроведения {bare}",
                max_matches=5,
                context_lines=2,
            )
            snips = []
            for m in (sc.get("matches") or [])[:5]:
                sn = str(m.get("snippet") or "")
                path = m.get("path") or ""
                has_lock = bool(
                    re.search(r"БлокировкаДанных|УстановитьБлокировку", sn, re.I)
                )
                has_post = bool(
                    re.search(r"ОбработкаПроведения|Движения\.|Отразить", sn, re.I)
                )
                snips.append(
                    {
                        "path": path,
                        "has_posting_markers": has_post,
                        "has_lock_in_snippet": has_lock,
                        "snippet": sn[:350],
                    }
                )
            posting_risk = {
                "note": "Фрагменты по ОбработкаПроведения (snippet ≠ весь модуль)",
                "samples": snips,
                "likely_missing_locks": any(
                    s.get("has_posting_markers") and not s.get("has_lock_in_snippet")
                    for s in snips
                ),
            }
        except Exception as e:
            posting_risk = {"error": str(e)}

    existing_count = int(existing.get("hits_count") or 0) if isinstance(existing, dict) else 0
    summary = (
        f"Объект: {fn or bare or 'не указан'}. "
        f"Регистров/пространств из движений: {len(recommendations)}. "
        f"Уже найдено фрагментов блокировок в коде (по object/общему скану): {existing_count}. "
    )
    if recommendations:
        need = sum(1 for r in recommendations if not r.get("already_has_lock_in_code"))
        summary += f"Рекомендуется добавить/проверить блокировки: {need}."
    else:
        summary += (
            "Список регистров пуст — укажите full_name из find_metadata "
            "этой базы (класс.Имя), не из другой CF."
            "или сначала analyze_object_movements."
        )

    return {
        "ok": True,
        "mode": "recommend_introduce_managed_locks",
        "request": req,
        "full_name": fn or None,
        "object": bare or None,
        "summary": summary,
        "recommendations": recommendations,
        "places_to_edit": places,
        "bsl_template": template,
        "migration_checklist": checklist,
        "existing_locks_scan": {
            "hits_count": existing_count,
            "hot_objects": (existing.get("hot_objects") or [])[:5]
            if isinstance(existing, dict)
            else [],
            "hot_spaces": (existing.get("hot_spaces") or [])[:8]
            if isinstance(existing, dict)
            else [],
        },
        "movements_summary": {
            "conclusion": movements.get("conclusion"),
            "registers_sample": norm_regs[:12],
            "error": movements.get("error"),
        },
        "posting_code_samples": posting_risk,
        "prefer": (
            "1) Объясни менеджеру/разработчику: это РЕКОМЕНДАЦИИ по внедрению, не live. "
            "2) Покажи places_to_edit + recommendations (space, mode, dimensions). "
            "3) Дай bsl_template — подставить УстановитьЗначение из structure. "
            "4) Чеклист migration_checklist. "
            "5) Не выдумывай имена регистров — только из recommendations/movements. "
            "6) Уточни full_name если object пустой."
        ),
        "related_tools": [
            "analyze_object_movements(full_name=из resolve этой базы)",
            "analyze_locks(request=…, object_hint=…)",
            "get_object_structure(full_name=РегистрНакопления.Y) — измерения",
            "search_code(query='ОбработкаПроведения')",
        ],
    }
