# -*- coding: utf-8 -*-
"""Карточки форм (Form.xml) и макетов (Template.xml: печатные формы MXL и схемы компоновки СКД).

Из сотен килобайт XML остаётся то, о чём спрашивают: какие реквизиты/команды/элементы на форме,
какие обработчики к чему привязаны, какие области, параметры и поля у макета.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from ingest.skip_names import is_skipped_name
from ingest.xml_cards import _child, _ln, _text, _types, pack_lines

# элементы формы -> подпись; всё остальное (служебные дополнения) пропускаем
_ELEM_RU = {
    "InputField": "Поле", "LabelField": "Поле-надпись", "CheckBoxField": "Флажок", "RadioButtonField": "Переключатель",
    "PictureField": "Поле картинки", "SpreadSheetDocumentField": "Табличный документ", "TextDocumentField": "Текстовый документ",
    "HTMLDocumentField": "HTML-поле", "CalendarField": "Календарь", "ProgressBarField": "Индикатор", "TrackBarField": "Ползунок",
    "Table": "Таблица", "Button": "Кнопка", "UsualGroup": "Группа", "ColumnGroup": "Группа колонок", "Pages": "Страницы",
    "Page": "Страница", "CommandBar": "Командная панель", "ButtonGroup": "Группа кнопок", "Popup": "Подменю",
    "LabelDecoration": "Надпись", "PictureDecoration": "Картинка",
}
_SKIP = {"ContextMenu", "ExtendedTooltip", "SearchStringAddition", "ViewStatusAddition", "SearchControlAddition", "AutoCommandBar"}
_MAX_DEPTH = 5  # только отступ; обход дерева — на всю глубину
# модуль формы меняет свойства элементов: Элементы.X.Доступность = …, ЭлементыФормы.X.Видимость = …, БСП УстановитьСвойствоЭлементаФормы
_UI_PROPS = ("Видимость", "Доступность", "ТолькоПросмотр")
_UI_SET = re.compile(r"(?<![\wА-Яа-яЁё])(?:ЭлементыФормы|Элементы)\.([\wА-Яа-яЁё]+)\.(Видимость|Доступность|ТолькоПросмотр)\s*=")
_UI_SET_BSP = re.compile(r'УстановитьСвойствоЭлементаФормы\s*\(\s*[\wА-Яа-яЁё.]+\s*,\s*"([^"]+)"\s*,\s*"(Видимость|Доступность|ТолькоПросмотр)"')
_UI_PROC = re.compile(r"^\s*(?:Асинх\s+)?(?:Процедура|Функция|Procedure|Function)\s+([\wА-Яа-яЁё]+)", re.I)
# разметка форматированной строки: <b>, <colorstyle 0:uuid>, </> …
_FMT_TAG = re.compile(r"</?(?:b|i|u|s|colorstyle|color|fontstyle|font|link|img)?(?:\s[^<>]*)?>", re.I)
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _plain(s: str) -> str:
    return " ".join(_FMT_TAG.sub("", s).split())


def _title(el: ET.Element | None) -> str:
    """Локализованный заголовок: <Title><v8:item><v8:lang>ru</v8:lang><v8:content>…"""
    t = _child(el, "Title") if el is not None else None
    if t is None:
        return ""
    for item in t:
        lang = cont = ""
        for x in item:
            if _ln(x.tag) == "lang":
                lang = (x.text or "").strip()
            elif _ln(x.tag) == "content":
                cont = (x.text or "").strip()
        if cont and lang in ("ru", ""):
            return _plain(cont)
    return ""


def _events(el: ET.Element) -> str:
    ev = _child(el, "Events")
    if ev is None:
        return ""
    out = []
    for e in ev:
        if _ln(e.tag) != "Event" or not e.get("name"):
            continue
        h = (e.text or "").strip()
        out.append(h if _UUID.match(e.get("name")) else f"{e.get('name')}→{h}")  # uuid вместо имени события — только обработчик
    return ", ".join(x for x in out if x)


def _picture_ref(el: ET.Element) -> str:
    pic = _child(el, "Picture")
    if pic is None:
        return ""
    for x in pic.iter():
        if _ln(x.tag) == "Ref" and (x.text or "").strip():
            return (x.text or "").strip().replace("CommonPicture.", "")
    return ""


def _walk(items: ET.Element, depth: int, lines: list[str]) -> None:
    for el in items:
        tag = _ln(el.tag)
        if tag in _SKIP or tag not in _ELEM_RU:
            continue
        name = el.get("name") or ""
        if is_skipped_name(name):
            continue
        parts = [f"{_ELEM_RU[tag]} {name}"]
        if _title(el):
            parts.append(f"«{_title(el)}»")
        dp = _text(el, "DataPath")
        if dp:
            parts.append(f"← {dp}")
        cmd = _text(el, "CommandName")
        if cmd:
            parts.append(f"[команда {cmd.replace('Form.Command.', '').replace('Form.StandardCommand.', 'стандартная ')}]")
        pic = _picture_ref(el)
        if pic:
            parts.append(f"[картинка {pic}]")
        flags = _flags(el)
        if flags:
            parts.append(f"[{', '.join(flags)}]")
        ev = _events(el)
        if ev:
            parts.append(f"({ev})")
        lines.append("  " * min(depth, _MAX_DEPTH) + " ".join(parts))
        kids = _child(el, "ChildItems")
        if kids is not None:
            _walk(kids, depth + 1, lines)


def _flags(el: ET.Element) -> list[str]:
    """Статические свойства элемента в Form.xml (по умолчанию видим, доступен, редактируется)."""
    out = []
    if _text(el, "Visible") == "false":
        out.append("невидимый")
    uv = _child(el, "UserVisible")
    if uv is not None and _text(uv, "Common") == "false":
        out.append("скрыт у пользователей")
    if _text(el, "Enabled") == "false":
        out.append("недоступный")
    if _text(el, "ReadOnly") == "true":
        out.append("только просмотр")
    return out


def code_ui_control(module_text: str, limit: int = 60) -> list[str]:
    """Где модуль формы меняет Видимость/Доступность/ТолькоПросмотр элементов: «Склад: Доступность (УстановитьДоступность)»."""
    acc: dict[str, dict[str, set[str]]] = {}
    proc = ""
    for ln in (module_text or "").splitlines():
        m = _UI_PROC.match(ln)
        if m:
            proc = m.group(1)
        s = ln.split("//", 1)[0]
        for rx in (_UI_SET, _UI_SET_BSP):
            for name, prop in rx.findall(s):
                if is_skipped_name(name):
                    continue
                acc.setdefault(name, {}).setdefault(prop, set()).add(proc or "модуль")
    out = []
    for name, props in list(acc.items())[:limit]:
        procs = sorted({p for ps in props.values() for p in ps})
        out.append(f"{name}: {', '.join(p for p in _UI_PROPS if p in props)} ({', '.join(procs[:4])})")
    return out


def form_cards(path: Path, label: str, max_chars: int = 1500) -> list[str]:
    """label — «Форма ИтогоПоВалютам объекта Документ.АвансовыйОтчет»."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return []
    head = label + (f" «{_title(root)}»" if _title(root) else "")
    lines: list[str] = []
    ev = _events(root)
    if ev:
        lines.append("События формы: " + ev)
    attrs = []
    for a in (_child(root, "Attributes") or []):
        if _ln(a.tag) != "Attribute" or is_skipped_name(a.get("name") or ""):
            continue
        t = _child(a, "Type")
        ty = _types(ET.Element("P", {}) if t is None else _wrap(t))
        cols = [c.get("name") for c in (_child(a, "Columns") or []) if _ln(c.tag) == "Column" and c.get("name")]
        main = " (основной)" if (_text(a, "MainAttribute") == "true") else ""
        attrs.append(f"{a.get('name')}{main}" + (f" [{ty}]" if ty else "") + (f" колонки: {', '.join(cols[:25])}" if cols else ""))
    if attrs:
        lines.append("Реквизиты формы: " + "; ".join(attrs))
    cmds = []
    for c in (_child(root, "Commands") or []):
        if _ln(c.tag) == "Command" and not is_skipped_name(c.get("name") or ""):
            ttl, act = _title(c), _text(c, "Action")
            cmds.append(f"{c.get('name')}" + (f" «{ttl}»" if ttl else "") + (f" → {act}" if act else ""))
    if cmds:
        lines.append("Команды формы: " + "; ".join(cmds))
    items = _child(root, "ChildItems")
    if items is not None:
        tree: list[str] = []
        _walk(items, 0, tree)
        if tree:
            lines.append("Элементы формы:")
            lines.extend(tree)
    try:
        module = (path.parent / "Form" / "Module.bsl").read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        module = ""
    ctl = code_ui_control(module)
    if ctl:
        lines.append("Видимость/доступность элементов меняется в коде: " + "; ".join(ctl))
    return pack_lines(head, lines, max_chars) if lines else []


