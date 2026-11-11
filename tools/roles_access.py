"""
Роли, права, пользователи 1С.

Источники:
  1) Выгрузка конфигурации: Roles/<Имя>/Ext/Rights.xml + Roles/<Имя>.xml (синоним)
  2) База: справочник пользователей (имя из find_metadata) через HTTP execute_query

Права ИБ (кто в какую роль назначен в runtime) runtime HTTP часто не отдаёт
без админ-API — в ответе явно помечаем, что есть в dump vs в базе.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterator

from tools.code_search import PROJECT_ROOT, resolve_code_roots

# EN object type in Rights.xml → RU prefix
_RIGHTS_TYPE_RU: dict[str, str] = {
    "Catalog": "Справочник",
    "Document": "Документ",
    "InformationRegister": "РегистрСведений",
    "AccumulationRegister": "РегистрНакопления",
    "AccountingRegister": "РегистрБухгалтерии",
    "CalculationRegister": "РегистрРасчета",
    "Report": "Отчет",
    "DataProcessor": "Обработка",
    "CommonForm": "ОбщаяФорма",
    "CommonModule": "ОбщийМодуль",
    "CommonCommand": "ОбщаяКоманда",
    "Constant": "Константа",
    "Enum": "Перечисление",
    "ChartOfAccounts": "ПланСчетов",
    "ChartOfCharacteristicTypes": "ПланВидовХарактеристик",
    "ChartOfCalculationTypes": "ПланВидовРасчета",
    "BusinessProcess": "БизнесПроцесс",
    "Task": "Задача",
    "ExchangePlan": "ПланОбмена",
    "DocumentJournal": "ЖурналДокументов",
    "FilterCriterion": "КритерийОтбора",
    "HTTPService": "HTTPСервис",
    "WebService": "WebСервис",
    "SessionParameter": "ПараметрСеанса",
    "Subsystem": "Подсистема",
    "Role": "Роль",
    "Configuration": "Конфигурация",
}

# речь пользователя → тип Rights.xml (платформа, не имя объекта CF)
_SPEECH_TO_RIGHTS_TYPE: tuple[tuple[str, str], ...] = (
    (r"(?<![а-яё])справочник", "Catalog"),
    (r"(?<![а-яё])документ", "Document"),
    (r"регистр\w*\s*сведен|informationregister", "InformationRegister"),
    (r"регистр\w*\s*накоплен|accumulationregister", "AccumulationRegister"),
    (r"регистр\w*\s*бухгалтер", "AccountingRegister"),
    (r"(?<![а-яё])обработк", "DataProcessor"),
    (r"(?<![а-яё])отч[её]т", "Report"),
    (r"общ\w*\s+модул|commonmodule", "CommonModule"),
    (r"план\w*\s+счет", "ChartOfAccounts"),
    (r"бизнес.?процесс", "BusinessProcess"),
    (r"план\w*\s+обмен", "ExchangePlan"),
    (r"http.?сервис", "HTTPService"),
)


def rights_object_type_from_phrase(phrase: str) -> str:
    """
    Catalog/Document/… из речи. Пусто = все классы (не подставлять Catalog).
    Несколько типов в фразе → пусто (не угадывать один).
    """
    pl = (phrase or "").lower().replace("ё", "е")
    if not pl:
        return ""
    hits: list[str] = []
    for rx, en in _SPEECH_TO_RIGHTS_TYPE:
        if re.search(rx, pl) and en not in hits:
            hits.append(en)
    if len(hits) == 1:
        return hits[0]
    return ""


_RIGHT_RU: dict[str, str] = {
    "Read": "Чтение",
    "Insert": "Добавление",
    "Update": "Изменение",
    "Delete": "Удаление",
    "Use": "Использование",
    "View": "Просмотр",
    "Edit": "Редактирование",
    "InputByString": "ВводПоСтроке",
    "InteractiveInsert": "ИнтерактивноеДобавление",
    "InteractiveDelete": "ИнтерактивноеУдаление",
    "InteractiveSetDeletionMark": "ИнтерактивнаяПометкаУдаления",
    "InteractiveClearDeletionMark": "ИнтерактивноеСнятиеПометкиУдаления",
    "InteractiveDeleteMarked": "ИнтерактивноеУдалениеПомеченных",
    "Posting": "Проведение",
    "UndoPosting": "ОтменаПроведения",
    "InteractivePosting": "ИнтерактивноеПроведение",
    "InteractivePostingRegular": "ИнтерактивноеПроведениеНеоперативное",
    "InteractiveUndoPosting": "ИнтерактивнаяОтменаПроведения",
    "InteractiveChangeOfPosted": "ИнтерактивноеИзменениеПроведенных",
    "Start": "Старт",
    "InteractiveStart": "ИнтерактивныйСтарт",
    "Execute": "Выполнение",
    "InteractiveExecute": "ИнтерактивноеВыполнение",
    "TotalsControl": "УправлениеИтогами",
    "ReadDataHistory": "ЧтениеИсторииДанных",
    "ReadDataHistoryOfMissingData": "ЧтениеИсторииДанныхОтсутствующихДанных",
    "UpdateDataHistory": "ИзменениеИсторииДанных",
    "UpdateDataHistoryOfMissingData": "ИзменениеИсторииДанныхОтсутствующихДанных",
    "UpdateDataHistorySettings": "ИзменениеНастроекИсторииДанных",
    "UpdateDataHistoryVersionComment": "ИзменениеКомментарияВерсииИсторииДанных",
    "ViewDataHistory": "ПросмотрИсторииДанных",
    "EditDataHistoryVersionComment": "РедактированиеКомментарияВерсииИсторииДанных",
    "SwitchToDataHistoryVersion": "ПереходНаВерсиюИсторииДанных",
}

_NS = {"r": "http://v8.1c.ru/8.2/roles"}
_NS_MD = {
    "md": "http://v8.1c.ru/8.3/MDClasses",
    "v8": "http://v8.1c.ru/8.1/data/core",
}


def _roles_root(code_dir: str | Path | None = None) -> Path | None:
    # Прямой путь — без os.walk всей выгрузки (resolve_code_roots ищет .bsl).
    from vector.catalog import runtime_fs_allowed

    if not runtime_fs_allowed():  # runtime: только зеркало коллекции файлов Qdrant, не диск
        p = next((r / "Roles" for r in resolve_code_roots().roots), None)
        return p if p is not None and p.is_dir() else None
    candidates: list[Path] = []
    if code_dir:
        candidates.append(Path(code_dir) / "Roles")
    candidates.append(PROJECT_ROOT / "knowledge" / "код_конфигурации" / "Roles")
    for p in candidates:
        try:
            if p.is_dir():
                return p
        except OSError:
            continue
    cr = resolve_code_roots(code_dir)
    for root in cr.roots:
        p = root / "Roles"
        if p.is_dir():
            return p
    return None


def _role_synonym(roles_root: Path, role_name: str) -> str:
    meta = roles_root / f"{role_name}.xml"
    if not meta.is_file():
        return ""
    try:
        text = meta.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    # <v8:content>...</v8:content> first usually synonym
    m = re.search(
        r"<v8:lang>ru</v8:lang>\s*<v8:content>([^<]+)</v8:content>",
        text,
    )
    if m:
        return m.group(1).strip()
    m = re.search(r"<v8:content>([^<]+)</v8:content>", text)
    return m.group(1).strip() if m else ""


def list_roles(
    search: str = "",
    *,
    limit: int = 50,
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Список ролей конфигурации из выгрузки Roles/."""
    root = _roles_root(code_dir)
    if not root:
        return {
            "ok": False,
            "ready": False,
            "roles": [],
            "hint": (
                "Папка Roles/ в выгрузке не найдена. "
                "Нужна выгрузка конфигурации в файлы (RAG_CODE_DIR / knowledge/код_конфигурации)."
            ),
        }

    q = (search or "").strip().lower().replace("ё", "е")
    limit = max(1, min(int(limit or 50), 200))
    items: list[dict[str, Any]] = []

    try:
        names = sorted(
            (e.name for e in os.scandir(root) if e.is_dir()),
            key=str.lower,
        )
    except OSError as e:
        return {"ok": False, "ready": False, "roles": [], "error": str(e)}

    total = len(names)
    toks = re.findall(r"[a-zа-яё0-9_]{3,}", q) if q else []

    for name in names:
        syn = ""
        if q:
            syn = _role_synonym(root, name)
            blob = f"{name} {syn}".lower().replace("ё", "е")
            if q not in blob and toks and not any(t in blob for t in toks):
                continue
        elif len(items) < limit:
            syn = _role_synonym(root, name)
        if len(items) >= limit:
            # total уже известен — дальше XML не читаем
            break
        rights = root / name / "Ext" / "Rights.xml"
        items.append(
            {
                "name": name,
                "full_name": f"Роль.{name}",
                "synonym": syn,
                "rights_file": str(rights) if rights.is_file() else "",
                "has_rights_xml": rights.is_file(),
            }
        )
    return {
        "ok": True,
        "source": "config_dump/Roles",
        "search": search,
        "count": len(items),
        "total_roles_in_dump": total,
        "roles": items,
        "prefer": (
            "Покажи name + synonym. Права роли: role_rights(role=name). "
            "Кто имеет доступ к объекту: find_roles_for_object(object_name=Отчет.X)."
        ),
        "note": (
            "Это роли КОНФИГУРАЦИИ (метаданные), не назначения пользователям ИБ. "
            "Пользователи базы: list_users."
        ),
        "truncated": total > len(items),
    }


