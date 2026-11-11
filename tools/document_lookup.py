"""Документ по номеру: resolve этой CF + один ВЫБРАТЬ (не угадывать тип)."""

from __future__ import annotations

import re
from typing import Any

_NUM_RE = re.compile(
    r"(?:№|no\.?|номер[ауе]?)\s*([A-Za-zА-Яа-яЁё0-9][\w\-/.]{0,40})",
    re.I,
)


def extract_document_number(phrase: str) -> str:
    m = _NUM_RE.search(phrase or "")
    num = (m.group(1) if m else "").strip().rstrip(".,;")
    if num and re.search(r"\d", num):
        return num
    # «реализация ТД00-000059» / «документ 000000999» — без слова «номер»
    try:
        from tools.router_detect import extract_doc_number

        return extract_doc_number(phrase or "") or num
    except Exception:
        return num


# HTTP-сервис отклоняет тексты с этими подстроками (H1) — такие имена не кладём в общий запрос
_SERVICE_BLOCKED = ("удалить", "изменить", "выполнить", "поместить", "уничтожить")


def _search_all_documents(backend: Any, num: str, lim: int) -> dict[str, Any]:
    """Номер есть, тип не понятен (или 0 строк) — ищем по всем документам CF пачками ОБЪЕДИНИТЬ ВСЕ."""
    try:
        lst = backend.list_objects("Документы")
    except Exception as e:  # pragma: no cover
        return {"error": str(e)}
    names = [
        str(o.get("full_name") or f"Документ.{o.get('name')}")
        for o in (lst.get("objects") or [])
        if isinstance(o, dict) and (o.get("full_name") or o.get("name"))
    ]
    names = [n for n in names if not any(b in n.lower() for b in _SERVICE_BLOCKED)]
    esc = num.replace('"', '""')
    cond = f'Д.Номер = "{esc}"'
    if re.fullmatch(r"\d+", num):
        # «000000999» / «999» — номер мог храниться с префиксом
        cond = f'Д.Номер ПОДОБНО "%{esc.lstrip("0") or esc}"'
    rows: list[dict] = []
    errors = 0

    def run(chunk: list[str]) -> None:
        nonlocal errors
        if not chunk or len(rows) >= lim:
            return
        parts = [
            f'ВЫБРАТЬ ПЕРВЫЕ {lim} "{fn}" КАК Тип, ПРЕДСТАВЛЕНИЕ(Д.Ссылка) КАК Документ, '
            f"Д.Номер КАК Номер, Д.Дата КАК Дата, Д.Проведен КАК Проведен ИЗ {fn} КАК Д ГДЕ {cond}"
            for fn in chunk
        ]
        data = backend.execute_query("\nОБЪЕДИНИТЬ ВСЕ\n".join(parts), limit=lim)
        if data.get("error"):
            if len(chunk) == 1:
                errors += 1
                return
            mid = len(chunk) // 2
            run(chunk[:mid])
            run(chunk[mid:])
            return
        rows.extend(data.get("rows") or data.get("data") or [])

    for i in range(0, len(names), 40):
        run(names[i : i + 40])
        if len(rows) >= lim:
            break
    return {"rows": rows[:lim], "searched_types": len(names), "skipped_errors": errors}


