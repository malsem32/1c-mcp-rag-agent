# -*- coding: utf-8 -*-
"""
Аудит текстов запросов 1С / BSL: антипаттерны + «как лучше».

Каталог: knowledge/антипаттерны_запросов.yaml (без hardcode CF).
"""

from __future__ import annotations

import re
from typing import Any

# --- intent ---

_AUDIT_MARKERS = (
    "антипаттерн",
    "анти паттерн",
    "аудит запрос",
    "аудит код",
    "code review",
    "code review запрос",
    "ревью запрос",
    "плохой запрос",
    "плохие запрос",
    "тяжёл запрос",
    "тяжел запрос",
    "оптимиз запрос",
    "оптимизация запрос",
    "как лучше писать запрос",
    "как писать запрос",
    "best practice запрос",
    "query antipattern",
    "n+1",
    "запрос в цикл",
    "запросы в цикл",
)


def is_query_audit_question(phrase: str) -> bool:
    pl = (phrase or "").lower().replace("ё", "е")
    if not pl:
        return False
    if any(m in pl for m in _AUDIT_MARKERS):
        return True
    # «проверь … на антипаттерны» / «проверь документ … на …»
    if re.search(r"(провер|разбер|проанализ|найди|скан).{0,60}антипаттерн", pl):
        return True
    # «проверь/разбери запросы» + код/модуль/документ
    if re.search(r"(проверь|разбер|проанализ|найди).{0,40}запрос", pl):
        return True
    if "запрос" in pl and any(
        x in pl for x in ("аудит", "антипат", "оптимиз", "тормоз", "slow", "review")
    ):
        return True
    return False


def _load_book() -> dict[str, Any]:
    try:
        from kb.store import get_query_antipatterns

        return get_query_antipatterns()
    except Exception:
        return {"patterns": [], "how_better": [], "response_rules": [], "source": ""}


def list_antipatterns_catalog() -> dict[str, Any]:
    """Справочник для query_syntax_help / справки (без скана кода)."""
    book = _load_book()
    items = []
    for p in book.get("patterns") or []:
        if not isinstance(p, dict):
            continue
        items.append(
            {
                "id": p.get("id"),
                "severity": p.get("severity"),
                "title": p.get("title"),
                "bad": p.get("bad"),
                "better": p.get("better"),
                "why": p.get("why"),
                "recommendation": p.get("recommendation"),
                "match_scope": p.get("match_scope") or "query",
            }
        )
    return {
        "ok": True,
        "source": book.get("source"),
        "how_better": book.get("how_better") or [],
        "response_rules": book.get("response_rules") or [],
        "antipatterns": items,
        "count": len(items),
        "prefer": (
            "При аудите кода: audit_query_code(request=фраза) или "
            "audit_query_code(query_text=…). "
            "Ответ: findings + рекомендация «НЕ применено». Имена таблиц — из metadata."
        ),
    }


def extract_query_fragments(bsl_or_query: str, *, max_frags: int = 25) -> list[dict[str, Any]]:
    """
    Достаёт фрагменты ВЫБРАТЬ / SELECT из BSL (кавычки, |) или целый текст запроса.
    """
    text = bsl_or_query or ""
    if not text.strip():
        return []
    frags: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(body: str, line: int, kind: str) -> None:
        body = (body or "").strip()
        if len(body) < 12:
            return
        key = re.sub(r"\s+", " ", body.lower())[:240]
        if key in seen:
            return
        seen.add(key)
        frags.append({"text": body[:8000], "line": line, "kind": kind})

    # цельный запрос без BSL
    if re.search(r"(?i)\bВЫБРАТЬ\b|\bSELECT\b", text) and "Новый Запрос" not in text:
        if text.count("\n") < 3 or not re.search(
            r"(?i)^\s*(Процедура|Функция|КонецПроцедуры|КонецФункции)\b", text, re.M
        ):
            _add(text, 1, "query_text")
            if len(frags) >= max_frags:
                return frags[:max_frags]

    # "ВЫБРАТЬ …" / 'ВЫБРАТЬ …'
    for m in re.finditer(
        r'["\'](\s*(?:ВЫБРАТЬ|SELECT)\b[^"\']{10,})["\']',
        text,
        re.I | re.S,
    ):
        line = text.count("\n", 0, m.start()) + 1
        _add(m.group(1), line, "quoted")
        if len(frags) >= max_frags:
            return frags[:max_frags]

    # |ВЫБРАТЬ … (многострочный литерал 1С)
    for m in re.finditer(
        r"(?im)^[ \t]*\|[ \t]*(.*(?:ВЫБРАТЬ|SELECT).*)",
        text,
    ):
        # собрать подряд идущие | строки
        start = m.start()
        line = text.count("\n", 0, start) + 1
        lines_out = [m.group(1)]
        pos = m.end()
        while pos < len(text):
            nm = re.match(r"[ \t]*\|(.*)\r?\n?", text[pos:])
            if not nm:
                break
            lines_out.append(nm.group(1))
            pos += nm.end()
            if text[pos - 1 : pos] != "\n" and pos < len(text) and text[pos] == "\n":
                pos += 1
        body = "\n".join(lines_out)
        if re.search(r"(?i)\bВЫБРАТЬ\b|\bSELECT\b", body):
            _add(body, line, "pipe")
        if len(frags) >= max_frags:
            return frags[:max_frags]

    return frags[:max_frags]


