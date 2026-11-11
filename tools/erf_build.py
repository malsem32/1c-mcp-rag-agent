# -*- coding: utf-8 -*-
"""
Сборка внешнего отчёта 1С (.erf) на СКД без платформы: контейнер v8 + скобочные метаданные + схема компоновки.

Устройство (снято с файлов, собранных платформой): объект-отчёт, одна обычная форма «ФормаОтчета» (стандартная панель
«Сформировать / Настройки…», модуль пуст — платформа сама строит отчёт по СКД), макет «ОсновнаяСхемаКомпоновкиДанных»
(заголовок из трёх частей + схема + настройки варианта + пустая схема-хвост), необязательный модуль объекта.
Контейнер, GUID-ы и сжатие — общие с epf_build.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from tools import epf_build as B
from tools import erf_settings as ES
from tools.erf_form import report_form
from tools.epf_build import BuildError, _GUID0, _deflate, _guid, _ident, _pack_container, _q, _text

_TPL_DIR = Path(__file__).parent / "data" / "erf_tpl"
_CLS_REPORT = "e41aff26-25cf-4bb6-b6c1-3f478a75f374"
_CLS_FORM = "a3b368c0-29e2-11d6-a3c7-0050bae0a776"
_CLS_TEMPLATE = "3daea016-69b7-4ed4-9453-127911372fe6"
_CLS_ATTR = "7e7123e0-29e2-11d6-a3c7-0050bae0a776"
_CLS_TS = "b077d780-29e2-11d6-a3c7-0050bae0a776"
_FORM_NAME, _FORM_SYN = "ФормаОтчета", "Форма отчета"
_TPL_NAME, _TPL_SYN = "ОсновнаяСхемаКомпоновкиДанных", "Основная схема компоновки данных"

_NS_HEAD = '<?xml version="1.0" encoding="UTF-8"?>\r\n'
_NS_SCHEMA = "http://v8.1c.ru/8.1/data-composition-system/schema"
_NS_CORE = "http://v8.1c.ru/8.1/data/core"
_NS_CFG = "http://v8.1c.ru/8.1/data/enterprise/current-config"

# тип ссылки в схеме компоновки (латиница, как пишет платформа)
_REF_XML = {
    "Справочник": "CatalogRef", "Документ": "DocumentRef", "Перечисление": "EnumRef",
    "ПланВидовХарактеристик": "ChartOfCharacteristicTypesRef", "ПланСчетов": "ChartOfAccountsRef",
    "ПланВидовРасчета": "ChartOfCalculationTypesRef", "ПланОбмена": "ExchangePlanRef",
    "БизнесПроцесс": "BusinessProcessRef", "Задача": "TaskRef",
}
# стандартные даты настроек: «НачалоМесяца» → BeginningOfThisMonth …
_STD_DATE = {
    "начало дня": "BeginningOfThisDay", "начало недели": "BeginningOfThisWeek", "начало месяца": "BeginningOfThisMonth",
    "начало квартала": "BeginningOfThisQuarter", "начало года": "BeginningOfThisYear",
    "начало следующего дня": "BeginningOfNextDay", "начало следующей недели": "BeginningOfNextWeek",
    "начало следующего месяца": "BeginningOfNextMonth", "начало следующего квартала": "BeginningOfNextQuarter",
    "начало следующего года": "BeginningOfNextYear",
    "начало прошлого дня": "BeginningOfLastDay", "начало прошлой недели": "BeginningOfLastWeek",
    "начало прошлого месяца": "BeginningOfLastMonth", "начало прошлого квартала": "BeginningOfLastQuarter",
    "начало прошлого года": "BeginningOfLastYear",
}
_AGG = {"сумма": "Сумма", "количество": "Количество", "максимум": "Максимум", "минимум": "Минимум", "среднее": "Среднее"}
_IDENT_CH = r"A-Za-zА-Яа-яЁё_0-9"


def _tpl(name: str) -> str:
    return (_TPL_DIR / name).read_text("utf-8")


def _x(s: str) -> str:
    return escape(str(s))


def _title_xml(text: str, ns: str = "d4p1", indent: str = "\t\t\t") -> str:
    return (f'{indent}<title xmlns:{ns}="{_NS_CORE}" xsi:type="{ns}:LocalStringType">\r\n'
            f"{indent}\t<{ns}:item>\r\n{indent}\t\t<{ns}:lang>ru</{ns}:lang>\r\n{indent}\t\t<{ns}:content>{_x(text)}</{ns}:content>\r\n"
            f"{indent}\t</{ns}:item>\r\n{indent}</title>\r\n")


# ------------------------------------------------------------------ разбор запроса
_STR_LIT = re.compile(r'"(?:[^"]|"")*"')


def _mask(query: str) -> str:
    """Запрос без литералов и комментариев (позиции сохранены) — для поиска ключевых слов и параметров."""
    out = _STR_LIT.sub(lambda m: '"' + " " * (len(m.group(0)) - 2) + '"', query)
    return re.sub(r"//[^\n]*", lambda m: " " * len(m.group(0)), out)


def _split_depth0(text: str, sep: str = ",") -> list[tuple[int, int]]:
    spans, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        depth += (ch == "(") - (ch == ")")
        if ch == sep and depth == 0:
            spans.append((start, i))
            start = i + 1
    spans.append((start, len(text)))
    return spans


def query_fields(query: str) -> list[str]:
    """Имена колонок результата последнего запроса пакета (без ПОМЕСТИТЬ) — по списку выбора первого ВЫБРАТЬ."""
    masked = _mask(query)
    stmts = [(a, b) for a, b in _split_depth0(masked, ";") if re.search(r"(?i)\b(выбрать|select)\b", masked[a:b])]
    final = [(a, b) for a, b in stmts if not re.search(r"(?i)\b(поместить|into)\b", masked[a:b])]
    if not final:
        raise BuildError("В пакете запросов нет итогового ВЫБРАТЬ без ПОМЕСТИТЬ — результат отчёта должен вернуть последний запрос.")
    a, b = final[-1]
    body, mbody = query[a:b], masked[a:b]
    m = re.search(r"(?i)\b(?:выбрать|select)\b(\s+(?:разрешенные|allowed|различные|distinct|первые\s+\d+|top\s+\d+))*", mbody)
    start = m.end()
    depth, end = 0, len(mbody)
    for mi in re.finditer(r"\(|\)|(?i:\b(?:из|from|поместить|into|где|where|сгруппировать|упорядочить|итоги|объединить)\b)", mbody[start:]):
        t = mi.group(0)
        if t == "(":
            depth += 1
        elif t == ")":
            depth -= 1
        elif depth == 0:
            end = start + mi.start()
            break
    names: list[str] = []
    for s, e in _split_depth0(mbody[start:end]):
        item = body[start + s:start + e].strip()
        mitem = mbody[start + s:start + e].strip()
        if not item:
            continue
        if item == "*" or item.endswith(".*"):
            raise BuildError("В запросе «*» — перечисли поля явно или передай fields.")
        ma = list(re.finditer(rf"(?i)\bкак\s+([{_IDENT_CH}]+)\s*$|\bas\s+([{_IDENT_CH}]+)\s*$", mitem))
        if ma:
            names.append(next(g for g in ma[-1].groups() if g))
            continue
        if not re.fullmatch(rf"[{_IDENT_CH}]+(?:\.[{_IDENT_CH}]+)*", mitem) or mitem[0].isdigit():
            raise BuildError(f"Колонка «{item[:40]}» без псевдонима — добавь «КАК Имя» или передай fields.")
        names.append(mitem.rsplit(".", 1)[-1])
    dup = {n.lower() for n in names if [x.lower() for x in names].count(n.lower()) > 1}
    if dup:
        raise BuildError(f"Повторяются имена колонок: {', '.join(sorted(dup))}")
    return names


def query_parameters(query: str) -> list[str]:
    seen: list[str] = []
    for m in re.finditer(rf"&([{_IDENT_CH}]+)", _mask(query)):
        if m.group(1).lower() not in [s.lower() for s in seen]:
            seen.append(m.group(1))
    return seen


# ------------------------------------------------------------------ части схемы
def _norm_fields(spec: dict[str, Any], query: str) -> list[dict[str, str]]:
    raw = spec.get("fields") or query_fields(query)
    out: list[dict[str, str]] = []
    for f in raw:
        d = {"name": f} if isinstance(f, str) else dict(f)
        d["name"] = _ident(d.get("name", ""), "Поле")
        d["title"] = (d.get("title") or d.get("synonym") or "").strip()
        out.append(d)
    names = [d["name"].lower() for d in out]
    if len(set(names)) != len(names):
        raise BuildError("Поля отчёта повторяются")
    return out


def _dcs_type(type_text: str) -> tuple[str, str]:
    """Тип параметра → (XML valueType, вид значения: date | decimal | string | boolean | ref)."""
    parts = B._split_types(type_text or "")
    if len(parts) != 1:
        raise BuildError(f"Тип параметра «{type_text}»: составные типы не поддержаны — укажи один тип")
    t = parts[0]
    low = t.lower().replace("ё", "е")
    pre = f'\t\t\t<valueType>\r\n\t\t\t\t<Type xmlns="{_NS_CORE}"%s>%s</Type>\r\n'
    if low in ("дата", "date", "датавремя", "datetime", "время", "time"):
        frac = {"дата": "Date", "date": "Date", "время": "Time", "time": "Time"}.get(low, "DateTime")
        return (pre % ("", "xs:dateTime") + f'\t\t\t\t<DateQualifiers xmlns="{_NS_CORE}">\r\n\t\t\t\t\t<DateFractions>{frac}</DateFractions>\r\n'
                "\t\t\t\t</DateQualifiers>\r\n\t\t\t</valueType>\r\n", "date")
    if low in ("булево", "boolean", "bool"):
        return pre % ("", "xs:boolean") + "\t\t\t</valueType>\r\n", "boolean"
    m = re.fullmatch(r"(?:строка|string)(?:\s*\(\s*(\d+)\s*\))?", low)
    if m:
        n = int(m.group(1) or 0)
        return (pre % ("", "xs:string") + f'\t\t\t\t<StringQualifiers xmlns="{_NS_CORE}">\r\n\t\t\t\t\t<Length>{n}</Length>\r\n'
                "\t\t\t\t\t<AllowedLength>Variable</AllowedLength>\r\n\t\t\t\t</StringQualifiers>\r\n\t\t\t</valueType>\r\n", "string")
    m = re.fullmatch(r"(?:число|number)(?:\s*\(\s*(\d+)\s*(?:,\s*(\d+))?\s*(?:,\s*(неотр\w*|nonneg\w*))?\s*\))?", low)
    if m:
        dg, fr = int(m.group(1) or 15), int(m.group(2) or 0)
        if dg > 38 or fr > dg:
            raise BuildError(f"Тип «{t}»: разрядность до 38, дробная часть не больше разрядности")
        sign = "Nonnegative" if m.group(3) else "Any"
        return (pre % ("", "xs:decimal") + f'\t\t\t\t<NumberQualifiers xmlns="{_NS_CORE}">\r\n\t\t\t\t\t<Digits>{dg}</Digits>\r\n'
                f"\t\t\t\t\t<FractionDigits>{fr}</FractionDigits>\r\n\t\t\t\t\t<AllowedSign>{sign}</AllowedSign>\r\n"
                "\t\t\t\t</NumberQualifiers>\r\n\t\t\t</valueType>\r\n", "decimal")
    m = B._REF_RE.match(t.replace(" ", ""))
    if m:
        ref = B._resolve_ref(m.group(1), m.group(2))
        ctx = B._REF_CTX.get()
        if ctx is not None:
            ctx.setdefault("refs", {})[ref["type_id"]] = ref
        kind = next(k for k in _REF_XML if k.lower() == m.group(1).lower())
        return (f'\t\t\t<valueType>\r\n\t\t\t\t<Type xmlns="{_NS_CORE}" xmlns:d5p1="{_NS_CFG}">d5p1:{_REF_XML[kind]}.{ref["name"]}</Type>\r\n'
                "\t\t\t</valueType>\r\n", "ref")
    raise BuildError(f"Тип параметра «{t}» не поддержан: Строка(N), Число(N,M), Булево, Дата, ДатаВремя, Время или ссылка на объект конфигурации.")


def _iso(value: Any) -> str:
    s = str(value).strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}:\d{2}:\d{2}))?", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T{m.group(4) or '00:00:00'}"
    m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", s)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}T00:00:00"
    raise BuildError(f"Дата «{value}»: нужен формат ГГГГ-ММ-ДД, ДД.ММ.ГГГГ или «Начало месяца/года/…»")


def _param(p: dict[str, Any]) -> tuple[str, str]:
    """(XML параметра схемы, XML значения для настроек или '')."""
    name = _ident(p.get("name", ""), "Параметр")
    vt, kind = _dcs_type(p.get("type", ""))
    title = (p.get("title") or p.get("synonym") or "").strip()
    val = p.get("value")
    has = val not in (None, "")
    schema_val, std, lit = '\t\t\t<value xsi:nil="true"/>\r\n', "", ""
    if kind == "date":
        schema_val = '\t\t\t<value xsi:type="xs:dateTime">0001-01-01T00:00:00</value>\r\n'
        if has:
            key = re.sub(r"\s+", " ", str(val).lower().replace("ё", "е").replace("_", " ")).strip()
            key = re.sub(r"(начало)(дня|недели|месяца|квартала|года)", r"\1 \2", key)
            std = _STD_DATE.get(key) or (str(val) if re.fullmatch(r"BeginningOf(This|Next|Last)(Day|Week|Month|Quarter|Year)", str(val)) else "")
            lit = "" if std else _iso(val)
            if lit:
                schema_val = f'\t\t\t<value xsi:type="xs:dateTime">{lit}</value>\r\n'
    elif kind == "decimal":
        schema_val = f'\t\t\t<value xsi:type="xs:decimal">{_x(val if has else 0)}</value>\r\n'
    elif kind == "string":
        schema_val = f'\t\t\t<value xsi:type="xs:string">{_x(val) if has else ""}</value>\r\n'
    elif kind == "boolean":
        b = str(val).strip().lower() in ("1", "true", "истина", "да") if has else False
        val = "true" if b else "false"
        has, schema_val = True, f'\t\t\t<value xsi:type="xs:boolean">{val}</value>\r\n'
    elif has and ES.is_design_time_value(val):
        val = ES.check_ref_value(str(val))
        schema_val = f'\t\t\t<value xmlns:dcscor="http://v8.1c.ru/8.1/data-composition-system/core" xsi:type="dcscor:DesignTimeValue">{_x(val)}</value>\r\n'
    elif has:
        raise BuildError(f"Параметр «{name}»: ссылочное значение задаётся как «Класс.Объект.Значение» (Справочник.Контрагенты.ПустаяСсылка, Перечисление.Статусы.Закрыт); произвольный элемент справочника по имени не задать — его выберет пользователь")
    xml = (f"\t\t<parameter>\r\n\t\t\t<name>{name}</name>\r\n" + (_title_xml(title) if title else "") + vt + schema_val
           + f"\t\t\t<useRestriction>false</useRestriction>\r\n\t\t\t<inputParameters/>\r\n\t\t</parameter>\r\n")
    setting = ""
    if has and (kind != "ref" or ES.is_design_time_value(val)):
        if std:
            v = (f'<dcscor:value xsi:type="v8:StandardBeginningDate">\r\n\t\t\t\t<v8:variant xsi:type="v8:StandardBeginningDateVariant">'
                 f"{std}</v8:variant>\r\n\t\t\t</dcscor:value>")
        elif kind == "ref":
            v = f'<dcscor:value xsi:type="dcscor:DesignTimeValue">{_x(val)}</dcscor:value>'
        else:
            xt = {"date": "xs:dateTime", "decimal": "xs:decimal", "string": "xs:string", "boolean": "xs:boolean"}[kind]
            v = f'<dcscor:value xsi:type="{xt}">{lit or _x(val)}</dcscor:value>'
        setting = ('\t\t<dcscor:item xsi:type="SettingsParameterValue">\r\n'
                   f"\t\t\t<dcscor:parameter>{name}</dcscor:parameter>\r\n\t\t\t{v}\r\n\t\t</dcscor:item>\r\n")
    return xml, setting


def _resources(spec: dict[str, Any], field_names: list[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    low = {n.lower(): n for n in field_names}
    for r in spec.get("resources") or []:
        d = {"name": r} if isinstance(r, str) else dict(r)
        name = low.get(str(d.get("name", "")).lower())
        if not name:
            raise BuildError(f"Ресурс «{d.get('name')}» — такого поля нет среди колонок запроса: {', '.join(field_names)}")
        expr = (d.get("expression") or "").strip()
        if not expr:
            agg = _AGG.get(str(d.get("aggregate") or "сумма").lower())
            if not agg:
                raise BuildError(f"Агрегат «{d.get('aggregate')}»: доступны {', '.join(_AGG.values())}")
            expr = f"{agg}({name})"
        out.append((name, expr))
    return out


def build_dcs(spec: dict[str, Any]) -> bytes:
    """Содержимое макета СКД: заголовок + схема + настройки варианта + пустая схема-хвост."""
    raw_sets = spec.get("datasets") or [{"name": "НаборДанных1", "query": spec.get("query"), "fields": spec.get("fields")}]
    sets: list[dict[str, Any]] = []

    def parse_set(ds: dict[str, Any], i: int, allow_union: bool = True) -> dict[str, Any]:
        nm = _ident(ds.get("name") or f"НаборДанных{i}", "Набор данных")
        if ds.get("union"):
            if not allow_union:
                raise BuildError(f"Набор «{nm}»: объединение внутри объединения не поддержано")
            subs = [parse_set(x, k, allow_union=False) for k, x in enumerate(ds["union"], 1)]
            if len(subs) < 2:
                raise BuildError(f"Набор «{nm}»: в объединении нужно минимум два набора")
            if len({x["name"].lower() for x in subs}) != len(subs):
                raise BuildError(f"Набор «{nm}»: имена наборов объединения повторяются")
            fields: list[dict[str, str]] = []
            for sub in subs:
                for f in sub["fields"]:
                    if f["name"].lower() not in [g["name"].lower() for g in fields]:
                        fields.append(f)
            return {"kind": "union", "name": nm, "items": subs, "fields": fields, "query": "\n".join(x["query"] for x in subs)}
        if ds.get("object"):
            fl = ds.get("fields") or []
            if not fl:
                raise BuildError(f"Набор-объект «{nm}»: нужны поля fields=[{{name, title, type}}] — данные подставляются из кода (ВнешниеНаборыДанных)")
            fields = []
            for f in fl:
                d = {"name": f} if isinstance(f, str) else dict(f)
                d["name"] = _ident(d.get("name", ""), "Поле")
                d["title"] = (d.get("title") or d.get("synonym") or "").strip()
                if d.get("type"):
                    d["value_type"] = _dcs_type(d["type"])[0]
                fields.append(d)
            if len({f["name"].lower() for f in fields}) != len(fields):
                raise BuildError(f"Набор-объект «{nm}»: поля повторяются")
            return {"kind": "object", "name": nm, "object": _ident(ds["object"], "Имя объекта"), "fields": fields, "query": ""}
        q = (ds.get("query") or "").strip()
        if not q or not re.search(r"(?i)\b(выбрать|select)\b", q):
            raise BuildError(f"Набор «{nm}»: нужен текст запроса (query) — ВЫБРАТЬ … ИЗ …, параметры как &Имя; либо object+fields, либо union.")
        return {"kind": "query", "name": nm, "query": q, "fields": _norm_fields({"fields": ds.get("fields")}, q)}

    for i, ds in enumerate(raw_sets, 1):
        st = parse_set(ds, i)
        if st["name"].lower() in [s["name"].lower() for s in sets]:
            raise BuildError(f"Набор данных «{st['name']}» указан дважды")
        sets.append(st)
    query = "\n".join(s["query"] for s in sets if s["query"])
    fnames: list[str] = []
    for s in sets:
        for f in s["fields"]:
            if f["name"].lower() not in [n.lower() for n in fnames]:
                fnames.append(f["name"])
    calcs: list[dict[str, str]] = []
    for c in spec.get("calculated_fields") or []:
        d = dict(c)
        d["name"] = _ident(d.get("name", ""), "Вычисляемое поле")
        d["expression"] = (d.get("expression") or "").strip()
        d["title"] = (d.get("title") or d.get("synonym") or "").strip()
        if not d["expression"]:
            raise BuildError(f"Вычисляемое поле «{d['name']}»: нужно выражение (expression)")
        if d["name"].lower() in [n.lower() for n in fnames]:
            raise BuildError(f"Вычисляемое поле «{d['name']}» совпадает с колонкой запроса — выбери другое имя")
        calcs.append(d)
        fnames.append(d["name"])
    links = []
    setnames = {s["name"].lower(): s["name"] for s in sets}
    for lk in spec.get("links") or []:
        a, b = setnames.get(str(lk.get("source", "")).lower()), setnames.get(str(lk.get("dest", "")).lower())
        se, de = (lk.get("source_expression") or "").strip(), (lk.get("dest_expression") or "").strip()
        if not (a and b and se and de):
            raise BuildError(f"Связь {lk}: нужны source и dest (имена наборов: {', '.join(s['name'] for s in sets)}), source_expression, dest_expression")
        links.append((a, b, se, de))
    declared = spec.get("parameters") or []
    pxml, psettings, pnames = [], [], []
    for p in declared:
        x, s = _param(dict(p))
        pxml.append(x)
        psettings.append(s)
        pnames.append(_ident(p.get("name", ""), "Параметр").lower())
    missing = [n for n in query_parameters(query) if n.lower() not in pnames]
    if missing:
        raise BuildError("Параметры запроса не описаны в parameters: " + ", ".join("&" + n for n in missing)
                         + " — укажи для каждого {name, title, type, value}.")
    res = _resources(spec, fnames)
    low = {n.lower(): n for n in fnames}
    group_by = []
    for g in spec.get("group_by") or []:
        n = low.get(str(g).lower())
        if not n:
            raise BuildError(f"Группировка по «{g}» — такого поля нет среди колонок запроса: {', '.join(fnames)}")
        group_by.append(n)
    order = []
    for o in spec.get("order") or []:
        parts = str(o).split()
        n = low.get(parts[0].lower()) if parts else None
        if not n:
            raise BuildError(f"Сортировка по «{o}» — такого поля нет среди колонок запроса: {', '.join(fnames)}")
        order.append((n, "Desc" if len(parts) > 1 and parts[1].lower().startswith(("убыв", "desc")) else "Asc"))

    title = spec.get("form_title") or spec.get("synonym") or spec.get("name") or ""
    low_all = dict(low)
    main = {k: spec.get(k) for k in ("order", "filters", "structure", "appearance", "user_fields", "output", "select")}
    main["group_by"] = group_by
    variants = [("Основной", "Основной", main)]
    for i, vs in enumerate(spec.get("variants") or [], 2):
        vn = _ident(vs.get("name") or f"Вариант{i}", "Имя варианта")
        if vn.lower() in [x[0].lower() for x in variants]:
            raise BuildError(f"Вариант «{vn}» указан дважды")
        gb = []
        for g in vs.get("group_by") or []:
            n = low.get(str(g).lower())
            if not n:
                raise BuildError(f"Вариант «{vn}»: группировка по «{g}» — такого поля нет среди колонок запроса: {', '.join(fnames)}")
            gb.append(n)
        variants.append((vn, vs.get("presentation") or vs.get("title") or vn, {**vs, "group_by": gb}))

    def field_xml(f: dict[str, str], ind: str) -> str:
        vt = f.get("value_type") or ""
        return (f'{ind}<field xsi:type="DataSetFieldField">\r\n{ind}\t<dataPath>{f["name"]}</dataPath>\r\n{ind}\t<field>{f["name"]}</field>\r\n'
                + (_title_xml(f["title"], "d5p1", ind + "\t") if f["title"] else "") + (vt.replace("\t\t\t", ind + "\t", 1) if vt else "")
                + f"{ind}\t<appearance/>\r\n{ind}\t<inputParameters/>\r\n{ind}</field>\r\n")

    def set_xml(s: dict[str, Any], ind: str, tag: str = "dataSet") -> str:
        fx = "".join(field_xml(f, ind + "\t") for f in s["fields"])
        if s["kind"] == "union":
            items = "".join(set_xml(x, ind + "\t", "item") for x in s["items"])
            return f'{ind}<{tag} xsi:type="DataSetUnion">\r\n{ind}\t<name>{s["name"]}</name>\r\n{fx}{items}{ind}</{tag}>\r\n'
        if s["kind"] == "object":
            return (f'{ind}<{tag} xsi:type="DataSetObject">\r\n{ind}\t<name>{s["name"]}</name>\r\n{fx}{ind}\t<dataSource>ИсточникДанных1</dataSource>\r\n'
                    f'{ind}\t<objectName>{_x(s["object"])}</objectName>\r\n{ind}</{tag}>\r\n')
        return (f'{ind}<{tag} xsi:type="DataSetQuery">\r\n{ind}\t<name>{s["name"]}</name>\r\n{fx}{ind}\t<dataSource>ИсточникДанных1</dataSource>\r\n'
                f"{ind}\t<query>{_x(s['query'])}</query>\r\n{ind}\t<useQueryGroupIfPossible>false</useQueryGroupIfPossible>\r\n{ind}</{tag}>\r\n")

    dsx = "".join(set_xml(s, "\t\t") for s in sets)
    lkx = "".join(f"\t\t<dataSetLink>\r\n\t\t\t<sourceDataSet>{a}</sourceDataSet>\r\n\t\t\t<destinationDataSet>{b}</destinationDataSet>\r\n"
                  f"\t\t\t<sourceExpression>{_x(se)}</sourceExpression>\r\n\t\t\t<destinationExpression>{_x(de)}</destinationExpression>\r\n"
                  "\t\t</dataSetLink>\r\n" for a, b, se, de in links)
    cfx = "".join(f"\t\t<calculatedField>\r\n\t\t\t<dataPath>{c['name']}</dataPath>\r\n\t\t\t<expression>{_x(c['expression'])}</expression>\r\n"
                  + (_title_xml(c["title"]) if c["title"] else "") + "\t\t\t<appearance/>\r\n\t\t</calculatedField>\r\n" for c in calcs)
    schema = (
        f'{_NS_HEAD}<SchemaFile xmlns="" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\r\n'
        f'\t<dataCompositionSchema xmlns="{_NS_SCHEMA}">\r\n'
        "\t\t<dataSource>\r\n\t\t\t<name>ИсточникДанных1</name>\r\n\t\t\t<dataSourceType>Local</dataSourceType>\r\n\t\t</dataSource>\r\n"
        + dsx + lkx + cfx
        + "".join(f"\t\t<totalField>\r\n\t\t\t<dataPath>{n}</dataPath>\r\n\t\t\t<expression>{_x(e)}</expression>\r\n\t\t</totalField>\r\n" for n, e in res)
        + "".join(pxml)
        + "".join(f'\t\t<settingsVariant>\r\n\t\t\t<name xmlns="http://v8.1c.ru/8.1/data-composition-system/settings">{_x(vn)}</name>\r\n'
                  f'\t\t\t<presentation xmlns="http://v8.1c.ru/8.1/data-composition-system/settings" xsi:type="xs:string">{_x(vp)}</presentation>\r\n'
                  "\t\t</settingsVariant>\r\n" for vn, vp, _ in variants)
        + "\t</dataCompositionSchema>\r\n</SchemaFile>"
    )
    settings_list = [ES.variant_xml(v, head=_NS_HEAD, names=low_all, field_names=fnames, data_params="".join(psettings), title=v.get("title") or title)
                     for _, _, v in variants]
    tail = (f'{_NS_HEAD}<SchemaFile xmlns="" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\r\n'
            f'\t<dataCompositionSchema xmlns="{_NS_SCHEMA}"/>\r\n</SchemaFile>')
    s1, s3 = (("﻿" + x).encode("utf-8") for x in (schema, tail))
    sx = [("﻿" + x).encode("utf-8") for x in settings_list]
    head = struct.pack("<3I", 0, len(sx), len(s1)) + struct.pack("<I", 0) + b"".join(struct.pack("<2I", len(x), 0) for x in sx)
    return head + s1 + b"".join(sx) + s3


# ------------------------------------------------------------------ сборка файла
def _copyinfo(obj_id: str, obj_name: str, type_id: str, forms: list[tuple[str, str]], tpl_id: str, refs: dict[str, dict[str, str]],
              bound: list[tuple[str, str]] | None = None, tables: list[dict[str, Any]] | None = None) -> str:
    nl = "\n"
    bound, tables = bound or [], tables or []
    ents = [(obj_id, f'1,{nl}{{{_CLS_REPORT},"{_q(obj_name)}"}}'), (tpl_id, f'1,{nl}{{{_CLS_TEMPLATE},"{_TPL_NAME}"}}')]
    ents += [(fid, f'1,{nl}{{{_CLS_FORM},"{_q(fname)}"}}') for fid, fname in forms]
    ents += [(r["obj_id"], f'1,{nl}{{{r["cls"]},"{_q(r["name"])}"}}') for r in refs.values()]
    ents += [(aid, f'1,{nl}{{{_CLS_ATTR},"{_q(an)}"}}') for aid, an in bound]
    for t in tables:
        ents.append((t["id"], f'1,{nl}{{{_CLS_TS},"{_q(t["name"])}"}}'))
        for c in t["cols"]:
            ents.append((c["id"], f'2,{nl}{{{_CLS_TS},"{_q(t["name"])}"}},{nl}{{{_CLS_TS_COL},"{_q(c["name"])}"}}'))
    objs = f",{nl}".join(f"{{{i},{i},{body}{nl}}}" for i, body in sorted(ents))
    types = sorted([(type_id, obj_id, 0)] + [(t["type"], t["id"], 0) for t in tables] + [(r["type_id"], r["obj_id"], 1) for r in refs.values()])
    types_blk = f",{nl}".join(f"{{{a},{b},{f}}}" for a, b, f in types)
    pids = sorted([aid for aid, _ in bound] + [t["id"] for t in tables])
    paths = f",{nl}".join(f"{{{nl}{{0,{p}}},{type_id},{nl}{{-1}}{nl}}}" for p in pids)
    paths_blk = f"{{{len(pids)},{nl}{paths}{nl}}}" if pids else "{0}"
    return f"{{4,{nl}{{{len(ents)},{nl}{objs}{nl}}},{nl}{{{len(types)},{nl}{types_blk}{nl}}},{nl}{paths_blk},{nl}{{0,0}},{nl}{{0}}{nl}}}"


def build_erf(spec: dict[str, Any], *, cf_root: str | Path | None = None) -> bytes:
    """
    spec: name, synonym, comment, query (текст запроса СКД), fields[{name,title}] (иначе из запроса),
    parameters[{name,title,type,value}], resources[имя | {name,aggregate|expression}], group_by[имена],
    order["Поле" | "Поле Убыв"], form_title, module (необязательный модуль объекта-отчёта).
    """
    token = B._REF_CTX.set({"root": cf_root, "refs": {}})
    try:
        return _build(spec)
    finally:
        B._REF_CTX.reset(token)


_CLS_TS_COL = "c339c860-29e2-11d6-a3c7-0050bae0a776"


def _rep_attr(a: dict[str, Any], attr_id: str, what: str = "Реквизит") -> str:
    """Реквизит/колонка ТЧ отчёта (узел снят с эталона платформы: от обработки отличается хвостом {"S",""})."""
    name = _ident(a.get("name", ""), what)
    return ('{\r\n{0,\r\n{27,\r\n{2,\r\n{3,\r\n{1,0,%s},"%s",\r\n{1,"ru","%s"},"",0,0,%s,0},\r\n%s\r\n},0,\r\n{0},\r\n{0},0,"",0,\r\n'
            '{"U"},\r\n{"U"},0,%s,2,0,\r\n{5006,0},\r\n{3,0,0},\r\n{0,0},0,\r\n{0},\r\n{"S",""},0,0,0}\r\n},0}'
            % (attr_id, _q(name), _q(a.get("synonym") or name), _GUID0, B._pattern(a.get("type", "")), _GUID0))


def _rep_ts(ts: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """(узел метаданных, сведения для формы и copyinfo: id ТЧ, тип таблицы, колонки)."""
    name = _ident(ts.get("name", ""), "Табличная часть")
    cols = ts.get("columns") or []
    if not cols:
        raise BuildError(f"Табличная часть «{name}»: нужна хотя бы одна колонка")
    _dups([c.get("name", "") for c in cols], f"Колонка табличной части «{name}»")
    ts_id, g1 = _guid(), _guid()
    col_ids = [_guid() for _ in cols]
    items = ",\r\n".join(_rep_attr(c, i, "Колонка") for c, i in zip(cols, col_ids))
    node = ('{\r\n{0,\r\n{11,%s,%s,%s,%s,\r\n{0,\r\n{3,\r\n{1,0,%s},"%s",\r\n{1,"ru","%s"},"",0,0,%s,0}\r\n},0,\r\n{0},\r\n{0}\r\n}\r\n},1,\r\n'
            "{%s,%d,\r\n%s\r\n}\r\n}"
            % (g1, _guid(), _guid(), _guid(), ts_id, _q(name), _q(ts.get("synonym") or name), _GUID0, _CLS_TS_COL, len(cols), items))
    info = {"id": ts_id, "type": g1, "name": name, "caption": ts.get("synonym") or name,
            "cols": [{"id": i, "name": c["name"], "caption": c.get("synonym") or c["name"], "pattern": B._pattern(c.get("type", ""))}
                     for c, i in zip(cols, col_ids)]}
    return node, info


def _dups(names: list[str], what: str) -> None:
    seen: set[str] = set()
    for n in names:
        if str(n).lower() in seen:
            raise BuildError(f"{what} «{n}» указан дважды")
        seen.add(str(n).lower())


def _coll(cls: str, items: list[str]) -> str:
    return "{%s,%d,\r\n%s\r\n}" % (cls, len(items), ",\r\n".join(items)) if items else "{%s,0}" % cls


def _variant_menu(spec: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """Подменю «Вариант отчёта» на командной панели: пункт на каждый вариант, нажатие загружает настройки варианта в компоновщик."""
    variants = spec.get("variants") or []
    if not variants:
        return None, ""
    items = [("Основной", "Основной")]
    for i, vs in enumerate(variants, 2):
        vn = _ident(vs.get("name") or f"Вариант{i}", "Имя варианта")
        items.append((vn, vs.get("presentation") or vs.get("title") or vn))
    menu_items, code = [], []
    for vn, vp in items:
        bn = f"Вариант_{vn}"
        menu_items.append({"name": bn, "title": vp})
        code.append(f"Процедура {bn}Нажатие(Кнопка)\r\n\tКомпоновщикНастроек.ЗагрузитьНастройки(СхемаКомпоновкиДанных.ВариантыНастроек.{vn}.Настройки);\r\n"
                    f"КонецПроцедуры\r\n")
    return {"name": "ВариантОтчета", "title": spec.get("variant_menu_title") or "Вариант отчета", "items": menu_items}, "\r\n".join(code)


def _build(spec: dict[str, Any]) -> bytes:
    name = _ident(spec.get("name", ""), "Имя отчёта")
    synonym = (spec.get("synonym") or name).strip()
    module = spec.get("module") or ""
    root_id, obj_id, type_id, value_id, form_id, tpl_id = (_guid() for _ in range(6))
    dcs = build_dcs(spec)
    attr_specs = spec.get("attributes") or []
    _dups([a.get("name", "") for a in attr_specs], "Реквизит")
    _dups([t.get("name", "") for t in spec.get("tabular_sections") or []], "Табличная часть")
    attr_ids = [_guid() for _ in attr_specs]
    attrs = _coll(_CLS_ATTR, [_rep_attr(a, i) for a, i in zip(attr_specs, attr_ids)])
    ts_pairs = [_rep_ts(t) for t in spec.get("tabular_sections") or []]
    tss = _coll(_CLS_TS, [n for n, _ in ts_pairs])
    refs = (B._REF_CTX.get() or {}).get("refs") or {}

    # своя форма: поля реквизитов / таблицы ТЧ / элементы палитры / модуль формы / доп. формы
    form_module = spec.get("form_module") or ""
    bar_buttons = list(spec.get("bar_buttons") or [])
    bar_menus = list(spec.get("bar_menus") or [])
    vmenu, vcode = _variant_menu(spec)
    if vmenu and spec.get("variant_buttons", True):  # в обычной форме отчёта выбора варианта нет — подменю на панели
        bar_menus.append(vmenu)
        form_module = form_module.rstrip() + "\r\n\r\n" + vcode
    show_fields = bool(spec.get("form_fields"))
    controls = spec.get("controls") or []
    fields = [{"name": a["name"], "caption": a.get("synonym") or a["name"], "pattern": B._pattern(a.get("type", "")), "attr_id": i}
              for a, i in zip(attr_specs, attr_ids)] if show_fields else []
    tables = [info for _, info in ts_pairs] if show_fields else []
    extra: list[dict[str, Any]] = []
    for ef in spec.get("extra_forms") or []:
        ef_name = _ident(ef.get("name", ""), "Имя формы")
        if ef_name.lower() in {_FORM_NAME.lower()} | {e["name"].lower() for e in extra}:
            raise BuildError(f"Форма «{ef_name}» указана дважды")
        extra.append({"id": _guid(), "name": ef_name, "title": _q(ef.get("title") or ef_name), "module": ef.get("module") or "",
                      "controls": ef.get("controls") or [], "width": ef.get("width"), "height": ef.get("height"),
                      "panels": ef.get("panels", True)})
    form_ids = [form_id] + [e["id"] for e in extra]
    forms_coll = "{%s,%d,%s}" % (_CLS_FORM, len(form_ids), ",".join(form_ids))
    tpls = B._templates(spec.get("templates") or [], {_TPL_NAME.lower(), _FORM_NAME.lower()} | {e["name"].lower() for e in extra})

    meta = (
        "{1,\r\n{%s},1,\r\n{%s,\r\n{1,\r\n{4,%s,%s,\r\n{0,\r\n{3,\r\n"
        '{1,0,%s},"%s",\r\n{1,"ru","%s"},"%s",0,0,%s,0}\r\n},%s,%s,%s,%s,%s,%s,"",%s,%s},4,\r\n'
        "{%s,%d,%s},\r\n%s,\r\n%s,\r\n%s\r\n}\r\n}\r\n}"
        % (root_id, _CLS_REPORT, type_id, value_id, obj_id, _q(name), _q(synonym), _q(spec.get("comment") or ""), _GUID0,
           form_id, tpl_id, _GUID0, _GUID0, _GUID0, _GUID0, _GUID0, _GUID0,
           _CLS_TEMPLATE, 1 + len(tpls), ",".join([tpl_id] + [t["id"] for t in tpls]), attrs, forms_coll, tss)
    )
    node = lambda i, n, s: '{1,\r\n{1,\r\n{0,\r\n{13,\r\n{3,\r\n{1,0,%s},"%s",\r\n{1,"ru","%s"},"",0,0,%s,0},0,0,\r\n{2,\r\n' \
        '{"#",1708fdaa-cbce-4289-b373-07a5a74bee91,1},\r\n{"#",1708fdaa-cbce-4289-b373-07a5a74bee91,2}\r\n}\r\n},\r\n{0}\r\n}\r\n},0}' % (i, n, s, _GUID0)

    title = _q(spec.get("form_title") or synonym)
    custom = bool(fields or tables or controls or form_module.strip() or spec.get("form_width") or bar_buttons or bar_menus)
    if custom:
        form_text = report_form(title, type_id, fields, tables, B.form_events_of(form_module), form_module, controls, spec.get("form_width"),
                                bar_buttons=bar_buttons, bar_menus=bar_menus)
    else:
        form_text = _tpl("form_report.txt").replace("@@TITLE@@", title).replace("@@TYPE@@", type_id)
    files: dict[str, bytes] = {
        root_id: _deflate(_text(meta)),
        form_id: _deflate(_text(node(form_id, _FORM_NAME, _FORM_SYN))),
        f"{form_id}.0": _deflate(_pack_container([("form", _text(form_text)), ("module", _text(form_module))])),
        tpl_id: _deflate(_text('{1,\r\n{2,6,\r\n{3,\r\n{1,0,%s},"%s",\r\n{1,"ru","%s"},"",0,0,%s,0}\r\n},0}' % (tpl_id, _TPL_NAME, _TPL_SYN, _GUID0))),
        f"{tpl_id}.0": _deflate(dcs),
        "root": _deflate(_text("{2,%s,}" % root_id)),
        "version": _deflate(_text("{\r\n{216,0,\r\n{80214,0}\r\n}\r\n}")),
    }
    for t in tpls:
        files[t["id"]] = _deflate(_text(t["meta"]))
        files[f'{t["id"]}.0'] = _deflate(t["blob"])
    for e in extra:
        fmod = e["module"]
        files[e["id"]] = _deflate(_text(node(e["id"], e["name"], e["name"])))
        files[f'{e["id"]}.0'] = _deflate(_pack_container([("form", _text(B._form_with_fields(
            e["title"], type_id, [], events=B.form_events_of(fmod), form_module=fmod, controls=e["controls"], width=e["width"],
            height_min=e["height"], panels=e["panels"]))), ("module", _text(fmod))]))
    forms = [(form_id, _FORM_NAME)] + [(e["id"], e["name"]) for e in extra]
    files["copyinfo"] = _deflate(_text(_copyinfo(obj_id, name, type_id, forms, tpl_id, refs,
                                                 [(f["attr_id"], f["name"]) for f in fields], tables)))
    if module.strip():
        files[f"{obj_id}.0"] = _deflate(_pack_container([("info", _text('{3,1,0,"",0}')), ("text", _text(module))]))
    order = sorted(files)
    pairs = ",".join(f'"{n}",{_guid()}' for n in [""] + order + ["versions"])
    files["versions"] = _deflate(_text("{1,%d,%s}" % (len(order) + 2, pairs)))
    return _pack_container([(n, files[n]) for n in sorted(files)])


def dump_template(erf: Path, name: str = _TPL_NAME) -> str:
    """Выгрузка отчёта платформой в XML и текст макета СКД: платформа сама разбирает схему и настройки, потому по выгрузке видно, что она приняла."""
    import subprocess, tempfile
    exe = B._platform_exe()
    if exe is None:
        return ""
    with tempfile.TemporaryDirectory(prefix="erf_dump_") as tmp:
        ib, out = Path(tmp) / "ib", Path(tmp) / "x"
        subprocess.run([str(exe), "CREATEINFOBASE", f"File={ib}"], timeout=180, capture_output=True)
        subprocess.run([str(exe), "DESIGNER", "/F", str(ib), "/DisableStartupDialogs", "/DumpExternalDataProcessorOrReportToFiles",
                        str(out / "root.xml"), str(erf), "/Out", str(Path(tmp) / "log.txt")], timeout=300, capture_output=True)
        p = out / "root" / "Templates" / name / "Ext" / "Template.xml"
        return p.read_text("utf-8-sig") if p.is_file() else ""


def dropped_parameters(erf: Path, spec: dict[str, Any]) -> list[str]:
    """Имена параметров вывода/оформления из наших настроек, которых нет в выгрузке платформой (она молча выбрасывает неизвестные)."""
    raw = build_dcs(spec)
    import struct as _st
    n, ls = _st.unpack("<I", raw[4:8])[0], _st.unpack("<I", raw[8:12])[0]
    pos, lens = 24 + ls - 0, []
    hdr = 12 + 4
    for k in range(n):
        lens.append(_st.unpack("<I", raw[hdr + 8 * k: hdr + 8 * k + 4])[0])
    off = 12 + 4 + 8 * n + ls
    ours: set[str] = set()
    for ln in lens:
        ours |= set(re.findall(r"<dcscor:parameter>([^<]+)</dcscor:parameter>", raw[off: off + ln].decode("utf-8-sig")))
        off += ln
    ours -= {p["name"] for p in spec.get("parameters") or []}  # параметры данных проверяет отдельный разбор
    dump = dump_template(erf)
    if not dump:
        return []
    theirs = set(re.findall(r"<dcscor:parameter>([^<]+)</dcscor:parameter>", dump))
    return sorted(ours - theirs)


def write_erf(spec: dict[str, Any], out_dir: Path, *, platform_check: bool = True,
              cf_root: str | Path | None = None) -> dict[str, Any]:
    """Собрать, записать в out_dir/<Имя>.erf и проверить (свой разбор + выгрузка платформой, если она есть)."""
    data = build_erf(spec, cf_root=cf_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_ident(spec.get('name', ''), 'Имя отчёта')}.erf"
    path.write_bytes(data)
    verify = B.verify_epf(path, platform=platform_check)
    if platform_check and (verify.get("platform") or {}).get("ok"):
        dropped = dropped_parameters(path, spec)
        if dropped:
            verify["dropped_parameters"] = dropped  # платформа не знает этих параметров вывода/оформления и отбросила их при загрузке
    return {"path": path, "size": len(data), "verify": verify}
