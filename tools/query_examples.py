"""
Универсальный каталог эталонов запросов 1С.
Контент: knowledge/query_examples.yaml (правьте YAML, не Python).
"""

from __future__ import annotations

import re
from typing import Any


def _book() -> dict[str, Any]:
    from kb.store import get_query_examples_book

    return get_query_examples_book()


def _default_roles() -> dict[str, str]:
    return dict(
        _book().get("default_roles")
        or {
            "number": "Номер",
            "date": "Дата",
            "counterparty": "Контрагент",
            "amount": "СуммаДокумента",
            "posted": "Проведен",
            "code": "Код",
            "name": "Наименование",
            "deletion": "ПометкаУдаления",
            "line_item": "Номенклатура",  # кандидат; реальное имя — из structure
            "qty": "Количество",
            "line_amount": "Сумма",
            "tabular": "Товары",  # кандидат ТЧ; реальное — из structure
        }
    )


def _cookbook() -> list[dict[str, Any]]:
    return list(_book().get("cookbook") or [])


# алиасы сценариев (короткие ключи → id)
_SCENARIO_ALIASES: dict[str, tuple[str, ...]] = {
    "document_period": ("period", "месяц", "феврал", "январ", "список документ"),
    "document_period_excel": ("excel", "выгруз", "файл"),
    "sum_period": ("сумма", "итого", "сколько"),
    "catalog_list": ("справочник", "список"),
    "catalog_search": ("найди", "поиск", "подобно"),
    "tabular": ("тч", "строк", "табличн"),
    "join": ("join", "соединен"),
    "any_report": ("отчет", "отчёт", "report"),
}

def fill_template(
    template: str,
    *,
    document: str = "",
    catalog: str = "",
    roles: dict[str, str] | None = None,
) -> str:
    """Подставляет {document}/{catalog}/роли полей в шаблон."""
    if not template:
        return ""
    r = dict(_default_roles())
    if roles:
        for k, v in roles.items():
            if v:
                r[str(k)] = str(v)
    # document / catalog
    doc = (document or "").strip() or "Документ.<ИмяИзFindMetadata>"
    cat = (catalog or "").strip() or "Справочник.<ИмяИзFindMetadata>"
    if doc and not doc.startswith("Документ.") and "." not in doc:
        doc = f"Документ.{doc}"
    if cat and not cat.startswith("Справочник.") and "." not in cat:
        cat = f"Справочник.{cat}"

    values = {**r, "document": doc, "catalog": cat}
    out = template
    # {{name}} → value (удвоенные скобки в _cookbook())
    for key, val in values.items():
        out = out.replace("{{" + key + "}}", val)
        out = out.replace("{" + key + "}", val)
    return out


def list_scenarios() -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for item in _cookbook():
        sid = str(item.get("scenario") or item["id"])
        if sid in seen:
            continue
        seen.add(sid)
        out.append(
            {
                "scenario": sid,
                "when": str(item.get("when") or ""),
                "user": str(item.get("user") or ""),
            }
        )
    return out


def _match_score(item: dict[str, Any], q: str) -> int:
    q = q.lower().replace("ё", "е")
    score = 0
    iid = str(item.get("id") or "").lower()
    sid = str(item.get("scenario") or "").lower()

    if q in (iid, sid) or q.replace(" ", "_") in (iid, sid):
        score += 120

    for field in ("id", "scenario", "when", "user", "note"):
        val = str(item.get(field) or "").lower().replace("ё", "е")
        if not val:
            continue
        if q in val:
            score += 25
        for word in q.split():
            if len(word) >= 3 and word in val:
                score += 5

    for alias_key, words in _SCENARIO_ALIASES.items():
        if alias_key not in (sid, iid):
            continue
        for w in words:
            if w in q:
                score += 18

    if any(x in q for x in ("excel", "xlsx", "выгруз", "файл")):
        if iid == "doc_period_excel":
            score += 50
        elif iid == "excel_custom":
            score += 15
    if any(x in q for x in ("сумма", "итого", "сколько", "sum")):
        if iid == "sum_period":
            score += 55
    if any(x in q for x in ("топ", "top", "групп")):
        if iid == "top_group":
            score += 55
    if any(x in q for x in ("тч", "строк", "табличн")):
        if iid in ("tabular", "join"):
            score += 40
    if any(x in q for x in ("найди", "поиск", "подобно")):
        if iid == "catalog_search":
            score += 45
    if any(x in q for x in ("отчет", "отчёт", "report", "сделай", "выгруз")):
        if iid == "any_document_report":
            score += 40
    if any(x in q for x in ("регистр", "остатк", "оборот", "срез", "balances")):
        if iid.startswith("register"):
            score += 55
    if any(x in q for x in ("феврал", "январ", "март", "месяц", "период")) or re.search(r"(19|20)\d{2}", q):
        if iid == "doc_period":
            score += 25
        if iid == "doc_period_excel" and any(
            x in q for x in ("excel", "xlsx", "выгруз", "файл")
        ):
            score += 20
    return score


