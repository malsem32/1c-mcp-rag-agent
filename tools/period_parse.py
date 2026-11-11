"""
Универсальный разбор периодов для отчётов/сравнений 1С.

Поддерживает (примеры, не исчерпывающе):
  • день: 2015-02-15 | 15.02.2015
  • дата+время: 2015-02-15 14:30 | 15.02.2015 09:00:00
  • диапазон: с 01.01.2015 09:00 по 01.01.2015 18:30 | 2015-01-01..2015-02-28
  • сегодня с 9:00 до 18:00
  • месяцы / список дней / квартал / год
  • относительные: последний час, последние N минут/часов/дней/недель/месяцев/лет
  • сегодня, вчера, сутки

Возвращает список срезов {label, start, end} → ДАТАВРЕМЯ(г,м,д,ч,мин,сек).
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

# год для примеров в подсказках (не демо-год базы)
_EX_Y = str(date.today().year - 1)


@dataclass
class PeriodSlice:
    label: str
    start: datetime  # inclusive
    end: datetime  # inclusive

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "start": self.start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": self.end.strftime("%Y-%m-%d %H:%M:%S"),
            "start_date": self.start.strftime("%Y-%m-%d"),
            "end_date": self.end.strftime("%Y-%m-%d"),
            "has_time": not (
                self.start.hour == 0
                and self.start.minute == 0
                and self.start.second == 0
                and self.end.hour == 23
                and self.end.minute == 59
            ),
        }

    def datavremya_start(self) -> str:
        d = self.start
        return f"ДАТАВРЕМЯ({d.year},{d.month},{d.day},{d.hour},{d.minute},{d.second})"

    def datavremya_end(self) -> str:
        d = self.end
        return f"ДАТАВРЕМЯ({d.year},{d.month},{d.day},{d.hour},{d.minute},{d.second})"


_MONTH_NAMES: dict[str, int] = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _norm(s: str) -> str:
    return (
        (s or "")
        .strip()
        .lower()
        .replace("ё", "е")
        .replace("\u00a0", " ")
    )


def _eod(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 23, 59, 59)


def _sod(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 0, 0, 0)


def _month_slice(y: int, m: int) -> PeriodSlice:
    last = calendar.monthrange(y, m)[1]
    return PeriodSlice(
        label=f"{y}-{m:02d}",
        start=_sod(date(y, m, 1)),
        end=_eod(date(y, m, last)),
    )


def _day_slice(d: date) -> PeriodSlice:
    return PeriodSlice(
        label=d.strftime("%Y-%m-%d"),
        start=_sod(d),
        end=_eod(d),
    )


def _parse_one_datetime(token: str) -> datetime | None:
    """
    Дата или дата+время:
      2015-02-15 | 15.02.2015 | 2015-02-15 14:30 | 15.02.2015 09:00:00
      14:30 (только время — без даты вернёт None)
    """
    t = (token or "").strip()
    if not t:
        return None
    # нормализовать T-separator
    t = t.replace("t", " ").replace("T", " ")
    t = re.sub(r"\s+", " ", t).strip()

    dt_fmts = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%Y.%m.%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%Y-%m-%d",
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%Y.%m.%d",
        "%d-%m-%Y",
    )
    for fmt in dt_fmts:
        try:
            return datetime.strptime(t, fmt)
        except ValueError:
            continue
    # 2015-02 → 1-е число 00:00
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", t)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return datetime(y, mo, 1, 0, 0, 0)
    return None


def _parse_time_only(token: str) -> tuple[int, int, int] | None:
    """09:00 | 9:30 | 18:00:00 → (h, m, s)"""
    t = token.strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", t)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    sec = int(m.group(3) or 0)
    if 0 <= h <= 23 and 0 <= mi <= 59 and 0 <= sec <= 59:
        return h, mi, sec
    return None


def _extract_year(text: str, default: int | None) -> int | None:
    m = re.search(r"\b(19|20)\d{2}\b", text)
    if m:
        return int(m.group(0))
    return default


def default_period(now: date | datetime | None = None) -> str:
    """
    Дефолт, если пользователь **не указал** период: «за все время».
    Универсально (УТ/УПП/ERP/демо/прод) — не подставлять «два последних года»
    (на старых ИБ это часто пустые 2024–2025).
    """
    _ = now  # API совместимость
    return "за все время"


def default_period_years(now: date | datetime | None = None) -> str:
    """
    Дефолт без явного периода → «за все время» (см. default_period).

    Имя историческое (раньше — два последних года); оставлено для импортов.
    Явные годы: только если пользователь написал 2015 / «прошлый год» / …
    """
    return default_period(now)


def extract_period_spec(
    *parts: str,
    default: str | None = None,
    now: date | datetime | None = None,
) -> str:
    """
    Вытащить компактный period для parse_periods / compare_periods.

    НЕ возвращает целую фразу «что продаётся / abc / кто вырос…».
    Приоритет: all-time → годы → относительные (последний месяц…) → default.

    default=None → «за все время»; default="" → пусто (caller сам решит).
    """
    blob = " ".join(p for p in parts if p).strip()
    if not blob:
        if default is None:
            return default_period(now)
        return default

    if is_all_time_phrase(blob):
        return "за все время"

    # явные годы (1+); один и тот же год из period и из фразы — это ОДИН год
    years = list(dict.fromkeys(re.findall(r"\b((?:19|20)\d{2})\b", blob)))
    if len(years) >= 2:
        return " и ".join(years)
    if len(years) == 1:
        # «1 квартал 2016» / «первый квартал 2016» / «2 полугодие 2016»
        m_q = re.search(
            r"((?:[1-4]|перв\w*|втор\w*|трет\w*|четверт\w*|i{1,3}|iv)\s*(?:-?й\s*)?(?:квартал|кв\.?|полугоди\w*)\s*(?:\d{4}\s*(?:г\.?|года?)?)?)",
            _norm(blob),
        )
        if m_q:
            chunk = m_q.group(1).strip()
            return chunk if years[0] in chunk else f"{chunk} {years[0]}"
        # «январь 2015» / «2015 год» / «за 2015»
        m_mon = re.search(
            r"((?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|"
            r"октябр|ноябр|декабр)\w*(?:\s*(?:и|,)\s*"
            r"(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|"
            r"октябр|ноябр|декабр)\w*)*\s*"
            + years[0]
            + r")",
            _norm(blob),
        )
        if m_mon:
            return m_mon.group(1).strip()
        return years[0]

    # относительные / сейчас / диапазоны без года в тексте
    t = _norm(blob)
    rel_markers = (
        r"последн\w*\s+(\d+\s+)?(час|день|дня|дней|недел|месяц|год|минут)",
        r"прошл\w*\s+(месяц|год|недел|квартал)",
        r"текущ\w*\s+(месяц|год|недел|квартал|день)",
        r"\b(сегодня|вчера|сутки)\b",
        r"\bсейчас\b",
        r"на\s+текущ\w*\s+момент",
        r"с\s+\d{1,2}[./]\d{1,2}",
        r"\d{4}-\d{2}-\d{2}",
    )
    m_pair = re.search(
        r"(?:этот|этого|эту|этой|текущ\w*)\s+(?:месяц|год|недел|квартал)\w*.{0,25}?(?:и|к|с|vs|против)\s+(?:прошл\w*|предыдущ\w*)(?:\s+\w+)?",
        t,
    )
    if m_pair:
        return m_pair.group(0).strip()
    for pat in rel_markers:
        m = re.search(pat, t)
        if m and re.match(r"(?:прошл|текущ|последн)", m.group(0)):
            # «за прошлую неделю что отгрузили…» → только «прошлую неделю»
            return m.group(0).strip()
        if m:
            # вырезать короткий кусок вокруг match
            start = max(0, m.start() - 5)
            end = min(len(t), m.end() + 40)
            chunk = t[start:end].strip()
            # не тащить весь вопрос
            if len(chunk) <= 80:
                return chunk
            return m.group(0).strip()

    # короткий period-only ввод («январь и февраль», «1 квартал»)
    if len(blob) <= 40 and re.search(
        r"(январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|"
        r"октябр|ноябр|декабр|квартал|месяц|недел|год)",
        t,
    ):
        return blob.strip()

    # длинная бизнес-фраза без дат → «за все время», не raw
    if default is None:
        return default_period(now)
    return default


def parse_periods(
    spec: str,
    *,
    today: date | datetime | None = None,
    default_year: int | None = None,
    legacy_year: int | None = None,
    legacy_months: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Разбор свободного текста периода → список PeriodSlice.

    legacy_year + legacy_months — совместимость со старым API compare_periods.
    now/today — «сейчас» для относительных (часы/минуты требуют datetime).
    """
    if now is not None:
        now_dt = now
    elif isinstance(today, datetime):
        now_dt = today
    elif isinstance(today, date):
        now_dt = datetime(today.year, today.month, today.day, 12, 0, 0)
    else:
        now_dt = datetime.now()
    now_d = now_dt.date()

    raw = (spec or "").strip()
    # «этот месяц и прошлый / этого месяца к прошлому / текущий год vs прошлый» → ДВА периода
    m_pair = re.search(
        r"(?:этот|этого|эту|этой|текущ\w*)\s+(месяц|год|недел|квартал)\w*.{0,25}?(?:и|к|с|vs|против|по\s+сравнению\s+с)\s+(?:прошл\w*|предыдущ\w*)"
        r"|(?:прошл\w*|предыдущ\w*)\s+(месяц|год|недел|квартал)\w*.{0,25}?(?:и|к|с|vs)\s+(?:этот|этого|эту|этой|текущ\w*)",
        _norm(raw),
    )
    if m_pair:
        unit = m_pair.group(1) or m_pair.group(2)
        word = {"месяц": "месяц", "год": "год", "недел": "неделю", "квартал": "квартал"}[unit]
        prev = parse_periods(f"прошлый {word}" if word != "неделю" else "прошлую неделю", now=now_dt)
        cur = parse_periods(f"текущий {word}" if word != "неделю" else "текущую неделю", now=now_dt)
        sl = list(prev.get("slices") or []) + list(cur.get("slices") or [])
        if len(sl) == 2:
            return {
                "ok": True,
                "periods": list(prev.get("periods") or []) + list(cur.get("periods") or []),
                "slices": sl,
                "source": "relative_pair",
            }

    # legacy
    if not raw and legacy_year and legacy_months:
        raw = f"{legacy_months} {legacy_year}"
    elif not raw and legacy_year:
        raw = str(legacy_year)
    elif raw and legacy_year and not re.search(r"\b(19|20)\d{2}\b", raw):
        if legacy_year:
            raw = f"{raw} {legacy_year}"

    if not raw:
        return {
            "error": "empty_period",
            "need_clarification": True,
            "question": (
                "Уточните период: день, время, диапазон, месяцы, "
                "«последний час/день/неделя/месяц», «с 9:00 до 18:00»…"
            ),
            "about": "period",
            "examples": [
                f"январь и февраль {_EX_Y}",
                f"{_EX_Y}-02-15 14:30",
                f"с 01.01.{_EX_Y} 09:00 по 01.01.{_EX_Y} 18:00",
                "сегодня с 9:00 до 18:00",
                "последний час",
                "последние 30 минут",
                "последние 7 дней",
                f"с 01.01.{_EX_Y} по 28.02.{_EX_Y}",
                f"1 квартал {_EX_Y}",
                "за все время",
            ],
        }

    # safety net: user-фраза вместо period → сжать / default
    # (даже короткие: «что продается а что нет», «abc по контрагентам»)
    if not is_all_time_phrase(raw):
        has_year = bool(re.search(r"\b(?:19|20)\d{2}\b", raw))
        has_rel = bool(
            re.search(
                r"(последн|сегодня|вчера|сейчас|квартал|январ|феврал|март|"
                r"апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр|"
                r"\d{1,2}[./]\d{1,2}|\d{4}-\d{2}-\d{2})",
                _norm(raw),
            )
        )
        looks_business = bool(
            re.search(
                r"(прода|реализ|ассортимент|контрагент|клиент|марж|abc|авс|"
                r"вырос|упал|заморож|остат|склад|менеджер|номенклатур|"
                r"воронк|сравни|отчёт|отчет|что\s+прода|что\s+нет)",
                _norm(raw),
            )
        )
        if looks_business or len(raw) > 40:
            compact = extract_period_spec(raw, default="", now=now_dt)
            if compact and compact != raw:
                raw = compact
            elif not has_year and not has_rel:
                raw = default_period(now_dt)

    text = _norm(raw)
    text_work = text
    for w in (
        "за период",
        "за ",
        "период",
        "данные",
        "сравнить",
        "сравнение",
        "вывести",
        "покажи",
        "отчёт",
        "отчет",
    ):
        text_work = text_work.replace(w, " ")
    text_work = re.sub(r"\s+", " ", text_work).strip()

    slices: list[PeriodSlice] = []
    year_hint = _extract_year(text_work, default_year or legacy_year)

    # --- «за всё время» / all time (до relative) ---
    all_time = _parse_all_time(text, now_dt) or _parse_all_time(text_work, now_dt)
    if all_time is not None:
        return {
            "ok": True,
            "periods": [p.to_dict() for p in all_time],
            "slices": all_time,
            "source": "all_time",
        }

    # --- относительные (в т.ч. часы/минуты) ---
    rel = _parse_relative(text_work, now_dt)
    if rel is not None:
        return {"ok": True, "periods": [p.to_dict() for p in rel], "slices": rel, "source": "relative"}

    # --- сегодня/вчера с HH:MM до HH:MM ---
    tod_range = _parse_today_time_range(text_work, now_d)
    if tod_range is not None:
        return {
            "ok": True,
            "periods": [tod_range.to_dict()],
            "slices": [tod_range],
            "source": "time_range",
        }

    # --- диапазон: с X по Y (даты и/или дата-время) ---
    range_s = _parse_range(text_work, year_hint, now_d)
    if range_s is not None:
        return {
            "ok": True,
            "periods": [range_s.to_dict()],
            "slices": [range_s],
            "source": "range",
        }

    # --- квартал ---
    q = _parse_quarter(text_work, year_hint, now_d)
    if q is not None:
        return {"ok": True, "periods": [q.to_dict()], "slices": [q], "source": "quarter"}

    # --- один/несколько date-time токенов ---
    # дата + опционально время
    dt_tokens = re.findall(
        r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{4})"
        r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\b",
        text_work,
    )
    if dt_tokens and not re.search(
        r"\b(январ|феврал|март|апрел|май|июн|июл|август|сентябр|октябр|ноябр|декабр)\b",
        text_work,
    ):
        for dtok in dt_tokens:
            dt = _parse_one_datetime(dtok)
            if not dt:
                continue
            # если указано время (не полночь по умолчанию из date-only) —
            # date-only formats leave 00:00; treat as full day unless time in token
            if re.search(r"\d{1,2}:\d{2}", dtok):
                # точка: минута (start..end of that second/minute)
                end = dt.replace(second=59) if dt.second == 0 else dt
                if dt.second == 0 and dt.minute >= 0:
                    # интервал «в 14:30» = вся минута
                    end = dt.replace(second=59)
                slices.append(
                    PeriodSlice(
                        label=dt.strftime("%Y-%m-%d %H:%M"),
                        start=dt.replace(second=0),
                        end=end if end >= dt else dt,
                    )
                )
            else:
                slices.append(_day_slice(dt.date()))
        if slices:
            return {
                "ok": True,
                "periods": [p.to_dict() for p in slices],
                "slices": slices,
                "source": "datetime" if any(":" in t for t in dt_tokens) else "days",
            }

    # --- месяцы (имена или номера) + год ---
    months_found = _parse_months_list(text_work, year_hint, now_d)
    if months_found:
        return {
            "ok": True,
            "periods": [p.to_dict() for p in months_found],
            "slices": months_found,
            "source": "months",
        }

    # --- несколько лет: 2015 и 2016 / за 2016 и 2015 года ---
    multi_years = _parse_multi_years(text_work)
    if multi_years:
        return {
            "ok": True,
            "periods": [p.to_dict() for p in multi_years],
            "slices": multi_years,
            "source": "multi_year",
        }

    # --- год: 2015 | 2015 год | за 2015 год | год 2015 | весь 2015 | в 2015 ---
    year_slice = _parse_year_only(text_work, year_hint)
    if year_slice is not None:
        return {
            "ok": True,
            "periods": [year_slice.to_dict()],
            "slices": [year_slice],
            "source": "year",
        }

    # --- YYYY-MM alone ---
    m_ym = re.fullmatch(r"(\d{4})-(\d{1,2})", text_work.strip())
    if m_ym:
        y, mo = int(m_ym.group(1)), int(m_ym.group(2))
        if 1 <= mo <= 12:
            sl = _month_slice(y, mo)
            return {"ok": True, "periods": [sl.to_dict()], "slices": [sl], "source": "year_month"}

    return {
        "error": "unparsed_period",
        "need_clarification": True,
        "question": (
            f"Не удалось разобрать период «{spec}». "
            f"Уточните: «{_EX_Y}», «{_EX_Y} год», «январь {_EX_Y}», "
            "диапазон «с … по …», «последний месяц/час»."
        ),
        "about": "period",
        "raw": spec,
        "examples": [
            f"{_EX_Y}",
            f"{_EX_Y} год",
            f"за {_EX_Y} год",
            f"январь и февраль {_EX_Y}",
            f"{_EX_Y}-02-15 14:30",
            f"с 01.01.{_EX_Y} 09:00 по 01.01.{_EX_Y} 18:00",
            "сегодня с 9:00 до 18:00",
            "последний час",
            "последние 30 минут",
            "последние 7 дней",
        ],
    }


