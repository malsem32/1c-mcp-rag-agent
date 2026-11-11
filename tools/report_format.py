# -*- coding: utf-8 -*-
"""Единый формат ответа отчёта для менеджера (любая CF)."""

from __future__ import annotations

import re
from typing import Any


def is_manager_report_question(phrase: str) -> bool:
    """
    Фраза про данные/отчёт (не код, не права, не структура).

    Универсально: маркеры ищем только в «речи» (PascalCase вырезан),
    чтобы «ЗаполнитьУсловияПродаж» не давало ложный «продаж» → build_report.
    Без доменных исключений «если заполн то не отчёт».
    """
    raw = (phrase or "").strip()
    if not raw:
        return False
    try:
        from tools.phrase_struct import (
            is_code_intent,
            marker_in_speech,
            speech_norm,
        )
    except Exception:
        pl = raw.lower().replace("ё", "е")
        speech = pl
        is_code_intent = lambda _t: False  # noqa: E731
        marker_in_speech = lambda s, ms: any(m in s for m in ms)  # noqa: E731
        speech_norm = lambda t: (t or "").lower().replace("ё", "е")  # noqa: E731

    # код / логика / процедура — не data-отчёт
    if is_code_intent(raw):
        return False

    speech = speech_norm(raw)
    pl = raw.lower().replace("ё", "е")  # полная фраза — только для meta/code списков

    # аудит кода / антипаттерны — НЕ build_report (даже при слове «реализац» в фразе)
    try:
        from tools.query_antipatterns import is_query_audit_question

        if is_query_audit_question(raw):
            return False
    except Exception:
        pass
    if any(
        x in pl
        for x in (
            "антипаттерн",
            "аудит запрос",
            "аудит код",
            "code review",
            "ревью запрос",
            "скан код",
            "инъекц",
        )
    ):
        return False

    # meta / structure / forms / print — НЕ data (по полной фразе ок)
    if any(
        x in pl
        for x in (
            "реквизит",
            "структур",
            "атрибут",
            "табличн",
            "какие поля",
            "все поля",
            "все реквиз",
            "какие есть документ",
            "какие документ",
            "документы про ",
            "найди документ",
            "макет",
            "печатн",
            "форм",
            "событ",
            "элемент",
            "какие макет",
            "какие форм",
            "что выводит",
            "layout",
            "template",
            "пф_mxl",
            "команд объект",
            "команды документ",
        )
    ) and not marker_in_speech(
        speech, ("сколько", "сумм", "excel", "выручк", "за 20", "за год", "в excel")
    ):
        return False

    # code-ish по речи
    if marker_in_speech(
        speech,
        (
            "в коде",
            "процедур",
            "модул",
            "обработкапроведения",
            "трассир",
            "кто вызыв",
            "оптимиз",
            "запрос в цикл",
            "не заполнил",
            "не заполнен",
            "как заполняется",
            "логик",
            "почему не ",
            "блокировк",
            "управляем",
            "как написать",
            "синтаксис",
        ),
    ):
        return False

    # data-маркеры — ТОЛЬКО в речи (не внутри PascalCase)
    data_markers = (
        "сколько",
        "сумм",
        "топ",
        "продаж",
        "реализ",
        "поступлен",
        "список",
        "отчет",
        "отчёт",
        "выручк",
        "остатк",
        "осталось",
        "остались",
        "на склад",
        "задолжен",
        "неоплач",
        "долг",
        "по контрагент",
        "по клиент",
        "за 20",
        "за год",
        "excel",
        "выгруз",
        "сравни",
        "оборот",
        "количество документ",
        "покажи продаж",
        "покажи реализ",
    )
    if marker_in_speech(speech, data_markers):
        return True
    if marker_in_speech(speech, ("покажи", "выведи", "нужен")) and marker_in_speech(
        speech, ("за ", "20", "месяц", "год", "контрагент", "товар")
    ):
        return True
    return False


