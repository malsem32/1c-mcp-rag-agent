"""
Обычная (неуправляемая) форма 1С: Form.bin выгрузки или …/form внутри .epf/.erf.

ordinary_form_layout(form_text, module_text, object_attrs) → тот же формат, что managed_form_layout:
вкладки (страницы панелей) с элементами, все элементы (тип, подпись, путь к данным, события, страница),
кнопки командных панелей (подпись → процедура модуля), события формы.

Формат внутренний («скобочный»), разбирается деревом (brace.parse). Что известно точно:
  элемент  = {<GUID типа>, <id>, {свойства}, …, {8, лево, верх, право, низ, …раскладка…}, {14,"Имя",…}, {0}}
  страница = {5, {1,1,{"ru","Заголовок"}}, …, -1, <видима 0/1>, 1, "Имя", …} внутри свойств Панели
  действие = {7, <uuid>, n, <тип>, {3,"Процедура", {1,"Процедура", {1,1,{"ru","Подпись"}}}}}
  кнопка   = <uuid действия>, {8,"Имя", n, n, {1,1,{"ru","Подпись"}}, …}
  Видимость = 5-е поле раскладки {8, л, в, п, н, <видим 0/1>, …} — у всех типов элементов;
  ПолеВвода / Переключатель: свойства {n, …, {{<общие>}}, …}, общие[0] = {10|16, <Доступность 0/1>, {цвета…}, …};
  у ПолеВвода ещё ТолькоПросмотр = общие[13] (у Переключателя такого свойства нет);
  ТабличноеПоле: свойства[2] = {{10|16, <Доступность>, …}, {<версия>, <маска>}, …}, бит 1024 маски сброшен = ТолькоПросмотр
  (сверено по эталонной .epf с выставленными флагами; у других типов блок свойств свой — не читаем).
Номер страницы элемента — 5-й с конца в хвосте раскладки {8,…}; родительская панель — наименьшая
панель, чей прямоугольник содержит элемент (у формата нет явной ссылки на родителя).
Путь к данным строкой не хранится: берём имя элемента, если это реквизит объекта (data_path_by_name).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from tools import brace

# карта прочих свойств элементов/формы: где лежит свойство, тип, умолчание — строится scripts/form_bin_map.py по эталону
MAP_PATH = Path(__file__).parent / "data" / "ordinary_form_props.json"
FORM_KEY = "_Форма"
_OWN_FLAGS = {"Видимость", "Доступность", "ТолькоПросмотр", "Заголовок"}  # читаются отдельно (visible / enabled / read_only / caption)

# GUID типа элемента обычной формы → тип (по статистике имён по умолчанию «ТипN» во всей выгрузке)
ELEMENT_TYPES = {
    "381ed624-9217-4e63-85db-c4c3cb87daae": "ПолеВвода",
    "0fc7e20d-f241-460c-bdf4-5ad88e5474a5": "Надпись",
    "e69bf21d-97b2-4f37-86db-675aea9ec2cb": "КоманднаяПанель",
    "ea83fe3a-ac3c-4cce-8045-3dddf35b28b1": "ТабличноеПоле",
    "36e52348-5d60-4770-8e89-a16ed50a2006": "Разделитель",
    "90db814a-c75f-4b54-bc96-df62e554d67d": "РамкаГруппы",
    "35af3d93-d7c7-4a2e-a8eb-bac87a1a3f26": "Флажок",
    "09ccdc77-ea1a-4a6d-ab1c-3435eada2433": "Панель",
    "6ff79819-710e-4145-97cd-1618da79e3e2": "Кнопка",
    "64483e7f-3833-48e2-8c75-2c31aac49f6e": "ПолеВыбора",
    "236a17b3-7f44-46d9-a907-75f9cdc61ab5": "ПолеТабличногоДокумента",
    "14c4a229-bfc3-42fe-9ce1-2da049fd0109": "ПолеТекстовогоДокумента",
    "782e569a-79a7-4a4f-a936-b48d013936ec": "Переключатель",
    "42248403-7748-49da-b782-e4438fd7bff3": "ПолеГрафическойСхемы",
    "19f8b798-314e-4b4e-8121-905b2a7a03f5": "ПолеСписка",
    "151ef23e-6bb2-4681-83d0-35bc2217230c": "ПолеКартинки",
    "d92a805c-98ae-4750-9158-d9ce7cec2f20": "ПолеHTMLДокумента",
    "b1db1f86-abbb-4cf0-8852-fe6ae21650c2": "ИндикаторПрогресса",
    "a8b97779-1a4b-4059-b09c-807f86d2a461": "Диаграмма",
    "e3c063d8-ef92-41be-9c89-b70290b5368b": "ПолеКалендаря",
    # сняты с эталона владельца (scripts/epf_ctl_templates.py)
    "6c06cd5d-8481-4b6f-a90a-7a97a8bb8bef": "ПолосаРегулирования",
    "ad37194e-555e-4305-b718-5dca84baf145": "ПолеГеографическойСхемы",
    "e5fdc112-5c84-4a16-9728-72b85692b6e2": "ДиаграммаГанта",
    "a26da99e-184a-4823-b0d6-62816d38dc4e": "СводнаяДиаграмма",
    "984981b1-622d-4ebc-94f7-885f0cdfb59a": "Дендрограмма",
}
# события обычной формы привязываются по имени процедуры модуля (в файле формы их нет)
FORM_EVENTS = (
    "ПередОткрытием", "ПриОткрытии", "ПриПовторномОткрытии", "ПередЗакрытием", "ПриЗакрытии", "ОбработкаВыбора",
    "ОбработкаАктивизацииОбъекта", "ПередЗаписью", "ПриЗаписи", "ПослеЗаписи", "ОбработкаЗаписиНовогоОбъекта",
    "ОбработкаОповещения", "ОбработкаПроверкиЗаполнения", "ОбработкаВнешнегоСобытия", "ОбновлениеОтображения",
    "ПриСменеСтраницы", "ОбработкаПолученияФормы", "ПриИзмененииДанных", "ПриВыводеСтроки",
)
# тип по имени элемента «ТипN», если GUID не в словаре (редкие типы: индикатор, картинка, календарь…)
_TYPE_BY_NAME = (
    "ИндикаторПрогресса", "ИндикаторВыполнения", "ПолеКартинки", "ПолеКалендаря", "Календарь", "ПолеHTMLДокумента",
    "ПолосаРегулирования", "Диаграмма", "ДиаграммаГанта", "Дендрограмма", "СводнаяДиаграмма", "ПолеВвода",
    "Надпись", "Кнопка", "Флажок", "Переключатель", "ПолеВыбора", "ПолеСписка", "ТабличноеПоле", "Панель",
    "РамкаГруппы", "Разделитель", "КоманднаяПанель",
)
_ACTION_PROC = "e1692cc2-605b-4535-84dd-28440238746c"  # действие = процедура модуля формы
_EVENT_SUFFIX = re.compile(
    r"(ПриИзменении|НачалоВыбора(?:ИзСписка)?|Очистка|ОбработкаВыбора|Регулирование|АвтоПодборТекста|ОкончаниеВводаТекста"
    r"|Открытие|Нажатие|ПриАктивизацииСтроки|ПриАктивизацииКолонки|ПередНачаломДобавления|ПередНачаломИзменения"
    r"|ПередУдалением|ПриВыводеСтроки|ПередОкончаниемРедактирования|ПриОкончанииРедактирования|ПриНачалеРедактирования"
    r"|ПриИзмененииФлажка|Выбор|ПриСменеСтраницы|ПриПолученииДанных|ПередРазворачиванием|ОбработкаПеретаскивания)$"
)
_TYPE_CODES = {"S": "Строка", "N": "Число", "D": "Дата", "B": "Булево"}


def element_type(guid: str, name: str, learned: dict[str, str] | None = None) -> str:
    """GUID — истина; типы, которых нет в ELEMENT_TYPES, берём из карты эталона (_Типы), и лишь потом — по имени элемента."""
    return (ELEMENT_TYPES.get(guid) or (learned if learned is not None else _prop_map().get("_Типы") or {}).get(guid)
            or next((t for t in _TYPE_BY_NAME if name.startswith(t)), "") or f"Элемент({guid[:8]})")


@lru_cache(maxsize=1)
def _prop_map() -> dict[str, dict[str, Any]]:
    try:
        return json.loads(MAP_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def mapped_props(key: str, node: Any) -> dict[str, str]:
    """Свойства из карты эталона со значением не по умолчанию: {"Шапка": "Нет", "РежимВыделения": "Множественный"}."""
    out: dict[str, str] = {}
    for prop, ent in (_prop_map().get(key) or {}).items():
        if prop in _OWN_FLAGS or ent.get("unreliable") or ent.get("ambiguous"):
            continue
        try:
            v = node
            for p in ent["path"]:
                v = v[p]
        except (IndexError, TypeError, KeyError):
            continue
        parent = ent.get("requires")  # свойство имеет смысл только при включённом «родителе» (перенос строк — при многострочном режиме)
        if parent:
            pe = (_prop_map().get(key) or {}).get(parent) or {}
            try:
                pv = node
                for p in pe["path"]:
                    pv = pv[p]
            except (IndexError, TypeError, KeyError):
                continue
            if str(pv) != "1":
                continue
        kind, raw = ent["kind"], v if isinstance(v, list) else str(v)
        if kind == "bool" and isinstance(raw, list):
            continue  # блока нет у полей с другим типом значения
        dflt = ent.get("common", ent.get("default"))  # типовое значение по выгрузке; иначе — значение в эталоне
        if kind == "bool":
            if raw != dflt:
                out[prop] = {"1": "Да", "0": "Нет"}.get(raw, f"код {raw}")
        elif kind == "bit":
            try:
                is_set = bool(int(raw) & ent["bit"])
                if is_set != ent.get("common_set", ent["default_set"]):
                    out[prop] = "Да" if is_set else "Нет"
            except (TypeError, ValueError):
                pass
        elif kind in ("enum", "int", "str"):
            if raw != dflt:
                out[prop] = (ent.get("values") or {}).get(raw, f"код {raw}") if kind == "enum" else raw
        elif kind == "block":
            continue  # цвет / шрифт / картинка: значение не расшифровано, голое «задано» ответу ничего не даёт
        else:  # text / enum_block — поддерево целиком
            dump = json.dumps(v, ensure_ascii=False)
            if dump != dflt:
                val = brace.ru_text(v) if kind == "text" else (ent.get("values") or {}).get(dump, "задано")
                if val:
                    out[prop] = val
    return out


def _name(el: list[Any]) -> str:
    for ch in el:
        if isinstance(ch, list) and len(ch) > 1 and ch[0] == "14" and isinstance(ch[1], brace.Str):
            return str(ch[1])
    return ""


def _layout(el: list[Any]) -> list[Any] | None:
    for ch in el:
        if isinstance(ch, list) and len(ch) > 6 and ch[0] == "8" and all(not isinstance(x, list) for x in ch[1:5]):
            return ch
    return None


_FLAG_TYPES = ("ПолеВвода", "Переключатель", "ТабличноеПоле")  # сверены по эталону; у других типов блок свойств иной
_TABLE_EDITABLE = 1024  # бит маски ТабличногоПоля: установлен — можно редактировать


def _control_flags(etype: str, props: Any) -> dict[str, bool]:
    """Доступность / ТолькоПросмотр из блока общих свойств (по умолчанию доступно и редактируется)."""
    try:
        if etype == "ТабличноеПоле":
            block = props[2]
            if not (isinstance(block[0], list) and block[0][0] in ("10", "16")):
                return {}
            out = {"enabled": False} if block[0][1] == "0" else {}
            if not int(block[1][1]) & _TABLE_EDITABLE:
                out["read_only"] = True
            return out
        common = props[2][0]
        if not (isinstance(common[0], list) and common[0][0] in ("10", "16")):
            return {}
        out: dict[str, bool] = {}
        if common[0][1] == "0":
            out["enabled"] = False
        if etype == "ПолеВвода" and common[13] == "1":
            out["read_only"] = True
        return out
    except (IndexError, TypeError):
        return {}


def _value_type(props: Any) -> str:
    """{"Pattern",{"S",14,1}} → Строка(14); ссылочные типы — «Ссылка»."""
    for lst in brace.walk(props):
        if lst and lst[0] == "Pattern" and isinstance(lst[0], brace.Str):
            out = []
            for t in lst[1:]:
                if not isinstance(t, list) or not t:
                    continue
                code = str(t[0])
                if code in _TYPE_CODES:
                    q = [str(x) for x in t[1:] if not isinstance(x, list)]
                    out.append(_TYPE_CODES[code] + (f"({','.join(q[:2])})" if code in "SN" and q and q[0] != "0" else ""))
                elif code == "#":
                    out.append("Ссылка")
            return ", ".join(dict.fromkeys(out))
    return ""


def _handlers(node: Any) -> list[str]:
    """Процедуры, назначенные событиям/действиям внутри узла: {…,e1692cc2…,{3,"Процедура",…}}."""
    out: list[str] = []
    for lst in brace.walk(node):
        for i, x in enumerate(lst):
            if x == _ACTION_PROC and i + 1 < len(lst):
                nxt = lst[i + 1]
                if isinstance(nxt, list) and len(nxt) > 1 and nxt[0] == "3" and isinstance(nxt[1], brace.Str):
                    if str(nxt[1]) not in out:
                        out.append(str(nxt[1]))
    return out


def _pages(props: Any) -> list[dict[str, Any]]:
    """Страницы панели: {5,{1,1,{"ru","Заголовок"}},{…},-1,видима,1,"Имя",…}."""
    pages = []
    for lst in brace.walk(props):
        if len(lst) >= 7 and lst[0] == "5" and isinstance(lst[1], list):
            cap = brace.ru_text(lst[1])
            name = next((str(x) for x in lst[2:] if isinstance(x, brace.Str) and str(x)), "")
            ints = [x for x in lst[2:8] if not isinstance(x, list)]
            vis = True
            if len(ints) >= 3 and ints[0] == "-1":
                vis = ints[1] != "0"
            if cap or name:
                pages.append({"name": name or cap, "caption": cap or name, "visible": vis})
    return pages


def _caption(etype: str, props: Any) -> str:
    # заголовок элемента — первый {"ru",…} в свойствах (у Надписи — её текст, у Флажка — подпись);
    # описание обработчика события {3,"Процедура",{…{"ru","… при изменении"}}} — не подпись
    handler = {id(x) for h in brace.walk(props) if len(h) > 1 and h[0] == "3" and isinstance(h[1], brace.Str)
               for x in brace.walk(h)}
    for lst in brace.walk(props):
        if (id(lst) not in handler and len(lst) >= 2 and lst[0] == "ru" and isinstance(lst[1], brace.Str)
                and lst[1] != "ru"):
            return str(lst[1])[:200]
    return ""


def _form_title(top: list[Any]) -> str:
    # заголовок окна — первые {"ru",…} в корне формы до списка элементов
    for ch in top[:3]:
        t = brace.ru_text(ch) if isinstance(ch, list) else ""
        if t:
            return t
    return ""


def _default_element_id(top: Any) -> str:
    """Идентификатор элемента, активного по умолчанию: слот уровня формы в блоке [1,2,1,1], за ним идут «5, 64»; 4294967295 — нет."""
    try:
        blk = top[1][2][1][1]
    except (IndexError, TypeError, KeyError):
        return ""
    if not isinstance(blk, list):
        return ""
    idx = [i for i in range(len(blk) - 2) if not isinstance(blk[i], list) and blk[i + 1] == "5" and blk[i + 2] == "64"]
    v = blk[idx[-1]] if idx else ""
    return str(v) if str(v).isdigit() and str(v) != "4294967295" else ""


def ordinary_form_layout(
    form_text: str,
    module_text: str = "",
    object_attrs: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    form_text — текст …/form (обычная форма). module_text — модуль формы (для сверки обработчиков).
    object_attrs — реквизиты/ТЧ объекта {Имя: Синоним} — для путей к данным по имени элемента.
    """
    tree = brace.parse(form_text)
    top = tree[0] if tree and isinstance(tree[0], list) else tree
    attrs = object_attrs or {}
    procs = re.findall(r"(?im)^[ \t]*(?:Процедура|Функция|Procedure|Function)[ \t]+([A-Za-zА-Яа-яЁё_]\w*)", module_text or "")

    # ---- элементы: дочерние вложены в описание родителя (Панель/РамкаГруппы) — родитель из дерева
    raw: list[tuple[str, str, list[Any]]] = []
    parent_of: dict[str, str] = {}

    def visit(node: list[Any], parent: str) -> None:
        cur = parent
        if len(node) >= 4 and brace.is_guid(node[0]) and not isinstance(node[1], list):
            nm = _name(node)
            if nm:
                raw.append((node[0], nm, node))
                if parent:
                    parent_of[nm] = parent
                cur = nm
        for ch in node:
            if isinstance(ch, list):
                visit(ch, cur)

    visit(top, "")
    elements: list[dict[str, Any]] = []
    panels: list[dict[str, Any]] = []
    default_id = _default_element_id(top)
    for guid, nm, el in raw:
        etype = element_type(guid, nm)
        props = el[2] if len(el) > 2 else []
        lay = _layout(el)
        rect = [brace.to_int(x) for x in lay[1:5]] if lay else []
        tail = [x for x in (lay or [])[7:] if not isinstance(x, list)]
        page_idx = brace.to_int(tail[-5], -1) if len(tail) >= 5 else -1
        item: dict[str, Any] = {"name": nm, "type": etype}
        if lay and len(lay) > 5 and lay[5] == "0":
            item["visible"] = False
        if etype in _FLAG_TYPES and isinstance(props, list):
            item.update(_control_flags(etype, props))
        if etype == "ТабличноеПоле":
            # колонки: подписи {"ru",…} в свойствах, кроме описаний обработчиков событий
            from tools.epf_inspect import _TECH_CAPTION

            handler_caps = {brace.ru_text(x) for x in brace.walk(props) if x and x[0] == "3" and len(x) > 1}
            cols = []
            for lst in brace.walk(props):
                if len(lst) == 2 and lst[0] == "ru" and isinstance(lst[1], brace.Str):
                    c = str(lst[1]).strip()
                    if c and c not in cols and c not in handler_caps and not _TECH_CAPTION.search(c):
                        cols.append(c)
            if cols:
                item["columns"] = cols
        cap = "" if etype == "ТабличноеПоле" else _caption(etype, props)
        if cap and cap != nm:
            item["caption"] = cap
        vt = _value_type(props)
        if vt:
            item["value_type"] = vt
        if nm in attrs:
            item["data_path"] = nm
            item["data_path_by_name"] = True
            if attrs[nm] and attrs[nm] != cap:
                item["attribute_synonym"] = attrs[nm]
        hs = _handlers(props) if etype != "КоманднаяПанель" else []
        if hs:
            item["events"] = {(_EVENT_SUFFIX.search(h).group(1) if _EVENT_SUFFIX.search(h) else "Действие"): h for h in hs}
        mp = {**mapped_props("_Элемент", el), **mapped_props(etype, el)}
        if default_id and len(el) > 1 and str(el[1]) == default_id:
            mp["АктивизироватьПоУмолчанию"] = "Да"  # хранится на уровне формы, а не в элементе
        if mp:
            item["props"] = mp
        item["_rect"], item["_page"] = rect, page_idx
        elements.append(item)
        if etype == "Панель":
            pg = _pages(props)
            if pg:
                panels.append({"name": nm, "pages": pg, "_rect": rect})

    # ---- родитель и страница: родитель — вложенность в дереве; страница — номер из раскладки элемента
    by_panel = {pn["name"]: pn for pn in panels}
    for it in elements:
        par = parent_of.get(it["name"], "")
        if not par:
            continue
        it["parent"] = par
        pn = by_panel.get(par)
        if pn and 0 <= it["_page"] < len(pn["pages"]):
            it["page"] = pn["pages"][it["_page"]]["caption"]
    # вкладка «по цепочке»: поле в РамкеГруппы/вложенной панели — на вкладке своего предка
    page_of = {it["name"]: it.get("page") for it in elements}
    for it in elements:
        if it.get("page"):
            continue
        par = it.get("parent")
        hops = 0
        while par and hops < 10 and not page_of.get(par):
            par = parent_of.get(par, "")
            hops += 1
        if par and page_of.get(par):
            it["page"] = page_of[par]
            it["page_via"] = par

    # ---- вкладки с элементами (верхние панели первыми)
    pages_out = []
    for pn in panels:
        for i, pg in enumerate(pn["pages"]):
            members = [it["name"] for it in elements if it.get("parent") == pn["name"] and it["_page"] == i]
            pages_out.append({"panel": pn["name"], "name": pg["name"], "caption": pg["caption"],
                              "visible": pg["visible"], "elements": members})

    # ---- кнопки: действия {7,uuid,…,{3,"Процедура",…}} и пункты <uuid>,{8,"Имя",…}
    actions: dict[str, dict[str, str]] = {}
    for lst in brace.walk(top):
        if len(lst) >= 5 and lst[0] == "7" and brace.is_guid(lst[1]):
            body = next((x for x in lst[2:] if isinstance(x, list) and len(x) > 1 and x[0] == "3"), None)
            handler = str(body[1]) if body and isinstance(body[1], brace.Str) and lst[3] == _ACTION_PROC else ""
            actions[lst[1]] = {"handler": handler, "caption": brace.ru_text(body) if body else ""}
    owner_of: dict[int, str] = {}
    for _g, nm, el in raw:
        for lst in brace.walk(el):
            owner_of.setdefault(id(lst), nm)
    buttons, seen = [], set()
    for lst in brace.walk(top):
        for i in range(1, len(lst)):
            x = lst[i]
            if (isinstance(x, list) and len(x) > 4 and x[0] == "8" and isinstance(x[1], brace.Str)
                    and brace.is_guid(lst[i - 1])):
                nm = str(x[1])
                if nm.startswith("Разделитель"):
                    continue
                act = actions.get(lst[i - 1], {})
                cap = brace.ru_text(x) or act.get("caption") or nm
                if re.match(r"Действие\d*$", cap):
                    continue  # пустой пункт без подписи
                key = (owner_of.get(id(lst), ""), nm)
                if key in seen:
                    continue
                seen.add(key)
                b = {"caption": cap, "name": nm, "panel": owner_of.get(id(lst), "")}
                if act.get("handler"):
                    b["handler"] = act["handler"]
                else:
                    b["standard"] = True  # стандартное действие платформы (Записать, ОК, Закрыть…)
                buttons.append(b)
    # обычные «Кнопка» (не в командной панели)
    for it in elements:
        if it["type"] == "Кнопка":
            buttons.append({"caption": it.get("caption") or it["name"], "name": it["name"],
                            "handler": next(iter((it.get("events") or {}).values()), None)})

    # действия-процедуры без пункта меню (подменю, контекстные меню) — тоже кнопки
    have = {b.get("handler") for b in buttons}
    for act in actions.values():
        if act["handler"] and act["handler"] not in have:
            have.add(act["handler"])
            buttons.append({"caption": act["caption"] or act["handler"], "name": act["handler"], "handler": act["handler"]})

    # ---- события формы: в обычной форме привязаны по имени процедуры модуля
    used = {h for it in elements for h in (it.get("events") or {}).values()} | {b.get("handler") for b in buttons}
    form_events = [p for p in procs if p in FORM_EVENTS]
    # прочие процедуры, упомянутые в форме, но не найденные у элементов/кнопок
    other_bound = [h for h in _handlers(top) if h not in used and h not in form_events]

    for it in elements:
        it.pop("_rect", None)
        it.pop("_page", None)
    out: dict[str, Any] = {
        "kind": "ordinary",
        "title": _form_title(top if isinstance(top, list) else []),
        "pages": pages_out,
        "elements": elements,
        "buttons": buttons,
        "form_events": form_events,
        "other_bound_handlers": other_bound,
        "notes": [
            "Обычная форма. data_path взят по имени элемента = реквизиту объекта (data_path_by_name), "
            "в самом файле формы путь к данным строкой не хранится.",
        ],
    }
    fp = mapped_props(FORM_KEY, top)
    dflt = next((it["name"] for it in elements if (it.get("props") or {}).get("АктивизироватьПоУмолчанию")), "")
    if dflt:
        fp["ЭлементАктивныйПоУмолчанию"] = dflt
    if fp:
        out["form_props"] = fp
    if procs:
        all_h = used | set(form_events)
        out["handlers_missing_in_module"] = sorted(h for h in all_h if h and h not in procs)
    return out
