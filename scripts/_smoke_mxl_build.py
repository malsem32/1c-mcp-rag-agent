# -*- coding: utf-8 -*-
"""
Смоук макетов табличного документа в epf: python scripts/_smoke_mxl_build.py [--platform]
Собирает обработку с макетом (tools/mxl_build.py), с --platform выгружает её Конфигуратором в XML и сверяет
с описанием: ячейки, параметры, расшифровка, объединения, области, рамки, шрифты, цвета, ширины, высота строки.
Открытие в клиенте и ПолучитьМакет().ПолучитьОбласть() проверяет владелец (exports/ТестМакета.epf).
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.epf_build import BuildError, _platform_exe, build_epf  # noqa: E402
from _mxl_png import png_b64 as _png  # noqa: E402

SPEC = {"name": "СмоукМакета", "module": "", "form": False, "templates": [{
    "name": "Накладная", "synonym": "Накладная",
    "columns": [30, 200, 60, 80],
    "styles": {"шапка": {"bold": True, "size": 12, "align": "center"},
               "ячейка": {"border": "all", "wrap": True, "valign": "top"},
               "число": {"border": "all", "align": "right", "fill": "#FFFFCC", "numfmt": "ЧЦ=15; ЧДЦ=2; ЧГ=3,0"},
               "целое": {"border": "all", "align": "right", "numfmt": "ЧДЦ=0"}},
    "rows": [
        {"cells": [{"text": "Накладная № [Номер] от [Дата]", "style": "шапка", "colspan": 4}]},
        {"height": 24, "cells": [{"text": "№", "style": "ячейка", "bold": True}, {"text": "Товар", "style": "ячейка", "bold": True},
                                  {"text": "Кол-во", "style": "ячейка", "bold": True}, {"text": "Сумма", "style": "число", "bold": True}]},
        {"cells": [{"param": "НомерСтроки", "style": "ячейка"}, {"param": "Товар", "detail": "ТоварСсылка", "style": "ячейка"},
                   {"param": "Количество", "style": "целое"}, {"param": "Сумма", "style": "число", "color": "#CC0000"}]},
        {"cells": [{"text": "Итого", "style": "ячейка", "colspan": 2, "rowspan": 2}, {"param": "ИтогоКоличество", "style": "число"},
                   {"param": "ИтогоСумма", "style": "число"}]},
        {"cells": [{"col": 2, "text": "ед.", "style": "ячейка", "italic": True, "underline": True, "strike": True},
                   {"text": "руб", "style": "ячейка", "border": "left,top", "line": "dotted", "border_color": "#0000FF",
                    "font": "Times New Roman", "size": 9.5}]},
    ],
    "drawings": [{"base64": _png(), "row": 1, "col": 0, "rows": 2, "cols": 1, "size": "stretch", "offset": [5, 7, 9, 11]},
                 {"base64": _png(), "row": 3, "col": 2, "rows": 1, "cols": 2}],
    "areas": [{"name": "Шапка", "rows": [0, 1]}, {"name": "Строка", "rows": [2, 2]}, {"name": "Подвал", "rows": [3, 4]},
              {"name": "ПервыеКолонки", "cols": [0, 1]}, {"name": "Угол", "rect": [0, 0, 1, 1]}],
}]}

# что должно найтись в XML, выгруженном платформой
EXPECT = [
    ("параметр", r"<parameter>НомерСтроки</parameter>"),
    ("расшифровка", r"<detailParameter>ТоварСсылка</detailParameter>"),
    ("текст", r"<v8:content>Накладная № \[Номер\] от \[Дата\]</v8:content>"),
    ("шаблон", r"<fillType>Template</fillType>"),
    ("объединение по ширине", r"<merge>\s*<r>0</r>\s*<c>0</c>\s*<w>3</w>\s*</merge>"),
    ("объединение 2x2", r"<merge>\s*<r>3</r>\s*<c>0</c>\s*<h>1</h>\s*<w>1</w>\s*</merge>"),
    ("область строк", r"<name>Подвал</name>\s*<area>\s*<type>Rows</type>\s*<beginRow>3</beginRow>\s*<endRow>4</endRow>"),
    ("область колонок", r"<name>ПервыеКолонки</name>\s*<area>\s*<type>Columns</type>"),
    ("область-прямоугольник", r"<type>Rectangle</type>"),
    ("ширина колонки", r"<width>200</width>"),
    ("высота строки", r"<height>24</height>"),
    ("цвет фона", r"<backColor>#FFFFCC</backColor>"),
    ("цвет текста", r"<textColor>#CC0000</textColor>"),
    ("цвет рамки", r"<borderColor>#0000FF</borderColor>"),
    ("пунктир", r">Dotted</v8ui:style>"),
    ("жирный 12", r'<font faceName="Arial" height="12" bold="true"'),
    ("курсив+подчёркнутый+зачёркнутый", r'italic="true" underline="true" strikeout="true"'),
    ("шрифт 9.5", r'faceName="Times New Roman" height="9.5"'),
    ("перенос", r"<textPlacement>Wrap</textPlacement>"),
    ("выравнивание", r"<horizontalAlignment>Right</horizontalAlignment>"),
    ("рисунок: тип", r"<drawingType>Picture</drawingType>"),
    ("рисунок 1: строки и колонки", r"<beginRow>1</beginRow>\s*<beginRowOffset>7</beginRowOffset>\s*<endRow>3</endRow>\s*<endRowOffset>11</endRowOffset>\s*<beginColumn>0</beginColumn>\s*<beginColumnOffset>5</beginColumnOffset>\s*<endColumn>1</endColumn>\s*<endColumnOffset>9</endColumnOffset>"),
    ("рисунок: растяжение", r"<pictureSize>Stretch</pictureSize>"),
    ("рисунок 2: пропорционально", r"<pictureSize>Proportionally</pictureSize>"),
    ("числовой формат 1", r"<v8:content>ЧЦ=15; ЧДЦ=2; ЧГ=3,0</v8:content>"),
    ("числовой формат 2", r"<v8:content>ЧДЦ=0</v8:content>"),
]


def _dump_xml(epf: Path, work: Path) -> str:
    exe = _platform_exe()
    ib, xml, log = work / "ib", work / "xml", work / "log.txt"
    subprocess.run([str(exe), "CREATEINFOBASE", f"File={ib}"], timeout=180, capture_output=True)
    subprocess.run([str(exe), "DESIGNER", "/F", str(ib), "/DisableStartupDialogs", "/DumpExternalDataProcessorOrReportToFiles",
                    str(xml / "root.xml"), str(epf), "/Out", str(log)], timeout=300, capture_output=True)
    files = list(xml.rglob("Template.xml"))
    return files[0].read_text("utf-8-sig") if files else "ОШИБКА: " + log.read_text("utf-8-sig", errors="replace")[-300:]


def main() -> int:
    bad = 0
    data = build_epf(SPEC)
    print(f"сборка: OK ({len(data)} байт)")
    if "--platform" in sys.argv:
        if _platform_exe() is None:
            print("платформа: не найдена, пропуск")
        else:
            work = Path(tempfile.mkdtemp(prefix="mxl_smoke_"))
            try:
                epf = work / "t.epf"
                epf.write_bytes(data)
                xml = _dump_xml(epf, work)
                for name, rx in EXPECT:
                    ok = re.search(rx, xml) is not None
                    bad += not ok
                    print(f"  выгрузка — {name}: {'OK' if ok else 'FAIL'}")
            finally:
                shutil.rmtree(work, ignore_errors=True)
    negatives = {
        "неизвестный стиль": {"columns": [10], "rows": [{"cells": [{"text": "A", "style": "нет"}]}]},
        "text и param": {"rows": [{"cells": [{"text": "A", "param": "B"}]}]},
        "detail без param": {"rows": [{"cells": [{"text": "A", "detail": "B"}]}]},
        "пересечение объединений": {"rows": [{"cells": [{"text": "A", "colspan": 2}, {"col": 1, "text": "B"}]}]},
        "плохая линия": {"rows": [{"cells": [{"text": "A", "border": "all", "line": "волна"}]}]},
        "плохой цвет": {"rows": [{"cells": [{"text": "A", "fill": "красный"}]}]},
        "область без границ": {"rows": [], "areas": [{"name": "А"}]},
        "дубль области": {"rows": [], "areas": [{"name": "А", "rows": [0, 0]}, {"name": "а", "rows": [1, 1]}]},
        "рисунок без картинки": {"rows": [], "drawings": [{"row": 0}]},
        "рисунок: не картинка": {"rows": [], "drawings": [{"base64": "QUJD"}]},
        "рисунок: размер": {"rows": [], "drawings": [{"base64": _png(), "size": "огромный"}]},
        "рисунок: файла нет": {"rows": [], "drawings": [{"file": "Z:/нет/такого.png"}]},
    }
    for name, t in negatives.items():
        try:
            build_epf({"name": "Т", "module": "", "form": False, "templates": [{"name": "М", **t}]})
            print(f"негатив «{name}»: FAIL (ошибки нет)")
            bad += 1
        except BuildError as e:
            print(f"негатив «{name}»: OK ({str(e)[:60]}…)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
