# -*- coding: utf-8 -*-
"""Справка: возможности агента, синтаксис BSL/запросов, примеры, проверка связи."""

from __future__ import annotations

from typing import Any

from tools.onec_query_syntax import get_section, topics_index
from tools.query_examples import get_examples
from tools.responses import err
from tools.uni.catalog import _manager_help


class HelpMixin:
    def manager_help(self) -> dict[str, Any]:
        from tools.route_intent import route_prompt_block
        from tools.agent_capabilities import get_capabilities_payload

        base = dict(_manager_help())
        base["routing"] = route_prompt_block()
        caps = get_capabilities_payload(short=False)
        base["capabilities_answer"] = caps.get("answer")
        base["capabilities_sections"] = caps.get("sections")
        base["cli_commands"] = caps.get("cli_commands")
        base["prefer"] = (
            "Если пользователь спросил «что умеешь / команды» — "
            "выведи capabilities_answer целиком (или agent_capabilities). "
            "Иначе routing: data→build_report/analyst_report, code→inspect_code, "
            "help→code_help, meta→resolve_object."
        )
        return base

    def agent_capabilities(self, short: bool = False) -> dict[str, Any]:
        """Что умеет агент / какие команды — для «что можешь?», «help»."""
        from tools.agent_capabilities import get_capabilities_payload

        return get_capabilities_payload(short=bool(short))

    def code_help(self, topic: str = "", query: str = "") -> dict[str, Any]:
        """Справка по BSL / запросам / регистрам / HTTP — без привязки к конфигурации."""
        from tools.onec_code_help import get_code_help

        return get_code_help(topic=topic or "", query=query or "")

    def query_syntax_help(self, topic: str = "") -> dict[str, Any]:
        """
        Синтаксис языка запросов 1С.
        topic пустой → список тем; functions/aggregate/… → каталог функций конструктора.
        """
        t = (topic or "").strip()
        if not t or t in ("list", "темы", "topics", "index", "help"):
            return topics_index()
        tl = t.lower().replace("ё", "е")
        if tl in (
            "functions",
            "функции",
            "function",
            "aggregate",
            "string",
            "date",
            "date_fn",
            "number",
            "misc",
            "logic",
            "operators",
            "constructors",
            "агрегат",
            "строки",
            "даты_функции",
        ) or tl.startswith("func"):
            from tools.onec_query_syntax import get_functions_catalog

            group = ""
            if tl not in ("functions", "функции", "function") and not tl.startswith(
                "func"
            ):
                group = tl
            cat = get_functions_catalog(group)
            cat["topic"] = "functions"
            cat["title"] = "Функции языка запросов 1С (конструктор)"
            cat["prefer"] = (
                "В ВЫБРАТЬ используй функции из all_names/groups. "
                "Имена полей — только structure. Не ANSI SQL."
            )
            return cat
        if tl in (
            "antipatterns",
            "антипаттерны",
            "антипаттерн",
            "аудит",
            "audit",
            "best_practice",
            "как_лучше",
            "best practices",
        ):
            from tools.query_antipatterns import list_antipatterns_catalog

            cat = list_antipatterns_catalog()
            cat["topic"] = "antipatterns"
            cat["title"] = "Антипаттерны запросов 1С (аудит)"
            return cat
        return get_section(t)

    def query_examples(
        self,
        scenario: str = "",
        document_name: str = "",
        catalog_name: str = "",
        roles: dict[str, str] | None = None,
        object_name: str = "",
    ) -> dict[str, Any]:
        """
        Универсальные шаблоны ВЫБРАТЬ для любого full_name ЭТОЙ базы.
        object_name / document_name / catalog_name — из find_metadata.
        """
        raw_obj = (object_name or document_name or "").strip()
        cat = (catalog_name or "").strip()
        doc = raw_obj
        if raw_obj.startswith("Справочник.") and not cat:
            cat, doc = raw_obj, ""
        elif raw_obj.startswith("Регистр"):
            doc = raw_obj  # templates use {document} as generic object table
        resolved_roles = dict(roles or {})
        target = doc or cat
        if target and not resolved_roles:
            try:
                # short name → full
                res = self.resolve_full_name(target)
                if not res.get("error"):
                    target = str(res["full_name"])
                    if target.startswith("Справочник."):
                        cat, doc = target, ""
                    else:
                        doc = target
                rb = self.resolve_business_fields(target)
                raw = rb.get("roles") if isinstance(rb, dict) else None
                if isinstance(raw, dict):
                    if raw.get("number"):
                        resolved_roles["number"] = str(raw["number"])
                    if raw.get("date"):
                        resolved_roles["date"] = str(raw["date"])
                    if raw.get("counterparty"):
                        resolved_roles["counterparty"] = str(raw["counterparty"])
                    if raw.get("amount"):
                        resolved_roles["amount"] = str(raw["amount"])
                    if raw.get("posted"):
                        resolved_roles["posted"] = str(raw["posted"])
                    if raw.get("code"):
                        resolved_roles["code"] = str(raw["code"])
                    desc = raw.get("description") or raw.get("name")
                    if desc:
                        resolved_roles["name"] = str(desc)
                    if raw.get("deletion_mark"):
                        resolved_roles["deletion"] = str(raw["deletion_mark"])
            except Exception:
                pass
        out = get_examples(
            scenario or "",
            document_name=doc,
            catalog_name=cat,
            roles=resolved_roles or None,
        )
        if resolved_roles:
            out["roles_from_structure"] = resolved_roles
            out["note_universal"] = (
                "Поля из resolve_business_fields ЭТОЙ базы; full_name из metadata."
            )
        out["object_used"] = doc or cat
        return out

    def test_connection(self) -> dict[str, Any]:
        raw = self.backend.test_connection()
        if not isinstance(raw, dict):
            return err(str(raw))
        if raw.get("status") == "error" or raw.get("error"):
            return err(str(raw.get("error") or raw.get("message") or "connection failed"))
        return {
            "ok": True,
            "config": raw.get("config_name") or raw.get("config_synonym") or "",
            "version": raw.get("config_version") or "",
        }
