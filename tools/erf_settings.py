# -*- coding: utf-8 -*-
"""
Настройки варианта СКД внешнего отчёта: структура (группировки, таблица, диаграмма), условное оформление,
пользовательские поля, параметры вывода. Формат узлов сверен по выгрузке CF (Reports/*/Templates/*/Ext/Template.xml)
и подтверждается платформой: она разбирает собранный макет и выгружает его заново (scripts/_smoke_erf_build.py --platform).

Порядок элементов настроек по выгрузке: userFields, selection, filter, dataParameters, order, conditionalAppearance,
outputParameters, затем структура (item…).
"""
from __future__ import annotations

import re
from typing import Any

from tools.epf_build import BuildError

NL = "\r\n"

# параметры вывода: русское имя → (xsi:type, допустимые значения); значения — как в платформе (по выгрузке CF)
_TXT = ("dcsset:DataCompositionTextOutputType", ("Auto", "Output", "DontOutput"))
_PLACE = ("dcscor:DataCompositionTotalPlacement", ("Auto", "None", "Begin", "End", "BeginAndEnd"))
OUTPUT_PARAMS: dict[str, tuple[str, tuple[str, ...] | None]] = {
    "ВыводитьЗаголовок": _TXT, "ВыводитьОтбор": _TXT, "ВыводитьПараметрыДанных": _TXT,
    "РасположениеИтогов": _PLACE,
    "ВертикальноеРасположениеОбщихИтогов": _PLACE, "ГоризонтальноеРасположениеОбщихИтогов": _PLACE,
    "РасположениеРеквизитов": ("dcsset:DataCompositionAttributesPlacement", ("Auto", "Separately", "SpecialPosition", "WithOwnerField")),
    "РасположениеПолейГруппировки": ("dcsset:DataCompositionGroupFieldsPlacement",
                                     ("Auto", "Separately", "Together", "SeparatelyAndInTotal")),
    "ТипЗаголовкаПолей": ("dcscor:DataCompositionFieldsTitleType", ("Full", "Short")),
    "АвтоПозицияРесурсов": ("dcsset:DataCompositionResourcesAutoPosition", ("Auto", "DontUse", "Use")),
    "МакетОформления": ("xs:string", None),
    "ТипДиаграммы": ("v8ui:ChartType", ("Column", "Column3D", "StackedColumn", "StackedColumn3D", "Bar", "Bar3D", "StackedBar", "Line", "StackedLine",
                                         "Area", "StackedArea", "Pie", "Pie3D", "Bubble", "Stock", "Scatter")),
    "ТипДиаграммы.ВидПодписей": ("v8ui:ChartLabelType", ("None", "Value", "Series", "Point", "Percent", "ValuePercent", "SeriesPoint",
                                                        "SeriesValue", "PointValue", "SeriesPointValue")),
}
# русские названия значений → платформенные
_VALUE_ALIAS = {
    "авто": "Auto", "выводить": "Output", "невыводить": "DontOutput", "не выводить": "DontOutput", "нет": "None",
    "начало": "Begin", "конец": "End", "началоиконец": "BeginAndEnd", "начало и конец": "BeginAndEnd",
    "вместе": "Together", "отдельно": "Separately", "полный": "Full", "краткий": "Short",
    "гистограмма": "Column", "гистограмма объёмная": "Column3D", "гистограмма объемная": "Column3D", "гистограмма с накоплением": "StackedColumn",
    "график": "Line", "круговая": "Pie", "круговая объёмная": "Pie3D", "круговая объемная": "Pie3D", "область": "Area", "горизонтальная гистограмма": "Bar",
    "точечная": "Scatter", "пузырьковая": "Bubble", "биржевая": "Stock",
    "значение": "Value", "серия": "Series", "точка": "Point", "процент": "Percent", "значение и процент": "ValuePercent",
}
_ORDER = {"asc": "Asc", "desc": "Desc"}


def _x(s: Any) -> str:
    from xml.sax.saxutils import escape
    return escape(str(s))


