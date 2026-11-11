# -*- coding: utf-8 -*-
"""
«Что делает кнопка X / почему кнопка (поле) X недоступна / не видна» — одна цепочка:

  объект (каталог выгрузки) → форма (по умолчанию или ФормаX из фразы) → Form.xml:
  кнопка → CommandName → команда формы → Action (обработчик) + функциональные опции;
  поле/элемент → Visible / Enabled / ReadOnly / функциональные опции;
  → тело обработчика из модуля формы (Qdrant) + серверная процедура 1-го уровня + вызовы;
  → строки кода «Элементы.X.Видимость / Доступность / ТолькоПросмотр» (кто и где их меняет).

Имена объектов/кнопок — только из фразы и выгрузки этой CF.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tools.meta_dump import dump_object_path

_FOLDER = {"Документ": "Documents", "Справочник": "Catalogs", "Обработка": "DataProcessors", "Отчет": "Reports",
           "РегистрСведений": "InformationRegisters", "ПланВидовХарактеристик": "ChartsOfCharacteristicTypes"}
_STD_COMMANDS = {
    "Write": "Записать", "Post": "Провести", "PostAndClose": "Провести и закрыть", "WriteAndClose": "Записать и закрыть",
    "Close": "Закрыть", "Help": "Справка", "CustomizeForm": "Изменить форму", "Copy": "Скопировать",
    "SetDeletionMark": "Пометить на удаление", "UndoPosting": "Отмена проведения",
}


def _low(s: str) -> str:
    return (s or "").lower().replace("ё", "е")


def _ru(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\s*<v8:item>\s*<v8:lang>ru</v8:lang>\s*<v8:content>(.*?)</v8:content>", block or "", re.S)
    return m.group(1).strip() if m else ""


def _tag(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}>([^<]*)</{tag}>", block or "")
    return m.group(1).strip() if m else ""


def _fopts(block: str) -> list[str]:
    m = re.search(r"<FunctionalOptions>(.*?)</FunctionalOptions>", block or "", re.S)
    return re.findall(r"<Item>([^<]+)</Item>", m.group(1)) if m else []


def _own_props(block: str) -> str:
    """Свойства самого элемента — без вложенных ChildItems/ExtendedTooltip/ContextMenu."""
    b = re.split(r"<ChildItems>|<ContextMenu|<ExtendedTooltip|<Events>", block, maxsplit=1)[0]
    return b


def parse_form_xml(text: str) -> dict[str, Any]:
    commands: dict[str, dict[str, Any]] = {}
    m = re.search(r"\n\t<Commands>(.*?)\n\t</Commands>", text, re.S)
    if m:
        for cm in re.finditer(r'<Command name="([^"]+)"[^>]*>(.*?)</Command>', m.group(1), re.S):
            body = cm.group(2)
            commands[cm.group(1)] = {
                "name": cm.group(1),
                "title": _ru(body, "Title"),
                "tooltip": _ru(body, "ToolTip"),
                "action": _tag(body, "Action"),
                "functional_options": _fopts(body),
                "modifies_data": _tag(body, "ModifiesSavedData") == "true",
            }
    elements: list[dict[str, Any]] = []
    # каждый открывающий тег отдельно + поиск ЕГО закрывающего с учётом вложенности
    # (finditer по «<X>…</X>» съедал контейнеры целиком — вложенные поля/кнопки терялись)
    for em in re.finditer(
        r'<(Button|InputField|CheckBoxField|LabelField|Table|UsualGroup|Page|RadioButtonField|PictureField|Popup)\s+name="([^"]+)"[^>/]*>',
        text,
    ):
        kind, name = em.group(1), em.group(2)
        depth, pos, end = 1, em.end(), len(text)
        tag_re = re.compile(rf"<(/?){kind}\b[^>]*?(/?)>")
        while depth and pos < len(text):
            t = tag_re.search(text, pos)
            if not t:
                break
            if t.group(1):
                depth -= 1
            elif not t.group(2):
                depth += 1
            pos = t.end()
            end = t.start()
        body = text[em.end():end]
        own = _own_props(body)
        children = re.findall(r"<CommandName>([^<]+)</CommandName>", body) if kind == "Popup" else []
        el: dict[str, Any] = {
            "kind": kind,
            "name": name,
            "title": _ru(own, "Title"),
            "data_path": _tag(own, "DataPath"),
            "command": _tag(own, "CommandName"),
            "visible": _tag(own, "Visible") != "false",
            "enabled": _tag(own, "Enabled") != "false",
            "read_only": _tag(own, "ReadOnly") == "true",
            "functional_options": _fopts(own),
        }
        if children:
            el["popup_commands"] = children[:30]
            if len(children) > 30:
                el["popup_commands_total"] = len(children)
        ev = re.search(r"<Events>(.*?)</Events>", body, re.S)
        if ev and kind != "UsualGroup":
            el["events"] = [{"event": e, "handler": h.strip()} for e, h in re.findall(r'<Event name="([^"]+)">([^<]+)</Event>', ev.group(1))][:8]
        elements.append(el)
    return {"commands": commands, "elements": elements}


def _target_words(request: str) -> list[str]:
    """Имя кнопки/поля из фразы: «в кавычках», CamelCase, слова с заглавной (кроме первого слова фразы)."""
    req = request or ""
    out: list[str] = []
    for m in re.finditer(r"[«\"']([^«»\"']{2,60})[»\"']", req):
        out.append(m.group(1))
    for m in re.finditer(r"(?<![\wА-Яа-яЁё])([A-ZА-ЯЁ][\wА-Яа-яЁё]+)((?:\s+(?:и|по|на)\s+[а-яё]+)*)(\s+[а-яё]{3,})?", req):
        if m.start() == 0:
            continue
        head, mid, tail = m.group(1), m.group(2) or "", m.group(3) or ""
        # «Провести и закрыть», «Подбор товаров», «Склад» — все варианты, лучший выберет скоринг
        for v in (head + mid + tail, head + mid, head):
            v = v.strip()
            if v and v not in out:
                out.append(v)
    return out


def _stem(w: str) -> str:
    try:
        from tools.object_catalog import _stem as st

        return st(w)
    except Exception:
        return _low(w)[:5]


def _score(cand_texts: list[str], target: str) -> float:
    t = _low(target)
    t_glued = re.sub(r"\s+", "", t)
    best = 0.0
    tw = [_stem(w) for w in re.findall(r"[а-яёa-z0-9]{3,}", t)]
    for c in cand_texts:
        cl = _low(c)
        if not cl:
            continue
        if cl == t or re.sub(r"\s+", "", cl) == t_glued:
            best = max(best, 100.0)
            continue
        parts = re.findall(r"[А-ЯЁA-Z][а-яёa-z0-9]+", c) if re.search(r"[a-zа-яё][A-ZА-ЯЁ]", c) else re.findall(r"[А-Яа-яЁёA-Za-z0-9]{3,}", c)
        cw = [_stem(x) for x in parts if len(x) >= 3]
        if not tw or not cw:
            continue
        hit = sum(1 for w in tw if any(w == x or (len(w) >= 5 and len(x) >= 5 and (x.startswith(w) or w.startswith(x))) for x in cw))
        if hit:
            best = max(best, 60.0 * hit / len(tw) + 20.0 * hit / len(cw))
    return best


def _find_procedure(text: str, name: str, max_lines: int = 60) -> dict[str, Any] | None:
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if re.match(rf"\s*(?:Асинх\s+)?(?:Процедура|Функция)\s+{re.escape(name)}\s*\(", ln):
            directive = lines[i - 1].strip() if i and lines[i - 1].strip().startswith("&") else ""
            body = []
            for j in range(i, min(len(lines), i + 600)):
                body.append(lines[j])
                if re.match(r"\s*Конец(?:Процедуры|Функции)", lines[j]):
                    break
            calls = sorted(set(re.findall(r"([A-ZА-ЯЁ][\wА-Яа-яЁё]+(?:\.[A-ZА-ЯЁ][\wА-Яа-яЁё]+)?)\s*\(", "\n".join(body[1:]))))
            return {
                "procedure": name,
                "directive": directive,
                "start_line": i + 1,
                "lines_total": len(body),
                "text": "\n".join(body[:max_lines]) + ("\n…" if len(body) > max_lines else ""),
                "calls": calls[:30],
                "calls_total": len(calls),
            }
    return None


def _element_code_sites(text: str, names: list[str], limit: int = 15) -> list[dict[str, Any]]:
    """Где код меняет Видимость/Доступность/ТолькоПросмотр элемента (и условное оформление по нему)."""
    out: list[dict[str, Any]] = []
    if not names:
        return out
    pat = re.compile(
        # управляемая форма: Элементы.X; обычная: ЭлементыФормы.X (и ЭлементыФормы.Панель.Страницы.X)
        r"(?:Элементы|ЭлементыФормы)(?:\.\w+\.Страницы)?\.(" + "|".join(re.escape(n) for n in names)
        + r")\.(Видимость|Доступность|ТолькоПросмотр|Enabled|Visible|ReadOnly)\s*=",
    )
    # БСП/типовые: УстановитьСвойствоЭлементаФормы(Элементы, "Имя", "Доступность", …), Элементы["Имя"]
    alt = "|".join(re.escape(n) for n in names)
    pat_bsp = re.compile(r'(?:УстановитьСвойствоЭлементаФормы|УстановитьСвойствоЭлемента)\s*\([^,]+,\s*"(?:' + alt + r')"')
    pat_idx = re.compile(r'(?:Элементы|ЭлементыФормы)\[\s*"(?:' + alt + r')"\s*\]\.(?:Видимость|Доступность|ТолькоПросмотр)')
    pat_ca = re.compile(r"(?:ПолеЭлемента|ОформляемыеПоля|ДобавитьОформляемоеПоле|Поле\s*=\s*Новый\s+ПолеКомпоновкиДанных)\W.*\b(" + "|".join(re.escape(n) for n in names) + r")\b")
    proc = ""
    for i, ln in enumerate(text.splitlines(), 1):
        mp = re.match(r"\s*(?:Асинх\s+)?(?:Процедура|Функция)\s+([\wА-Яа-яЁё]+)", ln)
        if mp:
            proc = mp.group(1)
        if ln.strip().startswith("//"):
            continue
        # имя элемента строкой внутри процедуры, которая по имени управляет видимостью/доступностью/оформлением
        in_ui_proc = bool(re.search(r"Видимост|Доступност|ТолькоПросмотр|УсловноеОформлен|НастроитьЭлемент|УстановитьСвойств|ОбновитьЭлемент", proc))
        named = re.search(r'"(?:' + alt + r')"', ln)
        if pat.search(ln) or pat_ca.search(ln) or pat_bsp.search(ln) or pat_idx.search(ln) or (in_ui_proc and named) or (
            named and "УсловноеОформление" in ln
        ):
            out.append({"line": i, "procedure": proc, "code": ln.strip()[:200]})
            if len(out) >= limit:
                break
    return out


def _explain_ordinary(fn: str, obj_dir: Path, form: str, req: str, command: str = "") -> dict[str, Any]:
    """Обычная форма (Form.bin): кнопка/поле по подписи → процедура модуля формы, вкладка, где код меняет элемент."""
    from tools.form_info import load_form_layout

    lay = load_form_layout(obj_dir, form, full_name=fn)
    if lay.get("error"):
        return {"ok": False, "error": lay["error"], "available_forms": lay.get("available_forms"), "full_name": fn}
    module_text = lay.get("_module_text") or ""
    want_field = bool(re.search(r"(?<![а-яё])(пол[еяю]|колонк\w*|реквизит\w*|флажок|галочк\w*)(?![а-яё])", _low(req)))
    targets = [command] if command else _target_words(req)
    cands: list[tuple[str, dict[str, Any], list[str]]] = []
    for b in lay.get("buttons") or []:
        cands.append(("button", b, [b.get("caption") or "", b.get("name") or "", b.get("handler") or ""]))
    for el in lay.get("elements") or []:
        if el["type"] in ("Разделитель", "КоманднаяПанель"):
            continue
        cands.append(("element", el, [el.get("caption") or "", el.get("attribute_synonym") or "", el["name"]]))
    best: tuple[float, str, dict[str, Any]] | None = None
    for t in targets:
        for kind, obj, texts in cands:
            sc = _score(texts, t)
            if want_field and kind == "element" and obj.get("type") not in ("Надпись", "Панель", "РамкаГруппы"):
                sc += 8
            elif not want_field and kind == "button":
                sc += 3
            if best is None or sc > best[0]:
                best = (sc, kind, obj)
    if not best or best[0] < 40:
        return {
            "ok": True,
            "need_clarification": True,
            "question": f"Какая кнопка/поле формы {fn}.{form}? Назовите как на экране.",
            "options": [b.get("caption") for b in (lay.get("buttons") or []) if b.get("handler")][:20],
            "options_total": len([b for b in (lay.get("buttons") or []) if b.get("handler")]),
            "full_name": fn,
            "form_name": form,
            "form_kind": "ordinary",
        }
    _sc, kind, obj = best
    result: dict[str, Any] = {"ok": True, "full_name": fn, "form_name": form, "form_kind": "ordinary",
                              "match_score": round(_sc, 1), kind: obj}
    handler = obj.get("handler") if kind == "button" else ""
    if handler and module_text:
        h = _find_procedure(module_text, handler)
        if h:
            result["handler"] = h
            for c in h["calls"]:
                if "." in c or c == handler:
                    continue
                sub = _find_procedure(module_text, c, max_lines=40)
                if sub:
                    result.setdefault("called_in_module", []).append(sub)
                if len(result.get("called_in_module") or []) >= 2:
                    break
    elif kind == "button" and obj.get("standard"):
        result["note"] = "Стандартное действие платформы (Записать/ОК/Закрыть…): логика — в событиях формы/объекта (ПередЗаписью, ПриЗаписи…)."
    for ev, proc in list((obj.get("events") or {}).items())[:3] if kind == "element" else []:
        h = _find_procedure(module_text, proc, max_lines=40) if module_text else None
        result.setdefault("element_events", []).append({"event": ev, **(h or {"procedure": proc})})
    name = obj.get("name") or ""
    sites = _element_code_sites(module_text, [name]) if module_text and name else []
    reasons = []
    page = obj.get("page")
    if page:
        hidden = any(p.get("caption") == page and p.get("visible") is False for p in lay.get("pages") or [])
        result["page"] = page
        if hidden:
            reasons.append(f"вкладка «{page}» в форме скрыта (Видимость = Ложь) — показывается, только если код её включит")
    if kind == "element":
        if obj.get("visible") is False:
            reasons.append("в форме у элемента Видимость = Ложь (виден, только если код включит)")
        if obj.get("enabled") is False:
            reasons.append("в форме у элемента Доступность = Ложь")
        if obj.get("read_only"):
            reasons.append("в форме у элемента ТолькоПросмотр = Истина")
    if sites:
        reasons.append(f"код формы меняет свойства элемента в {len(sites)} местах (см. code_sites)")
    result["availability"] = {"reasons": reasons, "code_sites": sites}
    result["module_path"] = lay.get("module_path")
    result["prefer"] = (
        "Ответ: что на экране (подпись, вкладка) → что делает (handler.text, called_in_module) → "
        "почему может быть недоступно (availability). Не выдумывай того, чего нет в результате."
    )
    return result


def _default_form(obj_dir: Path, want_list: bool) -> str:
    xml = obj_dir.parent / f"{obj_dir.name}.xml"
    try:
        t = xml.read_text(encoding="utf-8-sig", errors="replace")[:60000]
    except OSError:
        t = ""
    tag = "DefaultListForm" if want_list else "DefaultObjectForm"
    m = re.search(rf"<{tag}>[^<]*\.Form\.([^<.]+)</{tag}>", t)
    if m:
        return m.group(1)
    forms = sorted(p.name for p in (obj_dir / "Forms").glob("*") if p.is_dir()) if (obj_dir / "Forms").is_dir() else []
    return forms[0] if forms else ""


def explain_command(
    request: str = "",
    *,
    full_name: str = "",
    form_name: str = "",
    command: str = "",
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    req = (request or "").strip()
    fn = (full_name or "").strip()
    if not fn:
        try:
            from tools.form_map import _ui_object_full_name

            # название кнопки/поля («Подбор товаров») — не объект: вырезаем его из фразы перед поиском объекта
            obj_phrase = req
            cut = sorted(_target_words(req), key=len, reverse=True)[:1]
            for t in cut:
                obj_phrase = obj_phrase.replace(t, " ")
            # объект, точно названный во фразе целиком («в документе Реализация товаров и услуг»), важнее:
            # вырезанное «название кнопки» могло быть частью имени объекта. Но не сама кнопка («Подбор товаров»).
            from tools.name_match import resolve_object_full_name

            strict = resolve_object_full_name(req, strict=True) or ""
            cut_camel = "".join(w[:1].upper() + w[1:] for t in cut for w in t.split())
            if strict and strict.split(".", 1)[-1] != cut_camel:
                fn = strict
            else:
                fn = _ui_object_full_name(obj_phrase) or _ui_object_full_name(req)
        except Exception:
            fn = ""
    if not fn or "." not in fn:
        return {
            "ok": True,
            "need_clarification": True,
            "question": "В форме какого документа или справочника эта кнопка/поле?",
            "about": "object",
        }
    obj_dir = dump_object_path(fn, code_dir)
    if not obj_dir:
        return {"ok": False, "error": f"Нет выгрузки формы для {fn}", "full_name": fn}
    cls, name = fn.split(".", 1)
    form = (form_name or "").strip()
    if not form:
        m = re.search(r"(Форма[A-ZА-ЯЁ][\wА-Яа-яЁё]*)", req)
        form = m.group(1) if m else _default_form(obj_dir, bool(re.search(r"списк", _low(req))))
    xml = obj_dir / "Forms" / form / "Ext" / "Form.xml"
    try:
        text = xml.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        if (obj_dir / "Forms" / form / "Ext" / "Form.bin").is_file():
            return _explain_ordinary(fn, obj_dir, form, req, command)
        forms = sorted(p.name for p in (obj_dir / "Forms").glob("*") if p.is_dir()) if (obj_dir / "Forms").is_dir() else []
        if not forms:
            return {
                "ok": False,
                "full_name": fn,
                "error": f"У {fn} в конфигурации нет своих форм — платформа строит форму автоматически.",
                "hint": "Кнопки такой формы — стандартные команды платформы (Записать, Провести, Заполнить…); "
                        "логика — в модуле объекта (ОбработкаЗаполнения, ПередЗаписью, ОбработкаПроведения): "
                        "inspect_code / read_code модуля объекта.",
            }
        return {"ok": True, "need_clarification": True, "question": f"Какая форма {fn}?", "options": forms[:10], "full_name": fn}
    parsed = parse_form_xml(text)
    commands, elements = parsed["commands"], parsed["elements"]

    # --- что ищем ---
    want_field = bool(re.search(r"(?<![а-яё])(пол[еяю]|колонк\w*|реквизит\w*|флажок|галочк\w*)(?![а-яё])", _low(req)))
    targets = [command] if command else _target_words(req)
    best: tuple[float, str, dict[str, Any]] | None = None
    for t in targets:
        for el in elements:
            cmd = el.get("command") or ""
            cmd_name = cmd.split(".")[-1] if cmd else ""
            texts = [el["name"], el.get("title") or "", cmd_name]
            if cmd.startswith("Form.Command."):
                c = commands.get(cmd_name) or {}
                texts += [c.get("title") or "", c.get("tooltip") or ""]
            elif ".StandardCommand." in cmd:
                texts.append(_STD_COMMANDS.get(cmd_name, ""))
            sc = _score(texts, t)
            if want_field and el["kind"] in ("InputField", "CheckBoxField", "LabelField", "RadioButtonField", "PictureField"):
                sc += 8
            elif not want_field and el["kind"] in ("Button", "Popup"):
                sc += 3  # «кнопка» — сначала кнопки
            if best is None or sc > best[0]:
                best = (sc, "element", el)
        for c in commands.values():
            sc = _score([c["name"], c.get("title") or "", c.get("tooltip") or ""], t) - (10 if want_field else 0)
            if best is None or sc > best[0]:
                best = (sc, "command", {"kind": "Command", "name": c["name"], "title": c.get("title"), "command": f"Form.Command.{c['name']}"})
    if not best or best[0] < 40:
        _btn_all = [f"{e.get('title') or e['name']} ({e['name']})" for e in elements if e["kind"] == "Button"]
        buttons = _btn_all[:15]
        return {
            "ok": True,
            "need_clarification": True,
            "question": f"Какая кнопка/поле формы {fn}.{form}? Назовите как на экране.",
            "options": buttons,
            "options_total": len(_btn_all),
            "full_name": fn,
            "form_name": form,
        }
    el = best[2]
    result: dict[str, Any] = {"ok": True, "full_name": fn, "form_name": form, "element": el, "match_score": round(best[0], 1)}

    # --- команда и обработчик ---
    cmd = el.get("command") or ""
    cmd_info: dict[str, Any] = {}
    handler = ""
    if cmd.startswith("Form.Command."):
        cmd_info = commands.get(cmd.split(".")[-1]) or {}
        handler = cmd_info.get("action") or ""
    elif ".StandardCommand." in cmd:
        cmd_info = {"standard": cmd.split(".")[-1], "title": _STD_COMMANDS.get(cmd.split(".")[-1], "")}
        result["note"] = "Стандартная команда платформы: логика — в обработчиках модуля формы/объекта (ПередЗаписью, ОбработкаПроведения…), а не в отдельной процедуре."
    elif cmd:
        cmd_info = {"external": cmd}
        result["note"] = f"Команда другого объекта ({cmd}) — её обработчик в модуле команды того объекта."
    result["command_info"] = cmd_info
    if el.get("popup_commands"):
        sub = []
        for cn in el["popup_commands"]:
            c = commands.get(cn.split(".")[-1]) if cn.startswith("Form.Command.") else None
            sub.append({"command": cn, "title": (c or {}).get("title") or _STD_COMMANDS.get(cn.split(".")[-1], ""), "action": (c or {}).get("action")})
        result["popup_items"] = sub
        result["note"] = "Это подменю: на экране — кнопка со списком команд (popup_items). Уточните пункт, чтобы показать его обработчик."

    # --- модуль формы из Qdrant ---
    module_path = f"{_FOLDER.get(cls, '')}/{name}/Forms/{form}/Ext/Form/Module.bsl"
    module_text = ""
    try:
        from vector.catalog import assemble_module_text, get_chunks_by_source_path

        module_text = assemble_module_text(get_chunks_by_source_path(module_path))
    except Exception:
        module_text = ""
    result["module_path"] = module_path
    if handler and module_text:
        h = _find_procedure(module_text, handler)
        if h:
            result["handler"] = h
            # серверная пара 1-го уровня (ИмяНаСервере / ИмяСервер) из того же модуля
            for c in h["calls"]:
                if "." in c or c == handler:
                    continue
                if re.search(r"(НаСервере|Сервер|НаКлиенте)$", c) or c.startswith(handler):
                    sub = _find_procedure(module_text, c, max_lines=40)
                    if sub:
                        result.setdefault("called_in_module", []).append(sub)
                if len(result.get("called_in_module") or []) >= 2:
                    break
    elif handler:
        result["handler"] = {"procedure": handler, "note": "модуль формы не найден в индексе Qdrant"}
    for e in (el.get("events") or [])[:3]:
        if module_text and e.get("handler") and "handler" not in result:
            h = _find_procedure(module_text, e["handler"], max_lines=40)
            if h:
                result.setdefault("element_events", []).append({"event": e["event"], **h})

    # --- почему недоступна / не видна ---
    reasons: list[str] = []
    if not el.get("visible", True):
        reasons.append("в форме у элемента Видимость = Ложь (виден, только если код включит)")
    if not el.get("enabled", True):
        reasons.append("в форме у элемента Доступность = Ложь")
    if el.get("read_only"):
        reasons.append("в форме у элемента ТолькоПросмотр = Истина")
    fos = list(el.get("functional_options") or []) + list(cmd_info.get("functional_options") or [])
    if fos:
        reasons.append("зависит от функциональных опций: " + ", ".join(fos) + " (если опция выключена — кнопка/поле скрыты)")
    names = [el["name"]]
    # для кнопки — и все элементы с той же командой (кнопки часто дублируются в подменю)
    if cmd:
        names += [e["name"] for e in elements if e.get("command") == cmd and e["name"] != el["name"]]
    sites = _element_code_sites(module_text, names) if module_text else []
    if sites:
        reasons.append(f"код формы меняет свойства элемента в {len(sites)} местах (см. code_sites)")
    if cmd_info.get("modifies_data"):
        reasons.append("команда изменяет данные — в режиме «только просмотр» (нет прав на изменение, закрытый период, документ проведён и запрещено редактирование) платформа её отключает")
    result["availability"] = {"reasons": reasons, "code_sites": sites, "functional_options": fos}
    result["prefer"] = (
        "Ответ: 1) что делает — по handler.text и called_in_module (процедура + ключевые вызовы); "
        "2) почему может быть недоступна/не видна — availability.reasons и code_sites (процедура + строка). "
        "Проверить значения функциональных опций/прав можно в базе. Не выдумывай то, чего нет в результате."
    )
    return result
