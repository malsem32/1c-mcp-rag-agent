# -*- coding: utf-8 -*-
"""Скелеты узлов настроек СКД из выгрузки CF (эталоны формата): _dcs_skeleton.py <Template.xml> <xsi:type|тег> [глубина]"""
import re
import sys
import xml.etree.ElementTree as ET

XSI = "{http://www.w3.org/2001/XMLSchema-instance}type"


def short(tag: str) -> str:
    return tag.split("}")[-1]


def dump(el, depth, maxd, ind=0, out=None):
    out = out if out is not None else []
    typ = el.get(XSI, "")
    txt = (el.text or "").strip()
    out.append("  " * ind + short(el.tag) + (f" [{typ}]" if typ else "") + (f" = {txt[:60]}" if txt else ""))
    if depth < maxd:
        for ch in el:
            dump(ch, depth + 1, maxd, ind + 1, out)
    return out


def main() -> None:
    path, what = sys.argv[1], sys.argv[2]
    maxd = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    root = ET.parse(path).getroot()
    for el in root.iter():
        if el.get(XSI, "").endswith(what) or short(el.tag) == what:
            print("\n".join(dump(el, 0, maxd)))
            return
    print("не найдено")


if __name__ == "__main__":
    main()