def _title_ls(text: str, ind: str, tag: str = "dcscor:value") -> str:
    """Строка v8:LocalStringType (подпись/значение параметра оформления)."""
    return (f'{ind}<{tag} xsi:type="v8:LocalStringType">{NL}{ind}\t<v8:item>{NL}{ind}\t\t<v8:lang>ru</v8:lang>{NL}'
            f"{ind}\t\t<v8:content>{_x(text)}</v8:content>{NL}{ind}\t</v8:item>{NL}{ind}</{tag}>{NL}")


# ------------------------------------------------------------------ отборы
_CMP = {"=": "Equal", "<>": "NotEqual", ">": "Greater", ">=": "GreaterOrEqual", "<": "Less", "<=": "LessOrEqual",
        "содержит": "Contains", "не содержит": "NotContains", "начинается с": "BeginsWith", "не начинается с": "NotBeginsWith",
        "подобно": "Like", "не подобно": "NotLike",
        "заполнено": "Filled", "не заполнено": "NotFilled",
        "в списке": "InList", "не в списке": "NotInList", "в группе": "InHierarchy", "не в группе": "NotInHierarchy",
        "в списке по иерархии": "InListByHierarchy", "не в списке по иерархии": "NotInListByHierarchy"}
_CMP_NO_VALUE = ("Filled", "NotFilled")
_CMP_LIST = ("InList", "NotInList", "InListByHierarchy", "NotInListByHierarchy")
_GROUPS = {"и": "AndGroup", "and": "AndGroup", "или": "OrGroup", "or": "OrGroup", "не": "NotGroup", "not": "NotGroup"}
_VIEW = {"normal": "Normal", "обычный": "Normal",  # QuickAccess платформа без userSettingID выкидывает — не даём
         "inaccessible": "Inaccessible", "недоступен": "Inaccessible"}
# значение ссылочного типа в отборе/параметре: «Справочник.Контрагенты.ПустаяСсылка», «Перечисление.Статусы.Закрыт», «ПланСчетов.Хозрасчетный.Касса»
_REF_CLASSES = ("Справочник", "Перечисление", "ПланСчетов", "ПланВидовХарактеристик", "ПланВидовРасчета", "ПланОбмена", "БизнесПроцесс", "Задача")
_REF_RE = re.compile(r"^(" + "|".join(_REF_CLASSES) + r")\.([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)\.([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)$")


def is_design_time_value(v: Any) -> bool:
    return isinstance(v, str) and bool(_REF_RE.match(v.strip()))


def check_ref_value(v: str) -> str:
    """Проверка «Класс.Объект.Значение»: объект должен быть в каталоге этой базы (если каталог доступен)."""
    cls, obj, val = _REF_RE.match(v.strip()).groups()  # type: ignore[union-attr]
    try:
        from tools.object_catalog import catalog_by_full_name
        cat = catalog_by_full_name()
    except Exception:
        cat = {}
    if cat and f"{cls}.{obj}" not in cat:
        near = [k for k in cat if k.startswith(cls + ".") and obj[:4].lower() in k.lower()][:5]
        raise BuildError(f"Ссылочное значение «{v}»: объекта «{cls}.{obj}» нет в этой базе" + (f"; похожие: {', '.join(near)}" if near else ""))
    return f"{cls}.{obj}.{val}"


def _value_xml(fld: str, v: Any, tag: str = "right") -> str:
    from tools.erf_build import _iso
    if v in (None, ""):
        raise BuildError(f"Отбор «{fld}»: нужно значение (value/values)")
    if isinstance(v, dict) and v.get("field"):
        return f'<{tag} xsi:type="dcscor:Field">{_x(v["field"])}</{tag}>'
    sv = str(v).strip()
    if is_design_time_value(sv):
        return f'<{tag} xsi:type="dcscor:DesignTimeValue">{_x(check_ref_value(sv))}</{tag}>'
    if isinstance(v, bool) or sv.lower() in ("истина", "ложь", "true", "false"):
        b = v if isinstance(v, bool) else sv.lower() in ("истина", "true")
        return f'<{tag} xsi:type="xs:boolean">{"true" if b else "false"}</{tag}>'
    if isinstance(v, (int, float)) or re.fullmatch(r"-?\d+([.,]\d+)?", sv):
        return f'<{tag} xsi:type="xs:decimal">{sv.replace(",", ".")}</{tag}>'
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}:\d{2})?|\d{2}\.\d{2}\.\d{4}", sv):
        return f'<{tag} xsi:type="xs:dateTime">{_iso(sv)}</{tag}>'
    return f'<{tag} xsi:type="xs:string">{_x(v)}</{tag}>'