def _wrap(type_el: ET.Element) -> ET.Element:
    """_types() ждёт родителя со вложенным <Type>."""
    p = ET.Element("Properties")
    p.append(type_el)
    return p


# ------------------------------------------------------------------ макеты


def _dcs_title(el: ET.Element) -> str:
    return _title(el)


def _dcs_lines(root: ET.Element) -> list[str]:
    lines: list[str] = []
    for ds in root:
        if _ln(ds.tag) != "dataSet":
            continue
        fields = []
        for f in ds:
            if _ln(f.tag) == "field":
                dp = _text(f, "dataPath")
                if dp:
                    ttl = _dcs_title(f)
                    fields.append(dp + (f" «{ttl}»" if ttl and ttl != dp else ""))
        q = _text(ds, "query")
        lines.append(f"Набор данных {_text(ds, 'name')}" + (f" (запрос ≈{len(q)} симв.)" if q else "")
                     + (": " + ", ".join(fields) if fields else ""))
    calc = []
    for f in root:
        if _ln(f.tag) == "calculatedField":
            ttl = _dcs_title(f)
            calc.append(f"{_text(f, 'dataPath')}" + (f" «{ttl}»" if ttl else "") + f" = {_text(f, 'expression')[:100]}")
    if calc:
        lines.append("Вычисляемые поля: " + "; ".join(calc))
    tot = [f"{_text(f, 'dataPath')} = {_text(f, 'expression')[:80]}" for f in root if _ln(f.tag) == "totalField"]
    if tot:
        lines.append("Ресурсы: " + "; ".join(tot))
    prm = []
    for p in root:
        if _ln(p.tag) == "parameter":
            ttl = _dcs_title(p)
            prm.append(_text(p, "name") + (f" «{ttl}»" if ttl else ""))
    if prm:
        lines.append("Параметры: " + ", ".join(prm))
    vs = []
    for v in root:
        if _ln(v.tag) == "settingsVariant":
            nm = ""
            for x in v:
                if _ln(x.tag) == "name":
                    nm = (x.text or "").strip()
            pres = ""
            for x in v.iter():
                if _ln(x.tag) == "content" and (x.text or "").strip():
                    pres = x.text.strip()
                    break
            vs.append(nm + (f" «{pres}»" if pres and pres != nm else ""))
    if vs:
        lines.append("Варианты отчёта: " + ", ".join(vs))
    return lines