def _parse_multi_years(text: str) -> list[PeriodSlice] | None:
    """2015 и 2016 / 2015, 2016 / 2015–2017 → список годовых срезов."""
    t = (text or "").strip()
    if not t:
        return None
    # не трогать, если явные месяцы (их разберёт months)
    if re.search(
        r"\b(январ|феврал|март|апрел|май|июн|июл|август|сентябр|октябр|ноябр|декабр)\b",
        t,
    ):
        return None
    years = [int(y) for y in re.findall(r"\b((?:19|20)\d{2})\b", t)]
    # уникальные с сохранением порядка
    uniq: list[int] = []
    for y in years:
        if y not in uniq and 1990 <= y <= 2100:
            uniq.append(y)
    if len(uniq) < 2:
        # диапазон 2015-2017 через дефис уже в findall как два числа? "2015-2017" → 2015, 2017
        m = re.search(r"\b((?:19|20)\d{2})\s*[-–—]\s*((?:19|20)\d{2})\b", t)
        if m:
            y1, y2 = int(m.group(1)), int(m.group(2))
            if y1 > y2:
                y1, y2 = y2, y1
            uniq = list(range(y1, y2 + 1))
    if len(uniq) < 2:
        return None
    out: list[PeriodSlice] = []
    for y in uniq[:12]:
        out.append(
            PeriodSlice(
                label=str(y),
                start=__import__("datetime").datetime(y, 1, 1, 0, 0, 0),
                end=__import__("datetime").datetime(y, 12, 31, 23, 59, 59),
            )
        )
    return out or None