def _filter_items(items: list[dict[str, Any]], names: dict[str, str], ind: str) -> str:
    out = ""
    for f in items:
        use = "false" if f.get("use") is False else "true"
        view = ""
        if f.get("view"):
            vv = _VIEW.get(str(f["view"]).lower())
            if not vv:
                raise BuildError(f"Отбор: доступ «{f['view']}» неизвестен; доступны {', '.join(sorted(set(_VIEW.values())))}")
            view = f"{ind}\t\t<viewMode>{vv}</viewMode>{NL}"
        if f.get("group"):
            gt = _GROUPS.get(str(f["group"]).strip().lower())
            if not gt:
                raise BuildError(f"Группа отбора «{f['group']}»: доступны И, ИЛИ, НЕ")
            if not f.get("items"):
                raise BuildError("Группа отбора: нужен список items")
            out += (f'{ind}\t<item xsi:type="FilterItemGroup">{NL}{ind}\t\t<use>{use}</use>{NL}{ind}\t\t<groupType>{gt}</groupType>{NL}'
                    + _filter_items(f["items"], names, ind + "\t") + view + f"{ind}\t</item>{NL}")
            continue
        fld = names.get(str(f.get("field", "")).strip().lower())
        if not fld:
            raise BuildError(f"Отбор по «{f.get('field')}» — такого поля нет среди колонок запроса: {', '.join(names.values())}")
        op = str(f.get("op") or "=").strip().lower()
        cmp_ = _CMP.get(op) or next((v for v in _CMP.values() if v.lower() == op), None)
        if not cmp_:
            raise BuildError(f"Отбор «{fld}»: вид сравнения «{f.get('op')}» неизвестен; доступны {', '.join(_CMP)}")
        rights = ""
        if cmp_ in _CMP_LIST and f.get("values") is not None:
            vals = f["values"] if isinstance(f["values"], (list, tuple)) else [f["values"]]
            for v in vals:
                rights += f"{ind}\t\t{_value_xml(fld, v)}{NL}"
        elif cmp_ not in _CMP_NO_VALUE and not (cmp_ in _CMP_LIST and f.get("value") is None):
            rights = f"{ind}\t\t{_value_xml(fld, f.get('value'))}{NL}"
        # InList без значений — пользователь заполнит список в настройках (как в типовых отчётах)
        out += (f'{ind}\t<item xsi:type="FilterItemComparison">{NL}{ind}\t\t<use>{use}</use>{NL}'
                f'{ind}\t\t<left xsi:type="dcscor:Field">{fld}</left>{NL}{ind}\t\t<comparisonType>{cmp_}</comparisonType>{NL}'
                f"{rights}{view}{ind}\t</item>{NL}")
    return out


def filters_xml(filters: list[dict[str, Any]], names: dict[str, str], ind: str = "\t") -> str:
    """Отборы: [{field, op, value|values, use, view}] и группы [{group: И|ИЛИ|НЕ, items:[…]}]; поле — колонка, вычисляемое или пользовательское."""
    if not filters:
        return ""
    return f"{ind}<filter>{NL}{_filter_items(filters, names, ind)}{ind}</filter>{NL}"


# ------------------------------------------------------------------ параметры вывода
def _font_xml(f: dict[str, Any], ind: str, tag: str = "dcscor:value") -> str:
    b = lambda k: "true" if f.get(k) else "false"
    if f.get("name"):
        attrs = f'faceName="{_x(f["name"])}" height="{_x(f.get("size", 10))}" kind="Absolute"'
    else:
        attrs = 'ref="sys:DefaultGUIFont" kind="WindowsFont"'
    return (f'{ind}<{tag} xmlns:sys="http://v8.1c.ru/8.1/data/ui/fonts/system" xsi:type="v8ui:Font" {attrs} '
            f'bold="{b("bold")}" italic="{b("italic")}" underline="{b("underline")}" strikeout="{b("strikeout")}"/>{NL}')


