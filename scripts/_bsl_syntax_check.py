# -*- coding: utf-8 -*-
"""
Синтаксическая проверка BSL без запуска 1С:Предприятия:
    python scripts/_bsl_syntax_check.py модуль.bsl [часть2.bsl …]   — файлы склеиваются в ОДИН модуль
Для модуля объекта и каждой формы вызывать отдельно (у форм свой модуль, `Перем` может быть только в начале модуля).
Использует `oscript -check` (OneScript). Платформенные имена (ЭлементыФормы, ТипЛинии…) и вызовы чужих модулей OneScript не знает:
скрипт объявляет их заглушками по мере ошибок «Неизвестный символ» (переменные — в начало, вызываемые как Имя(…) — функции в конец).
Ловит: незакрытые блоки, опечатки в ключевых словах, скобки, лишние точки с запятой, объявления не в начале.
НЕ ловит: неверные имена методов/свойств платформы (сверять со справкой), типы, ошибки времени выполнения.
Режим совместимости: `--compat 8.2.16` — дополнительно ищет в модуле функции/методы, появившиеся в платформе ПОЗЖЕ этой версии
(по справке «Доступен, начиная с версии …»): в конфигурации владельца Version8_2_16, и, например, СтрРазделить (8.3.6) там
недоступна. OneScript эти функции знает, поэтому без `--compat` такая ошибка не видна.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

OSCRIPT = Path(r"C:\Program Files\OneScript\bin\oscript.exe")
_ERR = re.compile(r"Ошибка в строке:\s*(\d+)(?:,(\d+))?\s*/\s*(.+?)\}", re.S)
_UNKNOWN = re.compile(r"Неизвестный символ:\s*([\wА-Яа-яЁё]+)")
_STUB_FN = "\nФункция {0}(П1 = 0, П2 = 0, П3 = 0, П4 = 0, П5 = 0, П6 = 0, П7 = 0, П8 = 0, П9 = 0)\n\tВозврат 0;\nКонецФункции\n"


def _run(text: str) -> str:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "m.os"
        p.write_text(text, encoding="utf-8-sig")
        r = subprocess.run([str(OSCRIPT), "-check", str(p)], capture_output=True, timeout=60)
    return (r.stdout + r.stderr).decode("cp866", "replace").strip() if r.returncode else ""


def check(text: str) -> dict:
    """{ok, line, message}; line — номер строки исходного текста."""
    if not OSCRIPT.is_file():
        return {"ok": False, "line": 0, "message": "OneScript не найден: " + str(OSCRIPT)}
    stubs: list[str] = []
    funcs: list[str] = []
    for _ in range(300):
        head = ("Перем " + ", ".join(stubs) + ";\n") if stubs else ""
        tail = "".join(_STUB_FN.format(f) for f in funcs)
        out = _run(head + text + tail)
        if not out:
            return {"ok": True, "line": 0, "message": ""}
        m = _ERR.search(out)
        line = (int(m.group(1)) - (1 if head else 0)) if m else 0
        msg = (m.group(3) if m else out).strip().replace("\r", "")
        u = _UNKNOWN.search(msg)
        if u and u.group(1) not in stubs and u.group(1) not in funcs:
            name = u.group(1)
            if re.search(rf"(?<![\wА-Яа-яЁё.]){name}\s*\(", text):
                funcs.append(name)
            else:
                stubs.append(name)
            continue
        return {"ok": False, "line": line, "message": msg}
    return {"ok": False, "line": 0, "message": "слишком много неизвестных символов"}


HELP = Path(__file__).resolve().parents[1] / "knowledge" / "справка_платформы" / "синтаксис_помощник"


def _ver(text: str):
    m = re.search(r"начиная с версии\s+(\d+(?:\.\d+)*)", text)
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def compat_issues(text: str, limit: tuple[int, ...]) -> list[tuple[int, str, str]]:
    """[(строка, имя, версия)] — глобальные функции и члены типов, доступные только с версии новее limit."""
    newer_globals: dict[str, tuple] = {}
    members: dict[str, list] = {}
    for f in HELP.glob("*.md"):
        v = _ver(f.read_text("utf-8", errors="replace")[:1500])
        if v is None:
            continue
        stem = f.stem
        if stem.startswith("Глобальный_контекст."):
            stem = stem.split(".", 1)[1]
        if "." not in stem:
            if v > limit and re.match(r"^[А-ЯЁ][\wА-Яа-яЁё]*$", stem):
                newer_globals[stem] = v
        else:
            members.setdefault(stem.split(".")[-1], []).append(v)
    newer_members = {n: min(vs) for n, vs in members.items() if min(vs) > limit and len(n) > 4}
    out = []
    call = re.compile(r"(?<![\wА-Яа-яЁё.])([А-Яа-яЁёA-Za-z_][\wА-Яа-яЁё]*)\s*\(")
    member = re.compile(r"\.([А-Яа-яЁёA-Za-z_][\wА-Яа-яЁё]*)")
    for no, line in enumerate(text.splitlines(), 1):
        code = re.sub(r'"[^"]*"', '""', line.split("//")[0])
        for m in call.finditer(code):
            if m.group(1) in newer_globals:
                out.append((no, m.group(1), ".".join(map(str, newer_globals[m.group(1)]))))
        for m in member.finditer(code):
            if m.group(1) in newer_members:
                out.append((no, "." + m.group(1), ".".join(map(str, newer_members[m.group(1)]))))
    return out


def main() -> int:
    args = sys.argv[1:]
    compat = None
    if "--compat" in args:
        i = args.index("--compat")
        compat = tuple(int(x) for x in args[i + 1].split("."))
        del args[i:i + 2]
    files = [Path(a) for a in args]
    if not files:
        print(__doc__)
        return 2
    text = "\n".join(f.read_text("utf-8-sig") for f in files)
    if compat:
        bad = compat_issues(text, compat)
        for no, name, v in bad:
            print(f"СОВМЕСТИМОСТЬ: строка {no}: {name} доступно с {v}, а режим {'.'.join(map(str, compat))}")
        if bad:
            return 1
    r = check(text)
    if r["ok"]:
        print("OK:", " + ".join(f.name for f in files))
        return 0
    lines = text.splitlines()
    ln = r["line"]
    ctx = lines[ln - 1].strip() if 0 < ln <= len(lines) else ""
    print(f"ОШИБКА в {' + '.join(f.name for f in files)}, строка {ln} (файлы подряд): {r['message']}\n    {ctx}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
