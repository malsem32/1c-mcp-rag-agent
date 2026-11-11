# -*- coding: utf-8 -*-
"""
Сборка внешней обработки 1С (.epf) без платформы: контейнер v8 + скобочные метаданные.

Что умеет: модуль объекта, реквизиты и табличные части (примитивные типы), обычная форма с кнопками
«Выполнить/Закрыть» (кнопка вызывает экспортную `ВыполнитьОбработку()` модуля объекта).
Шаблоны узлов (реквизит, ТЧ, форма по умолчанию) — tools/data/epf_tpl/*.txt, сняты с файлов, собранных платформой.
Проверка готового файла: verify_epf() — свой разбор + (если есть платформа) выгрузка Конфигуратором.
"""

from __future__ import annotations

import contextvars
import difflib
import re
import struct
import subprocess
import tempfile
import time
import uuid
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any

_TPL_DIR = Path(__file__).parent / "data" / "epf_tpl"
_BOM = "\ufeff"
_GUID0 = "00000000-0000-0000-0000-000000000000"
_END = 0x7FFFFFFF
_PAGE = 512
_IDENT_RE = re.compile(r"^[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*$")
_ENTRY_PROC = "ВыполнитьОбработку"
_FORM_HANDLER = "ОсновныеДействияФормыОсновныеДействияФормыВыполнить"


class BuildError(ValueError):
    """Ошибка в описании обработки (понятное сообщение для пользователя)."""


# ------------------------------------------------------------------ контейнер v8
def _ticks() -> bytes:
    """Время в единицах 1/10000 с от 0001-01-01 (как в заголовках элементов контейнера)."""
    return struct.pack("<Q", int((datetime.utcnow() - datetime(1, 1, 1)).total_seconds() * 10000))


def _block(payload: bytes, *, pad: bool) -> bytes:
    """Документ контейнера из одной страницы; тело и оглавление дополняются до 512, заголовок элемента — нет."""
    page = max(len(payload), _PAGE) if pad else len(payload)
    hdr = b"\r\n%08x %08x %08x \r\n" % (len(payload), page, _END)
    return hdr + payload + b"\x00" * (page - len(payload))


def _pack_container(entries: list[tuple[str, bytes]], stamp: bytes | None = None) -> bytes:
    """entries — уже в порядке оглавления; тело кладётся как есть (сжатие — забота вызывающего)."""
    stamp = stamp or _ticks()
    out = bytearray(struct.pack("<IIII", _END, _PAGE, 6, 0))
    toc_len = len(_block(b"\x00" * (12 * len(entries)), pad=True))
    pos = 16 + toc_len
    toc = bytearray()
    body = bytearray()
    for name, data in entries:
        hdr_payload = stamp + stamp + b"\x00" * 4 + name.encode("utf-16-le") + b"\x00" * 4
        hdr_blk, data_blk = _block(hdr_payload, pad=False), _block(data, pad=True)
        toc += struct.pack("<III", pos, pos + len(hdr_blk), _END)
        body += hdr_blk + data_blk
        pos += len(hdr_blk) + len(data_blk)
    return bytes(out) + _block(bytes(toc), pad=True) + bytes(body)


def _deflate(data: bytes) -> bytes:
    z = zlib.compressobj(9, zlib.DEFLATED, -15)
    return z.compress(data) + z.flush()


def _text(s: str) -> bytes:
    """Поток с BOM и CRLF (так пишет платформа)."""
    return (_BOM + re.sub(r"\r?\n", "\r\n", s)).encode("utf-8")


# ------------------------------------------------------------------ типы реквизитов
def _guid() -> str:
    return str(uuid.uuid4())


# ссылочные типы конфигурации: класс из фразы → каталог выгрузки (GUID класса для copyinfo — epf_inspect._MD_CLASS_RU)
_REF_DIRS = {
    "Справочник": "Catalogs", "Документ": "Documents", "Перечисление": "Enums",
    "ПланВидовХарактеристик": "ChartsOfCharacteristicTypes", "ПланСчетов": "ChartsOfAccounts",
    "ПланВидовРасчета": "ChartsOfCalculationTypes", "ПланОбмена": "ExchangePlans",
    "БизнесПроцесс": "BusinessProcesses", "Задача": "Tasks",
}
_REF_RE = re.compile(r"^(%s)(?:ссылка|ref)?\.([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)$" % "|".join(_REF_DIRS), re.I)
_REF_CTX: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("epf_ref_ctx", default=None)


def _cf_root() -> Path:
    """Корень выгрузки CF: зеркало коллекции файлов Qdrant (runtime диск конфигурации напрямую не читает)."""
    ctx = _REF_CTX.get() or {}
    if ctx.get("root"):
        return Path(ctx["root"])
    from vector.files import ensure_mirror

    root = ensure_mirror()
    if root is None:
        raise BuildError("Ссылочные типы берутся из выгрузки конфигурации в Qdrant, а она не проиндексирована.")
    return root


def _resolve_ref(kind: str, name: str) -> dict[str, str]:
    """«Справочник», «Номенклатура» → {type_id (GUID типа …Ref), obj_id (uuid объекта), cls (GUID класса), name, full}."""
    from tools.epf_inspect import _MD_CLASS_RU

    kind = next(k for k in _REF_DIRS if k.lower() == kind.lower())
    folder = _cf_root() / _REF_DIRS[kind]
    files = {p.stem.lower(): p for p in folder.glob("*.xml")} if folder.is_dir() else {}
    path = files.get(name.lower())
    if path is None:
        near = difflib.get_close_matches(name.lower(), list(files), n=5, cutoff=0.6)
        hint = f" Похожие: {', '.join(kind + '.' + files[n].stem for n in near)}." if near else ""
        raise BuildError(f"В конфигурации нет объекта {kind}.{name}.{hint}")
    text = path.read_text("utf-8-sig", errors="replace")
    m_obj = re.search(r'<\w+ uuid="([0-9a-f-]{36})"', text)
    m_ref = re.search(r'category="Ref">\s*<xr:TypeId>([0-9a-f-]{36})</xr:TypeId>', text)
    if not (m_obj and m_ref):
        raise BuildError(f"{kind}.{path.stem}: в выгрузке нет идентификатора ссылочного типа")
    cls = next(g for g, ru in _MD_CLASS_RU.items() if ru == kind)
    return {"type_id": m_ref.group(1), "obj_id": m_obj.group(1), "cls": cls, "name": path.stem, "full": f"{kind}.{path.stem}"}


def _split_types(t: str) -> list[str]:
    """«Строка(10), Число(15,2), СправочникСсылка.Х» → части по запятым вне скобок."""
    out, depth, cur = [], 0, ""
    for ch in t:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return [x.strip() for x in out + [cur] if x.strip()]


def _type_item(t: str) -> str:
    low = t.lower().replace("ё", "е")
    m = re.fullmatch(r"(?:строка|string)(?:\s*\(\s*(\d+)\s*\))?", low)
    if m:
        n = int(m.group(1) or 0)
        return '{"S",%d,1}' % n if n else '{"S"}'
    m = re.fullmatch(r"(?:число|number)(?:\s*\(\s*(\d+)\s*(?:,\s*(\d+))?\s*(?:,\s*(неотр\w*|nonneg\w*))?\s*\))?", low)
    if m:
        digits, frac = int(m.group(1) or 15), int(m.group(2) or 0)
        if digits > 38 or frac > digits:
            raise BuildError(f"Тип «{t}»: разрядность до 38, дробная часть не больше разрядности")
        return '{"N",%d,%d,%d}' % (digits, frac, 1 if m.group(3) else 0)
    simple = {"булево": '{"B"}', "boolean": '{"B"}', "bool": '{"B"}', "дата": '{"D","D"}', "date": '{"D","D"}',
              "датавремя": '{"D"}', "datetime": '{"D"}', "время": '{"D","T"}', "time": '{"D","T"}'}
    if low in simple:
        return simple[low]
    m = _REF_RE.match(t.replace(" ", ""))
    if m:
        ref = _resolve_ref(m.group(1), m.group(2))
        ctx = _REF_CTX.get()
        if ctx is not None:
            ctx.setdefault("refs", {})[ref["type_id"]] = ref
        return '{"#",%s}' % ref["type_id"]
    raise BuildError(
        f"Тип «{t}» не поддержан: доступны Строка(N), Число(N,M), Булево, Дата, ДатаВремя, Время, Произвольный "
        "и ссылки на объекты этой конфигурации (СправочникСсылка.Имя, ДокументСсылка.Имя, ПеречислениеСсылка.Имя …)."
    )