def _is_color(v: Any) -> bool:
    s = str(v).strip()
    return bool(re.fullmatch(r"#[0-9A-Fa-f]{6}", s) or re.match(r"^(style|web|win):\w+$", s) or s.lower() in _COLORS)


def _color_value(v: Any) -> str:
    s = str(v).strip()
    return s if re.match(r"^(style|web|win):", s) else _color(s)


def _param_item(name: str, val: Any, ind: str) -> str:
    """Параметр вывода {Имя: значение | {value, use, params:{подпараметры}, type}}; тип — по таблице, по виду значения или явный."""
    use, nested, explicit = True, {}, None
    if isinstance(val, dict) and ("value" in val or "font" in val or "params" in val or "use" in val):
        use = val.get("use", True) is not False
        nested = val.get("params") or {}
        explicit = val.get("type")
        val = val["font"] if "font" in val else val.get("value")
    leaf = name.split(".")[-1]
    head = f'{ind}<dcscor:item xsi:type="dcsset:SettingsParameterValue">{NL}'
    if not use:
        head += f"{ind}\t<dcscor:use>false</dcscor:use>{NL}"
    head += f"{ind}\t<dcscor:parameter>{_x(name)}</dcscor:parameter>{NL}"
    body = ""
    spec = OUTPUT_PARAMS.get(name)
    if val is None and not nested:
        raise BuildError(f"Параметр вывода «{name}»: нужно значение")
    if val is None:
        body = ""
    elif isinstance(val, dict):  # шрифт
        body = _font_xml(val, ind + "\t")
    elif spec:
        typ, allowed = spec
        v = _VALUE_ALIAS.get(str(val).strip().lower(), str(val).strip())
        if allowed and v not in allowed:
            raise BuildError(f"Параметр вывода «{name}»: значение «{val}» не из списка {', '.join(allowed)}")
        body = f'{ind}\t<dcscor:value xsi:type="{typ}">{_x(v)}</dcscor:value>{NL}'
    elif explicit:
        body = f'{ind}\t<dcscor:value xsi:type="{_x(explicit)}">{_x(val)}</dcscor:value>{NL}'
    elif leaf.startswith("Цвет") or (isinstance(val, str) and _is_color(val)):
        body = f'{ind}\t<dcscor:value xsi:type="v8ui:Color">{_x(_color_value(val))}</dcscor:value>{NL}'
    elif isinstance(val, bool):
        body = f'{ind}\t<dcscor:value xsi:type="xs:boolean">{"true" if val else "false"}</dcscor:value>{NL}'
    elif isinstance(val, (int, float)):
        body = f'{ind}\t<dcscor:value xsi:type="xs:decimal">{val}</dcscor:value>{NL}'
    else:
        body = f'{ind}\t<dcscor:value xsi:type="xs:string">{_x(val)}</dcscor:value>{NL}'
    sub = "".join(_param_item(f"{name}.{k}", v, ind + "\t") for k, v in nested.items())
    return head + body + sub + f"{ind}</dcscor:item>{NL}"


def _nest_params(params: dict[str, Any] | None) -> dict[str, Any]:
    """«ТипДиаграммы.ЦветФона» при наличии «ТипДиаграммы» становится вложенным параметром (так платформа и хранит); иначе остаётся как есть."""
    out = dict(params or {})
    for k in [k for k in out if "." in k]:
        parent, leaf = k.rsplit(".", 1)
        if parent in out:
            pv = out[parent]
            pv = dict(pv) if isinstance(pv, dict) and ("value" in pv or "params" in pv) else {"value": pv}
            pv["params"] = {**(pv.get("params") or {}), leaf: out.pop(k)}
            out[parent] = pv
    return out