def _parse_year_only(text: str, year_hint: int | None) -> PeriodSlice | None:
    """
    2015 | 2015 год | год 2015 | за 2015 | за 2015 год | весь 2015 | в 2015 году
    Не срабатывает, если рядом месяцы/квартал (их разбирают раньше).
    """
    t = (text or "").strip()
    if not t:
        return None
    # уже есть явные месяцы — не год целиком
    if re.search(
        r"\b(январ|феврал|март|апрел|май|июн|июл|август|сентябр|октябр|ноябр|декабр|"
        r"квартал|q[1-4])\b",
        t,
    ):
        return None
    # диапазон дат — не сюда
    if re.search(r"\d{1,2}[./]\d{1,2}[./]\d{4}", t) or re.search(
        r"\d{4}-\d{2}-\d{2}", t
    ):
        return None

    m = re.search(
        r"(?:^|\s)(?:за|в|весь|целый|полный)?\s*"
        r"(?:год\s+)?((?:19|20)\d{2})(?:\s*год(?:у|а|ом)?)?(?:\s|$)",
        t,
    )
    if not m:
        m = re.fullmatch(r"((?:19|20)\d{2})", t)
    if not m and year_hint and re.fullmatch(
        r"(?:за|в|весь)?\s*год(?:у|а)?", t
    ):
        y = int(year_hint)
        return PeriodSlice(
            label=str(y),
            start=_sod(date(y, 1, 1)),
            end=_eod(date(y, 12, 31)),
        )
    if not m:
        return None
    y = int(m.group(1))
    if y < 1990 or y > 2100:
        return None
    return PeriodSlice(
        label=str(y),
        start=_sod(date(y, 1, 1)),
        end=_eod(date(y, 12, 31)),
    )