def wants_period_compare(phrase: str) -> bool:
    try:
        from tools.phrase_struct import speech_norm

        pl = speech_norm(phrase)
    except Exception:
        pl = (phrase or "").lower().replace("ё", "е")
    if any(x in pl for x in ("сравни", "сравнен", "versus", "vs ")):
        # «сравни заказанное и отгруженное» — сравнение источников, не периодов:
        # нужно ≥2 периода или относительный период
        n_years = len(set(re.findall(r"\b((?:19|20)\d{2})\b", pl)))
        n_months = len(set(re.findall(
            r"(январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр|квартал)", pl
        )))
        rel = re.search(r"прошл|предыдущ|позапрошл|текущ|этот|этим|к\s+прошлому|динамик|год\s+к\s+год|месяц\s+к\s+месяц|период", pl)
        return bool(n_years >= 2 or n_months >= 2 or rel)
    if any(
        x in pl
        for x in (
            "этот месяц",
            "прошлый месяц",
            "к прошлому",
            "динамика",
            "два период",
            "оба период",
            "месяц к месяц",
            "год к год",
        )
    ):
        return True
    # два+ года в речи: «продажи 2015 и 2016» (не внутри PascalCase)
    years = re.findall(r"\b((?:19|20)\d{2})\b", pl)
    if len(set(years)) >= 2 and any(
        x in pl for x in ("продаж", "реализ", "поступлен", "сумм", "выручк", "оборот", "за ")
    ):
        return True
    return False


