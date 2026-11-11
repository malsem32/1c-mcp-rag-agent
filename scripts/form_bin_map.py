# -*- coding: utf-8 -*-
"""Карта свойств обычных форм (Form.bin) по эталонной .epf из Конфигуратора.

Эталон: в обычной форме обработки элементы-копии одного эталона, имя = что изменено:
  <Префикс>_Эталон                      — ничего не менять
  <Префикс>_<Свойство>                  — изменено одно свойство (флажок, число, строка)
  <Префикс>_<Свойство>_<Значение>       — перечисление: выбрано <Значение>
Свойства самой формы — отдельные формы обработки: Форма_Эталон, Форма_<Свойство>[_<Значение>].

  python scripts/form_bin_map.py Эталон.epf [ещё.epf …] [--validate D:/conf_test] [--dry-run]

Каждая копия сравнивается с эталоном; координаты/привязки/имя/id — шум и отбрасываются. Найденное место
свойства пишется в tools/data/ordinary_form_props.json (мерж по свойствам). --validate прогоняет
карту по Form.bin выгрузки: у булевых свойств должны быть только 0/1 и умолчание у большинства — иначе
свойство помечается unreliable и в разбор не попадает.
"""

from __future__ import annotations

import argparse
import collections
import difflib
import itertools
import json
import random
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import brace  # noqa: E402
from tools.epf_inspect import _FORM_HDR, _walk_blob  # noqa: E402
from tools.ordinary_form import ELEMENT_TYPES, FORM_KEY, MAP_PATH, _name, element_type  # noqa: E402


# у элемента: [guid, id, {свойства}, {8, раскладка…}, {14,"Имя",…}, …]; из раскладки свойство — только [5] (Видимость)
_NOISE = re.compile(r"^\(1,\)|^\(4, 1\)|^\(3, (?!5\))")  # id, имя элемента, раскладка (кроме Видимости); блок {14,…} — свойства
GENERIC = {"ИзменяетДанные", "ПропускатьПриВводе", "ПервыйВГруппе"}  # {14,"Имя",<источник>,<Изменяет>,<Пропускать>,<Первый>}: общие для всех типов
GENERIC_KEY = "_Элемент"
# имена свойств палитры (имя копии пишут как удобно: «горизонтальноеположение» → ГоризонтальноеПоложение)
CANON = (
    "Видимость Доступность ТолькоПросмотр Заголовок Подсказка ПоУмолчанию ИзменяетДанные ПропускатьПриВводе ПервыйВГруппе "
    "КонтекстноеМеню АвтоКонтекстноеМеню СочетаниеКлавиш РежимМеню ГоризонтальноеПоложение ВертикальноеПоложение "
    "МногострочныйРежим ЦветФона ЦветТекста ЦветФонаКнопки ЦветТекстаКнопки ЦветФонаПоля ЦветТекстаПоля ЦветРамки Рамка Шрифт "
    "ПоложениеКартинки Картинка РазмерКартинки ИсточникДействий АктивизироватьПоУмолчанию ПрозрачныйФон Маска РежимПароля "
    "РасширенноеРедактирование КнопкаСпискаВыбора КнопкаВыбора КнопкаОчистки КнопкаРегулирования КнопкаОткрытия "
    "АвтоОтметкаНезаполненного АвтоВыборНезаполненного РежимВыбораИзСписка СвязьПоТипу ВыбиратьТип РежимВыбораНезаполненного "
    "РедактированиеТекста АвтоПереносСтрок ВыделятьОтрицательные ВысотаСпискаВыбора ШиринаСпискаВыбора ПоложениеЗаголовка "
    "ИзменятьСоставСтрок РазрешитьНачалоПеретаскивания РазрешитьПеретаскивание ИзменятьНастройкуКолонок "
    "ИзменятьПозициюКолонок Вывод ИзменятьПорядокСтрок РежимВводаСтрок РежимВыделения РежимВыделенияСтроки Шапка Подвал "
    "ВысотаШапки ВысотаПодвала ГоризонтальнаяПолосаПрокрутки ВертикальнаяПолосаПрокрутки ФиксацияСлева ФиксацияСправа "
    "АвтоВводНовойСтроки НачальноеОтображениеСписка ГоризонтальныеЛинии ВертикальныеЛинии ЦветФонаШапки ЦветТекстаШапки "
    "ЦветФонаПодвала ЦветТекстаПодвала ЦветФонаВыделения ЦветТекстаВыделения ЦветФонаЧередованияСтрок ЧередованиеЦветовСтрок "
    "ШрифтШапки ШрифтПодвала ВыбираемоеЗначение ГиперСсылка АвтоЗаголовок ПоведениеКлавишиEnter АвтоПравила АвтоПорядокОбхода "
    "РежимПрокручиваемыхСтраниц СохранятьЗначения ВосстанавливатьЗначения ПроверятьЗаполнениеАвтоматически "
    "ИспользоватьВыравнивающиеЛинии ИспользоватьСетку ОтображениеЗакладок РаспределятьПоСтраницам Стиль "
    "КартинкаЗаголовка СостояниеОкна СоединяемоеОкно ПоложениеОкна ПоложениеПрикрепленногоОкна ИзменениеРазмера "
    "РазрешитьЗакрытие СпособОтображенияОкна ИзменятьСпособОтображенияОкна РежимРабочегоСтола"
).split()
_CANON = {c.lower(): c for c in CANON}
# перечисления (не флажки и не числа): без суффикса значения в имени копии — значение показывается кодом
ENUM_PROPS = {
    "ГоризонтальноеПоложение", "ВертикальноеПоложение", "ПоложениеКартинки", "РазмерКартинки", "КонтекстноеМеню",
    "РежимВыбораНезаполненного", "ПоложениеЗаголовка", "Рамка", "Вывод", "РежимВводаСтрок", "РежимВыделения",
    "РежимВыделенияСтроки", "ГоризонтальнаяПолосаПрокрутки", "ВертикальнаяПолосаПрокрутки", "НачальноеОтображениеСписка",
    "ПоведениеКлавишиEnter", "ОтображениеЗакладок", "Стиль", "СостояниеОкна", "ПоложениеОкна", "ПоложениеПрикрепленногоОкна",
    "ИзменениеРазмера", "СпособОтображенияОкна", "ИзменятьСпособОтображенияОкна",
}
_ACTION_PROC = "e1692cc2-605b-4535-84dd-28440238746c"  # привязка процедуры модуля — событие, их читает разбор по обработчикам