def _as_datetime(now: date | datetime) -> datetime:
    if isinstance(now, datetime):
        return now
    return datetime(now.year, now.month, now.day, 12, 0, 0)


def _parse_today_time_range(text: str, today: date) -> PeriodSlice | None:
    """сегодня с 9:00 до 18:00 | сегодня 09:00-18:30"""
    m = re.search(
        r"(сегодня|вчера)?\s*(?:с|from)?\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*"
        r"(?:до|по|to|-|—|–)\s*(\d{1,2}:\d{2}(?::\d{2})?)",
        text,
    )
    if not m:
        return None
    day_word = m.group(1) or "сегодня"
    t1 = _parse_time_only(m.group(2))
    t2 = _parse_time_only(m.group(3))
    if not t1 or not t2:
        return None
    base = today if day_word == "сегодня" else today - timedelta(days=1)
    start = datetime(base.year, base.month, base.day, t1[0], t1[1], t1[2])
    end = datetime(base.year, base.month, base.day, t2[0], t2[1], t2[2])
    if end < start:
        end += timedelta(days=1)
    # если секунды 0 на конце — включить всю минуту
    if end.second == 0:
        end = end.replace(second=59)
    return PeriodSlice(
        label=f"{base.isoformat()}_{m.group(2)}-{m.group(3)}",
        start=start,
        end=end,
    )