def _match_pattern(pat: dict[str, Any], text: str, *, scope: str) -> bool:
    """scope: query | bsl."""
    ms = (pat.get("match_scope") or "query").lower()
    if ms == "query" and scope != "query":
        # bsl-only patterns handled separately
        if not pat.get("detect_code"):
            # still allow regex on full module for both
            if ms != "both":
                return False
    if ms == "bsl" and scope == "query":
        return False

    detect_code = (pat.get("detect_code") or "").strip()
    if detect_code and scope == "query":
        return False

    text = text or ""
    flags = re.I | re.M | re.S

    detect = pat.get("detect") or []
    if isinstance(detect, str):
        detect = [detect]
    detect_all = pat.get("detect_all") or []
    detect_none = pat.get("detect_none") or []

    if detect_code:
        return False  # handled in bsl-specific path

    if detect_all:
        for rx in detect_all:
            try:
                if not re.search(rx, text, flags):
                    return False
            except re.error:
                return False
        for rx in detect_none or []:
            try:
                if re.search(rx, text, flags):
                    return False
            except re.error:
                pass
        # if only detect_all — ok; if also detect — need one of detect
        if detect:
            for rx in detect:
                try:
                    if re.search(rx, text, flags):
                        return True
                except re.error:
                    continue
            return False
        return True

    for rx in detect:
        try:
            if re.search(rx, text, flags):
                # detect_none
                ok = True
                for nx in detect_none or []:
                    try:
                        if re.search(nx, text, flags):
                            ok = False
                            break
                    except re.error:
                        pass
                if ok:
                    return True
        except re.error:
            continue
    return False


def _finding(
    pat: dict[str, Any],
    *,
    path: str = "",
    line: int = 0,
    snippet: str = "",
    extra: str = "",
) -> dict[str, Any]:
    title = str(pat.get("title") or pat.get("id") or "")
    if extra:
        title = f"{title} ({extra})" if title else extra
    return {
        "id": pat.get("id"),
        "severity": pat.get("severity") or "medium",
        "title": title,
        "path": path or None,
        "line": line or None,
        "snippet": (snippet or "")[:280],
        "bad": pat.get("bad"),
        "better": pat.get("better"),
        "why": pat.get("why"),
        "recommendation": pat.get("recommendation"),
    }


def scan_query_text(
    query: str,
    *,
    path: str = "",
    line: int = 0,
    max_findings: int = 30,
) -> list[dict[str, Any]]:
    """Скан одного текста ВЫБРАТЬ."""
    book = _load_book()
    out: list[dict[str, Any]] = []
    q = query or ""
    if not q.strip():
        return []
    for pat in book.get("patterns") or []:
        if not isinstance(pat, dict):
            continue
        if pat.get("detect_code"):
            continue
        ms = (pat.get("match_scope") or "query").lower()
        if ms == "bsl":
            continue
        if _match_pattern(pat, q, scope="query"):
            # snippet: first matching line-ish
            snip = re.sub(r"\s+", " ", q.strip())[:220]
            out.append(_finding(pat, path=path, line=line, snippet=snip))
        if len(out) >= max_findings:
            break
    return out[:max_findings]