def _prop_name(raw: str) -> str:
    """Имя свойства из имени копии: цифры автонумерации в конце отбрасываем («ЧерноБелыйПросмотр1»)."""
    stem = re.sub(r"\d+$", "", raw)
    return canon(stem or raw)


def canon(prop: str) -> str:
    """Каноническое имя свойства; опечатки в имени копии («Расширенноережактирование») — по близости к палитре."""
    key = prop.lower()
    if key in _CANON:
        return _CANON[key]
    near = difflib.get_close_matches(key, list(_CANON), n=1, cutoff=0.86)
    return _CANON[near[0]] if near else prop[:1].upper() + prop[1:]


def _is_etalon(rest: str) -> bool:
    """«Эталон», «Этлаон», «Эталон2», «ЭталонМногострочный» — имя копии пишут с опечатками."""
    low = rest.lower()
    return low.startswith("эталон") or difflib.SequenceMatcher(None, low[:6], "эталон").ratio() >= 0.8


def _etalon_tail(rest: str) -> str:
    return rest[6:].lstrip("_")


def _forms(epf: Path) -> list[tuple[str, list[Any]]]:
    acc: list[tuple[str, str]] = []
    _walk_blob(epf.read_bytes(), "", depth=0, acc=acc, budget=[10**9])
    names = {}
    for rel, body in acc:
        m = _FORM_HDR.search(body[:600])
        if m:
            names[m.group(1)] = m.group(2)
    out = []
    for rel, body in acc:
        if rel.endswith(".0/form"):
            tree = brace.parse(body)
            out.append((names.get(rel.split("/")[-2][:36], rel), tree[0] if tree and isinstance(tree[0], list) else tree))
    return out


LEARNED: dict[str, str] = {}  # GUID типа → имя типа, выученное по формам эталона (форма названа как тип)


def _learn_types(forms: list[tuple[str, list[Any]]]) -> None:
    """Форма «ПолеГеографическойСхемы» с элементами «ПолеГеографическойСхемы_Эталон» → GUID их типа = имя формы."""
    for fname, top in forms:
        def visit(n: list[Any]) -> None:
            if len(n) >= 4 and brace.is_guid(n[0]) and not isinstance(n[1], list):
                nm = _name(n)
                if "_" in nm and nm.split("_", 1)[0].lower() == fname.lower() and n[0] not in ELEMENT_TYPES:
                    LEARNED.setdefault(n[0], fname)
            for c in n:
                if isinstance(c, list):
                    visit(c)
        visit(top)


