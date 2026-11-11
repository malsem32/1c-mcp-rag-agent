"""
Универсальная проверка HTTP API 1С (без hardcode конкретной конфигурации).

  D:\\rag_agent\\.venv\\Scripts\\python.exe onec_http\\test_http_api.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE = (os.environ.get("RAG_HTTP_BASE_URL") or "http://localhost/test/hs/ai").rstrip("/")
TIMEOUT = 60.0


def ok(title: str, cond: bool, detail: str = "") -> bool:
    mark = "OK " if cond else "FAIL"
    print(f"  [{mark}] {title}" + (f" — {detail}" if detail else ""))
    return cond


def first_catalog_name(list_json: dict) -> str | None:
    items = list_json.get("Справочники") or []
    if not items:
        return None
    # предпочитаем «похожих на клиентов/товары», иначе первый не-шумный
    prefer = ("контрагент", "номенклатур", "партнер", "организац", "валют")
    noise = ("присоединен", "удалить", "настройк")

    def score(item) -> int:
        if isinstance(item, dict):
            name = str(item.get("name") or "")
            syn = str(item.get("synonym") or "")
        else:
            name, syn = str(item), ""
        blob = f"{name} {syn}".lower()
        if any(n in blob for n in noise):
            return -100
        for i, p in enumerate(prefer):
            if p in blob:
                return 50 - i
        return 0

    ranked = sorted(items, key=score, reverse=True)
    top = ranked[0]
    if isinstance(top, dict):
        return str(top.get("full_name") or f"Справочник.{top.get('name')}")
    return f"Справочник.{top}"


def first_document_name(list_json: dict) -> str | None:
    items = list_json.get("Документы") or []
    if not items:
        return None
    prefer = ("реализац", "заказ", "поступлен", "счет", "оплат", "реализация")
    noise = ("присоединен", "удалить")

    def score(item) -> int:
        if isinstance(item, dict):
            name = str(item.get("name") or "")
        else:
            name = str(item)
        blob = name.lower()
        if any(n in blob for n in noise):
            return -100
        for i, p in enumerate(prefer):
            if p in blob:
                return 50 - i
        return 0

    ranked = sorted(items, key=score, reverse=True)
    top = ranked[0]
    if isinstance(top, dict):
        return str(top.get("full_name") or f"Документ.{top.get('name')}")
    return f"Документ.{top}"


def main() -> int:
    print(f"Base URL: {BASE}")
    print("Режим: универсальный (имена объектов из list/search этой базы)\n")
    passed = 0
    failed = 0

    def check(title: str, cond: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok(title, cond, detail):
            passed += 1
        else:
            failed += 1

    catalog_full = None
    document_full = None

    with httpx.Client(timeout=TIMEOUT) as client:
        print("1) GET list (+ rich=1)")
        r = client.get(f"{BASE}/list", params={"rich": "1"})
        if r.status_code != 200:
            r = client.get(f"{BASE}/list")
        check("list 200", r.status_code == 200, str(r.status_code))
        data = {}
        try:
            data = r.json()
        except Exception as e:
            check("list JSON", False, str(e))
        else:
            check("есть Справочники", bool(data.get("Справочники")))
            check("есть Документы", bool(data.get("Документы")))
            catalog_full = first_catalog_name(data)
            document_full = first_document_name(data)
            print(f"       sample catalog: {catalog_full}")
            print(f"       sample document: {document_full}")

        print("\n2) GET info")
        r = client.get(f"{BASE}/info")
        check("info 200 (желательно)", r.status_code == 200, str(r.status_code))
        if r.status_code == 200:
            info = r.json()
            print(f"       config: {info.get('config_name')} {info.get('config_synonym')}")

        if catalog_full:
            print(f"\n3) GET object?name={catalog_full}")
            r = client.get(f"{BASE}/object", params={"name": catalog_full})
            check("object catalog 200", r.status_code == 200, f"{r.status_code} {r.text[:100]}")
            if r.status_code == 200:
                obj = r.json()
                attrs = obj.get("attributes") or []
                check("attributes > 0", len(attrs) > 0, f"count={len(attrs)}")
                names = [
                    a.get("name") if isinstance(a, dict) else str(a) for a in attrs[:15]
                ]
                print(f"       fields: {names}")

        if document_full:
            print(f"\n4) GET object document {document_full}")
            r = client.get(f"{BASE}/object", params={"name": document_full})
            check("object document 200", r.status_code == 200, str(r.status_code))
            if r.status_code == 200:
                obj = r.json()
                attrs = obj.get("attributes") or []
                names = [
                    a.get("name") if isinstance(a, dict) else str(a) for a in attrs
                ]
                print(f"       fields sample: {names[:12]}")
                check("есть поля шапки", len(names) > 0)

        print("\n5) GET search?q= (короткий запрос)")
        r = client.get(f"{BASE}/search", params={"q": "орг", "limit": 5})
        check("search 200", r.status_code == 200, str(r.status_code))
        if r.status_code == 200:
            s = r.json()
            print(f"       found={s.get('found_count')} sample={s.get('results', [])[:2]}")

        if catalog_full:
            # поле наименования может называться иначе — берём из structure
            print("\n6) POST query (динамический SELECT)")
            # сначала structure
            r = client.get(f"{BASE}/object", params={"name": catalog_full})
            field_name = "Наименование"
            if r.status_code == 200:
                attrs = r.json().get("attributes") or []
                for a in attrs:
                    if not isinstance(a, dict):
                        continue
                    n = str(a.get("name") or "")
                    syn = str(a.get("synonym") or "").lower()
                    if "наимен" in n.lower() or "наимен" in syn or n.lower() in (
                        "description",
                        "descriptionfull",
                    ):
                        field_name = n
                        break
                    if n.lower() in ("код", "code") and field_name == "Наименование":
                        field_name = n  # fallback later
            q = (
                f"ВЫБРАТЬ ПЕРВЫЕ 3\n"
                f"\tТ.{field_name} КАК Поле\n"
                f"ИЗ\n"
                f"\t{catalog_full} КАК Т"
            )
            body = json.dumps({"query": q, "limit": 3}, ensure_ascii=False).encode("utf-8")
            r = client.post(
                f"{BASE}/query",
                content=body,
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            check("query 200", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
            if r.status_code == 200:
                qj = r.json()
                rows = qj.get("data") or qj.get("rows") or []
                print(f"       field={field_name} rows={len(rows)}")

    print("\n7) Python tools (suggest_objects — универсально)")
    try:
        from backends import create_backend
        from config import load_config
        from tools.universal import UniversalTools

        cfg = load_config()
        cfg["backend"] = "http"
        cfg["http"]["base_url"] = BASE
        tools = UniversalTools(create_backend(cfg), read_only=True)

        for phrase in ("клиенты", "продажи", "товары"):
            s = tools.suggest_objects(phrase, limit=5)
            tops = [c.get("full_name") for c in s.get("candidates") or []][:3]
            check(f"suggest_objects «{phrase}»", (s.get("found_count") or 0) >= 0, str(tops))

        if catalog_full:
            res = tools.resolve_business_fields(catalog_full)
            check(
                "resolve_business_fields catalog",
                "error" not in res,
                str(res.get("error") or list((res.get("roles") or {}).keys())),
            )
    except Exception as e:
        check("Python tools", False, str(e))

    print(f"\n=== Итого: OK={passed} FAIL={failed} ===")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