def output_params_xml(params: dict[str, Any] | None, ind: str, title: str | None = None) -> str:
    """Параметры вывода варианта/группировки/таблицы/диаграммы; неизвестные платформе имена она при загрузке молча отбрасывает —
    сборка с проверкой платформой сообщает их в verify.dropped_parameters."""
    params = _nest_params(params)
    items = ""
    if title is not None and "Заголовок" not in (params or {}):
        items += _param_item("Заголовок", title, ind + "\t")
    for name, val in (params or {}).items():
        items += _param_item(name, val, ind + "\t")
    return f"{ind}<outputParameters>{NL}{items}{ind}</outputParameters>{NL}" if items else ""


# ------------------------------------------------------------------ структура
def _group_items(fields: list[str], names: dict[str, str], ind: str) -> str:
    if not fields:
        return ""
    out = ""
    for f in fields:
        n = names.get(str(f).strip().lower())
        if not n:
            raise BuildError(f"Группировка по «{f}» — такого поля нет среди колонок запроса: {', '.join(names.values())}")
        out += (f'{ind}\t\t<item xsi:type="GroupItemField">{NL}{ind}\t\t\t<field>{n}</field>{NL}{ind}\t\t\t<groupType>Items</groupType>{NL}'
                f"{ind}\t\t\t<periodAdditionType>None</periodAdditionType>{NL}"
                f'{ind}\t\t\t<periodAdditionBegin xsi:type="xs:dateTime">0001-01-01T00:00:00</periodAdditionBegin>{NL}'
                f'{ind}\t\t\t<periodAdditionEnd xsi:type="xs:dateTime">0001-01-01T00:00:00</periodAdditionEnd>{NL}{ind}\t\t</item>{NL}')
    return f"{ind}\t<groupItems>{NL}{out}{ind}\t</groupItems>{NL}"


def _order_sel(node: dict[str, Any], names: dict[str, str], ind: str) -> str:
    od = ""
    if node.get("order"):
        od = f"{ind}\t<order>{NL}" + "".join(_order_item(o, names, ind + "\t\t") for o in node["order"]) + f"{ind}\t</order>{NL}"
    else:
        od = f'{ind}\t<order>{NL}{ind}\t\t<item xsi:type="OrderItemAuto"/>{NL}{ind}\t</order>{NL}'
    if node.get("select"):
        fl = ""
        for f in node["select"]:
            n = names.get(str(f).strip().lower())
            if not n:
                raise BuildError(f"Поле группировки «{f}» — такого поля нет среди колонок запроса: {', '.join(names.values())}")
            fl += f'{ind}\t\t<item xsi:type="SelectedItemField">{NL}{ind}\t\t\t<field>{n}</field>{NL}{ind}\t\t</item>{NL}'
        sel = f"{ind}\t<selection>{NL}{fl}{ind}\t</selection>{NL}"
    else:
        sel = f'{ind}\t<selection>{NL}{ind}\t\t<item xsi:type="SelectedItemAuto"/>{NL}{ind}\t</selection>{NL}'
    return od + sel


def _order_item(o: Any, names: dict[str, str], ind: str) -> str:
    parts = str(o).split()
    n = names.get(parts[0].lower()) if parts else None
    if not n:
        raise BuildError(f"Сортировка по «{o}» — такого поля нет среди колонок запроса: {', '.join(names.values())}")
    d = "Desc" if len(parts) > 1 and parts[1].lower().startswith(("убыв", "desc")) else "Asc"
    return f'{ind}<item xsi:type="OrderItemField">{NL}{ind}\t<field>{n}</field>{NL}{ind}\t<orderType>{d}</orderType>{NL}{ind}</item>{NL}'


def _group_node(node: dict[str, Any], names: dict[str, str], ind: str, ctx: dict[str, Any]) -> str:
    """StructureItemGroup: {group:[поля], select, order, filters, output, items:[вложенные]}."""
    g = node.get("group", [])
    g = [g] if isinstance(g, str) else list(g or [])
    inner = "".join(_structure_node(n, names, ind + "\t", ctx) for n in node.get("items") or [])
    flt = filters_xml(node.get("filters") or [], names, ind + "\t") if node.get("filters") else ""
    return (f'{ind}<item xsi:type="StructureItemGroup">{NL}{_group_items(g, names, ind)}{flt}{_order_sel(node, names, ind)}'
            f'{output_params_xml(node.get("output"), ind + chr(9)) or ind + chr(9) + "<outputParameters/>" + NL}{inner}{ind}</item>{NL}')