def is_all_time_phrase(text: str) -> bool:
    """«за всё время», «за всю историю», all time…"""
    t = _norm(text)
    if not t:
        return False
    if re.search(
        r"\b("
        r"все\s+время|всё\s+время|"
        r"весь\s+период|весь\s+срок|"
        r"всю\s+истори\w*|"
        r"все\s+годы|всех\s+лет|все\s+года|"
        r"за\s+вс[её]\b|за\s+весь\s+срок|"
        r"all\s*time|entire\s+period|all\s+years|"
        r"без\s+ограничен\w*\s+период|любой\s+период|"
        r"за\s+все\s+годы|за\s+все\s+время"
        r")\b",
        t,
    ):
        return True
    if t.strip() in (
        "все время",
        "всё время",
        "весь период",
        "all time",
        "всегда",
    ):
        return True
    return False


def _parse_all_time(text: str, now: date | datetime) -> list[PeriodSlice] | None:
    if not is_all_time_phrase(text):
        return None
    now_dt = _as_datetime(now)
    # широкий диапазон: 15 лет назад → сейчас
    start_year = max(2000, now_dt.year - 15)
    start = datetime(start_year, 1, 1, 0, 0, 0)
    return [
        PeriodSlice(
            label="за все время",
            start=start,
            end=now_dt,
        )
    ]


