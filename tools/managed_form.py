# -*- coding: utf-8 -*-
"""
Раскладка управляемой формы (Form.xml) в единый формат «как видит пользователь»:
страницы → элементы, кнопки → команды → обработчики, события формы, реквизиты.

Тот же формат отдаёт разбор обычных форм (ordinary_form.py), поэтому типы элементов — по-русски,
а поля не зависят от платформенных имён тегов. Только стандартная библиотека; namespace снимается,
отсутствующие узлы дают пустые значения, а не исключения.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

try:
    # стандартные команды платформы → подпись на экране (общий словарь с explain_command)
    from tools.form_command import _STD_COMMANDS
except Exception:  # pragma: no cover - разбор формы не должен падать из-за импорта соседа
    _STD_COMMANDS = {}

# тег Form.xml → тип элемента по-русски (общий словарь с обычными формами)
_TYPE_RU = {
    "InputField": "ПолеВвода",
    "LabelField": "ПолеНадписи",
    "LabelDecoration": "Надпись",
    "PictureDecoration": "Картинка",
    "Button": "Кнопка",
    "Table": "Таблица",
    "UsualGroup": "Группа",
    "ColumnGroup": "ГруппаКолонок",
    "ButtonGroup": "ГруппаКнопок",
    "Pages": "ГруппаСтраниц",
    "Page": "Страница",
    "Popup": "Подменю",
    "CommandBar": "КоманднаяПанель",
    "AutoCommandBar": "КоманднаяПанель",
    "CheckBoxField": "Флажок",
    "RadioButtonField": "Переключатель",
    "PictureField": "ПолеКартинки",
    "ProgressBarField": "ИндикаторПрогресса",
    "TrackBarField": "Регулятор",
    "TextDocumentField": "ПолеТекстовогоДокумента",
    "SpreadSheetDocumentField": "ПолеТабличногоДокумента",
    "HTMLDocumentField": "ПолеHTMLДокумента",
    "FormattedDocumentField": "ПолеФорматированногоДокумента",
    "GraphicalSchemaField": "ПолеГрафическойСхемы",
    "GeographicalSchemaField": "ПолеГеографическойСхемы",
    "CalendarField": "ПолеКалендаря",
    "ChartField": "ПолеДиаграммы",
    "GanttChartField": "ПолеДиаграммыГанта",
    "DendrogramField": "ПолеДендрограммы",
    "PlannerField": "ПолеПланировщика",
    "PeriodField": "ПолеПериода",
}

# служебные «дети» элемента: на экране отдельными элементами не видны
_AUX_TAGS = {
    "ContextMenu", "ExtendedTooltip", "SearchStringAddition", "ViewStatusAddition",
    "SearchControlAddition", "Events", "Title", "ToolTip",
}


def _local(tag: Any) -> str:
    """Имя тега без namespace: «{http://...logform}InputField» → «InputField»."""
    return tag.split("}", 1)[-1] if isinstance(tag, str) else ""


def _child(node: ET.Element | None, name: str) -> ET.Element | None:
    if node is None:
        return None
    for ch in node:
        if _local(ch.tag) == name:
            return ch
    return None


def _children(node: ET.Element | None, name: str) -> list[ET.Element]:
    """Дети узла-контейнера name (Commands/Attributes/Columns); нет узла — пустой список."""
    c = _child(node, name)
    return list(c) if c is not None else []


def _text(node: ET.Element | None, name: str) -> str:
    ch = _child(node, name)
    return (ch.text or "").strip() if ch is not None else ""


def _bool(node: ET.Element | None, name: str, default: bool) -> bool:
    v = _text(node, name).lower()
    if v in ("true", "истина"):
        return True
    if v in ("false", "ложь"):
        return False
    return default


def _lstr(node: ET.Element | None, name: str = "Title") -> str:
    """Многоязычная строка (v8:item/v8:lang/v8:content): ru, иначе первый язык, иначе текст узла."""
    t = _child(node, name)
    if t is None:
        return ""
    first = ""
    for item in t:
        if _local(item.tag) != "item":
            continue
        lang = content = ""
        for x in item:
            ln = _local(x.tag)
            if ln == "lang":
                lang = (x.text or "").strip()
            elif ln == "content":
                content = (x.text or "").strip()
        if lang == "ru" and content:
            return content
        first = first or content
    return first or (t.text or "").strip()


def _events(node: ET.Element | None) -> dict[str, str]:
    """<Events><Event name="OnChange">Обработчик</Event> → {"OnChange": "Обработчик"}."""
    out: dict[str, str] = {}
    for e in _children(node, "Events"):
        if _local(e.tag) != "Event":
            continue
        name, h = e.get("name") or "", (e.text or "").strip()
        if not name or not h:
            continue
        # расширения: callType Before/After/Override — отдельный ключ, чтобы не затирать основной
        ct = e.get("callType") or ""
        out[f"{name}:{ct}" if ct else name] = h
    return out


def _type_str(node: ET.Element | None) -> str:
    """Тип реквизита: «cfg:CatalogObject.X» → «CatalogObject.X»; составной — через запятую."""
    parts = []
    for x in _children(node, "Type"):
        if _local(x.tag) in ("Type", "TypeSet", "TypeId") and x.text:
            parts.append(x.text.strip().split(":", 1)[-1])
    return ", ".join(parts)


def _parse_root(xml_text: str) -> ET.Element:
    text = (xml_text or "").lstrip("﻿").strip()
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        # битая выгрузка: убираем XML-декларацию и недопустимые управляющие символы, вторая попытка
        text = re.sub(r"^<\?xml[^>]*\?>", "", text)
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        return ET.fromstring(text)


def _empty(error: str = "") -> dict[str, Any]:
    d: dict[str, Any] = {
        "kind": "managed", "title": "", "pages": [], "elements": [], "buttons": [],
        "form_events": {}, "attributes": [], "commands": [],
        "handlers_missing_in_module": [], "module_checked": False,
    }
    if error:
        d["error"] = error
    return d


def _module_procs(module_text: str) -> set[str]:
    """Имена процедур/функций модуля формы (регистр не важен — как в 1С)."""
    return {
        m.group(1).lower()
        for m in re.finditer(
            r"(?im)^\s*(?:Асинх\s+|Async\s+)?(?:Процедура|Функция|Procedure|Function)\s+([\wА-Яа-яЁё]+)\s*\(",
            module_text or "",
        )
    }


def managed_form_layout(xml_text: str, module_text: str = "") -> dict[str, Any]:
    """
    Form.xml управляемой формы → единый формат раскладки формы.

    Порядок elements — как в XML (обход в глубину). parent — ближайший контейнер
    (группа / страница / таблица / командная панель), page — заголовок ближайшей страницы.
    Если передан module_text — handlers_missing_in_module: обработчики из XML, которых нет в модуле
    (module_checked=True; без модуля список пуст и module_checked=False).
    """
    try:
        root = _parse_root(xml_text)
    except ET.ParseError as e:
        return _empty(f"Form.xml не разобран: {e}")
    if _local(root.tag) != "Form":
        # на вход мог прийти документ-обёртка — ищем узел Form внутри
        found = next((n for n in root.iter() if _local(n.tag) == "Form"), None)
        if found is None:
            return _empty("в XML нет узла Form")
        root = found

    res = _empty()
    res["title"] = _lstr(root, "Title")  # при AutoTitle=true заголовок в XML пуст (берётся из синонима объекта)
    res["form_events"] = _events(root)

    # --- команды формы ---
    cmd_by_name: dict[str, dict[str, Any]] = {}
    for c in _children(root, "Commands"):
        if _local(c.tag) != "Command" or not c.get("name"):
            continue
        row = {"name": c.get("name"), "caption": _lstr(c, "Title"), "action": _text(c, "Action")}
        cmd_by_name[row["name"]] = row
        res["commands"].append(row)

    # --- реквизиты формы ---
    for a in _children(root, "Attributes"):
        if _local(a.tag) != "Attribute" or not a.get("name"):
            continue
        attr: dict[str, Any] = {
            "name": a.get("name"),
            "type": _type_str(a),
            "caption": _lstr(a, "Title"),
            "main": _bool(a, "MainAttribute", False),
        }
        cols = [c.get("name") for c in _children(a, "Columns") if _local(c.tag) == "Column" and c.get("name")]
        if cols:
            attr["columns"] = cols  # колонки реквизита-таблицы значений / дерева
        res["attributes"].append(attr)

    def _cmd_caption(cmd: str) -> str:
        last = cmd.split(".")[-1] if cmd else ""
        if cmd.startswith("Form.Command."):
            return (cmd_by_name.get(last) or {}).get("caption") or ""
        if ".StandardCommand." in cmd:
            return _STD_COMMANDS.get(last, "")
        return ""

    def _cmd_handler(cmd: str) -> str:
        if cmd.startswith("Form.Command."):
            return (cmd_by_name.get(cmd.split(".")[-1]) or {}).get("action") or ""
        return ""

    # --- дерево элементов ---
    elements: list[dict[str, Any]] = res["elements"]
    pages: list[dict[str, Any]] = res["pages"]

    def _walk(node: ET.Element, parent: str, page: dict[str, Any] | None, open_pages: list[dict[str, Any]]) -> None:
        """Дети node в порядке XML: ChildItems и AutoCommandBar (у формы и таблиц)."""
        for ch in node:
            ln = _local(ch.tag)
            if ln == "ChildItems":
                for item in ch:
                    _element(item, parent, page, open_pages)
            elif ln == "AutoCommandBar":
                _element(ch, parent, page, open_pages)

    def _element(item: ET.Element, parent: str, page: dict[str, Any] | None, open_pages: list[dict[str, Any]]) -> None:
        tag = _local(item.tag)
        name = item.get("name") or ""
        if not name or tag in _AUX_TAGS:
            return
        # пустая автокомандная панель (есть у каждой формы/таблицы) — шум; панель с кнопками оставляем
        if tag == "AutoCommandBar" and not len(_children(item, "ChildItems")):
            return
        cmd = _text(item, "CommandName")
        caption = _lstr(item, "Title") or _cmd_caption(cmd)
        el: dict[str, Any] = {
            "name": name,
            "type": _TYPE_RU.get(tag, tag),
            "caption": caption,
            "data_path": _text(item, "DataPath"),
            "parent": parent or None,
            "page": (page["caption"] or page["name"]) if page else None,
            "visible": _bool(item, "Visible", True),
            "enabled": _bool(item, "Enabled", True),
            "read_only": _bool(item, "ReadOnly", False),
            "events": _events(item),
            "command": cmd or None,
        }
        elements.append(el)
        for p in open_pages:
            p["elements"].append(name)  # элемент вложенной страницы виден и во внешней
        if tag == "Button":
            res["buttons"].append({
                "caption": caption, "name": name, "handler": _cmd_handler(cmd) or None, "command": cmd or None,
            })
        child_page, child_open = page, open_pages
        if tag == "Page":
            pg = {"panel": parent or None, "name": name, "caption": caption, "visible": el["visible"], "elements": []}
            pages.append(pg)
            child_page, child_open = pg, open_pages + [pg]
        _walk(item, name, child_page, child_open)

    _walk(root, "", None, [])

    # --- обработчики из XML, которых нет в модуле формы ---
    if module_text:
        procs = _module_procs(module_text)
        missing: list[dict[str, str]] = []

        def _check(handler: str, where: str) -> None:
            if handler and handler.lower() not in procs:
                missing.append({"handler": handler, "where": where})

        for ev, h in res["form_events"].items():
            _check(h, f"Форма.{ev}")
        for el in elements:
            for ev, h in el["events"].items():
                _check(h, f"{el['name']}.{ev}")
        for c in res["commands"]:
            _check(c["action"], f"Команда.{c['name']}")
        res["handlers_missing_in_module"] = missing
        res["module_checked"] = True
    return res
