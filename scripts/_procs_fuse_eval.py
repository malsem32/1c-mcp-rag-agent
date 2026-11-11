import sys, re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from _stress_vector_eval import Q
from config import load_config
from vector.procs import search_procs
from vector.search import search_code_vectors
cfg = load_config()
def hit(b, subs): b=b.replace("\\","/").lower(); return any(s.lower() in b for s in subs)
res = {k:[0,0,0.0] for k in ("code","fused","fused_w")}
qs=[q for q in Q if q[2] in ("nl","id")]
for q,subs,t in qs:
    code=[(m.get("path") or "", " ".join(str(m.get(k) or "") for k in ("path","object_name"))+" "+" ".join(map(str,m.get("proc_names") or []))) for m in search_code_vectors(q,limit=20,match_mode="hybrid",cfg=cfg).get("matches") or []]
    pr=[(h.get("source_path") or "", " ".join(str(h.get(k) or "") for k in ("source_path","object_name","proc_name"))) for h in search_procs(q,limit=20,cfg=cfg)]
    for name,w in (("code",None),("fused",1.0),("fused_w",0.5)):
        if w is None: lst=code
        else:
            sc={};blob={}
            for r,(p,b) in enumerate(code): sc[p]=sc.get(p,0)+1/(60+r); blob[p]=b
            for r,(p,b) in enumerate(pr): sc[p]=sc.get(p,0)+w/(60+r); blob.setdefault(p,b)
            lst=[(p,blob[p]) for p,_ in sorted(sc.items(),key=lambda x:-x[1])]
        rank=next((i+1 for i,(p,b) in enumerate(lst[:10]) if hit(b,subs)),0)
        g=res[name]; g[0]+=rank==1; g[1]+=bool(rank); g[2]+=1/rank if rank else 0
n=len(qs)
for k,g in res.items(): print(f"{k:<8} hit@1={g[0]/n:.2f} hit@10={g[1]/n:.2f} MRR={g[2]/n:.3f}")
