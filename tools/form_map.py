"""События Form.xml → обработчики; параметры макета ПФ."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tools.meta_dump import dump_folder_path, dump_object_path
from tools.object_detail import pick_main_form_name


def _xml_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


_UI_WORDS = re.compile(
    r"(?<![а-яё])(?:что|как|почему|где|какие|какой|делает|работает|выполняется|происходит|кнопк\w*|команд\w*|"
    r"форм\w*|пол[ея]|колонк\w*|элемент\w*|обработчик\w*|событи\w*|недоступн\w*|видно|видна|виден|"
    r"печатн\w*|макет\w*|параметр\w*|печата\w*|берется|откуда|при|нажати\w*|открыти\w*|создани\w*|"
    r"списка|элемента|выбора|обрабатыва\w*|основани\w*|заполн\w*|записи|записью|проведени\w*|удалени\w*|"
    r"копировани\w*|вводе|ввод|после|перед|когда|по|из|на|в|у|и|не|а)(?![а-яё])",
    re.I,
)


def _ui_object_full_name(request: str) -> str:
    """
    Объект формы/макета из фразы про UI: точное имя → как есть; иначе фраза без UI-слов
    («кнопка Заполнить в форме заказа клиента» → «заказа клиента») → каталог (документ/справочник/обработка).
    """
    from tools.name_match import resolve_object_full_name
    from tools.object_catalog import resolve_full_name

    strict = resolve_object_full_name(request, strict=True)
    if strict:
        return strict
    # «… в форме заказа клиента» — объект обычно сразу после слова «форма»
    m_after = re.search(r"форм\w*\s+(?:документа\s+|справочника\s+|списка\s+)?([а-яё][а-яё\s]{2,40})$", (request or "").lower().replace("ё", "е"))
    if m_after:
        tail = re.sub(r"\s+", " ", _UI_WORDS.sub(" ", m_after.group(1))).strip()
        if len(tail) >= 4:
            for prefix in ("Документ", "Справочник"):
                rc = resolve_full_name(tail, class_prefix=prefix, top_k=3)
                cands = rc.get("candidates") or []
                if cands and float(cands[0].get("score") or 0) >= 70:
                    return str(cands[0]["full_name"])
    low = (request or "").lower().replace("ё", "е")
    # CamelCase-имена (кнопки/процедуры/формы) и UI-слова убираем
    # имена кнопок/полей/форм пишут с заглавной («Заполнить», «Скидка», «ФормаДокумента») — не объект
    low = re.sub(r"(?<!^)[A-ZА-ЯЁ][\wА-Яа-яЁё-]*", " ", (request or "").strip()).lower().replace("ё", "е")
    cleaned = re.sub(r"\s+", " ", _UI_WORDS.sub(" ", low)).strip()
    if len(cleaned) < 4:
        return ""
    # лучший среди классов с формами; при равенстве — документ/справочник (у них формы чаще всего)
    best: tuple[float, str] = (0.0, "")
    classes = ((6.0, "Документ"), (4.0, "Справочник"), (0.0, "Обработка"), (0.0, "Отчет"))
    # класс назван явно («в документе …») — ищем только в нём: иначе подтянется похожий объект другого класса
    named = [c for c, w in (("Документ", r"документ"), ("Справочник", r"справочник"), ("Обработка", r"обработк"),
                            ("Отчет", r"отчет")) if re.search(w, (request or "").lower().replace("ё", "е"))]
    if len(named) == 1:
        classes = tuple(x for x in classes if x[1] == named[0])
    # главное (первое значимое) слово фразы должно быть в имени/синониме: «реализация товаров» ≠ «поступление товаров»
    head = next((w for w in re.findall(r"[а-яё]{4,}", cleaned) if w not in ("форма", "формы", "форме", "документе",
                 "справочнике", "документа", "справочника", "обработки", "отчета")), "")
    stem = head[:5]
    for bonus, prefix in classes:
        rc = resolve_full_name(cleaned, class_prefix=prefix, top_k=3)
        cands = [c for c in (rc.get("candidates") or [])
                 if not stem or stem in (str(c.get("full_name")) + " " + str(c.get("synonym") or "")).lower().replace("ё", "е")]
        if cands:
            sc = float(cands[0].get("score") or 0) + bonus
            if sc > best[0]:
                best = (sc, str(cands[0]["full_name"]))
    return best[1] if best[0] >= 75 else ""


def _typed_full_name_from_request(request: str, full_name: str = "") -> str:
    """Класс только из речи (документ/справочник/…) + hint; не Документ.{имя} по умолчанию."""
    fn = (full_name or "").strip()
    if fn and "." in fn:
        return fn
    try:
        full = _ui_object_full_name(fn or request or "")
        if full:
            return full
    except Exception:
        pass
    try:
        from tools.name_match import (
            pascal_candidates_in_text,
            resolve_object_name,
        )

        cands = pascal_candidates_in_text(request or "")
        hint = cands[0] if cands else resolve_object_name(request or "")
        if not hint:
            return ""
        pl = (request or "").lower().replace("ё", "е")
        if "справочник" in pl:
            return f"Справочник.{hint}"
        if "обработк" in pl:
            return f"Обработка.{hint}"
        if "отчет" in pl or "отчёт" in pl:
            return f"Отчет.{hint}"
        if "документ" in pl:
            return f"Документ.{hint}"
    except Exception:
        return ""
    return ""


def form_event_map(
    request: str = "",
    *,
    full_name: str = "",
    form_name: str = "",
    limit: int = 80,
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    """События формы (OnCreateAtServer → ПриСозданииНаСервере) из Form.xml."""
    fn = _typed_full_name_from_request(request, full_name)
    if not fn or "." not in fn:
        return {
            "ok": False,
            "error": "Нужен full_name (Документ.X / Справочник.Y) из find_metadata.",
            "need_clarification": True,
            "question": "Форму какого документа или справочника смотреть?",
            "events": [],
        }

    obj_dir = dump_object_path(fn, code_dir)
    if not obj_dir:
        return {
            "ok": False,
            "error": f"Нет папки объекта в выгрузке для {fn}",
            "events": [],
        }
    forms_dir = obj_dir / "Forms"
    form = (form_name or "").strip()
    names: list[str] = []
    if forms_dir.is_dir():
        names = sorted(
            p.name for p in forms_dir.iterdir() if p.is_dir() and not p.name.startswith(".")
        )
    if not form:
        # «ФормаСписка / ФормаДокумента» прямо во фразе, иначе основная форма из метаданных объекта
        m_form = re.search(r"(Форма[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_]*)", request or "")
        if m_form and m_form.group(1) in names:
            form = m_form.group(1)
        else:
            try:
                from tools.form_command import _default_form

                form = _default_form(obj_dir, bool(re.search(r"списк", (request or "").lower())))
            except Exception:
                form = ""
        if form not in names:
            form = pick_main_form_name(names, {}, preferred="")
    if not form:
        return {
            "ok": False,
            "error": "Не указан form_name и форм нет в dump.",
            "available_forms": names,
            "events": [],
        }
    xml = obj_dir / "Forms" / form / "Ext" / "Form.xml"
    text = _xml_text(xml)
    if not text:
        return {
            "ok": False,
            "error": f"Нет Form.xml: {xml}",
            "available_forms": names,
            "events": [],
        }

    pairs = re.findall(
        r'<Event\s+name="([^"]+)">\s*([^<]+)\s*</Event>',
        text,
    )
    limit = max(1, min(int(limit or 80), 200))
    events = [{"event": e, "handler": h.strip()} for e, h in pairs[:limit]]
    module = obj_dir / "Forms" / form / "Ext" / "Form" / "Module.bsl"
    mod_text = _xml_text(module)
    for row in events:
        h = row["handler"]
        row["in_module"] = bool(
            mod_text
            and re.search(
                rf"(?im)^\s*(?:Процедура|Функция|Procedure|Function)\s+{re.escape(h)}\b",
                mod_text,
            )
        )

    return {
        "ok": True,
        "full_name": fn,
        "form_name": form,
        "path": str(xml).replace("\\", "/"),
        "module_path": str(module).replace("\\", "/") if module.is_file() else "",
        "count": len(events),
        "events": events,
        "available_forms": names,
        "truncated": len(pairs) > limit,
        "prefer": (
            "Таблица event → handler. in_module=false — обработчик в XML, "
            "процедуры нет в Module.bsl. Тело — read_code по module_path."
        ),
        "source": "dump Form.xml",
    }


_TPL_FOLDERS = {"Documents": "Документ", "Catalogs": "Справочник", "DataProcessors": "Обработка", "Reports": "Отчет"}


def _find_templates_by_phrase(request: str, code_dir: str | Path | None = None) -> list[dict[str, str]]:
    """Макеты, имя которых содержит «содержательный» токен фразы (ТОРГ-12 → торг12, накладная → накладн)."""
    root = dump_folder_path("Documents", code_dir)
    if not root:
        return []
    base = Path(root).parent
    low = (request or "").lower().replace("ё", "е")
    toks = []
    for w in re.findall(r"[a-zа-яё]+-?\d+|[a-zа-яё]{5,}", low):
        w = w.replace("-", "")
        if w in ("печатн", "печатной", "печатная", "печатает", "печатается", "макета", "макет", "параметры", "почему", "откуда", "берется", "форме", "формы"):
            continue
        toks.append(w if re.search(r"\d", w) else w[:6])
    if not toks:
        return []
    out: list[dict[str, str]] = []
    for folder, prefix in _TPL_FOLDERS.items():
        d = base / folder
        if not d.is_dir():
            continue
        for tdir in d.glob("*/Templates/*"):
            if not tdir.is_dir():
                continue
            nm = tdir.name.lower().replace("ё", "е").replace("_", "")
            if any(t in nm for t in toks):
                out.append({"full_name": f"{prefix}.{tdir.parent.parent.name}", "template": tdir.name})
    # печатные формы (ПФ_*) — первыми
    out.sort(key=lambda x: (0 if x["template"].upper().startswith("ПФ_") else 1, x["full_name"]))
    return out


def print_form_bind(
    request: str = "",
    *,
    full_name: str = "",
    template_name: str = "",
    limit: int = 80,
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Параметры макета ПФ (parameter / detailParameter) из Template.xml."""
    fn = _typed_full_name_from_request(request, full_name)
    if "." not in fn:
        # «ТОРГ-12», «накладная», «счёт-фактура» — это имя МАКЕТА, ищем макеты по всей выгрузке
        found = _find_templates_by_phrase(request, code_dir)
        if len(found) == 1:
            fn, template_name = found[0]["full_name"], found[0]["template"]
        elif found:
            return {
                "ok": True,
                "need_clarification": True,
                "question": "Найдено несколько макетов печатных форм. Какой нужен?",
                "options": [f"{f['full_name']} / {f['template']}" for f in found[:8]],
                "templates_found": found[:15],
                "parameters": [],
            }
        else:
            return {
                "ok": False,
                "need_clarification": True,
                "question": "Печатную форму какого документа смотреть? Назовите документ или макет.",
                "error": "Нужен full_name объекта из find_metadata.",
                "parameters": [],
            }
    obj_dir = dump_object_path(fn, code_dir)
    if not obj_dir:
        return {"ok": False, "error": f"Нет dump для {fn}", "parameters": []}
    tdir = obj_dir / "Templates"
    tnames: list[str] = []
    if tdir.is_dir():
        tnames = sorted(
            p.name for p in tdir.iterdir() if p.is_dir() and not p.name.startswith(".")
        )
    want = (template_name or "").strip()
    q = (request or "").lower().replace("ё", "е")
    if not want:
        for n in tnames:
            if n.lower().replace("ё", "е") in q or "пф_" in n.lower():
                want = n
                if "пф_" in n.lower() or "печат" in q:
                    break
        if not want and tnames:
            pf = [n for n in tnames if n.upper().startswith("ПФ_") or "MXL" in n.upper()]
            want = pf[0] if pf else tnames[0]
    if not want:
        return {
            "ok": False,
            "error": "Нет макетов Templates/ у объекта.",
            "templates": tnames,
            "parameters": [],
        }
    xml = tdir / want / "Ext" / "Template.xml"
    text = _xml_text(xml)
    if not text:
        return {
            "ok": False,
            "error": f"Нет Template.xml: {xml}",
            "templates": tnames,
            "parameters": [],
        }
    params = list(dict.fromkeys(re.findall(r"<parameter>([^<]+)</parameter>", text)))
    details = list(dict.fromkeys(re.findall(r"<detailParameter>([^<]+)</detailParameter>", text)))
    limit = max(1, min(int(limit or 80), 200))
    binds = []
    for p in params[:limit]:
        binds.append({"parameter": p, "detail": ""})
    for d in details:
        matched = False
        for row in binds:
            if not row["detail"] and (
                d in row["parameter"] or row["parameter"].startswith(d)
            ):
                row["detail"] = d
                matched = True
                break
        if not matched and d not in {r["detail"] for r in binds}:
            binds.append({"parameter": "", "detail": d})
    binds = binds[:limit]
    return {
        "ok": True,
        "full_name": fn,
        "template_name": want,
        "path": str(xml).replace("\\", "/"),
        "parameters": binds,
        "parameter_names": params[:limit],
        "detail_names": details[:limit],
        "count": len(binds),
        "templates": tnames,
        "truncated": len(params) > limit,
        "prefer": (
            "parameter — имя в макете; detailParameter — поле объекта, если задано. "
            "Заполнение в BSL — inspect_code (Печать) той же CF."
        ),
        "source": "dump Templates/*/Ext/Template.xml",
    }
