# -*- coding: utf-8 -*-
"""
Макет табличного документа (MOXCEL) для внешних обработок/отчётов: описание → бинарный блок макета без платформы.

Формат расшифрован опытами по платформе 8.3.24 (XML макета → Конфигуратор → бинарный MOXCEL, scripts/mxl_*):
  {8,1,12,{язык},{формат по умолчанию},{линии},0,{0,0}×6,1,2,
   <число строк>, строки…, {колонки}, <высота>,0×8, {объединения},{0},{0}, {именованные области}, "",
   {печать}, …, <форматы>, <шрифты>, <числовые форматы>,0,0, <палитра>, …}
Строка: индекс, формат строки, число ячеек, затем пары (колонка, ячейка); ячейка {16,формат,{1,1,{язык,текст}},0}
(для параметра язык пустой, для текста «ru»; с расшифровкой — {24,формат,"Расшифровка",{…},0}).
Формат: {маска, значения по возрастанию битов}: 0x1 шрифт, 0x2/0x4/0x8/0x10 рамки лево/верх/право/низ (индекс линии),
0x20 цвет рамки, 0x40 высота, 0x80 ширина, 0x100 гориз. выравнивание, 0x200 вертик., 0x400 цвет текста, 0x800 цвет фона,
0x4000 размещение текста, 0x8000 тип заполнения. Цвета — индекс палитры ({3,0,{BGR}} после двух системных).

Описание макета (spec):
  name, synonym
  columns: [ширина | {width}]            — единицы платформы: 1 ед ≈ 0,87 px, стандартная колонка 72 ≈ 63 px;
                                           высота строки 1 ед ≈ 0,38 px, строка 10 pt ≈ 40 ед — без height строка растёт по тексту
  styles:  {имя: {свойства ячейки}}      — общие стили, на них ссылается cell.style
  rows:    [{height?, cells: [ячейка…]}] — ячейка: {text | param | detail, col?, colspan?, rowspan?, style?, + свойства}
  areas:   [{name, rows:[с,по]} | {name, cols:[с,по]} | {name, rect:[строка1,кол1,строка2,кол2]}]  (номера с 0, включительно)
Свойства ячейки: font, size, bold, italic, underline, strike, align (left|center|right), valign (top|center|bottom),
wrap (bool), border ("all" | "left,top" | {left:…}), line (solid|dotted|double|dashed|thick_dashed|large_dashed|none),
border_width (1-3), fill (#RRGGBB), color (#RRGGBB), border_color (#RRGGBB).
Текст со «[Параметр]» — заполнение «Шаблон», param — «Параметр», иначе «Текст».
"""

from __future__ import annotations

import re
import struct
from typing import Any

HEADER = b"MOXCEL\x00" + struct.pack("<HHH", 8, 1, 12)
_GUID0 = "00000000-0000-0000-0000-000000000000"
_LINE_GUID = "f527dc88-1d39-40b3-bcbb-d98b690ead68"

_LINES = {"none": 0, "solid": 1, "dotted": 2, "double": 3, "dashed": 4, "thin_dashed": 4, "thick_dashed": 5, "large_dashed": 6}
_HALIGN = {"left": 0, "center": 6, "right": 2}
_VALIGN = {"top": 0, "center": 24, "bottom": 8}
_SIDES = {"left": 0x2, "top": 0x4, "right": 0x8, "bottom": 0x10}
_SIDE_ALIASES = {"l": "left", "t": "top", "r": "right", "b": "bottom", "лево": "left", "верх": "top", "право": "right", "низ": "bottom"}
_CELL_PROPS = {"font", "size", "bold", "italic", "underline", "strike", "align", "valign", "wrap", "border", "line",
               "border_width", "fill", "color", "border_color", "height", "width", "numfmt"}
_FALSE_FILL = 0


class MxlError(ValueError):
    """Ошибка в описании макета (сообщение читает пользователь/агент)."""


def _q(s: str) -> str:
    return (s or "").replace('"', '""')


