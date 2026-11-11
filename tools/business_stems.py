# -*- coding: utf-8 -*-
"""
Универсальные поисковые стемы для find_metadata / compare (любая CF: УТ/УПП/ERP/…).

НЕ full_name объектов — только слова, по которым rank_items_by_phrase
сопоставляет metadata **текущей** базы. Хардкод Документ.X запрещён.
"""

from __future__ import annotations

# Продажи / отгрузка / выручка (документ или регистр — решит ranking)
SALES_SEARCH = "продажа реализация отгрузка выручка"

# Закупки / поступления
PURCHASE_SEARCH = "поступление закупка приобретение"

# Складские остатки (регистры накопления)
STOCK_SEARCH = "товары склады остатки запас"

# Взаиморасчёты / долги
DEBT_SEARCH = "расчеты взаиморасчеты задолженность оплата клиент"

# План продаж (если есть в CF)
PLAN_SEARCH = "план продаж"

# Оплаты / деньги
CASH_SEARCH = "оплата платеж поступление денежных"


def sales_search(extra: str = "") -> str:
    e = (extra or "").strip()
    return f"{SALES_SEARCH} {e}".strip() if e else SALES_SEARCH


def purchase_search(extra: str = "") -> str:
    e = (extra or "").strip()
    return f"{PURCHASE_SEARCH} {e}".strip() if e else PURCHASE_SEARCH


def stock_search(extra: str = "") -> str:
    e = (extra or "").strip()
    return f"{STOCK_SEARCH} {e}".strip() if e else STOCK_SEARCH

