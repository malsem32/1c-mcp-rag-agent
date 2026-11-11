# -*- coding: utf-8 -*-
"""
Каталог объектов ТЕКУЩЕЙ CF: full_name + класс + синоним + представления (+ движения документов).

Источник — выгрузка конфигурации (paths.code_dir), строится один раз в sidecar
knowledge/qdrant_ready/object_catalog.jsonl (рядом с object_names.jsonl). Нет выгрузки —
fallback на object_names.jsonl (только имена + папка класса).

Резолвер resolve_full_name(): фраза → top-k full_name с баллами:
  1) точное CamelCase-имя из фразы (самый сильный сигнал);
  2) класс из речи («регистр сведений», «отчёт») — ФИЛЬТР, слова класса убираются из токенов;
  3) совпадение стемов фразы с частями имени, синонимом и представлениями;
  4) расширение запроса словарём бизнеса (покупатель ≈ клиент) — только как слабые токены.
Никаких имён объектов в коде: всё из выгрузки этой базы.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from ingest.skip_names import is_skipped_name
from tools.meta_class import detect_meta_class, strip_class_words

# папка выгрузки → префикс full_name
_FOLDER_PREFIX: dict[str, str] = {
    "Documents": "Документ",
    "Catalogs": "Справочник",
    "AccumulationRegisters": "РегистрНакопления",
    "InformationRegisters": "РегистрСведений",
    "AccountingRegisters": "РегистрБухгалтерии",
    "CalculationRegisters": "РегистрРасчета",
    "Reports": "Отчет",
    "DataProcessors": "Обработка",
    "Enums": "Перечисление",
    "ChartsOfCharacteristicTypes": "ПланВидовХарактеристик",
    "ChartsOfAccounts": "ПланСчетов",
    "ChartsOfCalculationTypes": "ПланВидовРасчета",
    "BusinessProcesses": "БизнесПроцесс",
    "Tasks": "Задача",
    "ExchangePlans": "ПланОбмена",
    "DocumentJournals": "ЖурналДокументов",
    "Constants": "Константа",
    "CommonModules": "ОбщийМодуль",
    "Subsystems": "Подсистема",
    "ScheduledJobs": "РегламентноеЗадание",
    "EventSubscriptions": "ПодпискаНаСобытие",
    "CommonForms": "ОбщаяФорма",
    "CommonAttributes": "ОбщийРеквизит",
    "DefinedTypes": "ОпределяемыйТип",
    "FunctionalOptions": "ФункциональнаяОпция",
    "HTTPServices": "HTTPСервис",
    "WebServices": "WebСервис",
    "Roles": "Роль",
}

# при отсутствии класса в речи — лёгкое предпочтение «прикладных» классов
_CLASS_PRIOR: dict[str, float] = {
    "Документ": 1.0,
    "Справочник": 0.9,
    "РегистрНакопления": 0.7,
    "РегистрСведений": 0.6,
    "Отчет": 0.6,
    "Обработка": 0.4,
    "Перечисление": 0.4,
    "ПланВидовХарактеристик": 0.3,
    "Константа": 0.2,
    "ОбщийМодуль": 0.2,
    "Подсистема": 0.2,
}
# эти классы выбираются только при явном слове класса
_ONLY_EXPLICIT = {"Роль", "ОбщийРеквизит", "ОпределяемыйТип", "ФункциональнаяОпция", "ПодпискаНаСобытие", "ОбщаяФорма"}

_STOP = frozenset(
    "где как какой какая какие каким каком какого что чем это этот эта для при без или и на по из в с со за от до "
    "все весь мне нам нас наш наши у о об про покажи найди найти показывает показать оформляется оформляют оформить "
    "оформлять хранится хранятся хранить храним завести заводить создать вводится вводятся ввести делать делается "
    "называется смотреть посмотреть есть ли программе программа 1с базе база системе конфигурации нужен нужна нужно "
    "который которая которые связаны связанные связан работы работа объект объекта объекты опиши описание структура "
    "реквизиты реквизит поля поле состав значения какие бывают таблица табличная часть формы форма макеты макет "
    "документ документа документом документе документы справочник справочника справочнике регистр регистра отчет "
    "отчета отчете обработка обработки перечисление константа подсистема новый новую новое нового сколько список "
    "пожалуйста можно хочу если когда между через также".split()
)


def _low(s: str) -> str:
    return (s or "").lower().replace("ё", "е")


def _camel_parts(name: str) -> list[str]:
    return [p for p in re.findall(r"[A-ZА-ЯЁ][a-zа-яё0-9_]*|[A-ZА-ЯЁ]+(?![a-zа-яё])|[a-zа-яё0-9]+", name or "") if p]


try:
    import snowballstemmer as _sb

    _RU = _sb.stemmer("russian")
except Exception:  # pragma: no cover — без пакета: грубый префикс
    _RU = None


@lru_cache(maxsize=50_000)
def _stem(w: str) -> str:
    w = _low(w)
    if _RU is not None:
        return _RU.stemWord(w)
    return w[:5] if len(w) > 5 else w


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-zа-яё0-9]{3,}", _low(text)) if w not in _STOP]


# ---------------- построение sidecar ----------------


def catalog_path() -> Path:
    from tools.name_match import object_names_jsonl_path

    return object_names_jsonl_path().with_name("object_catalog.jsonl")


def _dump_dir() -> Path | None:
    try:
        from tools.code_search import resolve_code_roots
        from vector.catalog import runtime_fs_allowed

        if not runtime_fs_allowed():  # runtime: только зеркало коллекции файлов Qdrant, не диск
            roots = resolve_code_roots().roots
            return roots[0] if roots else None
    except Exception:
        pass
    try:
        from config import load_config

        cfg = load_config()
        raw = cfg.get("code_dir") or (cfg.get("paths") or {}).get("code_dir") or ""
        p = Path(raw)
        if raw and p.is_dir():
            return p
    except Exception:
        pass
    return None


def _xml_prop(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\s*<v8:item>\s*<v8:lang>ru</v8:lang>\s*<v8:content>(.*?)</v8:content>", text, re.S)
    if m:
        return m.group(1).strip()
    m = re.search(rf"<{tag}>([^<]*)</{tag}>", text)
    return (m.group(1).strip() if m else "")


def build_catalog_from_dump(dump: Path, out: Path | None = None) -> Path:
    """Пройти <Folder>/<Name>.xml выгрузки и записать каталог (1 строка = 1 объект)."""
    out = out or catalog_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w", encoding="utf-8") as f:
        for folder, prefix in _FOLDER_PREFIX.items():
            d = dump / folder
            if not d.is_dir():
                continue
            for xml in sorted(d.glob("*.xml")):
                try:
                    text = xml.read_text(encoding="utf-8-sig", errors="replace")[:200_000]
                except OSError:
                    continue
                props = text.split("<ChildObjects>", 1)[0]
                name = _xml_prop(props, "Name") or xml.stem
                if is_skipped_name(name):
                    continue
                rec: dict[str, Any] = {
                    "full_name": f"{prefix}.{name}",
                    "name": name,
                    "class": prefix,
                    "folder": folder,
                    "synonym": _xml_prop(props, "Synonym"),
                }
                for tag, key in (
                    ("ListPresentation", "list_presentation"),
                    ("ObjectPresentation", "object_presentation"),
                    ("ExtendedListPresentation", "ext_presentation"),
                    ("Explanation", "explanation"),
                    ("Comment", "comment"),
                ):
                    v = _xml_prop(props, tag)
                    if v:
                        rec[key] = v[:300]
                if folder == "Documents":
                    regs = re.findall(r"<xr:Item xsi:type=\"xr:MDObjectRef\">([^<]+)</xr:Item>", props.split("<RegisterRecords>", 1)[-1].split("</RegisterRecords>", 1)[0]) if "<RegisterRecords>" in props else []
                    if regs:
                        rec["register_records"] = regs
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
    load_catalog.cache_clear()
    return out


@lru_cache(maxsize=1)
def load_catalog() -> tuple[dict[str, Any], ...]:
    path = catalog_path()
    if not path.is_file():
        dump = _dump_dir()
        if dump is not None:
            try:
                build_catalog_from_dump(dump, path)
            except Exception:
                pass
    recs: list[dict[str, Any]] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    if recs:
        return tuple(_prep(r) for r in recs)
    # fallback: только имена + папка
    try:
        from tools.name_match import object_names_jsonl_path

        side = object_names_jsonl_path()
        for line in side.read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(line)
            except Exception:
                continue
            folder = o.get("dump_folder") or ""
            prefix = _FOLDER_PREFIX.get(folder, "")
            name = o.get("object_name") or ""
            if name:
                recs.append({"full_name": f"{prefix}.{name}" if prefix else name, "name": name, "class": prefix, "folder": folder, "synonym": ""})
    except Exception:
        pass
    return tuple(_prep(r) for r in recs)


def _prep(r: dict[str, Any]) -> dict[str, Any]:
    name_parts = [_low(p) for p in _camel_parts(r.get("name") or "") if len(p) >= 3]
    syn_words = [w for w in _words(r.get("synonym") or "")]
    extra = " ".join(str(r.get(k) or "") for k in ("list_presentation", "object_presentation", "ext_presentation"))
    r["_name_l"] = _low(r.get("name") or "")
    # варианты «слов объекта»: части имени и слова синонима (порядок важен — первое слово = главное)
    r["_name_seq"] = [_stem(p) for p in name_parts]
    r["_syn_seq"] = [_stem(w) for w in syn_words]
    r["_extra_stems"] = {_stem(w) for w in _words(extra)}
    return r


def catalog_by_full_name() -> dict[str, dict[str, Any]]:
    return {r["full_name"]: r for r in load_catalog()}


def documents_writing_register(register_full_name: str) -> list[str]:
    """Обратный индекс: какие документы делают движения по регистру (из RegisterRecords выгрузки)."""
    want = (register_full_name or "").strip()
    leaf = want.split(".")[-1]
    out = []
    for r in load_catalog():
        regs = r.get("register_records") or []
        if any(x == want or x.split(".")[-1] == leaf for x in regs):
            out.append(r["full_name"])
    return out


# ---------------- синонимы бизнеса (расширение запроса) ----------------


@lru_cache(maxsize=1)
def _business_groups() -> tuple[frozenset[str], ...]:
    try:
        import yaml

        from config import load_config

        kd = Path((load_config().get("paths") or {}).get("knowledge_dir") or "knowledge")
        data = yaml.safe_load((kd / "словарь_бизнеса.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        return tuple()
    groups: list[frozenset[str]] = []
    for sect in data.values():
        if not isinstance(sect, dict):
            continue
        for vals in sect.values():
            if isinstance(vals, list):
                st = frozenset(_stem(str(v).split()[0]) for v in vals if v)
                # «контрагент/партнер» есть почти везде — не расширяем ими (шум)
                if 1 < len(st) <= 8:
                    groups.append(st)
    return tuple(groups)


def _expand(stems: list[str]) -> dict[str, float]:
    """stem → вес (1.0 — из фразы, 0.6 — синоним из словаря бизнеса)."""
    out = {s: 1.0 for s in stems}
    for s in stems:
        for g in _business_groups():
            if s in g:
                for o in g:
                    out.setdefault(o, 0.6)
    return out


# ---------------- резолвер ----------------


def _phrase_stems(phrase: str) -> list[str]:
    s = re.sub(r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_]*[a-zа-яё][A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_]*", " ", phrase or "")  # CamelCase отдельно
    s = strip_class_words(s)
    out: list[str] = []
    for w in _words(s):
        st = _stem(w)
        if st not in out:
            out.append(st)
    return out


def _stem_eq(a: str, b: str) -> bool:
    if a == b:
        return True
    if len(a) >= 5 and len(b) >= 5:
        return a.startswith(b) or b.startswith(a)
    return False


def _score_seq(weights: dict[str, float], stems: list[str], seq: list[str]) -> tuple[float, int, bool]:
    """(сумма весов совпавших стемов фразы, сколько слов объекта покрыто, совпало ли главное слово)."""
    if not seq:
        return 0.0, 0, False
    got = 0.0
    for st in stems:
        best = 0.0
        for cand, w in weights.items():
            if w < 1.0 and cand == st:
                continue
            if (cand == st or w < 1.0) and any(_stem_eq(cand, x) for x in seq):
                # синоним учитываем только для «своего» стема фразы
                if cand == st or cand in _group_of(st):
                    best = max(best, w)
        got += best
    covered = sum(1 for x in seq if any(_stem_eq(c, x) for c in weights))
    head = any(_stem_eq(c, seq[0]) for c in weights)
    return got, covered, head


def _group_of(st: str) -> frozenset[str]:
    out: set[str] = set()
    for g in _business_groups():
        if st in g:
            out |= g
    return frozenset(out)


_WEIGHT_CACHE: dict[str, int] = {}


def _code_weight(name: str) -> int:
    """Сколько чанков кода/метаданных у объекта в Qdrant (payload.object_name). -1 — не удалось."""
    if name in _WEIGHT_CACHE:
        return _WEIGHT_CACHE[name]
    w = -1
    try:
        from qdrant_client.http import models as qm

        from vector.catalog import collection_name, get_qdrant

        res = get_qdrant().count(
            collection_name("code"),
            count_filter=qm.Filter(must=[qm.FieldCondition(key="object_name", match=qm.MatchValue(value=name))]),
            exact=True,
        )
        w = int(getattr(res, "count", 0) or 0)
    except Exception:
        w = -1
    _WEIGHT_CACHE[name] = w
    return w


def _tiebreak_by_code_weight(scored: list[tuple[float, dict[str, Any]]], *, window: float = 4.0) -> list[tuple[float, dict[str, Any]]]:
    """
    Почти равные по тексту кандидаты (разница ≤ window) — поднять «основной» объект:
    у него на порядок больше кода/форм/макетов в индексе (любая CF, без имён).
    """
    if len(scored) < 2 or scored[0][0] - scored[1][0] > window:
        return scored
    top = scored[0][0]
    tied = [x for x in scored[:6] if top - x[0] <= window]
    weights = {id(r): _code_weight(str(r.get("name") or "")) for _s, r in tied}
    if all(w <= 0 for w in weights.values()):
        return scored
    best = max(tied, key=lambda x: (weights[id(x[1])], x[0]))
    if best is scored[0]:
        return scored
    w_best, w_first = weights[id(best[1])], weights[id(scored[0][1])]
    if w_best < 2 * max(w_first, 1):
        return scored
    rest = [x for x in scored if x is not best]
    return [(top + 1.0, best[1])] + rest


def resolve_full_name(
    phrase: str,
    *,
    class_prefix: str = "",
    top_k: int = 5,
) -> dict[str, Any]:
    """
    Фраза → {"full_name", "candidates": [{"full_name","synonym","score"}], "class", "confident"}.
    confident=False при малом отрыве top1/top2 — вызывающий должен уточнить у пользователя.
    """
    cat = load_catalog()
    if not cat:
        return {"full_name": "", "candidates": [], "confident": False}
    mtype, prefix = detect_meta_class(phrase) if not class_prefix else ("", class_prefix)

    def class_ok(r: dict[str, Any]) -> bool:
        if prefix:
            if prefix == "Регистр":
                return str(r.get("class") or "").startswith("Регистр")
            return r.get("class") == prefix
        return r.get("class") not in _ONLY_EXPLICIT

    # 1) точное CamelCase/Title-имя из фразы; класс — по слову ПРЯМО перед именем
    #    («какие документы делают движения по регистру ТоварыНаСкладах» → регистр, не документ)
    exact: list[dict[str, Any]] = []
    for m in re.finditer(r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_]{3,}", phrase or ""):
        nl = _low(m.group(0))
        same = [r for r in cat if r["_name_l"] == nl]
        if not same:
            continue
        _t, near = detect_meta_class((phrase or "")[max(0, m.start() - 40): m.start()].split(".")[-1])
        # одно слово с заглавной («Склад», «Подбор», «Заполнить») — часто имя поля/кнопки, а не объекта:
        # принимаем только если прямо перед ним слово класса («справочника Номенклатура»)
        if not re.search(r"[a-zа-яё][A-ZА-ЯЁ]", m.group(0)) and not near and not class_prefix:
            continue
        if near and not class_prefix:
            pick = [r for r in same if (str(r.get("class") or "").startswith("Регистр") if near == "Регистр" else r.get("class") == near)]
            if pick:
                exact.extend(pick)
                continue
        pick = [r for r in same if class_ok(r)]
        exact.extend(pick or ([] if class_prefix else same))
    if exact:
        exact.sort(key=lambda r: -_CLASS_PRIOR.get(r.get("class") or "", 0.1))
        cands = [{"full_name": r["full_name"], "synonym": r.get("synonym") or "", "score": 100.0} for r in exact[:top_k]]
        return {"full_name": exact[0]["full_name"], "candidates": cands, "class": prefix, "confident": True, "by": "exact_name"}

    # 1b) слова фразы, склеенные в CamelCase, = имя объекта («использовать соглашения с клиентами»)
    words = re.findall(r"[а-яёa-z0-9]+", strip_class_words(re.sub(r"[A-ZА-ЯЁ]\w*[a-zа-яё][A-ZА-ЯЁ]\w*", " ", phrase or "")))
    if 1 < len(words) <= 8:
        glued = "".join(words)
        hit = [r for r in cat if r["_name_l"] == glued and class_ok(r)]
        if hit:
            hit.sort(key=lambda r: -_CLASS_PRIOR.get(r.get("class") or "", 0.1))
            cands = [{"full_name": r["full_name"], "synonym": r.get("synonym") or "", "score": 99.0} for r in hit[:top_k]]
            return {"full_name": hit[0]["full_name"], "candidates": cands, "class": prefix, "confident": True, "by": "glued_words"}

    stems = _phrase_stems(phrase)
    if not stems:
        return {"full_name": "", "candidates": [], "class": prefix, "confident": False}
    weights = _expand(stems)
    scored: list[tuple[float, dict[str, Any]]] = []
    n = len(stems)
    for r in cat:
        if not class_ok(r):
            continue
        best = 0.0
        for seq in (r["_name_seq"], r["_syn_seq"]):
            got, covered, head = _score_seq(weights, stems, seq)
            if got <= 0:
                continue
            recall = got / n
            precision = covered / len(seq)
            # главное слово фразы (первое значимое) = главное слово названия объекта
            phrase_head = _stem_eq(stems[0], seq[0])
            sc = 60 * recall + 20 * precision + (12 if head else 0) + (15 if phrase_head else 0)
            best = max(best, sc)
        if best <= 0:
            # представления списка/объекта — слабый сигнал
            hits = sum(1 for st in stems if any(_stem_eq(st, x) for x in r["_extra_stems"]))
            if not hits:
                continue
            best = 25 * hits / n
        best += 0 if prefix else _CLASS_PRIOR.get(r.get("class") or "", 0.0) * 5
        if r.get("_name_l", "").startswith("удалить"):
            best *= 0.3  # устаревшие объекты (конвенция «Удалить*»)
        scored.append((best, r))
    scored.sort(key=lambda x: -x[0])
    scored = _tiebreak_by_code_weight(scored)
    cands = [
        {"full_name": r["full_name"], "synonym": r.get("synonym") or "", "score": round(s, 1)}
        for s, r in scored[:top_k]
    ]
    if not cands:
        return {"full_name": "", "candidates": [], "class": prefix, "confident": False}
    s1 = scored[0][0]
    s2 = scored[1][0] if len(scored) > 1 else 0.0
    confident = s1 >= 60 and (s1 - s2) >= 6
    return {"full_name": cands[0]["full_name"], "candidates": cands, "class": prefix, "confident": confident, "by": "stems"}


DATA_CLASSES: tuple[str, ...] = (
    "Документ",
    "Справочник",
    "РегистрСведений",
    "РегистрНакопления",
    "РегистрБухгалтерии",
    "ПланВидовХарактеристик",
    "ПланСчетов",
    "БизнесПроцесс",
    "Задача",
)

_PRED_WORDS = re.compile(
    r"(?<![а-яё])(?:помечен\w*|удалени\w*|проведен\w*|непроведен\w*|статус\w*|заполнен\w*|незаполнен\w*|"
    r"указан\w*|пуст\w*|без|котор\w*|ожида\w*|закрыт\w*|открыт\w*|отгрузк\w*|оплат\w*|не)(?![а-яё])",
    re.I,
)


def resolve_data_object(phrase: str, *, min_score: float = 70.0) -> dict[str, Any]:
    """
    Объект-ТАБЛИЦА для списка/отчёта (документ, справочник, регистр) — классы без данных
    (общие модули, перечисления, обработки, регл. задания) исключены. Слова-предикаты
    («помеченные на удаление», «не проведены», «со статусом») из поиска убираются.
    Возвращает {"full_name", "candidates", "confident"}; full_name пуст, если ничего убедительного.
    """
    cleaned = re.sub(r"\s+", " ", _PRED_WORDS.sub(" ", phrase or "")).strip() or (phrase or "")
    pooled: list[tuple[float, dict[str, Any]]] = []
    for cls in DATA_CLASSES:
        rc = resolve_full_name(cleaned, class_prefix=cls, top_k=3)
        for c in rc.get("candidates") or []:
            pooled.append((float(c.get("score") or 0), c))
    if not pooled:
        return {"full_name": "", "candidates": [], "confident": False}
    pooled.sort(key=lambda x: -x[0])
    # ничья по тексту → «основной» объект (больше кода в индексе)
    cat = catalog_by_full_name()
    as_rows = [(sc, cat.get(c["full_name"], {"name": c["full_name"].split(".")[-1], "full_name": c["full_name"]})) for sc, c in pooled]
    as_rows = _tiebreak_by_code_weight(as_rows)
    top_fn = as_rows[0][1].get("full_name")
    ordered = [c for _sc, c in pooled]
    ordered.sort(key=lambda c: 0 if c["full_name"] == top_fn else 1)
    s1 = pooled[0][0]
    ok = s1 >= min_score
    return {"full_name": top_fn if ok else "", "candidates": ordered[:5], "confident": ok, "query": cleaned}