def _iter_object_blocks(rights_path: Path) -> Iterator[tuple[str, list[tuple[str, bool]]]]:
    """
    Потоковый разбор Rights.xml: (object_name, [(right_name, value), ...]).
    Не грузит весь DOM для файлов 1MB+.
    """
    try:
        # iterparse streaming
        context = ET.iterparse(str(rights_path), events=("end",))
    except OSError:
        return

    # strip ns
    def local(tag: str) -> str:
        if "}" in tag:
            return tag.rsplit("}", 1)[-1]
        return tag

    # simpler: regex for medium files; for large use iterparse carefully
    try:
        size = rights_path.stat().st_size
    except OSError:
        return

    if size < 2_000_000:
        try:
            text = rights_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for m in re.finditer(
            r"<object>\s*<name>([^<]+)</name>(.*?)</object>",
            text,
            re.S | re.I,
        ):
            obj = m.group(1).strip()
            body = m.group(2)
            rs: list[tuple[str, bool]] = []
            for rm in re.finditer(
                r"<right>\s*<name>([^<]+)</name>\s*<value>(true|false)</value>",
                body,
                re.S | re.I,
            ):
                rs.append((rm.group(1).strip(), rm.group(2).lower() == "true"))
            yield obj, rs
        return

    # large file: line-ish iterparse
    try:
        for _event, elem in context:
            tag = local(elem.tag)
            if tag == "object":
                # collect children
                obj_name = ""
                rs = []
                for ch in elem:
                    ct = local(ch.tag)
                    if ct == "name" and ch.text:
                        obj_name = ch.text.strip()
                    elif ct == "right":
                        rn, rv = "", False
                        for rch in ch:
                            rt = local(rch.tag)
                            if rt == "name" and rch.text:
                                rn = rch.text.strip()
                            elif rt == "value" and rch.text:
                                rv = rch.text.strip().lower() == "true"
                        if rn:
                            rs.append((rn, rv))
                if obj_name:
                    yield obj_name, rs
                elem.clear()
    except ET.ParseError:
        return


