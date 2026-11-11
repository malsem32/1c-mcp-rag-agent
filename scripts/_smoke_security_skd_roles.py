# -*- coding: utf-8 -*-
"""Проверки: Execute/Вычислить, СКД-маржа, роли на удаление/изменение справочников."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.loop import build_agent_from_config
from backends import create_backend
from config import load_config
from tools.universal import UniversalTools
from vector.search import search_code_vectors


def scan_exec_eval(tools: UniversalTools) -> dict:
    """Поиск Выполнить/Вычислить/Execute/Eval, с уклоном в обмен."""
    queries = (
        "Выполнить(",
        "Вычислить(",
        "Execute(",
        "Eval(",
        "ОбщегоНазначения.Выполнить",
        "ВыполнитьВБезопасномРежиме",
    )
    hits = []
    seen = set()
    for q in queries:
        sc = search_code_vectors(q, limit=20, match_mode="exact")
        for m in sc.get("matches") or []:
            path = (m.get("path") or "").replace("\\", "/")
            key = f"{path}:{q}:{m.get('id')}"
            if key in seen:
                continue
            seen.add(key)
            text = m.get("text") or m.get("snippet") or ""
            low = path.lower()
            exchange = any(
                x in low
                for x in (
                    "обмен",
                    "exchange",
                    "синхрон",
                    "synchron",
                    "conversion",
                    "конверт",
                    "dataexchange",
                    "обменданными",
                )
            )
            # простая эвристика риска
            risk = "medium"
            if re.search(
                r"Выполнить\s*\(\s*[^)\"']+|Вычислить\s*\(\s*[^)\"']+",
                text,
            ):
                risk = "high"  # динамическая строка
            if "Безопасн" in text or "SafeMode" in text or "БезопасномРежиме" in text:
                risk = "low"
            if re.search(r'Выполнить\s*\(\s*["\']', text):
                risk = "low"  # литерал
            hits.append(
                {
                    "query": q,
                    "path": path,
                    "exchange_related": exchange,
                    "risk_heuristic": risk,
                    "snippet": text[:350].replace("\n", " "),
                    "object_name": m.get("object_name"),
                    "score": m.get("score"),
                }
            )
    exchange_hits = [h for h in hits if h["exchange_related"]]
    return {
        "total_hits": len(hits),
        "exchange_hits": len(exchange_hits),
        "exchange": exchange_hits[:40],
        "other_sample": [h for h in hits if not h["exchange_related"]][:15],
        "note": "Эвристика: high=динамический аргумент; low=литерал/безопасный режим. Не замена аудита.",
    }


def roles_catalog_write(tools: UniversalTools) -> dict:
    """Роли с интерактивным изменением/удалением справочников (из dump)."""
    # list roles then sample rights — may be heavy; use find_roles / list
    try:
        roles = tools.list_roles(search="", limit=200)
    except Exception as e:
        return {"error": str(e)}
    names = []
    if isinstance(roles, dict):
        for r in roles.get("roles") or roles.get("items") or roles.get("names") or []:
            if isinstance(r, dict):
                names.append(r.get("name") or r.get("full_name") or "")
            else:
                names.append(str(r))
    names = [n for n in names if n][:120]
    # rights search: InteractiveDelete / Update / Edit on Catalogs
    risky = []
    for role in names[:80]:
        try:
            rr = tools.role_rights(role=role, object_filter="Справочник", limit=50)
        except Exception:
            continue
        if not isinstance(rr, dict):
            continue
        rights = rr.get("rights") or rr.get("items") or rr.get("rows") or []
        # also raw text
        blob = json.dumps(rr, ensure_ascii=False).lower()
        if any(
            x in blob
            for x in (
                "interactivedelete",
                "интерактивноеудаление",
                "delete",
                "update",
                "edit",
                "изменение",
                "удаление",
            )
        ):
            # extract a few object lines
            sample = []
            if isinstance(rights, list):
                for it in rights[:8]:
                    if isinstance(it, dict):
                        sample.append(
                            {
                                "object": it.get("object") or it.get("name") or it.get("full_name"),
                                "right": it.get("right") or it.get("rights") or it.get("value"),
                            }
                        )
            risky.append({"role": role, "sample": sample, "hint": "see role_rights"})
    return {
        "roles_scanned": min(len(names), 80),
        "roles_total_listed": len(names),
        "roles_with_catalog_write_or_delete_heuristic": len(risky),
        "roles": risky[:40],
        "note": "Эвристика по тексту rights; точность зависит от role_rights dump.",
    }


def main() -> int:
    cfg = load_config()
    backend = create_backend(cfg)
    print("1C:", backend.test_connection())
    tools = UniversalTools(
        backend,
        read_only=True,
        default_limit=25,
        max_limit=80,
        list_objects_cap=80,
        tool_result_max_chars=6000,
        compact_results=True,
        code_dir=cfg.get("code_dir") or "",
    )

    print("\n=== 1. Execute/Eval scan ===")
    scan = scan_exec_eval(tools)
    print("total", scan["total_hits"], "exchange", scan["exchange_hits"])
    for h in scan["exchange"][:12]:
        print(
            f"  [{h['risk_heuristic']}] {h['path'][:70]}\n    {h['snippet'][:120]}"
        )
    if not scan["exchange"]:
        print("  (нет path с «обмен» — sample other)")
        for h in scan["other_sample"][:8]:
            print(f"  [{h['risk_heuristic']}] {h['path'][:70]}")

    print("\n=== 2. SKD / margin query draft (structure-driven) ===")
    # найти регистры/документы для выручки
    for q in ("выручка себестоимость", "продажи", "реализация"):
        m = tools.find_metadata(q, "РегистрыНакопления", 5)
        print("meta", q, [i.get("full_name") for i in (m.get("items") or [])[:4]])
    st = tools.get_object_structure("РегистрНакопления.ВыручкаИСебестоимостьПродаж")
    print("dims", st.get("dimension_names"))
    print("res", st.get("resource_names"))
    print("vt", st.get("virtual_tables"))
    # draft query using обороты (manager field if any)
    draft = (
        "ВЫБРАТЬ\n"
        "  Т.Менеджер КАК Менеджер,\n"
        "  СУММА(Т.СуммаВыручкиОборот) КАК Выручка,\n"
        "  СУММА(Т.СтоимостьОборот) КАК Себестоимость,\n"
        "  СУММА(Т.СуммаВыручкиОборот) - СУММА(Т.СтоимостьОборот) КАК Маржа\n"
        "ИЗ\n"
        "  РегистрНакопления.ВыручкаИСебестоимостьПродаж.Обороты(&Начало, &Конец, , ) КАК Т\n"
        "ГДЕ\n"
        "  Т.ХозяйственнаяОперация <> ЗНАЧЕНИЕ(Перечисление.ХозяйственныеОперации.ВозвратТоваровОтКлиента)\n"
        "СГРУППИРОВАТЬ ПО\n"
        "  Т.Менеджер\n"
    )
    # try real field names from structure
    dims = st.get("dimension_names") or []
    res = st.get("resource_names") or []
    print("trying execute with structure-aware fields if possible...")
    # build safer query
    mgr = next((d for d in dims if "менеджер" in d.lower() or "manager" in d.lower() or "ответств" in d.lower()), None)
    sum_f = next((r for r in res if "выруч" in r.lower() or "сумма" in r.lower()), res[0] if res else None)
    cost_f = next((r for r in res if "себест" in r.lower() or "стоим" in r.lower()), None)
    if mgr and sum_f:
        q = (
            f"ВЫБРАТЬ ПЕРВЫЕ 30 Т.{mgr} КАК Менеджер, "
            f"СУММА(Т.{sum_f}Оборот) КАК СуммаОборот "
        )
        if cost_f:
            q += f", СУММА(Т.{cost_f}Оборот) КАК СебестоимостьОборот "
        q += (
            f"ИЗ РегистрНакопления.ВыручкаИСебестоимостьПродаж.Обороты("
            f"ДАТАВРЕМЯ(2015,1,1,0,0,0), ДАТАВРЕМЯ(2015,12,31,23,59,59), , ) КАК Т "
            f"СГРУППИРОВАТЬ ПО Т.{mgr}"
        )
        er = tools.execute_query(q, 30)
        print("exec err", er.get("error"), "rows", len(er.get("rows") or []))
        if er.get("rows"):
            print(er["rows"][:3])
    else:
        print("no manager/sum fields in dims/res — draft only:\n", draft[:400])

    print("\n=== 3. Roles catalog write/delete ===")
    roles = roles_catalog_write(tools)
    print(
        "scanned",
        roles.get("roles_scanned"),
        "risky",
        roles.get("roles_with_catalog_write_or_delete_heuristic"),
    )
    for r in (roles.get("roles") or [])[:15]:
        print(" ", r.get("role"), r.get("sample")[:2] if r.get("sample") else "")

    # Extra similar checks
    print("\n=== extra: ЗаписьJSON / HTTP без auth patterns ===")
    for q in ("ЗаписьJSON", "HTTPСоединение", "УстановитьПривилегированныйРежим"):
        sc = search_code_vectors(q, limit=4, match_mode="exact")
        print(q, len(sc.get("matches") or []), [(m.get("path") or "")[:55] for m in (sc.get("matches") or [])[:2]])

    print("\n=== agent (if LLM) ===")
    agent = build_agent_from_config(cfg, tools)
    agent_qs = [
        'Найди в модуле обмена все места, где выполняется Выполнить() или Вычислить(). Оцени уязвимость к инъекциям.',
        "Напиши запрос (язык 1С/СКД-смысл) маржинальность продаж по менеджерам за 2015, без возвратов. Выполни если можно.",
        "Какие роли имеют право интерактивно удалять или изменять справочники? Перечисли.",
    ]
    out_agent = []
    for i, q in enumerate(agent_qs, 1):
        print(f"\n--- agent {i} ---\n{q}")
        t0 = time.time()
        try:
            ans = agent.run(q, verbose=True)
        except Exception as e:
            ans = f"ERROR: {e}"
        print(f"({time.time()-t0:.0f}s)", (ans or "")[:1200])
        out_agent.append({"q": q, "a": ans or "", "sec": round(time.time() - t0, 1)})

    path = ROOT / "exports" / "security_skd_roles.json"
    path.write_text(
        json.dumps(
            {"scan": scan, "roles": roles, "agent": out_agent},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("SAVED", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
