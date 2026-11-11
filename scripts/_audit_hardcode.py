# -*- coding: utf-8 -*-
"""Аудит хардкода в коде проекта (tools, vector, ingest, backends, agent, kb) — отчёт в exports/hardcode_audit.txt"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_DIRS = ("tools", "vector", "ingest", "backends", "agent", "kb")
OUT = ROOT / "exports" / "hardcode_audit.txt"

PATS = {
    "full_name": re.compile(
        r"(Документ|Справочник|РегистрНакопления|РегистрСведений|ОбщийМодуль)"
        r"\.[A-Za-zА-Яа-яЁё_]{4,}"
    ),
    "en_path": re.compile(
        r"(Documents|Catalogs|CommonModules)/[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_]{5,}"
    ),
    # word-boundary: не ловить «подсказка» как «сказка»
    "demo": re.compile(
        r"(?<![А-Яа-яЁёA-Za-z])(Ассоль|Сказка|Кафе\s*Сказка)(?![А-Яа-яЁёA-Za-z])",
        re.I,
    ),
    "domain": re.compile(
        r"[\"'](реализац\w*|поступлен\w*|корректир\w*|приобрет\w*|"
        r"перемещ\w*|списан\w*|заявлени\w*|деятельн\w*|партионн\w*|"
        r"себестоим\w*|проведениесервер\w*|вариантыотчетовут\w*|"
        r"общегоназначенияут)[\"']",
        re.I,
    ),
    "ut_suffix": re.compile(r"СерверУТ|УТПереопределяемый|ПовтИспУТ|УТ\b"),
}

SKIP_VAL = re.compile(
    r"\.(X|Y|Z|W)\b|Документ\.X|Справочник\.Y|Документ\.Имя|Справочник\.Имя|"
    r"full_name|ИмяДокумента|ИмяОбъекта|\.XXX"
)


def main() -> None:
    by: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    paths = [f for d in CODE_DIRS for f in (ROOT / d).rglob("*.py")]
    paths += [ROOT / "config.py", ROOT / "context_budget.py", ROOT / "session_memory.py",
              ROOT / "mcp_compat.py", ROOT / "metadata_paths.py", ROOT / "main.py"]
    for p in paths:
        if "__pycache__" in p.parts or not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        for i, line in enumerate(lines, 1):
            for name, pat in PATS.items():
                for m in pat.finditer(line):
                    val = m.group(0)
                    if SKIP_VAL.search(val):
                        continue
                    # шаблоны Documents/{obj}/…
                    if "{" in line and name == "en_path":
                        continue
                    by[rel][name].append((i, line.strip()[:140], val))

    out: list[str] = []
    out.append("# Hardcode audit")
    out.append("")
    out.append("## A) Concrete full_name / EN path / demo (critical if runtime)")
    for f in sorted(by):
        for k in ("full_name", "en_path", "demo", "ut_suffix"):
            items = by[f].get(k) or []
            if not items:
                continue
            out.append(f"\n### {f} [{k}] n={len(items)}")
            for i, s, v in items[:15]:
                out.append(f"  L{i}: {v}")
                out.append(f"       {s}")

    out.append("\n\n## B) Domain stem string literals (ranking / NL)")
    dom = []
    for f in by:
        n = len(by[f].get("domain") or [])
        if n:
            dom.append((n, f))
    for n, f in sorted(dom, reverse=True):
        out.append(f"\n{n:4d}  {f}")
        for i, s, v in (by[f].get("domain") or [])[:8]:
            out.append(f"       L{i}: {v}  |  {s[:100]}")

    out.append("\n\n## C) Classification guide")
    out.append(
        """
OK (not CF hardcode):
  - Platform BSL: ОбработкаПроведения, Запрос.Текст, Движения.
  - Field roles: Контрагент/Номенклатура as *roles* from YAML
  - business_stems SALES_SEARCH — search words for find_metadata ranking
  - Examples in docs/help: «продажи за 2015»
  - period_parse examples with years

RISKY:
  - Domain tuples used to *pick* object names (реализац→Документ.X)
  - *УТ suffix modules: ПроведениеСерверУТ, …УТПереопределяемый
  - Concrete Documents/Имя in runtime code (not f-string template)

BAD:
  - Demo counterparty names (Ассоль)
  - Fixed Документ.РеализацияТоваровУслуг etc.
"""
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(out), encoding="utf-8")
    print(f"Wrote {OUT} ({len(out)} lines)")
    # summary counts
    for k in PATS:
        c = sum(len(by[f].get(k) or []) for f in by)
        print(f"  {k}: {c}")


if __name__ == "__main__":
    main()
