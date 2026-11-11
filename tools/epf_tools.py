"""
Кодовые tools поверх разобранной внешней обработки (.epf/.erf/.cfe).

Без этого search_code / trace_calls / explain_fill / explain_command / audit_query_code
на вопрос «…в ВыпускСО.epf» молча шли в Qdrant CF: пустой ответ с ok=true
или — хуже — ответ про одноимённый объект конфигурации (Документ.ВыпускСО).
external_dispatch перехватывает такие вызовы в UniversalTools.dispatch.
"""

from __future__ import annotations

import json
import re
from typing import Any

# tools, которые умеют работать с внешним файлом; inspect_code/proc_index — сами,
# read_code — только «файл без части» (часть читает read_external_module)
_TOOLS = frozenset(
    {"search_code", "trace_calls", "find_callers", "explain_fill", "explain_command",
     "audit_query_code", "module_outline", "read_skd_query", "analyze_object_movements",
     "describe_object", "get_object_structure", "list_object_attributes", "explain_config", "read_code"}
)
_DESCRIBE = ("describe_object", "get_object_structure", "list_object_attributes")
# «ВнешняяОбработка.Имя» / «ВнешнийОтчет.Имя» — объект внешнего файла, в CF его нет
_EXT_FULL = re.compile(r"^\s*(ВнешняяОбработка|ВнешнийОтчет|ВнешниеОбработки|ВнешниеОтчеты|ExternalDataProcessor|ExternalReport)\.(.+)$", re.I)
_LIMIT = 7600  # бюджет ответа tool (символов JSON)
_ARG_KEYS = ("path", "module_filter", "module", "full_name", "object_name", "object_hint", "request", "query")
_DECL = re.compile(r"(?im)^[ \t]*(?:&\w+[ \t]*\n[ \t]*)?(Процедура|Функция|Procedure|Function)[ \t]+([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)[ \t]*\(")
_END = re.compile(r"(?im)^[ \t]*(КонецПроцедуры|КонецФункции|EndProcedure|EndFunction)\b")
_IDENT = re.compile(r"[A-ZА-ЯЁ][\wА-Яа-яЁё]{3,}")
_STOP = {"процедура", "функция", "обработка", "обработке", "обработки", "внешняя", "внешней", "кнопка", "команда",
         "проследи", "вызовы", "поле", "реквизит", "где", "заполняется"}


def _cache() -> dict[str, Any]:
    from tools.epf_inspect import _CACHE

    return _CACHE.get("parsed") or {}


def _norm(s: str) -> str:
    return re.sub(r"[\s_]+", "", (s or "").lower().replace("ё", "е"))


def _cached_names() -> set[str]:
    """Как может называться загруженный файл: имя файла, имя/синоним объекта."""
    p = _cache()
    if not p:
        return set()
    from tools.epf_inspect import external_structure

    st = external_structure(p)
    names = {str(p.get("name") or ""), str(p.get("synonym") or ""), str(p.get("file_name") or "").rsplit(".", 1)[0],
             st.get("name") or "", st.get("synonym") or ""}
    return {_norm(n) for n in names if n}


def _declared_in_file(proc: str) -> bool:
    if not proc or not _cache():
        return False
    return any(p["name"].lower() == proc.lower() for p in _procs(_texts("")))


def _implicit_target(name: str, args: dict[str, Any]) -> bool:
    """Вызов без упоминания файла, но про процедуру/поле загруженной обработки (а не CF)."""
    if not _cache() or any(str(args.get(k) or "").strip() for k in ("path", "module", "full_name", "object_name")):
        return False
    if name in ("trace_calls", "find_callers"):
        proc = str(args.get("procedure") or "").strip()
        if not _declared_in_file(proc):
            return False
        # одноимённая процедура есть и в конфигурации (ПриОткрытии, ОбработкаПроведения…) — вопрос, скорее, про CF
        try:
            from tools.proc_index import find_definitions

            return not find_definitions(proc, "", limit=1)
        except Exception:
            return True
    if name == "explain_fill":
        f = str(args.get("field") or "").strip()
        if not re.fullmatch(r"[A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]{3,}", f):
            return False
        from tools.epf_inspect import external_structure

        st = external_structure(_cache())
        own = {a["name"].lower() for a in st.get("attributes") or []}
        own |= {t["name"].lower() for t in st.get("tabular_sections") or []}
        pat = re.compile(rf"(?<![\wА-Яа-яЁё.]){re.escape(f)}\s*(=|\.)", re.I)
        in_file = f.lower() in own or any(pat.search(b) for _, b in _texts(""))
        # только если в коде конфигурации этого имени нет — иначе вопрос, скорее, про CF
        return in_file and _cf_has_identifier(f) is False
    return False


def _cf_has_identifier(ident: str) -> bool | None:
    """Есть ли слово целиком в коде CF (точный фильтр Qdrant). None — индекс недоступен."""
    try:
        from qdrant_client.http import models as qm

        from vector.catalog import _scroll_payload, collection_name, get_qdrant

        rows = _scroll_payload(get_qdrant(), collection_name("code"),
                               scroll_filter=qm.Filter(must=[qm.FieldCondition(key="text", match=qm.MatchText(text=ident))]),
                               limit=60)
    except Exception:
        return None
    word = re.compile(rf"(?<![\wА-Яа-яЁё]){re.escape(ident)}(?![\wА-Яа-яЁё])", re.I)
    return any(word.search(str(r.get("text") or "")) for r in rows)


