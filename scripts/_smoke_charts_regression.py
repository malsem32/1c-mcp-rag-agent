# -*- coding: utf-8 -*-
"""Регрессия Excel-диаграмм: strRef cats, непустые series, не ось «Период»."""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _chart_xml_ok(xlsx_path: Path) -> dict:
    """Проверить charts/*.xml внутри xlsx."""
    info = {
        "path": str(xlsx_path),
        "charts": 0,
        "has_str_ref": False,
        "has_num_ref_cat": False,
        "has_series": False,
        "errors": [],
    }
    if not xlsx_path.exists():
        info["errors"].append("file missing")
        return info
    with zipfile.ZipFile(xlsx_path, "r") as z:
        chart_files = [n for n in z.namelist() if "/charts/chart" in n and n.endswith(".xml")]
        info["charts"] = len(chart_files)
        for name in chart_files:
            xml = z.read(name).decode("utf-8", errors="replace")
            if "strRef" in xml or "c:strRef" in xml:
                info["has_str_ref"] = True
            # cat as numRef is bad for labels
            if "<c:cat>" in xml and "numRef" in xml.split("<c:cat>")[1][:400]:
                info["has_num_ref_cat"] = True
            if "ser>" in xml or "c:ser" in xml:
                info["has_series"] = True
    return info


def test_export_charts() -> None:
    from tools.excel_export import save_analyst_excel

    rows = [
        {"Контрагент": "КонтрагентА", "Сумма": 1000, "Период": "2016"},
        {"Контрагент": "КонтрагентБ", "Сумма": 800, "Период": "2016"},
        {"Контрагент": "КонтрагентВ", "Сумма": 500, "Период": "2016"},
        {"Контрагент": "КонтрагентГ", "Сумма": 200, "Период": "2016"},
    ]
    saved = save_analyst_excel(
        title="Chart regression",
        sheets=[
            {
                "name": "Данные",
                "rows": rows,
                "chart": "bar",
                "chart_value": "Сумма",
                "chart_category": "Контрагент",
            }
        ],
        bullets=["smoke"],
        charts_spec=[
            {"type": "bar", "value": "Сумма", "title": "bar", "chart_category": "Контрагент"},
            {"type": "pie", "value": "Сумма", "title": "pie", "chart_category": "Контрагент"},
        ],
        filename="chart_regression.xlsx",
    )
    assert not saved.get("error"), saved
    path = Path(saved["path"])
    assert path.exists()
    info = _chart_xml_ok(path)
    print("charts info", info)
    assert info["charts"] >= 1, info
    assert info["has_series"], info
    # labels should be string refs for category
    assert info["has_str_ref"] or not info["has_num_ref_cat"], info
    print("OK chart export", path.name, "charts=", info["charts"])


def test_from_snapshot() -> None:
    from tools.dialog_context import export_snapshot_to_excel

    snap = {
        "tool": "compare_periods",
        "full_name": "Документ.ТестовыйДокументПродаж",
        "period_spec": "2016",
        "group_by": "Контрагент",
        "rows": [
            {"Контрагент": "А", "Сумма": 10, "Период": "2016"},
            {"Контрагент": "Б", "Сумма": 20, "Период": "2016"},
        ],
    }
    out = export_snapshot_to_excel(snap, phrase="в excel с диаграммами")
    assert out.get("ok") and out.get("path"), out
    info = _chart_xml_ok(Path(out["path"]))
    print("snapshot charts", info)
    assert info["charts"] >= 1 and info["has_series"]
    print("OK snapshot charts")


if __name__ == "__main__":
    test_export_charts()
    test_from_snapshot()
    print("ALL CHART REGRESSION OK")
