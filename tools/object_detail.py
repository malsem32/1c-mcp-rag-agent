"""
Детали объекта метаданных из HTTP + выгрузки:
реквизиты, ТЧ, формы (layout), макеты, команды, состав подсистемы.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tools.code_search import resolve_code_roots

# EN dump folder by RU/EN full_name prefix
_PREFIX_TO_DUMP: dict[str, str] = {
    "Справочник": "Catalogs",
    "Документ": "Documents",
    "Отчет": "Reports",
    "Обработка": "DataProcessors",
    "РегистрСведений": "InformationRegisters",
    "РегистрНакопления": "AccumulationRegisters",
    "РегистрБухгалтерии": "AccountingRegisters",
    "ОбщаяФорма": "CommonForms",
    "ОбщийМодуль": "CommonModules",
    "Подсистема": "Subsystems",
    "Роль": "Roles",
    "Константа": "Constants",
    "Перечисление": "Enums",
    "ПланСчетов": "ChartsOfAccounts",
    "ПланВидовХарактеристик": "ChartsOfCharacteristicTypes",
    "ЖурналДокументов": "DocumentJournals",
    "БизнесПроцесс": "BusinessProcesses",
    "Задача": "Tasks",
    "ПланОбмена": "ExchangePlans",
    "HTTPСервис": "HTTPServices",
}


def parse_full_name(full_name: str) -> tuple[str, str]:
    raw = (full_name or "").strip()
    if "." not in raw:
        return "", raw
    pref, rest = raw.split(".", 1)
    return pref, rest


def dump_object_dir(full_name: str, code_dir: str | Path | None = None) -> Path | None:
    pref, rest = parse_full_name(full_name)
    folder = _PREFIX_TO_DUMP.get(pref)
    if not folder or not rest:
        return None
    # first segment is object name (Подсистема.A.B → need special)
    cr = resolve_code_roots(code_dir)
    for root in cr.roots:
        if pref == "Подсистема":
            # Subsystems/A/Subsystems/B
            parts = rest.split(".")
            p = root / "Subsystems"
            for i, part in enumerate(parts):
                if i == 0:
                    p = p / part
                else:
                    p = p / "Subsystems" / part
            if p.is_dir():
                return p
            continue
        obj_name = rest.split(".")[0]
        p = root / folder / obj_name
        if p.is_dir():
            return p
    return None


def list_dir_children(obj_dir: Path, sub: str) -> list[str]:
    d = obj_dir / sub
    if not d.is_dir():
        return []
    names = []
    for ch in sorted(d.iterdir(), key=lambda x: x.name.lower()):
        if ch.is_dir() and not ch.name.startswith("."):
            names.append(ch.name)
        elif ch.is_file() and ch.suffix.lower() == ".xml" and ch.stem not in names:
            # Forms/Name.xml
            if sub in ("Forms", "Templates", "Commands"):
                names.append(ch.stem)
    # unique preserve
    seen = set()
    out = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def dump_meta_xml_path(full_name: str, code_dir: str | Path | None = None) -> Path | None:
    """Путь к ObjectName.xml рядом с папкой объекта (Documents/X.xml)."""
    pref, rest = parse_full_name(full_name)
    folder = _PREFIX_TO_DUMP.get(pref)
    if not folder or not rest:
        return None
    obj_name = rest.split(".")[0]
    cr = resolve_code_roots(code_dir)
    for root in cr.roots:
        if pref == "Подсистема":
            continue
        p = root / folder / f"{obj_name}.xml"
        if p.is_file():
            return p
    return None


def _clean_v8_type(raw: str) -> str:
    t = (raw or "").strip()
    if t.startswith("cfg:"):
        t = t[4:]
    if t.startswith("xs:"):
        # xs:string → Строка, xs:decimal → Число, xs:dateTime → Дата, xs:boolean → Булево
        mapping = {
            "string": "Строка",
            "decimal": "Число",
            "boolean": "Булево",
            "dateTime": "Дата",
            "base64Binary": "ДвоичныеДанные",
        }
        key = t[3:] if t.startswith("xs:") else t
        return mapping.get(key, t)
    # CatalogRef.X → СправочникСсылка.X etc.
    rep = (
        ("CatalogRef.", "СправочникСсылка."),
        ("DocumentRef.", "ДокументСсылка."),
        ("EnumRef.", "ПеречислениеСсылка."),
        ("ChartOfAccountsRef.", "ПланСчетовСсылка."),
        ("ChartOfCharacteristicTypesRef.", "ПланВидовХарактеристикСсылка."),
        ("ExchangePlanRef.", "ПланОбменаСсылка."),
        ("BusinessProcessRef.", "БизнесПроцессСсылка."),
        ("TaskRef.", "ЗадачаСсылка."),
        ("ChartOfCalculationTypesRef.", "ПланВидовРасчетаСсылка."),
    )
    for a, b in rep:
        if t.startswith(a):
            return b + t[len(a) :]
    return t


def _props_block_fields(block: str) -> dict[str, str]:
    """Из фрагмента <Properties>…</Properties> — Name, Synonym, Type."""
    name_m = re.search(r"<Name>([^<]+)</Name>", block)
    syn_m = re.search(
        r"<Synonym>\s*<v8:item>\s*<v8:lang>ru</v8:lang>\s*<v8:content>([^<]*)</v8:content>",
        block,
        re.S,
    )
    types = re.findall(r"<v8:Type>([^<]+)</v8:Type>", block)
    type_str = ", ".join(_clean_v8_type(t) for t in types) if types else ""
    return {
        "name": name_m.group(1).strip() if name_m else "",
        "synonym": syn_m.group(1).strip() if syn_m else "",
        "type": type_str,
        "тип": type_str,
    }


def _extract_top_level_blocks(text: str, tag: str) -> list[str]:
    """
    Блоки <Tag …>…</Tag> верхнего ChildObjects (не внутри TabularSection).
    Работает через счётчик вложенности.
    """
    open_re = re.compile(rf"<{tag}(?:\s[^>]*)?>")
    close_tag = f"</{tag}>"
    blocks: list[str] = []
    # Restrict to main ChildObjects of object (first big ChildObjects after Properties)
    # Split TabularSection out later by parsing whole text carefully.
    pos = 0
    while True:
        m = open_re.search(text, pos)
        if not m:
            break
        start = m.start()
        i = m.end()
        depth = 1
        while depth > 0 and i < len(text):
            nxt_open = open_re.search(text, i)
            nxt_close = text.find(close_tag, i)
            if nxt_close < 0:
                break
            if nxt_open and nxt_open.start() < nxt_close:
                depth += 1
                i = nxt_open.end()
            else:
                depth -= 1
                if depth == 0:
                    blocks.append(text[start : nxt_close + len(close_tag)])
                    pos = nxt_close + len(close_tag)
                    break
                i = nxt_close + len(close_tag)
        else:
            break
    return blocks


def read_object_meta_xml(
    full_name: str,
    *,
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    Реквизиты, ТЧ, измерения/ресурсы, Form/Template/Command из ObjectName.xml выгрузки.
    Работает без HTTP — достаточно dump конфигурации.
    """
    meta_path = dump_meta_xml_path(full_name, code_dir)
    if not meta_path:
        return {"error": f"Meta XML не найден для {full_name}", "source": "config_dump"}

    try:
        text = meta_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"error": str(e), "source": "config_dump"}

    # synonym of object
    synonym = ""
    m_syn = re.search(
        r"<Properties>.*?<Synonym>\s*<v8:item>\s*<v8:lang>ru</v8:lang>\s*"
        r"<v8:content>([^<]*)</v8:content>",
        text,
        re.S,
    )
    if m_syn:
        synonym = m_syn.group(1).strip()

    # Tabular sections first (to exclude nested attributes from header)
    ts_blocks = _extract_top_level_blocks(text, "TabularSection")
    tabular_sections: list[dict[str, Any]] = []
    ts_spans: list[tuple[int, int]] = []
    for tb in ts_blocks:
        # find span in text for exclusion
        idx = text.find(tb)
        if idx >= 0:
            ts_spans.append((idx, idx + len(tb)))
        # name from Properties at start of TS
        props_m = re.search(r"<Properties>(.*?)</Properties>", tb, re.S)
        props = _props_block_fields(props_m.group(1) if props_m else tb)
        tname = props.get("name") or ""
        attrs: list[dict[str, str]] = []
        for ab in _extract_top_level_blocks(tb, "Attribute"):
            pm = re.search(r"<Properties>(.*?)</Properties>", ab, re.S)
            a = _props_block_fields(pm.group(1) if pm else ab)
            if a.get("name"):
                attrs.append(a)
        if tname:
            tabular_sections.append(
                {
                    "name": tname,
                    "synonym": props.get("synonym") or "",
                    "attributes": attrs,
                    "attribute_count": len(attrs),
                }
            )

    def _in_ts(pos: int) -> bool:
        return any(a <= pos < b for a, b in ts_spans)

    attributes: list[dict[str, str]] = []
    for ab in _extract_top_level_blocks(text, "Attribute"):
        idx = text.find(ab)
        if idx >= 0 and _in_ts(idx):
            continue
        pm = re.search(r"<Properties>(.*?)</Properties>", ab, re.S)
        a = _props_block_fields(pm.group(1) if pm else ab)
        if a.get("name"):
            attributes.append(a)

    # Dimensions / Resources (registers)
    dimensions: list[dict[str, str]] = []
    for db in _extract_top_level_blocks(text, "Dimension"):
        pm = re.search(r"<Properties>(.*?)</Properties>", db, re.S)
        d = _props_block_fields(pm.group(1) if pm else db)
        if d.get("name"):
            dimensions.append(d)
    resources: list[dict[str, str]] = []
    for rb in _extract_top_level_blocks(text, "Resource"):
        pm = re.search(r"<Properties>(.*?)</Properties>", rb, re.S)
        r = _props_block_fields(pm.group(1) if pm else rb)
        if r.get("name"):
            resources.append(r)

    # Enum values
    enum_values: list[str] = []
    for eb in _extract_top_level_blocks(text, "EnumValue"):
        pm = re.search(r"<Properties>(.*?)</Properties>", eb, re.S)
        e = _props_block_fields(pm.group(1) if pm else eb)
        if e.get("name"):
            enum_values.append(e["name"])

    # Child object refs listed as <Form>Name</Form> — names as in config (any custom names)
    forms_xml = re.findall(r"<Form>([^<]+)</Form>", text)
    templates_xml = re.findall(r"<Template>([^<]+)</Template>", text)
    commands_xml = re.findall(r"<Command>([^<]+)</Command>", text)

    # Default*Form from Properties — actual form names assigned in конфигураторе
    default_forms = _parse_default_forms(text)

    rel = str(meta_path)
    try:
        cr = resolve_code_roots(code_dir)
        if cr.roots:
            rel = str(meta_path.relative_to(cr.roots[0])).replace("\\", "/")
    except Exception:
        pass

    out: dict[str, Any] = {
        "ok": True,
        "full_name": full_name,
        "synonym": synonym,
        "attributes": attributes,
        "tabular_sections": tabular_sections,
        "source": "config_dump_xml",
        "meta_path": rel,
    }
    if dimensions:
        out["dimensions"] = dimensions
    if resources:
        out["resources"] = resources
    if enum_values:
        out["enum_values"] = enum_values
    if forms_xml:
        out["forms_xml"] = forms_xml
    if templates_xml:
        out["templates_xml"] = templates_xml
    if commands_xml:
        out["commands_xml"] = commands_xml
    if default_forms:
        out["default_forms"] = default_forms
    return out


