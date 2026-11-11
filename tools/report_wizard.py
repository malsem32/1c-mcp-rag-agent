# -*- coding: utf-8 -*-
"""
Мастер Excel-отчёта с несколькими диаграммами.

Шаги: объект → период → разрезы → показатели → фильтры → диаграммы → подтверждение.

КРИТИЧНО:
  - «да» / «да делай» / «строим» — ТОЛЬКО подтверждение, никогда object/period
  - pre-fill из last_report / seed
  - после всех шагов «да» → build (дефолты, если шаг мусор)
  - missing answers_to_builder_kwargs / wizard_result_payload ломали build
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


def _norm(s: str) -> str:
    return (s or "").lower().replace("ё", "е").strip()


WIZARD_STEPS: list[dict[str, Any]] = [
    {
        "id": "object",
        "title": "Объект / тема отчёта",
        "question": (
            "1/6. **Что строим?** Тема или объект **этой** базы:\n"
            "• как в прошлом ответе / из фразы\n"
            "• остатки, документы, регистры — как у вас называются\n"
            "• или точный full_name (Документ.XXX / РегистрНакопления.XXX)\n"
            "Не подставляйте чужую конфигурацию."
        ),
        "examples": ["как в прошлом отчёте", "остатки на складах", "документы за период"],
        "field": "search_query",
    },
    {
        "id": "period",
        "title": "Период / даты",
        "question": (
            "2/6. **За какой период?**\n"
            "Примеры: «прошлый год», «этот и прошлый год», «январь–март», "
            "«с 01.01 по 31.12», «на конец года», «сейчас», «за все время»."
        ),
        "examples": ["прошлый год", "этот и прошлый год", "сейчас"],
        "field": "period",
    },
    {
        "id": "dimensions",
        "title": "Разрезы / поля группировки",
        "question": (
            "3/6. **По каким полям группировать?** (ось X графика)\n"
            "• контрагент / номенклатура / склад / организация…\n"
            "• несколько через запятую\n"
            "• «без группировки» — только итог\n"
            "{field_options}"
        ),
        "examples": ["контрагент", "номенклатура", "склад", "без группировки"],
        "field": "group_by",
    },
    {
        "id": "metrics",
        "title": "Показатели",
        "question": (
            "4/6. **Какие показатели?** (числа на графике)\n"
            "• сумма / количество / остаток / цена…\n"
            "{field_options}"
        ),
        "examples": ["сумма", "количество", "сумма и количество", "остаток"],
        "field": "metrics",
    },
    {
        "id": "filters",
        "title": "Фильтры",
        "question": (
            "5/6. **Фильтры?**\n"
            "• «без фильтров» / «нет» / «-»\n"
            "• «только [контрагент]», «топ 20», «проведённые»"
        ),
        "examples": ["без фильтров", "топ 20", "только проведённые"],
        "field": "filters",
    },
    {
        "id": "charts",
        "title": "Диаграммы",
        "question": (
            "6/6. **Какие диаграммы?** Несколько — через запятую.\n"
            "Типы: **столбцы** (bar), **линия** (line), **круг** (pie).\n"
            "Можно: `столбцы и pie` или `bar, pie` — ось Y возьмётся из колонок таблицы.\n"
            "{field_options}"
        ),
        "examples": ["столбцы и pie", "bar, pie, line", "только круг"],
        "field": "charts",
    },
]


# ---------------------------------------------------------------------------
# Phrase helpers
# ---------------------------------------------------------------------------


def wants_excel_chart_wizard(phrase: str) -> bool:
    """
    Запрос «отчёт/excel с графиком» (данные + файл).
    По умолчанию это АВТО-build, не пошаговый мастер.
    """
    pl = _norm(phrase)
    if not pl:
        return False
    excel = any(
        x in pl
        for x in (
            "excel",
            "xlsx",
            "эксель",
            "ексель",
            "выгруз",
            "в файл",
            "в эксель",
            "в ексель",
            "в excel",
        )
    )
    chart = any(
        x in pl
        for x in (
            "диаграмм",
            "график",
            "chart",
            "с графиками",
            "с диаграм",
            "pie",
            "столбчат",
            "круговая",
        )
    )
    report = any(x in pl for x in ("отчет", "отчёт", "выгруз", "таблиц"))
    if (excel or report) and chart:
        return True
    if any(
        x in pl
        for x in (
            "excel с диаграм",
            "excel с график",
            "эксель с диаграм",
            "эксель с график",
            "красивый excel",
            "добавь чтобы выводилось",
            "отчет с график",
            "отчёт с график",
            "отчет с диаграм",
            "отчёт с диаграм",
        )
    ):
        return True
    if chart and any(x in pl for x in ("сделай", "давай", "нужен", "выгруз", "добавь")):
        return True
    # «запихни в отчёт» / «в excel» без слова «график» — тоже отчётный запрос
    if any(
        x in pl
        for x in (
            "в excel",
            "в эксель",
            "в xlsx",
            "выгруз",
            "запих",
            "красивый отч",
        )
    ):
        return True
    return False


def wants_step_wizard(phrase: str) -> bool:
    """Явный запрос пошагового мастера (редко). Иначе — auto-build."""
    pl = _norm(phrase)
    if not pl:
        return False
    return any(
        x in pl
        for x in (
            "мастер отч",
            "мастер excel",
            "по шагам",
            "пошагов",
            "спроси параметры",
            "уточни параметры",
            "wizard",
            "шаг за шагом",
        )
    )


def default_charts_spec(
    phrase: str = "",
    value: str = "",
    rows: list | None = None,
) -> list[dict[str, Any]]:
    """Несколько диаграмм. Имена осей — из фразы или позже из rows (не хардкод Сумма)."""
    pl = _norm(phrase)
    val = (value or "").strip()
    if not val:
        if "остат" in pl:
            val = "Остаток"
        elif "колич" in pl and "сумм" not in pl:
            val = "Количество"
        elif any(x in pl for x in ("сумм", "выруч", "amount")):
            val = "Сумма"
    if rows:
        try:
            from tools.excel_export import bind_charts_spec_to_rows

            charts = parse_charts_answer(
                phrase or "столбцы и pie", default_metric=val
            )
            return bind_charts_spec_to_rows(charts, rows, phrase=phrase)[:4]
        except Exception:
            pass
    charts = parse_charts_answer(phrase or "столбцы и pie", default_metric=val)
    if not charts:
        pretty = any(
            x in pl
            for x in ("красив", "график", "диаграмм", "dashboard", "дашборд", "excel")
        )
        charts = [{"type": "bar", "value": val, "title": f"bar: {val or 'auto'}"}]
        if pretty:
            charts.append(
                {"type": "pie", "value": val, "title": f"pie: {val or 'auto'}"}
            )
    types_have = {str(c.get("type") or "") for c in charts}
    if (
        any(x in pl for x in ("красив", "dashboard", "дашборд", "с график"))
        and "pie" not in types_have
    ):
        charts.append({"type": "pie", "value": val, "title": f"pie: {val or 'auto'}"})
    return charts[:4]


def is_wizard_skip(phrase: str) -> bool:
    pl = _norm(phrase)
    return pl in (
        "нет",
        "не надо",
        "без",
        "без фильтров",
        "без фильтра",
        "нету",
        "-",
        "—",
        "пропусти",
        "skip",
        "none",
        "no",
        "далее",
        "дальше",
        "по умолчанию",
        "default",
    )


def is_wizard_cancel(phrase: str) -> bool:
    pl = _norm(phrase)
    return any(
        x in pl
        for x in (
            "отмена",
            "отменить",
            "cancel",
            "стоп мастер",
            "сброс мастера",
            "не надо отчёт",
            "забудь",
        )
    )


def is_wizard_confirm(phrase: str) -> bool:
    """«да», «да делай» — подтверждение, НЕ ответ object/period."""
    pl = _norm(phrase)
    if not pl:
        return False
    if pl in (
        "да",
        "ok",
        "ок",
        "yes",
        "строим",
        "делай",
        "сделай",
        "давай",
        "да делай",
        "да сделай",
        "давай делай",
        "да строим",
        "поехали",
        "подтверждаю",
        "go",
        "build",
        "строить",
        "выполни",
        "запускай",
        "согласен",
        "согласна",
        "готов",
        "готова",
    ):
        return True
    if re.match(
        r"^(да|ок|ok|yes)([\s,!.]+(делай|сделай|строим|давай|поехали|подтверждаю))?[\s,!.]*$",
        pl,
    ):
        return True
    if re.match(r"^(делай|сделай|давай|строим)[\s,!.]*$", pl):
        return True
    return False


def is_garbage_answer(phrase: str) -> bool:
    """Ответы, которые нельзя писать в object/period/metrics."""
    if not (phrase or "").strip():
        return True
    if is_wizard_confirm(phrase):
        return True
    pl = _norm(phrase)
    # слишком общие команды без смысла шага
    if pl in ("excel", "xlsx", "отчёт", "отчет", "диаграмма", "график"):
        return True
    return False


def looks_like_period(phrase: str) -> bool:
    pl = _norm(phrase)
    if not pl:
        return False
    if re.search(r"\b(?:19|20)\d{2}\b", pl):
        return True
    if re.search(
        r"\d{1,2}[./]\d{1,2}[./](?:\d{2}|\d{4})",
        pl,
    ):
        return True
    if any(
        x in pl
        for x in (
            "январ",
            "феврал",
            "март",
            "апрел",
            "май",
            "июн",
            "июл",
            "август",
            "сентябр",
            "октябр",
            "ноябр",
            "декабр",
            "квартал",
            "полугоди",
            "сегодня",
            "сейчас",
            "вчера",
            "недел",
            "месяц",
            "год",
            "на конец",
            "на дату",
            "за весь",
            "без периода",
        )
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# State / UI
# ---------------------------------------------------------------------------


def new_wizard_state(seed_request: str = "") -> dict[str, Any]:
    return {
        "active": True,
        "step_index": 0,
        "seed_request": (seed_request or "")[:400],
        "answers": {},
        "charts_spec": [],
        "created": True,
    }


def _clean_answers(answers: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (answers or {}).items():
        if v is None:
            continue
        s = str(v).strip()
        if not s or is_garbage_answer(s):
            continue
        out[k] = s
    return out


def wizard_progress_text(state: dict[str, Any]) -> str:
    answers = _clean_answers(state.get("answers") or {})
    lines = ["**Мастер Excel + диаграммы** (универсальный отчёт)", ""]
    lines.append("Уже выбрано:")
    labels = {
        "object": "Объект",
        "period": "Период",
        "dimensions": "Разрезы",
        "metrics": "Показатели",
        "filters": "Фильтры",
        "charts": "Диаграммы",
    }
    shown = 0
    for sid, title in labels.items():
        val = answers.get(sid)
        if not val:
            continue
        lines.append(f"  • {title}: {val}")
        shown += 1
    if shown == 0:
        lines.append("  • (пока пусто)")
    lines.append("")
    return "\n".join(lines)


def current_step(state: dict[str, Any]) -> dict[str, Any] | None:
    idx = int(state.get("step_index") or 0)
    if idx < 0 or idx >= len(WIZARD_STEPS):
        return None
    return WIZARD_STEPS[idx]


def _field_options_for_step(state: dict[str, Any], step_id: str) -> str:
    schema = state.get("schema") or {}
    if not schema:
        return ""
    try:
        from tools.universal_schema import format_field_options

        if step_id == "dimensions":
            opts = format_field_options(schema.get("dimensions") or [])
            return f"Доступные разрезы: {opts}" if opts else ""
        if step_id in ("metrics", "charts"):
            opts = format_field_options(schema.get("measures") or [])
            return f"Доступные показатели: {opts}" if opts else ""
    except Exception:
        pass
    return ""


def _summary_answers(state: dict[str, Any]) -> str:
    answers = _clean_answers(state.get("answers") or {})
    lines = []
    mapping = [
        ("object", "Источник данных"),
        ("period", "Период / срез"),
        ("dimensions", "Группировки (ось X)"),
        ("metrics", "Показатели (ось Y)"),
        ("filters", "Фильтры"),
        ("charts", "Диаграммы"),
    ]
    for key, title in mapping:
        val = answers.get(key) or "—"
        lines.append(f"{title}: **{val}**")
    cs = state.get("charts_spec") or []
    if cs:
        parts = []
        for c in cs:
            if isinstance(c, dict):
                parts.append(f"{c.get('type', '?')}({c.get('value', '')})")
        if parts:
            lines.append("Разобрано диаграмм: " + ", ".join(parts))
    return "\n".join(lines) + "\n"


def format_step_question(state: dict[str, Any]) -> str:
    step = current_step(state)
    head = wizard_progress_text(state)
    if step is None:
        fn = (
            (state.get("schema") or {}).get("full_name")
            or state.get("resolved_full_name")
            or state.get("last_full_name")
            or "—"
        )
        return (
            head
            + f"Объект metadata: **{fn}**\n"
            + "**Проверьте и подтвердите.**\n"
            + _summary_answers(state)
            + "\nОтветьте **«да»** / **«строим»** — соберу Excel с диаграммами.\n"
            "Правка: «период …», «разрезы …», «диаграммы bar,pie».\n"
            "**«отмена»** — выйти из мастера."
        )
    opts = _field_options_for_step(state, step["id"])
    q = str(step["question"]).replace("{field_options}", opts or "")
    if opts and "{field_options}" not in str(step["question"]):
        q = q + "\n" + opts
    ex = ", ".join(f"«{e}»" for e in (step.get("examples") or [])[:3])
    return (
        head
        + q
        + (f"\n\nПримеры: {ex}" if ex else "")
        + "\n\n_«отмена» — выйти; «-» / «без…» — пропуск (где уместно)._"
    )


def wizard_result_payload(
    *,
    message: str,
    state: dict[str, Any],
    need_clarification: bool = True,
    ready: bool = False,
) -> dict[str, Any]:
    return {
        "ok": True,
        "wizard": True,
        "need_clarification": bool(need_clarification),
        "wizard_ready": bool(ready),
        "about": "report_wizard",
        "question": message,
        "answer": message,
        "wizard_state": {
            "active": state.get("active", True),
            "step_index": state.get("step_index"),
            "answers": _clean_answers(state.get("answers") or {}),
            "charts_spec": state.get("charts_spec") or [],
            "resolved_full_name": state.get("resolved_full_name"),
            "last_full_name": state.get("last_full_name"),
            "last_period": state.get("last_period"),
        },
        "prefer": (
            "Мастер Excel. Покажи question КАК ЕСТЬ. "
            "НЕ строй excel пока wizard_ready=false. "
            "«да»/«да делай» = подтверждение, не object. "
            "Следующий ход: report_builder(wizard_answer=ответ)."
        ),
        "note": "wizard_step" if need_clarification else "wizard_ready",
    }


# ---------------------------------------------------------------------------
# Charts parse
# ---------------------------------------------------------------------------


def parse_charts_answer(text: str, *, default_metric: str = "") -> list[dict[str, Any]]:
    pl = _norm(text)
    default_m = (default_metric or "").strip()
    if not default_m:
        if "остат" in pl:
            default_m = "Остаток"
        elif "колич" in pl and "сумм" not in pl:
            default_m = "Количество"
        elif any(x in pl for x in ("сумм", "выруч", "sum")):
            default_m = "Сумма"

    if not pl or is_wizard_skip(text):
        return [
            {"type": "bar", "value": default_m, "title": f"bar: {default_m or 'auto'}"},
            {"type": "pie", "value": default_m, "title": f"pie: {default_m or 'auto'}"},
        ]

    type_aliases = {
        "bar": "bar",
        "col": "bar",
        "column": "bar",
        "столб": "bar",
        "гистограм": "bar",
        "line": "line",
        "лин": "line",
        "линейн": "line",
        "pie": "pie",
        "круг": "pie",
        "доли": "pie",
        "сектор": "pie",
    }

    charts: list[dict[str, Any]] = []
    for m in re.finditer(
        r"(bar|col|column|line|pie|столб\w*|лин\w*|круг\w*|доли|сектор\w*)"
        r"\s*[:\-–]?\s*"
        r"(сумм\w*|количеств\w*|остат\w*|дельта|цена|выручк\w*|count|sum|qty)?",
        pl,
        re.I,
    ):
        raw_t = m.group(1).lower()
        ctype = "bar"
        for k, v in type_aliases.items():
            if raw_t.startswith(k) or k in raw_t:
                ctype = v
                break
        val_raw = (m.group(2) or "").lower()
        value = default_m
        if any(x in val_raw for x in ("колич", "count", "qty")):
            value = "Количество"
        elif "дельта" in val_raw:
            value = "Дельта"
        elif "остат" in val_raw:
            value = "Остаток"
        elif "цен" in val_raw:
            value = "Цена"
        elif any(x in val_raw for x in ("сумм", "выруч", "sum")):
            value = "Сумма"
        charts.append(
            {
                "type": ctype,
                "value": value,
                "title": f"{ctype}: {value or 'auto'}",
            }
        )

    if charts:
        seen: set[tuple[str, str]] = set()
        out = []
        for c in charts:
            key = (c["type"], c["value"])
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
        return out[:6]

    found_types: list[str] = []
    for k, v in (
        ("столб", "bar"),
        ("bar", "bar"),
        ("гист", "bar"),
        ("лин", "line"),
        ("line", "line"),
        ("pie", "pie"),
        ("круг", "pie"),
        ("доли", "pie"),
    ):
        if k in pl and v not in found_types:
            found_types.append(v)
    if "три" in pl and not found_types:
        found_types = ["bar", "pie", "line"]
    if "два" in pl and len(found_types) < 2:
        found_types = found_types or ["bar", "pie"]
    if not found_types:
        pretty = any(
            x in pl
            for x in (
                "красив",
                "график",
                "диаграмм",
                "dashboard",
                "дашборд",
                "excel",
                "xlsx",
            )
        )
        if "круг" in pl or "pie" in pl:
            found_types = ["pie"]
        else:
            found_types = ["bar"]
            if pretty or " и " in pl or "," in pl:
                found_types.append("pie")

    return [
        {
            "type": t,
            "value": default_m,
            "title": f"{t}: {default_m or 'auto'}",
        }
        for t in found_types[:6]
    ]


# ---------------------------------------------------------------------------
# Defaults / readiness
# ---------------------------------------------------------------------------


def _enough_for_build(answers: dict[str, Any], state: dict[str, Any]) -> bool:
    """Можно ли собрать отчёт (есть источник + период, хотя бы из контекста/дефолтов)."""
    answers = _clean_answers(answers)
    obj = answers.get("object")
    per = answers.get("period")
    has_obj = bool(
        obj
        or state.get("last_full_name")
        or state.get("resolved_full_name")
        or state.get("seed_request")
    )
    has_period = bool(
        (per and looks_like_period(str(per)))
        or state.get("last_period")
        or re.search(
            r"\b(?:19|20)\d{2}\b",
            str(state.get("seed_request") or ""),
        )
    )
    # на экране подтверждения (все шаги пройдены) — достаточно object-контекста;
    # период подставим дефолтом
    if int(state.get("step_index") or 0) >= len(WIZARD_STEPS):
        return has_obj
    return has_obj and has_period


def _is_bad_metadata_name(fn: str) -> bool:
    """Обработки/отчёты не подходят как источник табличных данных продаж."""
    s = (fn or "").strip()
    if not s:
        return True
    return s.startswith("Обработка.") or s.startswith("Отчет.") or s.startswith("Отчёт.")


def _best_object_default(state: dict[str, Any]) -> str:
    seed_guess = _guess_object_from_seed(state.get("seed_request") or "")
    last = str(state.get("last_full_name") or "")
    resolved = str(state.get("resolved_full_name") or "")
    # last_report с нормальным документом/регистром — приоритет
    if last and not _is_bad_metadata_name(last) and not is_garbage_answer(last):
        return last
    if seed_guess:
        return seed_guess
    if resolved and not _is_bad_metadata_name(resolved) and not is_garbage_answer(resolved):
        return resolved
    seed = str(state.get("seed_request") or "").strip()
    return seed


def _fill_defaults(answers: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    answers = _clean_answers(answers)

    obj = answers.get("object")
    if not obj or is_garbage_answer(str(obj)) or _is_bad_metadata_name(str(obj)):
        answers["object"] = _best_object_default(state)
        # сбросить ошибочный resolved (ПрайсЛист и т.п.)
        if _is_bad_metadata_name(str(state.get("resolved_full_name") or "")):
            state.pop("resolved_full_name", None)
            state.pop("schema", None)

    if not answers.get("period") or not looks_like_period(str(answers.get("period"))):
        from tools.period_parse import default_period_years

        answers["period"] = (
            state.get("last_period")
            or _guess_period_from_seed(state.get("seed_request") or "")
            or default_period_years()
        )

    dims = answers.get("dimensions")
    if (
        not dims
        or is_garbage_answer(str(dims))
        or is_wizard_skip(str(dims))
        or _norm(str(dims)) in ("нету", "нет")
    ):
        answers["dimensions"] = state.get("last_group_by") or ""
    else:
        # «по складу» / «по контрагентам» → каноническое имя
        d = _norm(str(dims))
        dim_map = (
            (("склад",), "склад"),
            (("контрагент", "клиент", "покупат"), "контрагент"),
            (("номенклатур", "товар"), "номенклатура"),
            (("организац",), "организация"),
            (("подраздел",), "подразделение"),
            (("менеджер", "ответственн"), "менеджер"),
        )
        mapped = None
        for keys, canon in dim_map:
            if any(k in d for k in keys):
                mapped = canon
                break
        if mapped:
            answers["dimensions"] = mapped
        else:
            m = re.match(r"^по\s+(.+)$", str(dims).strip(), re.I)
            if m:
                answers["dimensions"] = m.group(1).strip()

    met = answers.get("metrics")
    if not met or is_garbage_answer(str(met)):
        answers["metrics"] = (
            str(state.get("last_value_field") or "").strip() or "как в данных"
        )
    else:
        # «номенклатура цена количество» — вытащить метрики, остальное в dims
        ml = _norm(str(met))
        parts = []
        if "сумм" in ml:
            parts.append("сумма")
        if "колич" in ml:
            parts.append("количество")
        if "цен" in ml:
            parts.append("цена")
        if "остат" in ml:
            parts.append("остаток")
        if parts:
            answers["metrics"] = " и ".join(parts)
        # если в metrics затесалась номенклатура — и dims пустой/дефолт
        if "номенклатур" in ml and "номенклатур" not in _norm(
            str(answers.get("dimensions") or "")
        ):
            if answers.get("dimensions") in (None, "", "контрагент"):
                answers["dimensions"] = "номенклатура"

    if not answers.get("filters") or is_wizard_skip(str(answers.get("filters") or "")):
        answers["filters"] = "без фильтров"

    if not answers.get("charts") or is_garbage_answer(str(answers.get("charts") or "")):
        answers["charts"] = "столбцы и pie"

    def_m = ""
    met_n = _norm(str(answers.get("metrics") or ""))
    if "остат" in met_n:
        def_m = "Остаток"
    elif "колич" in met_n and "сумм" not in met_n:
        def_m = "Количество"
    elif "сумм" in met_n:
        def_m = "Сумма"
    if not state.get("charts_spec"):
        state["charts_spec"] = parse_charts_answer(
            str(answers.get("charts") or "столбцы и pie"),
            default_metric=def_m,
        )
    else:
        # пересобрать если charts текст есть и spec пустой/один bar по умолчанию
        ch_text = str(answers.get("charts") or "")
        if ch_text and ("круг" in _norm(ch_text) or "pie" in _norm(ch_text)):
            state["charts_spec"] = parse_charts_answer(ch_text, default_metric=def_m)

    return answers

def _guess_object_from_seed(seed: str) -> str:
    """Стемы поиска (не full_name CF) — resolve подставит объект текущей базы."""
    from tools.business_stems import (
        DEBT_SEARCH,
        PURCHASE_SEARCH,
        SALES_SEARCH,
        STOCK_SEARCH,
    )

    pl = _norm(seed)
    for key, label in (
        ("остатк", STOCK_SEARCH),
        ("продаж", SALES_SEARCH),
        ("реализ", SALES_SEARCH),
        ("поступлен", PURCHASE_SEARCH),
        ("закуп", PURCHASE_SEARCH),
        ("задолжен", DEBT_SEARCH),
    ):
        if key in pl:
            return label
    return ""


def _guess_period_from_seed(seed: str) -> str:
    try:
        from tools.report_intent import extract_period_from_text

        per = extract_period_from_text(seed or "")
        if per:
            return per
    except Exception:
        pass
    m = re.search(r"\b((?:19|20)\d{2}(?:\s*и\s*(?:19|20)\d{2})?)\b", seed or "")
    if m:
        return m.group(1)
    return ""


def _try_correction(
    text: str, answers: dict[str, Any]
) -> tuple[dict[str, Any], list | None] | None:
    """Правка на экране подтверждения: «период 2016», «разрезы склад»…"""
    answers = dict(answers)
    charts = None

    m = re.match(
        r"^(объект|источник|тема)\s*[:\-]?\s+(.+)$",
        text.strip(),
        re.I,
    )
    if m and not is_garbage_answer(m.group(2)):
        answers["object"] = m.group(2).strip()
        return answers, charts

    m = re.match(
        r"^(период|даты|за период)\s*[:\-]?\s+(.+)$",
        text.strip(),
        re.I,
    )
    if m:
        answers["period"] = m.group(2).strip()
        return answers, charts

    m = re.match(
        r"^(разрез\w*|групп\w*|по полям|ось\s*x)\s*[:\-]?\s+(.+)$",
        text.strip(),
        re.I,
    )
    if m:
        answers["dimensions"] = m.group(2).strip()
        return answers, charts

    m = re.match(
        r"^(показател\w*|метрик\w*|ось\s*y)\s*[:\-]?\s+(.+)$",
        text.strip(),
        re.I,
    )
    if m:
        answers["metrics"] = m.group(2).strip()
        return answers, charts

    m = re.match(r"^(фильтр\w*)\s*[:\-]?\s+(.+)$", text.strip(), re.I)
    if m:
        answers["filters"] = m.group(2).strip()
        return answers, charts

    m = re.match(
        r"^(диаграмм\w*|график\w*|charts?)\s*[:\-]?\s+(.+)$",
        text.strip(),
        re.I,
    )
    if m:
        answers["charts"] = m.group(2).strip()
        charts = parse_charts_answer(m.group(2).strip())
        return answers, charts

    # короткий период «2015 и 2016» на confirm-экране
    if looks_like_period(text) and len(text) < 60:
        answers["period"] = text.strip()
        return answers, charts

    return None


# ---------------------------------------------------------------------------
# Apply answer / start
# ---------------------------------------------------------------------------


def apply_answer(state: dict[str, Any], user_text: str) -> dict[str, Any]:
    """Применить ответ. «да»/«да делай» никогда не пишутся в object/period."""
    state = deepcopy(state) if state else new_wizard_state()
    text = (user_text or "").strip()

    if is_wizard_cancel(text):
        state["active"] = False
        return {
            "state": state,
            "cancel": True,
            "message": "Мастер отменён. Можно начать снова: «excel с диаграммами».",
            "ready_to_build": False,
            "done_questioning": False,
        }

    idx = int(state.get("step_index") or 0)
    answers: dict[str, Any] = _clean_answers(state.get("answers") or {})
    state["answers"] = answers

    # --- подтверждение ---
    if is_wizard_confirm(text):
        # Confirm-экран, достаточный контекст, или уже много ответов → build
        n_ans = len(answers)
        can = (
            idx >= len(WIZARD_STEPS)
            or _enough_for_build(answers, state)
            or (n_ans >= 3 and (answers.get("object") or state.get("last_full_name") or state.get("seed_request")))
            or bool(state.get("from_last_report") and (state.get("last_full_name") or state.get("last_period")))
        )
        if can:
            answers = _fill_defaults(answers, state)
            state["answers"] = answers
            state["step_index"] = len(WIZARD_STEPS)
            return {
                "state": state,
                "ready_to_build": True,
                "done_questioning": True,
                "message": "Собираю Excel с диаграммами…",
                "cancel": False,
            }
        # Недостаточно — НЕ писать «да» в answers, спросить текущий шаг
        state["answers"] = answers
        return {
            "state": state,
            "message": (
                "«да» понял, но не хватает **объекта** или **периода**.\n"
                "Укажите, например: «продажи» и «прошлый год».\n\n"
                + format_step_question(state)
            ),
            "ready_to_build": False,
            "done_questioning": False,
            "cancel": False,
        }

    # confirmation phase — правки параметров
    if idx >= len(WIZARD_STEPS):
        corr = _try_correction(text, answers)
        if corr:
            answers, ch = corr[0], corr[1]
            state["answers"] = _clean_answers(answers)
            if ch is not None:
                state["charts_spec"] = ch
            # schema может устареть при смене object
            if "object" in (corr[0] or {}):
                state.pop("schema", None)
                state.pop("resolved_full_name", None)
            return {
                "state": state,
                "message": format_step_question(state),
                "ready_to_build": False,
                "done_questioning": True,
                "cancel": False,
            }
        return {
            "state": state,
            "message": (
                "Нужно **«да»** / **«строим»**, или правка: "
                "«период прошлый год», «разрезы контрагент», «диаграммы bar,pie».\n"
                + format_step_question(state)
            ),
            "ready_to_build": False,
            "done_questioning": True,
            "cancel": False,
        }

    step = WIZARD_STEPS[idx]
    sid = step["id"]

    # не записывать confirm/мусор
    if is_garbage_answer(text):
        state["answers"] = answers
        return {
            "state": state,
            "message": (
                f"«{text}» — это не ответ на шаг «{step['title']}».\n"
                + format_step_question(state)
            ),
            "ready_to_build": False,
            "done_questioning": False,
            "cancel": False,
        }

    # Умный роутинг: период на шаге object, dim-слова на metrics и т.п.
    if sid == "object" and looks_like_period(text) and not _guess_object_from_seed(text):
        answers["period"] = text.strip()
        state["answers"] = answers
        # object ещё нужен
        return {
            "state": state,
            "message": (
                "Период запомнил: **"
                + text.strip()
                + "**. Теперь укажите **объект** (продажи / поступления…).\n"
                + format_step_question(state)
            ),
            "ready_to_build": False,
            "done_questioning": False,
            "cancel": False,
        }
    if sid == "period" and not looks_like_period(text) and _guess_object_from_seed(text):
        # «продажи» на шаге периода — записать object, спросить период снова
        if not answers.get("object"):
            answers["object"] = _guess_object_from_seed(text) or text
            state.pop("schema", None)
            state.pop("resolved_full_name", None)
        state["answers"] = answers
        return {
            "state": state,
            "message": (
                f"Объект: **{answers['object']}**. Укажите **период** (год/даты).\n"
                + format_step_question(state)
            ),
            "ready_to_build": False,
            "done_questioning": False,
            "cancel": False,
        }

    if sid == "filters" and is_wizard_skip(text):
        answers[sid] = "без фильтров"
    elif sid == "charts":
        answers[sid] = text
        def_m = ""
        measures = (state.get("schema") or {}).get("measure_names") or []
        if measures:
            def_m = str(measures[0])
        met = _norm(str(answers.get("metrics") or ""))
        if "остат" in met:
            def_m = "Остаток"
        elif "колич" in met and "сумм" not in met:
            def_m = "Количество"
        # «выводи количество круг» → pie + количество
        if "колич" in _norm(text) and "сумм" not in _norm(text):
            def_m = "Количество"
        state["charts_spec"] = parse_charts_answer(text, default_metric=def_m)
    elif sid == "period":
        if is_wizard_skip(text) or _norm(text) in ("без периода", "нет периода"):
            answers[sid] = "без периода"
        elif looks_like_period(text) or len(text) >= 2:
            answers[sid] = text
        else:
            state["answers"] = answers
            return {
                "state": state,
                "message": (
                    "Нужен период (год/даты). Пример: «прошлый год» или «этот и прошлый год».\n"
                    + format_step_question(state)
                ),
                "ready_to_build": False,
                "done_questioning": False,
                "cancel": False,
            }
    elif sid == "object":
        answers[sid] = text
        state.pop("schema", None)
        state.pop("resolved_full_name", None)
    elif sid == "dimensions" and is_wizard_skip(text):
        answers[sid] = ""
    else:
        answers[sid] = text

    state["answers"] = answers
    state["step_index"] = idx + 1

    if state["step_index"] >= len(WIZARD_STEPS):
        return {
            "state": state,
            "message": format_step_question(state),
            "ready_to_build": False,
            "done_questioning": True,
            "cancel": False,
        }

    return {
        "state": state,
        "message": format_step_question(state),
        "ready_to_build": False,
        "done_questioning": False,
        "cancel": False,
    }


def prefill_wizard_from_context(
    state: dict[str, Any],
    *,
    last_report: dict[str, Any] | None = None,
    session: Any = None,
) -> dict[str, Any]:
    """Дозаполнить из last_report / session; вычистить «да» из answers."""
    if not state:
        return state
    answers = _clean_answers(state.get("answers") or {})
    lr = last_report or {}
    if lr:
        state["from_last_report"] = True
        if lr.get("full_name"):
            state["last_full_name"] = str(lr["full_name"])
            if not answers.get("object"):
                answers["object"] = str(lr["full_name"])
        if lr.get("period_spec"):
            state["last_period"] = str(lr["period_spec"])
            if not answers.get("period"):
                answers["period"] = str(lr["period_spec"])
        if lr.get("periods"):
            labs = []
            for p in lr["periods"]:
                if isinstance(p, dict) and p.get("label"):
                    labs.append(str(p["label"]))
                elif p and not isinstance(p, dict):
                    labs.append(str(p))
            if labs:
                state["last_period"] = " и ".join(labs)
                if not answers.get("period"):
                    answers["period"] = state["last_period"]
        if lr.get("group_by"):
            state["last_group_by"] = str(lr["group_by"])
            if not answers.get("dimensions"):
                answers["dimensions"] = str(lr["group_by"])
        if lr.get("value_field") and not answers.get("metrics"):
            answers["metrics"] = str(lr["value_field"])
        if lr.get("value_field"):
            state["last_value_field"] = str(lr["value_field"])

    if session is not None:
        fn = getattr(session, "full_name", None) or (
            session.get("full_name") if isinstance(session, dict) else None
        )
        per = getattr(session, "period_spec", None) or (
            session.get("period_spec") if isinstance(session, dict) else None
        )
        gb = getattr(session, "group_by", None) or (
            session.get("group_by") if isinstance(session, dict) else None
        )
        if fn and not answers.get("object"):
            answers["object"] = str(fn)
            state["last_full_name"] = str(fn)
        if per and not answers.get("period"):
            answers["period"] = str(per)
            state["last_period"] = str(per)
        if gb and not answers.get("dimensions"):
            answers["dimensions"] = str(gb)

    state["answers"] = answers
    for i, step in enumerate(WIZARD_STEPS):
        if step["id"] not in answers or not answers[step["id"]]:
            state["step_index"] = i
            break
    else:
        state["step_index"] = len(WIZARD_STEPS)
    return state


def start_wizard(
    seed_request: str = "",
    *,
    last_report: dict[str, Any] | None = None,
    session: Any = None,
) -> dict[str, Any]:
    """Старт мастера с pre-fill из seed + last_report."""
    state = new_wizard_state(seed_request)
    seed = seed_request or ""
    pl = _norm(seed)
    answers: dict[str, Any] = {}

    lr = last_report or {}
    if lr:
        state["from_last_report"] = True
        if lr.get("full_name"):
            state["last_full_name"] = str(lr["full_name"])
            answers["object"] = str(lr["full_name"])
        if lr.get("period_spec"):
            state["last_period"] = str(lr["period_spec"])
            answers["period"] = str(lr["period_spec"])
        if lr.get("periods"):
            labs = []
            for p in lr["periods"]:
                if isinstance(p, dict) and p.get("label"):
                    labs.append(str(p["label"]))
                elif p and not isinstance(p, dict):
                    labs.append(str(p))
            if labs:
                state["last_period"] = " и ".join(labs)
                answers["period"] = state["last_period"]
        if lr.get("group_by"):
            state["last_group_by"] = str(lr["group_by"])
            answers["dimensions"] = str(lr["group_by"])
        if lr.get("value_field"):
            answers["metrics"] = str(lr["value_field"])
            state["last_value_field"] = str(lr["value_field"])

    if session is not None:
        fn = getattr(session, "full_name", None) or (
            session.get("full_name") if isinstance(session, dict) else None
        )
        per = getattr(session, "period_spec", None) or (
            session.get("period_spec") if isinstance(session, dict) else None
        )
        gb = getattr(session, "group_by", None) or (
            session.get("group_by") if isinstance(session, dict) else None
        )
        if fn and not answers.get("object"):
            answers["object"] = str(fn)
            state["last_full_name"] = str(fn)
        if per and not answers.get("period"):
            answers["period"] = str(per)
            state["last_period"] = str(per)
        if gb and not answers.get("dimensions"):
            answers["dimensions"] = str(gb)

    per = _guess_period_from_seed(seed)
    if per and not answers.get("period"):
        answers["period"] = per

    obj = _guess_object_from_seed(seed)
    if obj and not answers.get("object"):
        answers["object"] = obj

    dims = []
    if any(x in pl for x in ("контрагент", "клиент", "покупат")):
        dims.append("контрагент")
    if any(x in pl for x in ("номенклатур", "товар")):
        dims.append("номенклатура")
    if "склад" in pl:
        dims.append("склад")
    if dims and not answers.get("dimensions"):
        answers["dimensions"] = ", ".join(dims)

    if any(x in pl for x in ("сумм", "выручк")) and not answers.get("metrics"):
        answers["metrics"] = "сумма и количество" if "колич" in pl else "сумма"
    if "колич" in pl and not answers.get("metrics"):
        answers["metrics"] = "количество"
    if "цен" in pl and not answers.get("metrics"):
        answers["metrics"] = "сумма, цена"

    m = re.search(r"топ[-\s]?(\d{1,3})", pl)
    filt_parts = []
    if m:
        filt_parts.append(f"топ {m.group(1)}")
    m2 = re.search(r"только\s+([^\n,]{2,40})", seed, re.I)
    if m2:
        filt_parts.append(f"только {m2.group(1).strip()}")
    if filt_parts and not answers.get("filters"):
        answers["filters"] = ", ".join(filt_parts)

    if any(x in pl for x in ("столб", "pie", "line", "круг", "лин", "bar")):
        answers["charts"] = seed
        state["charts_spec"] = parse_charts_answer(seed)

    answers = _clean_answers(answers)
    # дефолты при богатом last_report — не гонять 6 шагов
    if last_report and (
        last_report.get("full_name") or last_report.get("rows")
    ):
        if not answers.get("dimensions"):
            answers["dimensions"] = str(last_report.get("group_by") or "")
        if not answers.get("metrics"):
            answers["metrics"] = str(
                last_report.get("value_field")
                or last_report.get("metrics")
                or "как в данных"
            )
        if not answers.get("filters"):
            answers["filters"] = "без фильтров"
        if not answers.get("charts"):
            answers["charts"] = "столбцы и pie"
            state["charts_spec"] = parse_charts_answer(answers["charts"])
        if not answers.get("period"):
            from tools.period_parse import default_period_years

            answers["period"] = str(
                last_report.get("period_spec")
                or state.get("last_period")
                or default_period_years()
            )
        if not answers.get("object"):
            answers["object"] = str(
                last_report.get("full_name")
                or _guess_object_from_seed(seed)
                or seed
            )

    state["answers"] = answers
    for i, step in enumerate(WIZARD_STEPS):
        if step["id"] not in answers or not answers[step["id"]]:
            state["step_index"] = i
            break
    else:
        state["step_index"] = len(WIZARD_STEPS)

    from_ctx = bool(last_report and last_report.get("full_name"))
    if from_ctx and state["step_index"] >= len(WIZARD_STEPS):
        msg = (
            "Уже есть **прошлый отчёт** в сессии — параметры подставил, "
            "мастер **не** спрашивает всё заново.\n"
            f"Объект: **{answers.get('object')}**, "
            f"период: **{answers.get('period')}**, "
            f"разрез: **{answers.get('dimensions')}**.\n\n"
            + format_step_question(state)
            + "\nОтветьте **«да»** — сразу Excel. "
            "Правка: «период прошлый год», «разрезы склад»."
        )
    elif from_ctx:
        msg = (
            "Запускаю мастер, но **учтён прошлый отчёт** "
            f"({last_report.get('full_name')}). "
            "Часть шагов уже заполнена — можно **«да делай»**.\n\n"
            + format_step_question(state)
        )
    else:
        msg = (
            "Запускаю **мастер Excel с диаграммами**.\n"
            "Шаги: объект → даты → поля → показатели → фильтры → типы диаграмм "
            "(можно **несколько**). Потом **«да»** — файл.\n"
            "Если контекст уже был — шаги частично заполнены; можно **«да делай»**.\n\n"
            + format_step_question(state)
        )
    return {
        "ok": True,
        "wizard": True,
        "need_clarification": True,
        "wizard_ready": False,
        "about": "report_wizard",
        "question": msg,
        "wizard_state": state,
        "prefer": (
            "Это МАСТЕР отчёта. Покажи question. "
            "НЕ строй excel пока wizard_ready=false. "
            "Если в question написано «прошлый отчёт» — не переспрашивай объект. "
            "«да»/«да делай» = подтверждение, не object. "
            "Следующий ход: report_builder(wizard_answer=ответ_пользователя)."
        ),
        "note": "wizard_started_from_context" if from_ctx else "wizard_started",
    }


def answers_to_builder_kwargs(state: dict[str, Any]) -> dict[str, Any]:
    """Преобразовать answers мастера → kwargs для report_builder execute."""
    answers = _fill_defaults(_clean_answers(state.get("answers") or {}), state)
    obj = str(
        answers.get("object")
        or state.get("seed_request")
        or state.get("last_full_name")
        or ""
    )
    from tools.period_parse import default_period_years

    period = str(answers.get("period") or default_period_years())
    dims = str(answers.get("dimensions") or state.get("last_group_by") or "")
    metrics = str(answers.get("metrics") or "")
    filters = str(answers.get("filters") or "")
    charts_raw = str(answers.get("charts") or "столбцы и pie")

    # full_name if looks like metadata path (не Обработка.*)
    full_name = ""
    resolved = state.get("resolved_full_name") or state.get("last_full_name") or ""
    if (
        resolved
        and not is_garbage_answer(str(resolved))
        and not _is_bad_metadata_name(str(resolved))
    ):
        full_name = str(resolved)
    elif re.match(r"^(Документ|Справочник|Регистр\w*)\.", obj):
        full_name = obj
    elif _is_bad_metadata_name(obj):
        obj = _best_object_default(state)
        answers["object"] = obj
        full_name = ""

    # top_n from filters
    top_n = 0
    m = re.search(r"топ[-\s]?(\d{1,3})", _norm(filters))
    if m:
        top_n = int(m.group(1))

    filter_text = ""
    if filters and not is_wizard_skip(filters) and "без фильтр" not in _norm(filters):
        # «только X» / free text — leave for builder filter
        filter_text = filters
        if top_n and re.fullmatch(r"топ[-\s]?\d{1,3}", _norm(filters)):
            filter_text = ""

    def_m = ""
    met = _norm(metrics)
    if "остат" in met:
        def_m = "Остаток"
    elif "колич" in met and "сумм" not in met:
        def_m = "Количество"
    elif "сумм" in met:
        def_m = "Сумма"

    charts_spec = state.get("charts_spec") or parse_charts_answer(
        charts_raw, default_metric=def_m
    )
    if not charts_spec:
        charts_spec = parse_charts_answer(charts_raw, default_metric=def_m)

    # value_field hint — только если пользователь явно сказал метрику
    value_field = ""
    if "колич" in met and "сумм" not in met:
        value_field = "Количество"
    elif "остат" in met:
        value_field = "Остаток"
    elif "сумм" in met:
        value_field = "Сумма"

    request_parts = [obj]
    if period:
        request_parts.append(f"за {period}")
    if dims and "без группир" not in _norm(dims):
        request_parts.append(f"по {dims}")
    if metrics and "как в данных" not in _norm(metrics):
        request_parts.append(metrics)
    if filter_text:
        request_parts.append(filter_text)
    request = " ".join(p for p in request_parts if p).strip()

    safe_name = re.sub(r"[^\w\-]+", "_", _norm(obj)[:30], flags=re.U).strip("_") or "report"
    return {
        "request": request,
        "full_name": full_name,
        "period": period,
        "group_by": dims if "без группир" not in _norm(dims) else "",
        "metrics": metrics,
        "value_field": value_field,
        "filter_text": filter_text,
        "top_n": top_n,
        "to_excel": True,
        "filename": f"wizard_{safe_name}.xlsx",
        "title": f"Отчёт: {obj}",
        "charts_spec": charts_spec,
        "wizard_answers": answers,
    }
