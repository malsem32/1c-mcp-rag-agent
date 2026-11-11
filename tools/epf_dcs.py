# -*- coding: utf-8 -*-
"""
СКД внешнего отчёта (.erf): наборы, поля, запросы, ресурсы, параметры, структура
+ статический анализ запросов. Без привязки к конфигурации: только платформенные паттерны.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

_NS_DCS = "{http://v8.1c.ru/8.1/data-composition-system/schema}"
_NS_SET = "{http://v8.1c.ru/8.1/data-composition-system/settings}"
_NS_CORE = "{http://v8.1c.ru/8.1/data/core}"
_XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"


def split_xml_docs(data: bytes) -> list[tuple[str, str]]:
    """Несколько XML, склеенных в одном потоке (и бинарный префикс перед ними)."""
    starts = [m.start() for m in re.finditer(rb"<\?xml", data)]
    if not starts:
        return []
    out: list[tuple[str, str]] = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(data)
        seg = data[s:e].rstrip(b"\xef\xbb\xbf\x00 \r\n\t")
        try:
            text = seg.decode("utf-8")
        except UnicodeDecodeError:
            text = seg.decode("utf-8", errors="replace")
        m = re.search(r"<(?:\w+:)?(\w+)[\s>]", text[text.find("?>") + 2 :])
        root = m.group(1) if m else "xml"
        out.append((root, text))
    return out


def _loc(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el: ET.Element | None, name: str) -> ET.Element | None:
    if el is None:
        return None
    return next((ch for ch in el if _loc(ch.tag) == name), None)


def _children(el: ET.Element | None, name: str) -> list[ET.Element]:
    if el is None:
        return []
    return [ch for ch in el if _loc(ch.tag) == name]


def _txt(el: ET.Element | None, name: str) -> str:
    ch = _child(el, name)
    return (ch.text or "").strip() if ch is not None else ""


def _title(el: ET.Element, tag: str = "title") -> str:
    ch = _child(el, tag)
    if ch is None:
        return ""
    for c in ch.iter():
        if _loc(c.tag) == "content" and c.text:
            return c.text.strip()
    return (ch.text or "").strip()


def _xsi(el: ET.Element) -> str:
    return (el.get(_XSI_TYPE) or "").split(":")[-1]


def _types(el: ET.Element | None) -> list[str]:
    """valueType → ['dateTime', 'EnumRef.X', 'String(50)']."""
    vt = _child(el, "valueType")
    if vt is None:
        return []
    out: list[str] = []
    for t in vt.iter():
        if _loc(t.tag) in ("Type", "TypeSet") and t.text:
            out.append(t.text.strip().split(":")[-1])
    sq = next((c for c in vt.iter() if _loc(c.tag) == "StringQualifiers"), None)
    if sq is not None and "string" in out:
        ln = _txt(sq, "Length")
        out[out.index("string")] = f"string({ln or '∞'})"
    nq = next((c for c in vt.iter() if _loc(c.tag) == "NumberQualifiers"), None)
    if nq is not None and "decimal" in out:
        out[out.index("decimal")] = f"decimal({_txt(nq, 'Digits')},{_txt(nq, 'FractionDigits')})"
    dq = next((c for c in vt.iter() if _loc(c.tag) == "DateFractions"), None)
    if dq is not None and "dateTime" in out and (dq.text or "") != "DateTime":
        out[out.index("dateTime")] = f"dateTime({(dq.text or '').strip()})"
    return out


def _value(el: ET.Element | None) -> str:
    """Значение параметра/отбора: простое, стандартная дата/период, список."""
    if el is None:
        return ""
    var = next((x for x in el.iter() if _loc(x.tag) == "variant"), None)
    if var is not None and (var.text or "").strip():
        return (var.text or "").strip()
    sd, ed = _txt(el, "startDate"), _txt(el, "endDate")
    if sd or ed:
        return f"{sd}..{ed}"
    items = [(_txt(i, "value") or (i.text or "").strip()) for i in el if _loc(i.tag) == "item"]
    if items:
        return "[" + ", ".join(x for x in items if x) + "]"
    return (el.text or "").strip()


def _flags(el: ET.Element | None) -> dict[str, str]:
    """<role>/<useRestriction>: дочерние теги → значения (только непустые/true)."""
    if el is None:
        return {}
    out: dict[str, str] = {}
    for c in el:
        v = (c.text or "").strip()
        if v and v != "false":
            out[_loc(c.tag)] = v
    return out


def _parse_field(f: ET.Element) -> dict[str, Any]:
    d: dict[str, Any] = {
        "path": _txt(f, "dataPath"),
        "field": _txt(f, "field"),
        "title": _title(f),
        "kind": _xsi(f).replace("DataSetField", "") or "Field",
    }
    role = _flags(_child(f, "role"))
    if role:
        d["role"] = role
    d["dimension"] = role.get("dimension") == "true"
    if "periodNumber" in role:
        d["period"] = {"number": role.get("periodNumber"), "type": role.get("periodType", "Main")}
    restr = _flags(_child(f, "useRestriction"))
    if restr:
        d["use_restriction"] = sorted(restr)
    restr_a = _flags(_child(f, "attributeUseRestriction"))
    if restr_a:
        d["attribute_use_restriction"] = sorted(restr_a)
    types = _types(f)
    if types:
        d["types"] = types
    for tag, key in (
        ("presentationExpression", "presentation_expression"),
        ("orderExpression", "order_expression"),
    ):
        el = _child(f, tag)
        if el is not None:
            v = _txt(el, "expression") or (el.text or "").strip()
            if v:
                d[key] = v
    app = _child(f, "appearance")
    if app is not None and len(app):
        d["appearance"] = [
            f"{_txt(i, 'parameter')}={_value(_child(i, 'value'))}" for i in app if _loc(i.tag) == "item"
        ]
    ip = _child(f, "inputParameters")
    if ip is not None and len(ip):
        d["input_parameters"] = [_txt(i, "parameter") for i in ip if _loc(i.tag) == "item"]
    return d


def _parse_dataset(el: ET.Element) -> dict[str, Any]:
    kind = _xsi(el).replace("DataSet", "") or "Query"
    fields = [_parse_field(f) for f in _children(el, "field")]
    ds: dict[str, Any] = {
        "name": _txt(el, "name"),
        "type": kind,
        "fields": fields,
        "query": _txt(el, "query"),
        "data_source": _txt(el, "dataSource"),
    }
    if kind == "Query":
        ds["auto_fill_fields"] = _txt(el, "autoFillFields") != "false"
        ds["use_query_group_if_possible"] = _txt(el, "useQueryGroupIfPossible") != "false"
    if kind == "Object":
        ds["object_name"] = _txt(el, "objectName")
    nested = [_parse_dataset(i) for i in _children(el, "item")]
    if nested:
        ds["items"] = nested
    return ds


def _parse_parameter(el: ET.Element) -> dict[str, Any]:
    p: dict[str, Any] = {
        "name": _txt(el, "name"),
        "title": _title(el),
        "expression": _txt(el, "expression"),
        "value": _value(_child(el, "value")),
        "types": _types(el),
        "use_restriction": _txt(el, "useRestriction") == "true",
    }
    for tag, key in (
        ("availableAsField", "available_as_field"),
        ("valueListAllowed", "value_list_allowed"),
        ("denyIncompleteValues", "deny_incomplete"),
        ("use", "use"),
    ):
        v = _txt(el, tag)
        if v:
            p[key] = v
    av = [
        (_title(a, "presentation") or _value(_child(a, "value")))
        for a in _children(el, "availableValue")
    ]
    if av:
        p["available_values"] = av
    return p


def _parse_schema(xml: str) -> dict[str, Any] | None:
    try:
        root = ET.fromstring(xml.lstrip("﻿").encode("utf-8"))
    except ET.ParseError:
        return None
    schema = next((e for e in root.iter() if _loc(e.tag) == "dataCompositionSchema"), None)
    if schema is None:
        return None
    out: dict[str, Any] = {
        "data_sources": [],
        "datasets": [],
        "links": [],
        "resources": [],
        "calculated_fields": [],
        "parameters": [],
        "templates": [],
        "nested_schemas": [],
        "variants": [],
    }
    for el in schema:
        tag = _loc(el.tag)
        if tag == "dataSource":
            out["data_sources"].append(
                {"name": _txt(el, "name"), "type": _txt(el, "dataSourceType")}
            )
        elif tag == "dataSet":
            out["datasets"].append(_parse_dataset(el))
        elif tag == "dataSetLink":
            out["links"].append(
                {
                    "source": _txt(el, "sourceDataSet"),
                    "destination": _txt(el, "destinationDataSet"),
                    "source_expression": _txt(el, "sourceExpression"),
                    "destination_expression": _txt(el, "destinationExpression"),
                    "parameter": _txt(el, "parameter"),
                    "required": _txt(el, "required") == "true",
                }
            )
        elif tag == "totalField":
            out["resources"].append(
                {
                    "path": _txt(el, "dataPath"),
                    "expression": _txt(el, "expression"),
                    "groups": [(g.text or "").strip() for g in _children(el, "group")],
                }
            )
        elif tag == "calculatedField":
            cf: dict[str, Any] = {
                "path": _txt(el, "dataPath"),
                "expression": _txt(el, "expression"),
                "title": _title(el),
            }
            if _types(el):
                cf["types"] = _types(el)
            out["calculated_fields"].append(cf)
        elif tag == "parameter":
            out["parameters"].append(_parse_parameter(el))
        elif tag in ("template", "groupTemplate", "groupHeaderTemplate", "fieldTemplate", "totalFieldsTemplate"):
            out["templates"].append(
                {
                    "kind": tag,
                    "name": _txt(el, "name") or _txt(el, "template"),
                    "field": _txt(el, "field") or _txt(el, "groupField") or _txt(el, "groupName"),
                }
            )
        elif tag == "nestedSchema":
            out["nested_schemas"].append({"name": _txt(el, "name"), "title": _title(el)})
        elif tag == "settingsVariant":
            v: dict[str, Any] = {"name": _txt(el, "name"), "title": _title(el, "presentation")}
            st = _child(el, "settings")
            if st is not None and len(st):
                v["settings"] = _settings_from_el(st)
            out["variants"].append(v)
    return out


def _filter_items(el: ET.Element | None, depth: int = 0) -> list[dict[str, Any]]:
    res: list[dict[str, Any]] = []
    if el is None or depth > 8:
        return res
    for it in _children(el, "item"):
        t = _xsi(it)
        d: dict[str, Any] = {"use": _txt(it, "use") != "false"}
        if t == "FilterItemGroup":
            d["group"] = _txt(it, "groupType") or "AndGroup"
            d["items"] = _filter_items(it, depth + 1)
        else:
            d["left"] = _txt(it, "left")
            d["comparison"] = _txt(it, "comparisonType") or "Equal"
            d["right"] = _value(_child(it, "right"))
        if _txt(it, "userSettingID"):
            d["user_setting"] = True
        if _title(it, "presentation"):
            d["presentation"] = _title(it, "presentation")
        if _txt(it, "viewMode"):
            d["view_mode"] = _txt(it, "viewMode")
        res.append(d)
    return res


def _fields_of(parent: ET.Element | None, container: str) -> list[str]:
    ch = _child(parent, container)
    if ch is None:
        return []
    out: list[str] = []
    for i in _children(ch, "item"):
        t = _xsi(i)
        if t.endswith("Auto"):
            out.append("(авто)")
        elif t == "SelectedItemFolder":
            sub = [_txt(x, "field") for x in _children(i, "item")]
            out.append(f"{_title(i, 'lwsTitle') or 'Папка'}[{', '.join(sub)}]")
        else:
            f = _txt(i, "field")
            ot = _txt(i, "orderType")
            out.append(f + (" УБЫВ" if ot == "Desc" else "") if f else "(?)")
    return out


def _out_params(el: ET.Element | None) -> dict[str, str]:
    op = _child(el, "outputParameters")
    res: dict[str, str] = {}
    for i in _children(op, "item"):
        name = _txt(i, "parameter")
        if name and _txt(i, "use") != "false":
            res[name] = _value(_child(i, "value"))[:120]
    return res


def _group_items(el: ET.Element) -> list[dict[str, Any]]:
    gi = _child(el, "groupItems")
    out: list[dict[str, Any]] = []
    for i in _children(gi, "item"):
        if _xsi(i) == "GroupItemAuto":
            out.append({"field": "(авто)"})
            continue
        d: dict[str, Any] = {"field": _txt(i, "field")}
        gt = _txt(i, "groupType")
        if gt and gt != "Items":
            d["group_type"] = gt
        pa = _txt(i, "periodAdditionType")
        if pa and pa != "None":
            d["period_addition"] = pa
        out.append(d)
    return out


def _structure(el: ET.Element, depth: int = 0) -> list[dict[str, Any]]:
    res: list[dict[str, Any]] = []
    if depth > 8:
        return res
    for ch in el:
        tag = _loc(ch.tag)
        t = _xsi(ch)
        if tag in ("row", "column", "point", "series"):
            kind = tag
        elif tag == "item" and (t.startswith("StructureItem") or _child(ch, "groupItems") is not None
                                or _child(ch, "selection") is not None):
            kind = t.replace("StructureItem", "").lower() or "group"
        else:
            continue
        node: dict[str, Any] = {"kind": kind}
        gis = _group_items(ch)
        node["group_by"] = [g["field"] for g in gis]
        extra = [g for g in gis if len(g) > 1]
        if extra:
            node["group_options"] = extra
        if kind == "group" and not gis:
            node["kind"] = "detail"  # детальные записи
        if _txt(ch, "name"):
            node["name"] = _txt(ch, "name")
        flt = _filter_items(_child(ch, "filter"))
        if flt:
            node["filter"] = flt
        sel = [s for s in _fields_of(ch, "selection") if s != "(авто)"]
        if sel:
            node["selection"] = sel
        op = _out_params(ch)
        if op:
            node["output"] = op
        node["nested"] = _structure(ch, depth + 1)
        res.append(node)
    return res


def _settings_from_el(root: ET.Element) -> dict[str, Any]:
    params = []
    for el in _children(_child(root, "dataParameters"), "item"):
        name = _txt(el, "parameter")
        if name:
            p = {"name": name, "value": _value(_child(el, "value"))}
            if _txt(el, "use") == "false":
                p["use"] = False
            if _txt(el, "userSettingID"):
                p["user_setting"] = True
            params.append(p)
    ca = []
    for it in _children(_child(root, "conditionalAppearance"), "item"):
        ca.append(
            {
                "fields": _fields_of(it, "selection"),
                "filter": _filter_items(_child(it, "filter")),
                "appearance": [
                    f"{_txt(a, 'parameter')}={_value(_child(a, 'value'))[:60]}"
                    for a in _children(_child(it, "appearance"), "item")
                    if _txt(a, "use") != "false"
                ],
                "use": _txt(it, "use") != "false",
            }
        )
    user_fields = [
        {"path": _txt(i, "dataPath"), "title": _title(i), "kind": _xsi(i)}
        for i in _children(_child(root, "userFields"), "item")
    ]
    return {
        "selection": _fields_of(root, "selection"),
        "filter": _filter_items(_child(root, "filter")),
        "order": _fields_of(root, "order"),
        "parameters": params,
        "conditional_appearance": ca,
        "output": _out_params(root),
        "user_fields": user_fields,
        "structure": _structure(root),
    }


def _parse_settings(xml: str) -> dict[str, Any]:
    try:
        root = ET.fromstring(xml.lstrip("﻿").encode("utf-8"))
    except ET.ParseError:
        return {}
    return _settings_from_el(root)


def _dcs_group(rel: str) -> str:
    """Макет, которому принадлежит часть: «Файл.erf/<uuid>.0/SchemaFile» → «Файл.erf/<uuid>.0»."""
    rel = rel.replace("\\", "/")
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


def extract_dcs_all(texts: list[tuple[str, str]]) -> list[tuple[str, dict[str, Any]]]:
    """Все СКД файла: [(группа-макет, схема + её настройки)], главная (больше всего текста запросов) — первой."""
    schemas: dict[str, dict[str, Any]] = {}
    settings: dict[str, dict[str, Any]] = {}
    for rel, body in texts:
        g = _dcs_group(rel)
        if "dataCompositionSchema" in body and "<dataSet" in body and g not in schemas:
            sch = _parse_schema(body)
            if sch:
                schemas[g] = sch
        elif body.lstrip("﻿").lstrip().startswith("<?xml") and "<Settings" in body[:1500]:
            if g not in settings:
                settings[g] = _parse_settings(body)
    out: list[tuple[str, dict[str, Any]]] = []
    for g, schema in schemas.items():
        # настройки — только из того же макета: раньше брались первые попавшиеся, в т.ч. от другой схемы
        st = settings.get(g) or next((v.get("settings") for v in schema.get("variants") or [] if v.get("settings")), {})
        schema["settings"] = st
        for ds in _all_datasets(schema):
            if (ds.get("query") or "").strip():
                ds["batch"] = query_structure(ds["query"])
        out.append((g, schema))
    weight = lambda s: (sum(len(d.get("query") or "") for d in _all_datasets(s)), len(_all_datasets(s)))  # noqa: E731
    out.sort(key=lambda x: weight(x[1]), reverse=True)
    return out


def extract_dcs(texts: list[tuple[str, str]]) -> dict[str, Any] | None:
    """Из извлечённых текстов собрать описание главной СКД (схема + настройки по умолчанию)."""
    all_ = extract_dcs_all(texts)
    return all_[0][1] if all_ else None


def _all_datasets(dcs: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def rec(items: list[dict[str, Any]]) -> None:
        for d in items:
            out.append(d)
            rec(d.get("items") or [])

    rec(dcs.get("datasets") or [])
    return out


# ───────────────────────── структура пакета запроса ─────────────────────────

_RE_SRC = re.compile(
    r"\b((?:РегистрСведений|РегистрНакопления|РегистрБухгалтерии|РегистрРасчета|Документ|Справочник|"
    r"Перечисление|ПланВидовХарактеристик|ПланСчетов|ПланВидовРасчета|БизнесПроцесс|Задача|"
    r"ЖурналДокументов|Константа|ПланОбмена|ВнешнийИсточникДанных)\.[\w.]+)",
    re.I,
)


def query_structure(q: str) -> list[dict[str, Any]]:
    """Пакет запроса: по шагам — что создаётся, из чего, соединения, параметры."""
    steps: list[dict[str, Any]] = []
    pos = 0
    raw = q or ""
    for part in re.split(r";", raw):
        line = raw.count("\n", 0, pos) + 1
        pos += len(part) + 1
        clean = _RE_COMMENT_P.sub("", part)
        if not clean.strip():
            continue
        lead = len(part) - len(part.lstrip())
        line += part[:lead].count("\n")
        st: dict[str, Any] = {"step": len(steps) + 1, "line": line}
        m = re.search(r"\bПОМЕСТИТЬ\s+(\w+)", clean, re.I)
        dm = re.match(r"\s*УНИЧТОЖИТЬ\s+(\w+)", clean, re.I)
        if dm:
            st["drops"] = dm.group(1)
            steps.append(st)
            continue
        st["puts"] = m.group(1) if m else ""
        st["unions"] = len(re.findall(r"\bОБЪЕДИНИТЬ\b", clean, re.I))
        if re.search(r"\bВЫБРАТЬ\s+(?:ПЕРВЫЕ\s+\d+\s+)?РАЗЛИЧНЫЕ\b", clean, re.I):
            st["distinct"] = True
        srcs: list[str] = []
        for fm in re.finditer(r"\b(?:ИЗ|СОЕДИНЕНИЕ)\s+([\w.]+)(\s*\([^;]*?\))?\s+(?:КАК\s+)?(\w+)", clean, re.I):
            s = fm.group(1) + ("(…)" if fm.group(2) else "")
            if s.upper() not in ("ИЗ",) and s not in srcs:
                srcs.append(s)
        st["sources"] = srcs
        joins = []
        for jm in re.finditer(
            r"(ЛЕВОЕ|ПРАВОЕ|ПОЛНОЕ|ВНУТРЕННЕЕ)?\s*СОЕДИНЕНИЕ\s+([\w.]+(?:\s*\([^;]*?\))?)\s+(?:КАК\s+)?(\w+)\s+ПО\s+([\s\S]*?)"
            r"(?=\b(?:ЛЕВОЕ|ПРАВОЕ|ПОЛНОЕ|ВНУТРЕННЕЕ)\b|\bСОЕДИНЕНИЕ\b|\bГДЕ\b|\bСГРУППИРОВАТЬ\b|\bОБЪЕДИНИТЬ\b|\bУПОРЯДОЧИТЬ\b|\bИНДЕКСИРОВАТЬ\b|$)",
            clean,
            re.I,
        ):
            joins.append(
                {
                    "kind": (jm.group(1) or "ВНУТРЕННЕЕ").upper(),
                    "table": jm.group(2).split("(")[0],
                    "alias": jm.group(3),
                    "on": " ".join(jm.group(4).split())[:200],
                }
            )
        if joins:
            st["joins"] = joins
        wm = re.search(r"\bГДЕ\b([\s\S]*?)(?=\bСГРУППИРОВАТЬ\b|\bОБЪЕДИНИТЬ\b|\bУПОРЯДОЧИТЬ\b|\bИМЕЮЩИЕ\b|\bИТОГИ\b|\bИНДЕКСИРОВАТЬ\b|$)", clean, re.I)
        if wm and wm.group(1).strip():
            st["where"] = " ".join(wm.group(1).split())[:200]
        gm = re.search(r"\bСГРУППИРОВАТЬ\s+ПО\b", clean, re.I)
        if gm:
            st["grouped"] = True
        if re.search(r"\bИТОГИ\b", clean, re.I):
            st["totals"] = True
        params = sorted(set(re.findall(r"&(\w+)", clean)), key=str.lower)
        if params:
            st["params"] = params
        refs = sorted(set(re.findall(r"\bССЫЛКА\s+([\w.]+)", clean, re.I)))
        if refs:
            st["ref_checks"] = refs
        if st["puts"] == "":
            head = re.split(r"\bИЗ\b", re.split(r"\bОБЪЕДИНИТЬ\b", clean, flags=re.I)[0], maxsplit=1, flags=re.I)[0]
            st["outputs"] = _select_aliases(head)
        steps.append(st)
    return steps


def _select_items(head: str) -> list[str]:
    """Элементы списка ВЫБРАТЬ (запятые только верхнего уровня)."""
    body = re.sub(r"^\s*ВЫБРАТЬ\s+(?:РАЗЛИЧНЫЕ\s+)?(?:ПЕРВЫЕ\s+\d+\s+)?", "", head, flags=re.I)
    out: list[str] = []
    depth = 0
    cur = ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _item_alias(item: str) -> tuple[str, str]:
    """(выражение, имя колонки)."""
    m = re.search(r"^([\s\S]*?)\s+КАК\s+(\w+)\s*$", item, re.I)
    if m:
        return " ".join(m.group(1).split()), m.group(2)
    mm = re.search(r"(\w+)\s*$", item)
    return " ".join(item.split()), (mm.group(1) if mm else "")


def _select_aliases(head: str) -> list[str]:
    return [a for _e, a in map(_item_alias, _select_items(head)) if a]


def query_field_expr(q: str, alias: str) -> str:
    """Выражение колонки alias в финальном запросе пакета."""
    stmts = [s for s in re.split(r";", _RE_COMMENT_P.sub("", q or "")) if s.strip()]
    if not stmts:
        return ""
    last = re.split(r"\bОБЪЕДИНИТЬ\b", stmts[-1], flags=re.I)[0]
    head = re.split(r"\bИЗ\b", last, maxsplit=1, flags=re.I)[0]
    for item in _select_items(head):
        expr, name = _item_alias(item)
        if name.lower() == alias.lower():
            return expr
    return ""


_RE_COMMENT_P = re.compile(r"//[^\n]*")


_REF_CLASSES = (
    "РегистрСведений", "РегистрНакопления", "РегистрБухгалтерии", "РегистрРасчета", "Документ",
    "Справочник", "Перечисление", "ПланВидовХарактеристик", "ПланСчетов", "ПланВидовРасчета",
    "БизнесПроцесс", "Задача", "ЖурналДокументов", "Константа", "ПланОбмена", "ВнешнийИсточникДанных",
)


def metadata_refs(texts: list[str]) -> list[str]:
    """Объекты CF, на которые ссылается файл (запросы, типы, значения параметров)."""
    out: list[str] = []
    for t in texts:
        for m in _RE_SRC.finditer(_RE_COMMENT_P.sub("", t or "")):
            parts = m.group(1).split(".")
            cls = next((c for c in _REF_CLASSES if c.lower() == parts[0].lower()), parts[0])
            ref = f"{cls}.{parts[1]}" if len(parts) > 1 else cls
            if ref.lower() not in {o.lower() for o in out}:
                out.append(ref)
    return out


def dcs_overview(dcs: dict[str, Any]) -> str:
    """Человекочитаемая полная сводка СКД (для агента; read_code по «СКД/описание»)."""
    L: list[str] = []
    for s in dcs.get("data_sources") or []:
        L.append(f"Источник данных: {s['name']} ({s['type']})")
    for ds in _all_datasets(dcs):
        L.append(f"\nНабор «{ds['name']}» [{ds['type']}]"
                 + (f", автозаполнение полей: {'да' if ds.get('auto_fill_fields') else 'нет'}" if ds['type'] == 'Query' else ""))
        for f in ds.get("fields") or []:
            bits = [f"  поле {f['path']}"]
            if f.get("field") and f["field"] != f["path"]:
                bits.append(f"← {f['field']}")
            if f.get("title"):
                bits.append(f"«{f['title']}»")
            if f.get("types"):
                bits.append("тип " + "|".join(f["types"]))
            if f.get("role"):
                bits.append("роль " + ",".join(f"{k}={v}" for k, v in f["role"].items()))
            if f.get("use_restriction"):
                bits.append("запрет: " + ",".join(f["use_restriction"]))
            if f.get("presentation_expression"):
                bits.append(f"представление: {f['presentation_expression']}")
            if f.get("appearance"):
                bits.append("оформление: " + "; ".join(f["appearance"]))
            L.append(" ".join(bits))
        for st in ds.get("batch") or []:
            if st.get("drops"):
                L.append(f"  шаг {st['step']} (стр.{st['line']}): УНИЧТОЖИТЬ {st['drops']}")
                continue
            tgt = f"→ {st['puts']}" if st.get("puts") else "→ РЕЗУЛЬТАТ [" + ", ".join(st.get("outputs") or []) + "]"
            line = f"  шаг {st['step']} (стр.{st['line']}) {tgt}; из: {', '.join(st.get('sources') or [])}"
            if st.get("distinct"):
                line += "; РАЗЛИЧНЫЕ"
            if st.get("unions"):
                line += f"; ОБЪЕДИНИТЬ×{st['unions']}"
            if st.get("grouped"):
                line += "; СГРУППИРОВАТЬ"
            if st.get("params"):
                line += "; параметры: " + ", ".join("&" + p for p in st["params"])
            L.append(line)
            for j in st.get("joins") or []:
                L.append(f"      {j['kind']} {j['table']} КАК {j['alias']} ПО {j['on']}")
            if st.get("where"):
                L.append(f"      ГДЕ {st['where']}")
            if st.get("ref_checks"):
                L.append(f"      ССЫЛКА: {', '.join(st['ref_checks'])}")
    for ln in dcs.get("links") or []:
        L.append(f"Связь наборов: {ln['source']}.{ln['source_expression']} → "
                 f"{ln['destination']}.{ln['destination_expression']}"
                 + (f" (параметр {ln['parameter']})" if ln.get("parameter") else ""))
    for c in dcs.get("calculated_fields") or []:
        L.append(f"Вычисляемое поле {c['path']} = {c['expression']}" + (f" «{c['title']}»" if c.get("title") else ""))
    for r in dcs.get("resources") or []:
        L.append(f"Ресурс {r['path']} = {r['expression']}"
                 + (f"; рассчитывать по: {', '.join(r['groups'])}" if r.get("groups") else "; во всех группировках"))
    for p in dcs.get("parameters") or []:
        bits = [f"Параметр {p['name']}"]
        if p.get("title"):
            bits.append(f"«{p['title']}»")
        if p.get("types"):
            bits.append("тип " + "|".join(p["types"]))
        if p.get("value"):
            bits.append(f"= {p['value']}")
        if p.get("expression"):
            bits.append(f"выражение: {p['expression']}")
        bits.append("скрыт от пользователя" if p.get("use_restriction") else "доступен пользователю")
        if p.get("available_values"):
            bits.append("варианты: " + ", ".join(p["available_values"]))
        L.append(" ".join(bits))
    for t in dcs.get("templates") or []:
        L.append(f"Макет {t['kind']}: {t['name']} {t['field']}".rstrip())
    for n in dcs.get("nested_schemas") or []:
        L.append(f"Вложенная схема: {n['name']}")
    if dcs.get("variants"):
        L.append("Варианты настроек: " + ", ".join(v["name"] + (f" «{v['title']}»" if v.get("title") and v["title"] != v["name"] else "") for v in dcs["variants"]))
    s = dcs.get("settings") or {}
    if s:
        L.append("\nНастройки (вариант по умолчанию):")
        if s.get("selection"):
            L.append("  выбранные поля: " + ", ".join(s["selection"]))
        for p in s.get("parameters") or []:
            L.append(f"  параметр {p['name']} = {p['value']}" + (" (не используется)" if p.get("use") is False else "")
                     + (" [в быстрых настройках]" if p.get("user_setting") else ""))
        for fl in _flat_filters(s.get("filter") or []):
            L.append("  отбор: " + fl)
        if s.get("order"):
            L.append("  порядок: " + ", ".join(s["order"]))
        for ca in s.get("conditional_appearance") or []:
            L.append("  усл.оформление: " + ", ".join(ca["fields"] or ["все поля"]) + " | "
                     + "; ".join(_flat_filters(ca["filter"])) + " → " + "; ".join(ca["appearance"]))
        if s.get("output"):
            L.append("  параметры вывода: " + "; ".join(f"{k}={v}" for k, v in s["output"].items()))
        for uf in s.get("user_fields") or []:
            L.append(f"  польз.поле {uf['path']} «{uf['title']}»")

        def walk(nodes: list[dict[str, Any]], ind: int) -> None:
            for n in nodes:
                g = ", ".join(n.get("group_by") or []) or ("детальные записи" if n["kind"] == "detail" else "")
                opt = "; ".join(
                    f"{o['field']}: " + ", ".join(f"{k}={v}" for k, v in o.items() if k != "field")
                    for o in n.get("group_options") or []
                )
                L.append("  " + "  " * ind + f"{n['kind']}: {g}" + (f" [{opt}]" if opt else "")
                         + ("; отбор: " + "; ".join(_flat_filters(n["filter"])) if n.get("filter") else ""))
                walk(n.get("nested") or [], ind + 1)

        L.append("  структура:")
        walk(s.get("structure") or [], 1)
    return "\n".join(L).strip()


def _flat_filters(items: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for it in items:
        if "items" in it:
            inner = _flat_filters(it["items"])
            out.append(f"{it['group']}(" + "; ".join(inner) + ")")
        else:
            s = f"{it.get('left')} {it.get('comparison')} {it.get('right')}".strip()
            if not it.get("use", True):
                s += " [выкл]"
            if it.get("user_setting"):
                s += " [быстр.]"
            out.append(s)
    return out



# ───────────────────────── статический анализ запросов ─────────────────────────

_RE_COMMENT = re.compile(r"//[^\n]*")
_RE_JOIN = re.compile(
    r"(ЛЕВОЕ|ПРАВОЕ|ПОЛНОЕ|ВНУТРЕННЕЕ)?\s*СОЕДИНЕНИЕ\s+([\w.]+(?:\([^)]*\))?)\s+(?:КАК\s+)?(\w+)",
    re.I,
)
_RE_FROM = re.compile(r"\bИЗ\s+([\w.]+(?:\([^)]*\))?)\s+(?:КАК\s+)?(\w+)", re.I)
_RE_VIRT = re.compile(r"\.(СрезПоследних|СрезПервых|Остатки|Обороты|ОстаткиИОбороты)\s*\(", re.I)


def _strip(q: str) -> str:
    return _RE_COMMENT.sub("", q or "")


def _statements(q: str) -> list[str]:
    return [s for s in re.split(r";\s*", _strip(q)) if s.strip()]


def _selects(stmt: str) -> list[str]:
    return re.split(r"\bОБЪЕДИНИТЬ(?:\s+ВСЕ)?\b", stmt, flags=re.I)


def _approx_line(query: str, needle: str) -> int:
    i = query.find(needle)
    return query.count("\n", 0, i) + 1 if i >= 0 else 0


def lint_dcs(dcs: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def add(sev: str, code: str, ds: str, msg: str, snippet: str = "", line: int = 0) -> None:
        out.append(
            {
                "severity": sev,
                "code": code,
                "dataset": ds,
                "line": line,
                "message": msg,
                "snippet": snippet[:240],
            }
        )

    declared = {p["name"].lower() for p in dcs.get("parameters", [])}
    resources = {r["path"].lower(): r for r in dcs.get("resources", [])}

    for ds in _all_datasets(dcs):
        q = ds.get("query") or ""
        if not q.strip():
            continue
        dn = ds["name"]
        clean = _strip(q)

        # 1. ЛЕВОЕ СОЕДИНЕНИЕ + условие на правую таблицу в ГДЕ
        for stmt in _statements(q):
            for sel in _selects(stmt):
                for jm in _RE_JOIN.finditer(sel):
                    kind = (jm.group(1) or "").upper()
                    alias = jm.group(3)
                    if kind not in ("ЛЕВОЕ", "ПРАВОЕ"):
                        continue
                    tail = sel[jm.end() :]
                    wm = re.search(r"\bГДЕ\b([\s\S]*?)(?=\bСГРУППИРОВАТЬ\b|\bУПОРЯДОЧИТЬ\b|\bИМЕЮЩИЕ\b|\bИТОГИ\b|$)", tail, re.I)
                    if not wm:
                        continue
                    where = wm.group(1)
                    # правая таблица для ЛЕВОГО; для ПРАВОГО — таблица из ИЗ
                    target = alias
                    if kind == "ПРАВОЕ":
                        fm = _RE_FROM.search(sel)
                        target = fm.group(2) if fm else ""
                    if not target:
                        continue
                    refs = re.findall(rf"\b{re.escape(target)}\.[\w.]+", where, re.I)
                    if not refs:
                        continue
                    null_ok = re.search(
                        rf"ЕСТЬ\s*NULL\s*\(\s*{re.escape(target)}\.|{re.escape(target)}\.[\w.]+\s+ЕСТЬ\s+(?:НЕ\s+)?NULL",
                        where,
                        re.I,
                    )
                    if null_ok:
                        continue
                    add(
                        "error",
                        "left_join_where",
                        dn,
                        f"{kind} СОЕДИНЕНИЕ с «{target}» + условие по «{target}» в ГДЕ: "
                        "строки без пары (NULL) отсекаются — соединение вырождается во "
                        "ВНУТРЕННЕЕ, часть записей (сотрудников) пропадает. "
                        "Условие перенести в ПО или проверять ЕСТЬNULL / ЕСТЬ NULL.",
                        " ".join(where.split())[:200],
                        _approx_line(q, where.strip()[:40]),
                    )

        # 2. Псевдоним «СрезПоследних/Остатки/Обороты» у обычной таблицы
        for jm in [*_RE_FROM.finditer(clean), *_RE_JOIN.finditer(clean)]:
            src, alias = (jm.group(1), jm.group(2)) if jm.re is _RE_FROM else (jm.group(2), jm.group(3))
            if re.search(r"СрезПоследних|СрезПервых|Остатки|Обороты", alias, re.I) and not _RE_VIRT.search(src):
                add(
                    "warning",
                    "alias_vs_table",
                    dn,
                    f"Псевдоним «{alias}» похож на виртуальную таблицу, но источник «{src}» — "
                    "обычная таблица регистра: читаются ВСЕ записи (все периоды), без среза — "
                    "возможны дубли и лишние/потерянные строки.",
                    jm.group(0),
                    _approx_line(q, jm.group(0)),
                )

        # 3. Соединение по цепочке разыменований / по Наименованию
        for jm in re.finditer(r"\bПО\b([\s\S]*?)(?=\bЛЕВОЕ\b|\bПРАВОЕ\b|\bВНУТРЕННЕЕ\b|\bПОЛНОЕ\b|\bСОЕДИНЕНИЕ\b|\bГДЕ\b|\bСГРУППИРОВАТЬ\b|;|$)", clean, re.I):
            cond = jm.group(1)
            if re.search(r"\b\w+\.\w+\.\w+\.\w+", cond) or re.search(r"\.Наименование\s*=", cond, re.I):
                add(
                    "warning",
                    "join_by_deref_or_name",
                    dn,
                    "Соединение по разыменованию через несколько точек и/или по Наименованию: "
                    "медленно (неявные соединения) и ненадёжно — одинаковые названия у разных "
                    "элементов дают дубли, переименование/пустое имя — потерю строк. "
                    "Соединять по ссылке/ключу.",
                    " ".join(cond.split())[:200],
                    _approx_line(q, cond.strip()[:30]),
                )
                break

        # 4. Строковый литерал "" в выборке с ОБЪЕДИНИТЬ
        if re.search(r"ОБЪЕДИНИТЬ", clean, re.I):
            for m in re.finditer(r'(?m)^[ \t]*""[ \t]*(?:КАК[ \t]+\w+)?[ \t]*,?[ \t]*$', clean):
                add(
                    "warning",
                    "union_empty_string",
                    dn,
                    'Литерал "" в выборке с ОБЪЕДИНИТЬ: колонка получает смешанный тип '
                    "(Строка + Дата/Число) — ошибки сравнения и обработки в СКД.",
                    m.group(0).strip(),
                    _approx_line(q, m.group(0).strip()),
                )
                break

        # 5. Список периодов из регистра (может быть не заполнен)
        for m in re.finditer(
            r"ВЫБРАТЬ\s+РАЗЛИЧНЫЕ\s+НАЧАЛОПЕРИОДА\([^)]*(?:МЕСЯЦ|КВАРТАЛ|ГОД|НЕДЕЛЯ)\)[\s\S]{0,400}?ИЗ\s+РегистрСведений\.\w+",
            clean,
            re.I,
        ):
            add(
                "warning",
                "periods_from_register",
                dn,
                "Список периодов (месяцев) берётся РАЗЛИЧНЫМИ датами из регистра сведений: "
                "если записи за период не заполнены, нужные месяцы выпадут и вместе с ними — "
                "сотрудники/значения за эти месяцы. Надёжнее генерировать ряд периодов "
                "или использовать СКД-периодичность.",
                " ".join(m.group(0).split())[:200],
                _approx_line(q, m.group(0)[:40]),
            )
            break

        # 6. Временные таблицы, которые нигде не используются
        puts = re.findall(r"ПОМЕСТИТЬ\s+(\w+)", clean, re.I)
        for t in puts:
            uses = len(re.findall(rf"(?<![\w]){re.escape(t)}(?![\w])", clean, re.I))
            if uses <= 1:
                add(
                    "info",
                    "unused_temp_table",
                    dn,
                    f"Временная таблица «{t}» создана, но не используется ниже.",
                    t,
                    _approx_line(q, t),
                )

        # 7. Параметры: используются, но не объявлены / объявлены, но не используются
        used = {p.lower(): p for p in re.findall(r"&(\w+)", clean)}
        for low, orig in used.items():
            if low not in declared:
                add(
                    "error",
                    "param_undeclared",
                    dn,
                    f"Параметр &{orig} в запросе не объявлен в схеме.",
                    f"&{orig}",
                )
        for p in dcs.get("parameters", []):
            if p["name"].lower() not in used and p["name"].lower() not in {
                "период", "начальнаядата", "конечнаядата"
            }:
                add(
                    "info",
                    "param_unused",
                    dn,
                    f"Параметр «{p['name']}» объявлен, но в запросе не используется.",
                    p["name"],
                )
            elif p["name"].lower() == "период" and "период" not in used:
                add(
                    "info",
                    "param_unused",
                    dn,
                    "Параметр «Период» объявлен, но в запросе не используется "
                    "(запрос работает по ДатаС/ДатаПо) — вводящий пользователь может решить, "
                    "что он влияет на отчёт.",
                    "Период",
                )

        # 8. Ресурс «Количество()» над полем-ссылкой
        for path, res in resources.items():
            expr = (res.get("expression") or "")
            if re.match(r"\s*Количество\s*\(", expr, re.I):
                m = re.search(
                    rf"([\w.]+)\s+КАК\s+{re.escape(res['path'])}\b", clean, re.I
                )
                if m:
                    add(
                        "warning",
                        "count_of_ref_field",
                        dn,
                        f"Ресурс «{res['path']}» = {expr.strip()}, а поле выбирается как "
                        f"«{m.group(1)} КАК {res['path']}» — это ссылка, не число. "
                        "Количество() считает непустые значения, а не людей: при дублях строк "
                        "(нескольких записях на одного) итог завышен, при NULL — занижен. "
                        "Для числа уникальных использовать КоличествоРазличных(); в расшифровке "
                        "по такому полю список строится по отбору группировок, поэтому набор "
                        "записей расшифровки должен совпадать с набором запроса.",
                        m.group(0),
                        _approx_line(q, m.group(0)),
                    )

        # 9. Поле набора есть, в запросе его нет (и наоборот — отсутствие в наборе)
        last_sel = _selects(_statements(q)[-1])[0] if _statements(q) else ""
        last_head = re.split(r"\bИЗ\b", last_sel, maxsplit=1, flags=re.I)[0]
        names: set[str] = set(a.lower() for a in re.findall(r"\bКАК\s+(\w+)", last_head, re.I))
        for item in re.split(r",", last_head):
            mm = re.search(r"([\w]+)\s*$", item.strip())
            if mm and "КАК" not in item.upper():
                names.add(mm.group(1).lower())
        for f in ds.get("fields", []):
            leaf = f["path"].lower()
            if names and leaf not in names:
                add(
                    "warning",
                    "dataset_field_not_in_query",
                    dn,
                    f"Поле набора «{f['path']}» не найдено в выборке последнего запроса пакета "
                    "(опечатка / поле не выбрано) — в отчёте будет пустым.",
                    f["path"],
                )

    _lint_period_and_groups(dcs, add)

    # сортировка: error → warning → info
    order = {"error": 0, "warning": 1, "info": 2}
    out.sort(key=lambda x: order.get(x["severity"], 3))
    return out


def dcs_summary(dcs: dict[str, Any]) -> str:
    ds = dcs.get("datasets") or []
    nq = sum(1 for d in ds if (d.get("query") or "").strip())
    return (
        f"СКД: наборов {len(ds)} (с запросом {nq}), ресурсов {len(dcs.get('resources') or [])}, "
        f"параметров {len(dcs.get('parameters') or [])}"
    )


_PERIOD_UNITS = r"(?:МЕСЯЦ|КВАРТАЛ|ГОД|НЕДЕЛЯ|ДЕКАДА|ПОЛУГОДИЕ|ДЕНЬ)"


def _structure_fields(nodes: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for n in nodes or []:
        out.extend(g for g in n.get("group_by") or [] if g != "(авто)")
        out.extend(_structure_fields(n.get("nested") or []))
    return out


def _lint_period_and_groups(dcs: dict[str, Any], add: Any) -> None:
    """Период/месяц в запросе вместо СКД; поле = параметр; «рассчитывать по» у ресурса."""
    settings = dcs.get("settings") or {}
    grouped = _structure_fields(settings.get("structure") or [])
    grouped_low = {g.lower() for g in grouped}
    params = {p["name"].lower(): p for p in dcs.get("parameters") or []}
    has_res = bool(dcs.get("resources"))

    for ds in _all_datasets(dcs):
        q = ds.get("query") or ""
        if not q.strip():
            continue
        clean = _strip(q)
        # алиасы, полученные усечением даты: НАЧАЛОПЕРИОДА(…, МЕСЯЦ) КАК X
        trunc_alias = {
            m.group(1).lower()
            for m in re.finditer(
                rf"НАЧАЛОПЕРИОДА\s*\([^;]*?,\s*{_PERIOD_UNITS}\s*\)\s+КАК\s+(\w+)", clean, re.I
            )
        }
        month_grid = re.search(
            r"НАЧАЛОПЕРИОДА\s*\([^;]*?,\s*(?:МЕСЯЦ|КВАРТАЛ|ГОД|НЕДЕЛЯ|ДЕКАДА|ПОЛУГОДИЕ)\s*\)", clean, re.I
        )
        for f in ds.get("fields") or []:
            path = f.get("path") or ""
            if not path or f.get("period"):
                continue
            expr = query_field_expr(q, path)
            leaf = expr.rsplit(".", 1)[-1].lower() if expr else ""
            by_expr = bool(re.search(r"НАЧАЛОПЕРИОДА|КОНЕЦПЕРИОДА", expr, re.I))
            by_alias = leaf in trunc_alias or path.lower() in trunc_alias
            if not (by_expr or (by_alias and month_grid)):
                continue
            in_struct = path.lower() in grouped_low
            add(
                "error" if (in_struct and has_res) else "warning",
                "period_in_query",
                ds["name"],
                f"Поле «{path}» — период, но месяц формируется в ЗАПРОСЕ ({expr or 'НАЧАЛОПЕРИОДА'}"
                + ("; строки «размножаются» по таблице месяцев" if month_grid else "")
                + "), а в СКД у поля "
                + ("только роль «Измерение»" if f.get("dimension") else "нет роли")
                + ", без роли «Период» и без периодичности группировки. Ячейка ресурса и её "
                "расшифровка идут по точному значению — расшифровка может показать не всех "
                "(сотрудников). Исправить: в запросе — исходная дата; в СКД роль «Период» + "
                "группировка с типом «Месяц» (при нужде дополнение периодов).",
                expr or path,
                _approx_line(q, expr) if expr else 0,
            )
        # поле набора с тем же именем, что параметр
        for f in ds.get("fields") or []:
            p = params.get((f.get("path") or "").lower())
            if p:
                add(
                    "warning",
                    "field_equals_parameter",
                    ds["name"],
                    f"Поле набора «{f['path']}» и параметр СКД «{p['name']}» называются одинаково: "
                    "в отборах/расшифровке/выражениях легко перепутать поле с ПараметрыДанных."
                    f"{p['name']}; параметр "
                    + ("скрыт (ограничение использования) и " if p.get("use_restriction") else "")
                    + "не влияет на поле.",
                    f["path"],
                )

    # ресурс с «Рассчитывать по» не покрывает группировки структуры
    for r in dcs.get("resources") or []:
        groups = [g for g in r.get("groups") or [] if g]
        if not groups:
            continue
        gl = {g.lower() for g in groups}
        missing = [g for g in dict.fromkeys(grouped) if g.lower() not in gl]
        unused = [g for g in groups if g.lower() not in grouped_low]
        if missing or unused:
            add(
                "warning",
                "resource_groups",
                "",
                f"Ресурс «{r['path']}» = {r.get('expression')} рассчитывается ТОЛЬКО в группировках: "
                f"{', '.join(groups)}."
                + (f" В группировках отчёта {', '.join(missing)} он считается иначе/не выводится —"
                   " значение ячейки и расшифровка могут не совпадать." if missing else "")
                + (f" Группировок {', '.join(unused)} нет в структуре варианта." if unused else ""),
                r["path"],
            )