def _pattern(type_text: str) -> str:
    """«Строка(50)», «Число(15,2)», «СправочникСсылка.Номенклатура», составной через запятую → {"Pattern",…}."""
    t = (type_text or "").strip()
    if not t or t.lower() in ("произвольный", "any", "неопределено"):
        return '{"Pattern"}'
    items = [_type_item(x) for x in _split_types(t)]
    return '{"Pattern",\r\n' + ",\r\n".join(items) + "\r\n}"


def _tpl(name: str) -> str:
    return (_TPL_DIR / name).read_text("utf-8")


def _q(s: str) -> str:
    return (s or "").replace('"', '""')


def _ident(name: str, what: str) -> str:
    n = (name or "").strip()
    if not _IDENT_RE.match(n) or len(n) > 80:
        raise BuildError(f"{what} «{name}»: имя — буквы/цифры/подчёркивание, не с цифры, до 80 знаков")
    return n


def _attr_node(name: str, synonym: str, type_text: str, attr_id: str | None = None) -> str:
    return (
        _tpl("attr_item.txt")
        .replace("@@ID@@", attr_id or _guid())
        .replace("@@NAME@@", _q(_ident(name, "Реквизит")))
        .replace("@@SYN@@", _q(synonym or name))
        .replace("@@PATTERN@@", _pattern(type_text))
    )


def _collection(cls: str, items: list[str]) -> str:
    if not items:
        return "{%s,0}" % cls
    return "{%s,%d,\r\n%s\r\n}" % (cls, len(items), ",\r\n".join(items))