def _color(value: str, what: str) -> int:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(value or "").strip())
    if not m:
        raise MxlError(f"{what}: цвет «{value}» — нужен вид #RRGGBB")
    v = int(m.group(1), 16)
    return ((v >> 16) & 255) | (v & 0xFF00) | ((v & 255) << 16)  # BGR


def _sides(spec: Any) -> dict[str, str | None]:
    """border → {сторона: стиль линии или None (по умолчанию)}."""
    if not spec:
        return {}
    if isinstance(spec, dict):
        out = {}
        for k, v in spec.items():
            side = _SIDE_ALIASES.get(str(k).lower(), str(k).lower())
            if side not in _SIDES:
                raise MxlError(f"Рамка: сторона «{k}» неизвестна (left, top, right, bottom)")
            out[side] = str(v).lower() if v else None
        return out
    s = str(spec).lower().replace(" ", "")
    if s in ("all", "box", "все", "вся"):
        return {k: None for k in _SIDES}
    parts = [p for p in re.split(r"[,;|]", s) if p] if re.search(r"[,;|]", s) else (
        [s] if s in _SIDES or s in _SIDE_ALIASES else list(s))
    out = {}
    for p in parts:
        side = _SIDE_ALIASES.get(p, p)
        if side not in _SIDES:
            raise MxlError(f"Рамка «{spec}»: ожидается all или стороны left,top,right,bottom (можно буквами lrtb)")
        out[side] = None
    return out


class _Tables:
    """Общие таблицы документа: линии, шрифты, палитра цветов, форматы (с дедупликацией)."""

    def __init__(self) -> None:
        self.lines: list[tuple[int, int, int]] = []
        self.fonts: list[tuple] = []
        self.palette: list[int] = []
        self.formats: list[tuple[int, tuple]] = []
        self.numfmts: list[str] = []

    @staticmethod
    def _idx(lst: list, item: Any) -> int:
        if item not in lst:
            lst.append(item)
        return lst.index(item)

    def format_index(self, props: dict[str, Any]) -> int:
        """Свойства (после разбора) → индекс формата, с 1; 0 — без формата."""
        vals: dict[int, int] = {}
        if any(k in props for k in ("font", "size", "bold", "italic", "underline", "strike")):
            face = props.get("font") or "Arial"
            size = float(props.get("size") or 10)
            vals[0x1] = self._idx(self.fonts, (str(face), int(round(size * 10)), 700 if props.get("bold") else 400,
                                              int(bool(props.get("italic"))), int(bool(props.get("underline"))),
                                              int(bool(props.get("strike")))))
        border = _sides(props.get("border"))
        width = int(props.get("border_width") or 1)
        for side, kind in border.items():
            style = _LINES.get(kind or str(props.get("line") or "solid").lower())
            if style is None:
                raise MxlError(f"Стиль линии «{kind or props.get('line')}» неизвестен: {', '.join(_LINES)}")
            vals[_SIDES[side]] = self._idx(self.lines, (style, 0 if style == 0 else width, 0))
        if props.get("border_color"):
            vals[0x20] = 2 + self._idx(self.palette, _color(props["border_color"], "border_color"))
        if props.get("height"):
            vals[0x40] = int(props["height"])
        if props.get("width"):
            vals[0x80] = int(props["width"])
        if props.get("align"):
            if str(props["align"]).lower() not in _HALIGN:
                raise MxlError(f"align «{props['align']}»: left, center или right")
            vals[0x100] = _HALIGN[str(props["align"]).lower()]
        if props.get("valign"):
            if str(props["valign"]).lower() not in _VALIGN:
                raise MxlError(f"valign «{props['valign']}»: top, center или bottom")
            vals[0x200] = _VALIGN[str(props["valign"]).lower()]
        if props.get("color"):
            vals[0x400] = 2 + self._idx(self.palette, _color(props["color"], "color"))
        if props.get("fill"):
            vals[0x800] = 2 + self._idx(self.palette, _color(props["fill"], "fill"))
        if "wrap" in props and props["wrap"] is not None:
            vals[0x4000] = 3 if props["wrap"] else 1
        if props.get("fill_type") is not None:
            vals[0x8000] = props["fill_type"]
        if props.get("numfmt"):  # числовой формат строкой платформы («ЧДЦ=2», «ЧЦ=15; ЧДЦ=2; ЧГ=3,0»), общий список с 0
            vals[0x1000000] = self._idx(self.numfmts, str(props["numfmt"]).strip())
        if not vals:
            return 0
        key = (sum(vals), tuple(vals[b] for b in sorted(vals)))
        if key not in self.formats:
            self.formats.append(key)
        return self.formats.index(key) + 1

    # --- запись
    def lines_block(self) -> str:
        if not self.lines:
            return "{0}"
        items = ",".join(f"1,{{4,0,{{0}},{st},{w},0,{_LINE_GUID},{gap}}},0" for st, w, gap in self.lines)
        return f"{{{len(self.lines)},{items}}}"

    def formats_block(self) -> str:
        items = "".join(f",{{{m}" + "".join(f",{v}" for v in vs) + "}" for m, vs in self.formats)
        return f"{len(self.formats)}{items}"

    def numfmts_block(self) -> str:
        items = "".join(f',{{1,1,{{"ru","{_q(f)}"}}}}' for f in self.numfmts)
        return f"{len(self.numfmts)}{items}"

    def fonts_block(self) -> str:
        items = "".join(
            f',{{7,0,63,{h},0,0,0,{w},{i},{u},{s},0,0,0,0,0,"{_q(face)}",1,100}}' for face, h, w, i, u, s in self.fonts)
        return f"{len(self.fonts)}{items}"

    def palette_block(self) -> str:
        items = "".join(f",{{3,0,{{{c}}}}}" for c in self.palette)
        return f"{2 + len(self.palette)},{{3,3,{{-1}}}},{{3,3,{{-3}}}}{items}"