def _parse_relative(text: str, now: date | datetime) -> list[PeriodSlice] | None:
    t = text
    now_dt = _as_datetime(now)
    now_d = now_dt.date()

    # all-time (если дошли сюда с уже очищенным text)
    at = _parse_all_time(t, now_dt)
    if at is not None:
        return at

    # сейчас / на текущий момент — точка «as of now» (остатки, срез)
    if re.search(
        r"\b("
        r"сейчас|"
        r"на\s+сейчас|"
        r"на\s+текущ\w*\s+момент|"
        r"текущ\w*\s+момент|"
        r"на\s+данн\w*\s+момент|"
        r"as\s*of\s*now|"
        r"current\s+moment"
        r")\b",
        t,
    ) or t.strip() in ("сейчас", "now", "current"):
        return [
            PeriodSlice(
                label="сейчас",
                start=now_dt,
                end=now_dt,
            )
        ]

    # сегодня / вчера / сутки
    if re.search(r"\bсегодня\b", t) and not re.search(r"\d{1,2}:\d{2}", t):
        return [_day_slice(now_d)]
    if t in ("день", "текущий день"):
        return [_day_slice(now_d)]
    if re.search(r"\bвчера\b", t) and not re.search(r"\d{1,2}:\d{2}", t):
        return [_day_slice(now_d - timedelta(days=1))]
    if re.search(r"\bсутки\b", t) and not re.search(r"\d", t):
        # последние 24 часа
        start = now_dt - timedelta(hours=24)
        return [
            PeriodSlice(
                label="сутки",
                start=start,
                end=now_dt,
            )
        ]

    # последние N минут/часов/дней/…
    m = re.search(
        r"(?:последн\w*|за\s+последн\w*|recent|last)\s+(\d+)\s*"
        r"(минут\w*|мин\.?|час\w*|ч\.?|день|дня|дней|сутк\w*|недел\w*|"
        r"месяц\w*|год\w*|minute|minutes|min|hour|hours|h|day|days|week|weeks|"
        r"month|months|year|years)",
        t,
    )
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        return _last_n(n, unit, now_dt)

    # последний час / последняя минута (без числа)
    m = re.search(
        r"(?:последн\w*|прошл\w*|предыдущ\w*|last|previous)\s+"
        r"(минут\w*|час\w*|день|сутки|недел\w*|месяц\w*|год\w*|квартал\w*|"
        r"minute|hour|day|week|month|year|quarter)",
        t,
    )
    if m:
        unit = m.group(1)
        return _last_n(1, unit, now_dt)

    # текущий/этот месяц|год|неделя|час
    m = re.search(
        r"(?:текущ\w*|эт(от|а|и)\s+|this|current)\s+"
        r"(час\w*|день|недел\w*|месяц\w*|год\w*|квартал\w*|"
        r"hour|day|week|month|year|quarter)",
        t,
    )
    if m:
        return _current_unit(m.group(2), now_dt)

    if re.fullmatch(r"месяц|неделя|год|квартал|час", t):
        return _current_unit(t, now_dt)

    return None


def _is_minute_unit(u: str) -> bool:
    u = u.lower().rstrip(".")
    return u.startswith(("минут", "мин", "minute", "min"))


def _is_hour_unit(u: str) -> bool:
    u = u.lower().rstrip(".")
    return u.startswith(("час", "hour", "h")) and not u.startswith("сут")


def _is_day_unit(u: str) -> bool:
    u = u.lower()
    return u.startswith(("день", "дня", "дне", "сут", "day"))


def _is_week_unit(u: str) -> bool:
    return u.lower().startswith(("недел", "week"))


def _is_month_unit(u: str) -> bool:
    return u.lower().startswith(("месяц", "month"))


def _is_year_unit(u: str) -> bool:
    return u.lower().startswith(("год", "year"))


def _last_n(n: int, unit: str, now: date | datetime) -> list[PeriodSlice]:
    u = unit.lower()
    now_dt = _as_datetime(now)
    now_d = now_dt.date()
    n = max(1, min(n, 10080))  # до недели в минутах * safety

    if _is_minute_unit(u):
        n = min(n, 24 * 60)
        start = now_dt - timedelta(minutes=n)
        return [
            PeriodSlice(
                label=f"последние_{n}_мин",
                start=start,
                end=now_dt,
            )
        ]

    if _is_hour_unit(u):
        n = min(n, 24 * 30)
        start = now_dt - timedelta(hours=n)
        if n == 1:
            return [
                PeriodSlice(
                    label="последний_час",
                    start=start,
                    end=now_dt,
                )
            ]
        return [
            PeriodSlice(
                label=f"последние_{n}_ч",
                start=start,
                end=now_dt,
            )
        ]

    if _is_day_unit(u):
        if n == 1:
            return [_day_slice(now_d - timedelta(days=1))]
        if n <= 14:
            return [_day_slice(now_d - timedelta(days=i)) for i in range(n - 1, -1, -1)]
        start = now_d - timedelta(days=n - 1)
        return [
            PeriodSlice(
                label=f"последние_{n}_дней",
                start=_sod(start),
                end=_eod(now_d),
            )
        ]

    if _is_week_unit(u):
        if n == 1:
            start_this = now_d - timedelta(days=now_d.weekday())
            end_prev = start_this - timedelta(days=1)
            start_prev = end_prev - timedelta(days=6)
            return [
                PeriodSlice(
                    label=f"неделя_{start_prev.isoformat()}",
                    start=_sod(start_prev),
                    end=_eod(end_prev),
                )
            ]
        slices = []
        start_this = now_d - timedelta(days=now_d.weekday())
        for i in range(n, 0, -1):
            end_w = start_this - timedelta(days=1 + (i - 1) * 7)
            start_w = end_w - timedelta(days=6)
            slices.append(
                PeriodSlice(
                    label=f"неделя_{start_w.isoformat()}",
                    start=_sod(start_w),
                    end=_eod(end_w),
                )
            )
        return slices

    if _is_month_unit(u):
        y, m = now_d.year, now_d.month
        if n == 1:
            mm, yy = m - 1, y
            if mm <= 0:
                mm += 12
                yy -= 1
            return [_month_slice(yy, mm)]
        slices = []
        for i in range(n - 1, -1, -1):
            mm = m - i
            yy = y
            while mm <= 0:
                mm += 12
                yy -= 1
            slices.append(_month_slice(yy, mm))
        return slices

    if _is_year_unit(u):
        if n == 1:
            return [
                PeriodSlice(
                    label=str(now_d.year - 1),
                    start=_sod(date(now_d.year - 1, 1, 1)),
                    end=_eod(date(now_d.year - 1, 12, 31)),
                )
            ]
        return [
            PeriodSlice(
                label=str(now_d.year - i),
                start=_sod(date(now_d.year - i, 1, 1)),
                end=_eod(date(now_d.year - i, 12, 31)),
            )
            for i in range(n - 1, -1, -1)
        ]

    if u.startswith("квартал") or u.startswith("quarter"):
        q = (now_d.month - 1) // 3
        q -= 1
        y = now_d.year
        if q < 0:
            q = 3
            y -= 1
        m0 = q * 3 + 1
        m1 = m0 + 2
        last = calendar.monthrange(y, m1)[1]
        return [
            PeriodSlice(
                label=f"{y}-Q{q+1}",
                start=_sod(date(y, m0, 1)),
                end=_eod(date(y, m1, last)),
            )
        ]

    return [_month_slice(now_d.year, now_d.month)]


