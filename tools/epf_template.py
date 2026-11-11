"""
Содержимое макета табличного документа (MOXCEL) из внешней обработки/отчёта: именованные области,
подписи и параметры. Формат внутренний: после префикса «MOXCEL\\0…» идёт скобочный текст, строки
записаны плоским списком «НомерСтроки, …, ЧислоЯчеек, …» + ячейки (между ячейками — номер колонки).
Ячейка {16,<формат>,{1,1,{"ru","Текст"}},0}: язык «ru» — подпись, пустой язык — имя параметра.
"""

from __future__ import annotations

import re
from typing import Any

from tools.brace import Str, parse

MXL_PREFIX = "MOXCEL\n"
_BRACKET = re.compile(r"\[([^\[\]\r\n]{1,60})\]")


def mxl_text(data: bytes) -> str | None:
    """Бинарный блок «MOXCEL…» → скобочный текст (без бинарного префикса) с маркером в начале."""
    if data[:6] != b"MOXCEL":
        return None
    i = data.find(b"\xef\xbb\xbf", 6, 64)
    if i < 0:
        return None
    return MXL_PREFIX + data[i:].decode("utf-8-sig", errors="ignore")


def _is_cell(x: Any) -> bool:
    """Ячейка: первое число — маска полей (8 — есть имя, 16 — есть текст/параметр), 0 — только формат."""
    if not (isinstance(x, list) and len(x) >= 2 and isinstance(x[0], str) and x[0].isdigit()):
        return False
    mask = int(x[0])
    if mask == 0:
        return len(x) == 2
    return mask in (8, 16, 24) and len(x) >= 3


def _cell_value(x: list[Any]) -> tuple[str, str]:
    """('text'|'param'|'', значение) для ячейки {16,формат,{1,1,{язык,текст}},0}."""
    if int(x[0]) & 16 == 0:
        return "", ""
    for lst in _flat(x):
        if len(lst) == 2 and isinstance(lst[0], Str) and isinstance(lst[1], Str):
            return ("text" if lst[0] else "param"), str(lst[1])
    return "", ""


def _flat(node: list[Any]):
    yield node
    for y in node:
        if isinstance(y, list):
            yield from _flat(y)


def _rows(top: list[Any]) -> dict[int, list[tuple[int, str, str]]]:
    """строка → [(колонка, вид, значение)]; сетка: серия чисел из 4+ перед ячейкой — заголовок строки, из одного — колонка."""
    rows: dict[int, list[tuple[int, str, str]]] = {}
    run: list[int] = []
    row, col, started = -1, 0, False
    for x in top:
        if isinstance(x, list):
            if not _is_cell(x):
                if started:
                    break
                run = []
                continue
            if len(run) >= 4:
                row, col, started = int(run[-4]), int(run[-1]), True
            elif len(run) == 1 and started:
                col = int(run[0])
            elif started or len(run) != 0:
                run = []
                continue
            kind, val = _cell_value(x)
            if started and val:
                rows.setdefault(row, []).append((col, kind, val))
            run = []
        elif isinstance(x, str) and not isinstance(x, Str) and re.fullmatch(r"-?\d+", x):
            run.append(int(x))
        else:
            run = []
    return rows


def _areas(tree: list[Any]) -> list[dict[str, Any]]:
    """{N,"Имя",{1,{вид,колонка1,строка1,колонка2,строка2,uuid},0},…} — именованные области."""
    for lst in _flat(tree):
        if len(lst) < 3 or not (isinstance(lst[0], str) and lst[0].isdigit()):
            continue
        n = int(lst[0])
        if n < 1 or len(lst) != 1 + 2 * n:
            continue
        out: list[dict[str, Any]] = []
        for i in range(n):
            name, rect = lst[1 + 2 * i], lst[2 + 2 * i]
            if not (isinstance(name, Str) and isinstance(rect, list) and rect and rect[0] == "1"
                    and len(rect) > 1 and isinstance(rect[1], list) and len(rect[1]) >= 5):
                break
            _k, c1, r1, c2, r2 = (int(v) if re.fullmatch(r"-?\d+", str(v)) else -1 for v in rect[1][:5])
            r1, r2 = min(r1, r2), max(r1, r2)  # в файле встречается обратный порядок
            c1, c2 = (min(c1, c2), max(c1, c2)) if c1 >= 0 and c2 >= 0 else (-1, -1)
            out.append({"name": str(name), "row_from": r1 + 1, "row_to": r2 + 1,
                        **({"col_from": c1 + 1, "col_to": c2 + 1} if c1 >= 0 else {})})
        else:
            if out:
                return out
    return []


def parse_mxl(text: str, max_texts: int = 40) -> dict[str, Any]:
    """Области (с подписями и параметрами), все параметры и подписи макета."""
    body = text[len(MXL_PREFIX):] if text.startswith(MXL_PREFIX) else text
    try:
        tree = parse(body)
    except Exception:  # noqa: BLE001 — битый макет не должен ронять разбор файла
        return {}
    top = tree[0] if tree and isinstance(tree[0], list) else tree
    rows = _rows(top)
    areas = _areas(tree)

    def collect(rs: list[int]) -> tuple[list[str], list[str]]:
        texts: list[str] = []
        params: list[str] = []
        for r in rs:
            for _c, kind, val in sorted(rows.get(r, [])):
                if kind == "param":
                    if val not in params:
                        params.append(val)
                else:
                    v = " ".join(val.split())
                    if v and v not in texts:
                        texts.append(v)
                    for p in _BRACKET.findall(val):
                        if p not in params:
                            params.append(p)
        return texts, params

    out_areas = []
    for a in areas:
        t, p = collect(list(range(a["row_from"] - 1, a["row_to"])))
        out_areas.append({**a, "texts": t[:max_texts], "parameters": p})
    all_t, all_p = collect(sorted(rows))
    return {
        "rows": (max(rows) + 1) if rows else 0,
        "areas": out_areas,
        "parameters": all_p,
        "texts": all_t[:max_texts],
    }
