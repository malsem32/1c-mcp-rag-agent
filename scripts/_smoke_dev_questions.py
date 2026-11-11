# -*- coding: utf-8 -*-
"""Выборочный прогон «разработческих» вопросов (код + запросы)."""
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

# Подмножество из списка пользователя + 1 с «запросом для проверки»
QUESTIONS = [
    'Найди в коде место, где выполняется контроль отрицательных остатков при проведении "Реализации товаров и услуг".',
    'Проанализируй модуль "УправлениеЗапасамиПартионныйУчет". Какая процедура отвечает за списание партий по ФИФО?',
    'Найди процедуру, в которой формируются бухгалтерские проводки (Дт/Кт) для документа "Авансовый отчет".',
    'Где в коде происходит распределение общепроизводственных расходов (ОПР) при расчете себестоимости?',
    "Объясни логику работы функции ПолучитьПараметрыДоговора в модуле взаиморасчетов.",
    "Напиши оптимальный запрос для получения актуальных остатков товаров на складах в разрезе характеристик и серий. Выполни если получится.",
    "Сформируй и выполни запрос, который найдет контрагентов с дублирующимися ИНН (или покажи что поля нет).",
    'Сделай запрос к виртуальной таблице оборотов/остатков регистра взаиморасчетов с клиентами (как найдешь в этой базе), долги на текущую дату. Выполни.',
    "Как переписать код с запросом в цикле: Для Каждого Стр Из Товары Цикл Запрос=Новый Запрос; Запрос.Текст=\"ВЫБРАТЬ ... ГДЕ Номенклатура=&Н\"; — предложи вариант без запроса в цикле (на 1С).",
]


def main() -> int:
    cfg = load_config()
    backend = create_backend(cfg)
    print("1C:", getattr(backend, "test_connection", lambda: "?")())
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
    for i, q in enumerate(QUESTIONS, 1):
        print(f"\n{'='*60}\n{i}/{len(QUESTIONS)}\nQ: {q}\n", flush=True)
        t0 = time.time()
        try:
            ans = agent.run(q, verbose=True)
        except Exception as e:
            ans = f"ERROR: {e}"
        dt = time.time() - t0
        print(f"\n--- A ({dt:.0f}s) ---\n{(ans or '')[:1800]}\n", flush=True)
        out.append({"n": i, "q": q, "sec": round(dt, 1), "a": ans or ""})

    path = ROOT / "exports" / "agent_dev_questions.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SAVED", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
