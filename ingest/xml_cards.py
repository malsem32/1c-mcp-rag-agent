# -*- coding: utf-8 -*-
"""Очищенные «карточки структуры» из XML объекта метаданных (вместо сырого XML).

Из ~130 КБ XML с xmlns/uuid/InternalInfo остаётся компактный текст:
  Документ АвансовыйОтчет «Авансовый отчет». Реквизиты: Организация [«Организация»; СправочникСсылка.Организации], …
Только имена, синонимы, типы и состав — то, о чём спрашивают, и то, что помещается в окно модели.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from ingest.skip_names import is_skipped_name

_TYPE_RU = {
    "CatalogRef": "СправочникСсылка", "DocumentRef": "ДокументСсылка", "EnumRef": "ПеречислениеСсылка",
    "ChartOfAccountsRef": "ПланСчетовСсылка", "ChartOfCharacteristicTypesRef": "ПВХСсылка",
    "ChartOfCalculationTypesRef": "ПВРСсылка", "ExchangePlanRef": "ПланОбменаСсылка",
    "BusinessProcessRef": "БизнесПроцессСсылка", "TaskRef": "ЗадачаСсылка",
    "DefinedType": "ОпределяемыйТип", "AnyRef": "ЛюбаяСсылка",
    "xs:string": "Строка", "xs:decimal": "Число", "xs:boolean": "Булево", "xs:dateTime": "Дата",
    "v8:ValueStorage": "ХранилищеЗначения", "v8:UUID": "УникальныйИдентификатор",
    "v8:ValueTable": "ТаблицаЗначений", "v8:ValueTree": "ДеревоЗначений", "v8:ValueListType": "СписокЗначений",
    "v8:TypeDescription": "ОписаниеТипов", "v8:StandardPeriod": "СтандартныйПериод", "xs:date": "Дата",
    "xs:int": "Число", "xs:double": "Число", "DynamicList": "ДинамическийСписок",
    "FormDataStructure": "ДанныеФормыСтруктура", "FormDataCollection": "ДанныеФормыКоллекция",
    "v8ui:FormattedString": "ФорматированнаяСтрока", "v8ui:Color": "Цвет", "v8ui:Font": "Шрифт",
    "v8ui:Picture": "Картинка", "v8:FixedStructure": "ФиксированнаяСтруктура", "v8:FixedArray": "ФиксированныйМассив",
    "v8:FixedMap": "ФиксированноеСоответствие", "v8:StandardBeginningDate": "СтандартнаяДатаНачала", "v8:Null": "Null",
}
_KIND_RU = {
    "Catalog": "Справочник", "Document": "Документ", "Enum": "Перечисление", "ChartOfAccounts": "ПланСчетов",
    "ChartOfCharacteristicTypes": "ПланВидовХарактеристик", "ChartOfCalculationTypes": "ПланВидовРасчета",
    "ExchangePlan": "ПланОбмена", "BusinessProcess": "БизнесПроцесс", "Task": "Задача",
    "InformationRegister": "РегистрСведений", "AccumulationRegister": "РегистрНакопления",
    "AccountingRegister": "РегистрБухгалтерии", "CalculationRegister": "РегистрРасчета",
    "DataProcessor": "Обработка", "Report": "Отчет", "Constant": "Константа",
}
_SUFFIX_RU = {
    "Object": "Объект", "Ref": "Ссылка", "RecordSet": "НаборЗаписей", "RecordKey": "КлючЗаписи",
    "Manager": "Менеджер", "List": "Список", "Selection": "Выборка", "RecordManager": "МенеджерЗаписи",
}
_SECTION_RU = {
    "Attribute": "Реквизиты", "Dimension": "Измерения", "Resource": "Ресурсы",
    "AddressingAttribute": "Реквизиты адресации", "Column": "Графы",
}
# свойства, важные для понимания объекта (подписки, задания, подсистемы, сервисы…)
_KEY_PROPS = {
    "Source": "Источник событий", "Event": "Событие", "Handler": "Обработчик", "MethodName": "Метод",
    "Content": "Состав подсистемы", "RootURL": "Корневой URL", "Description": "Описание",
    "BasedOn": "Ввод на основании", "Location": "Расположение",
}
_ONE_RU = {"Реквизиты": "Реквизит", "Измерения": "Измерение", "Ресурсы": "Ресурс",
           "Реквизиты адресации": "Реквизит адресации", "Графы": "Графа"}
_LIST_RU = {"Form": "Формы", "Template": "Макеты", "Command": "Команды", "EnumValue": "Значения"}


def _ln(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child(el: ET.Element | None, name: str) -> ET.Element | None:
    if el is None:
        return None
    for c in el:
        if _ln(c.tag) == name:
            return c
    return None


def _text(el: ET.Element | None, name: str) -> str:
    c = _child(el, name)
    return (c.text or "").strip() if c is not None else ""


def _synonym(props: ET.Element | None, tag: str = "Synonym") -> str:
    syn = _child(props, tag)
    if syn is None:
        return ""
    first = ""
    for item in syn:
        lang = cont = ""
        for x in item:
            if _ln(x.tag) == "lang":
                lang = (x.text or "").strip()
            elif _ln(x.tag) == "content":
                cont = (x.text or "").strip()
        if cont and lang == "ru":
            return cont
        first = first or cont
    return first


def _ru_head(head: str) -> str:
    """CatalogRef -> СправочникСсылка, TaskObject -> ЗадачаОбъект; неизвестное — как есть."""
    if head in _TYPE_RU:
        return _TYPE_RU[head]
    for suf, ru_suf in _SUFFIX_RU.items():
        if head.endswith(suf) and head[: -len(suf)] in _KIND_RU:
            return _KIND_RU[head[: -len(suf)]] + ru_suf
    return head


def _types(props: ET.Element | None) -> str:
    t = _child(props, "Type")
    if t is None:
        return ""
    out: list[str] = []
    for x in t:
        if _ln(x.tag) not in ("Type", "TypeSet"):
            continue
        v = (x.text or "").strip().replace("cfg:", "")
        if v in _TYPE_RU:
            out.append(_TYPE_RU[v])
            continue
        head, _, rest = re.sub(r"^[a-z][\w]*:", "", v).partition(".")  # v8:/v8ui:/… — служебный префикс XML
        head = _ru_head(head)
        out.append(f"{head}.{rest}" if rest else head)
    return ", ".join(out)  # составной тип целиком: по именам документов-владельцев ищут


def _field(el: ET.Element) -> str:
    p = _child(el, "Properties")
    name = _text(p, "Name")
    if not name or is_skipped_name(name):
        return ""
    meta = "; ".join(x for x in (f"«{_synonym(p)}»" if _synonym(p) else "", _types(p), *_field_flags(p)) if x)
    return f"{name} [{meta}]" if meta else name


_FILL_RU = {"ShowError": "обязательный", "ShowWarning": "проверка заполнения: предупреждение"}
_INDEX_RU = {"Index": "индексирован", "IndexWithAdditionalOrder": "индексирован с доп. упорядочиванием"}


def _field_flags(p: ET.Element | None) -> list[str]:
    """Свойства реквизита, по которым спрашивают: обязательность, индекс, подсказка, комментарий (умолчания — не пишем)."""
    out = [x for x in (_FILL_RU.get(_text(p, "FillChecking"), ""), _INDEX_RU.get(_text(p, "Indexing"), "")) if x]
    tip = " ".join(_synonym(p, "ToolTip").split())
    if tip:
        out.append("подсказка: " + tip)
    com = " ".join(_text(p, "Comment").split())
    if com:
        out.append("комментарий: " + com)
    return out


def _md_ref_ru(v: str) -> str:
    """Catalog.X → Справочник.X, DocumentObject.X → ДокументОбъект.X; прочее — как есть."""
    head, dot, rest = v.replace("cfg:", "").partition(".")
    return (_KIND_RU.get(head) or _ru_head(head)) + dot + rest


# свойства объекта, по которым спрашивают «какой регистр / кто владелец / проводится ли»; значения — по-русски
_ENUM_RU = {
    "Balance": "остатки", "Turnovers": "обороты", "RecorderSubordinate": "подчинён регистратору",
    "Independent": "независимый", "Nonperiodical": "непериодический", "Second": "в пределах секунды",
    "Day": "в пределах дня", "Month": "в пределах месяца", "Quarter": "в пределах квартала", "Year": "в пределах года",
    "RecorderPosition": "по позиции регистратора", "Allow": "разрешено", "Deny": "запрещено",
    "HierarchyFoldersAndItems": "иерархия групп и элементов", "HierarchyOfItems": "иерархия элементов",
    "ToItems": "элементам", "ToFolders": "группам", "ToFoldersAndItems": "группам и элементам",
    "String": "строка", "Number": "число", "true": "да", "false": "нет",
}
_OBJ_PROPS = (
    ("Owners", "Владельцы"), ("SubordinationUse", "Подчинение владельцу"), ("HierarchyType", "Иерархия"),
    ("CodeLength", "Длина кода"), ("CodeType", "Тип кода"), ("DescriptionLength", "Длина наименования"),
    ("Posting", "Проведение"), ("RealTimePosting", "Оперативное проведение"), ("NumberLength", "Длина номера"),
    ("NumberPeriodicity", "Периодичность номера"), ("RegisterType", "Вид регистра"),
    ("InformationRegisterPeriodicity", "Периодичность"), ("WriteMode", "Режим записи"),
    ("MainFilterOnPeriod", "Основной отбор по периоду"), ("Periodicity", "Периодичность"),
    ("ChartOfAccounts", "План счетов"), ("Correspondence", "Корреспонденция"),
    ("ChartOfCalculationTypes", "План видов расчёта"), ("ExtDimensionTypes", "Виды субконто"),
    ("MaxExtDimensionCount", "Макс. количество субконто"), ("CodeMask", "Маска кода"),
    ("RegisteredDocuments", "Документы журнала"), ("Documents", "Документы последовательности"), ("Task", "Задача"),
)


def _obj_props(props: ET.Element | None) -> list[str]:
    """«Вид регистра: остатки», «Владельцы: Справочник.X» — только заданные (непустые) свойства."""
    out: list[str] = []
    hier = _text(props, "Hierarchical") == "true"
    owners = any((x.text or "").strip() for x in (_child(props, "Owners") or []))
    for tag, label in _OBJ_PROPS:
        el = _child(props, tag)
        # вид иерархии без иерархии и подчинение без владельцев — умолчания, не свойства объекта
        if el is None or (tag == "HierarchyType" and not hier) or (tag == "SubordinationUse" and not owners):
            continue
        vals = [(x.text or "").strip() for x in el.iter() if (x.text or "").strip()]
        if not vals or (tag in ("NumberLength", "CodeLength", "DescriptionLength", "MaxExtDimensionCount") and vals == ["0"]):
            continue
        vals = [_ENUM_RU.get(v, _md_ref_ru(v) if "." in v else v) for v in vals]
        out.append(f"{label}: " + ", ".join(dict.fromkeys(vals)))
    return out


_ACC_TYPE_RU = {"Active": "активный", "Passive": "пассивный", "ActivePassive": "активно-пассивный"}


def _predefined_lines(path: Path, name: str) -> list[str]:
    """Предопределённые элементы (Class/Имя/Ext/Predefined.xml): имя, наименование, код, группа/родитель, для счетов — вид и субконто."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return []
    out: list[str] = []

    def walk(items: ET.Element, parent: str) -> None:
        for it in items:
            if _ln(it.tag) != "Item":
                continue
            nm = _text(it, "Name")
            if not nm or is_skipped_name(nm):
                continue
            desc, code = _text(it, "Description"), _text(it, "Code")
            bits = [f"«{desc}»" if desc and desc != nm else "", f"код {code}" if code else ""]
            if _text(it, "IsFolder") == "true":
                bits.append("группа")
            if parent:
                bits.append(f"в {parent}")
            if _text(it, "AccountType"):
                bits.append(_ACC_TYPE_RU.get(_text(it, "AccountType"), _text(it, "AccountType")))
            if _text(it, "OffBalance") == "true":
                bits.append("забалансовый")
            ext = [x.get("name", "").rsplit(".", 1)[-1] for x in (_child(it, "ExtDimensionTypes") or [])
                   if _ln(x.tag) == "ExtDimensionType" and x.get("name")]
            if ext:
                bits.append("субконто: " + ", ".join(ext))
            t = _types(it)
            if t:
                bits.append(f"тип: {t}")
            out.append(f"Предопределённый {name}.{nm} " + "; ".join(b for b in bits if b))
            kids = _child(it, "ChildItems")
            if kids is not None:
                walk(kids, nm)

    walk(root, "")
    return out