def _cell_props(cell: dict[str, Any], styles: dict[str, dict[str, Any]], where: str) -> dict[str, Any]:
    props: dict[str, Any] = {}
    name = cell.get("style")
    if name:
        if name not in styles:
            raise MxlError(f"{where}: стиль «{name}» не описан в styles ({', '.join(styles) or 'пусто'})")
        props.update(styles[name])
    props.update({k: v for k, v in cell.items() if k in _CELL_PROPS})
    return props


def _text_cell(fmt: int, text: str, lang: str, detail: str = "") -> str:
    mask, det = (24, f'"{_q(detail)}",') if detail else (16, "")
    return f'{{{mask},{fmt},{det}{{1,1,{{"{lang}","{_q(text)}"}}}},0}}'


_PIC_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF8", b"BM")
_PIC_SIZE = {"real": 0, "stretch": 1, "proportional": 2, "proportionally": 2, "realsize": 0}


def _picture_bytes(d: dict[str, Any], where: str) -> bytes:
    """Картинка рисунка: base64 или путь к файлу PNG/JPEG/GIF/BMP."""
    import base64
    from pathlib import Path
    raw = None
    if d.get("base64"):
        try:
            raw = base64.b64decode(re.sub(r"\s+", "", str(d["base64"])), validate=True)
        except Exception as e:  # noqa: BLE001
            raise MxlError(f"{where}: base64 картинки не разобран ({e})") from e
    elif d.get("file"):
        path = Path(str(d["file"]))
        if not path.is_file():
            raise MxlError(f"{where}: файл картинки «{d['file']}» не найден")
        raw = path.read_bytes()
    else:
        raise MxlError(f"{where}: нужна картинка — file (путь к PNG/JPEG/GIF/BMP) или base64")
    if not any(raw.startswith(m) for m in _PIC_MAGIC):
        raise MxlError(f"{where}: картинка не похожа на PNG/JPEG/GIF/BMP")
    return raw


