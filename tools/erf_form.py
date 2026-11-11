# -*- coding: utf-8 -*-
"""
Своя форма внешнего отчёта (.erf): эталонная форма отчёта (tools/data/erf_tpl/form_report.txt — панель «Сформировать /
Настройки», поле «Результат») + поля реквизитов, таблицы ТЧ и элементы палитры из epf_build.

Элементы кладутся полосой над полем «Результат»: оно сдвигается вниз, форма растёт. Формат правок снят по аналогии с
epf_build._form_with_fields (счётчики id, порядок обхода, число дочерних элементов, таблица форм-реквизитов); отличия
формы отчёта — id 1 «Результат», id 2 командная панель «ДействияФормы», реквизит формы «ОтчетОбъект».
"""
from __future__ import annotations

from typing import Any

from tools import epf_build as B
from tools.epf_build import BuildError, _q

_NL = "\n"
_W0, _H0 = 520, 300          # размер эталонной формы
_RESULT_TOP, _RESULT_H = 33, 259  # поле «Результат» в эталоне: верх и высота
_BAND_TOP = 36                # верх первой полосы элементов (под командной панелью)
_ATTR_NAME = "ОтчетОбъект"
_FIRST_ID = 3                 # id 1 и 2 заняты полем «Результат» и командной панелью
_REG_BASE = [[], [(1, 1)], [(2, 2)], [(1, 3), (2, 3)], [], []]  # реестр формы эталона: (элемент, сторона) по 6 сторонам


