# -*- coding: utf-8 -*-
"""
Прогон «человеческих» фраз по всем блокам /help:
  lowercase, другой порядок слов, синонимы.

python scripts/_eval_help_human_style.py
python scripts/_eval_help_human_style.py --with-llm
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backends import create_backend
from config import load_config
from tools.preflight import plan_preflight, run_preflight
from tools.universal import UniversalTools


# (id, section, human_phrase, expected_tool_substr, notes)
# expected_tool_substr — что preflight/route должен выбрать (подстрока tool)
CASES: list[tuple[str, str, str, str, str]] = [
    # 1. Ассортимент
    (
        "H01",
        "biz_assortment",
        "за 2016 год глянь что из товаров хорошо шло а что вообще не брали",
        "analyst_report",
        "хиты/неликвиды",
    ),
    # 2. Резервы
    (
        "H02",
        "biz_reserves",
        "покажи где у нас деньги зависли склад дебиторка просрочка",
        "analyst_report",
        "замороженные деньги",
    ),
    # 3. Маржа / прибыль
    (
        "H03",
        "biz_profit",
        "подскажи как прибыль поднять где маржа совсем низкая 2015 2016",
        "analyst_report",
        "маржинальность",
    ),
    # 4. Клиенты
    (
        "H04",
        "biz_customers",
        "какие клиенты vip и кто отвалился не покупал 2015 и 2016",
        "analyst_report",
        "клиентская аналитика",
    ),
    # Универсальный отчёт / excel
    (
        "H05",
        "report_builder",
        "сделай мне эксель с графиками по продажам за 2016",
        "report_builder",
        "excel+charts",
    ),
    # Данные: продажи
    (
        "H06",
        "data",
        "по контрагентам продажи за 2016 год сколько набежало",
        "build_report",
        "продажи",
    ),
    (
        "H07",
        "data",
        "сколько было реализаций в 2015",
        "build_report",
        "кол-во документов",
    ),
    (
        "H08",
        "data",
        "выведи список всей номенклатуры что есть",
        "build_report",
        "справочник",
    ),
    # Сравнение периодов
    (
        "H09",
        "compare",
        "сравни 2015 с 2016 по продажам кто вырос кто упал",
        "analyst_report",  # variance «кто вырос/упал» — правильнее compare
        "variance",
    ),
    # ABC / analyst
    (
        "H10",
        "analyst",
        "abc анализ контрагентов за 2016 год",
        "analyst_report",
        "ABC",
    ),
    (
        "H11",
        "analyst",
        "просроченные долги по корзинам давности",
        "analyst_report",
        "aging",
    ),
    # Долги / остатки
    (
        "H12",
        "debt",
        "на конец 2015 какая задолженность у клиентов",
        "linked_query",
        "долги",
    ),
    (
        "H13",
        "debt",
        "сколько товаров на складах осталось",
        "linked_query",
        "остатки",
    ),
    (
        "H14",
        "debt",
        "обороты по расчетам помесячно за 2015",
        "linked_query",
        "обороты",
    ),
    # Маржа tool
    (
        "H15",
        "margin",
        "маржа по менеджерам за 2015 год",
        "margin_report",
        "margin",
    ),
    # Meta
    (
        "H16",
        "meta",
        "какие есть документы про реализацию товаров",
        "",
        "resolve — preflight может быть None",
    ),
    (
        "H17",
        "meta",
        "все реквизиты у реализации товаров услуг",
        "",
        "structure",
    ),
    # Movements
    (
        "H18",
        "movements",
        "по каким регистрам проводится реализация товаров",
        "analyze_object_movements",
        "движения",
    ),
    (
        "H19",
        "movements",
        "какие движения пишет документ реализация",
        "analyze_object_movements",
        "движения",
    ),
    # Code
    (
        "H20",
        "code",
        "откуда в реализации берётся сумма покажи логику",
        "inspect_code",
        "код",
    ),
    (
        "H21",
        "code",
        "как устроена форма документа реализация",
        "inspect_code",
        "форма",
    ),
    # Locks
    (
        "H22",
        "locks",
        "где в коде стоят управляемые блокировки",
        "analyze_locks",
        "locks static",
    ),
    (
        "H23",
        "locks",
        "куда ставить managed locks при проведении реализации",
        "recommend_managed_locks",
        "recommend locks",
    ),
    # Access
    (
        "H24",
        "access",
        "какие роли могут удалять справочники",
        "list_roles_by_right",
        "роли",
    ),
    # Ops
    (
        "H25",
        "ops",
        "кто сейчас сидит в базе",
        "",
        "sessions — часто без preflight",
    ),
    (
        "H26",
        "ops",
        "ошибки из журнала регистрации за сегодня",
        "",
        "event log",
    ),
    # Query help
    (
        "H27",
        "query_help",
        "как работает функция добавитькдате в запросе 1с",
        "",
        "syntax help",
    ),
    (
        "H28",
        "query_help",
        "как написать выбрать с группировкой",
        "",
        "ВЫБРАТЬ",
    ),
    # Connection
    (
        "H29",
        "connection",
        "проверь связь с 1с",
        "",
        "test connection",
    ),
    # Follow-up style (standalone — memory less relevant)
    (
        "H30",
        "followup_standalone",
        "документы по контрагенту ассоль за 2015",
        "build_report",
        "filter partner",
    ),
]


def _score_result(tool: str | None, result: Any, section: str) -> tuple[str, str]:
    """return grade: good|ok|weak|fail , detail"""
    if result is None and not tool:
        return "weak", "no preflight / no tool"
    if not isinstance(result, dict):
        return "fail", f"non-dict: {type(result).__name__}"

    if result.get("need_clarification") and not result.get("rows"):
        about = result.get("about") or ""
        q = str(result.get("question") or "")[:80]
        # period ask on stock = bad; object options = ok
        if about == "period" and section in ("debt", "biz_reserves"):
            return "weak", f"asks period: {q}"
        if result.get("options"):
            return "ok", f"clarify options n={len(result['options'])}"
        return "weak", f"need_clarification about={about}: {q}"

    if result.get("error") and not any(
        result.get(k)
        for k in (
            "rows",
            "registers",
            "register_names_full",
            "hits",
            "findings",
            "roles",
            "recommendations",
            "manager_summary",
            "table_preview",
            "answer",
            "content",
            "example",
            "summary",
            "sessions",
            "ok",
        )
    ):
        return "fail", str(result.get("error"))[:120]

    # data tools
    if tool in (
        "linked_query",
        "build_report",
        "compare_periods",
        "margin_report",
        "analyst_report",
        "report_builder",
    ):
        rows = result.get("rows")
        n = len(rows) if isinstance(rows, list) else 0
        if isinstance(rows, dict):
            n = int(rows.get("shown") or rows.get("total") or 0)
        preview = result.get("table_preview") or ""
        ms = result.get("manager_summary") or {}
        path = result.get("path") or result.get("download_url")
        if n > 0 or preview or path:
            bits = [f"rows={n}"]
            if preview:
                bits.append("preview=yes")
            if path:
                bits.append("excel=yes")
            if ms.get("object") or result.get("primary"):
                bits.append(f"obj={ms.get('object') or result.get('primary')}")
            return "good", "; ".join(bits)
        if result.get("ok") and result.get("query"):
            return "ok", "query ok but 0 rows"
        if result.get("ok"):
            return "ok", "ok empty"
        return "weak", f"no rows err={str(result.get('error') or '')[:80]}"

    if tool == "analyze_object_movements":
        regs = result.get("register_names_full") or result.get("registers") or []
        n = len(regs) if isinstance(regs, list) else 0
        return ("good" if n else "weak", f"regs={n}")

    if tool == "analyze_locks":
        return (
            "good" if result.get("ok") else "weak",
            f"hits={result.get('hits_count')}",
        )

    if tool == "recommend_managed_locks":
        rec = result.get("recommendations") or []
        return (
            "good" if result.get("ok") and rec is not None else "weak",
            f"rec={len(rec) if isinstance(rec, list) else '?'}",
        )

    if tool == "list_roles_by_right":
        roles = result.get("roles")
        n = len(roles) if isinstance(roles, list) else 0
        return ("good" if n or result.get("ok") else "weak", f"roles={n}")

    if tool in ("inspect_code", "explain_fill"):
        hits = (
            result.get("hits")
            or result.get("matches")
            or result.get("modules")
            or result.get("findings")
            or []
        )
        n = len(hits) if isinstance(hits, list) else 0
        return ("good" if n or not result.get("error") else "weak", f"hits={n}")

    if tool in ("agent_capabilities", "manager_help"):
        return "good", "capabilities"

    if result.get("ok") is True:
        return "good", "ok"
    if result.get("answer") or result.get("content") or result.get("example"):
        return "good", "text answer"
    return "ok", f"keys={list(result.keys())[:8]}"


def _manual_dispatch(tools: UniversalTools, phrase: str, section: str) -> tuple[str | None, Any]:
    """Fallback tools when preflight is None."""
    pl = phrase.lower().replace("ё", "е")
    try:
        if section == "meta" or any(x in pl for x in ("реквизит", "структур", "какие есть документ")):
            if "реквизит" in pl or "структур" in pl:
                r = tools.resolve_object(phrase, "Документы", 8)
                fn = r.get("full_name") or r.get("top")
                if not fn and r.get("options"):
                    o0 = r["options"][0]
                    fn = o0.get("full_name") if isinstance(o0, dict) else o0
                if fn:
                    st = tools.get_object_structure(str(fn))
                    return "get_object_structure", st
                return "resolve_object", r
            return "resolve_object", tools.resolve_object(phrase, "Все", 10)
        if section == "query_help" or "добавитькдате" in pl or "выбрать" in pl or "группиров" in pl:
            if "добавить" in pl:
                return "query_syntax_help", tools.query_syntax_help("functions")
            return "query_syntax_help", tools.query_syntax_help("group")
        if section == "ops" or "журнал" in pl or "сидит" in pl or "сеанс" in pl:
            if "журнал" in pl or "ошибк" in pl:
                return "analyze_event_log", tools.analyze_event_log(request=phrase)
            return "list_active_sessions", tools.list_active_sessions()
        if section == "connection" or "связ" in pl:
            return "test_connection", tools.test_connection()
        if "форма" in pl or "откуда" in pl or "логик" in pl:
            return "inspect_code", tools.inspect_code(request=phrase)
    except Exception as e:
        return "manual_error", {"error": str(e)}
    return None, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-llm", action="store_true", help="full agent.run for each")
    ap.add_argument("--limit", type=int, default=0, help="only first N cases")
    ap.add_argument("--out", default="exports/help_human_eval.json")
    args = ap.parse_args()

    cfg = load_config()
    backend = create_backend(cfg)
    conn = backend.test_connection() if hasattr(backend, "test_connection") else {}
    print("1C connection:", conn if conn else "ok?", flush=True)

    tools = UniversalTools(
        backend,
        read_only=True,
        default_limit=cfg["agent"]["query_default_limit"],
        max_limit=cfg["agent"]["query_max_limit"],
        list_objects_cap=cfg.get("agent", {}).get("list_objects_cap", 80),
        tool_result_max_chars=cfg["llm"].get("tool_result_max_chars", 8000),
        compact_results=True,
        code_dir=cfg.get("code_dir") or "",
    )

    agent = None
    if args.with_llm:
        from agent.loop import build_agent_from_config

        agent = build_agent_from_config(cfg, tools)

    def dispatch(name: str, kwargs: dict[str, Any]) -> Any:
        return tools.dispatch(name, kwargs)

    cases = CASES[: args.limit] if args.limit else CASES
    results: list[dict[str, Any]] = []
    grades = {"good": 0, "ok": 0, "weak": 0, "fail": 0}

    for i, (cid, section, phrase, expected, note) in enumerate(cases, 1):
        print(f"\n{'='*60}\n{i}/{len(cases)} [{cid}] {section}\nQ: {phrase}", flush=True)
        t0 = time.time()
        row: dict[str, Any] = {
            "id": cid,
            "section": section,
            "q": phrase,
            "expected_tool": expected,
            "note": note,
        }
        try:
            plan = plan_preflight(phrase)
            row["preflight"] = (
                {
                    "tool": plan.get("tool"),
                    "conf": plan.get("confidence"),
                    "reason": plan.get("reason"),
                }
                if plan
                else None
            )
            tool_name = plan.get("tool") if plan else None
            result = None
            route = "preflight" if plan else "manual"

            if plan and float(plan.get("confidence") or 0) >= 0.78:
                pf = run_preflight(phrase, dispatch, min_confidence=0.78)
                if pf:
                    tool_name = pf.get("tool")
                    result = pf.get("result")
            if result is None:
                tool_name2, result2 = _manual_dispatch(tools, phrase, section)
                if tool_name2:
                    tool_name = tool_name2
                    result = result2
                    route = "manual"

            row["route"] = route
            row["tool"] = tool_name
            exp_ok = True
            if expected and tool_name:
                exp_ok = expected in str(tool_name)
            elif expected and not tool_name:
                exp_ok = False
            row["expected_match"] = exp_ok if expected else None

            grade, detail = _score_result(tool_name, result, section)
            # misroute penalty
            if expected and tool_name and expected not in str(tool_name):
                if grade == "good":
                    grade = "ok"
                detail = f"misroute want={expected} got={tool_name}; {detail}"
            row["grade"] = grade
            row["detail"] = detail

            # compact summary of result
            if isinstance(result, dict):
                slim = {
                    k: result.get(k)
                    for k in (
                        "ok",
                        "error",
                        "need_clarification",
                        "about",
                        "question",
                        "primary",
                        "full_name",
                        "row_count",
                        "path",
                        "plan_role",
                    )
                    if result.get(k) is not None
                }
                if result.get("table_preview"):
                    slim["table_preview_head"] = str(result["table_preview"])[:280]
                if result.get("manager_summary"):
                    slim["manager_summary"] = {
                        kk: result["manager_summary"].get(kk)
                        for kk in ("object", "period", "row_count", "message")
                        if isinstance(result["manager_summary"], dict)
                        and result["manager_summary"].get(kk) is not None
                    }
                rows = result.get("rows")
                if isinstance(rows, list) and rows:
                    slim["row0"] = rows[0]
                    slim["n_rows"] = len(rows)
                regs = result.get("register_names_full") or result.get("registers")
                if regs:
                    slim["regs_n"] = len(regs) if isinstance(regs, list) else 1
                    if isinstance(regs, list):
                        slim["regs_sample"] = [
                            (x.get("name") if isinstance(x, dict) else x) for x in regs[:6]
                        ]
                row["result_slim"] = slim

            if agent is not None:
                try:
                    agent.reset()
                    ans = agent.run(phrase, verbose=False)
                    row["agent_answer"] = (ans or "")[:1200]
                    # crude quality: empty table?
                    al = (ans or "").lower()
                    if "| ..." in (ans or "") or "| …" in (ans or "") or re.search(
                        r"\|\s*\.\.\.\s*\|", ans or ""
                    ):
                        row["agent_flag"] = "empty_table_placeholder"
                        if grade in ("good", "ok"):
                            grade = "weak"
                            row["grade"] = grade
                            row["detail"] += "; agent empty table"
                    elif any(
                        x in al
                        for x in (
                            "уточните период",
                            "нужно уточнить период",
                            "какой период",
                        )
                    ) and section in ("debt",):
                        row["agent_flag"] = "asks_period_on_stock"
                    elif len(ans or "") < 40:
                        row["agent_flag"] = "too_short"
                except Exception as e:
                    row["agent_error"] = str(e)[:200]

            grades[grade] = grades.get(grade, 0) + 1
            row["sec"] = round(time.time() - t0, 1)
            print(
                f"  → tool={tool_name} grade={grade} {detail} ({row['sec']}s)",
                flush=True,
            )
        except Exception as e:
            grades["fail"] = grades.get("fail", 0) + 1
            row["grade"] = "fail"
            row["detail"] = str(e)[:200]
            row["sec"] = round(time.time() - t0, 1)
            row["trace"] = traceback.format_exc()[-500:]
            print(f"  → FAIL {e}", flush=True)

        results.append(row)

    # summary by section
    by_sec: dict[str, dict[str, int]] = {}
    for r in results:
        s = r["section"]
        by_sec.setdefault(s, {"good": 0, "ok": 0, "weak": 0, "fail": 0, "n": 0})
        by_sec[s][r["grade"]] = by_sec[s].get(r["grade"], 0) + 1
        by_sec[s]["n"] += 1

    weak = [r for r in results if r["grade"] in ("weak", "fail")]
    misroutes = [
        r
        for r in results
        if r.get("expected_match") is False
    ]

    summary = {
        "grades": grades,
        "by_section": by_sec,
        "weak_ids": [r["id"] for r in weak],
        "misroute_ids": [r["id"] for r in misroutes],
        "n": len(results),
        "connection": conn,
    }

    out = {"summary": summary, "cases": results}
    path = ROOT / args.out
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 60)
    print("SUMMARY", json.dumps(summary, ensure_ascii=False, indent=2))
    print("SAVED", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