def _normalize_object_key(name: str) -> list[str]:
    """Варианты ключа для поиска: Report.X, Отчет.X, bare name."""
    raw = (name or "").strip()
    if not raw:
        return []
    keys = {raw, raw.replace(" ", "")}
    # RU → EN type
    ru_to_en = {v: k for k, v in _RIGHTS_TYPE_RU.items()}
    if "." in raw:
        pref, rest = raw.split(".", 1)
        keys.add(rest)
        if pref in ru_to_en:
            keys.add(f"{ru_to_en[pref]}.{rest}")
        if pref in _RIGHTS_TYPE_RU:
            keys.add(f"{_RIGHTS_TYPE_RU[pref]}.{rest}")
        # plural forms from list_objects
        plurals = {
            "Справочники": "Catalog",
            "Документы": "Document",
            "Отчеты": "Report",
            "Обработки": "DataProcessor",
            "РегистрыСведений": "InformationRegister",
            "РегистрыНакопления": "AccumulationRegister",
            "ОбщиеФормы": "CommonForm",
            "ОбщиеМодули": "CommonModule",
            "Роли": "Role",
        }
        if pref in plurals:
            keys.add(f"{plurals[pref]}.{rest}")
    else:
        # bare name — add common prefixes for search
        for en in ("Report", "Catalog", "Document", "DataProcessor", "CommonForm"):
            keys.add(f"{en}.{raw}")
        for ru in ("Отчет", "Справочник", "Документ", "Обработка", "ОбщаяФорма"):
            keys.add(f"{ru}.{raw}")
    return [k for k in keys if k]


def _object_to_ru(en_name: str) -> str:
    if "." not in en_name:
        return en_name
    pref, rest = en_name.split(".", 1)
    ru = _RIGHTS_TYPE_RU.get(pref, pref)
    return f"{ru}.{rest}"


def _rights_to_ru(rights: list[tuple[str, bool]]) -> list[dict[str, Any]]:
    out = []
    for name, val in rights:
        if not val:
            continue  # only granted
        out.append(
            {
                "right": name,
                "right_ru": _RIGHT_RU.get(name, name),
                "granted": True,
            }
        )
    return out


