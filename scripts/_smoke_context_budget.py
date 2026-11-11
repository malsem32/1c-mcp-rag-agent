# -*- coding: utf-8 -*-
"""Smoke A–E: context budget, turn trim, per-tool compact, session memory."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from context_budget import (
    budget_pressure,
    chars_budget,
    compact_json,
    messages_total_chars,
    split_into_turns,
    summarize_tool_payload,
    tool_budget,
    trim_messages,
)
from session_memory import SessionMemory


def test_a_budget() -> None:
    b = chars_budget(32000, reserve_output=3072, reserve_tools_schema=7000)
    assert b > 40_000, b
    assert b < 80_000, b
    print("OK A chars_budget 32k =", b)


def test_d_tool_budgets() -> None:
    assert tool_budget("compare_periods", 8000) == 10000  # per-tool до 1.5× default_max
    assert tool_budget("compare_periods", 12000) == 10000  # tool table
    assert tool_budget("get_object_structure", 20000) == 12000
    assert tool_budget("find_metadata", 8000) == 5000
    big = {
        "ok": True,
        "summary_by_period": [
            {"Период": "2016", "Сумма": 1},
            {"Период": "2015", "Сумма": 2},
        ],
        "prefer": "MUST both",
        "rows": [
            {"Период": y, "Контрагент": f"K{i}", "Сумма": i}
            for y in ("2016", "2015")
            for i in range(40)
        ],
    }
    s = compact_json(big, 8000, tool_name="compare_periods")
    assert "summary_by_period" in s
    assert "2015" in s and "2016" in s
    print("OK D compact per-tool", len(s))


def test_c_turn_trim() -> None:
    msgs: list[dict] = [{"role": "system", "content": "sys"}]
    for t in range(10):
        msgs.append({"role": "user", "content": f"вопрос номер {t} про продажи"})
        msgs.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"c{t}",
                        "type": "function",
                        "function": {
                            "name": "compare_periods",
                            "arguments": "{}",
                        },
                    }
                ],
            }
        )
        payload = json.dumps(
            {
                "ok": True,
                "full_name": "Документ.X",
                "summary_by_period": [{"Период": "2015", "Сумма": t}],
                "rows": [{"x": i, "pad": "я" * 50} for i in range(30)],
            },
            ensure_ascii=False,
        )
        msgs.append({"role": "tool", "tool_call_id": f"c{t}", "content": payload})
        msgs.append(
            {
                "role": "user",
                "content": "Уже выполнен tool `compare_periods` (preflight). Это ответ по ДАННЫМ.",
            }
        )
        msgs.append({"role": "assistant", "content": f"Ответ за ход {t}: сумма {t}"})

    turns = split_into_turns([m for m in msgs if m["role"] != "system"])
    assert len(turns) == 10, len(turns)

    trimmed = trim_messages(
        msgs,
        max_messages=100,
        max_total_chars=50_000,
        max_turns=6,
        keep_recent_tool_full=2,
        old_tool_chars=700,
    )
    rest = [m for m in trimmed if m.get("role") != "system"]
    turns2 = split_into_turns(rest)
    assert len(turns2) <= 6, len(turns2)

    # старые tool сжаты
    tool_lens = [
        len(m["content"])
        for m in rest
        if m.get("role") == "tool" and isinstance(m.get("content"), str)
    ]
    assert tool_lens, "no tools"
    # последние 2 могут быть длиннее
    oldish = tool_lens[:-2] if len(tool_lens) > 2 else []
    for L in oldish:
        assert L <= 750, L
    print(
        "OK C turns",
        len(turns2),
        "tool_lens",
        tool_lens,
        "chars",
        messages_total_chars(trimmed),
    )


def test_summarize_tool() -> None:
    raw = json.dumps(
        {
            "ok": True,
            "full_name": "Документ.ТестовыйДокументПродаж",
            "summary_by_period": [
                {"Период": "2016", "Сумма": 9},
                {"Период": "2015", "Сумма": 4},
            ],
            "manager_summary": {"message": "ok", "periods": ["2016", "2015"]},
            "rows": [{"a": 1}] * 20,
            "prefer": "show both",
        },
        ensure_ascii=False,
    )
    s = summarize_tool_payload(raw * 3, max_chars=700)
    assert "_old_tool_summary" in s
    assert "2015" in s
    print("OK summarize", len(s))


def test_b_e_memory() -> None:
    mem = SessionMemory(diary_max_chars=1400)
    mem.note_user("сравни продажи 2015 2016")
    mem.update_from_tool(
        "compare_periods",
        {
            "ok": True,
            "full_name": "Документ.ТестовыйДокументПродаж",
            "period_spec": "2015 и 2016",
            "summary_by_period": [
                {"Период": "2016", "Сумма": 9_555_027},
                {"Период": "2015", "Сумма": 4_271_248},
            ],
            "count": 16,
        },
    )
    assert mem.should_inject()
    block = mem.format_block()
    assert "ТестовыйДокументПродаж" in block
    assert "2015" in block and "2016" in block
    print("OK B memory block\n", block[:400])

    for i in range(12):
        mem.append_diary(f"report/compare_periods: step {i} " + ("x" * 40))
    assert mem.maybe_compact_diary(force=True, pressure=0.9)
    assert len(mem.diary) <= 10
    print("OK E diary compact", mem.diary)


def test_pressure() -> None:
    msgs = [{"role": "user", "content": "a" * 1000} for _ in range(10)]
    p = budget_pressure(msgs, max_total_chars=5000)
    assert p > 1.0
    print("OK pressure", round(p, 2))


if __name__ == "__main__":
    test_a_budget()
    test_d_tool_budgets()
    test_c_turn_trim()
    test_summarize_tool()
    test_b_e_memory()
    test_pressure()
    print("ALL CONTEXT OK")
