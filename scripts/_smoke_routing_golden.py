# -*- coding: utf-8 -*-
"""
Эталон роутинга + публичного API UniversalTools (страховка рефакторинга).

  python scripts/_smoke_routing_golden.py --record   # снять эталон → exports/golden_routing.json
  python scripts/_smoke_routing_golden.py            # сверить (exit 1 при расхождении)

Эталон зависит от состояния Qdrant (каталог имён) — снимать и сверять на одном индексе.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

GOLDEN = ROOT / "exports" / "golden_routing.json"

# AGENTS §4 + _smoke_preflight + края/негативы (без имён конкретной CF, кроме тестовых плейсхолдеров)
BASE_PHRASES = [
    "расскажи логику процедуры ЗаполнитьУсловияПродаж",
    "продажи за 2015",
    "почему не заполнился склад в документе",
    "в каких заказах ожидается оплата",
    "задолженность клиентов на конец 2015",
    "маржа по менеджерам за 2015",
    "обороты расчетов с клиентами помесячно за 2015",
    "по каким регистрам проводится реализация",
    "продажи по контрагентам за 2015",
    "какие роли могут удалять справочники",
    "что умеешь",
    "разбери Форма для СПОТ.epf",
    "проверь связь с 1С",
    "кто сейчас в базе",
    "как в запросе сделать левое соединение",
    "отследи вызовы ТестПроцедура",
    "какие поля в макете",
    "события формы",
]

# follow-up с объектом из сессии
CONTEXT_PHRASES = [
    ("какие поля в макете", "Документ.ТестовыйДокумент"),
    ("какие запросы есть", "Документ.ТестовыйДокумент"),
]


def collect_phrases() -> list[str]:
    out = list(BASE_PHRASES)
    try:
        import _eval_accuracy as ea

        out += [c["q"] for c in ea.CASES]
    except Exception as e:  # pragma: no cover
        print("WARN eval_accuracy:", e)
    src = (ROOT / "tests" / "test_unit_core.py").read_text(encoding="utf-8")
    out += [x for x in re.findall(r'plan_preflight\(\s*"([^"]+)"', src)]
    seen: list[str] = []
    for p in out:
        if p not in seen:
            seen.append(p)
    return seen


def loop_hints(user_text: str) -> dict:
    """Флаги need_* из agent.loop (единый роутер)."""
    from tools.router import compute_hints

    return compute_hints(user_text)


def _plain(x):
    return json.loads(json.dumps(x, ensure_ascii=False, default=str))


def snapshot_routing() -> dict:
    from tools.preflight import plan_preflight
    from tools.route_intent import route_user_message

    def pf(p: str, ctx: str = "") -> dict | None:
        plan = plan_preflight(p, context_full_name=ctx)
        if not plan:
            return None
        return _plain({k: plan.get(k) for k in ("tool", "args", "confidence", "format")})

    def rt(p: str) -> dict:
        r = route_user_message(p)
        return _plain({k: r.get(k) for k in ("route", "prefer_tool", "tool_args")})

    rows = {}
    for p in collect_phrases():
        rows[p] = {"preflight": pf(p), "route": rt(p), "hints": loop_hints(p)}
    for p, ctx in CONTEXT_PHRASES:
        rows[f"{p} @ctx={ctx}"] = {"preflight": pf(p, ctx)}
    return rows


def snapshot_api() -> dict:
    from tools import universal as u
    from tools.route_intent import route_prompt_block

    class _NoBackend:
        def __getattr__(self, name):
            raise RuntimeError("no backend in golden snapshot")

    t = u.UniversalTools(_NoBackend(), read_only=True)
    handlers = t.dispatch("__golden_unknown__", {}).get("known") or []
    sigs = {}
    for name, fn in inspect.getmembers(u.UniversalTools, predicate=callable):
        if name.startswith("__"):
            continue
        try:
            sigs[name] = str(inspect.signature(fn))
        except (TypeError, ValueError):
            sigs[name] = "?"
    sha = lambda s: hashlib.sha1(s.encode("utf-8")).hexdigest()  # noqa: E731
    return {
        "tool_definitions": [
            (d.get("function") or {}).get("name") for d in u.TOOL_DEFINITIONS
        ],
        "tool_definitions_sha1": sha(json.dumps(u.TOOL_DEFINITIONS, ensure_ascii=False, sort_keys=True)),
        "openai_tools": [(d.get("function") or {}).get("name") for d in t.openai_tools()],
        "hidden": sorted(u.HIDDEN_FROM_AGENT),
        "agent_visible": sorted(u.agent_visible_tool_names()),
        "dispatch_handlers": sorted(handlers),
        "signatures": sigs,
        "system_prompt_sha1": sha(u._system_prompt_ru()),
        "route_prompt_sha1": sha(route_prompt_block()),
        "reexports": sorted(
            n
            for n in (
                "UniversalTools",
                "TOOL_DEFINITIONS",
                "HIDDEN_FROM_AGENT",
                "_HIDDEN_FROM_LLM",
                "is_agent_visible_tool",
                "agent_visible_tool_names",
                "is_safe_select_query",
                "clamp_limit",
                "_system_prompt_ru",
            )
            if hasattr(u, n)
        ),
    }


def _diff(a, b, path="", out=None, limit=60):
    out = out if out is not None else []
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"+ {path}/{k}: {json.dumps(b[k], ensure_ascii=False)[:200]}")
            elif k not in b:
                out.append(f"- {path}/{k}: {json.dumps(a[k], ensure_ascii=False)[:200]}")
            else:
                _diff(a[k], b[k], f"{path}/{k}", out, limit)
    elif a != b:
        out.append(
            f"~ {path}: {json.dumps(a, ensure_ascii=False)[:200]} → {json.dumps(b, ensure_ascii=False)[:200]}"
        )
    return out


def main() -> int:
    record = "--record" in sys.argv
    cur = {"routing": snapshot_routing(), "api": snapshot_api()}
    if record:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"recorded {len(cur['routing'])} phrases → {GOLDEN}")
        return 0
    if not GOLDEN.exists():
        print("SKIP: нет эталона, запустите с --record")
        return 0
    gold = json.loads(GOLDEN.read_text(encoding="utf-8"))
    diffs = _diff(gold, cur)
    if diffs:
        print(f"FAIL: {len(diffs)} расхождений")
        for d in diffs:
            print(" ", d)
        return 1
    print(f"OK: {len(cur['routing'])} фраз + API совпадают с эталоном")
    return 0


if __name__ == "__main__":
    sys.exit(main())
