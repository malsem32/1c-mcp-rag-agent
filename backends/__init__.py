"""Бэкенды доступа к 1С: HTTP-сервис или COM."""

from __future__ import annotations

from typing import Any

from backends.base import OneCBackend
from backends.com_backend import ComBackend
from backends.http_backend import HttpBackend
from backends.offline_backend import OfflineBackend


def create_backend(config: dict[str, Any]) -> OneCBackend:
    name = (config.get("backend") or "http").lower()
    if name == "http":
        return HttpBackend(config["http"])
    if name == "com":
        return ComBackend(config["com"])
    if name in ("none", "offline", "off"):
        return OfflineBackend()
    raise ValueError(f"Неизвестный backend: {name}. Допустимо: http, com, none")


__all__ = ["OneCBackend", "HttpBackend", "ComBackend", "OfflineBackend", "create_backend"]