def _mxl_rows(root: ET.Element) -> dict[int, tuple[list[str], list[str]]]:
    """Номер строки → (подписи, параметры): rowsItem/index + ячейки row//c с <parameter> и <v8:content>."""
    rows: dict[int, tuple[list[str], list[str]]] = {}
    for it in root.iter():
        if _ln(it.tag) != "rowsItem":
            continue
        try:
            idx = int(_text(it, "index"))
        except ValueError:
            continue
        texts: list[str] = []
        params: list[str] = []
        for el in it.iter():
            t = _ln(el.tag)
            if t == "parameter" and (el.text or "").strip():
                params.append(el.text.strip())
            elif t == "content" and (el.text or "").strip():
                v = _plain(el.text)
                if v:
                    texts.append(v[:80])
        if texts or params:
            rows[idx] = (texts, params)
    return rows


def _mxl_lines(root: ET.Element) -> list[str]:
    areas, params, texts = [], [], []
    seen_t: set[str] = set()
    rows = _mxl_rows(root)
    area_lines: list[str] = []
    for el in root.iter():
        t = _ln(el.tag)
        if t == "namedItem":
            nm = _text(el, "name")
            if nm:
                areas.append(nm)
                ar = _child(el, "area")
                try:
                    r1, r2 = int(_text(ar, "beginRow")), int(_text(ar, "endRow"))
                except (TypeError, ValueError):
                    continue
                if r1 < 0 or r2 < 0:
                    continue  # область-колонки: строк нет
                r1, r2 = min(r1, r2), max(r1, r2)
                at = [x for r in range(r1, min(r2, r1 + 400) + 1) for x in rows.get(r, ([], []))[0]]
                ap = [x for r in range(r1, min(r2, r1 + 400) + 1) for x in rows.get(r, ([], []))[1]]
                at, ap = list(dict.fromkeys(at)), list(dict.fromkeys(ap))
                head = f"Область «{nm}» (строки {r1 + 1}–{r2 + 1})"
                if ap:
                    head += ". Параметры: " + ", ".join(ap)
                if at:
                    head += ". Подписи: " + " | ".join(at[:12])
                area_lines.append(head)
        elif t == "parameter" and (el.text or "").strip():
            params.append(el.text.strip())
        elif t == "content" and (el.text or "").strip():
            v = _plain(el.text)
            if len(v) >= 3 and v not in seen_t:
                seen_t.add(v)
                texts.append(v[:80])
    lines = []
    if areas:
        lines.append("Области макета: " + ", ".join(dict.fromkeys(areas)))
    lines += area_lines  # что лежит в каждой области: параметры и подписи
    if params:
        lines.append("Параметры макета: " + ", ".join(dict.fromkeys(params)))
    if texts and not area_lines:
        lines.append("Текст макета: " + " | ".join(texts[:40]))
    return lines


def template_cards(path: Path, label: str, max_chars: int = 1500) -> list[str]:
    """Печатная форма (MXL) или схема компоновки (СКД); прочие типы макетов пропускаем."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return []
    kind = _ln(root.tag)
    if kind == "DataCompositionSchema":
        lines, tag = _dcs_lines(root), "схема компоновки данных"
    elif kind == "document":
        lines, tag = _mxl_lines(root), "печатная форма (табличный документ)"
    else:
        return []
    return pack_lines(f"{label} — {tag}", lines, max_chars) if lines else []
