# -*- coding: utf-8 -*-
"""Разбор выгрузки CF: подписки, регламентные, формы, ПФ, HTTP, общие реквизиты, обмен, документ по номеру."""

from __future__ import annotations

from typing import Any


class DumpMixin:
    def explain_subscriptions(
        self,
        request: str = "",
        object_name: str = "",
        full_name: str = "",
        event: str = "",
        limit: int = 40,
    ) -> dict[str, Any]:
        from tools.event_subscriptions import (
            explain_subscriptions as _es,
        )

        return _es(
            request=request or "",
            object_name=object_name or "",
            full_name=full_name or "",
            event=event or "",
            limit=limit or 40,
            code_dir=self._code_dir() or None,
        )

    def describe_scheduled_job(
        self,
        request: str = "",
        name: str = "",
        limit: int = 30,
    ) -> dict[str, Any]:
        from tools.scheduled_jobs import describe_scheduled_job as _sj

        return _sj(
            request=request or "",
            name=name or "",
            limit=limit or 30,
            code_dir=self._code_dir() or None,
        )

    def form_event_map(
        self,
        request: str = "",
        full_name: str = "",
        form_name: str = "",
        limit: int = 80,
    ) -> dict[str, Any]:
        from tools.form_map import form_event_map as _fe

        return _fe(
            request=request or "",
            full_name=full_name or "",
            form_name=form_name or "",
            limit=limit or 80,
            code_dir=self._code_dir() or None,
        )

    def print_form_bind(
        self,
        request: str = "",
        full_name: str = "",
        template_name: str = "",
        limit: int = 80,
    ) -> dict[str, Any]:
        from tools.form_map import print_form_bind as _pf

        return _pf(
            request=request or "",
            full_name=full_name or "",
            template_name=template_name or "",
            limit=limit or 80,
            code_dir=self._code_dir() or None,
        )

    def http_service_map(
        self,
        request: str = "",
        name: str = "",
    ) -> dict[str, Any]:
        from tools.dump_meta_extra import http_service_map as _hs

        return _hs(
            request=request or "",
            name=name or "",
            code_dir=self._code_dir() or None,
        )

    def common_attr_and_types(
        self,
        request: str = "",
        name: str = "",
        object_name: str = "",
        kind: str = "",
        limit: int = 40,
    ) -> dict[str, Any]:
        from tools.dump_meta_extra import common_attr_and_types as _ca

        return _ca(
            request=request or "",
            name=name or "",
            object_name=object_name or "",
            kind=kind or "",
            limit=limit or 40,
            code_dir=self._code_dir() or None,
        )

    def exchange_composition(
        self,
        request: str = "",
        name: str = "",
        limit: int = 80,
    ) -> dict[str, Any]:
        from tools.dump_meta_extra import exchange_composition as _ex

        return _ex(
            request=request or "",
            name=name or "",
            limit=limit or 80,
            code_dir=self._code_dir() or None,
        )

    def explain_command(
        self,
        request: str = "",
        full_name: str = "",
        form_name: str = "",
        command: str = "",
    ) -> dict[str, Any]:
        from tools.form_command import explain_command as _ec

        return _ec(
            request=request or "",
            full_name=full_name or "",
            form_name=form_name or "",
            command=command or "",
            code_dir=self._code_dir() or None,
        )

    def document_by_number(
        self,
        request: str = "",
        full_name: str = "",
        number: str = "",
        limit: int = 5,
    ) -> dict[str, Any]:
        from tools.document_lookup import document_by_number as _dn

        return _dn(
            self.backend,
            request=request or "",
            full_name=full_name or "",
            number=number or "",
            limit=limit or 5,
        )