def object_extras(path: Path) -> tuple[list[str], list[str]]:
    """Для runtime (describe_object): свойства объекта и предопределённые элементы по Class/Имя.xml выгрузки."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return [], []
    obj = next((c for c in root if _ln(c.tag) != "InternalInfo"), None)
    props = _child(obj, "Properties")
    name = _text(props, "Name") or path.stem
    return _obj_props(props), _predefined_lines(path.with_suffix("") / "Ext" / "Predefined.xml", name)


def meta_synonym(path: Path) -> str:
    """Синоним из корневого XML объекта/формы/макета (если файл есть)."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return ""
    obj = next((c for c in root if _ln(c.tag) != "InternalInfo"), None)
    return _synonym(_child(obj, "Properties")) if obj is not None else ""


def build_cards(path: Path, class_ru: str, max_chars: int = 2800) -> list[str]:
    """Карточки объекта (1..n чанков ≤ max_chars). [] — файл не объект метаданных."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return []
    obj = next((c for c in root if _ln(c.tag) not in ("InternalInfo",)), None)
    if obj is None:
        return []
    props, kids = _child(obj, "Properties"), _child(obj, "ChildObjects")
    name = _text(props, "Name")
    if not name or is_skipped_name(name):
        return []
    syn, comment = _synonym(props), _text(props, "Comment")
    head = f"{class_ru} {name}" + (f" «{syn}»" if syn else "") + (f". {comment}" if comment else "")
    lines: list[str] = _obj_props(props)  # первыми: вид регистра/владельцы попадают в первый кусок карточки

    # каждое поле — своя строка с именем объекта: куски индекса не теряют смысл, а pack_lines не режет список посреди типа
    for tag, label in _SECTION_RU.items():
        fs = [f for c in (kids or []) if _ln(c.tag) == tag for f in [_field(c)] if f]
        if fs:
            lines.append(f"{label} объекта {name} ({len(fs)}):")
            lines += [f"{_ONE_RU.get(label, label)} {f}" for f in fs]
    for ts in (c for c in (kids or []) if _ln(c.tag) == "TabularSection"):
        tp = _child(ts, "Properties")
        tname = _text(tp, "Name")
        if is_skipped_name(tname):
            continue
        cols = [f for c in (_child(ts, "ChildObjects") or []) if _ln(c.tag) == "Attribute" for f in [_field(c)] if f]
        tsyn = _synonym(tp)
        lines.append(f"Табличная часть {tname} объекта {name}" + (f" «{tsyn}»" if tsyn else "") + f" ({len(cols)} колонок):")
        lines += [f"Колонка табличной части {tname} {f}" for f in cols]
    for tag, label in _LIST_RU.items():
        names = []
        for c in (kids or []):
            if _ln(c.tag) == tag:
                if tag == "EnumValue":
                    p = _child(c, "Properties")
                    s = _synonym(p)
                    names.append(_text(p, "Name") + (f" «{s}»" if s else ""))
                else:
                    names.append((c.text or "").strip())
        names = [n for n in names if n and not is_skipped_name(n)]
        if names:
            lines.append(f"{label}: " + ", ".join(names))
    t = _types(props)
    if t:
        lines.append(f"Тип: {t}")
    pre = _predefined_lines(path.with_suffix("") / "Ext" / "Predefined.xml", name)
    if pre:
        lines.append(f"Предопределённые элементы {class_ru}.{name} ({len(pre)}):")
        lines += pre
    for tag, label in _KEY_PROPS.items():
        el = _child(props, tag)
        if el is None:
            continue
        vals = [(x.text or "").strip() for x in el.iter() if (x.text or "").strip()] or []
        vals = [v.replace("cfg:", "") for v in vals]
        if vals:
            lines.append(f"{label}: " + ", ".join(dict.fromkeys(vals)))
    rr = _child(props, "RegisterRecords")
    if rr is not None:
        regs = [(i.text or "").strip() for i in rr if (i.text or "").strip()]
        if regs:
            lines.append("Движения по регистрам: " + ", ".join(regs))
    if not lines:
        return [head] if len(head) > 20 else []

    return pack_lines(head, lines, max_chars)


def pack_lines(head: str, lines: list[str], max_chars: int) -> list[str]:
    """Упаковка строк в чанки <= max_chars; шапка повторяется в каждом. Слишком длинные строки режутся по запятой."""
    NL = chr(10)
    out: list[str] = []
    buf: list[str] = []
    size = len(head)
    room = max(200, max_chars - len(head) - 2)
    for ln in lines:
        while len(ln) > room:
            cut = ln.rfind(", ", 0, room)
            cut = cut if cut > 0 else room
            piece, ln = ln[: cut + 1], "… " + ln[cut + 1:].lstrip()
            if buf:
                out.append(head + NL + NL.join(buf))
                buf, size = [], len(head)
            out.append(head + NL + piece)
        if buf and size + len(ln) + 1 > max_chars:
            out.append(head + NL + NL.join(buf))
            buf, size = [], len(head)
        buf.append(ln)
        size += len(ln) + 1
    if buf:
        out.append(head + NL + NL.join(buf))
    return out