def external_target(name: str, args: dict[str, Any]) -> tuple[str, str] | None:
    """(файл, часть) если вызов касается внешнего файла; None — обычный вопрос по CF."""
    if name not in _TOOLS:
        return None
    from tools.epf_inspect import extract_external_path, is_external_file_question, split_external_path

    parsed = _cache()
    if name == "read_code":
        # часть файла читает read_external_module; сюда — только «Файл.epf» без части
        for k in ("path", "module"):
            v = str(args.get(k) or "").strip().strip('"')
            if re.search(r"\.(epf|erf|cfe)$", v, re.I):
                return split_external_path(v)
        return None
    if name in _DESCRIBE:
        m = _EXT_FULL.match(str(args.get("full_name") or args.get("object_name") or ""))
        if m:
            if parsed and _norm(m.group(2)) in _cached_names():
                return str(parsed.get("file") or ""), ""
            return f"{m.group(2)}.{'erf' if 'отч' in m.group(1).lower() or 'report' in m.group(1).lower() else 'epf'}", ""
    rels = [r.replace("\\", "/") for r, _ in parsed.get("_texts") or []]
    for k in _ARG_KEYS:
        v = str(args.get(k) or "").strip()
        if not v:
            continue
        if k in ("path", "module_filter", "module") and rels:
            vn = v.replace("\\", "/").strip("/")
            # «<uuid>.0/module» без имени файла — часть уже разобранного файла
            if re.search(r"\.0/(module|form|text)$", vn) and any(r.endswith(vn) for r in rels):
                return str(parsed.get("file") or ""), vn
            # «977a5523» / «977a5523-…-cbff.0» — кусок uuid части разобранного файла
            if len(vn) >= 6 and not re.search(r"\.(epf|erf|cfe)", vn, re.I) and any(vn.lower() in r.lower() for r in rels):
                return str(parsed.get("file") or ""), vn
        m = re.search(r"\.(epf|erf|cfe)([\\/]|$)", v, re.I)
        if m and k in ("path", "module_filter", "module"):
            return split_external_path(v)
        ext = extract_external_path(v)
        if ext:
            return ext, ""
    req = str(args.get("request") or "")
    if req and parsed and is_external_file_question(req):
        return str(parsed.get("file") or ""), ""
    if parsed and _implicit_target(name, args):
        return str(parsed.get("file") or ""), ""
    return None


def _texts(inner: str) -> list[tuple[str, str]]:
    texts = [(r.replace("\\", "/"), b) for r, b in _cache().get("_texts") or []]
    code = [(r, b) for r, b in texts if _DECL.search(b)]
    if inner:
        inn = inner.replace("\\", "/").strip("/").lower()
        # указана часть (в т.ч. форма без процедур) — ищем в ней, а не во всём коде
        focused = [(r, b) for r, b in texts if inn in r.lower()]
        return focused or code
    return code


