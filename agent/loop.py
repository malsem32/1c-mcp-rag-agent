"""
AI-агент с tool-calling через OpenAI-compatible API.

Рассчитан на context window 32k (LM Studio): ужимает tool results и историю.
"""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Any

from openai import OpenAI

from context_budget import (
    budget_pressure,
    chars_budget,
    compact_json,
    messages_total_chars,
    trim_messages,
)
from session_memory import SessionMemory
from tools.universal import UniversalTools


def _message_to_dict(msg: Any) -> dict[str, Any]:
    if isinstance(msg, dict):
        data = dict(msg)
        if not (data.get("content") or "").strip():
            extra = data.get("reasoning") or data.get("reasoning_content") or ""
            if extra:
                data["content"] = extra if isinstance(extra, str) else str(extra)
        return data
    content = getattr(msg, "content", None) or ""
    if not str(content).strip():
        extra = getattr(msg, "reasoning", None) or getattr(msg, "reasoning_content", None)
        if extra:
            content = extra if isinstance(extra, str) else str(extra)
    data: dict[str, Any] = {
        "role": getattr(msg, "role", "assistant"),
        "content": content or "",
    }
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        data["tool_calls"] = []
        for tc in tool_calls:
            if isinstance(tc, dict):
                data["tool_calls"].append(tc)
            else:
                data["tool_calls"].append(
                    {
                        "id": getattr(tc, "id", ""),
                        "type": "function",
                        "function": {
                            "name": getattr(tc.function, "name", ""),
                            "arguments": getattr(tc.function, "arguments", "{}") or "{}",
                        },
                    }
                )
    return data


def _parse_arguments(raw: str | dict | None) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    raw = raw.strip() if isinstance(raw, str) else "{}"
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(raw[start : end + 1])
                return value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                pass
        return {"_raw": raw, "_parse_error": True}


class EmptyLLMResponse(RuntimeError):
    """OpenRouter/GLM иногда отдаёт 200 без choices (error в теле или reasoning съел лимит)."""


def _completion_error_text(completion: Any) -> str:
    err = getattr(completion, "error", None)
    if err is None and isinstance(completion, dict):
        err = completion.get("error")
    if err is None:
        return ""
    if isinstance(err, dict):
        return str(err.get("message") or err)
    return str(err)


def _first_choice(completion: Any) -> Any:
    if completion is None:
        raise EmptyLLMResponse("LLM вернул пустой объект")
    err_text = _completion_error_text(completion)
    if err_text:
        raise EmptyLLMResponse(err_text)
    choices = getattr(completion, "choices", None)
    if choices is None and isinstance(completion, dict):
        choices = completion.get("choices")
    if not choices:
        raise EmptyLLMResponse(
            "LLM вернул ответ без choices (часто reasoning GLM исчерпал max_tokens)"
        )
    choice = choices[0]
    if choice is None:
        raise EmptyLLMResponse("LLM вернул пустой choice")
    return choice


def _is_rate_limit(exc: BaseException) -> bool:
    text = str(exc)
    if "429" in text or "rate-limited" in text.lower() or "rate_limit" in text.lower():
        return True
    status = getattr(exc, "status_code", None)
    return status == 429


def _is_retryable_llm(exc: BaseException) -> bool:
    if _is_rate_limit(exc):
        return True
    if isinstance(exc, EmptyLLMResponse):
        text = str(exc).lower()
        if "cannot be disabled" in text:
            return False
        if re.search(r"\b400\b", text) and "429" not in text:
            return False
        return True
    return False


def _retry_after_seconds(exc: BaseException, *, attempt: int) -> float:
    text = str(exc)
    m = re.search(r"retry_after_seconds['\"]?\s*[:=]\s*(\d+)", text, re.I)
    if m:
        return max(1.0, float(m.group(1)))
    m = re.search(r"Retry-After['\"]?\s*[:=]\s*['\"]?(\d+)", text, re.I)
    if m:
        return max(1.0, float(m.group(1)))
    return min(30.0, 5.0 * (attempt + 1))