def load_enum_values(enum_name: str, code_dir: str | Path | None = None) -> list[str]:
    """Значения перечисления из dump: Enums/Имя.xml."""
    name = (enum_name or "").strip()
    if not name:
        return []
    # Перечисление.X / Enum.X / просто X
    if "." in name:
        name = name.split(".")[-1]
    from vector.catalog import runtime_fs_allowed

    if not code_dir and runtime_fs_allowed():
        try:
            from config import load_config

            cfg = load_config()
            code_dir = cfg.get("code_dir") or (cfg.get("paths") or {}).get("code_dir")
        except Exception:
            code_dir = None
    cr = resolve_code_roots(code_dir)
    for root in cr.roots:
        p = root / "Enums" / f"{name}.xml"
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        vals: list[str] = []
        for eb in _extract_top_level_blocks(text, "EnumValue"):
            pm = re.search(r"<Properties>(.*?)</Properties>", eb, re.S)
            e = _props_block_fields(pm.group(1) if pm else eb)
            if e.get("name"):
                vals.append(e["name"])
        if vals:
            return vals
        # fallback: все Name после корневого
        names = re.findall(r"<Name>([^<]+)</Name>", text)
        return [n for n in names if n != name][:80]
    return []


def enrich_enum_fields_on_structure(
    structure: dict[str, Any],
    fields: list[dict[str, Any]],
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    Вынести реквизиты-перечисления и статусы в начало ответа structure
    (чтобы не потерялись при обрезке 6000 символов).
    """
    status_like: list[dict[str, Any]] = []
    enum_fields: list[dict[str, Any]] = []
    for f in fields:
        if not isinstance(f, dict):
            continue
        name = str(f.get("name") or "")
        typ = str(f.get("type") or f.get("тип") or "")
        low_n = name.lower()
        low_t = typ.lower().replace("ё", "е")
        is_enum = (
            "перечислен" in low_t
            or low_t.startswith("enum")
            or "перечислениессылка" in low_t.replace(" ", "")
        )
        row = {"name": name, "type": typ}
        if is_enum:
            en = typ.split(".")[-1] if "." in typ else typ
            en = re.sub(r"^(Перечисление|ПеречислениеСсылка|EnumRef)\.?", "", en, flags=re.I)
            vals = load_enum_values(en or name, code_dir)
            if vals:
                row["enum_values"] = vals
                row["enum_name"] = en
            enum_fields.append(row)
            if "статус" in low_n or "status" in low_n:
                status_like.append(row)
        elif "статус" in low_n or "status" in low_n:
            status_like.append(row)
    if status_like:
        structure["status_fields"] = status_like
    if enum_fields:
        structure["enum_fields"] = enum_fields
        # плоский список для статуса, если один
        for ef in enum_fields:
            if ef.get("enum_values") and (
                "статус" in (ef.get("name") or "").lower()
                or len(enum_fields) == 1
            ):
                structure.setdefault("enum_values_for_status", ef["enum_values"])
    return structure


# Default*Form property names in dump (EN tags)
_DEFAULT_FORM_TAGS = (
    "DefaultObjectForm",
    "DefaultFolderForm",
    "DefaultListForm",
    "DefaultChoiceForm",
    "DefaultFolderChoiceForm",
    "DefaultRecordForm",
    "DefaultRecordSetForm",
    "DefaultLoadForm",
)


def _short_form_name(ref: str) -> str:
    """Document.X.Form.MyForm → MyForm; bare name stays."""
    raw = (ref or "").strip()
    if not raw or raw.lower() in ("", "undefined", "null"):
        return ""
    # Form.Name or .Form.Name
    if ".Form." in raw:
        return raw.split(".Form.", 1)[-1].strip()
    if ".Форма." in raw:
        return raw.split(".Форма.", 1)[-1].strip()
    if raw.startswith("Form."):
        return raw[5:].strip()
    return raw.split(".")[-1].strip() if "." in raw else raw


def _parse_default_forms(meta_text: str) -> dict[str, str]:
    """
    DefaultObjectForm / DefaultListForm / … из ObjectName.xml.
    Значения — короткие имена форм КАК В КОНФИГУРАЦИИ (не хардкод).
    """
    out: dict[str, str] = {}
    for tag in _DEFAULT_FORM_TAGS:
        m = re.search(rf"<{tag}>([^<]*)</{tag}>", meta_text)
        if not m:
            continue
        short = _short_form_name(m.group(1))
        if short:
            # key without Default/Form: object, list, choice…
            key = tag
            if key.startswith("Default"):
                key = key[len("Default") :]
            if key.endswith("Form"):
                key = key[: -len("Form")]
            key = key or tag
            # camel → snake-ish for JSON
            key_l = {
                "Object": "object",
                "Folder": "folder",
                "List": "list",
                "Choice": "choice",
                "FolderChoice": "folder_choice",
                "Record": "record",
                "RecordSet": "record_set",
                "Load": "load",
            }.get(key, key.lower())
            out[key_l] = short
    return out


def _ru_synonym_from_xml(text: str) -> str:
    m = re.search(
        r"<Synonym>\s*<v8:item>\s*<v8:lang>ru</v8:lang>\s*"
        r"<v8:content>([^<]*)</v8:content>",
        text,
        re.S,
    )
    if m:
        return m.group(1).strip()
    # first ru content as fallback
    m2 = re.search(
        r"<v8:lang>ru</v8:lang>\s*<v8:content>([^<]*)</v8:content>",
        text,
        re.S,
    )
    return m2.group(1).strip() if m2 else ""


def read_child_meta_brief(
    obj_dir: Path,
    kind: str,
    name: str,
) -> dict[str, str]:
    """
    Синоним/тип дочернего объекта из Forms|Templates|Commands/<name>.xml.
    kind: Form | Template | Command
    """
    folder = {
        "Form": "Forms",
        "Template": "Templates",
        "Command": "Commands",
    }.get(kind, kind)
    meta_file = obj_dir / folder / f"{name}.xml"
    info: dict[str, str] = {"name": name}
    if not meta_file.is_file():
        return info
    try:
        text = meta_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return info
    syn = _ru_synonym_from_xml(text)
    if syn:
        info["synonym"] = syn
    if kind == "Form":
        m = re.search(r"<FormType>([^<]+)</FormType>", text)
        if m:
            info["form_type"] = m.group(1).strip()
    elif kind == "Template":
        m = re.search(r"<TemplateType>([^<]+)</TemplateType>", text)
        if m:
            info["template_type"] = m.group(1).strip()
    return info


def merge_unique_names(*lists: list[str]) -> list[str]:
    """Объединить списки имён, сохранив порядок первого вхождения (как в конфиге)."""
    seen: set[str] = set()
    out: list[str] = []
    for lst in lists:
        for n in lst or []:
            n = str(n).strip()
            if not n or n in seen:
                continue
            seen.add(n)
            out.append(n)
    return out


def pick_main_form_name(
    form_names: list[str],
    default_forms: dict[str, str] | None = None,
    preferred: str = "",
) -> str:
    """
    Выбрать форму для layout: явный preferred → defaults из метаданных → первая из списка.
    Имена только из configuration (form_names / default_forms), без жёсткого списка.
    """
    names = [str(x) for x in (form_names or []) if x]
    if preferred and preferred in names:
        return preferred
    if preferred and preferred:
        # caller asked for a name that might still exist on disk
        return preferred
    df = default_forms or {}
    for key in (
        "object",
        "record",
        "list",
        "choice",
        "folder",
        "record_set",
        "load",
        "folder_choice",
    ):
        cand = df.get(key) or ""
        if cand and (not names or cand in names):
            return cand
        if cand and names and cand not in names:
            # still return — dump may have form under that name
            return cand
    return names[0] if names else ""


def build_children_catalog(
    full_name: str,
    obj_dir: Path | None,
    *,
    form_names: list[str],
    template_names: list[str],
    command_names: list[str],
    default_forms: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Полный каталог forms/templates/commands с именами ИЗ КОНФИГУРАЦИИ
    (+ synonym, type если есть в dump).
    """
    forms: list[dict[str, Any]] = []
    for n in form_names:
        row: dict[str, Any] = {
            "name": n,
            "full_name": f"{full_name}.Форма.{n}",
        }
        if obj_dir:
            brief = read_child_meta_brief(obj_dir, "Form", n)
            if brief.get("synonym"):
                row["synonym"] = brief["synonym"]
            if brief.get("form_type"):
                row["form_type"] = brief["form_type"]
        # mark if used as default
        if default_forms:
            roles = [k for k, v in default_forms.items() if v == n]
            if roles:
                row["default_for"] = roles
        forms.append(row)

    templates: list[dict[str, Any]] = []
    for n in template_names:
        row = {
            "name": n,
            "full_name": f"{full_name}.Макет.{n}",
        }
        if obj_dir:
            brief = read_child_meta_brief(obj_dir, "Template", n)
            if brief.get("synonym"):
                row["synonym"] = brief["synonym"]
            if brief.get("template_type"):
                row["template_type"] = brief["template_type"]
        templates.append(row)

    commands: list[dict[str, Any]] = []
    for n in command_names:
        row = {"name": n, "full_name": f"{full_name}.Команда.{n}"}
        if obj_dir:
            brief = read_child_meta_brief(obj_dir, "Command", n)
            if brief.get("synonym"):
                row["synonym"] = brief["synonym"]
        commands.append(row)

    return {
        "forms": forms,
        "templates": templates,
        "commands": commands,
        "form_names": [f["name"] for f in forms],
        "template_names": [t["name"] for t in templates],
        "command_names": [c["name"] for c in commands],
        "forms_count": len(forms),
        "templates_count": len(templates),
        "commands_count": len(commands),
        "default_forms": default_forms or {},
        "note_children": (
            "Имена forms/templates/commands — фактические из конфигурации "
            "(любые, не только «ФормаДокумента»). "
            "Layout: describe_form(full_name, form_name=forms[].name). "
            "default_forms — назначенные в конфигураторе Default*Form."
        ),
    }


def read_form_layout(
    full_name: str,
    form_name: str,
    *,
    code_dir: str | Path | None = None,
    max_elements: int = 80,
    tab: str = "",
) -> dict[str, Any]:
    """
    Раскладка формы из dump — управляемой (Form.xml) и обычной (Form.bin):
    вкладки → элементы (тип, подпись, путь к данным, события), кнопки → процедуры, события формы.
    """
    from tools.form_info import compact_form_layout, load_form_layout

    obj_dir = dump_object_dir(full_name, code_dir)
    if not obj_dir:
        return {
            "error": f"Папка объекта в dump не найдена: {full_name}",
            "hint": "Нужна выгрузка конфигурации (Forms/).",
        }
    lay = load_form_layout(obj_dir, form_name, full_name=full_name)
    if lay.get("error"):
        return {**lay, "path_tried": str(obj_dir / "Forms" / form_name)}

    rel = str(lay.get("form_path") or "")
    try:
        cr = resolve_code_roots(code_dir)
        if cr.roots:
            rel = str(Path(rel).relative_to(cr.roots[0])).replace("\\", "/")
    except Exception:
        pass
    els = lay.get("elements") or []
    dps = list(dict.fromkeys(str(e["data_path"]) for e in els if e.get("data_path")))
    out: dict[str, Any] = {
        "ok": True,
        "full_name": full_name,
        "form_name": form_name,
        "form_full_name": f"{full_name}.Форма.{form_name}",
        "path": rel,
        **compact_form_layout(lay, tab=tab),
        "data_paths": dps[:max_elements],
        "data_paths_count": len(dps),
        "prefer": (
            "Отвечай по tabs (вкладки → элементы: подпись [тип] имя → путь к данным; событие→процедура), "
            "buttons (подпись на экране → процедура модуля формы), form_events. "
            "Тело процедуры — proc_index(procedure=…) / read_code. Не выдумывай элементы, которых нет в tabs."
        ),
        "note": (
            "Обычная форма (Form.bin)." if lay.get("kind") == "ordinary" else "Управляемая форма (Form.xml)."
        ) + " Раскладка из выгрузки (не runtime): видимость, меняемая кодом, не учтена.",
    }
    return out


def subsystem_content(
    full_name: str,
    *,
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Состав подсистемы из dump XML Content + дочерние подсистемы."""
    pref, rest = parse_full_name(full_name)
    if pref != "Подсистема" and not full_name.startswith("Подсистема"):
        # allow bare
        if "." not in full_name:
            rest = full_name
            full_name = f"Подсистема.{full_name}"
        else:
            return {"error": "Нужно Подсистема.Имя", "got": full_name}

    cr = resolve_code_roots(code_dir)
    if not cr.roots:
        return {"error": "Нет выгрузки конфигурации"}

    # path: Subsystems/A.xml and Subsystems/A/
    parts = rest.split(".") if rest else full_name.replace("Подсистема.", "").split(".")
    content: list[str] = []
    synonym = ""
    children: list[dict[str, str]] = []

    for root in cr.roots:
        # meta xml for top-level or nested
        if len(parts) == 1:
            meta_xml = root / "Subsystems" / f"{parts[0]}.xml"
            sub_dir = root / "Subsystems" / parts[0]
        else:
            # nested: Subsystems/A/Subsystems/B.xml
            p = root / "Subsystems" / parts[0]
            for part in parts[1:-1]:
                p = p / "Subsystems" / part
            meta_xml = p / "Subsystems" / f"{parts[-1]}.xml"
            if not meta_xml.is_file():
                meta_xml = p / f"{parts[-1]}.xml"
            sub_dir = p / "Subsystems" / parts[-1]
            if not sub_dir.is_dir():
                sub_dir = p / parts[-1]

        if meta_xml.is_file():
            try:
                text = meta_xml.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            m = re.search(
                r"<v8:lang>ru</v8:lang>\s*<v8:content>([^<]+)</v8:content>",
                text,
            )
            if m:
                synonym = m.group(1).strip()
            # Content refs
            for item in re.findall(
                r"<xr:Item[^>]*>([^<]+)</xr:Item>", text
            ):
                content.append(item.strip())
            # sometimes without Item wrapper
            for item in re.findall(
                r"<Content>\s*<xr:Item[^>]*xsi:type=\"xr:MDObjectRef\">([^<]+)</xr:Item>",
                text,
            ):
                if item.strip() not in content:
                    content.append(item.strip())

        if sub_dir.is_dir():
            nested = sub_dir / "Subsystems"
            if nested.is_dir():
                for ch in sorted(nested.iterdir(), key=lambda x: x.name.lower()):
                    if ch.is_dir() and ch.name != "Ext":
                        children.append(
                            {
                                "name": ch.name,
                                "full_name": f"Подсистема.{'.'.join(parts + [ch.name])}",
                            }
                        )
            break

    # group content by type
    by_type: dict[str, list[str]] = {}
    for c in content:
        t = c.split(".", 1)[0] if "." in c else "Other"
        by_type.setdefault(t, []).append(c)

    return {
        "ok": True,
        "full_name": full_name if full_name.startswith("Подсистема") else f"Подсистема.{rest}",
        "synonym": synonym,
        "content_count": len(content),
        "content": content[:200],
        "content_by_type": {k: v for k, v in by_type.items()},
        "child_subsystems": children,
        "source": "config_dump",
        "prefer": (
            "Покажи synonym, child_subsystems и content_by_type "
            "(Report/Document/Catalog/…). "
            "Детали объекта: describe_object / get_object_structure."
        ),
        "truncated": len(content) > 200,
    }


def _has_attr_list(structure: dict[str, Any]) -> bool:
    for key in (
        "attributes",
        "Attributes",
        "Реквизиты",
        "fields",
        "dimensions",
        "resources",
    ):
        v = structure.get(key)
        if isinstance(v, list) and len(v) > 0:
            return True
        if isinstance(v, dict) and len(v) > 0:
            return True
    return False


def _has_ts_list(structure: dict[str, Any]) -> bool:
    for key in ("tabular_sections", "TabularSections", "ТабличныеЧасти"):
        v = structure.get(key)
        if isinstance(v, list) and len(v) > 0:
            return True
    return False


def enrich_from_dump(
    full_name: str,
    structure: dict[str, Any] | None = None,
    *,
    code_dir: str | Path | None = None,
    include_form_layout: bool = False,
    form_name: str = "",
) -> dict[str, Any]:
    """
    Дополнить structure из dump:
    реквизиты/ТЧ/измерения (ObjectName.xml), forms/templates/commands, layout Form.xml.
    """
    out = dict(structure or {})
    out.setdefault("full_name", full_name)
    pref, _ = parse_full_name(full_name)

    if pref == "Подсистема" or full_name.startswith("Подсистема."):
        sub = subsystem_content(full_name, code_dir=code_dir)
        if sub.get("ok"):
            out["subsystem"] = sub
            out["content"] = sub.get("content")
            out["content_by_type"] = sub.get("content_by_type")
            out["child_subsystems"] = sub.get("child_subsystems")
            if sub.get("synonym") and not out.get("synonym"):
                out["synonym"] = sub["synonym"]
            out.pop("error", None)
        return out

    # --- meta XML: attributes / tabular / dimensions / resources ---
    meta = read_object_meta_xml(full_name, code_dir=code_dir)
    if meta.get("ok"):
        if meta.get("synonym") and not out.get("synonym"):
            out["synonym"] = meta["synonym"]
        # fill attributes if HTTP empty or incomplete
        if not _has_attr_list(out) and meta.get("attributes"):
            out["attributes"] = meta["attributes"]
            out["Реквизиты"] = meta["attributes"]
        if not _has_ts_list(out) and meta.get("tabular_sections"):
            out["tabular_sections"] = meta["tabular_sections"]
            out["ТабличныеЧасти"] = meta["tabular_sections"]
        if not out.get("dimensions") and meta.get("dimensions"):
            out["dimensions"] = meta["dimensions"]
        if not out.get("resources") and meta.get("resources"):
            out["resources"] = meta["resources"]
        if not out.get("enum_values") and meta.get("enum_values"):
            out["enum_values"] = meta["enum_values"]
        # if HTTP had only error — clear it when dump has data
        if out.get("error") and (
            meta.get("attributes")
            or meta.get("tabular_sections")
            or meta.get("dimensions")
            or meta.get("resources")
            or meta.get("enum_values")
        ):
            out.pop("error", None)
            out["source"] = "config_dump_xml"
        out["meta_path"] = meta.get("meta_path")

    # --- names from HTTP (if any) — may be partial; dump is authoritative union ---
    def _names_from_structure(key: str) -> list[str]:
        raw = out.get(key) or []
        names: list[str] = []
        if isinstance(raw, list):
            for x in raw:
                if isinstance(x, dict):
                    n = str(x.get("name") or x.get("Name") or x.get("Имя") or "").strip()
                else:
                    n = str(x).strip()
                if n:
                    names.append(n)
        return names

    http_forms = _names_from_structure("forms")
    http_templates = _names_from_structure("templates")
    http_commands = _names_from_structure("commands")

    default_forms: dict[str, str] = {}
    if meta.get("ok") and meta.get("default_forms"):
        default_forms = dict(meta["default_forms"])

    obj_dir = dump_object_dir(full_name, code_dir)
    meta_forms = list(meta.get("forms_xml") or []) if meta.get("ok") else []
    meta_templates = list(meta.get("templates_xml") or []) if meta.get("ok") else []
    meta_commands = list(meta.get("commands_xml") or []) if meta.get("ok") else []

    fs_forms: list[str] = []
    fs_templates: list[str] = []
    fs_commands: list[str] = []
    if obj_dir:
        fs_forms = list_dir_children(obj_dir, "Forms")
        fs_templates = list_dir_children(obj_dir, "Templates")
        fs_commands = list_dir_children(obj_dir, "Commands")
        out["dump_path"] = str(obj_dir)

    # order: config XML order first (как в конфигураторе), then folder, then HTTP
    form_names = merge_unique_names(meta_forms, fs_forms, http_forms)
    template_names = merge_unique_names(meta_templates, fs_templates, http_templates)
    command_names = merge_unique_names(meta_commands, fs_commands, http_commands)

    if not obj_dir and not form_names and not template_names and not meta.get("ok"):
        out.setdefault("dump_note", "Папка объекта в dump не найдена")
        return out

    catalog = build_children_catalog(
        full_name,
        obj_dir,
        form_names=form_names,
        template_names=template_names,
        command_names=command_names,
        default_forms=default_forms,
    )
    # always overwrite with full config catalog (custom names included)
    out["forms"] = catalog["forms"]
    out["templates"] = catalog["templates"]
    out["commands"] = catalog["commands"]
    out["form_names"] = catalog["form_names"]
    out["template_names"] = catalog["template_names"]
    out["command_names"] = catalog["command_names"]
    out["forms_count"] = catalog["forms_count"]
    out["templates_count"] = catalog["templates_count"]
    out["commands_count"] = catalog["commands_count"]
    if default_forms:
        out["default_forms"] = default_forms
    out["note_children"] = catalog["note_children"]

    # modules present
    if obj_dir:
        modules = []
        for mod in (
            "ObjectModule.bsl",
            "ManagerModule.bsl",
            "RecordSetModule.bsl",
            "CommandModule.bsl",
        ):
            if (obj_dir / "Ext" / mod).is_file():
                modules.append(mod)
        if modules:
            out["modules"] = modules

    if include_form_layout:
        chosen = pick_main_form_name(
            form_names, default_forms, preferred=(form_name or "").strip()
        )
        if chosen:
            out["form_layout"] = read_form_layout(
                full_name, chosen, code_dir=code_dir
            )
            out["form_layout_of"] = chosen
        elif form_name:
            out["form_layout"] = {
                "error": f"Форма «{form_name}» не найдена",
                "available_forms": form_names,
                "hint": "form_name из forms[].name / form_names этой конфигурации",
            }

    return out