def _procs(texts: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Все процедуры файла: path, name, line (объявление), end (КонецПроцедуры)."""
    out: list[dict[str, Any]] = []
    for rel, body in texts:
        lines = body.splitlines()
        starts = []
        for i, ln in enumerate(lines, 1):
            m = re.match(r"(?i)\s*(Процедура|Функция|Procedure|Function)\s+([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)\s*\(", ln)
            if m:
                starts.append((i, m.group(2), ln.strip()[:200]))
        for j, (i, nm, sig) in enumerate(starts):
            nxt = starts[j + 1][0] - 1 if j + 1 < len(starts) else len(lines)
            end = nxt
            for k in range(i, nxt):
                if _END.match(lines[k]):
                    end = k + 1
                    break
            out.append({"path": rel, "name": nm, "line": i, "end": end, "signature": sig,
                        "export": bool(re.search(r"\)\s*Экспорт", sig, re.I))})
    return out


def _owner(procs: list[dict[str, Any]], path: str, line: int) -> str:
    for p in procs:
        if p["path"] == path and p["line"] <= line <= p["end"]:
            return p["name"]
    return ""


def _strip_file(s: str) -> str:
    """Убрать из фразы имя файла, в т.ч. с пробелами («Расчет плановой себестоимости.epf»)."""
    s = s or ""
    fn = str(_cache().get("file_name") or "")
    if fn:
        s = re.sub(re.escape(fn), " ", s, flags=re.I)
    from tools.epf_inspect import extract_external_path

    ext = extract_external_path(s)
    if ext:
        s = s.replace(ext, " ")
    return re.sub(r"[^\s\\/]+\.(epf|erf|cfe)\S*", " ", s, flags=re.I)


def _idents(*vals: str) -> list[str]:
    """PascalCase-имена из фразы, без имени файла и служебных слов."""
    out: list[str] = []
    for v in vals:
        v = _strip_file(v)
        for t in _IDENT.findall(v):
            if t.lower() not in _STOP and t not in out:
                out.append(t)
    return out


def _base(inner: str) -> dict[str, Any]:
    p = _cache()
    return {"ok": True, "source": "external_file", "file_name": p.get("file_name"),
            "part": inner or None,
            "note": "Ответ по коду внешнего файла, не по конфигурации. Тело — read_code(path, start_line=line)."}


def _module_labels() -> dict[str, str]:
    """path части → «модуль формы ФормаОсновная (Форма основная)» / «модуль объекта»."""
    p = _cache()
    lab: dict[str, str] = {}
    for f in p.get("forms_ui") or []:
        mp = str(f.get("module_path") or "").replace("\\", "/")
        if mp:
            syn = str(f.get("synonym") or "")
            lab[mp] = f"модуль формы {f['form']}" + (f" ({syn})" if syn and _norm(syn) != _norm(f["form"]) else "")
    for r, _b in p.get("_texts") or []:
        r = r.replace("\\", "/")
        if r in lab:
            continue
        if r.endswith(".0/text"):
            lab[r] = "модуль объекта"
        elif r.endswith(".0/module"):
            lab[r] = "модуль формы"
        elif r.startswith("СКД/"):
            lab[r] = "СКД"
    return lab


def _path_rank(path: str) -> int:
    """Основная форма → модуль объекта → прочие формы (одноимённые обработчики в копиях форм)."""
    from tools.epf_inspect import external_structure

    du = external_structure(_cache()).get("default_form_uuid") or ""
    if du and du in path:
        return 0
    return 1 if path.endswith(".0/text") else 2


def _fit(out: dict[str, Any], limit: int = _LIMIT) -> dict[str, Any]:
    """Предохранитель: JSON ответа ≤ limit. Режем самые большие списки пополам, затем длинные строки."""

    def size() -> int:
        return len(json.dumps(out, ensure_ascii=False, default=str))

    if size() <= limit:
        return out
    cut: dict[str, int] = {}
    target = limit - 300  # запас на пометки truncated/truncated_lists

    def lists(node: Any, path: str, depth: int):
        if depth > 4:
            return
        items = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else []
        for k, v in items:
            p = f"{path}.{k}" if path else str(k)
            if isinstance(v, list):
                yield len(json.dumps(v, ensure_ascii=False, default=str)), node, k, p, v
            if isinstance(v, (dict, list)):
                yield from lists(v, p, depth + 1)

    for _ in range(80):
        if size() <= target:
            break
        cands = [c for c in lists(out, "", 0) if len(c[4]) > 1 and c[3] != "truncated_lists"]
        if not cands:
            break
        big = max(cands, key=lambda c: c[0])
        # сначала «листовые» списки (строки: колонки, подписи), а не контейнеры целиком (ТЧ, формы)
        leaf = [c for c in cands if not any(isinstance(x, (list, dict)) for x in c[4])]
        leaf_big = max(leaf, key=lambda c: c[0]) if leaf else None
        _, parent, k, p, v = leaf_big if leaf_big and leaf_big[0] * 4 >= big[0] else big
        key = re.sub(r"\.\d+(?=\.|$)", "[]", p)
        cut.setdefault(key, len(v))
        parent[k] = v[: max(1, len(v) // 2)]
    for n in (600, 300, 160, 80):
        if size() <= target:
            break

        def shorten(node: Any) -> Any:
            if isinstance(node, dict):
                return {k: shorten(v) for k, v in node.items()}
            if isinstance(node, list):
                return [shorten(v) for v in node]
            if isinstance(node, str) and len(node) > n:
                return node[:n] + "…"
            return node

        out = shorten(out)
    if cut:
        out["truncated"] = True
        out["truncated_lists"] = cut
    if size() > limit:
        # крайний случай: только служебные поля
        keep = {k: out[k] for k in ("ok", "source", "file_name", "error", "question", "summary", "note") if k in out}
        out = keep | {"truncated": True, "note_truncated": "Ответ слишком большой — сузь вопрос (часть файла/процедура)."}
    return out


# ------------------------------------------------------------------ handlers


def _search(inner: str, args: dict[str, Any]) -> dict[str, Any]:
    q = str(args.get("query") or args.get("request") or "").strip()
    q = _strip_file(q).strip()
    lim = max(1, min(int(args.get("limit") or args.get("max_matches") or 20), 80))
    ctx = max(0, min(int(args.get("context_lines") or 0), 5))
    texts = _texts(inner)
    procs = _procs(texts)
    if args.get("regex"):
        try:
            pat = re.compile(q, re.I)
        except re.error as e:
            return {"ok": False, "error": f"Неверный regex: {e}"}
        test = pat.search
    else:
        toks = [t.lower() for t in re.findall(r"[\wА-Яа-яЁё.]{3,}", q)] or [q.lower()]
        test = lambda ln: any(t in ln.lower() for t in toks)  # noqa: E731
    matches: list[dict[str, Any]] = []
    total = 0
    for rel, body in texts:
        lines = body.splitlines()
        for i, ln in enumerate(lines, 1):
            if not q or not test(ln):
                continue
            total += 1
            if len(matches) < lim:
                m = {"path": rel, "line": i, "procedure": _owner(procs, rel, i), "snippet": ln.strip()[:240]}
                if ctx:
                    m["context"] = "\n".join(lines[max(0, i - 1 - ctx): i + ctx])[:600]
                matches.append(m)
    return _base(inner) | {"query": q, "matches": matches, "count": len(matches), "total": total,
                           "has_more": total > len(matches)}


def _trace(inner: str, args: dict[str, Any]) -> dict[str, Any]:
    from tools.trace_calls import extract_calls_from_text

    texts = _texts(inner)
    procs = _procs(texts)
    by_name: dict[str, list[dict[str, Any]]] = {}
    for p in procs:
        by_name.setdefault(p["name"].lower(), []).append(p)
    for lst in by_name.values():
        lst.sort(key=lambda p: _path_rank(p["path"]))
    want = [str(args.get("procedure") or "")] + _idents(str(args.get("request") or ""))
    start = next((by_name[w.lower()][0] for w in want if w and w.lower() in by_name), None)
    if start is None:
        return _base(inner) | {"ok": False, "need_clarification": True,
                               "question": "Из какой процедуры файла проследить вызовы?",
                               "options": [p["name"] for p in procs[:20]]}
    labels = _module_labels()
    same = [labels.get(p["path"], p["path"]) for p in by_name[start["name"].lower()][1:]]
    depth = max(1, min(int(args.get("depth") or 2), 3))
    bodies = dict(texts)
    nodes, edges, seen = [], [], set()
    frontier = [start]
    for d in range(depth):
        nxt = []
        for p in frontier:
            key = (p["path"], p["name"])
            if key in seen:
                continue
            seen.add(key)
            nodes.append({k: p[k] for k in ("path", "name", "line", "signature")})
            lines = bodies[p["path"]].splitlines()[p["line"]: p["end"]]
            for c in extract_calls_from_text("\n".join(lines), max_calls=40):
                if re.match(r"(?i)\s*(//|КонецПроцедуры|КонецФункции|EndProcedure|EndFunction)", c["snippet"] or ""):
                    continue  # «КонецПроцедуры // Имя()» — комментарий, не вызов
                tgt = by_name.get(c["name"].lower())
                e = {"from": p["name"], "call": (c["module_prefix"] + "." if c["module_prefix"] else "") + c["name"],
                     "line": p["line"] + c["line"], "snippet": c["snippet"]}
                if tgt and not c["module_prefix"]:
                    e["target"] = f'{tgt[0]["path"]}:{tgt[0]["line"]}'
                    nxt.append(tgt[0])
                elif c["module_prefix"]:
                    e["target"] = "вне файла (метод объекта или общий модуль CF)"
                else:
                    continue  # методы платформы / объектов — шум
                edges.append(e)
                if len(edges) >= 40:
                    break
        frontier = nxt
    out = _base(inner) | {
        "start_procedure": start["name"], "start_path": start["path"],
        "start_module": labels.get(start["path"], ""), "depth": depth,
        "nodes": nodes, "edges": edges, "edges_count": len(edges),
        "summary_lines": [f"Старт: {start['name']} ({labels.get(start['path'], start['path'])}, строка {start['line']})",
                          f"Узлов: {len(nodes)}, рёбер: {len(edges)}"],
    }
    if same:
        out["same_procedure_also_in"] = same[:6]
    return out


def _callers(inner: str, args: dict[str, Any]) -> dict[str, Any]:
    from tools.proc_index import external_procs

    proc = str(args.get("procedure") or "") or next(iter(_idents(str(args.get("request") or ""))), "")
    r = external_procs(str(args.get("request") or ""), proc, limit=int(args.get("limit") or 20))
    return r or (_base(inner) | {"ok": False, "error": f"Процедуры «{proc}» в файле нет."})


def _fill(inner: str, args: dict[str, Any]) -> dict[str, Any]:
    fields = [str(args.get("field") or "")] if args.get("field") else _idents(str(args.get("request") or ""))
    if not fields:
        return _base(inner) | {"ok": False, "need_clarification": True,
                               "question": "Какое поле/таблицу/переменную проверить?"}
    texts = _texts(inner)
    procs = _procs(texts)
    out: dict[str, Any] = _base(inner)
    for f in fields[:2]:
        fe = re.escape(f)
        kinds = [
            ("assign", re.compile(rf"(?<![\wА-Яа-яЁё.]){fe}\s*=(?!=)", re.I)),
            ("rows", re.compile(rf"{fe}\s*\.\s*(Добавить|Вставить|Загрузить|ЗагрузитьКолонку|Очистить|Удалить|Свернуть|Сортировать)\s*\(", re.I)),
            ("columns", re.compile(rf"{fe}\s*\.\s*Колонки\s*\.\s*(Добавить|Удалить)", re.I)),
            ("fill_props", re.compile(rf"ЗаполнитьЗначенияСвойств\s*\(\s*{fe}\b", re.I)),
            ("passed_to", re.compile(rf"[\wА-Яа-яЁё]+\s*\([^)]*(?<![\wА-Яа-яЁё.]){fe}\b", re.I)),
        ]
        hits: list[dict[str, Any]] = []
        for rel, body in texts:
            for i, ln in enumerate(body.splitlines(), 1):
                s = ln.strip()
                if s.startswith("//"):
                    continue
                for kind, pat in kinds:
                    if pat.search(ln):
                        hits.append({"kind": kind, "path": rel, "line": i,
                                     "procedure": _owner(procs, rel, i), "snippet": s[:220]})
                        break
        if hits:
            order = {"assign": 0, "rows": 1, "fill_props": 2, "columns": 3, "passed_to": 4}
            hits.sort(key=lambda h: (order[h["kind"]], h["line"]))
            out |= {"field": f, "hits": hits[:30], "hits_total": len(hits),
                    "procedures": sorted({h["procedure"] for h in hits if h["procedure"]})[:20],
                    "summary": f"«{f}»: {len(hits)} мест (assign — присваивание, rows — добавление/загрузка строк, "
                               "passed_to — передаётся в процедуру и может заполняться там)."}
            return out
    # похожие имена из кода файла: общая часть CamelCase («ТаблицаРасходов» → ТЧРасходов, КосвенныеРасходы…)
    parts = {p.lower()[:6] for f in fields[:2] for p in re.findall(r"[A-ZА-ЯЁ][a-zа-яё]{3,}", f)}
    similar: list[str] = []
    for _rel, body in texts:
        for ident in re.findall(r"(?<![\wА-Яа-яЁё.])([A-ZА-ЯЁ][\wА-Яа-яЁё]{4,})\s*(?:=|\.)", body):
            words = {p.lower()[:6] for p in re.findall(r"[A-ZА-ЯЁ][a-zа-яё]{3,}", ident)}
            if words & parts and ident not in similar:
                similar.append(ident)
        if len(similar) >= 15:
            break
    res = out | {"ok": False, "fields_tried": fields[:2], "error": "Таких имён в коде файла не найдено."}
    if similar:
        res["need_clarification"] = True
        res["question"] = "Такого имени в обработке нет. Возможно, имелось в виду одно из этих?"
        res["options"] = similar[:12]
    return res


# слова запроса, которые не бывают подписью кнопки
_CMD_STOP = {
    "что", "делает", "делают", "кнопка", "кнопки", "кнопку", "кнопкой", "команда", "команды", "команду", "при",
    "нажатии", "нажатие", "нажать", "нажимаю", "на", "в", "во", "и", "по", "с", "у", "форме", "форма", "формы",
    "обработке", "обработки", "обработка", "обработку", "этой", "эта", "как", "работает", "зачем", "нужна",
    "нужен", "для", "чего", "происходит", "если", "меню", "пункт", "пункта", "объясни", "расскажи", "покажи",
    "который", "которая", "файле", "файла", "внешней", "внешняя", "отчете", "отчета",
}


def _words(s: str) -> list[str]:
    s = _strip_file(s)
    s = re.sub(r"^\[\d+\]\s*", "", s.strip())
    return [w[:6] for w in re.findall(r"[а-яa-z0-9]+", s.lower().replace("ё", "е")) if w not in _CMD_STOP]


def _match_buttons(text: str) -> list[dict[str, Any]]:
    """Кнопки форм, чья подпись целиком есть в запросе (или совпала ≥2 словами). Лучшие — первыми."""
    req = set(_words(text))
    quoted = {_norm(q) for q in re.findall(r"[«\"']([^»\"']{2,80})[»\"']", text or "")}
    hits = []
    for f in _cache().get("forms_ui") or []:
        for b in f.get("buttons") or []:
            cw = _words(b["caption"])
            if not cw:
                continue
            got = sum(1 for w in cw if w in req)
            exact = _norm(re.sub(r"^\[\d+\]\s*", "", b["caption"])) in quoted
            if exact or got == len(cw) or got >= 2:
                hits.append({"score": (int(exact), got, got / len(cw)), "caption": b["caption"],
                             "handler": b["handler"], "form": f["form"], "module_path": f.get("module_path") or ""})
    hits.sort(key=lambda h: (h["score"], -_path_rank(h["module_path"])), reverse=True)
    return hits


def _command(inner: str, args: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(str(args.get(k) or "") for k in ("request", "command", "name"))
    procs = _procs(_texts(inner))
    labels = _module_labels()
    btns = _match_buttons(text)
    main: dict[str, Any] | None = None
    button = None
    if btns:
        b = btns[0]
        cands = [p for p in procs if p["name"] == b["handler"]]
        cands.sort(key=lambda p: (p["path"] != b["module_path"], _path_rank(p["path"])))
        if cands:
            main, button = cands[0], b
    if main is None:
        # подписи не совпали — по имени процедуры: точное > больше слов запроса в имени > короче
        words = [w.lower() for w in _idents(text)]
        scored = []
        for p in procs:
            nl = p["name"].lower()
            got = sum(1 for w in words if w in nl)
            if got:
                scored.append(((any(w == nl for w in words), got, -len(nl), -_path_rank(p["path"])), p))
        scored.sort(key=lambda x: x[0], reverse=True)
        if scored:
            main = scored[0][1]
    if main is None:
        opts: list[str] = []
        for f in _cache().get("forms_ui") or []:
            for b in f.get("buttons") or []:
                o = f'{b["caption"]} → {b["handler"]}'
                if o not in opts:
                    opts.append(o)
        return _base(inner) | {"ok": False, "need_clarification": True,
                               "question": "Какая кнопка/команда? Кнопки форм файла (подпись → обработчик):",
                               "options": opts[:30]}
    tr = _trace(inner, {"procedure": main["name"], "depth": 1})
    body = dict(_texts("")).get(main["path"], "").splitlines()[main["line"] - 1: main["end"]]
    preview = "\n".join(ln.rstrip()[:160] for ln in body[:60])
    if len(preview) > 2400:
        preview = preview[:2400] + "\n…"
    out = _base(inner) | {
        "handler": {k: main[k] for k in ("path", "name", "line", "end", "signature")}
        | {"module": labels.get(main["path"], "")},
        "body_preview": preview,
        "body_truncated": len(body) > 60 or len(preview) > 2400,
        "calls_from_handler": [{k: e[k] for k in ("call", "line", "target") if k in e}
                               for e in (tr.get("edges") or [])][:15],
        "prefer": f"Тело обработчика полностью — read_code(path='{main['path']}', start_line={main['line']}, "
                  f"max_lines={min(200, main['end'] - main['line'] + 1)}). Вглубь — trace_calls(procedure=…).",
    }
    if button:
        out["button"] = {"caption": button["caption"], "form": button["form"], "handler": button["handler"]}
        others = [f'{h["caption"]} → {h["handler"]} ({h["form"]})' for h in btns[1:]
                  if h["score"][2] == 1 and h["handler"] != button["handler"]]
        if others:
            out["other_matching_buttons"] = list(dict.fromkeys(others))[:5]
        same = sorted({h["form"] for h in btns if h["handler"] == button["handler"]} - {button["form"]})
        if same:
            out["same_button_in_forms"] = same
    else:
        out["matched_by"] = "имя процедуры (подпись кнопки в формах не найдена)"
    return out


_SEV = {"high": 0, "error": 0, "medium": 1, "warning": 1, "low": 2, "info": 3}


def _audit(inner: str, args: dict[str, Any]) -> dict[str, Any]:
    from tools.query_antipatterns import _load_book, scan_bsl_module

    lim = max(5, min(int(args.get("max_findings") or 40), 80))
    labels = _module_labels()
    procs = _procs(_texts(inner))
    findings: list[dict[str, Any]] = []
    scanned: list[str] = []
    # основная форма первой: её места попадут в примеры, копии из других форм — схлопнутся
    for rel, body in sorted(_texts(inner), key=lambda x: _path_rank(x[0])):
        scanned.append(labels.get(rel, rel))
        findings.extend(scan_bsl_module(body, path=rel, max_findings=lim))
    groups: dict[str, dict[str, Any]] = {}
    for f in findings:
        g = groups.setdefault(str(f.get("id")), {
            "id": f.get("id"), "severity": f.get("severity"), "title": f.get("title"),
            "why": f.get("why"), "recommendation": f.get("recommendation"), "count": 0, "places": [],
            "_seen": set()})
        g["count"] += 1
        path = str(f.get("path") or "")
        proc = _owner(procs, path, int(f.get("line") or 0))
        snip = re.sub(r"\s+", " ", str(f.get("snippet") or ""))[:140]
        # одна и та же процедура скопирована в несколько форм — одно место
        if (proc, snip) in g["_seen"]:
            continue
        g["_seen"].add((proc, snip))
        if len(g["places"]) < 2:
            g["places"].append({"module": labels.get(path, ""), "path": path, "line": f.get("line"),
                                "procedure": proc, "snippet": snip})
    out_groups = sorted(groups.values(), key=lambda g: (_SEV.get(str(g["severity"]), 9), -g["count"]))
    for g in out_groups:
        g["distinct_places"] = len(g.pop("_seen"))
    sev = {s: sum(g["count"] for g in out_groups if g["severity"] == s) for s in ("high", "medium", "low")}
    return {
        "ok": True, "mode": "external_file", "source": "external_file", "file_name": _cache().get("file_name"),
        "modules_scanned": scanned, "findings_total": len(findings), "severity_counts": sev,
        "groups": out_groups,
        "how_better": (_load_book().get("how_better") or [])[:6],
        "prefer": ("Перечисли группы (severity, title, count) и 1–2 места со snippet; пример правки — только "
                   "«Рекомендация (НЕ применено)». Не утверждай, что код уже исправлен. "
                   "Место целиком — read_code(path, start_line=line)."),
    }


_REG_COLL = (r"(РегистрыНакопления|РегистрыСведений|РегистрыБухгалтерии|РегистрыРасчета"
             r"|AccumulationRegisters|InformationRegisters|AccountingRegisters|CalculationRegisters)")
_REG_RU = {"регистрынакопления": "РегистрНакопления", "регистрысведений": "РегистрСведений",
           "регистрыбухгалтерии": "РегистрБухгалтерии", "регистрырасчета": "РегистрРасчета",
           "accumulationregisters": "РегистрНакопления", "informationregisters": "РегистрСведений",
           "accountingregisters": "РегистрБухгалтерии", "calculationregisters": "РегистрРасчета"}
_ID = r"([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)"
_MOVE_PATS = (
    ("Движения", re.compile(rf"(?<![\wА-Яа-яЁё])Движения\s*\.\s*{_ID}", re.I)),
    ("менеджер регистра", re.compile(rf"(?<![\wА-Яа-яЁё]){_REG_COLL}\s*\.\s*{_ID}", re.I)),
    ("НаборЗаписей", re.compile(r"НаборЗаписей|МенеджерЗаписи", re.I)),
    ("проведение документа", re.compile(r"РежимЗаписиДокумента\s*\.\s*Проведение|\.\s*Провести\s*\(", re.I)),
    ("Записать()", re.compile(r"\.\s*Записать\s*\(", re.I)),
)
_READ_REG = re.compile(rf"(?<![\wА-Яа-яЁё.])(РегистрНакопления|РегистрСведений|РегистрБухгалтерии|РегистрРасчета)\s*\.\s*{_ID}", re.I)
_NEW_DOC = re.compile(rf"(?<![\wА-Яа-яЁё])Документы\s*\.\s*{_ID}\s*\.\s*СоздатьДокумент", re.I)


def _movements(inner: str, args: dict[str, Any]) -> dict[str, Any]:
    texts = _texts(inner)
    procs = _procs(texts)
    labels = _module_labels()
    registers: dict[str, list[dict[str, Any]]] = {}
    writes: list[dict[str, Any]] = []
    writes_total = 0
    reads: list[str] = []
    new_docs: list[str] = []
    for rel, body in texts:
        for i, ln in enumerate(body.splitlines(), 1):
            s = ln.strip()
            if not s or s.startswith("//"):
                continue
            for m in _READ_REG.finditer(s):
                r = f"{m.group(1)}.{m.group(2)}"
                if r not in reads:
                    reads.append(r)
            for m in _NEW_DOC.finditer(s):
                if f"Документ.{m.group(1)}" not in new_docs:
                    new_docs.append(f"Документ.{m.group(1)}")
            for kind, pat in _MOVE_PATS:
                m = pat.search(s)
                if not m:
                    continue
                place = {"module": labels.get(rel, ""), "path": rel, "line": i,
                         "procedure": _owner(procs, rel, i), "snippet": s[:200], "kind": kind}
                reg = (f"Движения.{m.group(1)}" if kind == "Движения"
                       else f"{_REG_RU.get(m.group(1).lower(), m.group(1))}.{m.group(2)}" if kind == "менеджер регистра"
                       else "")
                if reg:
                    lst = registers.setdefault(reg, [])
                    if len(lst) < 3:
                        lst.append(place)
                else:
                    writes_total += 1
                    writes.append(place)
                break
    # проведение документов — главное для вопроса о движениях: первыми
    posts = [w for w in writes if w["kind"] == "проведение документа"]
    writes = (posts + [w for w in writes if w["kind"] != "проведение документа"])[:12]
    head = "Внешняя обработка сама не проводится и своих движений не имеет."
    if registers:
        summary = f"{head} Записи в регистры в её коде: {', '.join(registers)} — места в registers[].places."
    else:
        summary = (f"{head} Прямой записи в регистры в её коде не найдено "
                   "(нет Движения.*, РегистрыНакопления./РегистрыСведений., НаборЗаписей).")
    if posts:
        summary += (f" Проводит документы из кода (мест: {len(posts)}; writes[] с kind «проведение документа», "
                    "процедуры: " + ", ".join(list(dict.fromkeys(p["procedure"] for p in posts if p["procedure"]))[:6]) + ") — "
                    "движения по регистрам делает проведение этих документов в конфигурации.")
    if writes_total > len(posts):
        summary += (f" Прочих вызовов Записать(): {writes_total - len(posts)} — запись объектов или файлов, "
                    "смотри snippet.")
    if new_docs:
        summary += (" Обработка создаёт документы " + ", ".join(new_docs[:8]) + ": если они проводятся, движения делает "
                    "их проведение — analyze_object_movements(full_name='Документ.…') по конфигурации.")
    return _base(inner) | {
        "summary": summary,
        "registers": [{"register": r, "places": pl} for r, pl in registers.items()],
        "writes": writes, "writes_total": writes_total,
        "creates_documents": new_docs[:15],
        "reads_registers_in_queries": reads[:20],
        "note": "Ответ по коду внешнего файла, не по конфигурации. reads_registers_in_queries — только чтение "
                "в запросах, не движения.",
    }


def _brief_attr(a: dict[str, Any]) -> str:
    """«Имя «Синоним»: Тип»; синоним опущен, если это то же имя с пробелами."""
    syn = a.get("synonym") or ""
    s = a["name"] + (f" «{syn}»" if syn and _norm(syn) != _norm(a["name"]) else "")
    return s + (f": {a['type']}" if a.get("type") else "")


def _describe(inner: str, args: dict[str, Any]) -> dict[str, Any]:
    from tools.epf_inspect import external_structure

    p = _cache()
    st = external_structure(p)
    kind = str(p.get("kind") or "")
    cls = {"ExternalReport": "ВнешнийОтчет", "ConfigurationExtension": "Расширение"}.get(kind, "ВнешняяОбработка")
    name = st.get("name") or str(p.get("name") or "")
    ts = [{"name": t["name"], "synonym": t.get("synonym") or "", "columns_count": len(t["columns"]),
           "columns": [_brief_attr(c) for c in t["columns"]]} for t in st.get("tabular_sections") or []]
    labels = _module_labels()
    out = _base(inner) | {
        "full_name": f"{cls}.{name}", "name": name, "synonym": st.get("synonym") or p.get("synonym") or "",
        "kind": kind,
        "structure_brief": (f"Реквизитов: {len(st.get('attributes') or [])}; табличных частей: {len(ts)}"
                            + (" (" + ", ".join(t["name"] for t in ts) + ")" if ts else "")),
        "attributes": [_brief_attr(a) for a in st.get("attributes") or []],
        "tabular_sections": ts,
        "forms": p.get("forms") or [],
        "templates": p.get("templates") or [],
        "modules": [{"module": labels.get(r, r), "path": r, "procedures": len(_procs([(r, b)]))}
                    for r, b in sorted(_texts(""), key=lambda x: _path_rank(x[0]))],
        "note": "Структура внешнего файла (не объект конфигурации). Формат «Имя «Синоним»: Тип»; синоним опущен, "
                "если совпадает с именем. Интерфейс форм — describe_object(…, include_form_layout=True).",
    }
    if not st.get("attributes") and not ts:
        out["attributes_note"] = "Реквизитов и табличных частей в метаданных файла не найдено."
    if str(args.get("include_form_layout")).lower() == "true":
        # основная форма подробно, остальные — подписи кнопок; одна форма целиком —
        # inspect_external_processing(path=form_path)
        forms = sorted(p.get("forms_ui") or [], key=lambda f: _path_rank(f.get("module_path") or ""))
        out["forms_ui"] = [
            {"form": f["form"], "synonym": f["synonym"], "type": f["type"], "form_path": f["form_path"], "main": True,
             "buttons": [f'{b["caption"]} → {b["handler"]}' for b in f["buttons"]][:25],
             "event_handlers": f["event_handlers"][:15], "captions": f["captions"][:30]}
            for f in forms[:1]
        ] + [
            {"form": f["form"], "synonym": f["synonym"], "form_path": f["form_path"],
             "buttons": [b["caption"] for b in f["buttons"]][:15]}
            for f in forms[1:]
        ]
    return out


def _overview(inner: str, args: dict[str, Any]) -> dict[str, Any]:
    from tools.epf_inspect import inspect_external_processing

    r = inspect_external_processing(path=str(_cache().get("file") or ""), request=str(args.get("request") or ""))
    return dict(r) | {"note_routing": "Вопрос про внешний файл — обзор файла, не конфигурации."}


def _read_clarify(inner: str, args: dict[str, Any]) -> dict[str, Any]:
    labels = _module_labels()
    rows = [{"module": labels.get(r, "модуль"), "path": r, "procedures": len(_procs([(r, b)])),
             "lines": len(b.splitlines())} for r, b in sorted(_texts(""), key=lambda x: _path_rank(x[0]))]
    return {
        "ok": False, "source": "external_file", "file_name": _cache().get("file_name"),
        "need_clarification": True,
        "question": "Какой модуль файла показать?",
        "options": [f"{x['module']} → path='{x['path']}' ({x['procedures']} процедур, {x['lines']} строк)" for x in rows],
        "modules": rows,
        "hint": "read_code(path=<path модуля>, start_line=…, max_lines≤200). Найти процедуру — "
                "module_outline(path=файл) или proc_index(procedure=Имя).",
    }


def _outline(inner: str, args: dict[str, Any]) -> dict[str, Any]:
    flt = str(args.get("name_filter") or "").lower()
    procs = [p for p in _procs(_texts(inner)) if not flt or flt in p["name"].lower()]
    lim = max(1, min(int(args.get("max_symbols") or 200), 300))
    return _base(inner) | {"symbols": [{k: p[k] for k in ("path", "name", "line", "end", "export")} for p in procs[:lim]],
                           "total": len(procs)}


def _skd(inner: str, args: dict[str, Any]) -> dict[str, Any]:
    from tools.epf_inspect import read_external_module

    parts = [r for r, _ in _cache().get("_texts") or [] if r.startswith("СКД/") and r.endswith("/запрос")]
    if not parts:
        return _base(inner) | {"ok": False, "error": "В файле нет СКД. Запросы из кода — search_code(query='ВЫБРАТЬ')."}
    i = max(0, min(int(args.get("query_index") or 0), len(parts) - 1))
    r = read_external_module(parts[i], 1, 200) or {}
    return r | {"queries": parts}


_HANDLERS = {
    "search_code": _search, "trace_calls": _trace, "find_callers": _callers, "explain_fill": _fill,
    "explain_command": _command, "audit_query_code": _audit, "module_outline": _outline, "read_skd_query": _skd,
    "analyze_object_movements": _movements, "describe_object": _describe, "get_object_structure": _describe,
    "list_object_attributes": _describe, "explain_config": _overview, "read_code": _read_clarify,
}


def _mentions_file(args: dict[str, Any]) -> bool:
    from tools.epf_inspect import is_external_file_question

    if any(re.search(r"\.(epf|erf|cfe)\b", str(v), re.I) or _EXT_FULL.match(str(v)) for v in args.values()):
        return True
    return is_external_file_question(str(args.get("request") or ""))


def external_dispatch(name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """Ответ по внешнему файлу или None (обычный путь через CF). Ответ всегда ≤ _LIMIT символов JSON."""
    args = args or {}
    tgt = external_target(name, args)
    if tgt is None:
        return None
    from tools.epf_inspect import ensure_external_loaded

    file_spec, inner = tgt
    _, err = ensure_external_loaded(file_spec)
    if err:
        # не уходим молча в CF: там одноимённый объект даст неверный ответ
        return {"ok": False, "source": "external_file", "error": err,
                "hint": "Вложи файл в чат Open WebUI или положи в Downloads/uploads RAG-сервера; "
                        "terminal/grep Open WebUI эти файлы не видят."}
    out = _HANDLERS[name](inner, args)
    if isinstance(out, dict) and not _mentions_file(args):
        # файл не назван: процедура/поле нашлись в загруженной обработке
        out["note_source"] = (f"Отвечено по загруженной внешней обработке «{_cache().get('file_name')}». "
                              "Если имелась в виду конфигурация — укажи path (модуль/объект CF).")
    return _fit(out) if isinstance(out, dict) else out