def _drawings(spec: dict[str, Any]) -> tuple[str, str]:
    """(число рисунков, id и записи рисунков для хвоста строк; блок картинок): картинки без дублей, нумерация с 1."""
    import base64
    items = spec.get("drawings") or []
    if not items:
        return "0,0,", "0,"
    pics: list[bytes] = []
    recs = []
    for i, d in enumerate(items, 1):
        where = f"Рисунок {i}"
        raw = _picture_bytes(d, where)
        if raw not in pics:
            pics.append(raw)
        r0, c0 = int(d.get("row", 0)), int(d.get("col", 0))
        rows, cols = int(d.get("rows") or 1), int(d.get("cols") or 1)
        if rows < 1 or cols < 1:
            raise MxlError(f"{where}: rows и cols — не меньше 1")
        off = list(d.get("offset") or [0, 0, 0, 0])  # смещения: начало (кол, строка), конец (кол, строка)
        if len(off) != 4:
            raise MxlError(f"{where}: offset — четыре числа [нач.кол, нач.строка, кон.кол, кон.строка]")
        size = _PIC_SIZE.get(str(d.get("size") or "proportional").lower().replace("_", ""))
        if size is None:
            raise MxlError(f"{where}: size — real, stretch или proportional")
        recs.append(f"{{{{0,0}},5,{c0},{r0},{off[0]},{off[1]},{c0 + cols},{r0 + rows},{off[2]},{off[3]},{i},{pics.index(raw) + 1},"
                    f"{size},{1 if d.get('auto_size') else 0}}},")
    blocks = []
    for raw in pics:
        b = base64.b64encode(raw).decode()
        b = "\n".join(b[k:k + 64] for k in range(0, len(b), 64))
        blocks.append(f'{{4,3,{{0}},"",-1,-1,1,{{{{#base64:{b}}}}},0,""}}')
    return f"{len(items)},{len(items)}," + "".join(recs), f"{len(pics)}," + "".join(x + "," for x in blocks)


def build_moxcel_text(spec: dict[str, Any]) -> str:
    """Описание макета → скобочный текст MOXCEL (без заголовка и BOM)."""
    tabs = _Tables()
    styles = {str(k): dict(v or {}) for k, v in (spec.get("styles") or {}).items()}
    columns = spec.get("columns") or []
    col_fmt: dict[int, int] = {}
    for c, w in enumerate(columns):
        width = w.get("width") if isinstance(w, dict) else w
        if width:
            col_fmt[c] = tabs.format_index({"width": int(width)})
    cells_of: dict[int, dict[int, str]] = {}  # строка → колонка → запись ячейки
    row_fmt: dict[int, int] = {}
    merges: list[tuple[int, int, int, int]] = []
    taken: dict[tuple[int, int], str] = {}
    max_col, height = len(columns), 0
    for r, row in enumerate(spec.get("rows") or []):
        if isinstance(row, list):
            row = {"cells": row}
        if row.get("height"):
            row_fmt[r] = tabs.format_index({"height": int(row["height"])})
        cells_of.setdefault(r, {})
        height = r + 1
        col = 0
        for k, cell in enumerate(row.get("cells") or []):
            if isinstance(cell, str):
                cell = {"text": cell}
            at = f"Строка {r + 1}, ячейка {k + 1}"
            c = int(cell["col"]) if cell.get("col") is not None else col
            span_c, span_r = max(1, int(cell.get("colspan") or 1)), max(1, int(cell.get("rowspan") or 1))
            text, param, detail = cell.get("text"), cell.get("param"), cell.get("detail") or ""
            if param and text not in (None, ""):
                raise MxlError(f"{at}: задай либо text, либо param")
            if detail and not param:
                raise MxlError(f"{at}: detail (расшифровка) имеет смысл только вместе с param")
            props = _cell_props(cell, styles, at)
            if param:
                props["fill_type"] = 1
            elif text and re.search(r"\[[^\]\[]+\]", str(text)):
                props["fill_type"] = 2
            fmt = tabs.format_index(props)
            for dr in range(span_r):
                for dc in range(span_c):
                    key = (r + dr, c + dc)
                    if key in taken:
                        raise MxlError(f"{at}: клетка (строка {key[0] + 1}, колонка {key[1] + 1}) уже занята — {taken[key]}")
                    taken[key] = at
                    if (dr, dc) != (0, 0):  # покрытые объединением клетки несут тот же формат (рамки)
                        cells_of.setdefault(r + dr, {})[c + dc] = f"{{0,{fmt}}}"
            if param:
                cells_of[r][c] = _text_cell(fmt, str(param), "", str(detail))
            elif text not in (None, ""):
                cells_of[r][c] = _text_cell(fmt, str(text), "ru")
            else:
                cells_of[r][c] = f"{{0,{fmt}}}"
            if span_c > 1 or span_r > 1:
                merges.append((c, r, c + span_c - 1, r + span_r - 1))
            col = c + span_c
            max_col = max(max_col, col)
    height = max([height] + [r + 1 for r in cells_of])
    rows = [r for r in sorted(cells_of | {r: {} for r in row_fmt}) if cells_of.get(r) or row_fmt.get(r)]
    rows_txt = "".join(f"{r},{row_fmt.get(r, 0)},{len(cells_of.get(r, {}))}"
                       + "".join(f",{c},{b}" for c, b in sorted(cells_of.get(r, {}).items())) + "," for r in rows)
    cols_txt = f"{{{max_col or 1},0,{_GUID0},{len(col_fmt)}" + "".join(f",{c},{f}" for c, f in sorted(col_fmt.items())) + "}"
    merge_txt = "{0}" if not merges else f"{{{len(merges)}," + ",".join(f"{{{c1},{r1},{c2},{r2},0}}" for c1, r1, c2, r2 in merges) + "}"
    head = f'{{8,1,12,{{"ru","ru",1,1,"ru","Русский","Русский",0}},{{0}},{tabs.lines_block()},0,' + "{0,0}," * 6
    drw_txt, pic_txt = _drawings(spec)
    return (head + f"1,2,{len(rows)}," + rows_txt + cols_txt + f",{height},0,0,{drw_txt}0,0,0,0,{merge_txt},{{0}},{{0}},"
            f'{_named(spec.get("areas") or [])},"",'
            '{{0,6,6,{"N",1000},7,{"N",1000},8,{"N",1000},9,{"N",1000},10,{"N",1000},11,{"N",1000}}},'
            f"{{0,-1,-1,-1,-1,{_GUID0}}},0,0,0,0,0,0,0,1,0,1,"
            f'{tabs.formats_block()},{tabs.fonts_block()},{tabs.numfmts_block()},0,0,{tabs.palette_block()},{pic_txt}0,0,"",0,'
            '{3,0,0,100,1,1,0,1,1,0,0,0,0,0,0,0,0,0,0,0,0,"",0,0,0,0,0,0,0},{0},0,0,0,1,0,0,0}')