def _current_unit(unit: str, now: date | datetime) -> list[PeriodSlice]:
    u = unit.lower()
    now_dt = _as_datetime(now)
    now_d = now_dt.date()
    if _is_hour_unit(u):
        start = now_dt.replace(minute=0, second=0, microsecond=0)
        return [
            PeriodSlice(
                label=start.strftime("%Y-%m-%d %H:00"),
                start=start,
                end=now_dt,
            )
        ]
    if u.startswith("день") or u.startswith("day"):
        return [_day_slice(now_d)]
    if u.startswith("недел") or u.startswith("week"):
        start = now_d - timedelta(days=now_d.weekday())
        end = start + timedelta(days=6)
        return [
            PeriodSlice(
                label=f"неделя_{start.isoformat()}",
                start=_sod(start),
                end=_eod(end),
            )
        ]
    if u.startswith("месяц") or u.startswith("month"):
        return [_month_slice(now_d.year, now_d.month)]
    if u.startswith("год") or u.startswith("year"):
        return [
            PeriodSlice(
                label=str(now_d.year),
                start=_sod(date(now_d.year, 1, 1)),
                end=_eod(date(now_d.year, 12, 31)),
            )
        ]
    if u.startswith("квартал") or u.startswith("quarter"):
        q = (now_d.month - 1) // 3
        m0 = q * 3 + 1
        m1 = m0 + 2
        last = calendar.monthrange(now_d.year, m1)[1]
        return [
            PeriodSlice(
                label=f"{now_d.year}-Q{q+1}",
                start=_sod(date(now_d.year, m0, 1)),
                end=_eod(date(now_d.year, m1, last)),
            )
        ]
    return [_month_slice(now_d.year, now_d.month)]


