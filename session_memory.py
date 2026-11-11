# -*- coding: utf-8 -*-
"""
B + E: structured session memory + diary для 32k context.

Не тащим сырые таблицы в каждый ход — короткий блок фактов + дневник.
last_report (rows) живёт отдельно для local filter «только X».
"""

from __future__ import annotations

from typing import Any


def _clip(s: Any, n: int = 120) -> str:
    t = str(s or "").strip()
    if len(t) <= n:
        return t
    return t[: n - 1] + "…"


class SessionMemory:
    """Память одной CLI/сессии агента (сбрасывается на /reset)."""

    def __init__(self, *, diary_max_chars: int = 1400) -> None:
        self.diary_max_chars = diary_max_chars
        self.diary: list[str] = []
        self.user_turns: int = 0
        self.last_user: str = ""
        self.last_assistant_brief: str = ""
        # structured facts
        self.full_name: str | None = None
        self.period_spec: str | None = None
        self.periods: list[str] = []
        self.group_by: str | None = None
        self.filter_name: str | None = None
        self.last_tool: str | None = None
        self.last_totals: list[dict[str, Any]] = []
        self.pending_clarification: str | None = None
        self.code_objects: list[str] = []
        self.registers_hint: str | None = None
        self.facts_extra: list[str] = []

    def reset(self) -> None:
        self.__init__(diary_max_chars=self.diary_max_chars)

    def note_user(self, text: str) -> None:
        self.user_turns += 1
        self.last_user = (text or "")[:300]

    def note_assistant(self, text: str) -> None:
        t = (text or "").strip().replace("\n", " ")
        self.last_assistant_brief = _clip(t, 220)

    def update_from_report_snapshot(self, snap: dict[str, Any] | None) -> None:
        if not snap:
            return
        if snap.get("full_name"):
            self.full_name = str(snap["full_name"])
        if snap.get("period_spec"):
            self.period_spec = str(snap["period_spec"])[:200]
        if snap.get("periods"):
            self.periods = [str(p) for p in snap["periods"][:12]]
        if snap.get("group_by"):
            self.group_by = str(snap["group_by"])
        if snap.get("tool"):
            self.last_tool = str(snap["tool"])
        if snap.get("filter"):
            self.filter_name = str(snap["filter"])
        sbp = snap.get("summary_by_period")
        if isinstance(sbp, list) and sbp:
            self.last_totals = [x for x in sbp[:8] if isinstance(x, dict)]
        if snap.get("pending_clarification"):
            self.pending_clarification = str(
                snap.get("question") or "нужно уточнение"
            )[:200]
        else:
            self.pending_clarification = None

    def update_from_tool(
        self,
        tool: str,
        result: Any,
        *,
        user_text: str = "",
    ) -> None:
        if not isinstance(result, dict):
            return
        self.last_tool = tool
        if result.get("full_name"):
            self.full_name = str(result["full_name"])
        if result.get("period_spec") or result.get("period"):
            self.period_spec = str(
                result.get("period_spec") or result.get("period") or ""
            )[:200]
        sbp = result.get("summary_by_period")
        if isinstance(sbp, list) and sbp:
            self.last_totals = [x for x in sbp[:8] if isinstance(x, dict)]
            labs = [
                str(x.get("Период"))
                for x in self.last_totals
                if x.get("Период") is not None
            ]
            if labs:
                self.periods = labs
        if result.get("group_by"):
            self.group_by = str(result["group_by"])
        if result.get("filter"):
            self.filter_name = str(result["filter"])
        if result.get("need_clarification"):
            self.pending_clarification = str(
                result.get("question") or result.get("error") or "уточнение"
            )[:200]
        elif result.get("ok") or result.get("rows"):
            self.pending_clarification = None

        # code / movements / structure
        if tool in (
            "analyze_object_movements",
            "inspect_code",
            "get_object_structure",
            "describe_object",
            "describe_form",
            "list_object_attributes",
            "search_code",
        ):
            fn = result.get("full_name") or result.get("full_name_1c")
            if not fn and isinstance(result.get("structure_brief"), dict):
                fn = result["structure_brief"].get("full_name")
            if fn:
                self.full_name = str(fn)
                self._push_code_object(str(fn))
            regs = result.get("register_names_full") or result.get("registers_all")
            if isinstance(regs, list) and regs:
                names = [str(x) for x in regs[:20]]
                self.registers_hint = f"{len(regs)} шт.: " + ", ".join(names[:8])
                if len(regs) > 8:
                    self.registers_hint += "…"

        # diary line for important tools
        if tool in (
            "compare_periods",
            "build_report",
            "linked_query",
            "margin_report",
            "analyze_object_movements",
            "get_object_structure",
            "describe_object",
        ):
            line = self._tool_diary_line(tool, result, user_text=user_text)
            if line:
                self.append_diary(line)

    def _push_code_object(self, fn: str) -> None:
        if not fn:
            return
        if fn in self.code_objects:
            self.code_objects.remove(fn)
        self.code_objects.insert(0, fn)
        self.code_objects = self.code_objects[:8]

    def _tool_diary_line(
        self, tool: str, result: dict[str, Any], *, user_text: str = ""
    ) -> str | None:
        if result.get("need_clarification"):
            return f"clarify/{tool}: {_clip(result.get('question'), 100)}"
        if result.get("error") and not result.get("rows"):
            return f"err/{tool}: {_clip(result.get('error'), 100)}"
        fn = result.get("full_name") or ""
        if tool in ("compare_periods", "summarize_periods", "build_report"):
            sbp = result.get("summary_by_period") or []
            bits = []
            if isinstance(sbp, list):
                for p in sbp[:4]:
                    if isinstance(p, dict) and p.get("Период") is not None:
                        bits.append(f"{p.get('Период')}={p.get('Сумма', '?')}")
            filt = result.get("filter") or ""
            return (
                f"report/{tool}: {fn or '?'} "
                f"{'[' + filt + '] ' if filt else ''}"
                f"{'; '.join(bits) or result.get('period_spec') or ''} "
                f"rows={result.get('count') or result.get('row_count') or '?'}"
            ).strip()
        if tool == "linked_query":
            return f"linked: {fn or result.get('route') or ''} n={result.get('row_count') or result.get('count') or '?'}"
        if tool == "analyze_object_movements":
            regs = result.get("register_names_full") or result.get("registers") or []
            n = len(regs) if isinstance(regs, list) else "?"
            return f"movements: {fn} registers={n}"
        if tool == "get_object_structure":
            return f"structure: {fn} attrs={result.get('attribute_count') or '?'}"
        return None

    def append_diary(self, line: str) -> None:
        line = _clip(line, 200)
        if not line:
            return
        # de-dup last
        if self.diary and self.diary[-1] == line:
            return
        self.diary.append(line)
        self._fit_diary()

    def append_turn_diary(self, user: str, assistant: str, tools: list[str]) -> None:
        u = _clip(user, 80)
        a = _clip(assistant, 100)
        t = ",".join(tools[:6]) if tools else "—"
        self.append_diary(f"ход: «{u}» → [{t}] {_clip(a, 80)}")

    def _fit_diary(self) -> None:
        while self.diary and sum(len(x) + 1 for x in self.diary) > self.diary_max_chars:
            self.diary.pop(0)

    def maybe_compact_diary(self, *, force: bool = False, pressure: float = 0.0) -> bool:
        """
        E: при давлении бюджета схлопнуть diary в 3–6 буллетов-фактов.
        Без LLM — эвристика (для 32k/local offline).
        """
        if not force and pressure < 0.72 and len(self.diary) < 10:
            return False
        if len(self.diary) < 3:
            return False
        # оставить последние + ключевые report/movements
        keep: list[str] = []
        for line in self.diary:
            if any(
                x in line
                for x in (
                    "report/",
                    "movements:",
                    "structure:",
                    "linked:",
                    "clarify/",
                )
            ):
                keep.append(line)
        keep = keep[-6:]
        tail = self.diary[-3:]
        merged: list[str] = []
        for x in keep + tail:
            if x not in merged:
                merged.append(x)
        self.diary = merged[-8:]
        self.append_diary("(дневник сжат под context budget)")
        return True

    def format_block(self, *, max_chars: int = 1100) -> str:
        """B: блок для подмешивания в messages (не system prompt целиком)."""
        lines = ["[память сессии — используй для follow-up, не спрашивай заново если есть]"]
        if self.full_name:
            lines.append(f"объект: {self.full_name}")
        if self.periods:
            lines.append(f"периоды: {', '.join(self.periods)}")
        elif self.period_spec:
            lines.append(f"period_spec: {_clip(self.period_spec, 100)}")
        if self.group_by:
            lines.append(f"group_by: {self.group_by}")
        if self.filter_name:
            lines.append(f"фильтр: {self.filter_name}")
        if self.last_totals:
            bits = []
            for t in self.last_totals[:6]:
                lab = t.get("Период")
                sm = t.get("Сумма")
                if lab is not None:
                    bits.append(f"{lab}={sm}")
            if bits:
                lines.append("итоги: " + "; ".join(bits))
        if self.last_tool:
            lines.append(f"последний tool: {self.last_tool}")
        if self.pending_clarification:
            lines.append(f"ожидает уточнения: {_clip(self.pending_clarification, 120)}")
        if self.code_objects:
            lines.append("код/объекты: " + ", ".join(self.code_objects[:5]))
        if self.registers_hint:
            lines.append(f"регистры: {_clip(self.registers_hint, 160)}")
        if self.diary:
            lines.append("дневник:")
            for d in self.diary[-8:]:
                lines.append(f"  • {d}")
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[: max_chars - 20] + "\n…[memory_trim]"
        return text

    def should_inject(self) -> bool:
        return bool(
            self.full_name
            or self.periods
            or self.diary
            or self.pending_clarification
            or self.code_objects
            or self.filter_name
        )
