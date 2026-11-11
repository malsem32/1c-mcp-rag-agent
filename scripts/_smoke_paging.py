# -*- coding: utf-8 -*-
"""Smoke: limit / offset / page / has_more / next_offset."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backends import create_backend
from config import load_config
from tools.universal import UniversalTools


def labels(items, key_fn=None):
    if not items:
        return []
    if key_fn:
        return [key_fn(x) for x in items]
    if isinstance(items[0], dict):
        return [
            x.get("full_name")
            or x.get("Номер")
            or x.get("path")
            or str(x)[:50]
            for x in items
        ]
    return list(items)


def show(tag, r, items_key="names", key_fn=None):
    items = r.get(items_key) or r.get("rows") or r.get("matches") or r.get("items") or []
    labs = labels(items, key_fn)
    print(f"--- {tag} ---")
    print(
        f"  limit={r.get('limit')} offset={r.get('offset')} page={r.get('page')} "
        f"returned={r.get('returned')} total={r.get('total')} "
        f"has_more={r.get('has_more')} next_offset={r.get('next_offset')}"
    )
    print(f"  items={labs}")
    return labs


def main() -> int:
    cfg = load_config()
    t = UniversalTools(
        create_backend(cfg),
        read_only=True,
        default_limit=25,
        max_limit=200,
    )
    checks: list[tuple[str, bool]] = []

    print("=== 1) list_objects Documents limit=5 ===")
    a = t.list_objects_filtered("Документы", "", limit=5, offset=0)
    la = show("page1 offset=0", a)
    nxt = a.get("next_offset")
    b = t.list_objects_filtered("Документы", "", limit=5, offset=nxt if nxt is not None else 5)
    lb = show("page2 offset=next", b)
    ok_list = bool(a.get("has_more")) and set(la).isdisjoint(set(lb)) and len(la) == 5
    checks.append(("list pages disjoint + has_more", ok_list))

    print("\n=== 2) list via page=1 and page=2 ===")
    p1 = t.list_objects_filtered("Документы", "", limit=4, page=1)
    p2 = t.list_objects_filtered("Документы", "", limit=4, page=2)
    lp1 = show("page=1", p1)
    lp2 = show("page=2", p2)
    checks.append(
        (
            "list page param",
            p1.get("page") == 1
            and p2.get("page") == 2
            and set(lp1).isdisjoint(set(lp2)),
        )
    )

    print("\n=== 3) find_metadata limit=5 ===")
    f1 = t.find_metadata("документ", "Документы", limit=5, offset=0)
    f2 = t.find_metadata("документ", "Документы", limit=5, offset=5)
    lf1 = show("find off0", f1, "items", lambda x: x.get("full_name"))
    lf2 = show("find off5", f2, "items", lambda x: x.get("full_name"))
    checks.append(
        (
            "find_metadata pages",
            bool(f1.get("has_more"))
            and f1.get("next_offset") == 5
            and set(lf1).isdisjoint(set(lf2)),
        )
    )

    print("\n=== 4) resolve_object paging ===")
    r1 = t.resolve_object("документ", limit=4, offset=0)
    r2 = t.resolve_object("документ", limit=4, offset=4)
    lr1 = show("resolve off0", r1, "items", lambda x: x.get("full_name"))
    lr2 = show("resolve off4", r2, "items", lambda x: x.get("full_name"))
    checks.append(
        (
            "resolve paging",
            r1.get("has_more") is True
            and r1.get("next_offset") == 4
            and set(lr1).isdisjoint(set(lr2)),
        )
    )

    print("\n=== 5) execute_query data paging ===")
    # full_name только из find_metadata текущей CF (стемы, не hardcode)
    from tools.business_stems import sales_search

    meta = t.find_metadata(sales_search(), "Документы", 5)
    items = meta.get("items") or meta.get("results") or []
    doc_fn = ""
    for it in items:
        if isinstance(it, dict) and str(it.get("full_name") or "").startswith("Документ."):
            doc_fn = str(it["full_name"])
            break
    if not doc_fn:
        print("SKIP query paging: no sales document in metadata")
        checks.append(("query pages skip", True))
    else:
        print("  resolved", doc_fn)
        q = (
            f"ВЫБРАТЬ ПЕРВЫЕ 100 Т.Номер КАК Номер, Т.Дата КАК Дата "
            f"ИЗ {doc_fn} КАК Т "
            f"ГДЕ Т.Проведен = ИСТИНА УПОРЯДОЧИТЬ ПО Т.Дата УБЫВ"
        )
        e1 = t.execute_query(q, limit=5, offset=0)
        e2 = t.execute_query(q, limit=5, offset=5)
        e3 = t.execute_query(q, limit=5, page=3)
        n1 = show("query off0", e1, "rows", lambda x: x.get("Номер"))
        n2 = show("query off5", e2, "rows", lambda x: x.get("Номер"))
        n3 = show("query page3", e3, "rows", lambda x: x.get("Номер"))
        checks.append(
            (
                "query pages disjoint",
                bool(e1.get("has_more"))
                and e1.get("next_offset") == 5
                and set(n1).isdisjoint(set(n2))
                and len(n1) == 5
                and len(n2) == 5,
            )
        )
        checks.append(
            ("query page=3 offset=10", e3.get("offset") == 10 and len(n3) == 5)
        )

    print("\n=== 6) linked_query limit+offset ===")
    lq1 = t.linked_query(
        request="задолженность клиентов на конец 2015",
        execute=True,
        limit=3,
        offset=0,
    )
    lq2 = t.linked_query(
        request="задолженность клиентов на конец 2015",
        execute=True,
        limit=3,
        offset=3,
    )
    print(
        "  lq1",
        len(lq1.get("rows") or []),
        "has_more",
        lq1.get("has_more"),
        "next",
        lq1.get("next_offset"),
        "err",
        str(lq1.get("error") or "")[:60],
    )
    print(
        "  lq2",
        len(lq2.get("rows") or []),
        "offset",
        lq2.get("offset"),
        "err",
        str(lq2.get("error") or "")[:60],
    )
    lq_ok = (
        len(lq1.get("rows") or []) == 3
        and len(lq2.get("rows") or []) >= 1
        and (lq1.get("has_more") is True or lq1.get("next_offset") == 3)
    )
    if lq1.get("rows") and lq2.get("rows"):
        k = list(lq1["rows"][0].keys())[0]
        v1 = [str(r.get(k)) for r in lq1["rows"]]
        v2 = [str(r.get(k)) for r in lq2["rows"]]
        print("  key", k, "p1", v1, "p2", v2)
        lq_ok = lq_ok and set(v1).isdisjoint(set(v2))
    checks.append(("linked_query paging", lq_ok))

    print("\n=== 7) search_code paging ===")
    s1 = t.search_code(query="БлокировкаДанных", max_matches=3, offset=0)
    s2 = t.search_code(query="БлокировкаДанных", max_matches=3, offset=3)
    pths1 = [m.get("path") for m in (s1.get("matches") or [])]
    pths2 = [m.get("path") for m in (s2.get("matches") or [])]
    print("  s1", s1.get("returned"), s1.get("has_more"), s1.get("next_offset"), pths1)
    print("  s2", s2.get("returned"), pths2)
    checks.append(
        (
            "search_code paging",
            len(pths1) == 3
            and s1.get("has_more") is True
            and s1.get("next_offset") == 3
            and len(set(pths1) & set(pths2)) < 3,  # allow partial path reuse if ranked same
        )
    )

    print("\n=== 8) custom limit chosen by caller (limit=7) ===")
    c = t.list_objects_filtered("Справочники", "", limit=7, offset=0)
    show("limit=7", c)
    checks.append(("custom limit=7", c.get("returned") == 7 and c.get("limit") == 7))

    print("\n==== SUMMARY ====")
    all_ok = True
    for name, ok in checks:
        print(("OK  " if ok else "FAIL"), name)
        all_ok = all_ok and ok
    print("ALL_PASS" if all_ok else "SOME_FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
