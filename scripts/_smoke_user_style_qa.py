# -*- coding: utf-8 -*-
"""
Вопросы «как пользователь»: опечатки, бытовые имена, без CamelCase.
Проверка: понимание/поиск кода + сложные ВЫБРАТЬ (ВТ, соединения, вложенные).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.loop import build_agent_from_config
from backends import create_backend
from config import load_config
from tools.universal import UniversalTools


QUESTIONS = [
    # --- понимание / поиск кода (бытовой язык + опечатки) ---
    (
        "CODE",
        "где в коде при проведении реализацыи товаров и услгу вызывается запасы сервер "
        "отразить товары на складах? покажи кусок",
    ),
    (
        "CODE",
        "объясни простыми словами что делает обработка проведения у реализаци товаров — "
        "какие шаги по порядку",
    ),
    (
        "CODE",
        "почему может не заполнится склад в реализацыи если делают на основании заказа клиента",
    ),
    (
        "CODE",
        "в счете покупателя (счет на оплату клиенту) есть ли обработка проведения и движения по регистрам?",
    ),
    # --- сложные запросы 1С (написать/проверить) ---
    (
        "SQL",
        "напиши и выполни запрос 1с: по реализациям товаров и услуг за 2015 год "
        "сумма по контрагентам, только проведенные, топ 10. "
        "имена объектов ищи сам, я точное имя не помню",
    ),
    (
        "SQL",
        "сложный запрос: остатки взаиморасчетов с клиентами (виртуальная таблица остатков) "
        "на конец 2015, соединить с контрагентами, показать у кого долг больше 0, "
        "первые 15. если ошибка — поправь и повтори",
    ),
    (
        "SQL",
        "нужен запрос с вложенным: сначала выбрать реализации за 2015 (проведенные), "
        "потом сгруппировать по контрагенту и посчитать количество и сумму. "
        "через временную таблицу или вложенный выберать. выполни",
    ),
    (
        "SQL",
        "объединение: счета на оплату клиенту за 2015 и реализации товаров за 2015 "
        "в одном списке (номер, дата, тип документа, сумма, контрагент). "
        "только проведенные. напиши ВЫБРАТЬ с ОБЪЕДИНИТЬ и выполни",
    ),
    (
        "SQL",
        "проверь этот запрос и исправь если надо, потом выполни: "
        "SELECT TOP 5 Partner, SUM(Summa) FROM Document.Реализация WHERE Year=2015 GROUP BY Partner",
    ),
    (
        "SQL",
        "по счету покупателя / счету на оплату: сколько штук за 2015 и общая сумма, "
        "проведенные. напиши правильный выберать на языке 1с и выполни",
    ),
]


def main() -> int:
    cfg = load_config()
    backend = create_backend(cfg)
    print("1C:", backend.test_connection() if hasattr(backend, "test_connection") else "?")
    tools = UniversalTools(
        backend,
        read_only=True,
        default_limit=cfg["agent"]["query_default_limit"],
        max_limit=cfg["agent"]["query_max_limit"],
        list_objects_cap=cfg["agent"].get("list_objects_cap", 80),
        tool_result_max_chars=cfg["llm"].get("tool_result_max_chars", 6000),
        compact_results=True,
        code_dir=cfg.get("code_dir") or "",
    )
    agent = build_agent_from_config(cfg, tools)

    out = []
    for i, (kind, q) in enumerate(QUESTIONS, 1):
        print(f"\n{'='*60}\n{i}/{len(QUESTIONS)} [{kind}]\nQ: {q}\n", flush=True)
        t0 = time.time()
        try:
            ans = agent.run(q, verbose=True)
        except Exception as e:
            ans = f"ERROR: {e}"
        dt = time.time() - t0
        print(f"\n--- A ({dt:.0f}s) ---\n{(ans or '')[:2000]}\n", flush=True)
        out.append(
            {
                "n": i,
                "kind": kind,
                "q": q,
                "sec": round(dt, 1),
                "a": ans or "",
            }
        )

    path = ROOT / "exports" / "agent_user_style_qa.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SAVED", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