def _ts_node(ts: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """(узел метаданных, сведения для формы: id ТЧ, тип таблицы, колонки с id и шаблоном типа)."""
    name = _ident(ts.get("name", ""), "Табличная часть")
    cols = ts.get("columns") or []
    if not cols:
        raise BuildError(f"Табличная часть «{name}»: нужна хотя бы одна колонка")
    ts_id, g1 = _guid(), _guid()
    col_ids = [_guid() for _ in cols]
    col_items = [_attr_node(c.get("name", ""), c.get("synonym", ""), c.get("type", ""), i) for c, i in zip(cols, col_ids)]
    node = (
        _tpl("ts_item.txt")
        .replace("@@ID@@", ts_id)
        .replace("@@NAME@@", _q(name))
        .replace("@@SYN@@", _q(ts.get("synonym") or name))
        .replace("@@G1@@", g1).replace("@@G2@@", _guid()).replace("@@G3@@", _guid()).replace("@@G4@@", _guid())
        .replace("@@COLUMNS@@", _collection("5d24a9d1-098e-11d6-b9b8-0050bae0a95d", col_items))
    )
    info = {
        "id": ts_id, "type": g1, "name": name, "caption": ts.get("synonym") or name,
        "cols": [{"id": i, "name": c["name"], "caption": c.get("synonym") or c["name"], "pattern": _pattern(c.get("type", ""))}
                 for c, i in zip(cols, col_ids)],
    }
    return node, info


# ------------------------------------------------------------------ форма с полями ввода
_FORM_W = 400
_FIELD_TOP, _FIELD_STEP, _FIELD_H, _LABEL_H = 45, 28, 21, 19
_TABLE_H = 150
_PAGE_TOP = 33 # верх панели страниц в координатах формы; якорь элемента — смещение от него
_LABEL_LEFT, _LABEL_RIGHT, _FIELD_LEFT = 16, 144, 150
_NL = "\n"  # шаблоны читаются с нормализацией переводов строк; CRLF ставит _text()


def _table_elements(tables: list[dict[str, Any]], first_id: int, top0: int, form_w: int = _FORM_W) -> tuple[list[str], int]:
    """Надпись + ТабличноеПоле на каждую ТЧ (id надписи first_id+2j, таблицы +1); возвращает элементы и низ последней таблицы."""
    els: list[str] = []
    top = top0
    for j, t in enumerate(tables):
        lid, tid = first_id + 2 * j, first_id + 2 * j + 1
        els.append(
            _tpl("field_label.txt")
            .replace("@@ID@@", str(lid)).replace("@@TEXT@@", _q(t["caption"]) + ":")
            .replace("@@L@@", str(_LABEL_LEFT)).replace("@@T@@", str(top)).replace("@@R@@", str(form_w - 20))
            .replace("@@B@@", str(top + _LABEL_H)).replace("@@AT@@", str(top - _PAGE_TOP)).replace("@@H@@", str(_LABEL_H))
            .replace("@@W@@", str(form_w - 36)).replace("@@O1@@", str(lid)).replace("@@O2@@", str(lid - 1))
            .replace("@@NAME@@", "Надпись" + _q(t["name"]))
        )
        top += _LABEL_H + 4
        cols = ",\n".join(
            _tpl("tbl_column.txt")
            .replace("@@TITLE@@", _q(c["caption"])).replace("@@IDX@@", str(k)).replace("@@NAME@@", _q(c["name"]))
            .replace("@@FLAG@@", "0" if "#" in c["pattern"] else "1").replace("@@PATTERN@@", c["pattern"])
            .replace("@@COLID@@", c["id"]).replace("@@EDITOR@@", _tpl("tbl_editor.txt"))
            for k, c in enumerate(t["cols"])
        )
        els.append(
            _tpl("tbl_element.txt")
            .replace("@@ID@@", str(tid)).replace("@@TYPE@@", t["type"]).replace("@@COLUMNS@@", "{%d,\n%s\n}" % (len(t["cols"]), cols))
            .replace("@@L@@", str(_LABEL_LEFT)).replace("@@T@@", str(top)).replace("@@R@@", str(form_w - 20))
            .replace("@@B@@", str(top + _TABLE_H)).replace("@@NAME@@", _q(t["name"]))
        )
        top += _TABLE_H + 10
    return els, top


# события обычной формы: код в Form.bin (сверено по 4670 формам выгрузки) → подпись; без записи процедура не вызывается
_FORM_EVENT_CODES = {
    "ПередОткрытием": (70000, "Перед открытием"), "ПриОткрытии": (70001, "При открытии"),
    "ПередЗакрытием": (70002, "Перед закрытием"), "ПриЗакрытии": (70003, "При закрытии"),
    "ОбработкаВыбора": (70004, "Обработка выбора"), "ОбработкаАктивизацииОбъекта": (70005, "Обработка активизации объекта"),
    "ОбработкаЗаписиНовогоОбъекта": (70006, "Обработка записи нового объекта"), "ОбработкаОповещения": (70007, "Обработка оповещения"),
    "ПриПовторномОткрытии": (70008, "При повторном открытии"), "ОбновлениеОтображения": (70009, "Обновление отображения"),
    "ВнешнееСобытие": (70010, "Внешнее событие"), "ОбработкаПроверкиЗаполнения": (70011, "Обработка проверки заполнения"),
}
# события поля ввода (сверено по выгрузке): суффикс процедуры <ИмяПоля><Событие> → код, подпись
_FIELD_EVENT_CODES = {
    "ПриИзменении": (2147483647, "при изменении"), "НачалоВыбораИзСписка": (1, "начало выбора из списка"),
    "НачалоВыбора": (2, "начало выбора"), "Очистка": (3, "очистка"), "Регулирование": (4, "регулирование"),
    "Открытие": (5, "открытие"), "ОбработкаВыбора": (7, "обработка выбора"),
    "ОкончаниеВводаТекста": (10, "окончание ввода текста"), "АвтоПодборТекста": (11, "авто подбор текста"),
}
_FORM_PROC_RE = re.compile(r"(?im)^\s*Процедура\s+([А-Яа-яЁёA-Za-z_][\wА-Яа-яЁё]*)\s*\(")


def _event_item(code: int, proc: str, caption: str) -> str:
    """Запись обработчика события {код, e1692cc2…, {3,"Процедура",…}} — одинакова для формы и элементов."""
    nl = _NL
    ru = f'{{1,1,{nl}{{"ru","{caption}"}}{nl}}}'
    return (f'{{{code},e1692cc2-605b-4535-84dd-28440238746c,{nl}{{3,"{proc}",{nl}{{1,"{proc}",{nl}'
            f'{ru},{nl}{ru},{nl}{ru},{nl}{{3,0,{nl}{{0}},"",-1,-1,1,0}},{nl}{{0,0,0}}{nl}}}{nl}}}{nl}}}')


def _event_list(items: list[str]) -> str:
    """Список обработчиков {N, …}; пустой — {0}."""
    return f"{{{len(items)},{_NL}" + f",{_NL}".join(items) + f"{_NL}}}" if items else "{0}"


def field_events_of(form_module: str, field: str) -> list[tuple[int, str, str]]:
    """Процедуры <ИмяПоля><Событие> модуля формы (как их называет Конфигуратор) → события поля ввода."""
    names = {m.group(1) for m in _FORM_PROC_RE.finditer(form_module or "")}
    return [(code, field + sfx, f"{field} {cap}") for sfx, (code, cap) in _FIELD_EVENT_CODES.items() if field + sfx in names]


def form_events_of(form_module: str) -> list[str]:
    """Процедуры модуля формы с именами событий формы (ПриОткрытии, ПередЗакрытием…) — назначаются форме при сборке."""
    names = {m.group(1) for m in _FORM_PROC_RE.finditer(form_module or "")}
    return [e for e in _FORM_EVENT_CODES if e in names]


def _with_form_events(text: str, events: list[str]) -> str:
    if not events:
        return text
    nl = _NL
    anchor = f"{{0,0}},{nl}{{0}},1}}{nl}}},{nl}{{0}},1,4,1,0,0,0,"
    if text.count(anchor) != 1:
        raise BuildError("Шаблон формы изменился: не найдено место списка событий формы")
    block = _event_list([_event_item(_FORM_EVENT_CODES[e][0], e, _FORM_EVENT_CODES[e][1]) for e in events]) + ",1,4,1,0,0,0,"
    return text.replace(anchor, anchor[: -len("{0},1,4,1,0,0,0,")] + block)


# ------------------------------------------------------------------ элементы палитры (controls)
# шаблоны сняты с эталона владельца: scripts/epf_ctl_templates.py → tools/data/epf_tpl/ctl/<Тип>.txt;
# элемент без привязки к данным, из кода доступен как ЭлементыФормы.<Имя>
_CLICK = {"Нажатие": (0, "нажатие")}
_CHANGE = {"ПриИзменении": (2147483647, "при изменении")}
# коды событий по типам (сверено по 4670 формам выгрузки); процедура модуля формы <ИмяЭлемента><Событие>
_CTL_EVENT_CODES: dict[str, dict[str, tuple[int, str]]] = {
    "ПолеВвода": _FIELD_EVENT_CODES, "ПолеВыбора": _FIELD_EVENT_CODES,
    "Кнопка": _CLICK, "Надпись": _CLICK, "ПолеКартинки": _CLICK,
    "Флажок": _CHANGE, "Переключатель": _CHANGE, "ПолосаРегулирования": _CHANGE,
    "Панель": {"ПриСменеСтраницы": (0, "при смене страницы")},
    "ПолеКалендаря": {**_CHANGE, "ПриАктивизацииДаты": (0, "при активизации даты"), "Выбор": (1, "выбор"),
                      "ПриВыводеПериода": (2, "при выводе периода")},
    "ПолеСписка": {"Выбор": (34, "выбор"), "ПриИзмененииФлажка": (17, "при изменении флажка"),
                   "ПриАктивизацииСтроки": (37, "при активизации строки")},
    "ТабличноеПоле": {
        "Выбор": (34, "выбор"), "ПриАктивизацииСтроки": (35, "при активизации строки"),
        "ПриАктивизацииКолонки": (36, "при активизации колонки"), "ПриАктивизацииЯчейки": (37, "при активизации ячейки"),
        "ПередНачаломДобавления": (40, "перед началом добавления"), "ПередНачаломИзменения": (41, "перед началом изменения"),
        "ПередУдалением": (42, "перед удалением"), "ПриНачалеРедактирования": (43, "при начале редактирования"),
        "ПередОкончаниемРедактирования": (44, "перед окончанием редактирования"),
        "ПриИзмененииФлажка": (45, "при изменении флажка"), "ПриВыводеСтроки": (47, "при выводе строки"),
        "ПриОкончанииРедактирования": (49, "при окончании редактирования"), "ПослеУдаления": (51, "после удаления"),
        "ПриПолученииДанных": (53, "при получении данных"),
    },
    "ПолеТабличногоДокумента": {"Выбор": (0, "выбор"), "ОбработкаРасшифровки": (1, "обработка расшифровки"),
                                "ПриАктивизацииОбласти": (2, "при активизации области"),
                                "ПриИзмененииСодержимогоОбласти": (7, "при изменении содержимого области")},
    "ПолеHTMLДокумента": {"ДокументСформирован": (0, "документ сформирован"), "onclick": (2, "onclick")},
    # диаграммы: коды сверены по выгрузке (Выбор 0, ОбработкаРасшифровки 1; Гант — 2 при окончании редактирования интервала)
    "Диаграмма": {"Выбор": (0, "выбор"), "ОбработкаРасшифровки": (1, "обработка расшифровки")},
    "ДиаграммаГанта": {"Выбор": (0, "выбор"), "ПриОкончанииРедактированияИнтервала": (2, "при окончании редактирования интервала")},
}
_SCALAR_EMPTY_EVENTS = {"Диаграмма", "ДиаграммаГанта"}  # у диаграмм перед списком событий флаг 0/1
# размер по умолчанию (ширина None — во всю ширину формы)
_CTL_SIZE: dict[str, tuple[int | None, int]] = {
    "Кнопка": (120, 24), "Надпись": (None, 19), "Флажок": (None, 19), "Переключатель": (None, 19),
    "ПолеВвода": (None, 19), "ПолеВыбора": (None, 19), "ПолосаРегулирования": (None, 32), "Индикатор": (None, 14),
    "Разделитель": (None, 6), "РамкаГруппы": (None, 60), "КоманднаяПанель": (None, 25), "ПолеКалендаря": (180, 160),
    "ПолеКартинки": (120, 120), "ПолеСписка": (None, 120),
}
_CTL_DEFAULT_H = 250  # таблицы, документы, диаграммы, панель
_CTL_TITLED = {"Кнопка", "Надпись", "Флажок", "Переключатель", "РамкаГруппы"}


def _ctl_events_text(typ: str, evs: list[tuple[int, str, str]]) -> str:
    if typ in _SCALAR_EMPTY_EVENTS:  # флаг «есть события»: 0 — списка нет, 1 — следом список
        return "1," + _event_list([_event_item(*e) for e in evs]) if evs else "0"
    return _event_list([_event_item(*e) for e in evs])


def control_types() -> list[str]:
    return sorted(p.stem for p in (_TPL_DIR / "ctl").glob("*.txt"))


def _ctl_type(raw: str) -> str:
    key = re.sub(r"\s+", "", raw or "").lower()
    aliases = {"индикаторпрогресса": "Индикатор", "календарь": "ПолеКалендаря", "полеhtml": "ПолеHTMLДокумента",
               "табличныйдокумент": "ПолеТабличногоДокумента", "текстовыйдокумент": "ПолеТекстовогоДокумента",
               "картинка": "ПолеКартинки", "графическаясхема": "ПолеГрафическойСхемы",
               "географическаясхема": "ПолеГеографическойСхемы", "таблица": "ТабличноеПоле"}
    types = {t.lower(): t for t in control_types()}
    t = types.get(key) or aliases.get(key)
    if not t:
        near = difflib.get_close_matches(key, list(types), n=3, cutoff=0.6)
        hint = f" Похожие: {', '.join(types[n] for n in near)}." if near else ""
        raise BuildError(f"Тип элемента «{raw}» не поддерживается.{hint} Есть: {', '.join(control_types())}")
    return t


def control_events_of(form_module: str, name: str, typ: str) -> list[tuple[int, str, str]]:
    """Процедуры <ИмяЭлемента><Событие> модуля формы → обработчики событий элемента."""
    names = {m.group(1) for m in _FORM_PROC_RE.finditer(form_module or "")}
    return [(code, name + sfx, f"{name} {cap}") for sfx, (code, cap) in _CTL_EVENT_CODES.get(typ, {}).items()
            if name + sfx in names]


_SIDES = {"top": 0, "верх": 0, "bottom": 1, "низ": 1, "left": 2, "лево": 2, "right": 3, "право": 3}
_SIDE_NAMES = ("верх", "низ", "лево", "право")
_REG_BASE = [[], [(3, 1)], [(2, 2), (3, 2)], [(2, 3), (3, 3)], [], []]  # реестр формы по умолчанию: (элемент, сторона) по 6 сторонам


def _side_of(raw: Any, who: str) -> int:
    s = _SIDES.get(str(raw).strip().lower())
    if s is None:
        raise BuildError(f"{who}: сторона «{raw}» неизвестна (top/bottom/left/right или верх/низ/лево/право)")
    return s


def _ctl_layout(cid: int, rect: tuple[int, int, int, int], pairs: list[tuple[int, int, int] | None],
                deps: list[list[tuple[int, int]]], order: int, tab: int | None = None) -> str:
    """Раскладка {8,…}: rect = (лево, верх, право, низ); pairs — привязки сторон верх/низ/лево/право, каждая
    (объект, сторона, отступ) или None (свободна): объект 0 — форма, иначе id элемента, сторона 0 верх / 1 низ / 2 лево /
    3 право, граница = граница объекта + отступ. deps — по сторонам элемента: кто на неё привязан ({0,id,сторона} — сторона
    привязанного). order — O1 (уникальный индекс), tab — O2 (порядок обхода Tab; по умолчанию = order)."""
    nl = _NL
    none = "{2,-1,6,0}"

    def pair(b: tuple[int, int, int] | None) -> str:
        return f"{{0,{nl}{{2,{b[0]},{b[1]},{b[2]}}},{nl}{none}{nl}}}" if b else f"{{0,{nl}{none},{nl}{none}{nl}}}"

    free = pair(None)
    lists = "".join(f"{len(d)}," + "".join(f"{nl}{{0,{e},{k}}}," for e, k in d) for d in deps)
    left, top, right, bottom = rect
    return (f"{{8,{left},{top},{right},{bottom},1,{nl}" + f",{nl}".join(pair(b) for b in pairs)
            + f",{nl}{free},{nl}{free},{lists}0,0,0,{order},{order if tab is None else tab},0,0}}")


def _bind_controls(plan: list[dict[str, Any]], first_id: int, form_w: int, form_h: int) -> list[list[tuple[int, int]]]:
    """Привязки сторон каждого элемента плана: p["pairs"], p["deps"] (обратные ссылки), p["id"]; возвращает привязки к форме
    по сторонам формы — их надо записать в реестр формы (без записи платформа привязку к краю игнорирует).
    Спецификация — p["bind"]: {сторона элемента: "form" | "Имя" | "Имя.сторона" | {"to": "form"|"Имя", "side": …, "offset": N}}.
    Сторона цели по умолчанию: у формы та же, у соседа противоположная (верх → низ соседа, лево → право соседа).
    Отступ по умолчанию считается из координат. anchor: True = все четыре стороны к форме. Ось без заданных сторон —
    свободное начало и размер, привязанный к самому элементу (как у элемента, брошенного на форму)."""
    ids = {p["name"].lower(): first_id + k for k, p in enumerate(plan)}
    rects = {first_id + k: (p["l"], p["t"], p["r"], p["b"]) for k, p in enumerate(plan)}
    edge = lambda r, i: (r[1], r[3], r[0], r[2])[i]  # noqa: E731
    form_edge = (0, form_h, 0, form_w)
    form_deps: list[list[tuple[int, int]]] = [[] for _ in range(4)]
    for k, p in enumerate(plan):
        p["id"], p["deps"] = first_id + k, [[] for _ in range(4)]
    for p in plan:
        cid, who = p["id"], f"Элемент «{p['name']}»"
        spec = {_side_of(s, who): v for s, v in (p.get("bind") or {}).items()}
        if p.get("anchor"):
            spec = {i: "form" for i in range(4)}
        own = rects[cid]
        pairs: list[tuple[int, int, int] | None] = [None] * 4
        for k, v in spec.items():
            if isinstance(v, str):
                v = {"to": v.split(".")[0], "side": v.split(".")[1]} if "." in v else {"to": v}
            if not isinstance(v, dict) or not v.get("to"):
                raise BuildError(f"{who}: привязка стороны «{_SIDE_NAMES[k]}» — строка или {{to, side, offset}}")
            to = str(v["to"])
            if to.lower() in ("form", "форма"):
                obj, s = 0, _side_of(v["side"], who) if v.get("side") is not None else k
                target = form_edge[s]
            else:
                if to.lower() not in ids:
                    raise BuildError(f"{who}: привязка к «{to}» — такого элемента нет среди controls")
                obj = ids[to.lower()]
                s = _side_of(v["side"], who) if v.get("side") is not None else k ^ 1
                target = edge(rects[obj], s)
                if obj == cid and s == k:
                    raise BuildError(f"{who}: сторона «{_SIDE_NAMES[k]}» привязана сама к себе")
            if k // 2 != s // 2:
                raise BuildError(f"{who}: сторону «{_SIDE_NAMES[k]}» нельзя привязать к стороне «{_SIDE_NAMES[s]}» "
                                 f"(вертикальные — к вертикальным, горизонтальные — к горизонтальным)")
            off = int(v["offset"]) if v.get("offset") is not None else edge(own, k) - target
            pairs[k] = (obj, s, off)
        for lo, hi, size in ((0, 1, own[3] - own[1]), (2, 3, own[2] - own[0])):
            if pairs[lo] is None and pairs[hi] is None:
                pairs[hi] = (cid, lo, size)
        p["pairs"] = pairs
        for k, b in enumerate(pairs):
            if b is None:
                continue
            if b[0] == 0:
                form_deps[b[1]].append((cid, k))
            else:
                plan[b[0] - first_id]["deps"][b[1]].append((cid, k))
    return form_deps


def _registry_text(lists: list[list[tuple[int, int]]]) -> str:
    """Реестр формы: по 6 сторонам счётчик и ссылки {0,элемент,сторона} (то же, что списки привязанных в раскладке элемента)."""
    return "".join(f"{len(x)}," + "".join(f"{_NL}{{0,{e},{s}}}," for e, s in x) for x in lists)


def _plan_controls(controls: list[dict[str, Any]], top0: int, width: int,
                   taken: set[str] | None = None) -> tuple[list[dict[str, Any]], int, int]:
    """Тип, имя, координаты каждого элемента; без top — друг под другом с top0. Возвращает (план, низ, правый край)."""
    plan, seen, cursor, right_max = [], {x.lower() for x in taken or ()}, top0, 0
    for i, c in enumerate(controls):
        typ = _ctl_type(str(c.get("type", "")))
        name = _ident(c.get("name") or f"{typ}{i + 1}", "Имя элемента")
        if name.lower() in seen:
            raise BuildError(f"Элемент «{name}» указан дважды или совпадает с элементом формы (поле реквизита, надпись, таблица ТЧ)")
        seen.add(name.lower())
        dw, dh = _CTL_SIZE.get(typ, (None, _CTL_DEFAULT_H))
        left = int(c.get("left", _LABEL_LEFT))
        w = int(c.get("width") or dw or (width - 20 - left))
        h = int(c.get("height") or dh)
        top = int(c["top"]) if c.get("top") is not None else cursor
        if c.get("top") is None:
            cursor = top + h + 8
        plan.append({**c, "type": typ, "name": name, "l": left, "t": top, "r": left + w, "b": top + h})
        right_max = max(right_max, left + w)
    bottom = max([p["b"] for p in plan], default=top0)
    return plan, bottom, right_max


def _control_elements(plan: list[dict[str, Any]], first_id: int, form_module: str) -> list[str]:
    """Элементы палитры; _bind_controls(plan, …) вызывается до этого (p["pairs"], p["deps"])."""
    els = []
    for k, p in enumerate(plan):
        cid = first_id + k
        text = (_tpl(f"ctl/{p['type']}.txt").replace("@@ID@@", str(cid)).replace("@@NAME@@", _q(p["name"]))
                .replace("@@TITLE@@", _q(p.get("title") or p["name"])).replace("@@PATTERN@@", _pattern(p.get("value_type", "")))
                .replace("@@LAYOUT@@", _ctl_layout(cid, (p["l"], p["t"], p["r"], p["b"]), p["pairs"], p["deps"], cid - 1,
                                                    p.get("tab_order"))))
        evs = control_events_of(form_module, p["name"], p["type"])
        text = text.replace("@@EVENTS@@", _ctl_events_text(p["type"], evs))
        els.append(text)
    return els


_BARE_W, _BARE_H, _BARE_PAD = 882, 560, 8  # размер окна и отступ — как у формы без панелей, снятой с обработки владельца


def _form_bare(title: str, type_id: str, events: list[str], form_module: str,
               controls: list[dict[str, Any]], width: int | None, height: int | None) -> str:
    """
    Форма без верхней командной панели и без нижней «Выполнить/Закрыть»: только элементы палитры.
    Структура — по форме без панелей из рабочей обработки: список элементов без двух панелей, порядок обхода и «первый элемент»
    ссылаются на сам элемент, панель страниц тянется по краям окна. Элемент с `fill: True` занимает всё окно с отступом
    8 и привязан правой/нижней границей к форме (растягивается вместе с окном); без `fill` — left/top/width/height как есть.
    """
    form_w, form_h = int(width or _BARE_W), int(height or _BARE_H)
    nl, pad = _NL, _BARE_PAD
    text = _with_form_events(_tpl("form_default.txt").replace("@@TITLE@@", title).replace("@@TYPE_ID@@", type_id), events)
    plan = []
    for i, c in enumerate(controls):
        typ = _ctl_type(str(c.get("type", "")))
        name = _ident(c.get("name") or f"{typ}{i + 1}", "Имя элемента")
        if c.get("fill"):
            l, t, r, b = pad, pad, form_w - pad, form_h - pad
        else:
            l, t = int(c.get("left", pad)), int(c.get("top", pad))
            r, b = l + int(c.get("width") or form_w - 2 * pad), t + int(c.get("height") or _CTL_DEFAULT_H)
        if c.get("fill"):  # во всё окно: низ и правый край — к форме (верх и лево свободны)
            c = {**c, "bind": {**(c.get("bind") or {}), "bottom": "form", "right": "form"}}
        plan.append({**c, "type": typ, "name": name, "l": l, "t": t, "r": r, "b": b})
    if not plan:
        raise BuildError("Форма без панелей: нужен хотя бы один элемент (controls)")
    first = 4
    form_deps = _bind_controls(plan, first, form_w, form_h)
    els = []
    for k, p in enumerate(plan):
        cid = first + k
        layout = _ctl_layout(cid, (p["l"], p["t"], p["r"], p["b"]), p["pairs"], p["deps"], cid - 2, tab=p.get("tab_order"))
        tpl = (_tpl(f"ctl/{p['type']}.txt").replace("@@ID@@", str(cid)).replace("@@NAME@@", _q(p["name"]))
               .replace("@@TITLE@@", _q(p.get("title") or p["name"])).replace("@@PATTERN@@", _pattern(p.get("value_type", "")))
               .replace("@@LAYOUT@@", layout))
        evs = control_events_of(form_module, p["name"], p["type"])
        els.append(tpl.replace("@@EVENTS@@", _ctl_events_text(p["type"], evs)))

    def swap(old: str, new: str) -> None:
        nonlocal text
        if text.count(old) != 1:
            raise BuildError(f"Шаблон формы изменился: «{old[:30]}» встречается {text.count(old)} раз")
        text = text.replace(old, new)

    swap("},3,3},", "},%d,4294967295}," % (3 + len(plan)))
    swap(_registry_text(_REG_BASE), _registry_text(form_deps + [[], []]))  # панелей нет: реестр — только привязки элементов к форме
    swap("{2,33,0,1,2,0,0,0,0}", "{2,%d,0,1,2,0,0,0,0}" % pad)
    swap("{2,259,0,1,4,0,0,33,0}", "{2,%d,0,1,4,0,0,%d,0}" % (form_h - pad, pad))
    swap("{2,392,1,1,3,0,0,8,0}", "{2,%d,1,1,3,0,0,%d,0}" % (form_w - pad, pad))
    swap("},400,292,1,0,1,4,4,39,400,292,96}", "},%d,%d,1,0,1,4,4,36,%d,%d,96}" % (form_w, form_h, form_w, form_h))
    start = text.index(f"{nl}{{2,{nl}{{e69bf21d-97b2-4f37-86db-675aea9ec2cb,2,")
    anchor = f'{{14,"ОсновныеДействияФормы",0,0,0,0}},{nl}{{0}}{nl}}}'
    end = text.index(anchor) + len(anchor)
    return text[:start] + f"{nl}{{{len(plan)},{nl}" + f",{nl}".join(els) + text[end:]


def _form_with_fields(title: str, type_id: str, fields: list[dict[str, str]], tables: list[dict[str, Any]] | None = None,
                      events: list[str] | None = None, form_module: str = "", controls: list[dict[str, Any]] | None = None,
                      width: int | None = None, height_min: int | None = None, panels: bool = True) -> str:
    """
    Форма по умолчанию + на каждый реквизит надпись и поле ввода, привязанное к реквизиту объекта по его GUID,
    + на каждую ТЧ надпись и ТабличноеПоле. Идентификаторы: поле 4+2i, надпись 5+2i, далее пары надпись/таблица.
    Привязка — таблица форм-реквизитов {id элемента, путь: ОбработкаОбъект → реквизит/ТЧ}.
    events — события формы (ПриОткрытии…), назначаемые одноимённым процедурам модуля;
    события полей — по процедурам <ИмяПоля><Событие> из form_module (ПодразделениеПриИзменении).
    """
    if not panels:
        return _form_bare(title, type_id, events or [], form_module, controls or [], width, height_min)
    text = _with_form_events(_tpl("form_default.txt").replace("@@TITLE@@", title).replace("@@TYPE_ID@@", type_id), events or [])
    tables = tables or []
    n, m = len(fields), len(tables)
    top0 = _FIELD_TOP + n * _FIELD_STEP + (8 if n else 0)
    # верхняя командная панель формы занимает y < _FIELD_TOP: явный top элемента выше — наложение на панель
    tops = [int(c["top"]) for c in controls or [] if c.get("top") is not None]
    if tops and min(tops) < _FIELD_TOP:
        shift = _FIELD_TOP - min(tops)
        controls = [{**c, "top": int(c["top"]) + shift} if c.get("top") is not None else c for c in controls]
    # ширина формы: заданная или по самому правому элементу (не меньше 400)
    _, _, right_max = _plan_controls(controls or [], top0, int(width or _FORM_W))
    form_w = max(_FORM_W, int(width or 0), right_max + 20)
    if not n and not m and not controls:
        if width or height_min:  # форма без элементов, но с заданным размером окна
            h = max(292, int(height_min or 0))
            for a, b in (("{2,259,0,1,4,0,0,33,0}", "{2,%d,0,1,4,0,0,33,0}" % (h - 33)),
                         ("{8,0,267,400,292,1,", "{8,0,%d,%d,%d,1," % (h - 25, form_w, h)),
                         ("},400,292,1,0,1,4,4,39,400,292,96}", "},%d,%d,1,0,1,4,4,39,%d,%d,96}" % (form_w, h, form_w, h)),
                         ("{2,392,1,1,3,0,0,8,0}", "{2,%d,1,1,3,0,0,8,0}" % (form_w - 8)),
                         ("{8,0,0,400,25,1,", "{8,0,0,%d,25,1," % form_w)):
                if text.count(a) != 1:
                    raise BuildError(f"Шаблон формы изменился: «{a[:30]}» встречается {text.count(a)} раз")
                text = text.replace(a, b)
        return text
    tbl_els, bottom = _table_elements(tables, 4 + 2 * n, top0, form_w)
    taken = ({"КоманднаяПанель1", "ОсновныеДействияФормы"} | {x["name"] for x in fields + tables}
             | {"Надпись" + x["name"] for x in fields + tables})
    plan, ctl_bottom, _ = _plan_controls(controls or [], bottom, form_w, taken)  # элементы — под полями и таблицами
    bottom = max(bottom, ctl_bottom)
    k = len(plan)
    height = max(292, bottom + 35, int(height_min or 0))
    last_id, children = 3 + 2 * n + 2 * m + k, 2 + 2 * n + 2 * m + k
    tbl_ids = [5 + 2 * n + 2 * j for j in range(m)]

    def swap(old: str, new: str) -> None:
        nonlocal text
        if text.count(old) != 1:
            raise BuildError(f"Шаблон формы изменился: «{old[:30]}» встречается {text.count(old)} раз")
        text = text.replace(old, new)

    nl = _NL
    swap("},3,3},", "},%d,3}," % last_id)
    # реестр формы: поля и таблицы привязаны правым краем, элементы палитры — как в их bind/anchor
    form_deps = _bind_controls(plan, 4 + 2 * n + 2 * m, form_w, height)
    reg = [list(x) for x in _REG_BASE]
    reg[3] += [(i, 3) for i in [4 + 2 * k for k in range(n)] + tbl_ids]
    for s in range(4):
        reg[s] += form_deps[s]
    swap(_registry_text(_REG_BASE), _registry_text(reg))
    swap("{2,259,0,1,4,0,0,33,0}", "{2,%d,0,1,4,0,0,33,0}" % (height - 33))
    swap("{8,0,267,400,292,1,", "{8,0,%d,%d,%d,1," % (height - 25, form_w, height))
    swap("},400,292,1,0,1,4,4,39,400,292,96}", "},%d,%d,1,0,1,4,4,39,%d,%d,96}" % (form_w, height, form_w, height))
    if form_w != _FORM_W:  # панель страниц и верхняя командная панель — во всю ширину
        swap("{2,392,1,1,3,0,0,8,0}", "{2,%d,1,1,3,0,0,8,0}" % (form_w - 8))
        swap("{8,0,0,400,25,1,", "{8,0,0,%d,25,1," % form_w)
    swap(f"{nl}{{2,{nl}{{e69bf21d-97b2-4f37-86db-675aea9ec2cb,2,", f"{nl}{{{children},{nl}{{e69bf21d-97b2-4f37-86db-675aea9ec2cb,2,")

    els = []
    for i, f in enumerate(fields):
        top = _FIELD_TOP + i * _FIELD_STEP
        els.append(
            _tpl("field_input.txt")
            .replace("@@ID@@", str(4 + 2 * i)).replace("@@PATTERN@@", f["pattern"]).replace("@@CAPTION@@", "")
            .replace("@@L@@", str(_FIELD_LEFT)).replace("@@T@@", str(top)).replace("@@R@@", str(form_w - 20))
            .replace("@@B@@", str(top + _FIELD_H)).replace("@@AT@@", str(top - _PAGE_TOP)).replace("@@H@@", str(_FIELD_H))
            .replace("@@AR@@", "-12").replace("@@ORD@@", str(2 + 2 * i)).replace("@@NAME@@", _q(f["name"]))
            .replace(f"}},{nl}{{0}},0,1,0,{nl}{{1,0}},0}},",
                     f"}},{nl}{_event_list([_event_item(*e) for e in field_events_of(form_module, f['name'])])},0,1,0,{nl}{{1,0}},0}},")
        )
        els.append(
            _tpl("field_label.txt")
            .replace("@@ID@@", str(5 + 2 * i)).replace("@@TEXT@@", _q(f["caption"]) + ":")
            .replace("@@L@@", str(_LABEL_LEFT)).replace("@@T@@", str(top)).replace("@@R@@", str(_LABEL_RIGHT))
            .replace("@@B@@", str(top + _LABEL_H)).replace("@@AT@@", str(top - _PAGE_TOP)).replace("@@H@@", str(_LABEL_H))
            .replace("@@W@@", str(_LABEL_RIGHT - _LABEL_LEFT)).replace("@@O1@@", str(3 + 2 * i)).replace("@@O2@@", str(2 + 2 * i))
            .replace("@@NAME@@", "Надпись" + _q(f["name"]))
        )
    anchor = f'{{14,"ОсновныеДействияФормы",0,0,0,0}},{nl}{{0}}{nl}}}'
    swap(anchor, anchor + f",{nl}" + f",{nl}".join(els + tbl_els + _control_elements(plan, 4 + 2 * n + 2 * m, form_module)))
    if not n and not m:
        return text
    pairs = [(4 + 2 * i, f["attr_id"]) for i, f in enumerate(fields)] + [(tid, t["id"]) for tid, t in zip(tbl_ids, tables)]
    binds = f",{nl}".join(f"{{{eid},{nl}{{2,{nl}{{1}},{nl}{{0,{pid}}}{nl}}}{nl}}}" for eid, pid in pairs)
    swap(f"{{{nl}{{1}},2,{nl}{{1,", f"{{{nl}{{1}},{2 + n + m},{nl}{{1,")
    tail = f'"ОбработкаОбъект",{nl}{{"Pattern",{nl}{{"#",{type_id}}}{nl}}}{nl}}}{nl}}},{nl}{{0}}'
    swap(tail, tail[: -len("{0}")] + f"{{{n + m},{nl}{binds}{nl}}}")
    return text


# ------------------------------------------------------------------ макеты
def _templates(specs: list[dict[str, Any]], busy: set[str]) -> list[dict[str, Any]]:
    """Макеты табличного документа (tools/mxl_build.py): id, метаданные узла и бинарный блок MOXCEL."""
    from tools.mxl_build import MxlError, build_moxcel

    out, seen = [], set(busy)
    for t in specs:
        name = _ident(t.get("name", ""), "Имя макета")
        if name.lower() in seen:
            raise BuildError(f"Макет «{name}» совпадает с именем другого макета или формы")
        seen.add(name.lower())
        try:
            blob = build_moxcel(t)
        except MxlError as e:
            raise BuildError(f"Макет «{name}»: {e}") from e
        tid = _guid()
        syn = _q(t.get("synonym") or name)
        meta = ('{1,\r\n{2,0,\r\n{3,\r\n{1,0,%s},"%s",\r\n{1,"ru","%s"},"%s",0,0,%s,0}\r\n},0}'
                % (tid, name, syn, _q(t.get("comment") or ""), _GUID0))
        out.append({"id": tid, "name": name, "meta": meta, "blob": blob})
    return out


# ------------------------------------------------------------------ сборка
def default_form_module(object_module: str) -> str:
    """Модуль формы по умолчанию: кнопка «Выполнить» вызывает ВыполнитьОбработку() модуля объекта."""
    if re.search(rf"(?i)процедура\s+{_ENTRY_PROC}\s*\(", object_module or ""):
        call = f"{_ENTRY_PROC}();"
    else:
        call = f'Сообщить("В модуле объекта нет процедуры {_ENTRY_PROC}()");'
    return f"\r\nПроцедура {_FORM_HANDLER}(Кнопка)\r\n\t{call}\r\nКонецПроцедуры\r\n"


def _copyinfo(forms: list[tuple[str, str]], obj_id: str, obj_name: str, type_id: str, bound: list[tuple[str, str]],
              tables: list[dict[str, Any]] | None = None, refs: dict[str, dict[str, str]] | None = None) -> str:
    """
    copyinfo: объекты метаданных (по возрастанию id), типы и пути привязанных к форме реквизитов и ТЧ.
    Ссылочные типы CF: объект {uuid,uuid,1,{класс,"Имя"}} + тип {GUID …Ref, uuid объекта, 1} — по ним платформа
    сопоставляет тип с объектом базы, в которой открывают обработку.
    """
    nl = "\n"
    tables, refs = tables or [], refs or {}
    own = [(obj_id, "c3831ec8-d8d5-4f93-8a22-f9bfae07327f", obj_name)]
    own += [(fid, "d5b0e5ed-256d-401c-9c36-f630cafd8a62", fname) for fid, fname in forms]
    ents = [(i, f'1,{nl}{{{c},"{_q(n)}"}}') for i, c, n in own]
    ents += [(r["obj_id"], f'1,{nl}{{{r["cls"]},"{_q(r["name"])}"}}') for r in refs.values()]
    ents += [(aid, f'1,{nl}{{ec6bb5e5-b7a8-4d75-bec9-658107a699cf,"{_q(an)}"}}') for aid, an in bound]
    for t in tables:
        ents.append((t["id"], f'1,{nl}{{2bcef0d1-0981-11d6-b9b8-0050bae0a95d,"{_q(t["name"])}"}}'))
        for c in t["cols"]:
            ents.append((c["id"], f'2,{nl}{{2bcef0d1-0981-11d6-b9b8-0050bae0a95d,"{_q(t["name"])}"}},{nl}'
                                  f'{{5d24a9d1-098e-11d6-b9b8-0050bae0a95d,"{_q(c["name"])}"}}'))
    objs = f",{nl}".join(f"{{{i},{i},{body}{nl}}}" for i, body in sorted(ents))
    types = sorted([(type_id, obj_id, 0)] + [(t["type"], t["id"], 0) for t in tables]
                   + [(r["type_id"], r["obj_id"], 1) for r in refs.values()])
    types_blk = f",{nl}".join(f"{{{a},{b},{f}}}" for a, b, f in types)
    pids = sorted([aid for aid, _ in bound] + [t["id"] for t in tables])
    paths = f",{nl}".join(f"{{{nl}{{0,{p}}},{type_id},{nl}{{-1}}{nl}}}" for p in pids)
    paths_blk = f"{{{len(pids)},{nl}{paths}{nl}}}" if pids else "{0}"
    return (f"{{4,{nl}{{{len(ents)},{nl}{objs}{nl}}},{nl}{{{len(types)},{nl}{types_blk}{nl}}},{nl}"
            f"{paths_blk},{nl}{{0,0}},{nl}{{0}}{nl}}}")


def build_epf(spec: dict[str, Any], *, cf_root: str | Path | None = None) -> bytes:
    """
    spec: name, synonym, comment, module (текст модуля объекта), attributes[{name,synonym,type}],
    tabular_sections[{name,synonym,columns[{name,synonym,type}]}], form (bool, по умолчанию True),
    form_title, form_module (по умолчанию — вызов ВыполнитьОбработку()),
    extra_forms[{name, title, module, controls, width, height}] — доп. формы без полей реквизитов (основная остаётся «Форма»).
    templates[{name, synonym, columns, styles, rows, areas}] — макеты табличного документа (tools/mxl_build.py), в коде —
    ПолучитьМакет("<name>").ПолучитьОбласть("<область>").
    controls[{type, name, title, left, top, width, height, value_type}] — элементы палитры на основной форме
    (под полями и таблицами; без top — друг под другом), form_width — ширина основной формы.
    Ссылочные типы («СправочникСсылка.Номенклатура») берутся из выгрузки CF: cf_root или зеркало Qdrant.
    """
    token = _REF_CTX.set({"root": cf_root, "refs": {}})
    try:
        return _build(spec)
    finally:
        _REF_CTX.reset(token)


def _build(spec: dict[str, Any]) -> bytes:
    name = _ident(spec.get("name", ""), "Имя обработки")
    synonym = (spec.get("synonym") or name).strip()
    module = spec.get("module") or ""
    with_form = bool(spec.get("form", True))
    root_id, obj_id, type_id, value_id = _guid(), _guid(), _guid(), _guid()
    form_id = _guid() if with_form else ""
    form_name = "Форма"

    attr_ids = [_guid() for _ in spec.get("attributes") or []]
    attrs = [_attr_node(a.get("name", ""), a.get("synonym", ""), a.get("type", ""), i)
             for a, i in zip(spec.get("attributes") or [], attr_ids)]
    fields = [
        {"name": a["name"], "caption": a.get("synonym") or a["name"], "pattern": _pattern(a.get("type", "")), "attr_id": i}
        for a, i in zip(spec.get("attributes") or [], attr_ids)
        if spec.get("fields", True)
    ]
    seen: set[str] = set()
    for a in spec.get("attributes") or []:
        key = str(a.get("name", "")).lower()
        if key in seen:
            raise BuildError(f"Реквизит «{a.get('name')}» указан дважды")
        seen.add(key)
    ts_pairs = [_ts_node(t) for t in spec.get("tabular_sections") or []]
    tss = [node for node, _ in ts_pairs]
    tables = [info for _, info in ts_pairs] if spec.get("fields", True) else []

    # доп. формы (без полей ввода): открываются из кода через ПолучитьФорму("<Имя>")
    extra: list[dict[str, Any]] = []
    for ef in spec.get("extra_forms") or [] if with_form else []:
        ef_name = _ident(ef.get("name", ""), "Имя формы")
        if ef_name.lower() in {form_name.lower()} | {e["name"].lower() for e in extra}:
            raise BuildError(f"Форма «{ef_name}» указана дважды")
        extra.append({"id": _guid(), "name": ef_name, "title": _q(ef.get("title") or ef_name),
                      "module": ef.get("module") or "", "controls": ef.get("controls") or [], "width": ef.get("width"), "height": ef.get("height"),
                      "panels": ef.get("panels", True)})
    form_ids = ([form_id] if with_form else []) + [e["id"] for e in extra]
    tpls = _templates(spec.get("templates") or [], {form_name.lower()} | {e["name"].lower() for e in extra})
    forms_coll = "{d5b0e5ed-256d-401c-9c36-f630cafd8a62,%d%s}" % (len(form_ids), "".join("," + i for i in form_ids))
    meta = (
        "{1,\r\n{%s},1,\r\n{c3831ec8-d8d5-4f93-8a22-f9bfae07327f,\r\n{1,\r\n{4,%s,%s,\r\n{0,\r\n{3,\r\n"
        '{1,0,%s},"%s",\r\n{1,"ru","%s"},"%s",0,0,%s,0}\r\n},%s,"",%s},4,\r\n%s,\r\n%s,\r\n%s,\r\n%s\r\n}\r\n}\r\n}'
        % (
            root_id, type_id, value_id, obj_id, _q(name), _q(synonym), _q(spec.get("comment") or ""), _GUID0,
            form_id or _GUID0, _GUID0,
            _collection("2bcef0d1-0981-11d6-b9b8-0050bae0a95d", tss),
            "{3daea016-69b7-4ed4-9453-127911372fe6,%d%s}" % (len(tpls), "".join("," + t["id"] for t in tpls)),
            forms_coll,
            _collection("ec6bb5e5-b7a8-4d75-bec9-658107a699cf", attrs),
        )
    )

    files: dict[str, bytes] = {}
    files[root_id] = _deflate(_text(meta))
    for t in tpls:
        files[t["id"]] = _deflate(_text(t["meta"]))
        files[f'{t["id"]}.0'] = _deflate(t["blob"])
    if module.strip():
        obj_mod = _pack_container([("info", _text('{3,1,0,"",0}')), ("text", _text(module))])
        files[f"{obj_id}.0"] = _deflate(obj_mod)
    def put_form(fid: str, fname: str, ftext: str, fmod: str) -> None:
        files[fid] = _deflate(_text(
            '{1,\r\n{1,\r\n{0,\r\n{13,\r\n{3,\r\n{1,0,%s},"%s",\r\n{1,"ru","%s"},"",0,0,%s,0},0,0,\r\n{2,\r\n'
            '{"#",1708fdaa-cbce-4289-b373-07a5a74bee91,1},\r\n{"#",1708fdaa-cbce-4289-b373-07a5a74bee91,2}\r\n}\r\n},'
            "\r\n{0}\r\n}\r\n},0}" % (fid, _q(fname), _q(fname), _GUID0)
        ))
        files[f"{fid}.0"] = _deflate(_pack_container([("form", _text(ftext)), ("module", _text(fmod))]))

    if with_form:
        title = _q(spec.get("form_title") or synonym)
        fmod = spec.get("form_module") or default_form_module(module)
        put_form(form_id, form_name, _form_with_fields(title, type_id, fields, tables, form_events_of(fmod), fmod,
                                                       spec.get("controls") or [], spec.get("form_width")), fmod)
    for e in extra:
        fmod = e["module"] or default_form_module(module)
        put_form(e["id"], e["name"], _form_with_fields(e["title"], type_id, [], events=form_events_of(fmod), form_module=fmod,
                                                       controls=e["controls"], width=e["width"], height_min=e["height"],
                                                       panels=e["panels"]), fmod)
    refs = (_REF_CTX.get() or {}).get("refs") or {}
    if with_form or refs:
        forms = ([(form_id, form_name)] if with_form else []) + [(e["id"], e["name"]) for e in extra]
        copyinfo = _copyinfo(forms, obj_id, name, type_id,
                             [(f["attr_id"], f["name"]) for f in fields] if with_form else [], tables if with_form else [], refs)
    else:
        copyinfo = "{4,\r\n{0},\r\n{0},\r\n{0},\r\n{0,0},\r\n{0}\r\n}"
    files["copyinfo"] = _deflate(_text(copyinfo))
    files["root"] = _deflate(_text("{2,%s,}" % root_id))
    files["version"] = _deflate(_text("{\r\n{216,0,\r\n{80214,0}\r\n}\r\n}"))
    order = sorted(files)
    pairs = ",".join(f'"{n}",{_guid()}' for n in [""] + order + ["versions"])
    files["versions"] = _deflate(_text("{1,%d,%s}" % (len(order) + 2, pairs)))
    names = sorted(files)
    return _pack_container([(n, files[n]) for n in names])


# ------------------------------------------------------------------ запись и проверка
def _platform_exe() -> Path | None:
    for base in (Path(r"C:\Program Files\1cv8"), Path(r"C:\Program Files (x86)\1cv8")):
        if base.is_dir():
            cands = sorted(base.glob("8.*/bin/1cv8.exe"), reverse=True)
            if cands:
                return cands[0]
    return None


def verify_with_platform(epf: Path, timeout: int = 120) -> dict[str, Any]:
    """Выгрузка файла Конфигуратором в XML: платформа сама подтверждает, что контейнер и метаданные валидны."""
    exe = _platform_exe()
    if exe is None:
        return {"checked": False, "reason": "платформа 1С не найдена"}
    with tempfile.TemporaryDirectory(prefix="epf_verify_") as tmp:
        ib, out, log = Path(tmp) / "ib", Path(tmp) / "xml", Path(tmp) / "log.txt"
        try:
            subprocess.run([str(exe), "CREATEINFOBASE", f"File={ib}"], timeout=timeout, capture_output=True)
            subprocess.run(
                [str(exe), "DESIGNER", "/F", str(ib), "/DisableStartupDialogs", "/DumpExternalDataProcessorOrReportToFiles",
                 str(out / "root.xml"), str(epf), "/Out", str(log)],
                timeout=timeout, capture_output=True,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return {"checked": False, "reason": str(e)}
        text = log.read_text("utf-8-sig", errors="replace") if log.is_file() else ""
        ok = "Выгрузка завершена" in text and (out / "root.xml").is_file()
        return {"checked": True, "ok": ok, "platform": exe.parent.parent.name, "log": text.strip()[-400:]}


def verify_epf(path: Path, *, platform: bool = True) -> dict[str, Any]:
    """Свой разбор (имя, модули, реквизиты) + выгрузка платформой, если она установлена."""
    from tools import epf_inspect as E

    parsed = E.parse_external_file(str(path))
    res: dict[str, Any] = {"parsed_ok": bool(parsed.get("ok"))}
    if parsed.get("ok"):
        st = E.external_structure(parsed)
        res["name"] = st.get("name")
        res["attributes"] = [a["name"] for a in st.get("attributes", [])]
        res["tabular_sections"] = [t["name"] for t in st.get("tabular_sections", [])]
        res["types"] = {a["name"]: a.get("type") for a in st.get("attributes", [])}
    else:
        res["error"] = parsed.get("error")
    if platform:
        res["platform"] = verify_with_platform(path)
    return res


_STR_RE = re.compile(r'"(?:[^"\n]|"")*"?')
_PAIRS = (
    (r"\b(?:процедура|procedure)\b", r"\b(?:конецпроцедуры|endprocedure)\b", "Процедура/КонецПроцедуры"),
    (r"\b(?:функция|function)\b", r"\b(?:конецфункции|endfunction)\b", "Функция/КонецФункции"),
    (r"\b(?:если|if)\b", r"\b(?:конецесли|endif)\b", "Если/КонецЕсли"),
    (r"\b(?:цикл|do)\b", r"\b(?:конеццикла|enddo)\b", "Цикл/КонецЦикла"),
    (r"\b(?:попытка|try)\b", r"\b(?:конецпопытки|endtry)\b", "Попытка/КонецПопытки"),
)


def lint_bsl(text: str) -> list[str]:
    """Грубая проверка парности ключевых слов (литералы и комментарии отброшены). Это не компиляция."""
    lines: list[str] = []
    for ln in (text or "").splitlines():
        s = ln.lstrip()
        if s.startswith(("//", "#")):
            continue
        s = _STR_RE.sub('""', s.lstrip("|"))
        lines.append(s.split("//", 1)[0])
    body = "\n".join(lines).lower().replace("ё", "е")
    out: list[str] = []
    for op, cl, label in _PAIRS:
        a, b = len(re.findall(op, body)), len(re.findall(cl, body))
        if a != b:
            out.append(f"{label}: открывающих {a}, закрывающих {b}")
    return out


_MODAL_RE = re.compile(r"(?im)^[^/\n]*\b(Предупреждение|Вопрос|ВвестиЗначение|ВвестиЧисло|ВвестиСтроку|ВвестиДату|ОткрытьМодально)\s*\(")


def lint_ordinary_form(text: str, where: str) -> list[str]:
    """Грабли обычных форм из проверки в живом клиенте: модальные окна блокируют интерфейс (формы открывают из кода)."""
    return [f"{where}: модальное окно {m.group(1)}() блокирует интерфейс — используй Сообщить()"
            for m in _MODAL_RE.finditer(text or "")]


def write_epf(spec: dict[str, Any], out_dir: Path, *, platform_check: bool = True,
              cf_root: str | Path | None = None) -> dict[str, Any]:
    """Собрать, записать в out_dir/<Имя>.epf и проверить."""
    data = build_epf(spec, cf_root=cf_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_ident(spec.get('name', ''), 'Имя обработки')}.epf"
    path.write_bytes(data)
    return {"path": path, "size": len(data), "verify": verify_epf(path, platform=platform_check)}