def _elements(top: list[Any]) -> list[tuple[str, str, list[Any]]]:
    out = []

    def visit(n: list[Any]) -> None:
        if len(n) >= 4 and brace.is_guid(n[0]) and not isinstance(n[1], list):
            nm = _name(n)
            if nm:
                out.append((element_type(n[0], nm, LEARNED), nm, n))
        for c in n:
            if isinstance(c, list):
                visit(c)
    visit(top)
    return out


def _strip_elements(top: Any) -> Any:
    """Форма без поддеревьев элементов — для сравнения свойств самой формы."""
    if not isinstance(top, list):
        return top
    if len(top) >= 4 and brace.is_guid(top[0]) and not isinstance(top[1], list) and _name(top):
        return "ЭЛЕМЕНТ"
    return [_strip_elements(x) for x in top]


def _flat(x: Any, path: tuple = (), out: dict | None = None) -> dict[tuple, Any]:
    out = {} if out is None else out
    if isinstance(x, list):
        out[path + ("#len",)] = len(x)
        for i, y in enumerate(x):
            _flat(y, path + (i,), out)
    else:
        out[path] = x
    return out


def _get(node: Any, path: list[int]) -> Any:
    for p in path:
        node = node[p]
    return node


def _common_prefix(paths: list[tuple]) -> tuple:
    pre = paths[0]
    for p in paths[1:]:
        k = 0
        while k < min(len(pre), len(p)) and pre[k] == p[k]:
            k += 1
        pre = pre[:k]
    return tuple(x for x in pre if isinstance(x, int))


def _is_int(v: Any) -> bool:
    return not isinstance(v, (list, brace.Str)) and re.fullmatch(r"-?\d+", str(v)) is not None


TEXT_PROPS = {"Заголовок", "Подсказка", "Маска", "ВыбираемоеЗначение"}
_STYLE = re.compile(r"^(Цвет|Шрифт|Картинка|СочетаниеКлавиш)")  # оформление — блок целиком («задано»)
_SKIP_PROBES = {"Имя", "Верх", "Лево", "Ширина", "Высота", "ПривязкаГраниц", "ПорядокОбхода"}  # не свойства / раскладка


def _scalar_diffs(diffs: list[tuple]) -> list[tuple]:
    return [d for d in diffs if d[0][-1] != "#len" and d[1] is not None and d[2] is not None
            and not isinstance(d[1], list) and not isinstance(d[2], list)]


def _block(base: Any, probe: Any, diffs: list[tuple], kind: str, value_name: str) -> dict[str, Any] | None:
    pre = _common_prefix([p for p, _, _ in diffs])
    if len(pre) <= 2:  # весь блок свойств целиком — слишком грубо, место свойства не локализовано
        return None
    try:
        blk = _get(probe, list(pre))
        dflt = _get(base, list(pre))
    except (IndexError, TypeError):
        return None
    ent: dict[str, Any] = {"path": list(pre), "kind": kind, "default": json.dumps(dflt, ensure_ascii=False)}
    if value_name:
        ent.update(kind="enum_block", values={json.dumps(blk, ensure_ascii=False): value_name})
    return ent


def _scalar(path: tuple, a: Any, b: Any) -> dict[str, Any]:
    if isinstance(a, brace.Str) or isinstance(b, brace.Str):
        return {"path": list(path), "kind": "str", "default": str(a)}
    if _is_int(a) and _is_int(b):
        ia, ib = int(a), int(b)
        x = ia ^ ib
        if {str(a), str(b)} <= {"0", "1"}:
            return {"path": list(path), "kind": "bool", "default": str(a)}
        if max(abs(ia), abs(ib)) > 255 and x and x & (x - 1) == 0:
            return {"path": list(path), "kind": "bit", "bit": x, "default_set": bool(ia & x)}
    return {"path": list(path), "kind": "int", "default": str(a)}


KNOWN: dict[str, dict[tuple, str]] = collections.defaultdict(dict)  # тип → путь → свойство, уже найденные ранее