def _axis(tag: str, chain: Any, names: dict[str, str], ind: str, node_extra: dict[str, Any] | None = None) -> str:
    """Строки или колонки таблицы/точки или серии диаграммы: цепочка полей — вложенные группировки без xsi:type."""
    if isinstance(chain, dict):
        chain = chain.get("group", [])
    chain = [chain] if isinstance(chain, str) else list(chain or [])

    def build(i: int, t: str, ind2: str) -> str:
        head = f"{ind2}<{t}>{NL}" if t else ""
        tail = f"{ind2}</{t}>{NL}" if t else ""
        if i >= len(chain):
            return ""
        nested = build(i + 1, "item", ind2 + "\t")
        return (f"{head}{_group_items([chain[i]], names, ind2)}{_order_sel({}, names, ind2)}"
                f"{nested}{tail}")

    return build(0, tag, ind)


def _structure_node(node: dict[str, Any], names: dict[str, str], ind: str, ctx: dict[str, Any]) -> str:
    if "table" in node:
        t = node["table"]
        cols = _axis("column", t.get("columns") or [], names, ind + "\t")
        rows = _axis("row", t.get("rows") or [], names, ind + "\t")
        out = output_params_xml(t.get("output"), ind + "\t")
        flt = filters_xml(t.get("filters") or [], names, ind + "\t") if t.get("filters") else ""
        return f'{ind}<item xsi:type="StructureItemTable">{NL}{cols}{rows}{flt}{out}{ind}</item>{NL}'
    if "chart" in node:
        c = node["chart"]
        pts = _axis("point", c.get("points") or [], names, ind + "\t")
        ser = _axis("series", c.get("series") or [], names, ind + "\t")
        out = output_params_xml(c.get("output"), ind + "\t")
        if c.get("select"):  # какие поля (ресурсы) выводятся значениями диаграммы
            fl = ""
            for f in c["select"]:
                n = names.get(str(f).strip().lower())
                if not n:
                    raise BuildError(f"Диаграмма: поля «{f}» нет среди колонок запроса: {', '.join(names.values())}")
                fl += f'{ind}\t\t<item xsi:type="SelectedItemField">{NL}{ind}\t\t\t<field>{n}</field>{NL}{ind}\t\t</item>{NL}'
            sel = f"{ind}\t<selection>{NL}{fl}{ind}\t</selection>{NL}"
        else:
            sel = f'{ind}\t<selection>{NL}{ind}\t\t<item xsi:type="SelectedItemAuto"/>{NL}{ind}\t</selection>{NL}'
        return f'{ind}<item xsi:type="StructureItemChart">{NL}{pts}{ser}{sel}{out}{ind}</item>{NL}'
    return _group_node(node, names, ind, ctx)


# ------------------------------------------------------------------ условное оформление
_COLORS = {"красный": "#FF0000", "зелёный": "#008000", "зеленый": "#008000", "синий": "#0000FF", "чёрный": "#000000", "черный": "#000000",
           "белый": "#FFFFFF", "серый": "#808080", "жёлтый": "#FFFF00", "желтый": "#FFFF00", "оранжевый": "#FFA500",
           "розовый": "#FFC0CB", "светло-серый": "#D3D3D3", "светло-зелёный": "#90EE90", "светло-красный": "#FFB6C1"}


def _color(c: str) -> str:
    c = str(c).strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", c):
        return c.upper()
    v = _COLORS.get(c.lower())
    if not v:
        raise BuildError(f"Цвет «{c}»: нужен #RRGGBB или название ({', '.join(_COLORS)})")
    return v


