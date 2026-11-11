"""
Форма объекта конфигурации «как видит пользователь» — для обоих видов форм:
  управляемая: Forms/<Ф>/Ext/Form.xml (+ Ext/Form/Module.bsl) → managed_form_layout
  обычная:     Forms/<Ф>/Ext/Form.bin (контейнер: form + module)  → ordinary_form_layout
Внешние .epf/.erf — тот же ordinary_form_layout по частям файла (epf_inspect).

load_form_layout → полный единый формат; compact_form_layout → ответ tool в бюджет:
вкладки → элементы строками «Подпись [Тип] → Путь; Событие→Процедура», кнопки «Подпись → Процедура».
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# стандартные реквизиты (поле формы с таким именем = реквизит объекта)
_STD_ATTRS = ("Наименование", "Код", "Родитель", "Владелец", "Номер", "Дата", "Проведен", "ПометкаУдаления",
              "Ссылка", "Период", "Регистратор", "Активность", "НомерСтроки", "Предопределенный")
_SKIP_IN_TABS = {"Разделитель", "КоманднаяПанель"}
_SYN_RE = r"<Synonym>\s*<v8:item>\s*<v8:lang>ru</v8:lang>\s*<v8:content>([^<]*)</v8:content>"


def object_attributes(obj_dir: Path) -> dict[str, dict[str, Any]]:
    """Реквизиты и ТЧ объекта из <Класс>/<Имя>.xml: {Имя: {synonym, type, kind, columns?}}."""
    xml = obj_dir.parent / f"{obj_dir.name}.xml"
    out: dict[str, dict[str, Any]] = {n: {"synonym": "", "type": "", "kind": "standard"} for n in _STD_ATTRS}
    try:
        text = xml.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return out

    def props(block: str) -> tuple[str, str, str]:
        nm = re.search(r"<Name>([^<]+)</Name>", block)
        syn = re.search(_SYN_RE, block)
        types = re.findall(r"<v8:Type>([^<]+)</v8:Type>", block[:3000])
        return (nm.group(1) if nm else "", syn.group(1) if syn else "", ", ".join(dict.fromkeys(types))[:120])

    # табличные части: колонки — их собственные <Attribute> внутри <TabularSection>
    ts_blocks = re.findall(r"<TabularSection uuid=\"[^\"]+\">(.*?)</TabularSection>", text, re.S)
    for blk in ts_blocks:
        head = blk.split("<ChildObjects>", 1)[0]
        nm, syn, _ = props(head)
        cols = [props(a)[0] for a in re.findall(r"<Attribute uuid=\"[^\"]+\">(.*?)</Attribute>", blk, re.S)]
        if nm:
            out[nm] = {"synonym": syn, "type": "ТабличнаяЧасть", "kind": "tabular_section", "columns": cols}
    flat = re.sub(r"<TabularSection uuid=\"[^\"]+\">.*?</TabularSection>", "", text, flags=re.S)
    for blk in re.findall(r"<Attribute uuid=\"[^\"]+\">(.*?)</Attribute>", flat, re.S):
        nm, syn, tp = props(blk)
        if nm:
            out[nm] = {"synonym": syn, "type": tp, "kind": "attribute"}
    return out


def _unpack_bin(path: Path) -> tuple[str, str]:
    from tools.epf_inspect import _walk_blob

    acc: list[tuple[str, str]] = []
    _walk_blob(path.read_bytes(), "F", depth=0, acc=acc, budget=[32 * 1024 * 1024])
    d = dict(acc)
    return d.get("F/form", ""), d.get("F/module", "")


def load_form_layout(obj_dir: Path, form_name: str, *, full_name: str = "") -> dict[str, Any]:
    """Полная раскладка формы из выгрузки (оба вида). Ошибка — {"error", "available_forms"}."""
    from tools.managed_form import managed_form_layout
    from tools.ordinary_form import ordinary_form_layout

    fdir = obj_dir / "Forms" / form_name
    ext = fdir / "Ext"
    attrs = object_attributes(obj_dir)
    xml, binf = ext / "Form.xml", ext / "Form.bin"
    if not xml.is_file() and (fdir / "Form.xml").is_file():
        xml = fdir / "Form.xml"
    if xml.is_file():
        mod_path = ext / "Form" / "Module.bsl"
        module = mod_path.read_text(encoding="utf-8-sig", errors="replace") if mod_path.is_file() else ""
        lay = managed_form_layout(xml.read_text(encoding="utf-8-sig", errors="replace"), module)
        lay["form_path"], lay["module_path"] = str(xml), (str(mod_path) if module else None)
    elif binf.is_file():
        form_text, module = _unpack_bin(binf)
        if not form_text:
            return {"error": f"Не удалось разобрать обычную форму {form_name} (Form.bin)", "form_path": str(binf)}
        lay = ordinary_form_layout(form_text, module, {k: v.get("synonym", "") for k, v in attrs.items()})
        lay["form_path"], lay["module_path"] = str(binf), (str(binf) + "#module" if module else None)
        lay["_module_text"] = module
    else:
        avail = sorted(p.name for p in (obj_dir / "Forms").glob("*") if p.is_dir()) if (obj_dir / "Forms").is_dir() else []
        return {"error": f"Форма «{form_name}» не найдена у {full_name or obj_dir.name}", "available_forms": avail}
    # путь к данным у управляемой формы — Объект.X; синоним реквизита — из метаданных
    for el in lay.get("elements") or []:
        dp = str(el.get("data_path") or "")
        key = dp.split(".")[-1] if dp else ""
        if key in attrs and attrs[key].get("synonym") and not el.get("attribute_synonym"):
            el["attribute_synonym"] = attrs[key]["synonym"]
    lay["object_attributes"] = attrs
    return lay


def _flags_txt(el: dict[str, Any], *, with_props: bool = True) -> str:
    props = (el.get("props") or {}) if with_props else {}
    return ((" (скрыт)" if el.get("visible") is False else "") + (" (недоступен)" if el.get("enabled") is False else "")
            + (" (только просмотр)" if el.get("read_only") else "")
            + (" {" + ", ".join(f"{k}={v}" for k, v in props.items()) + "}" if props else ""))


def _line(el: dict[str, Any], *, events: bool = True) -> str:
    cap = el.get("caption") or el.get("attribute_synonym") or ""
    s = f"{cap} " if cap and cap != el["name"] else ""
    s += f"[{el['type']}] {el['name']}"
    if el.get("data_path"):
        s += f" → {el['data_path']}" + (" (по имени)" if el.get("data_path_by_name") else "")
    if el.get("value_type"):
        s += f" : {el['value_type']}"
    if el.get("columns"):
        s += " колонки: " + ", ".join(str(c) for c in el["columns"])
    s += _flags_txt(el)
    if events and el.get("events"):
        s += "; " + ", ".join(f"{k}→{v}" for k, v in list(el["events"].items()))
    return s


def compact_form_layout(lay: dict[str, Any], *, tab: str = "", max_chars: int = 6200) -> dict[str, Any]:
    """Ответ tool: вкладки с элементами строками, кнопки, события формы — в бюджет символов."""
    import json

    if lay.get("error"):
        return {k: v for k, v in lay.items() if not k.startswith("_") and k != "object_attributes"}
    els = lay.get("elements") or []
    by_name = {e["name"]: e for e in els}
    tabs = []
    on_tab: set[str] = set()
    for pg in lay.get("pages") or []:
        if tab and tab.lower() not in (str(pg.get("caption")) + " " + str(pg.get("name"))).lower():
            continue
        names = [n for n in pg.get("elements") or [] if n in by_name]
        on_tab.update(names)
        tabs.append({"tab": pg.get("caption") or pg.get("name"), "panel": pg.get("panel"),
                     **({"hidden": True} if pg.get("visible") is False else {}),
                     "items": [by_name[n] for n in names if by_name[n]["type"] not in _SKIP_IN_TABS
                               and by_name[n]["type"] not in ("Страница",)]})
    outside = [e for e in els if e["name"] not in on_tab and e["type"] not in _SKIP_IN_TABS
               and e["type"] not in ("Страница", "ГруппаСтраниц", "Панель")] if not tab else []
    btns = []
    for b in lay.get("buttons") or []:
        cap = b.get("caption") or b.get("name")
        btns.append(f"{cap} → {b['handler']}" if b.get("handler") else f"{cap} (стандартная)")
    by_type: dict[str, int] = {}
    for e in els:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1

    def short(e: dict[str, Any]) -> str:
        cap = e.get("caption") or e.get("attribute_synonym") or ""
        return (f"{cap} " if cap and cap != e["name"] else "") + f"[{e['type']}] {e['name']}" + _flags_txt(e, with_props=False)

    def build(events: bool, labels: bool, per_tab: int, nbtn: int, brief: bool = False) -> dict[str, Any]:
        def items(lst):
            lst = [e for e in lst if labels or e["type"] not in ("Надпись",)]
            out_ = [short(e) if brief else _line(e, events=events) for e in lst[:per_tab]]
            if len(lst) > per_tab:
                out_.append(f"…ещё {len(lst) - per_tab}")
            return out_
        res: dict[str, Any] = {
            "kind": lay.get("kind"), "title": lay.get("title"),
            "tabs": [{**{k: v for k, v in t.items() if k != "items"}, "items": items(t["items"])} for t in tabs],
        }
        if outside:
            res["outside_tabs"] = items(outside)
        res["buttons"] = btns[:nbtn] + ([f"…ещё {len(btns) - nbtn}"] if len(btns) > nbtn else [])
        if lay.get("form_props"):
            res["form_props"] = lay["form_props"]
        if lay.get("form_events"):
            fe = lay["form_events"]
            res["form_events"] = fe if isinstance(fe, list) else [f"{k}→{v}" for k, v in fe.items()]
        if lay.get("attributes"):
            res["form_attributes"] = [f"{a.get('name')}: {a.get('type')}" + (" (основной)" if a.get("main") else "")
                                      for a in lay["attributes"]]
            if len(res["form_attributes"]) > 25:
                res["form_attributes_total"] = len(res["form_attributes"])
                res["form_attributes_note"] = "реквизитов формы больше 25; список выше полный (при ужатии для модели см. *_total)"
        if lay.get("handlers_missing_in_module"):
            res["handlers_missing_in_module"] = lay["handlers_missing_in_module"]
        res["element_types"] = by_type
        res["notes"] = lay.get("notes") or []
        return res

    for args in ((True, True, 60, 60, False), (True, False, 40, 40, False), (False, False, 30, 30, False),
                 (False, False, 15, 20, False), (False, False, 12, 15, True), (False, False, 6, 10, True)):
        res = build(*args)
        if len(json.dumps(res, ensure_ascii=False)) <= max_chars:
            break
    if args != (True, True, 60, 60, False):
        res["truncated"] = True
        res["hint_more"] = "Подробно одна вкладка: тот же вызов с tab='<название вкладки>'."
    return res
