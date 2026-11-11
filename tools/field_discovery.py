"""
Обнаружение полей объекта 1С из метаданных конфигурации.

Никаких «настоящих» имён полей из hardcode — только то, что вернул
get_object_structure / HTTP /object. Списки ROLE_HINTS — это ключи поиска
(как ищет менеджер: «сумма», «контрагент»), которыми матчим имя и синоним
реквизита в ТЕКУЩЕЙ конфигурации.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class FieldInfo:
    name: str
    synonym: str = ""
    type_str: str = ""
    kind: str = "attribute"  # standard | attribute | dimension | resource | tabular
    tabular_section: str = ""

    @property
    def search_blob(self) -> str:
        return f"{self.name} {self.synonym} {self.type_str}".lower()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "synonym": self.synonym,
            "type": self.type_str,
            "kind": self.kind,
        }
        if self.tabular_section:
            d["tabular_section"] = self.tabular_section
        return d


# Роли из knowledge/field_roles.yaml (не хардкод — правьте YAML)


def _roles_pack() -> dict:
    from kb.store import get_field_roles

    return get_field_roles()


def _role_hints() -> dict[str, list[str]]:
    return _roles_pack().get("roles") or {}


def _document_header_roles() -> list[str]:
    return list(_roles_pack().get("document_header_roles") or [])


def __getattr__(name: str):
    # ROLE_HINTS / DOCUMENT_HEADER_ROLES — из knowledge/field_roles.yaml
    if name == "ROLE_HINTS":
        return _role_hints()
    if name == "DOCUMENT_HEADER_ROLES":
        hdr = _document_header_roles()
        return hdr or [
            "number",
            "date",
            "posted",
            "counterparty",
            "organization",
            "contract",
            "warehouse",
            "amount",
            "currency",
            "responsible",
            "status",
            "comment",
            "deletion_mark",
        ]
    raise AttributeError(name)

CATALOG_HEADER_ROLES = [
    "code",
    "description",
    "article",
    "inn",
    "kpp",
    "is_folder",
    "deletion_mark",
]


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _field_from_dict(item: dict[str, Any], *, kind: str = "attribute", ts: str = "") -> FieldInfo | None:
    name = _as_str(
        item.get("name")
        or item.get("Name")
        or item.get("Имя")
        or item.get("ИмяРеквизита")
        or item.get("field")
    )
    if not name:
        return None
    synonym = _as_str(
        item.get("synonym") or item.get("Synonym") or item.get("Синоним") or item.get("presentation")
    )
    type_str = _as_str(
        item.get("type") or item.get("Type") or item.get("Тип") or item.get("type_str")
    )
    kind = _as_str(item.get("kind") or item.get("Kind") or kind) or kind
    return FieldInfo(
        name=name,
        synonym=synonym,
        type_str=type_str,
        kind=kind,
        tabular_section=ts,
    )


def parse_structure_fields(structure: Any) -> list[FieldInfo]:
    """Разбирает ответ /object (или похожий JSON) в список полей конфигурации."""
    if not isinstance(structure, dict):
        return []
    if structure.get("error"):
        return []

    fields: list[FieldInfo] = []
    seen: set[str] = set()

    def add(fi: FieldInfo | None) -> None:
        if not fi or not fi.name:
            return
        key = f"{fi.tabular_section}.{fi.name}" if fi.tabular_section else fi.name
        if key in seen:
            return
        seen.add(key)
        fields.append(fi)

    def from_list(items: Any, kind: str, ts: str = "") -> None:
        if isinstance(items, list):
            for item in items:
                if isinstance(item, str):
                    add(FieldInfo(name=item, kind=kind, tabular_section=ts))
                elif isinstance(item, dict):
                    add(_field_from_dict(item, kind=kind, ts=ts))
        elif isinstance(items, dict):
            # { "Контрагент": "СправочникСсылка..." } или { "Контрагент": {...} }
            for k, v in items.items():
                if isinstance(v, dict):
                    fi = _field_from_dict({**v, "name": v.get("name") or k}, kind=kind, ts=ts)
                    add(fi)
                else:
                    add(FieldInfo(name=str(k), type_str=_as_str(v), kind=kind, tabular_section=ts))

    for key, kind in (
        ("attributes", "attribute"),
        ("Attributes", "attribute"),
        ("Реквизиты", "attribute"),
        ("standard_attributes", "standard"),
        ("StandardAttributes", "standard"),
        ("СтандартныеРеквизиты", "standard"),
        ("fields", "attribute"),
        ("Fields", "attribute"),
        ("columns", "attribute"),
        ("dimensions", "dimension"),
        ("Dimensions", "dimension"),
        ("Измерения", "dimension"),
        ("resources", "resource"),
        ("Resources", "resource"),
        ("Ресурсы", "resource"),
    ):
        if key in structure:
            from_list(structure.get(key), kind)

    # Табличные части
    for key in ("tabular_sections", "TabularSections", "ТабличныеЧасти", "tabularSections"):
        ts_list = structure.get(key)
        if not isinstance(ts_list, list):
            continue
        for ts in ts_list:
            if not isinstance(ts, dict):
                continue
            ts_name = _as_str(ts.get("name") or ts.get("Name") or ts.get("Имя"))
            for ak in ("attributes", "Attributes", "Реквизиты", "fields"):
                if ak in ts:
                    from_list(ts.get(ak), "tabular", ts_name)

    return fields


def search_fields(
    fields: list[FieldInfo],
    search_text: str,
    *,
    header_only: bool = False,
    limit: int = 50,
) -> list[FieldInfo]:
    """Поиск реквизитов по подстроке в имени, синониме или типе (как find_metadata для полей)."""
    q = (search_text or "").strip().lower()
    if not q:
        result = [f for f in fields if not header_only or not f.tabular_section]
        return result[:limit]

    matched: list[FieldInfo] = []
    for f in fields:
        if header_only and f.tabular_section:
            continue
        if q in f.name.lower() or q in f.synonym.lower() or q in f.type_str.lower():
            matched.append(f)
        if len(matched) >= limit:
            break
    return matched


def _score_role(field: FieldInfo, hints: list[str], *, role: str = "") -> int:
    """Чем выше — тем лучше совпадение роли с полем конфигурации."""
    name_l = field.name.lower().replace("ё", "е")
    syn_l = field.synonym.lower().replace("ё", "е")
    blob = field.search_blob.replace("ё", "е")
    best = 0
    for h in hints:
        h = h.lower().replace("ё", "е")
        if name_l == h:
            best = max(best, 100)
        elif name_l.startswith(h) or name_l.endswith(h):
            best = max(best, 80)
        elif h in name_l:
            best = max(best, 60)
        elif syn_l == h:
            best = max(best, 70)
        elif h in syn_l:
            best = max(best, 50)
        elif h in blob:
            best = max(best, 20)
    # штраф за табличную часть при поиске шапки
    if field.tabular_section:
        best = max(0, best - 40)
    # сумма документа важнее НДС / частичных сумм
    if role == "amount":
        if "ндс" in name_l and "безндс" not in name_l and "вснндс" not in name_l:
            if "суммадокумента" not in name_l and "всего" not in name_l:
                best = max(0, best - 50)
        if name_l in ("суммадокумента", "суммавсего", "итого"):
            best += 40
        if name_l == "сумма" or name_l.endswith("сумма") and "ндс" not in name_l:
            best += 15
    # номенклатура: точное имя важнее «Есть…ПродукцияГИСМ»
    if role == "nomenclature":
        if name_l in ("номенклатура", "товар", "услуга"):
            best += 50
        if name_l.startswith("есть") or "гисм" in name_l or "маркиру" in name_l:
            best = max(0, best - 80)
        if "булево" in (field.type_str or "").lower() or "boolean" in (
            field.type_str or ""
        ).lower():
            best = max(0, best - 90)
    # контрагент: не субконто/счёт учёта
    if role == "counterparty":
        if name_l in ("контрагент", "партнер", "партнёр", "покупатель", "поставщик"):
            best += 50
        if "субконто" in name_l or name_l.startswith("счет") or "счёт" in name_l:
            best = max(0, best - 100)
    return best


def match_role(
    fields: list[FieldInfo],
    role: str,
    *,
    header_only: bool = True,
) -> FieldInfo | None:
    hints = _role_hints().get(role)
    if not hints:
        return None
    candidates = [f for f in fields if not header_only or not f.tabular_section]
    scored = [(_score_role(f, hints, role=role), f) for f in candidates]
    scored = [(s, f) for s, f in scored if s > 0]
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], len(x[1].name)))
    return scored[0][1]


def match_roles(
    fields: list[FieldInfo],
    roles: list[str],
    *,
    header_only: bool = True,
) -> dict[str, FieldInfo]:
    found: dict[str, FieldInfo] = {}
    used: set[str] = set()
    for role in roles:
        fi = match_role(fields, role, header_only=header_only)
        if fi and fi.name not in used:
            found[role] = fi
            used.add(fi.name)
    return found


_RU_ENDINGS = (
    "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими",
    "ам", "ям", "ах", "ях", "ов", "ев", "ей", "ом", "ем", "ой", "ую", "юю",
    "ые", "ие", "ый", "ий", "ая", "яя", "ое", "ее",
    "у", "ю", "а", "я", "е", "ы", "и", "о", "ь",
)


def word_stem(word: str, *, min_len: int = 3) -> str:
    """Грубая основа русского слова (отрезать окончание), для матча с именами полей."""
    w = (word or "").lower().replace("ё", "е").strip()
    for end in _RU_ENDINGS:
        if w.endswith(end) and len(w) - len(end) >= min_len:
            return w[: -len(end)]
    return w


def match_field_by_words(
    fields: list[FieldInfo],
    phrase: str,
    *,
    ts_name: str = "",
    allow_tabular: bool = True,
    prefix_only: bool = False,
) -> FieldInfo | None:
    """
    Поле структуры по словам пользователя («менеджерам», «виду операции»).
    Морфология + имя/синоним; шапка важнее ТЧ. ts_name — допустимая ТЧ (если уже выбрана).
    prefix_only — имя/синоним должны НАЧИНАТЬСЯ со слова («продажам» ≠ ВариантОформленияПродажи).
    """
    words = [w for w in re.findall(r"[a-zа-яё0-9]+", (phrase or "").lower()) if len(w) >= 3]
    stems = [word_stem(w) for w in words]
    stems = [s for s in stems if len(s) >= 3]
    if not stems:
        return None
    best: tuple[int, FieldInfo] | None = None
    for f in fields:
        if f.tabular_section:
            if not allow_tabular or (ts_name and f.tabular_section != ts_name):
                continue
        name_l = f.name.lower().replace("ё", "е")
        syn_l = (f.synonym or "").lower().replace("ё", "е")
        if name_l in ("ссылка", "пометкаудаления", "проведен"):
            continue
        if not all(s in name_l or s in syn_l for s in stems):
            continue
        if prefix_only and not (name_l.startswith(stems[0]) or syn_l.startswith(stems[0])):
            continue
        sc = 100
        joined = "".join(stems)
        if name_l == joined or name_l.startswith(joined):
            sc += 50
        elif name_l.startswith(stems[0]):
            sc += 25
        if f.tabular_section:
            sc -= 30
        sc -= len(name_l) // 4  # короче = точнее
        if best is None or sc > best[0]:
            best = (sc, f)
    return best[1] if best else None


def is_reference_type(field: FieldInfo) -> bool:
    """Ссылочный тип по строке типа из метаданных (предпочтительно), иначе по имени."""
    t = field.type_str.lower()
    if t:
        ref_markers = (
            "ссылка",
            "ref",
            "catalogref",
            "documentref",
            "enumref",
            "справочникссылка",
            "документссылка",
            "перечислениессылка",
        )
        if any(m in t.replace(" ", "") for m in ref_markers) or "ссылка." in t or "ref." in t:
            return True
        # примитивы
        if any(p in t for p in ("строка", "число", "булево", "дата", "string", "number", "boolean", "date")):
            # составной тип может содержать и ссылку
            if "ссылка" not in t and "ref" not in t:
                return False
    # fallback: эвристика по имени, если тип не пришёл из 1С
    name_l = field.name.lower()
    ref_name_bits = (
        "контрагент", "партнер", "партнёр", "организация", "склад", "договор",
        "валюта", "ответственный", "менеджер", "автор", "номенклатура",
        "единица", "статус", "подразделение", "клиент", "покупатель",
        "заказчик", "соглашение", "сделка", "пользователь",
    )
    if any(b in name_l for b in ref_name_bits):
        return True
    return False


def is_numeric_type(field: FieldInfo) -> bool:
    t = field.type_str.lower()
    if t and any(x in t for x in ("число", "number", "decimal", "float", "int")):
        return True
    if "сумма" in field.name.lower() or "сумма" in field.synonym.lower():
        return True
    if "количество" in field.name.lower() or "цена" in field.name.lower():
        return True
    return False


def select_expr_for_field(field: FieldInfo, alias: str | None = None) -> str:
    a = alias or field.name
    name = field.name
    if is_reference_type(field):
        return f"ПРЕДСТАВЛЕНИЕ(Т.{name}) КАК {a}"
    if is_numeric_type(field) or field.name in (
        "Номер", "Дата", "Проведен", "ПометкаУдаления", "Код", "Наименование", "ЭтоГруппа"
    ):
        return f"Т.{name} КАК {a}"
    # неизвестный тип — для менеджера безопаснее представление
    if field.type_str and not is_reference_type(field):
        return f"Т.{name} КАК {a}"
    return f"ПРЕДСТАВЛЕНИЕ(Т.{name}) КАК {a}"


def resolve_business_map(
    structure: Any,
    *,
    object_kind: str = "document",
) -> dict[str, Any]:
    """
    Главный результат для агента: какие поля РЕАЛЬНО есть в конфигурации
    и какую бизнес-роль они закрывают.
    """
    fields = parse_structure_fields(structure)
    header = [f for f in fields if not f.tabular_section]
    tabular = [f for f in fields if f.tabular_section]

    hdr = _document_header_roles() or [
        "number",
        "date",
        "posted",
        "counterparty",
        "organization",
        "amount",
    ]
    roles = hdr if object_kind == "document" else CATALOG_HEADER_ROLES
    if object_kind not in ("document", "catalog"):
        roles = list(dict.fromkeys(hdr + CATALOG_HEADER_ROLES))

    matched = match_roles(header, roles, header_only=True)

    pack = _roles_pack()
    descriptions = pack.get("descriptions") or {}
    query_ex = pack.get("query_examples") or {}

    # реквизиты_1с: смысл → реальное Имя из конфигуратора + пример в запросе
    rekvizity: dict[str, Any] = {}
    for sense, fi in matched.items():
        rekvizity[sense] = {
            "имя_реквизита": fi.name,
            "синоним": fi.synonym,
            "тип": fi.type_str,
            "в_запросе": f"Т.{fi.name}",
            "что_это": descriptions.get(sense) or "",
            "пример_из_справки": query_ex.get(sense) or "",
        }

    role_map = {
        sense: {
            "name": fi.name,
            "synonym": fi.synonym,
            "type": fi.type_str,
        }
        for sense, fi in matched.items()
    }

    return {
        "object_kind": object_kind,
        "реквизиты_1с": rekvizity,
        "roles": role_map,  # совместимость: name = имя реквизита для Т.Имя
        "amount_field": role_map.get("amount", {}).get("name"),
        "counterparty_field": role_map.get("counterparty", {}).get("name"),
        "date_field": role_map.get("date", {}).get("name"),
        "number_field": role_map.get("number", {}).get("name"),
        "header_fields": [f.to_dict() for f in header],
        "header_field_names": [f.name for f in header],
        "tabular_field_count": len(tabular),
        "tabular_sections": sorted(
            {f.tabular_section for f in tabular if f.tabular_section}
        ),
        "как_в_запросе_1с": (
            "ВЫБРАТЬ Т.<имя_реквизита> ИЗ Документ.X КАК Т. "
            "Имена только из реквизиты_1с.*.имя_реквизита или header_field_names."
        ),
        "note": (
            "Это реквизиты метаданных 1С (как в конфигураторе). "
            "В запросе: Т.ИмяРеквизита. Для ссылок удобно ПРЕДСТАВЛЕНИЕ(Т.Контрагент)."
        ),
    }

