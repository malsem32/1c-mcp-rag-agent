"""
Стенд MOXCEL (оракул — платформа 8.3.24): XML макета -> epf платформой -> бинарный MOXCEL.
    mxl_lab.py <Template.xml> [<имя папки опыта>]
Печатает текст MOXCEL (скобочный формат) и кладёт его в <опыт>/moxcel.txt
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import epf_inspect as ei
from tools.epf_build import _platform_exe, write_epf

SP = Path(tempfile.gettempdir()) / "mxl_lab"
HEAD = (r'<?xml version="1.0" encoding="UTF-8"?>' "\n"
        '<document xmlns="http://v8.1c.ru/8.2/data/spreadsheet" xmlns:style="http://v8.1c.ru/8.1/data/ui/style" '
        'xmlns:v8="http://v8.1c.ru/8.1/data/core" xmlns:v8ui="http://v8.1c.ru/8.1/data/ui" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n')
LANG = ("\t<languageSettings>\n\t\t<currentLanguage>ru</currentLanguage>\n\t\t<defaultLanguage>ru</defaultLanguage>\n"
        "\t\t<languageInfo>\n\t\t\t<id>ru</id>\n\t\t\t<code>Русский</code>\n\t\t\t<description>Русский</description>\n"
        "\t\t</languageInfo>\n\t</languageSettings>\n")
META = ('<?xml version="1.0" encoding="UTF-8"?>\n<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" '
        'xmlns:v8="http://v8.1c.ru/8.1/data/core" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" version="2.17">\n'
        '\t<Template uuid="{uid}">\n\t\t<Properties>\n\t\t\t<Name>{name}</Name>\n\t\t\t<Synonym>\n\t\t\t\t<v8:item>\n'
        '\t\t\t\t\t<v8:lang>ru</v8:lang>\n\t\t\t\t\t<v8:content>{name}</v8:content>\n\t\t\t\t</v8:item>\n\t\t\t</Synonym>\n'
        '\t\t\t<Comment/>\n\t\t\t<TemplateType>SpreadsheetDocument</TemplateType>\n\t\t</Properties>\n\t</Template>\n</MetaDataObject>')


def run(body_xml: str, work: Path, name: str = "Макет") -> str:
    """body_xml — содержимое <document> без languageSettings (columns, rowsItem, …)."""
    work.mkdir(parents=True, exist_ok=True)
    exe = _platform_exe()
    base = work / "base.epf"
    base.write_bytes(__import__("tools.epf_build", fromlist=["build_epf"]).build_epf({"name": "Опыт", "module": "", "form": False}))
    xml, ib, log = work / "xml", work / "ib", work / "log.txt"
    if xml.exists():
        shutil.rmtree(xml)
    if not ib.exists():
        subprocess.run([str(exe), "CREATEINFOBASE", f"File={ib}"], timeout=180, capture_output=True)
    subprocess.run([str(exe), "DESIGNER", "/F", str(ib), "/DisableStartupDialogs", "/DumpExternalDataProcessorOrReportToFiles",
                    str(xml / "root.xml"), str(base), "/Out", str(log)], timeout=300, capture_output=True)
    root = xml / "root.xml"
    t = root.read_text("utf-8-sig")
    m = re.search(r"(\s*)</ChildObjects>", t)
    if m:
        t = t.replace(m.group(0), f"{m.group(1)}\t<Template>{name}</Template>{m.group(0)}", 1)
    else:  # пустой <ChildObjects/>
        t = t.replace("<ChildObjects/>", f"<ChildObjects>\n\t\t\t<Template>{name}</Template>\n\t\t</ChildObjects>", 1)
    root.write_text(t, "utf-8-sig")
    obj_dir = xml / "root"
    obj_dir.mkdir(exist_ok=True)
    (obj_dir / "Templates" / name / "Ext").mkdir(parents=True)
    (obj_dir / "Templates" / f"{name}.xml").write_text(META.format(uid="11111111-2222-3333-4444-555555555555", name=name), "utf-8-sig")
    (obj_dir / "Templates" / name / "Ext" / "Template.xml").write_text(HEAD + LANG + body_xml + "</document>", "utf-8-sig")
    out = work / "out.epf"
    out.unlink(missing_ok=True)
    subprocess.run([str(exe), "DESIGNER", "/F", str(ib), "/DisableStartupDialogs", "/LoadExternalDataProcessorOrReportFromFiles",
                    str(root), str(out), "/Out", str(log)], timeout=300, capture_output=True)
    if not out.is_file():
        return "ОШИБКА: " + log.read_text("utf-8-sig", errors="replace")[-600:]
    mox = ""

    def walk(blob: bytes) -> None:
        nonlocal mox
        for n, b in ei._v8_unpack_files(blob):
            raw = ei._inflate(b) or b
            if raw[:6] == b"MOXCEL":
                mox = raw[11:].decode("utf-8-sig", errors="replace").replace("\r", "")
            elif ei._is_v8_container(raw):
                walk(raw)

    walk(out.read_bytes())
    (work / "moxcel.txt").write_text(mox, "utf-8")
    return mox


if __name__ == "__main__":
    body = Path(sys.argv[1]).read_text("utf-8-sig")
    body = re.sub(r"(?s)^.*?</languageSettings>\s*", "", body)
    body = body.replace("</document>", "")
    print(run(body, SP / (sys.argv[2] if len(sys.argv) > 2 else "e1")))
