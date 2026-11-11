# -*- coding: utf-8 -*-
"""Права и эксплуатация: роли, RLS, пользователи, журнал регистрации, сеансы."""

from __future__ import annotations

import re
from typing import Any


class AccessMixin:
    def list_roles(self, search: str = "", limit: int = 50) -> dict[str, Any]:
        from tools.roles_access import list_roles as _lr

        return _lr(search=search or "", limit=limit or 50, code_dir=self._code_dir() or None)

    def list_roles_by_right(
        self,
        rights: str = "InteractiveDelete,Update,Edit,InteractiveSetDeletionMark",
        object_type: str = "",
        object_name: str = "",
        limit: int = 80,
    ) -> dict[str, Any]:
        from tools.roles_access import list_roles_by_right as _lrb

        return _lrb(
            rights=rights or "InteractiveDelete,Update,Edit,InteractiveSetDeletionMark",
            object_type=object_type or "",
            object_name=object_name or "",
            limit=limit or 80,
            code_dir=self._code_dir() or None,
        )

    def explain_rls(
        self,
        request: str = "",
        object_name: str = "",
        full_name: str = "",
        limit: int = 40,
    ) -> dict[str, Any]:
        from tools.roles_access import explain_rls as _rls

        return _rls(
            request=request or "",
            object_name=object_name or "",
            full_name=full_name or "",
            limit=limit or 40,
            code_dir=self._code_dir() or None,
        )

    def role_rights(
        self, role: str = "", object_filter: str = "", limit: int = 80
    ) -> dict[str, Any]:
        from tools.roles_access import role_rights as _rr

        return _rr(
            role or "",
            object_filter=object_filter or "",
            limit=limit or 80,
            code_dir=self._code_dir() or None,
        )

    def find_roles_for_object(
        self, object_name: str = "", right: str = "", limit: int = 100
    ) -> dict[str, Any]:
        from tools.roles_access import find_roles_for_object as _fr

        # limit по умолчанию выше: «это все роли?» должно видеть полный скан CF
        return _fr(
            object_name or "",
            right=right or "",
            limit=limit or 100,
            code_dir=self._code_dir() or None,
        )

    def list_users(
        self,
        search: str = "",
        limit: int = 50,
        include_invalid: bool = False,
    ) -> dict[str, Any]:
        from tools.roles_access import list_users as _lu

        return _lu(
            self.backend,
            search=search or "",
            limit=limit or 50,
            include_invalid=bool(include_invalid),
        )

    def access_overview(self, request: str = "") -> dict[str, Any]:
        from tools.roles_access import access_overview as _ao

        raw = (request or "").strip()
        # если в сессии уже есть full_name документа — роли по нему (не hardcode CF)
        pl = raw.lower().replace("ё", "е")
        if re.search(r"(это\s+все|все\s+ли|рол)", pl):
            snap = getattr(self, "_last_report_snapshot", None)
            # last object from roles/structure tools in memory
            try:
                mem = getattr(self, "_session_last_object", None) or ""
            except Exception:
                mem = ""
            if not mem and isinstance(snap, dict):
                mem = str(snap.get("full_name") or "")
            if mem and ("." in mem) and not re.search(
                re.escape(mem.split(".")[-1][:8]), raw, re.I
            ):
                # «это все роли?» без имени → дописать full_name из сессии
                raw = f"{raw} {mem}".strip()
        # resolve разговорного имени → full_name текущей CF (без hardcode)
        if raw and not re.search(
            r"^(Документ|Справочник|Отчет|Отчёт|Document|Catalog|Report)\.",
            raw,
            re.I,
        ):
            try:
                ro = self.resolve_object(raw, "Все", 6)
                top = ""
                if isinstance(ro, dict):
                    top = str(
                        ro.get("full_name")
                        or ro.get("top")
                        or ro.get("suggested")
                        or ""
                    )
                    if not top and ro.get("options"):
                        o0 = ro["options"][0]
                        top = (
                            str(o0.get("full_name") or "")
                            if isinstance(o0, dict)
                            else str(o0)
                        )
                if top and top.startswith(
                    (
                        "Документ.",
                        "Справочник.",
                        "Отчет.",
                        "Отчёт.",
                        "Document.",
                        "Catalog.",
                        "Report.",
                    )
                ):
                    # find_roles по resolved full_name
                    fr = self.find_roles_for_object(top, limit=120)
                    if fr.get("roles_count"):
                        fr["routed"] = "find_roles_for_object"
                        fr["resolved_full_name"] = top
                        fr["request"] = request
                        return fr
            except Exception:
                pass

        return _ao(
            raw or request or "",
            backend=self.backend,
            code_dir=self._code_dir() or None,
        )

    def query_event_log(
        self,
        date_from: str = "",
        date_to: str = "",
        level: str = "",
        user: str = "",
        event: str = "",
        search: str = "",
        comment: str = "",
        metadata: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        from tools.event_log import query_event_log as _qel

        return _qel(
            self.backend,
            date_from=date_from or "",
            date_to=date_to or "",
            level=level or "",
            user=user or "",
            event=event or "",
            search=search or "",
            comment=comment or "",
            metadata=metadata or "",
            limit=limit or 50,
        )

    def analyze_event_log(
        self,
        request: str = "",
        date_from: str = "",
        date_to: str = "",
        level: str = "",
        user: str = "",
        search: str = "",
        limit: int = 100,
    ) -> dict[str, Any]:
        from tools.event_log import analyze_event_log as _ael

        return _ael(
            self.backend,
            request=request or "",
            date_from=date_from or "",
            date_to=date_to or "",
            level=level or "",
            user=user or "",
            search=search or "",
            limit=limit or 100,
        )

    def list_active_sessions(
        self,
        user: str = "",
        application: str = "",
        unique_users: bool = False,
    ) -> dict[str, Any]:
        from tools.sessions import list_active_sessions as _las

        return _las(
            self.backend,
            user=user or "",
            application=application or "",
            unique_users=bool(unique_users),
        )

    def active_users_overview(self, request: str = "") -> dict[str, Any]:
        from tools.sessions import active_users_overview as _auo

        return _auo(self.backend, request=request or "")