def _with_alts(cands: list[dict[str, Any]]) -> dict[str, Any]:
    """Несколько правдоподобных мест: первое — основное, остальные — alts (выбор по выгрузке при --validate)."""
    ent = dict(cands[0])
    if len(cands) > 1:
        ent["alts"] = [dict(c) for c in cands[1:]]
    return ent


def _classify(base: Any, probe: Any, diffs: list[tuple], value_name: str, prop: str = "",
              owned: dict[tuple, str] | None = None) -> dict[str, Any] | None:
    """Место и вид свойства по различиям копии с эталоном; вид свойства задаёт, что искать среди различий."""
    # места, которые уже принадлежат другим свойствам этого типа, — побочный эффект копии, не её свойство
    diffs = [d for d in diffs if (owned or {}).get(d[0], prop) == prop]
    if not diffs:
        return None
    scal = _scalar_diffs(diffs)
    if prop in TEXT_PROPS:
        strs = [d for d in scal if isinstance(d[2], brace.Str) and not isinstance(d[1], list)]
        if len(diffs) == 1 and scal and isinstance(scal[0][2], brace.Str):
            return {"path": list(scal[0][0]), "kind": "str", "default": str(scal[0][1])}
        if strs:
            return _with_alts([{"path": list(d[0]), "kind": "str", "default": str(d[1])} for d in strs])
        return _block(base, probe, diffs, "text", "")
    if _STYLE.match(prop):
        return _block(base, probe, diffs, "block", "")
    if value_name or prop in ENUM_PROPS:
        small = [d for d in scal if _is_int(d[1]) and _is_int(d[2])]
        if len(small) == 1:
            path, a, b = small[0]
            return {"path": list(path), "kind": "enum", "default": str(a), "values": {str(b): value_name} if value_name else {}}
        return _block(base, probe, diffs, "block", value_name)
    # флажок / число: одно изменённое значение; рядом может добавиться вложенный блок (пункты меню у РежимМеню)
    flags = [d for d in scal if {str(d[1]), str(d[2])} <= {"0", "1"}]
    # флаг внутри списка, у которого изменилась длина (вставлена структура: список значений, подменю), — побочный эффект
    resized = {d[0][:-1] for d in diffs if d[0] and d[0][-1] == "#len" and d[1] is not None and d[2] is not None}
    depth = {id(d): max((k for k in range(1, len(d[0])) if d[0][:k] in resized), default=0) for d in flags}
    if depth and min(depth.values()) < max(depth.values()):
        core = [d for d in flags if depth[id(d)] == min(depth.values())]  # вставленная структура глубже — её флаги побочные
        flags = core
        scal = [d for d in scal if d in core or {str(d[1]), str(d[2])} - {"0", "1"}]
    if len(scal) == 1:
        return _scalar(*scal[0])
    if len(flags) == 1:
        return _scalar(*flags[0])
    if len(flags) > 1 and prop not in GENERIC:
        return _with_alts([_scalar(*d) for d in flags])  # переключатель потянул за собой другие флаги
    return _block(base, probe, diffs, "block", "")


def _diffs(a: Any, b: Any, *, noise: bool) -> list[tuple]:
    fa, fb = _flat(a), _flat(b)
    out = []
    for k in sorted(set(fa) | set(fb), key=str):
        if fa.get(k) != fb.get(k) and not (noise and _NOISE.match(str(k))):
            out.append((k, fa.get(k), fb.get(k)))
    return out


