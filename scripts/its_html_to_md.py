"""
Сохранённые страницы ИТС (HTML) → чистый markdown для knowledge/документация/.

Главы руководства разработчика (cp1251, имена «Глава N. …»):
    python scripts/its_html_to_md.py "C:\\Users\\...\\Конфигурация8.3"
Стандарты и методики разработки (по одному файлу на статью):
    python scripts/its_html_to_md.py "C:\\Users\\...\\Стандартыразработки" --standards
Параметр --dry-run ничего не пишет.

Отбор глав — в SELECT ниже (core — основной приоритет, extra — пониженный).
Всё остальное (интерфейс, конфигуратор, узкие механизмы) пропускается.
Глава режется на файлы по разделам второго уровня; в первой строке — путь «Глава › раздел».
"""
from __future__ import annotations

import argparse
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DEFAULT = ROOT / "knowledge" / "документация" / "its_8.3.24"
OUT_STANDARDS = ROOT / "knowledge" / "документация" / "its_standards"

# номер главы / «П<номер>» для приложений → приоритет
SELECT: dict[str, str] = {}
for _n in ("4", "5", "8", "9", "10", "11", "12", "19", "22", "П6", "П8"):
    SELECT[_n] = "core"
for _n in ("7", "13", "14", "15", "16", "17", "21", "24", "25", "35", "39", "П2", "П4", "П7", "П10"):
    SELECT[_n] = "extra"

SKIP_TAGS = {"script", "style", "head", "noscript", "img", "svg"}
SKIP_P = {"Picture", "MsoCaption", "Regularbeforepicture"}
BLOCK_END = {"p", "div", "li", "tr", "table", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}


class Conv(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.skip_stack: list[str] = []
        self.cur: list[str] = []
        self.kind = "p"  # p | h1..h6 | li | note | subheader | code
        self.indent = 0
        self.num = False
        self.pre_cls = ""
        self.pre_buf: list[str] = []
        self.in_pre = False
        self.table: list[list[str]] | None = None
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.bold = 0
        self.code: list[str] = []  # накопленные строки кода из p.Programtext
        self.code_cur: list[str] = []
        self.lists: list[str] = []  # стек ul/ol

    # --- вывод
    def _emit_code(self) -> None:
        if self.code:
            body = "\n".join(self.code).strip("\n")
            self.code = []
            if body.strip():
                self.out.append(f"\n```bsl\n{body}\n```\n")

    def _out(self, s: str) -> None:
        self._emit_code()
        self.out.append(s)

    def _flush(self) -> None:
        txt = "".join(self.cur)
        self.cur = []
        txt = re.sub(r"\s+", " ", txt.replace("\xa0", " ")).strip()
        txt = re.sub(r"^[●•·▪]\s*", "", txt)
        if not txt:
            return
        k = self.kind
        if k.startswith("h") and len(k) == 2:
            self._out("\n" + "#" * int(k[1]) + " " + txt + "\n")
        elif k == "li":
            self._out("  " * self.indent + ("1. " if self.num else "- ") + txt)
        elif k == "note":
            self._out("> " + txt)
        elif k == "subheader":
            self._out("\n**" + txt + "**")
        else:
            self._out("\n" + txt if self.out and not self.out[-1].startswith(("- ", "  ", "1.")) else txt)
        self.kind, self.indent, self.num = "p", 0, False

    # --- теги
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if self.skip:
            if tag not in ("br", "img", "link", "meta", "input", "hr"):
                self.skip_stack.append(tag)
                self.skip += 1
            return
        hidden = (tag == "span" and "Hidden" in cls) or (tag == "a" and "copy_source" in cls)
        if tag in SKIP_TAGS or hidden or (tag == "p" and cls in SKIP_P):
            if tag not in ("img",):
                self.skip += 1
                self.skip_stack.append(tag)
            return
        if tag == "pre":
            self._flush()
            self.in_pre, self.pre_buf, self.pre_cls = True, [], cls
            return
        if self.in_pre:
            return
        if tag == "table":
            self._flush()
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in ("td", "th") and self.table is not None:
            self.cell = []
        elif re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self.kind = tag
        elif tag in ("ul", "ol"):
            self._flush()
            self.lists.append(tag)
        elif tag == "li" and self.cell is None:
            self._flush()
            self.kind = "li"
            self.indent = max(len(self.lists) - 1, 0)
            self.num = bool(self.lists) and self.lists[-1] == "ol"
        elif tag == "p" and self.cell is None:
            self._flush()
            m = re.match(r"MsoList(Bullet|Number)(\d?)", cls)
            if m:
                self.kind = "li"
                self.num = m.group(1) == "Number"
                self.indent = max(int(m.group(2) or 1) - 1, 0)
            elif cls == "Programtext":
                self.kind = "code"
                self.code_cur = []
            elif cls.startswith("Note"):
                self.kind = "note"
            elif cls == "Lang-subheader":
                self.kind = "subheader"
        elif tag == "br":
            if self.kind == "code":
                self.code_cur.append("\n")
            else:
                self._add("\n" if self.kind == "pre" else " ")
        elif tag in ("b", "strong") or (tag == "span" and cls == "Bold"):
            if self.kind != "code":
                self.bold += 1
                self._add("**")

    def handle_endtag(self, tag):
        if self.skip:
            if self.skip_stack and self.skip_stack[-1] == tag:
                self.skip_stack.pop()
                self.skip -= 1
            return
        if tag == "pre" and self.in_pre:
            code = "".join(self.pre_buf).replace("\xa0", " ").strip("\n")
            lang = "bsl" if ("src_dev" in self.pre_cls or "Programtext" in self.pre_cls) else ""
            if code.strip():
                self._out(f"\n```{lang}\n{code}\n```\n")
            self.in_pre = False
            return
        if self.in_pre:
            return
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            t = re.sub(r"\s+", " ", "".join(self.cell)).strip().replace("|", "/")
            self.row.append(t)
            self.cell = None
        elif tag == "tr" and self.row is not None and self.table is not None:
            if any(self.row):
                self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            rows = self.table
            self.table = None
            if rows and len(rows) == 1 and len(rows[0]) == 1:
                self._out("\n> " + rows[0][0] + "\n")  # одноячеечная таблица = врезка (примечание/совет)
            elif rows:
                w = max(len(r) for r in rows)
                rows = [r + [""] * (w - len(r)) for r in rows]
                lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * w]
                lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
                self._out("\n" + "\n".join(lines) + "\n")
        elif tag in ("ul", "ol") and self.lists:
            self._flush()
            self.lists.pop()
        elif tag == "p" and self.kind == "code":
            line = "".join(self.code_cur).replace("\xa0", " ").rstrip()
            self.code_cur = []
            self.code.extend(line.split("\n"))
            self.kind = "p"
        elif tag in ("b", "strong") or (tag == "span" and self.bold and False):
            if self.bold:
                self.bold -= 1
                self._add("**")
        elif tag in BLOCK_END and self.cell is None:
            self._flush()

    def _add(self, s: str) -> None:
        if self.cell is not None:
            self.cell.append(s)
        else:
            self.cur.append(s)

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_pre:
            self.pre_buf.append(data)
        elif self.kind == "code" and self.cell is None:
            self.code_cur.append(data)
        else:
            self._add(data)

    def result(self) -> str:
        self._flush()
        self._emit_code()
        txt = "\n".join(self.out)
        txt = re.sub(r"\*\*\s*\*\*", "", txt)
        txt = re.sub(r"\n{3,}", "\n\n", txt)
        return txt.strip() + "\n"


def decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1251", errors="replace")


def to_md(raw: bytes) -> str:
    text = decode(raw)
    i = text.lower().find("<body")
    c = Conv()
    c.feed(text[i:] if i >= 0 else text)
    c.close()
    return c.result()


def key_of(name: str) -> str | None:
    m = re.match(r"Глава\s+(\d+)\.", name)
    if m:
        return m.group(1)
    m = re.match(r"Приложение\s+(\d+)\.", name)
    return "П" + m.group(1) if m else None


def slug(s: str, n: int = 60) -> str:
    s = re.sub(r"[^\w\-. ]+", "", s, flags=re.U).strip(" .").replace(" ", "_")
    return s[:n] or "раздел"


def split_h2(md: str) -> tuple[str, list[tuple[str, str]]]:
    """(заголовок главы, [(заголовок раздела h2, текст)]); вступление до первого h2 — «Введение»."""
    parts = re.split(r"(?m)^(## .+)$", md)
    head = parts[0].strip()
    title = head.splitlines()[0].lstrip("# ").strip() if head else ""
    secs: list[tuple[str, str]] = []
    intro = "\n".join(head.splitlines()[1:]).strip()
    if len(intro) > 200:
        secs.append(("Введение", intro))
    for i in range(1, len(parts), 2):
        secs.append((parts[i].lstrip("# ").strip(), parts[i + 1].strip() if i + 1 < len(parts) else ""))
    return title, secs


