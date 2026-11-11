# -*- coding: utf-8 -*-
"""Сканер СКД-макетов выгрузки CF: образцы узлов для эталонов формата.
python scripts/_dcs_scan.py <каталог Reports> <что> [сколько]
что: groups | inlist | designtime | objset | union | chart | params"""
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

XSI = "{http://www.w3.org/2001/XMLSchema-instance}type"


def sh(t: str) -> str:
    return t.split("}")[-1]


def dump(el, maxd, ind=0, out=None, d=0):
    out = [] if out is None else out
    typ = el.get(XSI, "")
    txt = (el.text or "").strip()
    out.append("  " * ind + sh(el.tag) + (f" [{typ}]" if typ else "") + (f" = {txt[:70]}" if txt else ""))
    if d < maxd:
        for ch in el:
            dump(ch, maxd, ind + 1, out, d + 1)
    return out


def main() -> None:
    root_dir, what = Path(sys.argv[1]), sys.argv[2]
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    shown = 0
    names: dict[str, int] = {}
    for f in sorted(root_dir.glob("*/Templates/*/Ext/Template.xml")):
        try:
            tree = ET.parse(f)
        except ET.ParseError:
            continue
        for el in tree.getroot().iter():
            typ = el.get(XSI, "")
            hit = False
            if what == "groups":
                hit = typ.endswith("FilterItemGroup")
            elif what == "inlist":
                hit = sh(el.tag) == "comparisonType" and (el.text or "").strip() in ("InList", "NotInList", "InHierarchy", "InListByHierarchy")
            elif what == "designtime":
                hit = typ.endswith("DesignTimeValue")
            elif what == "objset":
                hit = typ.endswith("DataSetObject")
            elif what == "union":
                hit = typ.endswith("DataSetUnion")
            elif what == "chart":
                hit = typ.endswith("StructureItemChart")
            elif what == "params":
                if sh(el.tag) == "parameter" and el.text:
                    names[el.text.strip()] = names.get(el.text.strip(), 0) + 1
            if hit and shown < limit:
                parent = None
                if what == "inlist":
                    for p in tree.getroot().iter():
                        if el in list(p):
                            parent = p
                            break
                node = parent if parent is not None else el
                print(f"--- {f.parent.parent.parent.parent.name}")
                print("\n".join(dump(node, 6)[:60]))
                shown += 1
        if shown >= limit and what != "params":
            break
    if what == "params":
        for k, v in sorted(names.items(), key=lambda x: -x[1])[:80]:
            print(v, k)


if __name__ == "__main__":
    main()