def appearance_xml(items: list[dict[str, Any]], names: dict[str, str], ind: str) -> str:
    """Условное оформление: [{fields, filters, text_color, back_color, bold, italic, underline, format, text}]."""
    if not items:
        return ""
    out = ""
    for it in items:
        sel = ""
        if it.get("fields"):
            fl = ""
            for f in it["fields"]:
                n = names.get(str(f).strip().lower())
                if not n:
                    raise BuildError(f"Оформление: поля «{f}» нет среди колонок запроса: {', '.join(names.values())}")
                fl += f"{ind}\t\t\t<item>{NL}{ind}\t\t\t\t<field>{n}</field>{NL}{ind}\t\t\t</item>{NL}"
            sel = f"{ind}\t\t<selection>{NL}{fl}{ind}\t\t</selection>{NL}"
        flt = filters_xml(it.get("filters") or [], names, ind + "\t\t") if it.get("filters") else ""
        ap = ""
        pi = f'{ind}\t\t\t<dcscor:item xsi:type="dcsset:SettingsParameterValue">{NL}{ind}\t\t\t\t<dcscor:parameter>'

        def simple(name: str, typ: str, val: str) -> str:
            return f'{pi}{name}</dcscor:parameter>{NL}{ind}\t\t\t\t<dcscor:value xsi:type="{typ}">{_x(val)}</dcscor:value>{NL}{ind}\t\t\t</dcscor:item>{NL}'

        if it.get("text_color"):
            ap += simple("ЦветТекста", "v8ui:Color", _color(it["text_color"]))
        if it.get("back_color"):
            ap += simple("ЦветФона", "v8ui:Color", _color(it["back_color"]))
        if any(it.get(k) is not None for k in ("bold", "italic", "underline")):
            b = lambda k: "true" if it.get(k) else "false"
            ap += (f'{pi}Шрифт</dcscor:parameter>{NL}{ind}\t\t\t\t<dcscor:value xmlns:sys="http://v8.1c.ru/8.1/data/ui/fonts/system" '
                   f'xsi:type="v8ui:Font" ref="sys:DefaultGUIFont" bold="{b("bold")}" italic="{b("italic")}" underline="{b("underline")}" '
                   f'strikeout="false" kind="WindowsFont"/>{NL}{ind}\t\t\t</dcscor:item>{NL}')
        for key, pname in (("format", "Формат"), ("text", "Текст")):
            if it.get(key):
                ap += f'{pi}{pname}</dcscor:parameter>{NL}' + _title_ls(it[key], f"{ind}\t\t\t\t") + f"{ind}\t\t\t</dcscor:item>{NL}"
        for pname, pval in (it.get("params") or {}).items():  # произвольные параметры оформления (ширина, выравнивание, цвет серии…)
            ap += _param_item(pname, pval, f"{ind}\t\t\t")
        if not ap:
            raise BuildError("Условное оформление: нужен хотя бы один параметр (text_color, back_color, bold, italic, underline, format, text, params)")
        out += (f"{ind}\t<item>{NL}{sel}{flt}{ind}\t\t<appearance>{NL}{ap}{ind}\t\t</appearance>{NL}{ind}\t</item>{NL}")
    return f"{ind}<conditionalAppearance>{NL}{out}{ind}</conditionalAppearance>{NL}"