def balance_period_rows(
    rows: list[dict[str, Any]],
    period_labels: list[str],
    *,
    per_period_cap: int = 8,
    total_cap: int = 24,
    want_sum: bool = True,
    want_cnt: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """
    Итоги по каждому периоду + чередование detail-строк.

    Нужно, чтобы при compact_json (~25 rows / ~6k) оба года/месяца
    оставались в контексте модели, а не только первый период.
    """
    labels = [str(x) for x in period_labels if str(x).strip()]
    by_p: dict[str, list[dict[str, Any]]] = {lab: [] for lab in labels}
    orphans: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        lab = str(r.get("Период") or r.get("period") or "").strip()
        if lab in by_p:
            by_p[lab].append(r)
        elif lab:
            by_p.setdefault(lab, []).append(r)
            if lab not in labels:
                labels.append(lab)
        else:
            orphans.append(r)

    summary: list[dict[str, Any]] = []
    for lab in labels:
        rs = by_p.get(lab) or []
        s_sum = 0.0
        s_cnt = 0.0
        has_sum = False
        has_cnt = False
        for r in rs:
            if "error" in r and r.get("error"):
                continue
            if "Сумма" in r or any("сумм" in str(k).lower() for k in r):
                try:
                    sv = r.get("Сумма")
                    if sv is None:
                        for k, v in r.items():
                            if "сумм" in str(k).lower():
                                sv = v
                                break
                    s_sum += float(sv or 0)
                    has_sum = True
                except (TypeError, ValueError):
                    pass
            if "Количество" in r:
                try:
                    s_cnt += float(r.get("Количество") or 0)
                    has_cnt = True
                except (TypeError, ValueError):
                    pass
        item: dict[str, Any] = {
            "Период": lab,
            "groups": len(rs),
        }
        if has_sum or want_sum:
            item["Сумма"] = s_sum
        if has_cnt or want_cnt:
            item["Количество"] = int(s_cnt) if s_cnt == int(s_cnt) else s_cnt
        summary.append(item)

    # топ уже отсортирован СУММА УБЫВ внутри периода — берём голову
    n_periods = max(1, len(labels))
    cap_each = max(2, min(per_period_cap, total_cap // n_periods))
    interleaved: list[dict[str, Any]] = []
    idxs = {lab: 0 for lab in labels}
    while len(interleaved) < total_cap:
        progressed = False
        for lab in labels:
            rs = by_p.get(lab) or []
            i = idxs[lab]
            if i >= cap_each or i >= len(rs):
                continue
            interleaved.append(rs[i])
            idxs[lab] = i + 1
            progressed = True
            if len(interleaved) >= total_cap:
                break
        if not progressed:
            break
    for r in orphans:
        if len(interleaved) >= total_cap:
            break
        interleaved.append(r)

    meta = {
        "rows_total_raw": len(rows),
        "rows_shown": len(interleaved),
        "per_period_cap": cap_each,
        "periods_in_rows": sorted(
            {str(r.get("Период") or "") for r in interleaved if isinstance(r, dict)}
            - {""}
        ),
    }
    return summary, interleaved, meta


def _fmt_cell(v: Any, *, max_cell: int = 40, numeric_hint: bool = False) -> str:
    """Красивое значение ячейки для markdown (числа с разделителями)."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Да" if v else "Нет"
    if not numeric_hint and isinstance(v, (int, float)) and float(v).is_integer():
        return str(int(v))  # Период/Год/Код — без разделителей и «.00»
    if isinstance(v, int) and not isinstance(v, bool):
        return f"{v:,}".replace(",", " ")
    if isinstance(v, float):
        if abs(v) >= 1000:
            return f"{v:,.2f}".replace(",", " ")
        return f"{v:.2f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))
    s = str(v).strip()
    # «2016», «000123», «12» в нечисловой колонке (Период/Год/Код/Номер) — идентификатор, не деньги
    if not numeric_hint and re.fullmatch(r"-?\d+", s.replace("\xa0", "")):
        return s
    # число в строке
    if numeric_hint or re.match(r"^-?\d+([.,]\d+)?$", s.replace(" ", "").replace("\xa0", "")):
        try:
            f = float(s.replace("\xa0", "").replace(" ", "").replace(",", "."))
            if abs(f) >= 1000:
                return f"{f:,.2f}".replace(",", " ")
            return f"{f:g}"
        except ValueError:
            pass
    s = s.replace("|", "/").replace("\n", " ")
    if len(s) > max_cell:
        s = s[: max_cell - 1] + "…"
    return s


def rows_to_markdown_table(
    rows: list[Any],
    *,
    max_rows: int = 15,
    max_cols: int = 8,
    max_cell: int = 40,
    with_totals: bool = True,
    chat_slim: bool = True,
) -> str:
    """Компактная markdown-таблица для чата (реальные числа, опционально Итого)."""
    if not rows:
        return ""
    dict_rows = [r for r in rows if isinstance(r, dict)]
    if not dict_rows:
        return ""
    # в чате не тащим ДатаНачала/ДатаКонца/Год/Месяц — место для контрагентов
    _chat_skip = {
        "датаначала",
        "датаконца",
        "datanachala",
        "datakonca",
        "год",
        "месяц",
        "year",
        "month",
    }
    keys: list[str] = []
    for r in dict_rows[:5]:
        for k in r.keys():
            if str(k).startswith("_"):
                continue
            if chat_slim and str(k).lower().replace("ё", "е") in _chat_skip:
                continue
            if k not in keys:
                keys.append(str(k))
    prefer_order = (
        "контрагент",
        "номенклатура",
        "товар",
        "склад",
        "период",
        "характеристика",
        "вналичии",
        "количество",
        "остаток",
        "котгрузке",
        "сумма",
        "выручка",
        "цена",
    )

    def _rank(k: str) -> tuple[int, str]:
        kl = k.lower().replace("ё", "е")
        for i, p in enumerate(prefer_order):
            if p in kl:
                return (i, kl)
        return (50, kl)

    keys = sorted(keys, key=_rank)[:max_cols]
    if not keys:
        return ""

    num_keys = {
        k
        for k in keys
        if any(
            x in k.lower()
            for x in ("сумм", "кол", "остат", "цена", "выруч", "%", "доля", "дельта", "qty", "средн", "маржа", "прибыл", "себестоим")
        )
    }

    lines = [
        "| " + " | ".join(keys) + " |",
        "| " + " | ".join("---:" if k in num_keys else "---" for k in keys) + " |",
    ]
    shown = 0
    totals: dict[str, float] = {k: 0.0 for k in num_keys}
    total_n: dict[str, int] = {k: 0 for k in num_keys}
    for r in dict_rows:
        if shown >= max_rows:
            break
        cells = []
        for k in keys:
            v = r.get(k)
            s = _fmt_cell(v, max_cell=max_cell, numeric_hint=k in num_keys)
            cells.append(s)
            if k in num_keys and v is not None:
                try:
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        totals[k] += float(v)
                        total_n[k] += 1
                    else:
                        fv = float(
                            str(v).replace("\xa0", "").replace(" ", "").replace(",", ".")
                        )
                        totals[k] += fv
                        total_n[k] += 1
                except (TypeError, ValueError):
                    pass
        if not any(cells):
            continue
        lines.append("| " + " | ".join(cells) + " |")
        shown += 1

    if with_totals and any(total_n.get(k, 0) > 0 for k in num_keys):
        tot_cells = []
        for i, k in enumerate(keys):
            if i == 0 and k not in num_keys:
                tot_cells.append("**Итого**")
            elif k in num_keys and total_n.get(k, 0) > 0 and not any(
                x in k.lower() for x in ("средн", "цена", "%", "доля", "дельта")
            ):
                # сумма средних/цен/долей бессмысленна — итог только для аддитивных колонок
                tot_cells.append(f"**{_fmt_cell(totals[k], numeric_hint=True)}**")
            else:
                tot_cells.append("")
        lines.append("| " + " | ".join(tot_cells) + " |")

    if len(dict_rows) > shown:
        lines.append(
            f"_… в чате топ **{shown}** из **{len(dict_rows)}** "
            f"(ещё {len(dict_rows) - shown}). Полный список — «в excel» / «покажи всех»._"
        )
    elif shown > 0:
        lines.append(f"_Всего строк в отчёте: **{shown}**._")
    return "\n".join(lines)


def enrich_report_result(
    out: dict[str, Any],
    *,
    request: str = "",
    full_name: str = "",
    period: str = "",
    report_mode: str = "",
) -> dict[str, Any]:
    """Добавить manager_summary + table_preview + жёсткий prefer для LLM."""
    if not isinstance(out, dict):
        return out
    fn = str(
        out.get("full_name") or out.get("primary") or full_name or ""
    ).strip()
    per = str(
        out.get("period")
        or out.get("period_spec")
        or out.get("as_of")
        or period
        or ""
    ).strip()
    # человекочитаемая дата вместо ДАТАВРЕМЯ(...)
    per_human = per
    m_dt = re.search(
        r"ДАТАВРЕМЯ\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)",
        per,
        re.I,
    )
    if m_dt:
        per_human = f"{int(m_dt.group(3)):02d}.{int(m_dt.group(2)):02d}.{m_dt.group(1)}"
    rows = out.get("rows")
    if not isinstance(rows, list):
        # nested periods
        nested = out.get("periods") or out.get("by_period")
        n = 0
        if isinstance(nested, list):
            for p in nested:
                if isinstance(p, dict) and isinstance(p.get("rows"), list):
                    n += len(p["rows"])
        row_count = n
        rows_for_preview: list[Any] = []
    else:
        row_count = len(rows)
        rows_for_preview = rows

    period_labels: list[str] = []
    sbp = out.get("summary_by_period")
    if isinstance(sbp, list):
        for p in sbp:
            if isinstance(p, dict) and p.get("Период"):
                period_labels.append(str(p["Период"]))
    if not period_labels and isinstance(out.get("periods"), list):
        for p in out["periods"]:
            if isinstance(p, dict) and p.get("label"):
                period_labels.append(str(p["label"]))
    if not period_labels and isinstance(rows, list):
        seen: set[str] = set()
        for r in rows:
            if isinstance(r, dict):
                lab = str(r.get("Период") or "").strip()
                if lab and lab not in seen:
                    seen.add(lab)
                    period_labels.append(lab)

    multi_period = len(period_labels) >= 2

    err = out.get("error")
    msg_parts = []
    if fn:
        msg_parts.append(f"Объект: {fn}")
    if per_human:
        msg_parts.append(f"Период: {per_human}")
    if multi_period:
        msg_parts.append(f"Срезы: {', '.join(period_labels)}")
    if err:
        msg_parts.append(f"Ошибка: {err}")
    else:
        msg_parts.append(f"Строк в результате: {row_count}")
    if isinstance(sbp, list) and sbp:
        bits = []
        for p in sbp:
            if not isinstance(p, dict):
                continue
            lab = p.get("Период")
            sm = p.get("Сумма")
            if lab is not None and sm is not None:
                bits.append(f"{lab}={sm}")
        if bits:
            msg_parts.append("Итого: " + "; ".join(bits))

    # один срез (за все время / один год) — в чат больше строк, не «топ-2»
    preview_max = 12 if multi_period else min(30, max(15, row_count if row_count else 15))
    table_preview = rows_to_markdown_table(
        rows_for_preview, max_rows=preview_max, chat_slim=True
    )
    if table_preview:
        out["table_preview"] = table_preview
    out["rows_total"] = row_count
    out["rows_shown_in_preview"] = min(preview_max, row_count) if row_count else 0

    out["manager_summary"] = {
        "object": fn or None,
        "period": per_human or per or None,
        "periods": period_labels or None,
        "row_count": row_count,
        "rows_shown_in_preview": out.get("rows_shown_in_preview"),
        "report_mode": report_mode or out.get("report_mode") or out.get("plan_role"),
        "message": ". ".join(msg_parts) + ".",
        "request": (request or "")[:200] or None,
    }
    if table_preview:
        out["manager_summary"]["table_preview"] = table_preview
    prefer = (
        "Ответ менеджеру СТРОГО так:\n"
        f"1) {out['manager_summary']['message']}\n"
        "2) Если есть summary_by_period — СНАЧАЛА короткая таблица итогов по КАЖДОМУ периоду.\n"
        "3) ОБЯЗАТЕЛЬНО выведи таблицу: table_preview (если есть) ИЛИ rows "
        "(реальные значения: номенклатура, склад, количество). "
        "ЗАПРЕЩЕНО рисовать пустую таблицу с «…» / placeholder.\n"
        "4) Не объясняй JOIN/ВЫБРАТЬ, если не просили.\n"
        "5) Если row_count=0 — скажи «данных нет» и предложи другой период/объект из candidates.\n"
        "6) Не подставляй имена из другой конфигурации."
    )
    if multi_period:
        prefer += (
            f"\n7) ОБЯЗАТЕЛЬНО покажи ВСЕ периоды ({', '.join(period_labels)}) — "
            "и в итогах, и в detail. НЕ пиши «только один год / уточните другой год» — "
            "оба уже в tool result. НЕ проси подтвердить второй период."
        )
    if table_preview:
        shown = int(out.get("rows_shown_in_preview") or 0)
        prefer += (
            "\n8) table_preview уже готов — выведи таблицу **целиком** (все строки markdown), "
            "с Итого и подписью «топ N из M», если есть. "
            f"Всего строк={row_count}, в preview≈{shown}. "
            "ЗАПРЕЩЕНО оставлять только 1–2 строки, если в table_preview их больше. "
            "Не говори «полный список в Excel», не показав table_preview."
        )
    # Excel / графики
    path = out.get("path") or out.get("excel_path") or out.get("download_url")
    if path or out.get("charts") or out.get("file_uri"):
        prefer += (
            "\n9) Если есть path / download_url / file_uri — **обязательно** дай ссылку "
            "на Excel. Упомяни: оформленная таблица, фильтр, "
            + ("график на листе. " if out.get("charts") else "можно попросить «с графиками». ")
        )
    prefer += (
        "\n10) Оформление ответа: краткий заголовок → 1–2 фразы итога → markdown-таблица → "
        "ссылка на Excel. Без «подождите» и без пустых `| ... |`."
    )
    prev = (out.get("prefer") or "").strip()
    out["prefer"] = prefer + (("\n" + prev) if prev else "")
    return out


def count_nonzero_rows(rows: list[Any] | None) -> int:
    if not rows:
        return 0
    n = 0
    for r in rows:
        if not isinstance(r, dict):
            n += 1
            continue
        if r.get("error"):
            continue
        # any numeric non-zero?
        has_num = False
        nonzero = False
        for k, v in r.items():
            if k.lower() in ("период", "period", "error", "год", "месяц", "датаначала", "датаконца", "year", "month", "groups_shown"):
                continue
            try:
                fv = float(v)
                has_num = True
                if fv != 0:
                    nonzero = True
                    break
            except (TypeError, ValueError):
                if v not in (None, "", 0):
                    nonzero = True
                    has_num = True
                    break
        if nonzero or not has_num:
            n += 1
    return n