def document_by_number(
    backend: Any,
    request: str = "",
    *,
    full_name: str = "",
    number: str = "",
    limit: int = 5,
) -> dict[str, Any]:
    num = (number or "").strip() or extract_document_number(request)
    if not num:
        return {
            "ok": False,
            "error": "Не вижу номер. Пример: документ № 00-000012 или number=…",
        }
    fn = (full_name or "").strip()
    if (not fn or not fn.startswith("Документ.")) and backend is not None:
        # тип не задан явно: номер ищем во всех документах, вид выбираем по словам фразы
        # против представления документа (синоним вида из этой CF)
        return _all_docs_result(backend, num, max(int(limit or 5), 20), phrase=request, final_limit=limit)
    if not fn or not fn.startswith("Документ."):
        # класс только Документ — из find_metadata этой базы
        hint = ""
        try:
            from tools.name_match import (
                pascal_candidates_in_text,
                resolve_object_name,
            )

            cands = pascal_candidates_in_text(request or "")
            hint = cands[0] if cands else ""
            if not hint:
                # убрать номер из фразы перед resolve
                cleaned = _NUM_RE.sub(" ", request or "").replace(num, " ")
                hint = resolve_object_name(cleaned) or ""
        except Exception:
            hint = ""
        if backend is not None:
            q = hint or re.sub(
                r"документ\w*|номер\w*|покаж\w*|шапк\w*|№",
                " ",
                request or "",
                flags=re.I,
            )
            q = q.strip() or hint
            q = q.replace(num, " ").strip()
            try:
                meta = backend.find_metadata(q or hint, "Документы", 8) if (q or hint) else {}
                rows = meta.get("results") or meta.get("items") or []
                for r in rows:
                    cand = str(r.get("full_name") or "")
                    if cand.startswith("Документ."):
                        fn = cand
                        break
            except Exception:
                pass
        if not fn.startswith("Документ.") and backend is not None:
            return _all_docs_result(backend, num, limit)
        if not fn.startswith("Документ."):
            return {
                "ok": False,
                "error": "Не понял тип документа. Укажи full_name=Документ.X из find_metadata.",
                "number": num,
            }

    # поля шапки из structure этой CF (не хардкод контрагента)
    fields = ["Д.Номер КАК Номер", "Д.Дата КАК Дата", "Д.Проведен КАК Проведен"]
    extra: list[str] = []
    if backend is not None:
        try:
            st = backend.get_object_structure(fn)
            attrs = st.get("attributes") or st.get("реквизиты") or []
            names = []
            for a in attrs:
                if isinstance(a, dict):
                    names.append(str(a.get("name") or a.get("Name") or ""))
                elif a:
                    names.append(str(a))
            for n in names:
                if n.lower() in ("контрагент", "организация", "партнер", "склад"):
                    extra.append(f"ПРЕДСТАВЛЕНИЕ(Д.{n}) КАК {n}")
                if len(extra) >= 4:
                    break
        except Exception:
            pass
    sel = ",\n  ".join(
        ["ПРЕДСТАВЛЕНИЕ(Д.Ссылка) КАК Документ"] + fields + extra
    )
    esc = num.replace('"', '""')
    lim = max(1, min(int(limit or 5), 20))
    query = (
        f"ВЫБРАТЬ ПЕРВЫЕ {lim}\n  {sel}\n"
        f"ИЗ {fn} КАК Д\n"
        f'ГДЕ Д.Номер = "{esc}"'
    )
    result: dict[str, Any] = {
        "ok": True,
        "full_name": fn,
        "number": num,
        "query": query,
        "prefer": "Покажи rows. ТЧ — get_object_structure + отдельный ВЫБРАТЬ по Ссылка.",
    }
    if backend is None:
        result["note"] = "backend нет — query не выполнен."
        return result
    try:
        data = backend.execute_query(query, limit=lim)
        result["rows"] = data.get("rows") or data.get("data") or []
        if data.get("error"):
            result["ok"] = False
            result["error"] = data.get("error")
            result["detail"] = data.get("detail")
        else:
            result["ok"] = True
            result["row_count"] = len(result["rows"])
            if not result["rows"] and not (full_name or "").strip():
                # тип угадан по фразе, но с таким номером его нет — ищем во всех документах
                alt = _all_docs_result(backend, num, limit)
                if alt.get("rows"):
                    alt["note"] = f"В {fn} номера {num} нет; найдено в других документах."
                    return alt
            if not result["rows"]:
                result["note"] = "0 строк: другой тип документа, другой номер или нет прав."
    except Exception as e:
        result["ok"] = False
        result["error"] = str(e)
    return result


def _stems(text: str) -> set[str]:
    words = re.findall(r"[а-яёa-z]{3,}", (text or "").lower().replace("ё", "е"))
    stop = {"покажи", "открой", "найди", "документ", "документа", "документе", "номер", "номером", "что", "какие",
            "сумма", "кто", "провел", "товары", "это", "мне", "дай", "выведи", "по"}
    return {w[:5] for w in words if w not in stop}


def _rank_by_phrase(rows: list[dict], phrase: str) -> list[dict]:
    want = _stems(re.sub(r"[\w-]*\d[\w-]*", " ", phrase or ""))
    if not want:
        return rows
    def score(r: dict) -> int:
        rep = str(r.get("Документ") or "")
        rep = re.split(r"\s[\w-]*\d", rep)[0]  # «<Вид документа> АБ-000007 от …» → «<Вид документа>»
        return len(want & _stems(rep))
    scored = sorted(rows, key=score, reverse=True)
    for r in scored:
        r["_score"] = score(r)
    return scored


def _all_docs_result(backend: Any, num: str, limit: int, *, phrase: str = "", final_limit: int = 5) -> dict[str, Any]:
    lim = max(1, min(int(limit or 5), 20))
    found = _search_all_documents(backend, num, lim)
    rows = _rank_by_phrase(found.get("rows") or [], phrase)
    if rows and rows[0].get("_score", 0) > 0:
        top = rows[0]["_score"]
        best = [r for r in rows if r.get("_score") == top]
        if len({r.get("Тип") for r in best}) == 1:
            rows = best
    rows = rows[: max(1, int(final_limit or 5))]
    for r in rows:
        r.pop("_score", None)
    out: dict[str, Any] = {
        "ok": True,
        "number": num,
        "rows": rows,
        "row_count": len(rows),
        "searched_types": found.get("searched_types"),
        "mode": "all_documents",
    }
    if found.get("error"):
        out.update(ok=False, error=found["error"])
    elif not rows:
        out["note"] = f"Документ с номером {num} не найден ни в одном из {found.get('searched_types')} видов документов."
    elif len({r.get("Тип") for r in rows}) > 1:
        out["need_clarification"] = True
        out["question"] = f"Номер {num} есть у нескольких документов — какой нужен?"
        out["options"] = [r.get("Документ") for r in rows][:5]
    else:
        out["full_name"] = rows[0].get("Тип")
    return out
