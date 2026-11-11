"""Стенд реквизитов/ТЧ отчёта (оракул — платформа): erf -> XML -> добавляем Attribute/TabularSection -> erf -> скобки метаданных.
    erf_attr_lab.py <исходный.erf> [папка опыта]
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import epf_inspect as ei
from tools.epf_build import _platform_exe

CR = chr(13)
SYN = "<Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>{n}</v8:content></v8:item></Synonym>"


def attr_xml(uid: str, name: str, typ: str) -> str:
    return (f'<Attribute uuid="{uid}"><Properties><Name>{name}</Name>' + SYN.format(n=name) + f"<Comment/>{typ}"
            "</Properties></Attribute>")


STR = "<Type><v8:Type>xs:string</v8:Type><v8:StringQualifiers><v8:Length>50</v8:Length><v8:AllowedLength>Variable</v8:AllowedLength></v8:StringQualifiers></Type>"
NUM = "<Type><v8:Type>xs:decimal</v8:Type><v8:NumberQualifiers><v8:Digits>15</v8:Digits><v8:FractionDigits>2</v8:FractionDigits><v8:AllowedSign>Any</v8:AllowedSign></v8:NumberQualifiers></Type>"
CHILD = (attr_xml("aaaaaaaa-0000-0000-0000-000000000001", "Орг", STR)
         + '<TabularSection uuid="bbbbbbbb-0000-0000-0000-000000000001"><InternalInfo>'
         '<xr:GeneratedType name="ExternalReportTabularSection.ТестОтчетаОтборы.Табл" category="TabularSection">'
         "<xr:TypeId>cccccccc-0000-0000-0000-000000000001</xr:TypeId><xr:ValueId>cccccccc-0000-0000-0000-000000000002</xr:ValueId>"
         "</xr:GeneratedType>"
         '<xr:GeneratedType name="ExternalReportTabularSectionRow.ТестОтчетаОтборы.Табл" category="TabularSectionRow">'
         "<xr:TypeId>cccccccc-0000-0000-0000-000000000003</xr:TypeId><xr:ValueId>cccccccc-0000-0000-0000-000000000004</xr:ValueId>"
         "</xr:GeneratedType></InternalInfo><Properties><Name>Табл</Name>" + SYN.format(n="Табл") + "<Comment/></Properties>"
         "<ChildObjects>" + attr_xml("aaaaaaaa-0000-0000-0000-000000000002", "Сумма", NUM) + "</ChildObjects></TabularSection>")


def walk(blob: bytes, depth: int = 0):
    for n, b in ei._v8_unpack_files(blob):
        raw = ei._inflate(b) or b
        yield depth, n, raw
        if ei._is_v8_container(raw):
            yield from walk(raw, depth + 1)


def main() -> None:
    src = Path(sys.argv[1])
    work = Path(tempfile.gettempdir()) / "erf_attr_lab" / (sys.argv[2] if len(sys.argv) > 2 else "e1")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    exe = str(_platform_exe())
    ib, xml, log = work / "ib", work / "xml", work / "log.txt"
    subprocess.run([exe, "CREATEINFOBASE", f"File={ib}"], timeout=180, capture_output=True)
    subprocess.run([exe, "DESIGNER", "/F", str(ib), "/DisableStartupDialogs", "/DumpExternalDataProcessorOrReportToFiles",
                    str(xml / "root.xml"), str(src), "/Out", str(log)], timeout=300, capture_output=True)
    print("DUMP:", log.read_text("utf-8-sig", errors="replace")[-200:])
    print("FILES:", [str(p.relative_to(xml)) for p in xml.rglob("*")])
    root = xml / "root.xml"
    t = root.read_text("utf-8-sig")
    t = t.replace("<ChildObjects>", "<ChildObjects>" + CHILD, 1)
    root.write_text(t, "utf-8-sig")
    out = work / "out.erf"
    subprocess.run([exe, "DESIGNER", "/F", str(ib), "/DisableStartupDialogs", "/LoadExternalDataProcessorOrReportFromFiles",
                    str(root), str(out), "/Out", str(log)], timeout=300, capture_output=True)
    print("LOAD:", log.read_text("utf-8-sig", errors="replace")[-300:])
    for d, n, raw in walk(out.read_bytes()):
        if d == 0 and n not in ("copyinfo", "root", "version", "versions") and not n.endswith(".0") and raw[:1] in (bytes([0xEF]), b"{"):
            txt = raw.decode("utf-8-sig", errors="replace").replace(CR, "")
            (work / f"{n}.txt").write_text(txt, "utf-8")
            print("==", n, len(txt))
        if d == 0 and n == "copyinfo":
            (work / "copyinfo.txt").write_text(raw.decode("utf-8-sig", errors="replace").replace(CR, ""), "utf-8")
    print(work)


if __name__ == "__main__":
    main()