def build_map(epfs: list[Path]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    found: dict[str, dict[str, Any]] = collections.defaultdict(dict)
    log: list[str] = []

    KNOWN.clear()
    if MAP_PATH.is_file():
        for k, props in json.loads(MAP_PATH.read_text(encoding="utf-8")).items():
            for pn, e in props.items():
                if isinstance(e, dict) and e.get("path"):
                    KNOWN[k][tuple(e["path"])] = pn

    def add(key: str, prop: str, ent: dict[str, Any] | None, raw: list[tuple], who: str) -> None:
        if ent is None:
            log.append(f"? {key}.{prop} ({who}): нет различий или не локализовано: {[str(d[0]) for d in raw][:6]}")
            return
        if prop in GENERIC and ent["path"][:1] == [4]:
            key = GENERIC_KEY
        KNOWN[key][tuple(ent["path"])] = prop
        cur = found[key].get(prop)
        if cur and cur["path"] != ent["path"] or (cur and cur.get("alts") != ent.get("alts")):
            # независимые копии одного свойства: общие кандидаты позиции — это и есть свойство
            have = {tuple(c["path"]) for c in [cur] + cur.get("alts", [])}
            both = [c for c in [{k: v for k, v in ent.items() if k != "alts"}] + ent.get("alts", [])
                    if tuple(c["path"]) in have]
            if both and len(both) < 1 + len(ent.get("alts", [])):
                ent = _with_alts(both)
                log.append(f"  ∩ {key}.{prop}: сужено пересечением копий до {[c['path'] for c in both]}")
        if cur and cur.get("values") and ent.get("values") and cur["path"] == ent["path"]:
            cur["values"].update(ent["values"])
        else:
            found[key][prop] = ent
        log.append(f"+ {key}.{prop}: {ent['kind']} {ent['path']}" + (f" bit={ent.get('bit')}" if ent.get("bit") else "")
                   + (f" {ent.get('values')}" if ent.get("values") else ""))

    for epf in epfs:
        forms = _forms(epf)
        _learn_types(forms)
        # свойства формы: формы-копии Форма_Эталон / Форма_<Свойство>
        fbase = next((t for n, t in forms if n.endswith("_Эталон") and n.split("_")[0] == "Форма"), None)
        for fname, top in forms:
            if fbase is not None and fname.split("_")[0] == "Форма" and not fname.endswith("_Эталон"):
                parts = fname.split("_")
                prop, val = canon(parts[1]), "_".join(parts[2:])
                sa, sb = _strip_elements(fbase), _strip_elements(top)
                d = _diffs(sa, sb, noise=False)
                add(FORM_KEY, prop, _classify(sa, sb, d, val, prop), d, fname)
            groups: dict[tuple[str, str], dict[str, list[Any]]] = collections.defaultdict(dict)
            for et, nm, node in _elements(top):
                if "_" in nm:
                    pre, rest = nm.split("_", 1)
                    groups[(et, pre.lower())][rest] = node
            for (et, pre), members in groups.items():
                # эталон — «Эталон»; «Эталон<Родитель>» — эталон с включённым родителем зависимых свойств
                # (одновременно и копия свойства <Родитель> относительно главного эталона)
                bases = {r: n for r, n in members.items() if _is_etalon(r)}
                main = next((n for r, n in bases.items() if not _etalon_tail(r)), None)
                if main is None:
                    log.append(f"! {et}: группа «{pre}» без эталона (опечатка в имени?): {sorted(members)[:8]}")
                    continue
                items: dict[str, tuple[str, str, Any]] = {}  # ключ → (свойство, значение, узел)
                for rest, node in members.items():
                    if _is_etalon(rest) and not _etalon_tail(rest):
                        continue
                    if _is_etalon(rest):
                        tail = _etalon_tail(rest)
                        if not tail or tail.isdigit():
                            continue  # «Эталон2» — безымянная копия (автонумерация), свойство неизвестно
                        items[rest] = (_prop_name(tail), "", node)
                    else:
                        p, _, v = rest.partition("_")
                        items[rest] = (_prop_name(p), v, node)
                items = {k: v for k, v in items.items() if v[0] and v[0] not in _SKIP_PROBES}
                vs_main = {k: _diffs(main, v[2], noise=True) for k, v in items.items()}
                # служебные отличия (счётчик порядка создания и т.п.): в одном месте у большинства копий одно и то же новое значение;
                # разные значения в одном месте (маска, где свойства меняют разные биты) — это свойства, их оставляем
                seen: dict[tuple, collections.Counter] = collections.defaultdict(collections.Counter)
                for d in vs_main.values():
                    for p, _, b in d:
                        seen[p][str(b)] += 1
                service = {p for p, c in seen.items() if (len(c) == 1 and sum(c.values()) >= max(2, 0.6 * len(items)))
                           or (len(items) >= 5 and c.most_common(1)[0][1] >= max(5, 0.6 * len(items)))}
                # номера создаваемых вместе с элементом объектов: изменены почти у всех копий, у каждой своё целое число
                service |= {p for p, c in seen.items() if len(items) >= 5 and sum(c.values()) >= 0.7 * len(items)
                            and len(c) >= 0.9 * sum(c.values()) and all(_is_int(v) for v in c)}
                for key, (prop, val, node) in items.items():
                    d = [x for x in vs_main[key] if x[0] not in service]
                    base, base_name = main, "Эталон"
                    for bn, bnode in bases.items():
                        if bnode is node or bnode is main:
                            continue
                        alt = [x for x in _diffs(bnode, node, noise=True) if x[0] not in service]
                        if len(alt) < len(d) or (not d and alt == []):
                            d, base, base_name = alt, bnode, bn
                    if any(_ACTION_PROC in str(x[2]) for x in d):
                        log.append(f"- {et}.{prop}: событие — обработчики читаются по именам процедур, в карту не нужно")
                        continue
                    add(et, prop, _classify(base, node, d, val, prop, KNOWN.get(et)), d,
                        f"{fname}:{pre}_{key}" + (f" от {base_name}" if base_name != "Эталон" else ""))
    if LEARNED:
        found["_Типы"] = dict(LEARNED)
        log.append("типы по GUID: " + ", ".join(f"{g[:8]}={n}" for g, n in LEARNED.items()))
    return found, log


def validate(mp: dict[str, dict[str, Any]], dump: Path, limit: int = 1500) -> list[str]:
    from tools.form_info import _unpack_bin

    files = list(dump.rglob("Form.bin"))
    random.seed(7)
    random.shuffle(files)
    stats: dict[tuple[str, str], collections.Counter] = collections.defaultdict(collections.Counter)
    c_def: dict[tuple[str, str], int] = {}
    toks: dict[tuple[str, str], collections.Counter] = collections.defaultdict(collections.Counter)
    for f in files[:limit]:
        try:
            ft, _ = _unpack_bin(f)
        except Exception:  # noqa: BLE001
            continue
        if not ft:
            continue
        tree = brace.parse(ft)
        top = tree[0] if tree and isinstance(tree[0], list) else tree
        for et, _, node in _elements(top):
            for key in (et, GENERIC_KEY):
                for prop, ent in (mp.get(key) or {}).items():
                    try:
                        v = _get(node, ent["path"])
                        stats[(key, prop)]["L" if isinstance(v, list) else str(v)] += 1
                        if ent["kind"] in ("text", "block", "enum_block"):
                            tok = json.dumps(v, ensure_ascii=False)
                        elif ent["kind"] == "bit":
                            tok = "1" if int(v) & ent["bit"] else "0"
                        else:
                            tok = str(v)
                        toks[(key, prop)][tok] += 1
                        if ent["kind"] in ("text", "block", "enum_block") and tok == ent["default"]:
                            c_def[(key, prop)] = c_def.get((key, prop), 0) + 1
                    except (IndexError, TypeError, ValueError):
                        stats[(key, prop)]["<нет пути>"] += 1
    rep = []
    for (et, prop), c in sorted(stats.items()):
        ent, tot = mp[et][prop], sum(c.values())
        bad = 0.0
        if ent["kind"] == "bool":
            bad = sum(n for v, n in c.items() if v not in ("0", "1")) / tot
            nondef = sum(n for v, n in c.items() if v != ent["default"]) / tot
            ent["unreliable"] = bad > 0.05 or nondef > 0.6
        elif ent["kind"] in ("int", "bit", "enum"):
            bad = c.get("<нет пути>", 0) / tot + c.get("L", 0) / tot
            ent["unreliable"] = bad > 0.05
        elif ent["kind"] in ("text", "block", "enum_block"):
            ndef = c_def.get((et, prop), 0)
            ent["unreliable"] = False
            rep_extra = f" не по умолчанию {1 - ndef / tot:.0%}" if tot else ""
            c = collections.Counter({f"отлично от эталона{rep_extra}": tot - ndef, "как эталон": ndef})
        # «по умолчанию» для ответов — самое частое значение по выгрузке (не значение в эталоне: у него бывают нетиповые стили)
        tk = toks.get((et, prop))
        if tk:
            top_tok, top_n = tk.most_common(1)[0]
            share = top_n / sum(tk.values())
            ent.pop("common", None)
            ent.pop("common_set", None)
            if share >= 0.5:
                if ent["kind"] == "bit":
                    ent["common_set"] = top_tok == "1"
                else:
                    ent["common"] = top_tok
            elif ent["kind"] in ("text", "block", "enum_block"):
                ent["unreliable"] = True  # значения слишком разнородны — по ним нечего сказать
            ent["share"] = round(share, 2)
        if ent.get("confirmed"):  # подтверждено парой эталонов «выкл/вкл»; блок бывает не у всех типов значения
            ent["unreliable"] = False
        ent["seen"] = tot
        rep.append(f"{'!' if ent.get('unreliable') else ' '} {et}.{prop} [{ent['kind']}] n={tot} {dict(c.most_common(4))}")
    return rep


def pick_alts(mp: dict[str, dict[str, Any]], dump: Path, limit: int = 600) -> list[str]:
    """Для свойств с несколькими кандидатами позиции выбрать ту, что в реальных формах всегда на месте и читается."""
    from tools.form_info import _unpack_bin

    todo = {(k, p): e for k, props in mp.items() for p, e in props.items() if isinstance(e, dict) and e.get("alts")}
    if not todo:
        return []
    files = list(dump.rglob("Form.bin"))
    random.seed(7)
    random.shuffle(files)
    types = {k for k, _ in todo}
    nodes: dict[str, list[Any]] = collections.defaultdict(list)
    for f in files[:limit]:
        try:
            ft, _ = _unpack_bin(f)
        except Exception:  # noqa: BLE001
            continue
        if not ft:
            continue
        tree = brace.parse(ft)
        for et, _, node in _elements(tree[0] if tree and isinstance(tree[0], list) else tree):
            if et in types:
                nodes[et].append(node)
    rep = []
    for (et, prop), ent in todo.items():
        cands = [{k: v for k, v in ent.items() if k != "alts"}] + ent["alts"]
        others = [tuple(e["path"]) for p, e in mp[et].items() if p != prop and isinstance(e, dict) and e.get("path")]
        scored = []
        for c in cands:
            bad = n = 0
            col: list[str] = []
            for node in nodes[et]:
                n += 1
                try:
                    v = _get(node, c["path"])
                except (IndexError, TypeError):
                    bad += 1
                    col.append("-")
                    continue
                if isinstance(v, list) or (c["kind"] == "bool" and str(v) not in ("0", "1")):
                    bad += 1
                col.append("L" if isinstance(v, list) else str(v))
            near = max((sum(1 for _ in itertools.takewhile(lambda t: t[0] == t[1], zip(c["path"], o))) for o in others), default=0)
            scored.append((round(bad / max(n, 1), 2), -near, c, col))
        scored.sort(key=lambda t: (t[0], t[1]))
        best = scored[0]
        mp[et][prop] = dict(best[2])
        tied = [t for t in scored[1:] if t[0] == best[0]]
        # кандидаты, значения которых в реальных формах совпадают (флаги идут парой), для ответа равнозначны
        split = [t for t in tied if sum(x != y for x, y in zip(best[3], t[3])) > 0.05 * max(len(best[3]), 1)]
        if split:
            mp[et][prop]["ambiguous"] = [best[2]["path"]] + [t[2]["path"] for t in split][:5]
            rep.append(f"?! {et}.{prop}: кандидатов равноценны ({len(split) + 1}) — нужен второй эталон")
            continue
        rep.append(f"~ {et}.{prop}: выбрано {best[2]['path']} из "
                   + ", ".join(f"{t[2]['path']} (нет/мусор {t[0]:.0%})" for t in scored))
    return rep


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("epf", nargs="+", type=Path)
    ap.add_argument("--validate", type=Path, help="папка выгрузки — проверить карту на её Form.bin")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    found, log = build_map(a.epf)
    print("\n".join(log))
    mp: dict[str, dict[str, Any]] = json.loads(MAP_PATH.read_text(encoding="utf-8")) if MAP_PATH.is_file() else {}
    for k, props in found.items():
        cur = mp.setdefault(k, {})
        for pn, e in props.items():
            old_e = cur.get(pn)
            if isinstance(old_e, dict) and old_e.get("confirmed") and old_e.get("path") == e.get("path"):
                e = {**e, **{k: old_e[k] for k in ("confirmed", "source", "requires") if k in old_e}}  # пометка ручной сверки не теряется
            cur[pn] = e
    if a.validate:
        print("\n".join(pick_alts(mp, a.validate)))
        print("\n".join(validate(mp, a.validate)))
    else:
        for k, props in mp.items():
            for pn, e in props.items():
                if isinstance(e, dict) and e.pop("alts", None):
                    print(f"? {k}.{pn}: несколько кандидатов позиции — запусти с --validate")
    if not a.dry_run:
        MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
        MAP_PATH.write_text(json.dumps(mp, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"карта: {MAP_PATH} ({sum(len(v) for v in mp.values())} свойств)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