def _swap(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise BuildError(f"Шаблон формы отчёта изменился: «{old[:40]}» встречается {text.count(old)} раз")
    return text.replace(old, new)


def report_form(title: str, type_id: str, fields: list[dict[str, Any]], tables: list[dict[str, Any]],
                events: list[str], form_module: str, controls: list[dict[str, Any]], width: int | None,
                height_min: int | None = None, bar_buttons: list[dict[str, Any]] | None = None, bar_menus: list[dict[str, Any]] | None = None) -> str:
    text = _with_bar_buttons(_read_tpl().replace("@@TITLE@@", title).replace("@@TYPE@@", type_id), bar_buttons or [], bar_menus)
    n, m = len(fields), len(tables)
    nl = _NL

    # события формы: тот же хвост «{0},1,4,1,0,0,0,», что у обычных форм
    if events:
        old = f"{nl}}},{nl}{{0}},1,4,1,0,0,0,"
        block = B._event_list([B._event_item(B._FORM_EVENT_CODES[e][0], e, B._FORM_EVENT_CODES[e][1]) for e in events])
        text = _swap(text, old, f"{nl}}},{nl}{block},1,4,1,0,0,0,")

    if not (n or m or controls):
        if width or height_min:
            return _resize(text, int(width or _W0), max(_H0, int(height_min or 0)), _RESULT_TOP)
        return text

    top0 = _BAND_TOP
    _, _, right_max = B._plan_controls(controls or [], top0, int(width or _W0))
    form_w = max(_W0, int(width or 0), right_max + 20)
    tbl_els, bottom = B._table_elements(tables, _FIRST_ID + 2 * n, top0 + n * B._FIELD_STEP + (8 if n else 0), form_w)
    taken = ({"ДействияФормы", "Результат"} | {x["name"] for x in fields + tables}
             | {"Надпись" + x["name"] for x in fields + tables})
    plan, ctl_bottom, _ = B._plan_controls(controls or [], bottom, form_w, taken)
    bottom = max(bottom, ctl_bottom, top0 + n * B._FIELD_STEP)
    k = len(plan)
    panel_top = bottom + 8
    height = max(_H0 - _RESULT_TOP + panel_top, int(height_min or 0))

    last_id = _FIRST_ID - 1 + 2 * n + 2 * m + k
    children = 2 + 2 * n + 2 * m + k
    tbl_ids = [_FIRST_ID + 2 * n + 2 * j + 1 for j in range(m)]
    text = _resize(text, form_w, height, panel_top)
    text = _swap(text, "},2,2},", "},%d,2}," % last_id)
    form_deps = B._bind_controls(plan, _FIRST_ID + 2 * n + 2 * m, form_w, height)
    reg = [list(x) for x in _REG_BASE]
    reg[3] += [(i, 3) for i in [_FIRST_ID + 2 * q for q in range(n)] + tbl_ids]
    for s in range(4):
        reg[s] += form_deps[s]
    text = _swap(text, B._registry_text(_REG_BASE), B._registry_text(reg))
    text = _swap(text, f"{nl}{{2,{nl}{{236a17b3-7f44-46d9-a907-75f9cdc61ab5,1,", f"{nl}{{{children},{nl}{{236a17b3-7f44-46d9-a907-75f9cdc61ab5,1,")

    els = []
    for i, f in enumerate(fields):
        top = top0 + i * B._FIELD_STEP
        fid = _FIRST_ID + 2 * i
        els.append(
            B._tpl("field_input.txt")
            .replace("@@ID@@", str(fid)).replace("@@PATTERN@@", f["pattern"]).replace("@@CAPTION@@", "")
            .replace("@@L@@", str(B._FIELD_LEFT)).replace("@@T@@", str(top)).replace("@@R@@", str(form_w - 20))
            .replace("@@B@@", str(top + B._FIELD_H)).replace("@@AT@@", str(top - _RESULT_TOP)).replace("@@H@@", str(B._FIELD_H))
            .replace("@@AR@@", "-12").replace("@@ORD@@", str(fid - 1)).replace("@@NAME@@", _q(f["name"]))
            .replace(f"}},{nl}{{0}},0,1,0,{nl}{{1,0}},0}},",
                     f"}},{nl}{B._event_list([B._event_item(*e) for e in B.field_events_of(form_module, f['name'])])},0,1,0,{nl}{{1,0}},0}},")
        )
        els.append(
            B._tpl("field_label.txt")
            .replace("@@ID@@", str(fid + 1)).replace("@@TEXT@@", _q(f["caption"]) + ":")
            .replace("@@L@@", str(B._LABEL_LEFT)).replace("@@T@@", str(top)).replace("@@R@@", str(B._LABEL_RIGHT))
            .replace("@@B@@", str(top + B._LABEL_H)).replace("@@AT@@", str(top - _RESULT_TOP)).replace("@@H@@", str(B._LABEL_H))
            .replace("@@W@@", str(B._LABEL_RIGHT - B._LABEL_LEFT)).replace("@@O1@@", str(fid)).replace("@@O2@@", str(fid - 1))
            .replace("@@NAME@@", "Надпись" + _q(f["name"]))
        )
    anchor = f'{{14,"ДействияФормы",0,0,0,0}},{nl}{{0}}{nl}}}'
    ctl = B._control_elements(plan, _FIRST_ID + 2 * n + 2 * m, form_module)
    text = _swap(text, anchor, anchor + f",{nl}" + f",{nl}".join(els + tbl_els + ctl))
    if n or m:
        pairs = ([(_FIRST_ID + 2 * i, f["attr_id"]) for i, f in enumerate(fields)] + [(tid, t["id"]) for tid, t in zip(tbl_ids, tables)])
        binds = f",{nl}".join(f"{{{eid},{nl}{{2,{nl}{{1}},{nl}{{0,{pid}}}{nl}}}{nl}}}" for eid, pid in pairs)
        text = _swap(text, f"{{{nl}{{1}},2,{nl}{{1,", f"{{{nl}{{1}},{2 + n + m},{nl}{{1,")
        tail = f'"{_ATTR_NAME}",{nl}{{"Pattern",{nl}{{"#",{type_id}}}{nl}}}{nl}}}{nl}}},{nl}{{0}}'
        text = _swap(text, tail, tail[: -len("{0}")] + f"{{{n + m},{nl}{binds}{nl}}}")
    return text


def _resize(text: str, w: int, h: int, panel_top: int) -> str:
    """Размер окна, верх панели/«Результата» и ширина верхней панели (в эталоне 520×300, панель с отступом 8)."""
    text = _swap(text, "{2,33,0,1,2,0,0,0,0}", "{2,%d,0,1,2,0,0,0,0}" % panel_top)
    text = _swap(text, "{2,292,0,1,4,0,0,8,0}", "{2,%d,0,1,4,0,0,8,0}" % (h - 8))
    text = _swap(text, "{8,8,33,512,292,1,", "{8,8,%d,%d,%d,1," % (panel_top, w - 8, h - 8))
    text = _swap(text, "},520,300,1,0,1,4,4,2,520,300,96}", "},%d,%d,1,0,1,4,4,2,%d,%d,96}" % (w, h, w, h))
    if w != _W0:
        text = _swap(text, "{2,512,1,1,3,0,0,8,0}", "{2,%d,1,1,3,0,0,8,0}" % (w - 8))
        text = _swap(text, "{8,0,0,520,25,1,", "{8,0,0,%d,25,1," % w)
    return text


def _read_tpl() -> str:
    from pathlib import Path
    return (Path(__file__).parent / "data" / "erf_tpl" / "form_report.txt").read_text("utf-8")


_ACTIONS = "a7653775-9bdf-4a52-a2d1-dbdf02d46eb9"   # контейнер действий командной панели «ДействияФормы»
_BAR = "b78f2e80-ec68-11d4-9dcf-0050bae2bc79"
_SUBMENU = "c93a51ed-b2d1-47f1-948d-38373f8710af"    # тип действия «подменю»
_HANDLER = "e1692cc2-605b-4535-84dd-28440238746c"    # тип действия «обработчик в модуле формы»


def _handler_action(gid: str, proc: str, title: str) -> str:
    nl = _NL
    ru = f'{{1,1,{nl}{{"ru","{_q(title)}"}}{nl}}}'
    return (f'{{7,{gid},1,{_HANDLER},{nl}{{3,"{_q(proc)}",{nl}{{1,"{_q(proc)}",{nl}{ru},{nl}{ru},{nl}{ru},'
            f'{nl}{{4,0,{nl}{{0}},"",-1,-1,1,0,""}},{nl}{{0,0,0}}{nl}}}{nl}}},0,0}}')


def _button(gid: str, name: str, title: str, idx: int, menu: bool = False) -> str:
    nl = _NL
    flags = "1,0,1,0,1,0,0" if menu else "0,0,1,0,1,0,0"
    own = "1" if menu else "0"  # у подменю в типовых формах после подписи стоит 1: платформа берёт подпись кнопки, а не стандартную («Служебные окна»)
    return f'{gid},{nl}{{8,"{_q(name)}",0,1,{nl}{{1,1,{nl}{{"ru","{_q(title)}"}}{nl}}},{own},{_ACTIONS},{idx},1e2,{flags}}}'


def _with_bar_buttons(text: str, buttons: list[dict[str, Any]], menus: list[dict[str, Any]] | None = None) -> str:
    """
    Свои кнопки и подменю на верхней командной панели отчёта. Действие-обработчик — запись в списке действий панели
    (`{5,a7653775…,последний индекс,1,число действий,`), кнопка — в конце верхней группы (`{5,b78f…,4,0,12,`); подменю — действие
    типа c93a51ed со ссылкой на контейнер (id 56…), своя группа кнопок после группы «Действия» и кнопка-«подменю» в верхней группе.
    """
    menus = menus or []
    if not buttons and not menus:
        return text
    nl = _NL
    seen: set[str] = set()

    def uniq(name: str, what: str) -> str:
        n = B._ident(name, what)
        if n.lower() in seen:
            raise BuildError(f"{what} «{n}» указано дважды")
        seen.add(n.lower())
        return n

    acts, top, start, groups, idx, cid = [], [], [], [], 31, 55
    for b in buttons:
        name = uniq(b.get("name", ""), "Кнопка панели")
        gid = B._guid()
        idx += 1
        acts.append(_handler_action(gid, b.get("handler") or f"{name}Нажатие", b.get("title") or name))
        top.append(_button(gid, name, b.get("title") or name, idx))
    for m in menus:
        mname = uniq(m.get("name", ""), "Подменю панели")
        if not m.get("items"):
            raise BuildError(f"Подменю «{mname}»: нужен хотя бы один пункт (items)")
        cid += 1
        mg = B._guid()
        acts.append(f'{{7,{mg},1,{_SUBMENU},{nl}{{4,{_BAR},{cid},{nl}{{1,99,{_BAR},4,1}},0,0}},0,0}}')
        idx += 1
        mbtn = _button(mg, mname, m.get("title") or mname, idx, menu=True)
        if m.get("first", True):  # как «Вариант отчета» в типовых отчётах: первым слева, за ним разделитель
            sg = B._guid()
            idx += 1
            acts.append(f"{{7,{sg},1,abde0c9a-18a6-4e0c-bbaa-af26b911b3e6,{nl}{{1,9d0a2e40-b978-11d4-84b6-008048da06df,0}},0,0}}")
            start.append(mbtn)
            start.append(f'{sg},{nl}{{8,"Разделитель{mname}",0,1,{nl}{{1,0}},0,{_ACTIONS},{idx},1e2,2,0,1,0,1,0,0}}')
        else:
            top.append(mbtn)
        items = []
        for it in m["items"]:
            iname = uniq(it.get("name", ""), "Пункт подменю")
            gid = B._guid()
            idx += 1
            acts.append(_handler_action(gid, it.get("handler") or f"{iname}Нажатие", it.get("title") or iname))
            items.append(_button(gid, iname, it.get("title") or iname, idx) + ",")
        groups.append(f'{{5,{_BAR},{cid},0,{len(items)},' + nl.join(items) + f'{nl}{{-1,0,{nl}{{0}}{nl}}}{nl}}}')
    k, total_top = len(acts), len(top) + len(start)
    text = _swap(text, f"{{5,{_ACTIONS},31,1,22,", f"{{5,{_ACTIONS},{idx},1,{22 + k},")
    old = f"{nl}{{0,0,{nl}{{1,{_ACTIONS},10,0}}"
    text = _swap(text, old, f"{nl}" + "".join(x + f",{nl}" for x in top) + f"{{0,0,{nl}{{1,{_ACTIONS},10,0}}")
    head = f"{nl}{{5,{_BAR},4,0,12,"
    first_item = "dee77fc5-609c-4283-b68d-77448df3efb4,"
    text = _swap(text, head + first_item, f"{nl}{{5,{_BAR},4,0,{12 + total_top}," + "".join(x + f",{nl}" for x in start) + first_item)
    old = f",0,1}},0,0}},2,{nl}{{5,{_BAR},4,0,"
    text = _swap(text, old, f",0,1}},0,0}},{nl}" + f",{nl}".join(acts) + f",{2 + len(groups)},{nl}{{5,{_BAR},4,0,")
    if groups:
        old = f"{{-1,0,{nl}{{0}}{nl}}}{nl}}}{nl}}},{_BAR},4,b7acb77b"
        text = _swap(text, old, f"{{-1,0,{nl}{{0}}{nl}}}{nl}}},{nl}" + f",{nl}".join(groups) + f"{nl}}},{_BAR},4,b7acb77b")
    return text