def _scan_query_in_loop(bsl: str, path: str) -> list[dict[str, Any]]:
    """Эвристика: Новый Запрос / Запрос.Выполнить в окне цикла."""
    lines = (bsl or "").splitlines()
    out: list[dict[str, Any]] = []
    loop_depth = 0
    for i, ln in enumerate(lines):
        raw = ln.strip()
        if re.search(r"(?i)^\s*//", raw):
            continue
        if re.search(r"(?i)\b(Для\s+Каждого|Для\s+\w+\s*=|Пока\s+)", raw):
            loop_depth += 1
        if re.search(r"(?i)\bКонецЦикла\b", raw):
            loop_depth = max(0, loop_depth - 1)
        if loop_depth <= 0:
            continue
        if re.search(
            r"(?i)Новый\s+Запрос|Запрос\s*\.\s*Выполнить\s*\(|"
            r"Query\s*\.\s*Execute\s*\(|\.Выполнить\s*\(\s*\)",
            raw,
        ):
            # не Запрос.Выполнить внутри комментария
            out.append(
                {
                    "line": i + 1,
                    "snippet": raw[:220],
                }
            )
    return out


def scan_bsl_module(
    bsl: str,
    *,
    path: str = "",
    max_findings: int = 40,
) -> list[dict[str, Any]]:
    """Скан модуля BSL: фрагменты запросов + bsl-only антипаттерны."""
    book = _load_book()
    patterns = [p for p in (book.get("patterns") or []) if isinstance(p, dict)]
    findings: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _push(f: dict[str, Any]) -> None:
        k = f"{f.get('id')}:{f.get('path')}:{f.get('line')}:{f.get('snippet', '')[:80]}"
        if k in seen:
            return
        seen.add(k)
        findings.append(f)

    # query fragments
    for frag in extract_query_fragments(bsl, max_frags=20):
        for f in scan_query_text(
            frag["text"],
            path=path,
            line=int(frag.get("line") or 0),
            max_findings=15,
        ):
            _push(f)
            if len(findings) >= max_findings:
                return findings[:max_findings]

    # bsl regex patterns + detect_code
    for pat in patterns:
        code = (pat.get("detect_code") or "").strip()
        ms = (pat.get("match_scope") or "query").lower()
        if code == "query_in_loop":
            for hit in _scan_query_in_loop(bsl, path):
                _push(
                    _finding(
                        pat,
                        path=path,
                        line=int(hit.get("line") or 0),
                        snippet=str(hit.get("snippet") or ""),
                    )
                )
            continue
        if code == "literal_query_hint":
            # только если есть Запрос.Текст = "…" и мало УстановитьПараметр
            if re.search(r"(?i)Запрос\s*\.\s*Текст\s*=", bsl) and bsl.count(
                "УстановитьПараметр"
            ) < bsl.lower().count("запрос.текст"):
                m = re.search(r"(?i)Запрос\s*\.\s*Текст\s*=", bsl)
                line = bsl.count("\n", 0, m.start()) + 1 if m else 0
                _push(
                    _finding(
                        pat,
                        path=path,
                        line=line,
                        snippet="Запрос.Текст = … (мало УстановитьПараметр)",
                        extra="hint",
                    )
                )
            continue
        if ms in ("bsl", "both"):
            if _match_pattern(pat, bsl, scope="bsl") or (
                ms == "both" and any(re.search(rx, bsl, re.I | re.M) for rx in (pat.get("detect") or []) if isinstance(rx, str))
            ):
                # first line of match
                line = 0
                snip = ""
                for rx in pat.get("detect") or []:
                    try:
                        m = re.search(rx, bsl, re.I | re.M | re.S)
                    except re.error:
                        m = None
                    if m:
                        line = bsl.count("\n", 0, m.start()) + 1
                        snip = bsl[m.start() : m.start() + 200].replace("\n", " ")
                        break
                _push(_finding(pat, path=path, line=line, snippet=snip))
        if len(findings) >= max_findings:
            break

    # severity sort
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(
        key=lambda x: (
            order.get(str(x.get("severity") or ""), 9),
            str(x.get("path") or ""),
            int(x.get("line") or 0),
        )
    )
    return findings[:max_findings]