class Agent:
    def __init__(
        self,
        tools: UniversalTools,
        *,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 3072,
        max_tool_rounds: int = 8,
        context_window: int = 32000,
        tool_result_max_chars: int = 8000,
        history_max_messages: int = 40,
        history_max_turns: int = 6,
        keep_recent_tool_full: int = 2,
        old_tool_chars: int = 700,
        diary_max_chars: int = 1400,
        diary_trigger_turns: int = 4,
        diary_pressure: float = 0.72,
        system_prompt: str | None = None,
    ) -> None:
        self.tools = tools
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_tool_rounds = max_tool_rounds
        self.context_window = context_window
        self.tool_result_max_chars = tool_result_max_chars
        self.history_max_messages = history_max_messages
        self.history_max_turns = history_max_turns
        self.keep_recent_tool_full = keep_recent_tool_full
        self.old_tool_chars = old_tool_chars
        self.diary_trigger_turns = diary_trigger_turns
        self.diary_pressure = diary_pressure
        self.history_char_budget = chars_budget(
            context_window, reserve_output=max_tokens, reserve_tools_schema=7000
        )
        if system_prompt:
            self.system_prompt = system_prompt
        else:
            try:
                from tools.universal import _system_prompt_ru

                self.system_prompt = _system_prompt_ru()
            except Exception:
                from tools.route_intent import route_prompt_block

                self.system_prompt = (
                    "Помощник по 1С (только чтение). Отвечай по-русски.\n"
                    + route_prompt_block()
                )
        self.base_url = (base_url or "").rstrip("/")
        self.client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0)
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt}
        ]
        # последний data-отчёт (полные rows до compact) — для follow-up «только X»
        self.last_report: dict[str, Any] | None = None
        # B+E: structured memory + diary
        self.memory = SessionMemory(diary_max_chars=diary_max_chars)

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": self.system_prompt}]
        self.last_report = None
        self.memory.reset()

    def _remember_report(
        self,
        tool: str,
        result: Any,
        *,
        user_text: str = "",
        args: dict[str, Any] | None = None,
    ) -> None:
        try:
            from tools.dialog_context import snapshot_from_result

            snap = snapshot_from_result(
                tool, result, user_text=user_text, args=args
            )
            if snap:
                # need_clarification без rows — не затирать прошлый полный отчёт
                if snap.get("pending_clarification") and self.last_report:
                    prev = dict(self.last_report)
                    prev["pending_clarification"] = True
                    prev["question"] = snap.get("question") or prev.get("question")
                    prev["user_text"] = user_text or prev.get("user_text")
                    prev["pending_args"] = dict(args or {})
                    self.last_report = prev
                    self.memory.update_from_report_snapshot(prev)
                else:
                    self.last_report = snap
                    self.memory.update_from_report_snapshot(snap)
                # для analyst_report.drill / local filter внутри tools
                try:
                    self.tools._last_report_snapshot = self.last_report
                except Exception:
                    pass
            self.memory.update_from_tool(tool, result, user_text=user_text)
        except Exception:
            try:
                self.memory.update_from_tool(tool, result, user_text=user_text)
            except Exception:
                pass

    def _prepare_messages(self) -> list[dict[str, Any]]:
        return trim_messages(
            self.messages,
            max_messages=self.history_max_messages,
            max_total_chars=self.history_char_budget,
            max_turns=self.history_max_turns,
            keep_recent_tool_full=self.keep_recent_tool_full,
            old_tool_chars=self.old_tool_chars,
        )

    def _inject_session_memory(self, *, verbose: bool = False) -> None:
        """B: короткий блок фактов сессии перед LLM (не раздувая system)."""
        if not self.memory.should_inject():
            return
        block = self.memory.format_block(max_chars=1100)
        # не дублировать подряд
        for m in reversed(self.messages[-4:]):
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                if m["content"].startswith("[память сессии"):
                    m["content"] = block
                    return
        self.messages.append({"role": "user", "content": block})
        if verbose:
            print(
                f"[memory] inject {len(block)} chars | turns={self.memory.user_turns}",
                file=sys.stderr,
            )

    def _finish_turn(
        self,
        user_text: str,
        answer: str,
        tools_used: set[str],
        *,
        verbose: bool = False,
    ) -> str:
        """E: diary + pressure after ответа."""
        self.memory.note_assistant(answer)
        self.memory.append_turn_diary(
            user_text, answer, sorted(tools_used)
        )
        pressure = budget_pressure(
            self.messages, max_total_chars=self.history_char_budget
        )
        force = (
            self.memory.user_turns >= self.diary_trigger_turns
            and self.memory.user_turns % self.diary_trigger_turns == 0
        )
        if self.memory.maybe_compact_diary(force=force, pressure=pressure):
            if verbose:
                print(
                    f"[memory] diary compact pressure={pressure:.2f} "
                    f"chars={messages_total_chars(self.messages)}",
                    file=sys.stderr,
                )
        elif verbose and pressure > 0.5:
            print(
                f"[memory] pressure={pressure:.2f} "
                f"budget={self.history_char_budget} "
                f"msgs={len(self.messages)}",
                file=sys.stderr,
            )
        return answer

    def _openrouter_kwargs(self) -> dict[str, Any]:
        if "openrouter.ai" not in self.base_url:
            return {}
        # :free GLM часто 429 в общем пуле (Decart). Reasoning у GLM 5.2
        # обязателен — не трогаем. Fallback — другие бесплатные модели.
        kwargs: dict[str, Any] = {
            "extra_headers": {
                "HTTP-Referer": "https://localhost/rag-agent",
                "X-Title": "1C RAG Agent",
            },
        }
        if ":free" in (self.model or ""):
            kwargs["extra_body"] = {
                "models": [
                    "minimax/minimax-m2.7:free",
                    "google/gemma-4-31b-it:free",
                ],
            }
        return kwargs

    def _create_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        with_tools: bool = True,
        verbose: bool = False,
    ) -> Any:
        try:
            from tools.tool_umbrella import visible_tool_text

            messages = [
                {**m, "content": visible_tool_text(m["content"])}
                if isinstance(m.get("content"), str) and m.get("role") in ("system", "user", "tool")
                else m
                for m in messages
            ]
        except Exception:
            pass
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            **self._openrouter_kwargs(),
        }
        if with_tools:
            kwargs["tools"] = self.tools.openai_tools(getattr(self, "_turn_tools", None))
            kwargs["tool_choice"] = "auto"
        last: BaseException | None = None
        attempts = 6
        for attempt in range(attempts):
            try:
                completion = self.client.chat.completions.create(**kwargs)
                _first_choice(completion)
                return completion
            except Exception as e:
                last = e
                if not _is_retryable_llm(e) or attempt >= attempts - 1:
                    raise
                wait = _retry_after_seconds(e, attempt=attempt)
                if verbose:
                    print(
                        f"[agent] LLM сбой ({e}), повтор через {wait:.0f}с "
                        f"({attempt + 1}/{attempts})",
                        file=sys.stderr,
                    )
                time.sleep(wait)
        assert last is not None
        raise last

    def _chat(self, messages: list[dict[str, Any]], *, verbose: bool = False) -> Any:
        return self._create_completion(messages, with_tools=True, verbose=verbose)

    def run(self, user_text: str, *, verbose: bool = True) -> str:
        self.memory.note_user(user_text)

        # --- Диалог: follow-up не должен терять период/объект прошлого отчёта ---
        effective_text = user_text
        dialog_state = self.last_report
        try:
            from tools.dialog_context import (
                expand_with_context,
                extract_state_from_messages,
            )

            if not dialog_state:
                dialog_state = extract_state_from_messages(self.messages)
            expanded = expand_with_context(user_text, dialog_state)
            if expanded and expanded != user_text:
                effective_text = expanded
                if verbose:
                    print(
                        f"[context] expand: {user_text!r} → {effective_text!r}",
                        file=sys.stderr,
                    )
        except Exception as e:
            if verbose:
                print(f"[context] expand skip: {e}", file=sys.stderr)

        # в историю кладём оригинал; если expand — пометку для модели
        self.messages.append({"role": "user", "content": user_text})
        if effective_text != user_text:
            self.messages.append(
                {
                    "role": "user",
                    "content": (
                        "[контекст диалога] Короткая реплика пользователя "
                        f"развернута с учётом прошлого отчёта: {effective_text!r}. "
                        "Не спрашивай период/объект заново, если они уже здесь. "
                        "Используй compare_periods / build_report с этой фразой."
                    ),
                }
            )

        # B: structured memory block
        self._inject_session_memory(verbose=verbose)

        # Подмножество tools по категории вопроса (меньше путаницы для 20–30B и −10 тыс. символов)
        self._turn_tools = None
        try:
            from tools.tool_subsets import tool_subset_for

            self._turn_tools = tool_subset_for(effective_text)
            if verbose and self._turn_tools is not None:
                print(f"[agent] tools subset: {len(self._turn_tools)}", file=sys.stderr)
        except Exception as e:
            if verbose:
                print(f"[agent] tools subset skip: {e}", file=sys.stderr)

        # Изменение данных («удали / проведи / поменяй …») — агент read-only: ответ без LLM и tools
        try:
            from tools.router import classify as _classify

            _d0 = _classify(user_text) or {}
            if _d0.get("route") == "refuse_readonly" and _d0.get("reply"):
                self.messages.append({"role": "assistant", "content": _d0["reply"]})
                return self._finish_turn(user_text, _d0["reply"], set(), verbose=verbose)
        except Exception as e:
            if verbose:
                print(f"[agent] refuse check skip: {e}", file=sys.stderr)

        # --- Preflight: high-confidence tool ДО первого LLM-round ---
        preflight_done = False
        preflight_tool: str | None = None
        try:
            from tools.preflight import run_preflight
            from tools.dialog_context import try_local_filter

            def _dispatch(name: str, args: dict[str, Any]) -> Any:
                return self.tools.dispatch(name, args)

            # 0) активный пошаговый мастер (редко) — иначе report_builder AUTO
            wiz = getattr(self.tools, "_report_wizard", None)
            if isinstance(wiz, dict) and wiz.get("active"):
                try:
                    from tools.report_wizard import wants_step_wizard

                    still_wizard = wants_step_wizard(user_text)
                except Exception:
                    still_wizard = False
                if still_wizard or (
                    len((user_text or "").strip()) < 40
                    and not any(
                        x in (user_text or "").lower()
                        for x in (
                            "excel",
                            "отчет",
                            "отчёт",
                            "продаж",
                            "остатк",
                            "отмена",
                        )
                    )
                ):
                    preflight_done = True
                    tool = "report_builder"
                    args = {
                        "wizard_answer": user_text,
                        "request": user_text,
                        "skip_wizard": False,
                    }
                    result = self.tools.report_builder(
                        request=user_text,
                        wizard_answer=user_text,
                        skip_wizard=False,
                    )
                    self._remember_report(
                        tool, result, user_text=user_text, args=args
                    )
                    if verbose:
                        print(
                            f"[wizard] step → need_clarification="
                            f"{result.get('need_clarification')} ready="
                            f"{result.get('wizard_ready')} path={bool(result.get('path'))}",
                            file=sys.stderr,
                        )
                    call_id = "preflight_report_wizard"
                    self.messages.append(
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": call_id,
                                    "type": "function",
                                    "function": {
                                        "name": tool,
                                        "arguments": json.dumps(
                                            args, ensure_ascii=False
                                        ),
                                    },
                                }
                            ],
                        }
                    )
                    payload = compact_json(
                        result, self.tool_result_max_chars, tool_name=tool
                    )
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": payload,
                        }
                    )
                    if result.get("need_clarification") and result.get("question"):
                        self.messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Мастер Excel. Покажи question КАК ЕСТЬ."
                                ),
                            }
                        )
                    elif result.get("path") or result.get("ok"):
                        self.messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Отчёт готов. Покажи path/download_url и "
                                    "листы/диаграммы. Не example.com."
                                ),
                            }
                        )
                else:
                    # уходим в auto: сбросить мастер
                    try:
                        self.tools._report_wizard = None
                    except Exception:
                        pass

            # 1) «только [фильтр]» → фильтр прошлого result (оба периода сохраняются)
            local = None if preflight_done else try_local_filter(user_text, dialog_state)
            if local:
                preflight_done = True
                tool = "compare_periods"
                args = {
                    "search_query": effective_text,
                    "period": str(
                        (dialog_state or {}).get("period_spec") or effective_text
                    ),
                    "group_by": (dialog_state or {}).get("group_by") or "",
                    "_dialog_filter": True,
                }
                result = local
                self._remember_report(
                    tool, result, user_text=user_text, args=args
                )
                if verbose:
                    print(
                        f"[context] local_filter ok rows={result.get('count')} "
                        f"filter={result.get('filter')!r}",
                        file=sys.stderr,
                    )
                call_id = f"preflight_{tool}_filter"
                self.messages.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": tool,
                                    "arguments": json.dumps(
                                        args, ensure_ascii=False
                                    ),
                                },
                            }
                        ],
                    }
                )
                payload = compact_json(
                    result, self.tool_result_max_chars, tool_name=tool
                )
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": payload,
                    }
                )
                self.messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Это фильтр ПРЕДЫДУЩЕГО отчёта (диалог). "
                            "Покажи summary_by_period + rows. "
                            "НЕ спрашивай период. НЕ вызывай linked_query."
                        ),
                    }
                )
                if verbose:
                    preview = payload if len(payload) < 300 else payload[:300] + "…"
                    print(
                        f"[preflight result] {len(payload)} chars | {preview}",
                        file=sys.stderr,
                    )
            elif not preflight_done:
                ctx_fn = (
                    (self.memory.full_name or "")
                    or (
                        str(self.last_report.get("full_name") or "")
                        if isinstance(self.last_report, dict)
                        else ""
                    )
                )
                pf = run_preflight(
                    effective_text,
                    _dispatch,
                    min_confidence=0.78,
                    context_full_name=ctx_fn,
                )
                if pf:
                    preflight_done = True
                    tool = pf["tool"]
                    preflight_tool = str(tool)
                    args = pf["args"]
                    result = pf["result"]
                    self._remember_report(
                        tool, result, user_text=user_text, args=args
                    )
                    if verbose:
                        print(
                            f"[preflight] {tool}({json.dumps(args, ensure_ascii=False)}) "
                            f"ok={pf.get('ok')} conf={pf['plan'].get('confidence')}",
                            file=sys.stderr,
                        )
                    # synthetic tool_call so LLM видит результат как обычный tool turn
                    call_id = f"preflight_{tool}"
                    self.messages.append(
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": call_id,
                                    "type": "function",
                                    "function": {
                                        "name": tool,
                                        "arguments": json.dumps(
                                            args, ensure_ascii=False
                                        ),
                                    },
                                }
                            ],
                        }
                    )
                    payload = compact_json(
                        result, self.tool_result_max_chars, tool_name=tool
                    )
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": payload,
                        }
                    )
                    self.messages.append(
                        {"role": "user", "content": pf["nudge"]}
                    )
                    if verbose:
                        preview = (
                            payload if len(payload) < 300 else payload[:300] + "…"
                        )
                        print(
                            f"[preflight result] {len(payload)} chars | {preview}",
                            file=sys.stderr,
                        )
        except Exception as e:
            if verbose:
                print(f"[preflight] skip: {e}", file=sys.stderr)

        # Универсальные хинты по типу вопроса (единый роутер, не привязаны к одному документу)
        try:
            from tools.object_movements import (
                is_empty_code_paste,
                looks_like_fake_progress,
                looks_like_false_fix_claim,
            )
        except Exception:
            looks_like_fake_progress = lambda _t: False  # noqa: E731
            looks_like_false_fix_claim = lambda _t: False  # noqa: E731
            is_empty_code_paste = lambda _p: False  # noqa: E731
        from tools.router import compute_hints

        _hints = compute_hints(user_text)
        need_movements = _hints["movements"]
        need_fill = _hints["fill"]
        need_debt = _hints["debt"]
        need_status = _hints["status"]
        need_code = _hints["code"]
        need_report = _hints["report"]
        need_attr = _hints["attr"]
        need_epf = _hints["epf"]
        tools_used: set[str] = set()
        # preflight уже вызвал tool — учесть в tools_used / не дублировать nudge
        if preflight_tool:
            tools_used.add(preflight_tool)
        if preflight_done:
            try:
                # все assistant tool_calls в хвосте (preflight + …)
                for m in reversed(self.messages[-12:]):
                    if m.get("role") == "assistant" and m.get("tool_calls"):
                        for tc in m["tool_calls"]:
                            n = (tc.get("function") or {}).get("name") or ""
                            if n:
                                tools_used.add(n)
            except Exception:
                pass
        # meta/structure уже ответил preflight — НЕ пинать в build_report
        META_TOOLS = frozenset(
            {
                "describe_object",
                "get_object_structure",
                "list_object_attributes",
                "describe_form",
                "resolve_object",
                "find_metadata",
                "agent_capabilities",
                "query_syntax_help",
                "code_help",
            }
        )
        CODE_AUDIT_TOOLS = frozenset(
            {
                "audit_query_code",
                "scan_code_injection",
                "inspect_code",
                "search_code",
                "analyze_locks",
                "recommend_managed_locks",
                "trace_calls",
                "find_callers",
                "read_code",
                "analyze_object_movements",
                "explain_fill",
            }
        )
        if tools_used & META_TOOLS or (
            preflight_tool and preflight_tool in META_TOOLS
        ):
            need_report = False
        if tools_used & CODE_AUDIT_TOOLS or (
            preflight_tool and preflight_tool in CODE_AUDIT_TOOLS
        ):
            need_report = False
            need_code = True
        progress_nudge_done = False
        fix_claim_nudge_done = False
        # пустой ``` от пользователя — сразу честный ответ, без «исправленного» кода
        if is_empty_code_paste(user_text):
            return self._finish_turn(
                user_text,
                (
                    "Пустой блок кода (``` без содержимого). "
                    "Я **не меняю** конфигурацию и не подставляю «исправленный» код за вас.\n\n"
                    "Напишите, что нужно:\n"
                    "• разобрать место в CF (path/процедура из прошлого ответа);\n"
                    "• **рекомендация / пример** BSL (помечу «НЕ применено»);\n"
                    "• цитата **как есть** из выгрузки (read_code)."
                ),
                tools_used,
                verbose=verbose,
            )
        # если preflight уже сделал movements/debt/report/meta — не nudge впустую
        movements_nudge_done = "analyze_object_movements" in tools_used
        epf_nudge_done = "inspect_external_processing" in tools_used
        code_nudge_done = bool(
            tools_used
            & {
                "inspect_code",
                "inspect_external_processing",
                "search_code",
                "find_modules",
                "read_code",
                "analyze_object_movements",
                "explain_fill",
                "trace_calls",
                "describe_object",
                "get_object_structure",
                "describe_form",
                "audit_query_code",
                "scan_code_injection",
                "analyze_locks",
                "recommend_managed_locks",
            }
        )
        fill_nudge_done = "explain_fill" in tools_used
        debt_nudge_done = "linked_query" in tools_used or "build_report" in tools_used
        status_nudge_done = bool(
            tools_used
            & {
                "get_object_structure",
                "describe_object",
                "list_object_attributes",
            }
        )
        # preflight audit/meta уже ответил — НЕ пинать в build_report
        report_nudge_done = bool(
            tools_used
            & {
                "build_report",
                "query_by_attributes",
                "linked_query",
                "margin_report",
                "compare_periods",
                "export_object_excel",
                "describe_object",
                "get_object_structure",
                "describe_form",
                "analyst_report",
                "report_builder",
                "resolve_object",
                "audit_query_code",
                "scan_code_injection",
                "inspect_code",
                "search_code",
            }
        ) or bool(
            preflight_tool
            and preflight_tool in (META_TOOLS | CODE_AUDIT_TOOLS)
        )

        CODE_TOOLS = frozenset(
            {
                "analyze_object_movements",
                "inspect_code",
                "inspect_external_processing",
                "search_code",
                "find_modules",
                "read_code",
                "module_outline",
                "read_skd_query",
                "trace_calls",
                "explain_fill",
                "find_callers",
            }
        )
        REPORT_TOOLS = frozenset(
            {
                "build_report",
                "query_by_attributes",
                "linked_query",
                "margin_report",
                "compare_periods",
                "summarize_periods",
                "export_object_excel",
                "export_document_period_excel",
                "export_excel_report",
            }
        )

        for round_i in range(self.max_tool_rounds):
            prepared = self._prepare_messages()
            try:
                completion = self._chat(prepared, verbose=verbose)
                choice = _first_choice(completion)
                msg = getattr(choice, "message", None)
                if msg is None:
                    raise EmptyLLMResponse("LLM вернул choice без message")
            except Exception as e:
                err = f"Ошибка LLM API: {e}"
                if verbose:
                    print(f"[agent] {err}", file=sys.stderr)
                return self._finish_turn(
                    user_text, err, tools_used, verbose=verbose
                )

            msg_dict = _message_to_dict(msg)
            self.messages.append(msg_dict)

            tool_calls = msg_dict.get("tool_calls") or []
            if not tool_calls:
                content = (msg_dict.get("content") or "").strip()

                # --- принуждение: регистры/проведение без code-tools ---
                if (
                    need_movements
                    and not movements_nudge_done
                    and "analyze_object_movements" not in tools_used
                    and not (tools_used & CODE_TOOLS)
                    and round_i < self.max_tool_rounds - 1
                ):
                    movements_nudge_done = True
                    if verbose:
                        print(
                            "[agent] nudge: movements question without code tools",
                            file=sys.stderr,
                        )
                    self.messages.append(
                        {
                            "role": "user",
                            "content": (
                                "СТОП. Вопрос про регистры/движения/проведение. "
                                "get_object_structure НЕДОСТАТОЧЕН. "
                                "Сейчас вызови tool analyze_object_movements("
                                f"request={user_text!r}"
                                ") — можно добавить full_name если уже известен. "
                                "Не пиши «подождите/анализирую». После tool — ответ по summary/registers."
                            ),
                        }
                    )
                    continue

                if (
                    need_debt
                    and not debt_nudge_done
                    and "linked_query" not in tools_used
                    and "build_report" not in tools_used
                    and round_i < self.max_tool_rounds - 1
                ):
                    debt_nudge_done = True
                    if verbose:
                        print("[agent] nudge: debt/unpaid without data tools", file=sys.stderr)
                    self.messages.append(
                        {
                            "role": "user",
                            "content": (
                                "СТОП. Задолженность/остатки/несколько источников. "
                                f"Вызови linked_query(request={user_text!r}, execute=true) "
                                "или build_report(request=…). "
                                "НЕ сырой execute_query. Имена из этой базы. "
                                "В ответе: manager_summary + таблица rows."
                            ),
                        }
                    )
                    continue

                if (
                    need_report
                    and not report_nudge_done
                    and not (tools_used & REPORT_TOOLS)
                    and not (tools_used & META_TOOLS)
                    and not (
                        preflight_tool
                        and preflight_tool
                        in (
                            "describe_object",
                            "get_object_structure",
                            "describe_form",
                            "resolve_object",
                        )
                    )
                    and round_i < self.max_tool_rounds - 1
                ):
                    report_nudge_done = True
                    if verbose:
                        print("[agent] nudge: manager report without build_report", file=sys.stderr)
                    self.messages.append(
                        {
                            "role": "user",
                            "content": (
                                "СТОП. Это отчёт/данные для менеджера. "
                                + (
                                    f"Свойство в РЕКВИЗИТАХ шапки → "
                                    f"query_by_attributes(request={user_text!r}). "
                                    "Не агрегат продаж / compare_periods. "
                                    if need_attr
                                    else (
                                        f"Вызови build_report(request={user_text!r}) "
                                        "(долги/остатки → linked_query). "
                                    )
                                )
                                + "Запрещён сырой execute_query с угаданными именами. "
                                "Ответ: manager_summary.message + таблица rows. "
                                "НЕ вызывай build_report, если вопрос про макеты/реквизиты/формы "
                                "(уже есть describe_object)."
                            ),
                        }
                    )
                    continue

                if (
                    need_status
                    and not status_nudge_done
                    and "get_object_structure" not in tools_used
                    and "describe_object" not in tools_used
                    and round_i < self.max_tool_rounds - 1
                ):
                    status_nudge_done = True
                    if verbose:
                        print("[agent] nudge: status question", file=sys.stderr)
                    self.messages.append(
                        {
                            "role": "user",
                            "content": (
                                "СТОП. Вопрос про статусы. "
                                "resolve/full_name → get_object_structure. "
                                "Ответь по status_fields / enum_values_for_status "
                                "(перечисли все значения). "
                                "Не пиши «статуса нет, только Проведен»."
                            ),
                        }
                    )
                    continue

                if (
                    need_fill
                    and not fill_nudge_done
                    and "explain_fill" not in tools_used
                    and "search_code" not in tools_used
                    and "analyze_object_movements" not in tools_used
                    and round_i < self.max_tool_rounds - 1
                ):
                    fill_nudge_done = True
                    if verbose:
                        print("[agent] nudge: fill question", file=sys.stderr)
                    self.messages.append(
                        {
                            "role": "user",
                            "content": (
                                "СТОП. Вопрос про заполнение / ошибку проведения / поля. "
                                f"Вызови explain_fill(request={user_text!r}) "
                                "и/или search_code по Отказ в ObjectModule. "
                                "Не structure alone. Не «подождите»."
                            ),
                        }
                    )
                    continue

                if (
                    need_epf
                    and not epf_nudge_done
                    and "inspect_external_processing" not in tools_used
                    and round_i < self.max_tool_rounds - 1
                ):
                    epf_nudge_done = True
                    if verbose:
                        print("[agent] nudge: external epf", file=sys.stderr)
                    self.messages.append(
                        {
                            "role": "user",
                            "content": (
                                "СТОП. Это файл внешней обработки/отчёта (.epf/.erf), не CF. "
                                f"Вызови inspect_external_processing(request={user_text!r}) "
                                "с path=абсолютный путь к файлу. Не inspect_code. Не Qdrant."
                            ),
                        }
                    )
                    continue

                # --- принуждение: код-вопрос без code-tools ---
                if (
                    need_code
                    and not need_movements
                    and not need_fill
                    and not code_nudge_done
                    and not (tools_used & CODE_TOOLS)
                    and round_i < self.max_tool_rounds - 1
                ):
                    code_nudge_done = True
                    if verbose:
                        print(
                            "[agent] nudge: code question without code tools",
                            file=sys.stderr,
                        )
                    self.messages.append(
                        {
                            "role": "user",
                            "content": (
                                "СТОП. Вопрос про код/устройство конфигурации. "
                                f"Вызови inspect_code(request={user_text!r}). "
                                "Не отвечай теорией без tool. Не «подождите»."
                            ),
                        }
                    )
                    continue

                # --- запрет имитации прогресса / «можно ли прочитать?» ---
                if (
                    looks_like_fake_progress(content)
                    and not progress_nudge_done
                    and round_i < self.max_tool_rounds - 1
                ):
                    progress_nudge_done = True
                    if verbose:
                        print(
                            "[agent] nudge: fake progress narrative",
                            file=sys.stderr,
                        )
                    # если уже search_code/find_modules — добить read_code
                    code_next = (
                        "search_code → module_outline → read_code(path, start_line=…) "
                        "→ ответ с цитатами кода. "
                        if not (tools_used & {"read_code", "inspect_code"})
                        else "read_code(path=…, start_line=line из matches/outline, max_lines=80) "
                        "и ответь фактами. "
                    )
                    self.messages.append(
                        {
                            "role": "user",
                            "content": (
                                "ЗАПРЕЩЕНО: «подождите / изучаю / мне нужно / могу ли я прочитать / "
                                "продолжить?». Пользователь уже ждёт ответ. "
                                "НЕ спрашивай разрешения — СРАЗУ tool. "
                                f"{code_next}"
                                "Запрещён build_report для вопросов про макеты/код. "
                                "После tool — список полей/код, без «хотите продолжить?»."
                            ),
                        }
                    )
                    continue

                # --- запрет «я исправил / уже оптимизировано / исправленный вариант» ---
                if (
                    looks_like_false_fix_claim(content or "")
                    and not fix_claim_nudge_done
                    and round_i < self.max_tool_rounds - 1
                ):
                    fix_claim_nudge_done = True
                    if verbose:
                        print(
                            "[agent] nudge: false fix claim (read-only)",
                            file=sys.stderr,
                        )
                    self.messages.append(
                        {
                            "role": "user",
                            "content": (
                                "СТОП. Ты READ-ONLY: конфигурацию/BSL НЕ менял и пользователь "
                                "не сообщал, что правил код. "
                                "ЗАПРЕЩЕНО: «я/мы исправили», «уже исправлено», "
                                "«разработчик уже вынес/оптимизировал», «Исправленный вариант» "
                                "как будто правка применена. "
                                "Код из Qdrant = КАК ЕСТЬ в выгрузке CF. "
                                "Переформулируй: 1) факт из read_code (цитата as-is); "
                                "2) если даёшь BSL — заголовок "
                                "«Рекомендация / пример (НЕ применено, CF не менялась)». "
                                "Не выдумывай, что проблема уже решена."
                            ),
                        }
                    )
                    continue

                return self._finish_turn(
                    user_text,
                    content or "(пустой ответ модели)",
                    tools_used,
                    verbose=verbose,
                )

            for tc in tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                args = _parse_arguments(fn.get("arguments"))
                call_id = tc.get("id") or f"call_{round_i}_{name}"
                if name:
                    tools_used.add(name)

                if verbose:
                    print(
                        f"[tool] {name}({json.dumps(args, ensure_ascii=False)})",
                        file=sys.stderr,
                    )

                if args.get("_parse_error"):
                    result: Any = {
                        "error": "Не удалось разобрать JSON аргументов",
                        "raw": args.get("_raw"),
                    }
                else:
                    try:
                        result = self.tools.dispatch(name, args)
                    except Exception as e:
                        result = {"error": str(e)}

                if name:
                    self._remember_report(
                        name, result, user_text=user_text, args=args
                    )

                payload = compact_json(
                    result, self.tool_result_max_chars, tool_name=name or None
                )

                if verbose:
                    preview = payload if len(payload) < 400 else payload[:400] + "…"
                    print(
                        f"[result] {len(payload)} chars | {preview}",
                        file=sys.stderr,
                    )

                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": payload,
                    }
                )

                # После structure на movements-вопросе — сразу подсказать movements tool
                if (
                    need_movements
                    and name
                    in (
                        "get_object_structure",
                        "describe_object",
                    )
                    and "analyze_object_movements" not in tools_used
                    and not movements_nudge_done
                ):
                    movements_nudge_done = True
                    full = ""
                    if isinstance(result, dict):
                        full = (
                            result.get("full_name")
                            or result.get("full_name_1c")
                            or ""
                        )
                    hint = (
                        "Structure получен. Это НЕ список регистров проведения. "
                        "Сейчас ОБЯЗАТЕЛЬНО вызови analyze_object_movements("
                        f"request={user_text!r}"
                    )
                    if full:
                        hint += f", full_name={full!r}"
                    hint += (
                        "). Ответ пользователю — только после этого tool: "
                        "перечисли ВСЕ имена из register_names_full / registers_all "
                        "(не 10–15 штук «из головы»)."
                    )
                    self.messages.append({"role": "user", "content": hint})
                    if verbose:
                        print("[agent] nudge: after structure → movements", file=sys.stderr)

        # Лимит раундов: попросить модель ответить по уже собранным tool results
        if verbose:
            print(
                f"[agent] лимит tool-rounds={self.max_tool_rounds}, финальный ответ без tools",
                file=sys.stderr,
            )
        self.messages.append(
            {
                "role": "user",
                "content": (
                    "Лимит вызовов tools. Больше tools НЕ вызывай. "
                    "Ответь пользователю по уже полученным результатам tools: "
                    "что нашёл, full_name_1c / likely_sources / path, "
                    "что ещё неясно. Коротко по-русски."
                ),
            }
        )
        try:
            # один финальный вызов без tools
            prepared = self._prepare_messages()
            completion = self._create_completion(
                prepared, with_tools=False, verbose=verbose
            )
            choice = _first_choice(completion)
            msg = getattr(choice, "message", None)
            content = (_message_to_dict(msg).get("content") or "").strip()
            if content:
                self.messages.append({"role": "assistant", "content": content})
                return self._finish_turn(
                    user_text, content, tools_used, verbose=verbose
                )
        except Exception as e:
            if verbose:
                print(f"[agent] финальный ответ не удался: {e}", file=sys.stderr)

        fallback = (
            "Собрал данные tools, но не успел сформулировать полный ответ "
            f"(лимит {self.max_tool_rounds} шагов). "
            "Повторите вопрос короче или вызовите inspect_code(request=…). "
            "Можно увеличить llm.max_tool_rounds в config.yaml."
        )
        return self._finish_turn(
            user_text, fallback, tools_used, verbose=verbose
        )