# ------------------------------------------------------------------ пользовательские поля
def user_fields_xml(fields: list[dict[str, Any]], names: dict[str, str], ind: str) -> tuple[str, list[str]]:
    """[{name, title, expression} | {name, title, cases:[{filters, value}], default?}] → (XML, имена dataPath)."""
    if not fields:
        return "", []
    out, paths = "", []
    for f in fields:
        nm = str(f.get("name", "")).strip()
        if not re.fullmatch(r"[A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*", nm):
            raise BuildError(f"Пользовательское поле «{nm}»: имя — буквы/цифры/подчёркивание")
        path = f"UserFields.{nm}"
        paths.append(path)
        title = _title_ls(f.get("title") or nm, f"{ind}\t\t", "lwsTitle").replace(' xsi:type="v8:LocalStringType"', "")
        if f.get("cases"):
            cs = ""
            for c in f["cases"]:
                v = c.get("value")
                vtype = "xs:decimal" if isinstance(v, (int, float)) and not isinstance(v, bool) else "xs:string"
                cs += (f"{ind}\t\t\t<item>{NL}{filters_xml(c.get('filters') or [], names, ind + chr(9) * 3)}"
                       f'{ind}\t\t\t\t<value xsi:type="{vtype}">{_x(v)}</value>{NL}{ind}\t\t\t</item>{NL}')
            out += (f'{ind}\t<item xsi:type="UserFieldCase">{NL}{ind}\t\t<dataPath>{path}</dataPath>{NL}{title}'
                    f"{ind}\t\t<cases>{NL}{cs}{ind}\t\t</cases>{NL}{ind}\t</item>{NL}")
        elif f.get("expression"):
            exp = _x(f["expression"])
            tot = _x(f.get("total_expression") or f["expression"])
            out += (f'{ind}\t<item xsi:type="UserFieldExpression">{NL}{ind}\t\t<dataPath>{path}</dataPath>{NL}{title}'
                    f"{ind}\t\t<detailExpression>{exp}</detailExpression>{NL}{ind}\t\t<totalExpression>{tot}</totalExpression>{NL}{ind}\t</item>{NL}")
        else:
            raise BuildError(f"Пользовательское поле «{nm}»: нужно expression или cases")
    return f"{ind}<userFields>{NL}{out}{ind}</userFields>{NL}", paths


# ------------------------------------------------------------------ вариант целиком
_NS = ('xmlns="http://v8.1c.ru/8.1/data-composition-system/settings" xmlns:dcsset="http://v8.1c.ru/8.1/data-composition-system/settings" xmlns:dcscor="http://v8.1c.ru/8.1/data-composition-system/core" '
       'xmlns:style="http://v8.1c.ru/8.1/data/ui/style" xmlns:sys="http://v8.1c.ru/8.1/data/ui/fonts/system" '
       'xmlns:v8="http://v8.1c.ru/8.1/data/core" xmlns:v8ui="http://v8.1c.ru/8.1/data/ui" '
       'xmlns:web="http://v8.1c.ru/8.1/data/ui/colors/web" xmlns:win="http://v8.1c.ru/8.1/data/ui/colors/windows" '
       'xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"')


def variant_xml(v: dict[str, Any], *, head: str, names: dict[str, str], field_names: list[str], data_params: str, title: str) -> str:
    """Настройки одного варианта; names — имена колонок/вычисляемых полей (нижний регистр → имя)."""
    nm = dict(names)
    uf_xml, uf_paths = user_fields_xml(v.get("user_fields") or [], nm, "\t")
    for p in uf_paths:
        nm[p.split(".", 1)[1].lower()] = p
    chosen = v.get("select")
    sel_names = [nm[str(f).strip().lower()] if str(f).strip().lower() in nm else _bad(f, nm) for f in chosen] if chosen else list(field_names) + uf_paths
    sel = "".join(f'\t\t<item xsi:type="SelectedItemField">{NL}\t\t\t<field>{f}</field>{NL}\t\t</item>{NL}' for f in sel_names)
    flt = filters_xml(v.get("filters") or [], nm, "\t") if v.get("filters") else ""
    dp = f"\t<dataParameters>{NL}{data_params}\t</dataParameters>{NL}" if data_params else ""
    order = v.get("order") or []
    od = (f"\t<order>{NL}" + "".join(_order_item(o, nm, "\t\t") for o in order) + f"\t</order>{NL}") if order else ""
    ca = appearance_xml(v.get("appearance") or [], nm, "\t")
    outp = output_params_xml(v.get("output"), "\t", title)
    structure = v.get("structure")
    if structure is None:
        chain = v.get("group_by") or []
        structure = []
        for f in reversed(chain):
            structure = [{"group": [f], "items": structure}]
        if not chain:
            structure = [{"group": []}]
    inner = "".join(_structure_node(n, nm, "\t", {}) for n in structure)
    return f"{head}<Settings {_NS}>{NL}{uf_xml}\t<selection>{NL}{sel}\t</selection>{NL}{flt}{dp}{od}{ca}{outp}{inner}</Settings>"


def _bad(f: Any, nm: dict[str, str]) -> str:
    raise BuildError(f"Поле «{f}» — такого поля нет среди колонок запроса: {', '.join(nm.values())}")
