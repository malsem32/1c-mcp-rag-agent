"""Какие свойства элементов обычной формы из справки платформы ещё не расшифрованы в ordinary_form_props.json.
python scripts/_props_gap.py [--all]   (--all: и свойства «только чтение»/времени выполнения)
ВНИМАНИЕ: список из справки платформы (API) шире панели свойств конструктора обычной формы — многих «нет» в панели нет
(Доступность у ПоляСписка, ЦветТекстаПоля у Флажка и т. п.). Реальный остаток — копии в эталонах владельца:
scripts/_props_audit.py, статусы НЕТ В КАРТЕ / РАСХОЖДЕНИЕ / БЕЗ РАЗЛИЧИЙ."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELP = ROOT / "knowledge" / "справка_платформы" / "синтаксис_помощник"
MAP = json.loads((ROOT / "tools" / "data" / "ordinary_form_props.json").read_text(encoding="utf-8"))
TYPES = ["Форма", "ПолеВвода", "ПолеВыбора", "Кнопка", "Надпись", "Флажок", "Переключатель", "ПолеСписка", "ТабличноеПоле",
         "ПолеТабличногоДокумента", "ПолеТекстовогоДокумента", "ПолеHTMLДокумента", "ПолеКалендаря", "ПолосаРегулирования",
         "Индикатор", "ПолеКартинки", "Панель", "РамкаГруппы", "Разделитель", "КоманднаяПанель", "Диаграмма",
         "ДиаграммаГанта", "Дендрограмма", "СводнаяДиаграмма", "ПолеГрафическойСхемы", "ПолеГеографическойСхемы"]
ALIAS = {"Индикатор": "ИндикаторПрогресса"}
norm = lambda s: re.sub(r"[^а-яa-z0-9]", "", s.lower().replace("ё", "е"))  # noqa: E731


def props(page: Path) -> list[str]:
    t = page.read_text(encoding="utf-8")
    m = re.search(r"Свойства:(.*?)(?:\nМетоды:|\nСобытия:|\nИспользование|\Z)", t, re.S)
    return [x.group(1) for x in re.finditer(r"^\s*([А-Яа-яЁёA-Za-z0-9_]+) \(", m.group(1), re.M)] if m else []


def design_time(typ: str, p: str) -> bool:
    f = HELP / f"{typ}.{p}.md"
    return f.exists() and "Чтение и запись" in f.read_text(encoding="utf-8")


all_mode = "--all" in sys.argv
tot_h = tot_m = 0
for t in TYPES:
    page = HELP / f"{t}.md"
    if not page.exists():
        print(f"{t}: страницы справки нет"); continue
    have = {norm(k) for k in MAP.get(ALIAS.get(t, t), {}) } | {norm(k) for k in MAP.get("_Элемент", {})}
    ps = props(page)
    rw = [p for p in ps if all_mode or design_time(t, p)]
    miss = [p for p in rw if norm(p) not in have]
    tot_h += len(rw); tot_m += len(miss)
    print(f"{t}: свойств {len(rw)}, в карте {len(rw) - len(miss)}, нет: {', '.join(miss) or '—'}")
print(f"ИТОГО: {tot_h}, не расшифровано {tot_m}")
