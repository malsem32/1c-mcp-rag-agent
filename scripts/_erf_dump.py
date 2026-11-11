# -*- coding: utf-8 -*-
"""Выгрузка .erf платформой в XML и чтение макета СКД: платформа сама разбирает схему и настройки, потому по выгрузке видно,
что из собранного она приняла (потерянные/неверные элементы пропадают). python scripts/_erf_dump.py <файл.erf> [подстрока…]"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.epf_build import _platform_exe


def dump_template(erf: Path, name: str = "ОсновнаяСхемаКомпоновкиДанных") -> str:
    exe = _platform_exe()
    with tempfile.TemporaryDirectory(prefix="erf_dump_") as tmp:
        ib, out = Path(tmp) / "ib", Path(tmp) / "x"
        subprocess.run([str(exe), "CREATEINFOBASE", f"File={ib}"], timeout=180, capture_output=True)
        subprocess.run([str(exe), "DESIGNER", "/F", str(ib), "/DisableStartupDialogs", "/DumpExternalDataProcessorOrReportToFiles",
                        str(out / "root.xml"), str(erf), "/Out", str(Path(tmp) / "log.txt")], timeout=300, capture_output=True)
        p = out / "root" / "Templates" / name / "Ext" / "Template.xml"
        return p.read_text("utf-8-sig") if p.is_file() else ""


if __name__ == "__main__":
    t = dump_template(Path(sys.argv[1]))
    if len(sys.argv) > 2:
        for s in sys.argv[2:]:
            print(f"{s}: {t.count(s)}")
    else:
        print(t)
