# -*- coding: utf-8 -*-
"""Машиночитаемый прогресс для панели: строка «@@PROGRESS <сделано> <всего> <подпись>» в stderr.

Включается переменной окружения RAG_PROGRESS=1 (её ставит панель); в обычном CLI молчит.
"""
from __future__ import annotations

import os
import sys
import time

_last = 0.0


def report(done: int, total: int, label: str = "", *, force: bool = False) -> None:
    global _last
    if os.environ.get("RAG_PROGRESS") != "1":
        return
    now = time.monotonic()
    if not force and now - _last < 0.5:
        return
    _last = now
    print(f"@@PROGRESS {int(done)} {int(total)} {label}".rstrip(), file=sys.stderr, flush=True)
