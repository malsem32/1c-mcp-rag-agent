# -*- coding: utf-8 -*-
"""Поиск по Qdrant: semantic / exact (lexical) / hybrid + RRF."""

from __future__ import annotations

import re
from typing import Any

from qdrant_client.http import models as qm

from config import load_config
from vector.client import collection_name, get_qdrant, qdrant_enabled
from vector.embed import Embedder, strip_context_header

_CONTEXT_END = "// --- КОНЕЦ КОНТЕКСТА ---"
_RRF_K = 60


def _collection_points(client: Any, name: str) -> int:
    try:
        info = client.get_collection(name)
        return int(getattr(info, "points_count", 0) or 0)
    except Exception:
        return 0


def _vector_search(
    client: Any,
    *,
    collection_name: str,
    vector: list[float],
    query_filter: Any = None,
    limit: int = 12,
) -> list[Any]:
    if hasattr(client, "query_points"):
        res = client.query_points(
            collection_name=collection_name,
            query=vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        return list(getattr(res, "points", None) or [])
    return list(
        client.search(
            collection_name=collection_name,
            query_vector=vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
    )


def _body_from_payload(pl: dict[str, Any]) -> str:
    text = pl.get("text") or ""
    return strip_context_header(text).strip() if text else ""


def _snippet_around(body: str, needle: str, *, radius: int = 450) -> str:
    if not body:
        return ""
    low = body.lower()
    n = (needle or "").lower()
    if not n:
        return body[:1200] + ("…" if len(body) > 1200 else "")
    i = low.find(n)
    if i < 0:
        # первая процедура — якорь
        m = re.search(
            r"(?im)^\s*(?:Процедура|Функция|Procedure|Function)\s+\S+",
            body,
        )
        if m:
            i = m.start()
            n = body[m.start() : m.end()]
        else:
            return body[:1200] + ("…" if len(body) > 1200 else "")
    a = max(0, i - radius)
    b = min(len(body), i + max(len(needle), len(n)) + radius)
    sn = body[a:b]
    if a > 0:
        sn = "…" + sn
    if b < len(body):
        sn = sn + "…"
    return sn


def _build_filter(
    *,
    metadata_class: str = "",
    object_name: str = "",
    content_type: str = "",
    module_filter: str = "",
) -> Any:
    must: list[Any] = []
    if metadata_class:
        must.append(
            qm.FieldCondition(
                key="metadata_class",
                match=qm.MatchValue(value=metadata_class),
            )
        )
    if object_name:
        must.append(
            qm.FieldCondition(
                key="object_name",
                match=qm.MatchValue(value=object_name),
            )
        )
    if content_type:
        must.append(
            qm.FieldCondition(
                key="content_type",
                match=qm.MatchValue(value=content_type),
            )
        )
    if module_filter:
        must.append(
            qm.FieldCondition(
                key="source_path",
                match=qm.MatchText(text=module_filter.replace("\\", "/")),
            )
        )
    return qm.Filter(must=must) if must else None


def _match_to_dict(
    pl: dict[str, Any], *, score: float, query: str, match_kind: str
) -> dict[str, Any]:
    body = _body_from_payload(pl)
    return {
        "id": pl.get("doc_id") or pl.get("_id") or "",
        "score": round(float(score), 4),
        "path": pl.get("source_path") or "",
        "location": pl.get("location") or "",
        "object_name": pl.get("object_name") or "",
        "metadata_class": pl.get("metadata_class") or "",
        "content_type": pl.get("content_type") or "",
        "xml_tag": pl.get("xml_tag") or "",
        "form_name": pl.get("form_name") or "",
        "module_role": pl.get("module_role") or "",
        "snippet": _snippet_around(body, query),
        "char_count": pl.get("char_count") or len(body),
        "match_kind": match_kind,
        "source": "qdrant",
        "proc_names": pl.get("proc_names") or [],
    }


def _merge_filters(base: Any, extra_should: list[Any]) -> Any:
    must = list(getattr(base, "must", None) or []) if base else []
    return qm.Filter(must=must, should=extra_should)


def _score_lexical_hit(pl: dict[str, Any], q: str, pattern: re.Pattern[str]) -> float:
    body = _body_from_payload(pl)
    extra = " ".join(
        [
            pl.get("source_path") or "",
            pl.get("location") or "",
            " ".join(pl.get("proc_names") or [])
            if isinstance(pl.get("proc_names"), list)
            else str(pl.get("proc_names_str") or ""),
        ]
    )
    hay = (body + "\n" + extra).lower()
    needle = q.lower()
    cnt = hay.count(needle) if needle else 0
    if cnt == 0:
        cnt = len(pattern.findall(body + "\n" + extra))
    sc = 10.0 + min(cnt, 20) * 2.0
    spath = pl.get("source_path") or ""
    if needle_in_path(q, spath):
        sc += 15
    q_core = re.sub(r"[.\s]+$", "", (q or "").strip())
    q_core = q_core.split(".")[0] if "." in q_core else q_core
    sp_norm = spath.replace("\\", "/")
    if q_core and len(q_core) >= 4:
        if f"CommonModules/{q_core}/" in sp_norm or f"CommonModules/{q_core}." in sp_norm:
            sc += 80
        elif f"/{q_core}/" in sp_norm and "ObjectModule" in sp_norm:
            sc += 25
        procs = pl.get("proc_names") or []
        if isinstance(procs, list) and any(q_core.lower() == str(p).lower() for p in procs):
            sc += 40
        if q_core.endswith("Сервер") or q_core.endswith("Server"):
            if q_core in body or f"{q_core}." in body:
                sc += 5 + min(cnt, 30)
    return sc


def _scroll_with_filter(
    client: Any,
    col: str,
    *,
    query_filter: Any,
    limit: int,
    scan_limit: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    offset = None
    scanned = 0
    page = 64
    while scanned < scan_limit and len(out) < limit:
        try:
            records, offset = client.scroll(
                collection_name=col,
                scroll_filter=query_filter,
                limit=page,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
        except Exception:
            break
        if not records:
            break
        for rec in records:
            scanned += 1
            pl = dict(getattr(rec, "payload", None) or {})
            pl["_id"] = str(getattr(rec, "id", ""))
            out.append(pl)
            if len(out) >= limit:
                break
        if offset is None:
            break
    return out


def _lexical_query_variants(query: str) -> list[str]:
    """Полная фраза + значимые токены/PascalCase для MatchText."""
    q = (query or "").strip()
    if not q:
        return []
    out: list[str] = [q]
    # PascalCase / идентификаторы 1С
    for m in re.findall(r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_]{4,}", q):
        if m not in out:
            out.append(m)
    # значимые слова ≥4 (без стоп-слов речи)
    stop = {
        "почему",
        "какая",
        "какой",
        "какие",
        "когда",
        "куда",
        "зачем",
        "можно",
        "нужно",
        "расскажи",
        "объясни",
        "покажи",
        "найди",
        "документ",
        "документа",
        "модуле",
        "модуля",
        "процедуры",
        "функции",
        "логику",
        "работа",
        "работает",
    }
    for t in re.findall(r"[A-Za-zА-Яа-яЁё0-9_]{4,}", q):
        tl = t.lower()
        if tl in stop or t in out:
            continue
        out.append(t)
        if len(out) >= 8:
            break
    return out


def _scroll_lexical(
    client: Any,
    col: str,
    query: str,
    *,
    query_filter: Any,
    limit: int,
    scan_limit: int,
    case_sensitive: bool = False,
    regex: bool = False,
) -> list[dict[str, Any]]:
    """
    Lexical: сначала MatchText по индексированным TEXT-полям (весь корпус),
    иначе fallback — bounded scroll + substring (старое поведение).
    """
    q = query or ""
    if not q:
        return []
    try:
        pattern = re.compile(q if regex else re.escape(q), 0 if case_sensitive else re.I)
    except re.error:
        pattern = re.compile(re.escape(q), re.I)

    hits_pl: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    if not regex and len(q.strip()) >= 3:
        variants = _lexical_query_variants(q)
        # Раздельно по полям с приоритетом: OR по всем полям + «первые N из scroll» давал случайную
        # выборку для частых идентификаторов (порядок хранения, а не релевантность).
        # Имя процедуры (объявление) и путь — маленькие точные множества; текст — окно побольше, потом скоринг.
        field_plan = (
            ("proc_names_str", max(limit * 2, 40), False),
            ("source_path", max(limit * 2, 40), False),
            ("proc_words", max(limit * 2, 40), True),
            ("text", max(limit * 8, 160), False),
        )
        for v in variants:
            if len(v) < 3:
                continue
            for field, cap, lower in field_plan:
                ft = _merge_filters(
                    query_filter,
                    [qm.FieldCondition(key=field, match=qm.MatchText(text=v.lower() if lower else v))],
                )
                try:
                    batch = _scroll_with_filter(
                        client,
                        col,
                        query_filter=ft,
                        limit=cap,
                        scan_limit=max(scan_limit // max(len(variants), 1), 2000),
                    )
                except Exception:
                    batch = []
                for pl in batch:
                    pid = str(pl.get("doc_id") or pl.get("_id") or id(pl))
                    if pid in seen_ids:
                        continue
                    seen_ids.add(pid)
                    hits_pl.append(pl)
            if len(hits_pl) >= limit * 12:
                break

    # fallback: старый scroll по корпусу (если индекс пуст / короткое имя)
    if not hits_pl:
        offset = None
        scanned = 0
        page = 64
        while scanned < scan_limit and len(hits_pl) < limit * 3:
            try:
                records, offset = client.scroll(
                    collection_name=col,
                    scroll_filter=query_filter,
                    limit=page,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
            except Exception:
                break
            if not records:
                break
            for rec in records:
                scanned += 1
                pl = dict(getattr(rec, "payload", None) or {})
                pl["_id"] = str(getattr(rec, "id", ""))
                body = _body_from_payload(pl)
                extra = " ".join(
                    [
                        pl.get("source_path") or "",
                        pl.get("location") or "",
                        " ".join(pl.get("proc_names") or [])
                        if isinstance(pl.get("proc_names"), list)
                        else "",
                    ]
                )
                hay = body if case_sensitive else body.lower()
                hay2 = extra if case_sensitive else extra.lower()
                m = pattern.search(body) or pattern.search(extra)
                if not m and not regex:
                    needle = q if case_sensitive else q.lower()
                    if needle not in hay and needle not in hay2:
                        # token any-hit
                        toks = _lexical_query_variants(q)[1:]
                        if not any(
                            (t if case_sensitive else t.lower()) in hay
                            or (t if case_sensitive else t.lower()) in hay2
                            for t in toks
                        ):
                            continue
                elif not m:
                    continue
                hits_pl.append(pl)
                if len(hits_pl) >= limit * 3:
                    break
            if offset is None:
                break

    out: list[dict[str, Any]] = []
    for pl in hits_pl:
        sc = _score_lexical_hit(pl, q, pattern)
        # бонус за совпадение вариантов токенов
        for v in _lexical_query_variants(q)[1:4]:
            blob = (
                (_body_from_payload(pl) + " " + (pl.get("source_path") or "")).lower()
            )
            if v.lower() in blob:
                sc += 3
        out.append(_match_to_dict(pl, score=sc, query=q, match_kind="exact"))
    out.sort(key=lambda x: -float(x.get("score") or 0))
    return out[: limit * 2]


def needle_in_path(q: str, path: str) -> bool:
    return (q or "").lower() in (path or "").lower().replace("\\", "/")


def _rrf_merge(
    *lists: list[dict[str, Any]],
    limit: int,
    k: int = _RRF_K,
) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion по path|id (не по сырым score разных шкал)."""
    scores: dict[str, float] = {}
    best: dict[str, dict[str, Any]] = {}
    kinds: dict[str, set[str]] = {}

    for lst in lists:
        for rank, m in enumerate(lst):
            key = f"{m.get('path')}|{m.get('id') or (m.get('snippet') or '')[:40]}"
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            kinds.setdefault(key, set()).add(str(m.get("match_kind") or ""))
            prev = best.get(key)
            if prev is None or float(m.get("score") or 0) > float(prev.get("score") or 0):
                best[key] = dict(m)

    merged: list[dict[str, Any]] = []
    for key, rrf in sorted(scores.items(), key=lambda kv: -kv[1]):
        m = best[key]
        m["score"] = round(rrf * 100.0, 4)  # удобный масштаб
        mk = kinds.get(key) or set()
        if len(mk) > 1 or "hybrid" in mk:
            m["match_kind"] = "hybrid"
        elif mk:
            m["match_kind"] = next(iter(mk))
        m["rrf"] = round(rrf, 6)
        merged.append(m)
        if len(merged) >= limit:
            break
    return merged


def _light_rerank(matches: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Лёгкий lexical rerank поверх RRF (без cross-encoder)."""
    q = (query or "").strip()
    if not q or not matches:
        return matches
    toks = [t for t in re.findall(r"[A-Za-zА-Яа-яЁё0-9_]{3,}", q) if len(t) >= 3]
    if not toks:
        return matches
    scored: list[tuple[float, dict[str, Any]]] = []
    for m in matches:
        base = float(m.get("score") or 0)
        blob = " ".join(
            [
                str(m.get("path") or ""),
                str(m.get("object_name") or ""),
                str(m.get("snippet") or ""),
                " ".join(m.get("proc_names") or [])
                if isinstance(m.get("proc_names"), list)
                else "",
            ]
        ).lower()
        hit = 0
        for t in toks:
            if t.lower() in blob:
                hit += 1
        bonus = hit * 0.015 + (0.04 if hit == len(toks) else 0)
        # точное имя процедуры в proc_names
        procs = [str(p).lower() for p in (m.get("proc_names") or [])]
        for t in toks:
            if t.lower() in procs:
                bonus += 0.08
                break
        scored.append((base + bonus, m))
    scored.sort(key=lambda x: -x[0])
    out = []
    for sc, m in scored:
        mm = dict(m)
        mm["score"] = round(sc, 4)
        out.append(mm)
    return out


_STRUCT_RULES: tuple[tuple[str, str], ...] = (
    # (regex по тексту вопроса в нижнем регистре, content_type карточки)
    (r"(?<![а-яё])(макет|печатн[а-яё]+ форм|област[а-яё]+ макет|схем[а-яё]* компоновк|скд(?![а-яё])|вариант[а-яё]* отчет)", "Структура макета"),
    (r"(?<![а-яё])(команд[а-яё]* форм|кнопк|элемент[а-яё]* форм|реквизит[а-яё]* форм|на форме|обработчик[а-яё]* (события|команды) форм|форм[а-яё]* (объекта|списка|элемента|документа))", "Структура формы"),
    (r"(?<![а-яё])(реквизит|табличн[а-яё]+ част|измерени|ресурс[а-яё]* регистр|значени[а-яё]* перечислени|подсистем|подписк[а-яё]* на событи|регламентн[а-яё]+ задани)", "Структура объекта"),
)


# Слова-маркеры вопроса про структуру: терминология платформы, а не конфигурации. Из лексического запроса убираем.
_MARKER_WORDS = frozenset({
    "реквизит", "реквизиты", "реквизита", "табличная", "табличной", "часть", "части", "команда", "команды", "команду",
    "формы", "форма", "форме", "печатной", "печатная", "печатные", "параметр", "параметры", "параметра", "макет",
    "макета", "макеты", "кнопка", "кнопки", "элемент", "элементы", "измерение", "ресурс", "области", "область",
})


def strip_markers(query: str) -> str:
    """Оставить смысловые слова вопроса (без «команда формы», «параметр печатной формы» и т. п.)."""
    toks = re.findall(r"[A-Za-zА-Яа-яЁё0-9_]+", query or "")
    core = [t for t in toks if t.lower() not in _MARKER_WORDS]
    return " ".join(core) if len(core) >= 1 else (query or "")


def structure_intents(query: str) -> list[str]:
    """Типы карточек, которые стоит подмешать к результатам (по терминам платформы, не по именам объектов CF)."""
    q = (query or "").lower()
    out: list[str] = []
    for rx, ct in _STRUCT_RULES:
        if re.search(rx, q) and ct not in out:
            out.append(ct)
    return out


def search_code_vectors(
    query: str,
    *,
    limit: int = 12,
    metadata_class: str = "",
    object_name: str = "",
    content_type: str = "",
    module_filter: str = "",
    match_mode: str = "",
    regex: bool = False,
    case_sensitive: bool = False,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Поиск кода: hybrid | semantic | exact.

    hybrid = dense + MatchText lexical + RRF (+ light rerank).
    Filters: metadata_class, object_name, content_type, module_filter (path).
    """
    cfg = cfg if cfg is not None else load_config()
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "Пустой query", "matches": []}
    if not qdrant_enabled(cfg):
        return {"ok": False, "error": "Qdrant выключен", "matches": []}

    col = collection_name("code", cfg)
    try:
        client = get_qdrant(cfg)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "matches": []}

    if _collection_points(client, col) <= 0:
        return {
            "ok": False,
            "error": f"Коллекция {col} пуста",
            "matches": [],
            "hint": "python scripts/index_qdrant.py --recreate  (config qdrant.mode=url + Docker)",
        }

    mode = (match_mode or (cfg.get("qdrant") or {}).get("search_mode") or "hybrid").lower()
    if regex:
        mode = "exact"
    lim = max(1, min(int(limit or 12), 40))
    qcfg = cfg.get("qdrant") or {}
    scan_limit = int(qcfg.get("lexical_scan_limit") or 8000)
    rrf_k = int(qcfg.get("rrf_k") or _RRF_K)
    do_rerank = bool(qcfg.get("light_rerank", True))
    qfilter = _build_filter(
        metadata_class=metadata_class,
        object_name=object_name,
        content_type=content_type,
        module_filter=module_filter,
    )

    semantic: list[dict[str, Any]] = []
    lexical: list[dict[str, Any]] = []
    sem_error: str = ""
    lex_error: str = ""

    def _gather(qf: Any, sem_limit: int, lex_limit: int, lex_q: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Смысловой + лексический поиск под одним фильтром payload."""
        sem: list[dict[str, Any]] = []
        lex: list[dict[str, Any]] = []
        nonlocal sem_error, lex_error
        if mode in ("semantic", "hybrid"):
            try:
                emb = Embedder(cfg)
                hits = _vector_search(client, collection_name=col, vector=emb.embed_query(q),
                                      query_filter=qf, limit=sem_limit)
                for h in hits:
                    pl = dict(getattr(h, "payload", None) or {})
                    pl["_id"] = str(getattr(h, "id", ""))
                    sc = float(getattr(h, "score", 0) or 0) * 100
                    if q.lower() in _body_from_payload(pl).lower():
                        sc += 25
                    sem.append(_match_to_dict(pl, score=sc, query=q, match_kind="semantic"))
            except Exception as exc:  # noqa: BLE001
                sem_error = str(exc)
        if mode in ("exact", "hybrid"):
            try:
                lex = _scroll_lexical(client, col, lex_q or q, query_filter=qf, limit=lex_limit, scan_limit=scan_limit,
                                      case_sensitive=case_sensitive, regex=regex)
            except Exception as exc:  # noqa: BLE001
                lex_error = str(exc)
        return sem, lex

    semantic, lexical = _gather(qfilter, lim if mode == "semantic" else min(lim * 3, 48), lim)
    if mode == "semantic" and sem_error:
        return {"ok": False, "error": sem_error, "matches": []}
    if mode == "exact" and lex_error:
        return {"ok": False, "error": lex_error, "matches": []}

    # Маршрутизация по маркерам платформы («реквизит», «команда формы», «печатная форма»…):
    # вопрос про структуру тонет среди десятков тысяч модулей — ищем отдельно среди карточек и подмешиваем.
    card_types: list[str] = [] if (content_type or object_name or module_filter) else structure_intents(q)
    extra_lists: list[list[dict[str, Any]]] = []
    if card_types and mode == "hybrid":
        for ct in card_types:
            csem, clex = _gather(_build_filter(metadata_class=metadata_class, object_name="", content_type=ct,
                                               module_filter=""), min(lim * 2, 24), lim, strip_markers(q))
            extra_lists += [clex, csem]

    if mode == "hybrid":
        matches = _rrf_merge(lexical, semantic, *extra_lists, limit=lim, k=rrf_k)
    elif mode == "exact":
        matches = lexical[:lim]
    else:
        matches = semantic[:lim]

    if do_rerank and matches:
        matches = _light_rerank(matches, q)[:lim]

    return {
        "ok": True,
        "source": "qdrant",
        "collection": col,
        "query": q,
        "match_mode": mode,
        "matches": matches,
        "count": len(matches),
        "lexical_count": len(lexical),
        "semantic_count": len(semantic),
        "prefer": (
            "Ответь по snippet/path/match_kind. exact=точное вхождение, semantic≈смысл, "
            "hybrid=RRF. read_code(path=…) / trace_calls / explain_fill для углубления."
        ),
    }


_HELP_IDENT = re.compile(r"[A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*(?:\.[A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)*")


# Множитель веса по payload.priority документации (frontmatter `priority:` → индексатор).
# low — «спорные» главы ИТС: находятся, но ниже основных при близком score.
_PRIORITY_WEIGHT = {"low": 0.85}


def _prio_weight(payload: dict[str, Any] | None) -> float:
    return _PRIORITY_WEIGHT.get(str((payload or {}).get("priority") or "").lower(), 1.0)


class _LexHit:
    """Совместимая с ScoredPoint обёртка (id, score, payload) для лексических попаданий справки."""

    def __init__(self, pid: Any, payload: dict[str, Any], score: float) -> None:
        self.id = pid
        self.payload = payload
        self.score = score


def _help_lexical(client: Any, col: str, query: str, *, limit: int = 5) -> list[Any]:
    idents = []
    for m in _HELP_IDENT.finditer(query or ""):
        tok = m.group(0)
        if "." in tok or re.search(r"[a-zа-яё][A-ZА-ЯЁ]", tok) or (tok[:1].isupper() and len(tok) >= 6 and m.start() > 0):
            idents.append(tok)
    if not idents:
        return []
    out: dict[str, _LexHit] = {}
    for ident in idents[:3]:
        parts = [p for p in ident.split(".") if p]
        for field in ("source_path", "text"):
            must = [qm.FieldCondition(key=field, match=qm.MatchText(text=p)) for p in parts]
            try:
                recs, _ = client.scroll(
                    collection_name=col,
                    scroll_filter=qm.Filter(must=must),
                    limit=60,
                    with_payload=True,
                    with_vectors=False,
                )
            except Exception:
                recs = []
            il = ident.lower()
            last = parts[-1].lower()
            for r in recs:
                pl = dict(getattr(r, "payload", None) or {})
                title = str(pl.get("title") or "").lower()
                head = title.split(" (")[0]
                if head == il or head.endswith("." + il):
                    sc = 3.0
                elif head.endswith("." + last) or head == last:
                    sc = 2.0
                elif il in title:
                    sc = 1.5
                else:
                    sc = 1.0 if field == "source_path" else 0.5
                pid = str(getattr(r, "id", ""))
                if pid not in out or out[pid].score < sc:
                    out[pid] = _LexHit(getattr(r, "id", ""), pl, sc)
            if any(h.score >= 2.0 for h in out.values()):
                break
        # «ФоновыеЗадания.Выполнить» → статья «МенеджерФоновыхЗаданий.Выполнить»: метод по пути,
        # объект — по основам слов в заголовке (морфология, без словаря имён платформы)
        if len(parts) >= 2 and not any(h.score >= 2.0 for h in out.values()):
            stems = [p.lower()[:5] for p in re.findall(r"[A-ZА-ЯЁ][a-zа-яё]+", parts[-2]) if len(p) >= 4]
            try:
                recs, _ = client.scroll(
                    collection_name=col,
                    scroll_filter=qm.Filter(must=[qm.FieldCondition(key="source_path", match=qm.MatchText(text=parts[-1]))]),
                    limit=300,
                    with_payload=True,
                    with_vectors=False,
                )
            except Exception:
                recs = []
            for r in recs:
                pl = dict(getattr(r, "payload", None) or {})
                head = str(pl.get("title") or "").lower().split(" (")[0]
                if not head.endswith("." + parts[-1].lower()) or not stems:
                    continue
                hit = sum(1 for st in stems if st in head)
                if hit == len(stems):
                    pid = str(getattr(r, "id", ""))
                    out[pid] = _LexHit(getattr(r, "id", ""), pl, 2.5)
    ranked = sorted(out.values(), key=lambda h: -h.score * _prio_weight(h.payload))
    return [h for h in ranked if h.score >= 1.5][: max(1, min(limit, 3))]


_DOC_TYPE = "Документация"
_DOC_SLOTS = 2  # мест в выдаче, гарантированных разделам документации (ИТС, стандарты, регламенты)
_DOC_MIN_REL = 0.85  # …если их score не ниже этой доли от лучшего попадания


def _with_docs(client: Any, col: str, vector: list[float], hits: list[Any], lim: int) -> list[Any]:
    """
    Справка платформы — десятки тысяч коротких статей, документации — тысячи разделов: в общем топе статьи
    синтакс-помощника вытесняют инструкции ИТС. Отдельный поиск по документации и до _DOC_SLOTS мест ей,
    если она почти так же близка к вопросу.
    """
    try:
        docs = _vector_search(client, collection_name=col, vector=vector, limit=_DOC_SLOTS * 2, query_filter=qm.Filter(
            must=[qm.FieldCondition(key="content_type", match=qm.MatchValue(value=_DOC_TYPE))]))
    except Exception:
        return hits
    w = lambda h: float(getattr(h, "score", 0) or 0) * _prio_weight(getattr(h, "payload", None))  # noqa: E731
    best = max((w(h) for h in hits[:1] + docs[:1]), default=0.0)
    have = {str(getattr(h, "id", "")) for h in hits[:lim]}
    extra = [d for d in sorted(docs, key=lambda d: -w(d)) if str(getattr(d, "id", "")) not in have
             and w(d) >= best * _DOC_MIN_REL]
    n_in = sum(1 for h in hits[:lim] if (getattr(h, "payload", None) or {}).get("content_type") == _DOC_TYPE)
    extra = extra[: max(0, _DOC_SLOTS - n_in)]
    if not extra:
        return hits
    # документы — на свои места по score, хвост справки сдвигается
    merged = sorted(hits[: lim - len(extra)] + extra, key=lambda h: -w(h))
    return merged + [h for h in hits[lim - len(extra):] if h not in merged]


# вопросительные и служебные слова — не тема вопроса
_DOC_STOP = frozenset("как что почему зачем какой какая какие каким нельзя можно нужно надо рекомендуется "
                      "стандарт стандартом писать сделать работает работают через если чтобы".split())


def _doc_stems(s: str) -> set[str]:
    """Основы слов (первые 5 букв) — грубая морфология: «запросы»/«запросов», «цикле»/«цикл»."""
    return {w[:5] for w in re.findall(r"[а-яёa-z]{4,}", (s or "").lower()) if w not in _DOC_STOP}


def search_docs(query: str, *, limit: int = 3, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Только разделы документации (ИТС, стандарты, регламенты) — агент уточняет по инструкции сам."""
    cfg = cfg if cfg is not None else load_config()
    q = (query or "").strip()
    if not q or not qdrant_enabled(cfg):
        return []
    col = collection_name("help", cfg)
    try:
        client = get_qdrant(cfg)
        hits = _vector_search(client, collection_name=col, vector=Embedder(cfg).embed_query(q), limit=max(30, min(limit, 10) * 4),
                              query_filter=qm.Filter(must=[qm.FieldCondition(key="content_type", match=qm.MatchValue(value=_DOC_TYPE))]))
    except Exception:  # noqa: BLE001 — нет коллекции/документации: просто пусто
        return []
    # вектор путает близкие главы; совпадение основ слов вопроса с заголовком/началом раздела — уточняющий сигнал
    qs = _doc_stems(q)

    def score(h: Any) -> float:
        pl = getattr(h, "payload", None) or {}
        t = len(qs & _doc_stems(str(pl.get("title") or "")))
        b = min(len(qs & _doc_stems(str(pl.get("text") or "")[:1500])), 4)
        return float(getattr(h, "score", 0) or 0) * _prio_weight(pl) * (1 + 0.08 * t + 0.03 * b)

    hits = sorted(hits, key=lambda h: -score(h))
    out = []
    for h in hits[:limit]:
        pl = dict(getattr(h, "payload", None) or {})
        text = pl.get("text") or ""
        out.append({"score": round(float(getattr(h, "score", 0) or 0), 4), "title": pl.get("title") or "",
                    "path": pl.get("source_path") or "", "doc": pl.get("doc_source") or "документация",
                    "text": text[:2000] + ("…" if len(text) > 2000 else "")})
    return out


def search_help_vectors(
    query: str,
    *,
    limit: int = 5,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = cfg if cfg is not None else load_config()
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "Пустой query", "hits": []}
    if not qdrant_enabled(cfg):
        return {"ok": False, "error": "Qdrant выключен", "hits": []}

    col = collection_name("help", cfg)
    try:
        client = get_qdrant(cfg)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "hits": []}

    if _collection_points(client, col) <= 0:
        return {"ok": False, "error": f"Коллекция {col} пуста", "hits": []}

    lim = max(1, min(int(limit or 5), 20))
    # лексика: точные идентификаторы платформы (СтрРазделить, ФоновыеЗадания.Выполнить) — первыми
    lex = _help_lexical(client, col, q, limit=lim)
    emb = Embedder(cfg)
    vector = emb.embed_query(q)
    try:
        # с запасом: после понижения low в выдачу должны войти next-best основные статьи
        hits = _vector_search(
            client, collection_name=col, vector=vector, limit=lim * 2
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "hits": []}
    hits = sorted(
        hits,
        key=lambda h: -float(getattr(h, "score", 0) or 0) * _prio_weight(getattr(h, "payload", None)),
    )
    hits = _with_docs(client, col, vector, hits, lim)
    seen_ids = {str(getattr(h, "id", "")) for h in lex}
    hits = list(lex) + [h for h in hits if str(getattr(h, "id", "")) not in seen_ids]
    hits = hits[: max(lim, len(lex))]

    out = []
    for h in hits:
        pl = dict(getattr(h, "payload", None) or {})
        text = pl.get("text") or ""
        out.append(
            {
                "id": str(getattr(h, "id", "")),
                "score": round(float(getattr(h, "score", 0) or 0), 4),
                "title": pl.get("title") or pl.get("source_path") or "",
                "path": pl.get("source_path") or "",
                "text": text[:2500] + ("…" if len(text) > 2500 else ""),
                "source": "qdrant",
                **({"priority": pl["priority"]} if pl.get("priority") in _PRIORITY_WEIGHT else {}),
                # раздел документации (ИТС/стандарт/регламент), а не статья синтакс-помощника
                **({"doc": pl.get("doc_source") or "документация"} if pl.get("content_type") == _DOC_TYPE else {}),
            }
        )
    return {"ok": True, "source": "qdrant", "collection": col, "hits": out, "count": len(out)}