def build_agent_from_config(config: dict[str, Any], tools: UniversalTools) -> Agent:
    llm = config["llm"]
    return Agent(
        tools,
        base_url=llm["base_url"],
        api_key=llm["api_key"],
        model=llm["model"],
        temperature=llm.get("temperature", 0.1),
        max_tokens=llm.get("max_tokens", 3072),
        max_tool_rounds=llm.get("max_tool_rounds", 8),
        context_window=llm.get("context_window", 32000),
        tool_result_max_chars=llm.get("tool_result_max_chars", 8000),
        history_max_messages=llm.get("history_max_messages", 40),
        history_max_turns=llm.get("history_max_turns", 6),
        keep_recent_tool_full=llm.get("keep_recent_tool_full", 2),
        old_tool_chars=llm.get("old_tool_chars", 700),
        diary_max_chars=llm.get("diary_max_chars", 1400),
        diary_trigger_turns=llm.get("diary_trigger_turns", 4),
        diary_pressure=float(llm.get("diary_pressure", 0.72)),
    )


def run_chat_cli(config: dict[str, Any], tools: UniversalTools) -> None:
    agent = build_agent_from_config(config, tools)
    llm = config["llm"]
    print(
        f"1C AI Agent | provider={llm['provider']} model={llm['model']} "
        f"ctx={llm.get('context_window', 32000)} "
        f"turns={llm.get('history_max_turns', 6)} "
        f"tool_cap={llm.get('tool_result_max_chars', 8000)} "
        f"url={llm['base_url']} backend={config['backend']}"
    )
    print(
        "Команды: /help — возможности и примеры, /reset — сброс истории, "
        "/exit — выход\n"
    )

    while True:
        try:
            text = input("Вы> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/exit", "/quit", "exit", "quit"):
            break
        if text == "/reset":
            agent.reset()
            print("История очищена.")
            continue
        if text in ("/help", "/?", "help", "?"):
            try:
                from tools.agent_capabilities import format_capabilities_text

                print(f"\nАссистент> {format_capabilities_text()}\n")
            except Exception as e:
                print(f"\nАссистент> (help) {e}\n")
            continue
        answer = agent.run(text, verbose=True)
        print(f"\nАссистент> {answer}\n")
