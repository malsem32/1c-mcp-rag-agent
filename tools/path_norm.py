"""
Нормализация путей к модулям конфигурации 1С (одна функция для всех tools кода).

Принимает:
  - нотацию 1С: «Справочник.X.Форма.Y», «Справочник.X.Форма.Y.Модуль»,
    «Документ.X.МодульОбъекта», «ОбщийМодуль.X», «ОбщаяФорма.X», «Отчет.X.Макет.Y»…
  - путь выгрузки (полный или «без Ext»): «Catalogs/X/Forms/Y», «Catalogs/X/Forms/Y/Form.xml»,
    «Documents/X/ObjectModule.bsl», «CommonModules/X», «Справочники/X/Формы/Y»…
  - абсолютный путь к dump (D:/conf/Catalogs/…) — префикс отрезается.

Возвращает упорядоченный список реальных source_path (как в Qdrant/выгрузке).
Класс-маппинг рус↔англ берётся из code_search.DUMP_TYPES (без hardcode объектов CF).
Внешние .epf/.erf/.cfe здесь не обрабатываются (их перехватывают раньше).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

_EXTERNAL_RE = re.compile(r"\.(epf|erf|cfe)\b", re.I)

# английские единственные числа (нотация 1С на англ.)
_EN_SINGULAR = {
    "Catalogs": "Catalog",
    "Documents": "Document",
    "DocumentJournals": "DocumentJournal",
    "Enums": "Enum",
    "Reports": "Report",
    "DataProcessors": "DataProcessor",
    "InformationRegisters": "InformationRegister",
    "AccumulationRegisters": "AccumulationRegister",
    "AccountingRegisters": "AccountingRegister",
    "CalculationRegisters": "CalculationRegister",
    "ChartsOfAccounts": "ChartOfAccounts",
    "ChartsOfCharacteristicTypes": "ChartOfCharacteristicTypes",
    "ChartsOfCalculationTypes": "ChartOfCalculationTypes",
    "BusinessProcesses": "BusinessProcess",
    "Tasks": "Task",
    "ExchangePlans": "ExchangePlan",
    "FilterCriteria": "FilterCriterion",
    "CommonForms": "CommonForm",
    "CommonModules": "CommonModule",
    "CommonCommands": "CommonCommand",
    "CommonTemplates": "CommonTemplate",
    "Constants": "Constant",
    "SettingsStorages": "SettingsStorage",
    "WebServices": "WebService",
    "HTTPServices": "HTTPService",
}

# под-папки объекта
_SUB_FORMS = {"forms", "form", "формы", "форма"}
_SUB_COMMANDS = {"commands", "command", "команды", "команда"}
_SUB_TEMPLATES = {"templates", "template", "макеты", "макет"}

# имена модулей → роль
_ROLE_BY_TOKEN = {
    "objectmodule": "object",
    "модульобъекта": "object",
    "managermodule": "manager",
    "модульменеджера": "manager",
    "recordsetmodule": "recordset",
    "модульнаборазаписей": "recordset",
    "valuemanagermodule": "valuemanager",
    "модульменеджеразначения": "valuemanager",
    "commandmodule": "command_module",
    "модулькоманды": "command_module",
    "module": "module",
    "модуль": "module",
    "модульформы": "module",
    "formmodule": "module",
}

_ROLE_FILE = {
    "object": "ObjectModule.bsl",
    "manager": "ManagerModule.bsl",
    "recordset": "RecordSetModule.bsl",
    "valuemanager": "ValueManagerModule.bsl",
}


def _low(s: str) -> str:
    return (s or "").strip().lower().replace("ё", "е")


_CLASS_MAP: dict[str, str] | None = None


def class_map() -> dict[str, str]:
    """lower(рус/англ, ед./мн. число) → английская папка выгрузки."""
    global _CLASS_MAP
    if _CLASS_MAP is not None:
        return _CLASS_MAP
    from tools.code_search import DUMP_TYPES

    m: dict[str, str] = {}
    for en, info in DUMP_TYPES.items():
        if en == "Ext":
            continue
        keys = [en, str(info.get("ru") or ""), str(info.get("prefix") or "")]
        keys.extend(str(a) for a in (info.get("aliases") or ()))
        if en in _EN_SINGULAR:
            keys.append(_EN_SINGULAR[en])
        for k in keys:
            if k:
                m.setdefault(_low(k), en)
    _CLASS_MAP = m
    return m


def class_prefix_1c(class_en: str) -> str:
    from tools.code_search import DUMP_TYPES

    return str((DUMP_TYPES.get(class_en) or {}).get("prefix") or class_en)


@dataclass
class CodePathSpec:
    class_en: str
    object_name: str
    form_name: str = ""
    command_name: str = ""
    template_name: str = ""
    # object / manager / recordset / valuemanager / module / form_module / form_xml /
    # form_any / object_xml / command_module / template / ""(любой модуль объекта)
    role: str = ""
    source: str = ""  # "1c" | "dump"
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def base(self) -> str:
        return f"{self.class_en}/{self.object_name}"

    @property
    def object_full_name(self) -> str:
        return f"{class_prefix_1c(self.class_en)}.{self.object_name}"

    @property
    def full_name_1c(self) -> str:
        fn = self.object_full_name
        if self.form_name and self.class_en != "CommonForms":
            fn += f".Форма.{self.form_name}"
        return fn

    def as_dict(self) -> dict[str, Any]:
        return {
            "class": self.class_en,
            "object_name": self.object_name,
            "object_full_name": self.object_full_name,
            "form_name": self.form_name or None,
            "command_name": self.command_name or None,
            "template_name": self.template_name or None,
            "role": self.role or None,
            "full_name_1c": self.full_name_1c,
        }


def is_external_path(raw: str) -> bool:
    return bool(_EXTERNAL_RE.search(raw or ""))


def _strip_file_ext(name: str) -> str:
    return re.sub(r"\.(xml|bsl|bin|os|txt)$", "", name or "", flags=re.I)


def _parse_tail(spec: CodePathSpec, rest: list[str]) -> CodePathSpec:
    """rest — сегменты после имени объекта (без «Ext»)."""
    rest = [r for r in rest if r and _low(r) != "ext"]
    if not rest:
        return spec
    head = _low(rest[0])
    if spec.class_en == "CommonForms":
        last = _low(rest[-1])
        spec.role = "form_xml" if last == "form.xml" else "form_module"
        return spec
    if head in _SUB_FORMS and len(rest) >= 2:
        spec.form_name = _strip_file_ext(rest[1])
        tail = [_low(x) for x in rest[2:]]
        if not tail or tail[-1] == "form.bin":
            spec.role = "form_any"
        elif tail[-1] == "form.xml":
            spec.role = "form_xml"
        else:
            spec.role = "form_module"
        return spec
    if head in _SUB_COMMANDS and len(rest) >= 2:
        spec.command_name = _strip_file_ext(rest[1])
        spec.role = "command_module"
        return spec
    if head in _SUB_TEMPLATES and len(rest) >= 2:
        spec.template_name = _strip_file_ext(rest[1])
        spec.role = "template"
        return spec
    role = _ROLE_BY_TOKEN.get(_low(_strip_file_ext(rest[-1])))
    if role:
        spec.role = role
        return spec
    spec.extras["unparsed_tail"] = "/".join(rest)
    return spec


def _strip_root_prefix(t: str) -> str:
    """D:/conf_test/Catalogs/X/… → Catalogs/X/… (первая известная папка класса)."""
    t = t.replace("\\", "/").strip().strip("\"'")
    while t.startswith("./"):
        t = t[2:]
    for marker in ("knowledge/код_конфигурации/", "код_конфигурации/"):
        if marker in t:
            t = t.split(marker, 1)[-1]
    parts = [p for p in t.split("/") if p]
    cmap = class_map()
    for i, p in enumerate(parts):
        if _low(p) in cmap and i + 1 < len(parts):
            return "/".join(parts[i:])
    return "/".join(parts)


def parse_code_path(raw: str) -> CodePathSpec | None:
    """Разобрать путь/имя 1С → CodePathSpec. None — не похоже на путь объекта."""
    t = (raw or "").strip()
    if not t or is_external_path(t):
        return None
    cmap = class_map()
    if "/" in t or "\\" in t:
        t = _strip_root_prefix(t)
        parts = [p for p in t.split("/") if p]
        if len(parts) < 2:
            return None
        cls = cmap.get(_low(parts[0]))
        if not cls:
            return None
        obj = parts[1]
        rest = parts[2:]
        role = ""
        if re.search(r"\.xml$", obj, re.I) and not rest:
            role = "object_xml"
        obj = _strip_file_ext(obj)
        if not obj:
            return None
        spec = CodePathSpec(class_en=cls, object_name=obj, role=role, source="dump")
        return _parse_tail(spec, rest)
    # нотация 1С через точку: «Справочник.X…» / «Catalog.X…» / «Справочники.X…»
    toks = [x.strip() for x in t.split(".") if x.strip()]
    if len(toks) < 2:
        return None
    cls = cmap.get(_low(toks[0]))
    if not cls:
        return None
    obj = toks[1]
    if not re.match(r"^[\wА-Яа-яЁё]+$", obj):
        return None
    spec = CodePathSpec(class_en=cls, object_name=obj, source="1c")
    rest = toks[2:]
    # «…Форма.Y.Form.xml» → точки разбили имя файла: склеить
    if len(rest) >= 2 and _low(rest[-1]) in ("xml", "bsl", "bin"):
        rest = rest[:-2] + [f"{rest[-2]}.{rest[-1]}"]
    return _parse_tail(spec, rest)


def candidate_paths(spec: CodePathSpec) -> list[str]:
    """Реальные source_path по убыванию соответствия (только тот же объект/форма/роль)."""
    b = spec.base
    cls = spec.class_en
    if cls == "CommonForms":
        fm, fx = f"{b}/Ext/Form/Module.bsl", f"{b}/Ext/Form.xml"
        out = [fx, fm] if spec.role == "form_xml" else [fm, fx]
        return out + [f"{b}.xml"]
    if cls == "CommonModules":
        out = [f"{b}/Ext/Module.bsl", f"{b}.xml"]
        return list(reversed(out)) if spec.role == "object_xml" else out
    if cls == "CommonCommands":
        return [f"{b}/Ext/CommandModule.bsl", f"{b}.xml"]
    if spec.form_name:
        f = f"{b}/Forms/{spec.form_name}"
        fm, fx = f"{f}/Ext/Form/Module.bsl", f"{f}/Ext/Form.xml"
        out = [fx, fm] if spec.role == "form_xml" else [fm, fx]
        return out + [f"{f}.xml"]
    if spec.command_name:
        return [f"{b}/Commands/{spec.command_name}/Ext/CommandModule.bsl"]
    if spec.template_name:
        t = f"{b}/Templates/{spec.template_name}"
        return [f"{t}/Ext/Template.xml", f"{t}.xml"]
    if spec.role in _ROLE_FILE:
        return [f"{b}/Ext/{_ROLE_FILE[spec.role]}"]
    if spec.role == "object_xml":
        return [f"{b}.xml"]
    out = [f"{b}/Ext/{fn}" for fn in ("ObjectModule.bsl", "ManagerModule.bsl", "RecordSetModule.bsl", "ValueManagerModule.bsl")]
    if not spec.role:
        out.append(f"{b}.xml")
    return out


def canonical_path(raw: str) -> str:
    """Первый кандидат без проверки индекса (для логов/подсказок); '' если не разобрано."""
    spec = parse_code_path(raw)
    if not spec:
        return ""
    c = candidate_paths(spec)
    return c[0] if c else ""


# ---------------------------------------------------------------------------
# Qdrant-aware resolve
# ---------------------------------------------------------------------------
def _qdrant_exists(path: str) -> bool:
    from vector.catalog import get_chunks_by_source_path

    try:
        return bool(get_chunks_by_source_path(path))
    except Exception:
        return False


def list_object_paths(object_name: str, class_en: str = "", *, limit: int = 3000) -> list[str]:
    """Все source_path объекта в Qdrant (payload object_name — keyword index)."""
    if not object_name:
        return []
    try:
        from qdrant_client.http import models as qm

        from vector.catalog import _cfg, _scroll_payload
        from vector.client import collection_name, get_qdrant

        cfg = _cfg(None)
        rows = _scroll_payload(
            get_qdrant(cfg),
            collection_name("code", cfg),
            scroll_filter=qm.Filter(
                must=[qm.FieldCondition(key="object_name", match=qm.MatchValue(value=object_name))]
            ),
            limit=limit,
            max_pages=80,
        )
    except Exception:
        return []
    seen: dict[str, None] = {}
    for r in rows:
        sp = str(r.get("source_path") or "").replace("\\", "/")
        if not sp:
            continue
        if class_en and not (sp.startswith(f"{class_en}/{object_name}/") or sp == f"{class_en}/{object_name}.xml"):
            continue
        seen.setdefault(sp, None)
    return list(seen)


def _rank_suggestions(paths: Iterable[str], spec: CodePathSpec) -> list[str]:
    fl = _low(spec.form_name)

    def key(p: str) -> tuple:
        pl = _low(p)
        same_form = 0 if fl and f"/forms/{fl}/" in pl else 1
        near_form = 0 if fl and "/forms/" in pl and fl[:8] in pl else 1
        is_form = 0 if (fl and "/forms/" in pl) else 1
        is_bsl = 0 if pl.endswith(".bsl") else 1
        return (same_form, near_form, is_form, is_bsl, len(p), p)

    return sorted(set(paths), key=key)


def _by_name_suggestions(spec: CodePathSpec, limit: int = 5) -> list[str]:
    try:
        from vector.catalog import find_modules_qdrant

        q = " ".join(x for x in (spec.object_name, spec.form_name) if x)
        found = find_modules_qdrant(q, limit=10, form_name=spec.form_name, object_name=spec.object_name)
        out: list[str] = []
        for m in found.get("modules") or []:
            p = str(m.get("path") or "").replace("\\", "/")
            if p and p not in out:
                out.append(p)
        return out[:limit]
    except Exception:
        return []


def resolve_code_path(
    raw: str,
    *,
    exists: Callable[[str], bool] | None = None,
    list_object: Callable[[str, str], list[str]] | None = None,
    by_name: Callable[[CodePathSpec], list[str]] | None = None,
    max_suggestions: int = 5,
) -> dict[str, Any]:
    """
    Путь/имя → реальный source_path, без подмены на чужой объект.

    Возврат:
      path         — найденный source_path ("" если нет)
      exact        — найден ровно запрошенный путь (после стрипа префиксов)
      spec         — разбор (dict) или None (не похоже на путь объекта)
      did_you_mean — ≤5 путей ТОГО ЖЕ объекта (иначе — по имени)
      object_found — объект есть в индексе (но нужного модуля/формы нет)
    """
    exists = exists or _qdrant_exists
    list_object = list_object or (lambda o, c: list_object_paths(o, c))
    by_name = by_name or (lambda s: _by_name_suggestions(s, max_suggestions))
    t = (raw or "").strip()
    res: dict[str, Any] = {"requested": t, "path": "", "exact": False, "spec": None, "did_you_mean": []}
    if not t or is_external_path(t):
        return res
    norm = _strip_root_prefix(t) if ("/" in t or "\\" in t) else t
    res["normalized"] = norm
    if "/" in norm and exists(norm):
        spec0 = parse_code_path(norm)
        res.update(path=norm, exact=True, spec=spec0.as_dict() if spec0 else None)
        return res
    spec = parse_code_path(t)
    if spec is None:
        return res
    res["spec"] = spec.as_dict()
    for cand in candidate_paths(spec):
        if exists(cand):
            res["path"] = cand
            return res
    obj_paths = list_object(spec.object_name, spec.class_en)
    named: list[str] | None = None
    if not obj_paths:
        # регистр имени («контрагенты» → «Контрагенты»): только то же имя без учёта регистра
        named = by_name(spec)
        for p in named:
            parts = p.split("/")
            if len(parts) < 2 or parts[0] != spec.class_en:
                continue
            real = _strip_file_ext(parts[1])
            if _low(real) == _low(spec.object_name) and real != spec.object_name:
                spec.object_name = real
                res["spec"] = spec.as_dict()
                for cand in candidate_paths(spec):
                    if exists(cand):
                        res["path"] = cand
                        return res
                obj_paths = list_object(spec.object_name, spec.class_en)
                break
    if obj_paths:
        res["object_found"] = True
        res["did_you_mean"] = _rank_suggestions(obj_paths, spec)[:max_suggestions]
    else:
        res["object_found"] = False
        res["did_you_mean"] = list(named if named is not None else by_name(spec))[:max_suggestions]
    return res


def not_found_payload(raw: str, res: dict[str, Any]) -> dict[str, Any]:
    """Единый ответ ok:false, когда путь не разрешён (не отдаём чужой объект)."""
    spec = res.get("spec") or {}
    fn = spec.get("full_name_1c") or ""
    if spec and res.get("object_found"):
        what = f"формы «{spec.get('form_name')}»" if spec.get("form_name") else "запрошенного модуля"
        err = f"В индексе Qdrant нет {what} объекта {spec.get('object_full_name')} (path: {raw})."
    elif spec:
        err = f"Объект {spec.get('object_full_name')} не найден в индексе Qdrant (path: {raw})."
    else:
        err = f"В Qdrant нет path: {raw}"
    out: dict[str, Any] = {
        "ok": False,
        "error": err,
        "source": "qdrant",
        "requested_path": raw,
        "did_you_mean": res.get("did_you_mean") or [],
    }
    if spec:
        out["parsed"] = spec
    hint = "Возьми path из did_you_mean / find_modules. Не подставляй код другого объекта."
    if spec.get("form_name") and res.get("object_found"):
        hint = (
            "Форма не проиндексирована как код (вероятно обычная форма Form.bin). "
            f"Состав формы: describe_object(full_name='{spec.get('object_full_name')}', "
            f"form_name='{spec.get('form_name')}'); код объекта — path из did_you_mean."
        )
        out["suggested_tools"] = ["describe_object"]
    out["hint"] = hint
    out["prefer"] = (
        "Скажи честно, что такого модуля/формы нет в индексе; предложи did_you_mean. "
        "НЕ отвечай кодом другого объекта."
    )
    return out