def clean_standard(md: str) -> tuple[str, dict[str, str]]:
    """Убрать из статьи стандарта служебное и вынести его во frontmatter: id, область применения, «См. также»."""
    meta: dict[str, str] = {}
    m = re.search(r"(?m)^#(std\d+)\s*$", md)
    if m:
        meta["std"] = m.group(1)
        md = md.replace(m.group(0), "", 1)
    m = re.search(r"(?m)^Область применения:\s*([^\n]+?)\.?\s*$", md)
    if m:
        meta["scope"] = m.group(1).strip()
        md = md.replace(m.group(0), "", 1)
    # раздел «См. также» — список заголовков без адресов: оставить как перечень связанных стандартов
    m = re.search(r"(?ms)^(?:#{2,4} )?См\. также\s*\n(.*?)(?=^#{2,4} |\Z)", md)
    if m:
        items = [re.sub(r"^[-*]\s*", "", ln).strip() for ln in m.group(1).splitlines() if ln.strip()]
        if items:
            meta["see_also"] = "; ".join(items)
        md = md.replace(m.group(0), "", 1)
    versions = sorted(set(re.findall(r"\b8\.3\.\d+\b", md)))
    if versions or re.search(r"режим[а-я]* совместимости", md, re.I):
        meta["compat"] = "упоминает " + (", ".join(versions) if versions else "режим совместимости") + "; для режима 8.2.16 проверить"
    return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n", meta


def run_standards(src: Path, out: Path, dry: bool, scope: str = "обычное") -> int:
    """
    Страницы «Системы стандартов и методик разработки»: один файл на статью.
    scope — подстрока, которая должна быть в «Области применения» (по умолчанию «обычное»:
    у нас обычные формы). Статьи без неё и без указанной области пропускаются.
    """
    files = total = 0
    skipped: list[str] = []
    for f in sorted(src.glob("*.html")):
        md = to_md(f.read_bytes())
        if len(md) < 200:
            print(f"  пропуск (пусто): {f.name[:60]}", file=sys.stderr)
            continue
        m = re.match(r"(?m)^# (.+)$", md)
        title = m.group(1).strip() if m else f.name.split(" __ ")[0]
        if not m:
            md = f"# {title}\n\n{md}"
        body, meta = clean_standard(md)
        if scope and scope not in meta.get("scope", "").lower():
            skipped.append(f"{title} | {meta.get('scope') or 'область не указана'}")
            continue
        head = ["source: ИТС, Система стандартов и методик разработки конфигураций", "priority: high", f"standard: {title}"]
        head += [f"{k}: {v}" for k, v in meta.items()]
        doc = "---\n" + "\n".join(head) + "\n---\n" + body
        total += len(doc)
        files += 1
        if not dry:
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{slug(title, 90)}.md").write_text(doc, encoding="utf-8")
        print(f"  {title[:90]}")
    if skipped:
        rep = ROOT / "exports" / "its_standards_excluded.txt"
        if not dry:
            rep.parent.mkdir(parents=True, exist_ok=True)
            rep.write_text("\n".join(skipped) + "\n", encoding="utf-8")
        print(f"исключено по области применения (нет «{scope}»): {len(skipped)} → {rep}", file=sys.stderr)
    print(f"файлов={files}, {total // 1024} КБ → {out}", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--standards", action="store_true", help="папка со страницами стандартов, а не глав руководства")
    ap.add_argument("--only", default="", help="через запятую: номера глав (4,8) или П6")
    ap.add_argument("--scope", default="обычное", help="для --standards: нужная область применения ('' — брать все)")
    args = ap.parse_args()
    if args.standards:
        return run_standards(args.src, args.out or OUT_STANDARDS, args.dry_run, args.scope.lower())
    out_dir = args.out or OUT_DEFAULT
    only = {x.strip() for x in args.only.split(",") if x.strip()}

    total = files = 0
    for f in sorted(args.src.glob("*.html")):
        name = f.name.split(" __ ")[0]
        key = key_of(name)
        if key not in SELECT or (only and key not in only):
            continue
        prio = SELECT[key]
        md = to_md(f.read_bytes())
        chap, secs = split_h2(md)
        chap = chap or name
        sub = out_dir / ("основное" if prio == "core" else "дополнительно") / f"{int(key[1:]) + 100 if key.startswith('П') else int(key):03d}_{slug(chap, 50)}"
        for n, (sec, body) in enumerate(secs, 1):
            if len(body) < 80:
                continue
            doc = (
                f"---\nsource: ИТС 1С:Предприятие 8.3.24, Руководство разработчика\n"
                f"priority: {'high' if prio == 'core' else 'low'}\nchapter: {chap}\n---\n"
                f"# {chap} › {sec}\n\n{body}\n"
            )
            total += len(doc)
            files += 1
            if not args.dry_run:
                sub.mkdir(parents=True, exist_ok=True)
                (sub / f"{n:02d}_{slug(sec)}.md").write_text(doc, encoding="utf-8")
        print(f"{prio:5} {key:>4} {chap[:60]:60} разделов={len(secs)}")
    print(f"файлов={files}, {total // 1024} КБ → {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