def role_rights(
    role: str,
    *,
    object_filter: str = "",
    limit: int = 80,
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Права одной роли (из Rights.xml). object_filter — подстрока имени объекта."""
    root = _roles_root(code_dir)
    if not root:
        return list_roles(code_dir=code_dir)

    role_name = (role or "").strip()
    role_name = role_name.replace("Роль.", "").replace("Role.", "")
    if not role_name:
        return {"error": "Укажи role=ИмяРоли из list_roles"}

    # resolve role dir
    role_dir = root / role_name
    if not role_dir.is_dir():
        # search case-insensitive / partial
        low = role_name.lower()
        for d in root.iterdir():
            if d.is_dir() and (d.name.lower() == low or low in d.name.lower()):
                role_dir = d
                role_name = d.name
                break
        else:
            found = list_roles(search=role_name, limit=10, code_dir=code_dir)
            return {
                "error": f"Роль не найдена: {role}",
                "candidates": found.get("roles") or [],
            }

    rights_path = role_dir / "Ext" / "Rights.xml"
    if not rights_path.is_file():
        return {
            "error": f"Нет Rights.xml у роли {role_name}",
            "role": role_name,
        }

    filt = (object_filter or "").strip().lower().replace("ё", "е")
    filt_keys = [k.lower() for k in _normalize_object_key(object_filter)] if object_filter else []
    limit = max(1, min(int(limit or 80), 200))
    objects: list[dict[str, Any]] = []
    total_objects = 0

    for obj_name, rights in _iter_object_blocks(rights_path):
        total_objects += 1
        granted = _rights_to_ru(rights)
        if not granted:
            continue
        if filt or filt_keys:
            blob = obj_name.lower()
            ok = filt in blob if filt else False
            if not ok and filt_keys:
                ok = any(k.lower() in blob or blob in k.lower() for k in filt_keys)
            if not ok:
                # also match RU form
                ru = _object_to_ru(obj_name).lower()
                ok = (filt and filt in ru) or any(
                    k.lower() in ru for k in filt_keys
                )
            if not ok:
                continue
        objects.append(
            {
                "object": obj_name,
                "object_ru": _object_to_ru(obj_name),
                "rights": granted,
                "rights_summary": ", ".join(r["right_ru"] for r in granted),
            }
        )
        if len(objects) >= limit:
            break

    return {
        "ok": True,
        "role": role_name,
        "full_name": f"Роль.{role_name}",
        "synonym": _role_synonym(root, role_name),
        "objects_with_rights": len(objects),
        "objects_total_in_role_file": total_objects,
        "object_filter": object_filter,
        "objects": objects,
        "prefer": (
            "Покажи object_ru + rights_summary. "
            "Для поиска «кто имеет доступ к X»: find_roles_for_object."
        ),
        "truncated": len(objects) >= limit,
        "source": "config_dump Rights.xml",
    }


def find_roles_for_object(
    object_name: str,
    *,
    right: str = "",
    limit: int = 40,
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    Какие роли дают права на объект конфигурации.
    object_name: Отчет.Имя | Report.Name | ИмяОбъекта
    right: optional filter View/Read/Use…
    """
    root = _roles_root(code_dir)
    if not root:
        return list_roles(code_dir=code_dir)

    raw = (object_name or "").strip()
    if not raw:
        return {
            "error": "object_name обязателен",
            "hint": "Отчет.Имя | Report.Name | Справочник.Имя | bare name",
        }

    keys = _normalize_object_key(raw)
    keys_l = [k.lower().replace("ё", "е") for k in keys]
    # имя объекта без типа: Document.X → x
    bare = re.sub(
        r"\s+",
        "",
        raw.split(".")[-1].lower().replace("ё", "е"),
    )
    # CamelCase → части (РеализацияТоваровУслуг → реализация, товаров, услуг)
    bare_parts = [
        p.lower()
        for p in re.findall(r"[A-ZА-ЯЁ][a-zа-яё0-9]+|[a-zа-яё0-9]+", raw.split(".")[-1])
        if len(p) >= 3
    ]
    right_f = (right or "").strip().lower()
    # запрос с типом (Документ.X / Document.X) — только этот объект + вложенные пути
    typed_query = "." in raw.strip()

    def _obj_matches(obj_name: str) -> bool:
        """
        Точное сопоставление без hardcode CF и без ложных:
        - Attribute.Реализация ⊂ Document.РеализацияТоваровУслуг
        - Catalog.XПрисоединенныеФайлы ⊂ bare X
        """
        ol = (obj_name or "").replace("\\", ".").lower().replace("ё", "е")
        parts = [p for p in ol.split(".") if p]
        if not parts:
            return False
        leaf = parts[-1]
        # 1) полный ключ из normalize (Document.X / Документ.X / bare.X)
        for kl in keys_l:
            if not kl:
                continue
            if ol == kl:
                return True
            # Document.X.TabularSection… / Document.X.Attribute… (дети объекта)
            if ol.startswith(kl + "."):
                return True
        # 2) typed full_name: только Type.Name и Type.Name.*
        if typed_query and bare:
            for kl in keys_l:
                if "." not in kl:
                    continue
                # Type.Name
                if ol == kl or ol.startswith(kl + "."):
                    return True
            # EN/RU type already in keys; no bare-in-leaf
            return False
        # 3) bare name: leaf == name ИЛИ второй сегмент Type.Name (не Attribute.Реализация)
        if bare and not typed_query:
            if leaf == bare:
                return True
            # Document.Name / Catalog.Name — parts[1] == bare
            if len(parts) >= 2 and parts[1] == bare:
                return True
            # Document.Name.TabularSection.X — parts[1] == bare
            if len(parts) >= 2 and parts[1] == bare:
                return True
        return False

    limit = max(1, min(int(limit or 40), 200))
    hits: list[dict[str, Any]] = []

    # prefilter: точное имя объекта (не короткий «реализ» → Attribute.Реализация)
    prefilter_needles: list[str] = []
    if bare and len(bare) >= 4:
        prefilter_needles.append(bare)
    for kl in keys_l:
        if "." in kl and len(kl) >= 6:
            prefilter_needles.append(kl)
    if not prefilter_needles and bare_parts:
        # только если части длинные; не одна короткая
        long_parts = [p for p in bare_parts if len(p) >= 5]
        if len(long_parts) >= 2:
            prefilter_needles.append(long_parts[0])

    for role_dir in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not role_dir.is_dir():
            continue
        rights_path = role_dir / "Ext" / "Rights.xml"
        if not rights_path.is_file():
            continue
        try:
            text = rights_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text_l = text.lower().replace("ё", "е")
        if prefilter_needles and not any(n in text_l for n in prefilter_needles):
            continue

        matched_objs: list[dict[str, Any]] = []
        for obj_name, rights in _iter_object_blocks(rights_path):
            if not _obj_matches(obj_name):
                continue
            granted = _rights_to_ru(rights)
            if right_f:
                granted = [
                    g
                    for g in granted
                    if right_f in g["right"].lower()
                    or right_f in g["right_ru"].lower()
                ]
            if not granted:
                continue
            matched_objs.append(
                {
                    "object": obj_name,
                    "object_ru": _object_to_ru(obj_name),
                    "rights": granted,
                    "rights_summary": ", ".join(g["right_ru"] for g in granted),
                }
            )

        if matched_objs:
            # приоритет: права именно на Type.Name, не только на вложенные
            matched_objs.sort(
                key=lambda a: (
                    0
                    if str(a.get("object") or "").lower().replace("ё", "е")
                    in keys_l
                    or str(a.get("object") or "").lower().split(".")[-1] == bare
                    else 1,
                    str(a.get("object") or ""),
                )
            )
            hits.append(
                {
                    "role": role_dir.name,
                    "full_name": f"Роль.{role_dir.name}",
                    "synonym": _role_synonym(root, role_dir.name),
                    "access": matched_objs,
                }
            )
        # не break по limit — сначала полный скан, потом обрезка

    total = len(hits)
    truncated = total > limit
    hits_out = hits[:limit]
    complete = not truncated

    return {
        "ok": True,
        "object_query": raw,
        "search_keys": keys[:12],
        "roles_count": len(hits_out),
        "roles_total": total,
        "roles": hits_out,
        "complete": complete,
        "prefer": (
            "Перечисли ВСЕ роли из roles (name/synonym + rights_summary). "
            f"roles_total={total}, shown={len(hits_out)}, complete={complete}. "
            + (
                "truncated=true — это НЕ полный список, скажи сколько всего и предложи limit выше. "
                if truncated
                else "complete=true — по выгрузке CF это все роли с правами на этот object_name. "
            )
            + "Права метаданных роли, не назначения пользователям ИБ. "
            "Пользователи: list_users."
        ),
        "source": "config_dump Roles/*/Ext/Rights.xml",
        "truncated": truncated,
    }


def list_roles_by_right(
    *,
    rights: str = "InteractiveDelete,Update,Edit,InteractiveSetDeletionMark",
    object_type: str = "",
    object_name: str = "",
    limit: int = 80,
    code_dir: str | Path | None = None,
    max_objects_per_role: int = 8,
) -> dict[str, Any]:
    """
    Роли, у которых на объектах object_type (Catalog/Document/… или пусто=все)
    выдано хотя бы одно из rights (true).

    object_type: Catalog | Document | InformationRegister | … (EN из Rights.xml)
                 или Справочник | Документ | пусто = все классы этой CF
    object_name: опционально bare name из metadata или полное full_name.
    """
    root = _roles_root(code_dir)
    if not root:
        return {
            "ok": False,
            "error": "Нет Roles/ в выгрузке (paths.code_dir)",
            "hint": "Выгрузите конфигурацию и укажите code_dir",
        }

    # normalize type — пусто / все = не фильтровать класс
    ot = (object_type or "").strip()
    ot_low = ot.lower().replace("ё", "е")
    if ot_low in ("", "all", "все", "*", "any", "все типы"):
        ot_en = ""
    else:
        ru_to_en = {v: k for k, v in _RIGHTS_TYPE_RU.items()}
        if ot in ru_to_en:
            ot_en = ru_to_en[ot]
        elif ot in _RIGHTS_TYPE_RU:
            ot_en = ot
        else:
            ot_en = rights_object_type_from_phrase(ot) or ot

    want_rights = {
        r.strip()
        for r in re.split(r"[,;\s]+", rights or "")
        if r.strip()
    }
    # accept RU names
    ru_inv = {v.lower(): k for k, v in _RIGHT_RU.items()}
    want_en: set[str] = set()
    for w in want_rights:
        if w in _RIGHT_RU:
            want_en.add(w)
        elif w.lower() in ru_inv:
            want_en.add(ru_inv[w.lower()])
        else:
            # partial
            for en, ru in _RIGHT_RU.items():
                if w.lower() in en.lower() or w.lower() in ru.lower():
                    want_en.add(en)
    if not want_en:
        want_en = {
            "InteractiveDelete",
            "Update",
            "Edit",
            "InteractiveSetDeletionMark",
            "Delete",
        }

    obj_filter = (object_name or "").strip()
    bare = obj_filter.split(".")[-1] if obj_filter else ""

    limit = max(1, min(int(limit or 80), 200))
    max_obj = max(1, min(int(max_objects_per_role or 8), 30))
    hits: list[dict[str, Any]] = []

    prefix = f"{ot_en}." if ot_en else ""
    for role_dir in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not role_dir.is_dir():
            continue
        rights_path = role_dir / "Ext" / "Rights.xml"
        if not rights_path.is_file():
            continue
        # prefilter: file must mention type and at least one right name
        try:
            head = rights_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if prefix:
            if prefix not in head and f"name>{ot_en}." not in head.replace(" ", ""):
                if ot_en + "." not in head:
                    continue
        if not any(r in head for r in want_en):
            continue

        matched: list[dict[str, Any]] = []
        for obj_name_xml, rights_list in _iter_object_blocks(rights_path):
            if prefix and not obj_name_xml.startswith(prefix):
                continue
            parts = obj_name_xml.split(".")
            if len(parts) < 2:
                continue
            # объект уровня Catalog.Name — не ТЧ/реквизит
            if len(parts) > 2:
                continue
            xml_name = parts[1]
            if bare:
                if (
                    bare.lower() not in xml_name.lower()
                    and bare.lower() not in obj_name_xml.lower()
                ):
                    continue
            granted = []
            for rn, rv in rights_list:
                if not rv:
                    continue
                if rn in want_en:
                    granted.append(
                        {
                            "right": rn,
                            "right_ru": _RIGHT_RU.get(rn, rn),
                            "value": True,
                        }
                    )
            if not granted:
                continue
            matched.append(
                {
                    "object": obj_name_xml,
                    "object_ru": _object_to_ru(obj_name_xml),
                    "rights": granted,
                    "rights_summary": ", ".join(g["right_ru"] for g in granted),
                }
            )
            if len(matched) >= max_obj:
                break

        if matched:
            hits.append(
                {
                    "role": role_dir.name,
                    "full_name": f"Роль.{role_dir.name}",
                    "synonym": _role_synonym(root, role_dir.name),
                    "objects_count": len(matched),
                    "objects": matched,
                }
            )
            if len(hits) >= limit:
                break

    prefer = (
        "Перечисли role + rights_summary + 1–2 object_ru. "
        "Это метаданные ролей из dump, не назначения пользователям ИБ. "
        "InteractiveDelete/Edit — интерактив; Update/Delete — в т.ч. программные."
    )
    note = "Универсально для любой CF в code_dir."
    if not hits:
        prefer = (
            "roles пуст: честно скажи «в выгрузке Roles/*/Rights.xml не нашёл ролей "
            "с этими правами на выбранный тип объектов» (или code_dir без Roles). "
            "Не выдумывай роли. Предложи access_overview / list_roles / уточнить right."
        )
        note = (
            "0 ролей: либо в dump нет Rights.xml, либо нет true на выбранных rights. "
            "Не ok «как будто всё хорошо» — сообщи пустой результат."
        )
    return {
        "ok": True,
        "object_type": ot_en or "All",
        "object_type_ru": _RIGHTS_TYPE_RU.get(ot_en, "все типы") if ot_en else "все типы",
        "rights_filter": sorted(want_en),
        "object_name_filter": bare or None,
        "roles_count": len(hits),
        "roles": hits,
        "empty": len(hits) == 0,
        "prefer": prefer,
        "source": "config_dump Roles/*/Ext/Rights.xml",
        "truncated": len(hits) >= limit,
        "note": note,
        "manager_summary": {
            "row_count": len(hits),
            "message": (
                f"Ролей с правами {', '.join(sorted(want_en)[:4])} "
                f"на {_RIGHTS_TYPE_RU.get(ot_en, 'все типы') if ot_en else 'все типы'}: {len(hits)}."
                + (
                    " В выгрузке конфигурации совпадений нет."
                    if not hits
                    else ""
                )
            ),
        },
    }


def _rls_object_keys(object_name: str, full_name: str) -> list[str]:
    """Catalog.X / Document.X / bare — как в Rights.xml этой CF."""
    keys: list[str] = []
    fn = (full_name or "").strip()
    bare = (object_name or "").strip() or (fn.split(".")[-1] if fn else "")
    if fn and "." in fn:
        prefix, name = fn.split(".", 1)
        ru_to_en = {v: k for k, v in _RIGHTS_TYPE_RU.items()}
        en = ru_to_en.get(prefix) or (
            prefix if prefix in _RIGHTS_TYPE_RU else ""
        )
        if en:
            keys.append(f"{en}.{name}")
        keys.append(fn)
        keys.append(name)
    if bare and bare not in keys:
        keys.append(bare)
        for en in ("Catalog", "Document", "InformationRegister", "AccumulationRegister"):
            keys.append(f"{en}.{bare}")
    # unique preserve order
    out: list[str] = []
    seen: set[str] = set()
    for k in keys:
        kl = k.lower().replace("ё", "е")
        if len(k) < 3 or kl in seen:
            continue
        seen.add(kl)
        out.append(k)
    return out


def explain_rls(
    request: str = "",
    *,
    object_name: str = "",
    full_name: str = "",
    limit: int = 40,
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    RLS: restrictionByCondition + имена шаблонов роли на объект.
    Не полный текст BSP-шаблона (тысячи строк) — имя шаблона и превью условия.
    """
    root = _roles_root(code_dir)
    if not root:
        return {
            "ok": False,
            "error": "Нет Roles/ в выгрузке (paths.code_dir)",
            "hint": "Выгрузите конфигурацию и укажите code_dir",
            "hits": [],
        }

    hint = (object_name or "").strip() or (full_name or "").split(".")[-1].strip()
    if not hint:
        try:
            from tools.name_match import (
                pascal_candidates_in_text,
                resolve_object_name,
            )

            for p in pascal_candidates_in_text(request or ""):
                if len(p) >= 4:
                    hint = p
                    break
            if not hint:
                hint = resolve_object_name(request or "") or ""
        except Exception:
            hint = ""
    if not hint:
        return {
            "ok": False,
            "error": "Не понял объект для RLS. Укажи object_name из find_metadata.",
            "hits": [],
        }

    keys = _rls_object_keys(hint, full_name)
    keys_l = [k.lower().replace("ё", "е") for k in keys]
    needle = hint.lower().replace("ё", "е")
    limit = max(1, min(int(limit or 40), 80))

    hits: list[dict[str, Any]] = []
    templates_seen: set[str] = set()
    scanned = 0

    try:
        role_dirs = sorted(
            (e for e in os.scandir(root) if e.is_dir()),
            key=lambda e: e.name.lower(),
        )
    except OSError as e:
        return {"ok": False, "error": str(e), "hits": []}

    obj_re = re.compile(
        r"<object>\s*<name>([^<]+)</name>(.*?)</object>",
        re.S | re.I,
    )
    cond_re = re.compile(
        r"<restrictionByCondition>\s*<condition>(.*?)</condition>",
        re.S | re.I,
    )
    tmpl_call_re = re.compile(r"#([A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)\s*\(")
    right_name_re = re.compile(
        r"<right>\s*<name>([^<]+)</name>(.*?)</right>",
        re.S | re.I,
    )

    for entry in role_dirs:
        rights = Path(entry.path) / "Ext" / "Rights.xml"
        if not rights.is_file():
            continue
        scanned += 1
        try:
            # быстрый отсев: объект не упоминается
            raw = rights.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if needle not in raw.lower().replace("ё", "е"):
            continue
        if "restrictionByCondition" not in raw:
            continue

        for om in obj_re.finditer(raw):
            oname = om.group(1).strip()
            on_l = oname.lower().replace("ё", "е")
            if not any(k == on_l or on_l.endswith("." + needle) for k in keys_l):
                if on_l.split(".")[-1] != needle:
                    continue
            body = om.group(2)
            if "restrictionByCondition" not in body:
                continue
            for rm in right_name_re.finditer(body):
                rname = rm.group(1).strip()
                rbody = rm.group(2)
                cm = cond_re.search(rbody)
                if not cm:
                    continue
                cond = (
                    cm.group(1)
                    .replace("&amp;", "&")
                    .replace("&lt;", "<")
                    .replace("&gt;", ">")
                    .strip()
                )
                tnames = tmpl_call_re.findall(cond)
                for t in tnames:
                    templates_seen.add(t)
                hits.append(
                    {
                        "role": entry.name,
                        "object": oname,
                        "right": rname,
                        "templates": tnames,
                        "condition_preview": cond[:500],
                    }
                )
                if len(hits) >= limit:
                    break
            if len(hits) >= limit:
                break
        if len(hits) >= limit:
            break

    return {
        "ok": True,
        "object_name": hint,
        "object_keys_tried": keys[:8],
        "count": len(hits),
        "roles_scanned": scanned,
        "hits": hits,
        "template_names": sorted(templates_seen),
        "source": "config_dump Roles/*/Ext/Rights.xml restrictionByCondition",
        "truncated": len(hits) >= limit,
        "prefer": (
            "Перечисли role + object + right + templates. "
            "condition_preview — вызов шаблона, не весь BSP-текст. "
            "Это RLS метаданных роли, не назначения пользователям ИБ."
        ),
        "note": (
            "Пустые hits — на объект нет restrictionByCondition в выгрузке "
            "(роль без RLS или другое имя объекта)."
        ),
    }


def list_users(
    backend: Any,
    *,
    search: str = "",
    limit: int = 50,
    include_invalid: bool = False,
) -> dict[str, Any]:
    """
    Пользователи из справочника конфигурации (имя — через find_metadata).
    Не путать с пользователями ИБ (Администрирование).
    """
    lim = max(1, min(int(limit or 50), 200))
    # detect catalog: стемы «пользовател» — не hardcode full_name CF
    full = ""
    try:
        for q in ("Пользователи", "Пользователь", "Users"):
            meta = backend.find_metadata(q, "Справочники", 15)
            results = meta.get("results") or meta.get("items") or []
            for r in results:
                fn = str(r.get("full_name") or "")
                low = fn.lower().replace("ё", "е")
                if not fn.startswith("Справочник."):
                    continue
                if "внешн" in low or "групп" in low:
                    continue
                if "пользовател" in low or low.endswith(".users") or "user" in low:
                    full = fn
                    break
            if full:
                break
    except Exception:
        full = ""
    if not full:
        return {
            "ok": False,
            "error": "Справочник пользователей не найден в metadata этой базы",
            "hint": "find_metadata(стемы: пользователи / users, class=Справочники)",
            "users": [],
            "count": 0,
        }

    # structure for available fields
    fields = ["Наименование"]
    try:
        st = backend.get_object_structure(full)
        attrs = st.get("attributes") or []
        names = set()
        for a in attrs if isinstance(attrs, list) else []:
            if isinstance(a, dict):
                names.add(str(a.get("name") or a.get("Name") or ""))
            elif isinstance(a, str):
                names.add(a)
        for cand in (
            "Наименование",
            "Недействителен",
            "Служебный",
            "Подразделение",
            "Комментарий",
            "ИдентификаторПользователяИБ",
            "Подготовлен",
        ):
            if cand in names and cand not in fields:
                fields.append(cand)
    except Exception:
        pass

    select = ", ".join(f"Т.{f} КАК {f}" for f in fields)
    where = ""
    qsearch = (search or "").strip()
    if qsearch:
        # simple filter in post; query LIKE may not work same
        pass
    if not include_invalid and "Недействителен" in fields:
        where = " ГДЕ Т.Недействителен = Ложь"

    query = f"ВЫБРАТЬ ПЕРВЫЕ {lim} {select} ИЗ {full} КАК Т{where}"
    try:
        raw = backend.execute_query(query, lim)
    except Exception as e:
        return {"error": str(e), "full_name": full}

    if isinstance(raw, dict) and raw.get("error"):
        return {
            "error": raw.get("error"),
            "full_name": full,
            "hint": raw.get("hint"),
            "note": (
                "Справочник пользователей может быть пуст/закрыт правами HTTP. "
                "Роли конфигурации всё равно: list_roles / find_roles_for_object."
            ),
        }

    rows = raw.get("data") or raw.get("rows") or []
    if not isinstance(rows, list):
        rows = []

    if qsearch:
        ql = qsearch.lower()
        rows = [
            r
            for r in rows
            if isinstance(r, dict)
            and ql in str(r.get("Наименование") or "").lower()
        ]

    return {
        "ok": True,
        "source": "database",
        "catalog": full,
        "count": len(rows),
        "users": rows[:lim],
        "fields": fields,
        "prefer": (
            "Это элементы справочника пользователей (прикладные). "
            "Роли конфигурации: list_roles. "
            "Права роли на объект: find_roles_for_object. "
            "Связка пользователь↔роли ИБ в read-only HTTP обычно недоступна."
        ),
        "note": (
            "Учебная/пустая база может содержать только «<Не указан>». "
            "Роли и Rights.xml — из выгрузки конфигурации (полные)."
        ),
    }


def access_overview(
    request: str,
    *,
    backend: Any = None,
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    ONE-SHOT: «права на отчёт X» / «какие роли» / «пользователи».
    Универсальный разбор фразы.
    """
    raw = (request or "").strip()
    pl = raw.lower().replace("ё", "е")

    # list all roles
    if re.search(r"какие\s+роли|список\s+рол|все\s+роли|роли\s+есть", pl) and not re.search(
        r"\b(на|для|отч[её]т|форм|документ|справочник)\b", pl
    ):
        return {
            **list_roles(limit=80, code_dir=code_dir),
            "routed": "list_roles",
        }

    # users
    if re.search(r"пользовател", pl) and not re.search(r"рол", pl):
        if backend is None:
            return {
                "error": "backend required for list_users",
                "hint": "list_users через агент",
            }
        return {**list_users(backend, search="", limit=50), "routed": "list_users"}

    # rights on object
    # strip noise
    obj = raw
    for noise in (
        "права на",
        "права для",
        "доступ к",
        "доступ на",
        "кто может",
        "какие роли",
        "роли на",
        "роли для",
        "права",
        "доступ",
        "роль",
        "роли",
    ):
        obj = re.sub(noise, " ", obj, flags=re.I)
    obj = re.sub(r"\s+", " ", obj).strip()
    # if empty keep original tokens
    if len(obj) < 2:
        obj = raw

    # Универсально: слова фразы → CamelCase-имя объекта + префиксы типов
    def _stem_ru(w: str) -> str:
        w = (w or "").replace("ё", "е")
        for suf in (
            "ую",
            "юю",
            "ая",
            "яя",
            "ой",
            "ей",
            "ий",
            "ый",
            "ое",
            "ее",
            "ые",
            "ие",
            "ов",
            "ев",
            "ам",
            "ям",
            "ах",
            "ях",
            "ом",
            "ем",
            "у",
            "ю",
            "а",
            "я",
            "ы",
            "и",
            "е",
        ):
            if len(w) > len(suf) + 3 and w.endswith(suf):
                return w[: -len(suf)]
        return w

    guesses = [obj]
    toks = re.findall(r"[A-Za-zА-Яа-яЁё0-9]{3,}", obj)
    stems = [_stem_ru(t.lower()) for t in toks]
    stems = [s for s in stems if len(s) >= 3]
    if stems:
        camel = "".join(s[:1].upper() + s[1:] for s in stems)
        # варианты без жёстких имён конфигурации
        type_prefixes = (
            "",
            "Отчет.",
            "Report.",
            "Справочник.",
            "Catalog.",
            "Документ.",
            "Document.",
            "Обработка.",
            "DataProcessor.",
            "ОбщаяФорма.",
            "CommonForm.",
            "РегистрСведений.",
            "InformationRegister.",
        )
        for pref in type_prefixes:
            guesses.append(pref + camel)
        if len(stems) == 1:
            guesses.append(stems[0])
            guesses.append(stems[0][:1].upper() + stems[0][1:])

    # убрать дубли, сохранить порядок
    seen_g: set[str] = set()
    uniq_guesses: list[str] = []
    for g in guesses:
        g = (g or "").strip()
        if not g or g in seen_g:
            continue
        seen_g.add(g)
        uniq_guesses.append(g)

    best: dict[str, Any] | None = None

    for g in uniq_guesses:
        # typed full_name → limit выше; complete по всей выгрузке
        lim = 120 if "." in g else 80
        found = find_roles_for_object(g, limit=lim, code_dir=code_dir)
        if not found.get("roles_count"):
            continue
        score = int(found.get("roles_count") or 0)
        # бонус: точное совпадение object (Document.X), не Attribute.X
        hit_exact = False
        g_norm = g.lower().replace("ё", "е").replace(" ", "")
        g_leaf = g_norm.split(".")[-1]
        for role in found.get("roles") or []:
            for acc in role.get("access") or []:
                oname = str(acc.get("object") or "").lower().replace("ё", "е")
                oru = str(acc.get("object_ru") or "").lower().replace("ё", "е")
                o_leaf = oname.split(".")[-1]
                if g_norm and (oname == g_norm or oru.replace(" ", "") == g_norm):
                    hit_exact = True
                    score += 100
                elif g_leaf and o_leaf == g_leaf and oname.startswith(
                    ("document.", "документ.")
                ):
                    # Document.Name exact leaf
                    hit_exact = True
                    score += 80
                elif g_leaf and len(g_leaf) >= 6 and (
                    oname == g_leaf or oname.endswith("." + g_leaf)
                ):
                    hit_exact = True
                    score += 40
        # typed guess сильнее bare
        if "." in g:
            score += 10
        found["_pick"] = score
        found["resolved_object_guess"] = g
        if hit_exact and score >= 80:
            found["routed"] = "find_roles_for_object"
            found["request"] = raw
            found.pop("_pick", None)
            return found
        if best is None or score > int(best.get("_pick") or 0):
            best = found

    if best and best.get("roles_count"):
        best["routed"] = "find_roles_for_object"
        best["request"] = raw
        best.pop("_pick", None)
        return best

    found = find_roles_for_object(obj, limit=100, code_dir=code_dir)
    found["routed"] = "find_roles_for_object"
    found["request"] = raw
    return found