def _parse_range(text: str, year_hint: int | None, now: date) -> PeriodSlice | None:
    """
    с DATE[ time] по DATE[ time]
    DATE .. DATE
    между DATE и DATE
    """
    # дата + опционально время — всё в одной capture-группе
    dt_token = (
        r"("
        r"(?:(?:19|20)\d{2}[./\-]\d{1,2}[./\-]\d{1,2}"
        r"|\d{1,2}[./\-]\d{1,2}[./\-](?:19|20)\d{2})"
        r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?"
        r")"
    )
    patterns = [
        rf"(?:с|от|from)\s+{dt_token}\s+(?:по|до|to)\s+{dt_token}",
        rf"{dt_token}\s*(?:\.\.|…)\s*{dt_token}",
        rf"(?:между|between)\s+{dt_token}\s+(?:и|and)\s+{dt_token}",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if not m:
            continue
        dt1 = _parse_one_datetime(m.group(1).strip())
        dt2 = _parse_one_datetime(m.group(2).strip())
        if not dt1 or not dt2:
            continue
        # date-only → start of day / end of day
        t1_has = bool(re.search(r"\d{1,2}:\d{2}", m.group(1)))
        t2_has = bool(re.search(r"\d{1,2}:\d{2}", m.group(2)))
        if not t1_has:
            dt1 = _sod(dt1.date())
        if not t2_has:
            dt2 = _eod(dt2.date())
        elif dt2.second == 0:
            dt2 = dt2.replace(second=59)
        if dt2 < dt1:
            dt1, dt2 = dt2, dt1
        lab1 = dt1.strftime("%Y-%m-%d %H:%M") if t1_has else dt1.strftime("%Y-%m-%d")
        lab2 = dt2.strftime("%Y-%m-%d %H:%M") if t2_has else dt2.strftime("%Y-%m-%d")
        return PeriodSlice(
            label=f"{lab1}_{lab2}",
            start=dt1,
            end=dt2,
        )
    return None


def _parse_quarter(text: str, year_hint: int | None, now: date) -> PeriodSlice | None:
    m = re.search(r"(?:^|\s)([1-4])\s*квартал(?:\s+(\d{4}))?", text)
    if not m:
        m = re.search(r"\bq([1-4])(?:\s*[\-/]?\s*(\d{4}))?\b", text)
    if not m:
        return None
    q = int(m.group(1))
    y = int(m.group(2)) if m.lastindex and m.group(2) else (year_hint or now.year)
    m0 = (q - 1) * 3 + 1
    m1 = m0 + 2
    last = calendar.monthrange(y, m1)[1]
    return PeriodSlice(
        label=f"{y}-Q{q}",
        start=_sod(date(y, m0, 1)),
        end=_eod(date(y, m1, last)),
    )


def _parse_months_list(
    text: str, year_hint: int | None, now: date
) -> list[PeriodSlice] | None:
    y = year_hint or now.year
    months: list[int] = []

    # имена месяцев
    for key, num in _MONTH_NAMES.items():
        if key in ("may",) or len(key) < 3:
            continue
        if re.search(rf"\b{key}\w*", text):
            if num not in months:
                months.append(num)

    # номера: 1,2,3 или 1 и 2 (если нет дат вида 2015-01-02 already handled)
    if not months:
        # «месяцы 1 2 3» / «1,2,3»
        if re.search(r"\bмесяц", text) or re.fullmatch(
            r"[\d,\sи]+(?:\s+\d{4})?", text.strip()
        ):
            for tok in re.findall(r"\b(\d{1,2})\b", text):
                n = int(tok)
                if 1 <= n <= 12 and n not in months:
                    months.append(n)

    # legacy «1,2» with year already in text
    if not months:
        only_nums = re.findall(r"\b(\d{1,2})\b", text)
        years = re.findall(r"\b((?:19|20)\d{2})\b", text)
        nums = [int(x) for x in only_nums if 1 <= int(x) <= 12]
        # exclude day parts if full dates present — already returned
        if nums and (years or year_hint):
            # if we have many small nums might be months
            if all(1 <= n <= 12 for n in nums):
                months = []
                for n in nums:
                    if n not in months:
                        months.append(n)

    if not months:
        return None

    return [_month_slice(y, m) for m in months]



# --- разбивка периода на шаги («по месяцам», «понедельно») ---

_GRAIN_ORDER = ("day", "week", "month", "quarter", "year")


def _grain_slices(s: PeriodSlice, grain: str) -> list[PeriodSlice]:
    out: list[PeriodSlice] = []
    cur = s.start
    while cur <= s.end:
        d = cur.date()
        if grain == "day":
            nxt = d + timedelta(days=1)
            label = d.strftime("%Y-%m-%d")
        elif grain == "week":
            nxt = d + timedelta(days=7 - d.weekday())
            iso = d.isocalendar()
            label = f"{iso[0]}-W{iso[1]:02d}"
        elif grain == "month":
            nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
            label = f"{d.year}-{d.month:02d}"
        elif grain == "quarter":
            q = (d.month - 1) // 3
            nm = q * 3 + 4
            nxt = date(d.year + (nm > 12), (nm - 1) % 12 + 1, 1)
            label = f"{d.year}-Q{q + 1}"
        else:
            nxt = date(d.year + 1, 1, 1)
            label = str(d.year)
        end = min(_sod(nxt) - timedelta(seconds=1), s.end)
        out.append(PeriodSlice(label=label, start=cur, end=end))
        cur = _sod(nxt)
    return out


def split_by_grain(
    slices: list[PeriodSlice], grain: str, *, cap: int = 60
) -> tuple[list[PeriodSlice], str]:
    """
    Срезы → подпериоды шага grain (day|week|month|quarter|year).
    Если выходит > cap срезов — укрупняет шаг. Возвращает (срезы, фактический шаг).
    """
    g = (grain or "").strip().lower()
    if g not in _GRAIN_ORDER or not slices:
        return slices, ""
    for gi in range(_GRAIN_ORDER.index(g), len(_GRAIN_ORDER)):
        gg = _GRAIN_ORDER[gi]
        res: list[PeriodSlice] = []
        for s in slices:
            res.extend(_grain_slices(s, gg))
            if len(res) > cap:
                break
        if len(res) <= cap:
            return res, gg
    return slices, ""
