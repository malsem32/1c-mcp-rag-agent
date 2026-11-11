# -*- coding: utf-8 -*-
"""Обёртка: полная пересборка индекса — scripts/rebuild_help_index.py"""
from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("rebuild_help_index.py")), run_name="__main__")