def _slim(
    item: dict[str, Any],
    *,
    document: str = "",
    catalog: str = "",
    roles: dict[str, str] | None = None,
) -> dict[str, Any]:
    tpl = str(item.get("query_template") or item.get("query") or "")
    filled = fill_template(tpl, document=document, catalog=catalog, roles=roles)
    out: dict[str, Any] = {
        "id": item["id"],
        "when": item.get("when"),
        "user": item.get("user"),
        "prefer_tool": item.get("prefer_tool"),
        "note": item.get("note"),
        "placeholders": {
            "document": "full_name документа из find_metadata/suggest_objects",
            "catalog": "full_name справочника из find_metadata",
            "fields": "roles.* из resolve_business_fields / имена из get_object_structure",
        },
    }
    if filled:
        out["query"] = filled
        out["query_template"] = tpl.replace("{{", "{").replace("}}", "}")
    tool_tpl = item.get("tool_call_template") or item.get("tool_call") or ""
    if tool_tpl:
        out["tool_call"] = fill_template(
            str(tool_tpl), document=document, catalog=catalog, roles=roles
        )
    if item.get("excel_tool"):
        out["excel_tool"] = item["excel_tool"]
    if item.get("bad"):
        out["do_not"] = item["bad"]
    if document:
        out["document_used"] = document
    if catalog:
        out["catalog_used"] = catalog
    if roles:
        out["roles_used"] = roles
    return out


def get_examples(
    scenario: str = "",
    *,
    document_name: str = "",
    catalog_name: str = "",
    roles: dict[str, str] | None = None,
    limit: int = 4,
) -> dict[str, Any]:
    """
    scenario пустой → индекс + главные эталоны.
    document_name / catalog_name / roles — подстановка в шаблоны (из metadata ТЕКУЩЕЙ базы).
    """
    q = (scenario or "").strip()
    doc = (document_name or "").strip()
    cat = (catalog_name or "").strip()

    rules = list(_book().get("rules") or []) or [
        "Имена таблиц — только из поиска по вашей базе",
        "Имена реквизитов — только из структуры объекта",
        "Весь ВЫБРАТЬ — одной строкой, без переносов",
        "Для менеджера чаще хватает построения отчёта",
    ]

    if not q or q.lower() in ("list", "темы", "index", "help", "all", "все"):
        must_ids = (
            "any_document_report",
            "doc_period_excel",
            "doc_period",
            "catalog_list",
            "sum_period",
        )
        samples = [x for x in _cookbook() if x["id"] in must_ids]
        return {
            "usage": (
                "query_examples(scenario, document_name=<full_name из metadata>). "
                "Сначала find_metadata/suggest_objects, потом эталон с подстановкой."
            ),
            "pipeline": [
                "suggest_objects(фраза) или find_metadata(ключевые слова, 'Документы'|'Справочники')",
                "full_name = candidates[0].full_name",
                "resolve_business_fields(full_name) → roles",
                "query_examples(scenario, document_name=full_name) → query",
                "execute_query(query) или export_document_period_excel / export_excel_report",
            ],
            "scenarios": list_scenarios(),
            "examples": [
                _slim(x, document=doc, catalog=cat, roles=roles) for x in samples
            ],
            "rules": rules,
        }

    scored = sorted(
        ((_match_score(item, q), item) for item in _cookbook()),
        key=lambda t: -t[0],
    )
    picked = [item for sc, item in scored if sc > 0][: max(1, min(limit, 6))]
    if not picked:
        picked = [x for x in _cookbook() if x["id"] in ("any_document_report", "doc_period")]

    out: dict[str, Any] = {
        "scenario": q,
        "count": len(picked),
        "examples": [
            _slim(x, document=doc, catalog=cat, roles=roles) for x in picked
        ],
        "rules": rules,
        "hint": (
            "Подставь full_name из metadata этой базы. "
            "Если document_name не передан — в query останется плейсхолдер "
            "Документ.<ИмяИзFindMetadata> — замени его."
        ),
    }
    if not doc and not cat:
        out["hint"] += (
            " Вызови: query_examples(scenario, document_name='Документ.X_из_find_metadata')."
        )
    return out

