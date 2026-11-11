"""Отклонения копий от самого частого значения по форме (когда у формы нет одного чистого эталона).
python scripts/_props_mode.py <epf> <ИмяФормы> [--skip путь,путь] [--node N]  (N — какой дочерний узел элемента сравнивать: 2 свойства, у диаграмм 3)   — для каждой копии пути узла [2], где она отличается от моды"""
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import form_bin_map as fb  # noqa: E402
from _props_audit import leaves  # noqa: E402


def main() -> None:
    epf, form = sys.argv[1], sys.argv[2]
    skip = {tuple(int(x) for x in s.split(".")) for s in (sys.argv[sys.argv.index("--skip") + 1].split(",") if "--skip" in sys.argv else [])}
    forms = fb._forms(Path(epf)); fb._learn_types(forms)
    top = next(t for n, t in forms if n.lower() == form.lower())
    els = {nm: n for et, nm, n in fb._elements(top) if "_" in nm}
    node = int(sys.argv[sys.argv.index("--node") + 1]) if "--node" in sys.argv else 2
    flat = {nm: dict(leaves(n[node])) for nm, n in els.items()}
    paths = set().union(*[set(f) for f in flat.values()])
    mode = {}
    for p in paths:
        c = collections.Counter(f.get(p) for f in flat.values())
        mode[p] = c.most_common(1)[0][0]
    for nm, f in flat.items():
        dev = [(p, mode[p], f.get(p)) for p in sorted(paths, key=str)
               if f.get(p) != mode[p] and p[-1] != "#len" and not any(p[:len(s)] == s for s in skip)]
        print(f"{nm}: {', '.join('.'.join(map(str, p)) + ':' + str(a)[:12] + '→' + str(b)[:12] for p, a, b in dev[:14])}")


main()
