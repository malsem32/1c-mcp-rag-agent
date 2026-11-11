"""
Шаблоны элементов обычной формы для сборщика epf: tools/data/epf_tpl/ctl/<Тип>.txt.

Источник — эталонная обработка владельца (на каждый тип палитры форма с элементом «<Тип>_Эталон»),
поле табличного документа — из выгрузки CF (в эталоне копии вложены друг в друга).
Метки: @@ID@@, @@NAME@@, @@LAYOUT@@ (блок {8,…} строит сборщик), @@EVENTS@@ (список обработчиков),
@@TITLE@@ (заголовок), @@PATTERN@@ (тип значения поля).

    python scripts/epf_ctl_templates.py [эталон.epf] [--cf D:/conf_test]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import epf_inspect as ei  # noqa: E402

OUT = ROOT / "tools" / "data" / "epf_tpl" / "ctl"
GUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

# тип → (GUID, имя элемента в эталоне, путь списка событий от узла или None, есть заголовок)
# пути событий сверены по 4670 формам выгрузки (где лежит {N,{код,e1692cc2…}})
TYPES: dict[str, tuple[str, str, tuple[int, ...] | None, bool]] = {
    "Кнопка": ("6ff79819-710e-4145-97cd-1618da79e3e2", "кнопка_Эталон", (2, 2), True),
    "Надпись": ("0fc7e20d-f241-460c-bdf4-5ad88e5474a5", "Надпись_Эталон", (2, 2), True),
    "ТабличноеПоле": ("ea83fe3a-ac3c-4cce-8045-3dddf35b28b1", "ТабличноеПоле_Эталон", (2, 4), False),
    "ПолеВвода": ("381ed624-9217-4e63-85db-c4c3cb87daae", "ПолеВвода_Эталон", (2, 4), False),
    "Флажок": ("35af3d93-d7c7-4a2e-a8eb-bac87a1a3f26", "Флажок_Эталон", (2, 2), True),
    "Переключатель": ("782e569a-79a7-4a4f-a936-b48d013936ec", "Переключатель_Эталон", (2, 5), True),
    "ПолеСписка": ("19f8b798-314e-4b4e-8121-905b2a7a03f5", "ПолеСписка_Эталон", (2, 2), False),
    "ПолеВыбора": ("64483e7f-3833-48e2-8c75-2c31aac49f6e", "ПолеВыбора_Эталон", (2, 2), False),
    "РамкаГруппы": ("90db814a-c75f-4b54-bc96-df62e554d67d", "РамкаГруппы1", None, True),
    "ПолеКартинки": ("151ef23e-6bb2-4681-83d0-35bc2217230c", "ПолеКартинки_Эталон", (2, 2), False),
    "КоманднаяПанель": ("e69bf21d-97b2-4f37-86db-675aea9ec2cb", "КоманднаяПанель_Эталон", None, False),
    "Панель": ("09ccdc77-ea1a-4a6d-ab1c-3435eada2433", "Панель_Эталон", (2, 2), False),
    "Разделитель": ("36e52348-5d60-4770-8e89-a16ed50a2006", "Разделитель_Эталон", None, False),
    "ПолеТабличногоДокумента": ("236a17b3-7f44-46d9-a907-75f9cdc61ab5", "", (2, 17), False),
    "ПолеКалендаря": ("e3c063d8-ef92-41be-9c89-b70290b5368b", "ПолеКалендаря_Эталон", (2, 2), False),
    "ПолеТекстовогоДокумента": ("14c4a229-bfc3-42fe-9ce1-2da049fd0109", "ПолеТекстовогоДокумента_Эталон", None, False),
    "ПолосаРегулирования": ("6c06cd5d-8481-4b6f-a90a-7a97a8bb8bef", "ПолосаРегулирования_Эталон", (2, 2), False),
    "Индикатор": ("b1db1f86-abbb-4cf0-8852-fe6ae21650c2", "Индикатор_Эталон", None, False),
    "ПолеГеографическойСхемы": ("", "ПолеГеографическойСхемы_Эталон", None, False),
    "ПолеHTMLДокумента": ("d92a805c-98ae-4750-9158-d9ce7cec2f20", "ПолеHTMLДокумента_Эталон", (2, 2), False),
    "ПолеГрафическойСхемы": ("42248403-7748-49da-b782-e4438fd7bff3", "ПолеГрафическойСхемы_Эталон", None, False),
    "Диаграмма": ("a8b97779-1a4b-4059-b09c-807f86d2a461", "Диаграмма_Эталон", None, False),
    "ДиаграммаГанта": ("", "ДиаграммаГанта_Эталон", None, False),
    "СводнаяДиаграмма": ("", "СводнаяДиаграмма1", None, False),
    "Дендрограмма": ("", "Дендрограмма1", None, False),
}


def _depths(t: str) -> list[int]:
    """Глубина скобок перед каждым символом (строки в кавычках не считаются)."""
    dm, d, q = [0] * (len(t) + 1), 0, False
    for i, c in enumerate(t):
        if q:
            q = c != '"'
        elif c == '"':
            q = True
        elif c == "{":
            d += 1
        elif c == "}":
            d -= 1
        dm[i + 1] = d
    return dm


def children(t: str, s: int) -> list[tuple[int, int]]:
    """Дети узла {…}, начинающегося в позиции s: [(начало, конец)) каждого, без пробелов и переводов строк."""
    dm = _depths(t)
    base, out, start, i = dm[s], [], s + 1, s + 1
    while True:
        c = t[i]
        if dm[i] == base + 1 and c in ",}":
            seg = t[start:i]
            lead = len(seg) - len(seg.lstrip())
            out.append((start + lead, start + len(seg.rstrip())))
            if c == "}":
                return out
            start = i + 1
        i += 1


def span_at(t: str, path: tuple[int, ...]) -> tuple[int, int]:
    s, e = 0, len(t)
    for k in path:
        s, e = children(t, s)[k]
    return s, e


def element_nodes(form: str) -> dict[str, str]:
    """Имя элемента → текст его узла {GUID,id,…,{14,"Имя",…},…}."""
    dm = _depths(form)
    starts = [m.start() for m in re.finditer(r"\{(" + GUID + r"),(\d+),\s*\{", form)]
    out = {}
    for m in re.finditer(r'\{14,"([^"]+)",', form):
        dn = dm[m.start()]
        s = next((s for s in reversed(starts) if s < m.start() and dm[s] == dn - 1), None)
        if s is None:
            continue
        e = s
        while dm[e + 1] != dm[s]:
            e += 1
        out.setdefault(m.group(1), form[s : e + 1])
    return out


def _forms(epf: Path) -> list[str]:
    out = []

    def walk(blob: bytes) -> None:
        for name, b in ei._v8_unpack_files(blob):
            raw = ei._inflate(b) or b
            if ei._is_v8_container(raw):
                walk(raw)
            elif name == "form":
                out.append(raw.decode("utf-8-sig", errors="replace").replace("\r", ""))

    walk(epf.read_bytes())
    return out


def _cf_node(cf: Path, guid: str) -> str:
    """Самый короткий узел типа без событий из выгрузки CF (запасной источник)."""
    best = ""
    for fb in cf.rglob("Form.bin"):
        try:
            files = ei._v8_unpack_files(fb.read_bytes())
        except Exception:
            continue
        for name, b in files:
            if name != "form":
                continue
            t = (ei._inflate(b) or b).decode("utf-8-sig", errors="replace").replace("\r", "")
            for node in element_nodes(t).values():
                if node.startswith("{" + guid) and "e1692cc2" not in node and (not best or len(node) < len(best)):
                    best = node
    return best


def make_template(node: str, events: tuple[int, ...] | None, titled: bool) -> str:
    kids = children(node, 0)
    lay = next(i for i, (s, e) in enumerate(kids) if node[s:e].startswith("{8,") and node[kids[i + 1][0]:].startswith("{14,"))
    repl: list[tuple[int, int, str]] = [(kids[1][0], kids[1][1], "@@ID@@"), (kids[lay][0], kids[lay][1], "@@LAYOUT@@")]
    s14, _ = kids[lay + 1]
    q1 = node.index('"', s14)
    repl.append((q1 + 1, node.index('"', q1 + 1), "@@NAME@@"))
    if events:
        s, e = span_at(node, events)
        if node[s:e] != "{0}":
            raise SystemExit(f"на месте списка событий не {{0}}: {node[s:e][:40]}")
        repl.append((s, e, "@@EVENTS@@"))
    props = node[kids[2][0]:kids[2][1]]
    if titled:
        m = re.search(r'\{"ru","([^"]*)"\}', props)
        repl.append((kids[2][0] + m.start(1), kids[2][0] + m.end(1), "@@TITLE@@"))
    m = re.search(r'\{"Pattern",\s*\{"S"\}\s*\}', props)
    if m:
        repl.append((kids[2][0] + m.start(), kids[2][0] + m.end(), "@@PATTERN@@"))
    for s, e, v in sorted(repl, reverse=True):
        node = node[:s] + v + node[e:]
    return node


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("epf", nargs="?", default=str(Path.home() / "Desktop" / "ВнешняяОбработка1.epf"))
    ap.add_argument("--cf", default="D:/conf_test")
    a = ap.parse_args()
    nodes: dict[str, str] = {}
    for f in _forms(Path(a.epf)):
        for k, v in element_nodes(f).items():
            nodes.setdefault(k, v)
    OUT.mkdir(parents=True, exist_ok=True)
    guids = {}
    for typ, (guid, etalon, events, titled) in TYPES.items():
        node = nodes.get(etalon) if etalon else _cf_node(Path(a.cf), guid)
        if not node:
            print(f"{typ}: узел не найден")
            continue
        guids[typ] = node[1:37]
        (OUT / f"{typ}.txt").write_text(make_template(node, events, titled), "utf-8")
        print(f"{typ}: {len(node)}")
    (OUT / "_types.json").write_text(json.dumps(guids, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