def _named(areas: list[dict[str, Any]]) -> str:
    if not areas:
        return "{0}"
    seen, items = set(), []
    for a in areas:
        name = str(a.get("name") or "").strip()
        if not re.fullmatch(r"[A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*", name):
            raise MxlError(f"Область «{name}»: имя — буквы/цифры/подчёркивание, не с цифры")
        if name.lower() in seen:
            raise MxlError(f"Область «{name}» указана дважды")
        seen.add(name.lower())
        if a.get("rows"):
            r1, r2 = (int(x) for x in a["rows"])
            geo = f"1,-1,{r1},-1,{r2}"
        elif a.get("cols"):
            c1, c2 = (int(x) for x in a["cols"])
            geo = f"2,{c1},-1,{c2},-1"
        elif a.get("rect"):
            r1, c1, r2, c2 = (int(x) for x in a["rect"])
            geo = f"3,{c1},{r1},{c2},{r2}"
        else:
            raise MxlError(f"Область «{name}»: нужен rows [с,по], cols [с,по] или rect [строка1,кол1,строка2,кол2]")
        items.append((name, f'"{name}",{{1,{{{geo},{_GUID0}}},0}}'))
    items.sort(key=lambda x: x[0].casefold())
    return f"{{{len(items)}," + ",".join(t for _, t in items) + "}"


def build_moxcel(spec: dict[str, Any]) -> bytes:
    """Описание макета → готовый блок MOXCEL (то, что лежит в <id>.0 макета)."""
    return HEADER + "﻿".encode("utf-8") + build_moxcel_text(spec).replace("\n", "\r\n").encode("utf-8")