def audit_query_code(
    *,
    request: str = "",
    query_text: str = "",
    path: str = "",
    max_findings: int = 40,
    max_modules: int = 8,
) -> dict[str, Any]:
    """
    Аудит запросов:
    - query_text: скан готового ВЫБРАТЬ
    - path / request: модули из Qdrant (как inspect_code)
    """
    max_findings = max(5, min(int(max_findings or 40), 80))
    max_modules = max(1, min(int(max_modules or 8), 15))
    book = _load_book()
    findings: list[dict[str, Any]] = []
    modules_scanned: list[str] = []

    qt = (query_text or "").strip()
    if qt:
        findings.extend(scan_query_text(qt, path=path or "", max_findings=max_findings))
        return _pack_result(
            findings,
            book=book,
            modules_scanned=modules_scanned,
            request=request,
            mode="query_text",
        )

    # modules from path / request
    paths: list[str] = []
    if path:
        paths.append(path.replace("\\", "/").lstrip("/"))

    req = (request or "").strip()
    if req and not paths:
        try:
            from tools.code_search import inspect_code

            insp = inspect_code(request=req, max_hits=8)
            if insp.get("ok"):
                for qf in insp.get("query_functions") or []:
                    p = str(qf.get("path") or "")
                    if p and p not in paths:
                        paths.append(p)
                for rh in insp.get("risk_hits") or []:
                    p = str(rh.get("path") or "")
                    if p and p not in paths:
                        paths.append(p)
                mod = (insp.get("module") or {}).get("path") or ""
                if mod and mod not in paths:
                    paths.insert(0, str(mod))
                for m in insp.get("related_modules") or []:
                    p = str(m.get("path") or "")
                    if p and p not in paths:
                        paths.append(p)
                for c in insp.get("candidates") or []:
                    p = str(c.get("path") or "")
                    if p and p.endswith(".bsl") and p not in paths:
                        paths.append(p)
        except Exception as e:
            return {
                "ok": False,
                "error": f"inspect_code failed: {e}",
                "hint": "Передай path=… или query_text=ВЫБРАТЬ…",
            }

    if not paths:
        # только справочник
        cat = list_antipatterns_catalog()
        cat["ok"] = True
        cat["findings"] = []
        cat["hint"] = (
            "Укажи request=фраза (объект/модуль), path=…/Module.bsl "
            "или query_text=текст ВЫБРАТЬ для скана."
        )
        cat["mode"] = "catalog_only"
        return cat

    try:
        from vector.catalog import assemble_module_text, get_chunks_by_source_path
    except Exception as e:
        return {"ok": False, "error": f"Qdrant catalog unavailable: {e}"}

    for pth in paths[:max_modules]:
        ch = get_chunks_by_source_path(pth)
        if not ch:
            continue
        txt = assemble_module_text(ch)
        modules_scanned.append(pth)
        for f in scan_bsl_module(txt, path=pth, max_findings=max_findings):
            findings.append(f)
            if len(findings) >= max_findings * 2:
                break

    # de-dupe
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for f in findings:
        k = f"{f.get('id')}:{f.get('path')}:{f.get('line')}"
        if k in seen:
            continue
        seen.add(k)
        uniq.append(f)
    order = {"high": 0, "medium": 1, "low": 2}
    uniq.sort(key=lambda x: (order.get(str(x.get("severity") or ""), 9), int(x.get("line") or 0)))
    uniq = uniq[:max_findings]

    return _pack_result(
        uniq,
        book=book,
        modules_scanned=modules_scanned,
        request=req,
        mode="modules",
    )


def _pack_result(
    findings: list[dict[str, Any]],
    *,
    book: dict[str, Any],
    modules_scanned: list[str],
    request: str,
    mode: str,
) -> dict[str, Any]:
    high = sum(1 for f in findings if f.get("severity") == "high")
    med = sum(1 for f in findings if f.get("severity") == "medium")
    low = sum(1 for f in findings if f.get("severity") == "low")
    return {
        "ok": True,
        "mode": mode,
        "request": request or None,
        "modules_scanned": modules_scanned,
        "findings": findings,
        "findings_count": len(findings),
        "severity_counts": {"high": high, "medium": med, "low": low},
        "how_better": book.get("how_better") or [],
        "response_rules": book.get("response_rules") or [],
        "source": book.get("source"),
        "prefer": (
            "1) Перечисли findings (severity, id, path, line, title). "
            "2) Для high/medium — snippet + recommendation. "
            "3) Пример правки — только «Рекомендация (НЕ применено, CF не менялась)». "
            "4) full_name в примерах — плейсхолдеры или из metadata этой CF. "
            "5) Не утверждай, что код уже исправлен."
        ),
        "catalog_hint": "Полный список: query_syntax_help(topic='antipatterns')",
    }
