"""Ручная сверка эталона со своей картой свойств: пара «копия <Тип>_<Свойство> — <Тип>_Эталон», все различия по путям.
python scripts/_props_audit.py <epf> [<epf>…] [--only Тип] [--show]   — статусы OK / РАСХОЖДЕНИЕ / НЕТ В КАРТЕ / БЕЗ РАЗЛИЧИЙ
Не меняет карту: решения по позициям принимаю глазами по выводу."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import form_bin_map as fb  # noqa: E402
from tools.ordinary_form import MAP_PATH  # noqa: E402

MAP = json.loads(Path(MAP_PATH).read_text(encoding="utf-8"))
SKIP = {"Имя", "Верх", "Лево", "Ширина", "Высота", "ПривязкаГраниц", "ПорядокОбхода", "Данные"}


def leaves(x, path=()):
    if isinstance(x, list):
        yield path + ("#len",), len(x)
        for i, y in enumerate(x):
            yield from leaves(y, path + (i,))
    else:
        yield path, str(x)


def _own(n):
    """Узел элемента без id; в раскладке {8,…} обнулены координаты, в {14,…} — имя: пути от узла, как в карте."""
    out = []
    for i, c in enumerate(n):
        if i == 1:
            c = None
        elif isinstance(c, list) and c and str(c[0]) == "8":
            c = [x if j == 5 or j == 0 else None for j, x in enumerate(c)]  # в раскладке своего — только флаг [5] (видимость), остальное — координаты/порядок
        elif isinstance(c, list) and c and str(c[0]) == "14":
            c = [x if j != 1 else None for j, x in enumerate(c)]
        out.append(c)
    return out


def diff(a, b):
    da, db = dict(leaves(_own(a))), dict(leaves(_own(b)))
    return sorted(((p, da.get(p), db.get(p)) for p in set(da) | set(db) if da.get(p) != db.get(p)), key=lambda d: str(d[0]))


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    if only:
        args.remove(only)
    show = "--show" in sys.argv
    seen, stat = set(), {}
    for epf in args:
        forms = fb._forms(Path(epf))
        fb._learn_types(forms)
        for fname, top in forms:
            els = {nm: (et, n) for et, nm, n in fb._elements(top)}
            base = next(((nm, v) for nm, v in els.items() if "_" in nm and fb._is_etalon(nm.split("_", 1)[1]) and not fb._etalon_tail(nm.split("_", 1)[1])), None)
            if not base:
                continue
            typ = base[1][0]
            if only and typ != only:
                continue
            for nm, (et, n) in els.items():
                if nm == base[0] or "_" not in nm or et != typ:
                    continue
                prop = fb._prop_name(nm.split("_", 1)[1])
                if prop in SKIP or (typ, prop) in seen:
                    continue
                seen.add((typ, prop))
                ds = diff(base[1][1], n)
                ent = MAP.get(typ, {}).get(prop) or MAP.get("_Элемент", {}).get(prop)
                ds = [d for d in ds if not (d[1] is None and d[2] is None) and not (d[0][-1] == "#len" and len(d[0]) <= 2)]
                paths = [d[0] for d in ds]
                if not ds:
                    st = "БЕЗ РАЗЛИЧИЙ"
                elif not ent:
                    st = "НЕТ В КАРТЕ"
                else:
                    mp = tuple(ent["path"]) if "path" in ent else ()
                    inside = [p for p in paths if p[: len(mp)] == mp]
                    st = "OK" if len(inside) == len(paths) else ("ЧАСТИЧНО" if inside else "РАСХОЖДЕНИЕ")
                stat.setdefault(st, []).append((typ, prop))
                if st != "OK" or show:
                    print(f"[{st}] {typ}.{prop}  карта={ent['path'] if ent else None}")
                    for p, va, vb in ds[:(40 if '--full' in sys.argv else 8)]:
                        print(f"     {'.'.join(map(str, p))}: {str(va)[:60]!r} -> {str(vb)[:60]!r}")
                    if len(ds) > 8:
                        print(f"     … ещё {len(ds) - 8}")
    print({k: len(v) for k, v in stat.items()})


if __name__ == "__main__":
    main()
