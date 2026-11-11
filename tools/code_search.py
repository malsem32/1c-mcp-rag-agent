"""
Поиск по исходному коду конфигурации 1С.

Runtime (агент / MCP):
  ТОЛЬКО Qdrant (см. vector.catalog).
  knowledge/код_конфигурации в runtime НЕ читается.

Подготовка индекса:
  scripts/prepare_config_for_qdrant.py  ← читает выгрузку
  scripts/index_qdrant.py

Лимиты под context 32k: сниппеты, outline, окна строк.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


# --- бюджет 32k (символы tool result) ---
DEFAULT_MAX_MATCHES = 12
DEFAULT_CONTEXT_LINES = 2
DEFAULT_MAX_LINE_CHARS = 220
DEFAULT_MAX_SNIPPET_CHARS = 900
DEFAULT_MAX_RESULT_CHARS = 5500
DEFAULT_READ_MAX_LINES = 100
DEFAULT_OUTLINE_MAX = 200
DEFAULT_MAX_FILE_BYTES = 2_500_000
DEFAULT_MAX_FILES_SCAN = 25_000

CODE_EXTENSIONS = {".bsl", ".os", ".txt"}
# EDT / выгрузка: Module.bsl, ObjectModule.bsl, ManagerModule.bsl…
MODULE_NAME_HINTS = (
    "module.bsl",
    "objectmodule.bsl",
    "managermodule.bsl",
    "recordsetmodule.bsl",
    "commandmodule.bsl",
    "valuemanagermodule.bsl",
)


def _norm_token(s: str) -> str:
    return re.sub(r"[^a-zA-Zа-яА-ЯёЁ0-9_]+", "", (s or "").lower().replace("ё", "е"))


# ---------------------------------------------------------------------------
# Выгрузка конфигуратора/EDT — ВСЕ корневые папки (EN) → русский + префикс 1С.
# Агент/пользователь говорят по-русски; dump всегда на английском.
# ---------------------------------------------------------------------------
# en_folder: {ru, prefix_1c, kind, aliases_ru}
DUMP_TYPES: dict[str, dict[str, Any]] = {
    "Catalogs": {
        "ru": "Справочники",
        "prefix": "Справочник",
        "kind": "catalog_module",
        "aliases": ("справочник", "справочники", "catalog", "catalogs"),
    },
    "Documents": {
        "ru": "Документы",
        "prefix": "Документ",
        "kind": "document_module",
        "aliases": ("документ", "документы", "document", "documents"),
    },
    "DocumentJournals": {
        "ru": "ЖурналыДокументов",
        "prefix": "ЖурналДокументов",
        "kind": "document_journal",
        "aliases": ("журналдокументов", "журналыдокументов", "documentjournals"),
    },
    "Enums": {
        "ru": "Перечисления",
        "prefix": "Перечисление",
        "kind": "enum",
        "aliases": ("перечисление", "перечисления", "enum", "enums"),
    },
    "Reports": {
        "ru": "Отчеты",
        "prefix": "Отчет",
        "kind": "report_module",
        "aliases": ("отчет", "отчёт", "отчеты", "отчёты", "report", "reports"),
    },
    "DataProcessors": {
        "ru": "Обработки",
        "prefix": "Обработка",
        "kind": "dataprocessor_module",
        "aliases": ("обработка", "обработки", "dataprocessor", "dataprocessors"),
    },
    "InformationRegisters": {
        "ru": "РегистрыСведений",
        "prefix": "РегистрСведений",
        "kind": "info_register_module",
        "aliases": (
            "регистрсведений",
            "регистрысведений",
            "informationregister",
            "informationregisters",
        ),
    },
    "AccumulationRegisters": {
        "ru": "РегистрыНакопления",
        "prefix": "РегистрНакопления",
        "kind": "accum_register_module",
        "aliases": (
            "регистрнакопления",
            "регистрынакопления",
            "accumulationregister",
            "accumulationregisters",
        ),
    },
    "AccountingRegisters": {
        "ru": "РегистрыБухгалтерии",
        "prefix": "РегистрБухгалтерии",
        "kind": "accounting_register_module",
        "aliases": (
            "регистрбухгалтерии",
            "регистрыбухгалтерии",
            "accountingregister",
            "accountingregisters",
        ),
    },
    "CalculationRegisters": {
        "ru": "РегистрыРасчета",
        "prefix": "РегистрРасчета",
        "kind": "calc_register_module",
        "aliases": ("регистррасчета", "регистрырасчета", "calculationregisters"),
    },
    "ChartsOfAccounts": {
        "ru": "ПланыСчетов",
        "prefix": "ПланСчетов",
        "kind": "chart_of_accounts",
        "aliases": ("плансчетов", "планысчетов", "chartsofaccounts"),
    },
    "ChartsOfCharacteristicTypes": {
        "ru": "ПланыВидовХарактеристик",
        "prefix": "ПланВидовХарактеристик",
        "kind": "chart_of_char_types",
        "aliases": ("планвидовхарактеристик", "chartsofcharacteristictypes"),
    },
    "ChartsOfCalculationTypes": {
        "ru": "ПланыВидовРасчета",
        "prefix": "ПланВидовРасчета",
        "kind": "chart_of_calc_types",
        "aliases": ("планвидоврасчета", "chartsofcalculationtypes"),
    },
    "BusinessProcesses": {
        "ru": "БизнесПроцессы",
        "prefix": "БизнесПроцесс",
        "kind": "business_process",
        "aliases": ("бизнеспроцесс", "бизнеспроцессы", "businessprocesses"),
    },
    "Tasks": {
        "ru": "Задачи",
        "prefix": "Задача",
        "kind": "task_module",
        "aliases": ("задача", "задачи", "task", "tasks"),
    },
    "ExchangePlans": {
        "ru": "ПланыОбмена",
        "prefix": "ПланОбмена",
        "kind": "exchange_plan",
        "aliases": ("планобмена", "планыобмена", "exchangeplans"),
    },
    "FilterCriteria": {
        "ru": "КритерииОтбора",
        "prefix": "КритерийОтбора",
        "kind": "filter_criteria",
        "aliases": ("критерийотбора", "критерииотбора", "filtercriteria"),
    },
    "CommonForms": {
        "ru": "ОбщиеФормы",
        "prefix": "ОбщаяФорма",
        "kind": "common_form",
        "aliases": ("общаяформа", "общиеформы", "commonform", "commonforms"),
    },
    "CommonModules": {
        "ru": "ОбщиеМодули",
        "prefix": "ОбщийМодуль",
        "kind": "common_module",
        "aliases": ("общиймодуль", "общиемодули", "commonmodule", "commonmodules"),
    },
    "CommonCommands": {
        "ru": "ОбщиеКоманды",
        "prefix": "ОбщаяКоманда",
        "kind": "command_module",
        "aliases": ("общаякоманда", "общиекоманды", "commoncommands"),
    },
    "CommonAttributes": {
        "ru": "ОбщиеРеквизиты",
        "prefix": "ОбщийРеквизит",
        "kind": "common_attribute",
        "aliases": ("общийреквизит", "общиереквизиты", "commonattributes"),
    },
    "CommonTemplates": {
        "ru": "ОбщиеМакеты",
        "prefix": "ОбщийМакет",
        "kind": "common_template",
        "aliases": ("общиймакет", "общиемакеты", "commontemplates"),
    },
    "CommonPictures": {
        "ru": "ОбщиеКартинки",
        "prefix": "ОбщаяКартинка",
        "kind": "common_picture",
        "aliases": ("общаякартинка", "общиекартинки", "commonpictures"),
    },
    "Constants": {
        "ru": "Константы",
        "prefix": "Константа",
        "kind": "constant",
        "aliases": ("константа", "константы", "constant", "constants"),
    },
    "Roles": {
        "ru": "Роли",
        "prefix": "Роль",
        "kind": "role",
        "aliases": ("роль", "роли", "role", "roles"),
    },
    "Subsystems": {
        "ru": "Подсистемы",
        "prefix": "Подсистема",
        "kind": "subsystem",
        "aliases": ("подсистема", "подсистемы", "subsystem", "subsystems"),
    },
    "SessionParameters": {
        "ru": "ПараметрыСеанса",
        "prefix": "ПараметрСеанса",
        "kind": "session_parameter",
        "aliases": ("параметрсеанса", "параметрысеанса", "sessionparameters"),
    },
    "DefinedTypes": {
        "ru": "ОпределяемыеТипы",
        "prefix": "ОпределяемыйТип",
        "kind": "defined_type",
        "aliases": ("определяемыйтип", "определяемыетипы", "definedtypes"),
    },
    "EventSubscriptions": {
        "ru": "ПодпискиНаСобытия",
        "prefix": "ПодпискаНаСобытие",
        "kind": "event_subscription",
        "aliases": ("подписканасобытие", "подпискинасобытия", "eventsubscriptions"),
    },
    "ScheduledJobs": {
        "ru": "РегламентныеЗадания",
        "prefix": "РегламентноеЗадание",
        "kind": "scheduled_job",
        "aliases": ("регламентноезадание", "регламентныезадания", "scheduledjobs"),
    },
    "FunctionalOptions": {
        "ru": "ФункциональныеОпции",
        "prefix": "ФункциональнаяОпция",
        "kind": "functional_option",
        "aliases": ("функциональнаяопция", "функциональныеопции", "functionaloptions"),
    },
    "FunctionalOptionsParameters": {
        "ru": "ПараметрыФункциональныхОпций",
        "prefix": "ПараметрФункциональныхОпций",
        "kind": "functional_option_param",
        "aliases": ("параметрфункциональныхопций", "functionaloptionsparameters"),
    },
    "SettingsStorages": {
        "ru": "ХранилищаНастроек",
        "prefix": "ХранилищеНастроек",
        "kind": "settings_storage",
        "aliases": ("хранилищенастроек", "хранилищанастроек", "settingsstorages"),
    },
    "Sequences": {
        "ru": "Последовательности",
        "prefix": "Последовательность",
        "kind": "sequence",
        "aliases": ("последовательность", "последовательности", "sequences"),
    },
    "DocumentNumerators": {
        "ru": "Нумераторы",
        "prefix": "Нумератор",
        "kind": "document_numerator",
        "aliases": ("нумератор", "нумераторы", "documentnumerators"),
    },
    "CommandGroups": {
        "ru": "ГруппыКоманд",
        "prefix": "ГруппаКоманд",
        "kind": "command_group",
        "aliases": ("группакоманд", "группыкоманд", "commandgroups"),
    },
    "StyleItems": {
        "ru": "ЭлементыСтиля",
        "prefix": "ЭлементСтиля",
        "kind": "style_item",
        "aliases": ("элементстиля", "элементыстиля", "styleitems"),
    },
    "Languages": {
        "ru": "Языки",
        "prefix": "Язык",
        "kind": "language",
        "aliases": ("язык", "языки", "language", "languages"),
    },
    "HTTPServices": {
        "ru": "HTTPСервисы",
        "prefix": "HTTPСервис",
        "kind": "http_service",
        "aliases": ("httpсервис", "httpсервисы", "httpservices"),
    },
    "WebServices": {
        "ru": "WebСервисы",
        "prefix": "WebСервис",
        "kind": "web_service",
        "aliases": ("webсервис", "webсервисы", "webservices"),
    },
    "WSReferences": {
        "ru": "WSСсылки",
        "prefix": "WSСсылка",
        "kind": "ws_reference",
        "aliases": ("wsссылка", "wsссылки", "wsreferences"),
    },
    "XDTOPackages": {
        "ru": "ПакетыXDTO",
        "prefix": "ПакетXDTO",
        "kind": "xdto_package",
        "aliases": ("пакетxdto", "пакетыxdto", "xdtopackages"),
    },
    "Ext": {
        "ru": "Расширение",
        "prefix": "Ext",
        "kind": "ext",
        "aliases": ("расширение", "ext"),
    },
}

# совместимость: EN → RU
DUMP_FOLDER_RU: dict[str, str] = {
    k: str(v["ru"]) for k, v in DUMP_TYPES.items()
}
DUMP_FOLDER_RU.update(
    {
        "Forms": "Формы",
        "Commands": "Команды",
        "Templates": "Макеты",
    }
)

# prefix 1С для full_name
_FOLDER_PREFIX_1C: dict[str, str] = {
    k: str(v["prefix"]) for k, v in DUMP_TYPES.items()
}

# kind → info (для tool descriptions)
KIND_INFO: dict[str, dict[str, str]] = {
    str(v["kind"]): {
        "ru": str(v["ru"]),
        "folder": en,
        "folder_ru": str(v["ru"]),
        "example": f"{en}/Имя/…",
    }
    for en, v in DUMP_TYPES.items()
}
KIND_INFO.update(
    {
        "form_module": {
            "ru": "Модуль формы объекта",
            "folder": "Forms",
            "folder_ru": "Формы",
            "example": "Catalogs|Documents/…/Forms/ИмяФормы/Ext/Form/Module.bsl",
        },
        "object_module": {
            "ru": "Модуль объекта",
            "folder": "ObjectModule.bsl",
            "folder_ru": "Модуль объекта",
            "example": "…/Ext/ObjectModule.bsl",
        },
        "manager_module": {
            "ru": "Модуль менеджера",
            "folder": "ManagerModule.bsl",
            "folder_ru": "Модуль менеджера",
            "example": "…/Ext/ManagerModule.bsl",
        },
        "recordset_module": {
            "ru": "Модуль набора записей",
            "folder": "RecordSetModule.bsl",
            "folder_ru": "Модуль набора записей",
            "example": "InformationRegisters/…/Ext/RecordSetModule.bsl",
        },
        "command_module": {
            "ru": "Модуль команды",
            "folder": "Commands|CommonCommands",
            "folder_ru": "Команды",
            "example": "…/Commands/Имя/Ext/CommandModule.bsl",
        },
        "register_module": {
            "ru": "Модуль регистра (любой)",
            "folder": "InformationRegisters|AccumulationRegisters|AccountingRegisters",
            "folder_ru": "Регистры",
            "example": "…Registers/Имя/…",
        },
        "module": {
            "ru": "Модуль",
            "folder": "",
            "folder_ru": "модуль",
            "example": "…/Module.bsl",
        },
    }
)

# kind aliases (нормализованные токены → kind)
KIND_ALIASES: dict[str, str] = {
    "form_module": "form_module",
    "form": "form_module",
    "forms": "form_module",
    "форма": "form_module",
    "формы": "form_module",
    "форму": "form_module",
    "форме": "form_module",
    "object_module": "object_module",
    "objectmodule": "object_module",
    "модульобъекта": "object_module",
    "manager_module": "manager_module",
    "managermodule": "manager_module",
    "модульменеджера": "manager_module",
    "recordset_module": "recordset_module",
    "recordsetmodule": "recordset_module",
    "модульнаборазаписей": "recordset_module",
    "register_module": "register_module",
    "register": "register_module",
    "registers": "register_module",
    "регистр": "register_module",
    "регистра": "register_module",
    "регистры": "register_module",
    "command": "command_module",
    "команда": "command_module",
    "команды": "command_module",
    "common_form": "common_form",
    "common_module": "common_module",
}

# folder EN aliases: ru/en token → EN folder name
FOLDER_ALIASES: dict[str, str] = {}
for _en, _meta in DUMP_TYPES.items():
    _k = str(_meta["kind"])
    KIND_ALIASES[_k] = _k
    KIND_ALIASES[_en.lower()] = _k
    KIND_ALIASES[_norm_token(str(_meta["ru"]))] = _k
    KIND_ALIASES[_norm_token(str(_meta["prefix"]))] = _k
    FOLDER_ALIASES[_en.lower()] = _en
    FOLDER_ALIASES[_norm_token(str(_meta["ru"]))] = _en
    FOLDER_ALIASES[_norm_token(str(_meta["prefix"]))] = _en
    for _a in _meta.get("aliases") or ():
        FOLDER_ALIASES[_norm_token(str(_a))] = _en
        KIND_ALIASES[_norm_token(str(_a))] = _k
FOLDER_ALIASES.update({"формы": "Forms", "forms": "Forms"})

# kind → возможные top folders (для фильтра)
KIND_FOLDERS: dict[str, tuple[str, ...]] = {
    str(v["kind"]): (en,) for en, v in DUMP_TYPES.items()
}
KIND_FOLDERS["form_module"] = tuple(DUMP_TYPES.keys())
KIND_FOLDERS["object_module"] = tuple(DUMP_TYPES.keys())
KIND_FOLDERS["manager_module"] = tuple(DUMP_TYPES.keys())
KIND_FOLDERS["recordset_module"] = (
    "InformationRegisters",
    "AccumulationRegisters",
    "AccountingRegisters",
    "CalculationRegisters",
)
KIND_FOLDERS["register_module"] = (
    "InformationRegisters",
    "AccumulationRegisters",
    "AccountingRegisters",
    "CalculationRegisters",
)
KIND_FOLDERS["command_module"] = ("CommonCommands",)

# слова фразы, которые не ищем в имени модуля
_STOP_WORDS = frozenset(
    {
        "а",
        "и",
        "в",
        "во",
        "на",
        "по",
        "с",
        "со",
        "к",
        "от",
        "до",
        "из",
        "у",
        "о",
        "об",
        "про",
        "для",
        "как",
        "что",
        "это",
        "там",
        "где",
        "куда",
        "откуда",
        "туда",
        "сюда",
        "здесь",
        "нужно",
        "посмотри",
        "посмотреть",
        "покажи",
        "найди",
        "найти",
        "есть",
        "данные",
        "попадают",
        "попадает",
        "берутся",
        "берется",
        "логика",
        "работы",
        "модуль",
        "модуля",
        "модуле",
        "код",
        "кода",
        "коде",
        "конфигурации",
        "конфигурация",
        "пожалуйста",
        "можно",
        "нужен",
        "нужна",
        "именно",
        "вообще",
        "the",
        "a",
        "of",
        "to",
        "in",
        "module",
        "code",
        "source",
    }
)

_PROC_RE = re.compile(
    r"^\s*(?:&[^\n]+?\n\s*)*"
    r"(Процедура|Функция|Procedure|Function)\s+([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)",
    re.MULTILINE | re.IGNORECASE,
)
_REGION_RE = re.compile(
    r"^\s*#\s*Область\s+(.+?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def normalize_kind(kind: str) -> str:
    """'общая форма' / 'Documents' / 'документ' → internal kind | ''."""
    raw = (kind or "").strip()
    if not raw:
        return ""
    t = _norm_token(raw)
    if t in KIND_ALIASES:
        return KIND_ALIASES[t]
    compact = _norm_token(raw.replace(" ", ""))
    if compact in KIND_ALIASES:
        return KIND_ALIASES[compact]
    # EN folder name as kind filter
    if raw in DUMP_TYPES:
        return str(DUMP_TYPES[raw]["kind"])
    if t in FOLDER_ALIASES:
        en = FOLDER_ALIASES[t]
        return str(DUMP_TYPES.get(en, {}).get("kind") or "")
    parts = [_norm_token(p) for p in re.split(r"[\s_/\\.-]+", raw) if _norm_token(p)]
    joined = "".join(parts)
    if "общ" in joined or "common" in joined:
        if any(p.startswith("форм") or p in ("form", "forms") for p in parts):
            return "common_form"
        if any(p.startswith("модул") or p in ("module", "modules") for p in parts):
            return "common_module"
    # регистр сведений / накопления / бухгалтерии
    if any(p.startswith("регистр") or p.startswith("register") for p in parts):
        if any("свед" in p or "info" in p for p in parts):
            return "info_register_module"
        if any("накоп" in p or "accum" in p for p in parts):
            return "accum_register_module"
        if any("бух" in p or "account" in p for p in parts):
            return "accounting_register_module"
        return "register_module"
    if any(p.startswith("форм") for p in parts) or "form" in parts:
        return "form_module"
    return KIND_ALIASES.get(t, "")


def normalize_folder_filter(module_filter: str) -> str:
    """'документы' / 'регистр сведений' → 'Documents' / 'InformationRegisters'."""
    raw = (module_filter or "").strip()
    if not raw:
        return ""
    if raw in DUMP_TYPES:
        return raw
    t = _norm_token(raw)
    if t in FOLDER_ALIASES:
        return FOLDER_ALIASES[t]
    compact = _norm_token(raw.replace(" ", ""))
    if compact in FOLDER_ALIASES:
        return FOLDER_ALIASES[compact]
    # многословные: регистр сведений
    parts = [_norm_token(p) for p in re.split(r"[\s_/\\.-]+", raw) if _norm_token(p)]
    joined = "".join(parts)
    if joined in FOLDER_ALIASES:
        return FOLDER_ALIASES[joined]
    if any(p.startswith("регистр") for p in parts) or "register" in parts:
        if any("свед" in p or "info" in p for p in parts):
            return "InformationRegisters"
        if any("накоп" in p or "accum" in p for p in parts):
            return "AccumulationRegisters"
        if any("бух" in p or "account" in p for p in parts):
            return "AccountingRegisters"
    k = normalize_kind(raw)
    if k and k in KIND_FOLDERS:
        folders = KIND_FOLDERS[k]
        if len(folders) == 1:
            return folders[0]
    if k in KIND_INFO:
        folder = KIND_INFO[k].get("folder") or ""
        if folder and "|" not in folder and not folder.endswith(".bsl") and folder in DUMP_TYPES:
            return folder
    return raw


# Имена типовых форм 1С (не отбрасывать как «шум слово форма»)
_FORM_NAME_HINTS = re.compile(
    r"^(форма|form)("
    r"документа|элемента|списка|выбора|записи|группы|констант|"
    r"document|item|list|choice|record|group|"
    r".{3,}"  # ФормаСоздания…, ФормаНастроек…
    r")$",
    re.I,
)

# Документ.Имя.Форма.ФормаДокумента | Справочник.X.Форма.Y
_DOTTED_1C = re.compile(
    r"(?P<cls>документ|справочник|обработка|отчет|отчёт|регистр(?:сведений|накопления)?|"
    r"document|catalog|dataprocessor|report)"
    r"\s*\.\s*"
    r"(?P<obj>[A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)"
    r"(?:\s*\.\s*форма\s*\.\s*(?P<form>[A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*))?",
    re.I,
)


def parse_nl_query(text: str) -> dict[str, Any]:
    """
    Разбор фразы:
      «документ реализация» → kind=document_module, folder=Documents
      «модуль формы Документ.X.Форма.ФормаДокумента» → form + object + form_name
      «общая форма список задач» → CommonForms
    """
    raw = (text or "").strip()
    low = raw.lower().replace("ё", "е")
    kind = ""
    folder = ""
    form_name_wanted = ""
    object_wanted = ""
    want_form_module = False

    # 1C dotted: Документ.<Имя>.Форма.<ИмяФормы>
    for m in _DOTTED_1C.finditer(raw):
        cls = (m.group("cls") or "").lower().replace("ё", "е")
        object_wanted = m.group("obj") or object_wanted
        if m.group("form"):
            form_name_wanted = m.group("form")
            want_form_module = True
        if cls.startswith("документ") or cls == "document":
            folder = folder or "Documents"
            kind = kind or ("form_module" if want_form_module else "document_module")
        elif cls.startswith("справочник") or cls == "catalog":
            folder = folder or "Catalogs"
            kind = kind or ("form_module" if want_form_module else "catalog_module")
        elif cls.startswith("обработ") or cls == "dataprocessor":
            folder = folder or "DataProcessors"
            kind = kind or ("form_module" if want_form_module else "dataprocessor_module")
        elif "отчет" in cls or "отчёт" in cls or cls == "report":
            folder = folder or "Reports"
            kind = kind or ("form_module" if want_form_module else "report_module")

    # «форма документа» / «модуль формы» / «форма списка»
    if re.search(r"форм\w*\s+документ", low) or re.search(
        r"модул\w*\s+форм", low
    ) or re.search(r"\bформ[аыуе]\b", low):
        want_form_module = True
        if not form_name_wanted:
            if re.search(r"форм\w*\s+документ|формадокумента", low):
                form_name_wanted = "ФормаДокумента"
            elif re.search(r"форм\w*\s+списк|формасписка", low):
                form_name_wanted = "ФормаСписка"
            elif re.search(r"форм\w*\s+выбор|формавыбора", low):
                form_name_wanted = "ФормаВыбора"
            elif re.search(r"форм\w*\s+элемент|формаэлемента", low):
                form_name_wanted = "ФормаЭлемента"

    # порядок: сначала более специфичные (регистр сведений до «регистр»)
    if re.search(r"общ\w*\s+форм", low) or "commonform" in _norm_token(low):
        kind = "common_form"
        folder = "CommonForms"
        want_form_module = True
    elif re.search(r"общ\w*\s+модул", low) or "commonmodule" in _norm_token(low):
        kind = "common_module"
        folder = "CommonModules"
    elif re.search(r"регистр\w*\s+свед", low) or "informationregister" in _norm_token(low):
        kind = "info_register_module"
        folder = "InformationRegisters"
    elif re.search(r"регистр\w*\s+накоп", low) or "accumulationregister" in _norm_token(low):
        kind = "accum_register_module"
        folder = "AccumulationRegisters"
    elif re.search(r"регистр\w*\s+бух", low) or "accountingregister" in _norm_token(low):
        kind = "accounting_register_module"
        folder = "AccountingRegisters"
    elif re.search(r"\b(обработк\w*|dataprocessors?)\b", low) and not folder:
        kind = kind or "dataprocessor_module"
        folder = "DataProcessors"
    elif re.search(r"\b(документ\w*|documents?)\b", low) and not folder:
        kind = kind or ("form_module" if want_form_module else "document_module")
        folder = "Documents"
    elif re.search(r"\b(справочник\w*|catalogs?)\b", low) and not folder:
        kind = kind or ("form_module" if want_form_module else "catalog_module")
        folder = "Catalogs"
    elif re.search(r"\b(отч[её]т\w*|reports?)\b", low) and not folder:
        kind = kind or "report_module"
        folder = "Reports"
    elif re.search(r"\b(бизнес[- ]?процесс\w*|businessprocesses?)\b", low):
        kind = "business_process"
        folder = "BusinessProcesses"
    elif re.search(r"\b(http[- ]?сервис\w*|httpservices?)\b", low):
        kind = "http_service"
        folder = "HTTPServices"
    elif re.search(r"\b(регламентн\w*\s+задан|scheduledjobs?)\b", low):
        kind = "scheduled_job"
        folder = "ScheduledJobs"
    elif re.search(r"\b(подписк\w*\s+на\s+событ|eventsubscriptions?)\b", low):
        kind = "event_subscription"
        folder = "EventSubscriptions"
    elif re.search(r"\b(план\w*\s+счет|chartsofaccounts)\b", low):
        kind = "chart_of_accounts"
        folder = "ChartsOfAccounts"
    elif re.search(r"\b(регистр\w*|registers?)\b", low) and not folder:
        kind = kind or "register_module"
    elif want_form_module or re.search(r"\b(форм[аыуе]|form)\b", low):
        kind = kind or "form_module"

    if want_form_module and kind in (
        "document_module",
        "catalog_module",
        "dataprocessor_module",
        "report_module",
        "",
    ):
        kind = "form_module"

    tokens: list[str] = []
    if object_wanted:
        tokens.append(object_wanted)
    if form_name_wanted:
        tokens.append(form_name_wanted)

    for part in re.split(r"[\s,.;:!?\"'()«»]+", raw):
        t = part.strip()
        if not t or len(t) < 2:
            continue
        # развернуть Document.X.Form.Y без regex (точки уже в split)
        tl = t.lower().replace("ё", "е")
        tn = _norm_token(tl)
        if not tn or tn in _STOP_WORDS:
            continue
        if tn in ("общая", "общий", "общие", "общей", "общего", "общую", "common"):
            continue
        # «форма» / «form» — шум; «ФормаДокумента» / «ФормаСписка» — имя формы
        if tn in ("form", "forms", "module", "modules", "форма", "формы", "форму", "форме"):
            continue
        if tn.startswith("форм") or tn.startswith("form"):
            if _FORM_NAME_HINTS.match(t) or len(tn) > 8:
                if t not in tokens:
                    tokens.append(t)
                    if not form_name_wanted:
                        form_name_wanted = t
                    want_form_module = True
            continue
        if tn.startswith("модул"):
            continue
        # только слова КЛАССА (документ/регистр/…), не имена объектов вроде «Задачи»
        if tn in (
            "документ",
            "документа",
            "документы",
            "справочник",
            "справочника",
            "справочники",
            "обработка",
            "обработки",
            "отчет",
            "отчёт",
            "отчета",
            "отчеты",
            "отчёты",
            "регистр",
            "регистра",
            "регистры",
            "сведений",
            "сведения",
            "накопления",
            "бухгалтерии",
            "расчета",
            "http",
            "сервис",
            "сервиса",
            "сервисы",
            "catalogs",
            "documents",
            "dataprocessors",
            "reports",
            "commonforms",
            "commonmodules",
            "informationregisters",
            "accumulationregisters",
        ):
            continue
        if t not in tokens:
            tokens.append(t)

    name_hints = [t for t in tokens if re.search(r"[A-Za-zА-Яа-я]", t) and len(t) >= 3]

    return {
        "raw": raw,
        "kind": kind,
        "folder": folder,
        "tokens": tokens,
        "name_hints": name_hints,
        "form_name": form_name_wanted,
        "object_name": object_wanted,
        "want_form_module": want_form_module,
        "search_blob": " ".join(tokens),
    }


@dataclass
class CodeRoots:
    roots: list[Path]
    source: str


def resolve_code_roots(explicit: str | Path | None = None) -> CodeRoots:
    """
    FS-корни выгрузки. В runtime агента (fallback_filesystem=false) — зеркало коллекции файлов Qdrant
    (vector/files.py), а не диск. Реальная выгрузка — только scripts/prepare и явный fallback.
    """
    try:
        from vector.catalog import runtime_fs_allowed

        # runtime: диск не читаем никогда (даже переданный code_dir) — только Qdrant
        if not runtime_fs_allowed():
            return _mirror_roots()
    except Exception:
        pass

    candidates: list[Path] = []
    notes: list[str] = []

    if explicit:
        candidates.append(Path(explicit))
        notes.append("explicit")
    try:
        from config import load_config

        cfg = load_config()
        # runtime: не подхватываем code_dir автоматически
        from vector.catalog import runtime_fs_allowed as _rfa

        if _rfa():
            cd = (cfg.get("code_dir") or "").strip()
            if cd:
                candidates.append(Path(cd))
                notes.append("config.paths.code_dir")
    except Exception:
        pass
    if notes:  # only if fallback or we already have explicit
        env = (os.environ.get("RAG_CODE_DIR") or "").strip()
        if env:
            candidates.append(Path(env))
            notes.append("RAG_CODE_DIR")
        for rel in (
            "knowledge/код_конфигурации",
            "knowledge/code",
            "code",
            "cf_src",
            "src",
        ):
            candidates.append(PROJECT_ROOT / rel)
            notes.append(rel)
    elif explicit:
        pass
    else:
        return CodeRoots(roots=[], source="qdrant-only")

    roots: list[Path] = []
    used: list[str] = []
    seen: set[str] = set()
    for p, note in zip(candidates, notes):
        try:
            rp = p.expanduser().resolve()
        except Exception:
            continue
        key = str(rp).lower()
        if key in seen:
            continue
        seen.add(key)
        if rp.is_dir() and any(rp.iterdir()) if rp.exists() else False:
            # пустая папка с одним README — не считаем готовым дампом
            has_code = any(
                f.suffix.lower() in CODE_EXTENSIONS or f.name.lower().endswith(".bsl")
                for f in _iter_code_files(rp, max_files=30)
            )
            if has_code:
                roots.append(rp)
                used.append(f"{note}={rp}")
    return CodeRoots(roots=roots, source="; ".join(used) if used else "none")


def _mirror_roots() -> CodeRoots:
    try:
        from vector.files import ensure_mirror

        root = ensure_mirror()
    except Exception:  # noqa: BLE001
        root = None
    if root is None:
        return CodeRoots(roots=[], source="qdrant-only (коллекция файлов не проиндексирована)")
    return CodeRoots(roots=[root], source=f"qdrant-mirror={root}")


def _iter_code_files(root: Path, *, max_files: int = DEFAULT_MAX_FILES_SCAN) -> Iterator[Path]:
    count = 0
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            # пропуск мусора
            dirnames[:] = [
                d
                for d in dirnames
                if d.lower()
                not in (
                    ".git",
                    ".svn",
                    "__pycache__",
                    "node_modules",
                    ".idea",
                    "bin",
                    "obj",
                )
            ]
            for name in filenames:
                low = name.lower()
                p = Path(dirpath) / name
                suf = p.suffix.lower()
                if suf in CODE_EXTENSIONS or low in MODULE_NAME_HINTS or low.endswith("module.bsl"):
                    # .txt только если похоже на модуль
                    if suf == ".txt" and "module" not in low and "модул" not in low:
                        continue
                    yield p
                    count += 1
                    if count >= max_files:
                        return
    except OSError:
        return


def classify_module(path: Path, root: Path | None = None) -> dict[str, str]:
    """
    Классификация по пути выгрузки (любая корневая EN-папка).
    Documents/X/…, DataProcessors/Y/…, InformationRegisters/Z/… — все покрыты.
    """
    try:
        rel = path.relative_to(root) if root else path
    except ValueError:
        rel = path
    rel_s = rel.as_posix() if isinstance(rel, Path) else str(rel).replace("\\", "/")
    parts = [x for x in rel_s.split("/") if x]
    low_parts = [p.lower() for p in parts]
    fname_low = path.name.lower()
    top_en = parts[0] if parts else ""
    type_meta = DUMP_TYPES.get(top_en, {})
    top_ru = str(type_meta.get("ru") or DUMP_FOLDER_RU.get(top_en, top_en))
    prefix = str(type_meta.get("prefix") or top_ru or top_en)
    base_kind = str(type_meta.get("kind") or "module")

    object_name = parts[1] if len(parts) >= 2 and parts[1].lower() not in ("ext", "forms", "commands") else ""
    form_name = ""
    module_role = "module"  # object / manager / form / recordset / command / module

    # роль файла
    if "objectmodule" in fname_low:
        module_role = "object"
    elif "managermodule" in fname_low:
        module_role = "manager"
    elif "recordsetmodule" in fname_low:
        module_role = "recordset"
    elif "commandmodule" in fname_low:
        module_role = "command"
    elif "forms" in low_parts or "формы" in low_parts:
        module_role = "form"
        try:
            fi = low_parts.index("forms")
        except ValueError:
            try:
                fi = low_parts.index("формы")
            except ValueError:
                fi = -1
        if fi >= 0 and fi + 1 < len(parts):
            form_name = parts[fi + 1]
        if fi >= 1:
            object_name = parts[fi - 1]

    # kind: форма/модуль объекта важнее «просто document_module»
    if top_en == "CommonForms":
        kind = "common_form"
        if not object_name and len(parts) >= 2:
            object_name = parts[1]
        form_name = object_name
    elif top_en == "CommonModules":
        kind = "common_module"
        if not object_name and len(parts) >= 2:
            object_name = parts[1]
    elif top_en == "CommonCommands":
        kind = "command_module"
        if not object_name and len(parts) >= 2:
            object_name = parts[1]
    elif module_role == "form":
        kind = "form_module"
    elif module_role == "object":
        kind = "object_module"
    elif module_role == "manager":
        kind = "manager_module"
    elif module_role == "recordset":
        kind = "recordset_module"
    elif module_role == "command":
        kind = "command_module"
    else:
        kind = base_kind

    if not object_name and len(parts) >= 2:
        # HTTPServices/Name/… Ext is often second
        if parts[1].lower() != "ext":
            object_name = parts[1]

    owner = object_name or path.stem
    if form_name and object_name:
        owner = f"{top_en}.{object_name}.{form_name}"

    full_name_1c = ""
    if kind == "common_form" and object_name:
        full_name_1c = f"ОбщаяФорма.{object_name}"
    elif kind == "common_module" and object_name:
        full_name_1c = f"ОбщийМодуль.{object_name}"
    elif kind == "form_module" and object_name and form_name:
        full_name_1c = f"{prefix}.{object_name}.Форма.{form_name}"
    elif object_name and prefix and top_en in DUMP_TYPES:
        full_name_1c = f"{prefix}.{object_name}"

    info = KIND_INFO.get(kind) or {
        "ru": top_ru or kind,
        "folder": top_en,
        "folder_ru": top_ru,
    }
    kind_ru = str(info.get("ru") or top_ru or kind)
    if kind in ("object_module", "manager_module", "recordset_module", "form_module") and top_ru:
        kind_ru = f"{info.get('ru', kind)} ({top_ru})"

    return {
        "kind": kind,
        "kind_ru": kind_ru,
        "owner": owner,
        "object_name": object_name or owner,
        "form_name": form_name,
        "top_folder_en": top_en,
        "top_folder_ru": top_ru,
        "full_name_1c": full_name_1c,
        "rel_path": rel_s,
        "filename": path.name,
        "module_role": module_role,
        "path_explained": (
            f"{kind_ru}: {full_name_1c or owner} "
            f"(файл {rel_s}; dump «{top_en}» = {top_ru})"
        ),
    }


def kind_matches(meta_kind: str, top_folder: str, want: str) -> bool:
    """Фильтр kind учитывает папку и «регистр» = все виды регистров."""
    if not want:
        return True
    mk = meta_kind or ""
    top = top_folder or ""
    if want == mk:
        return True
    if want == "form_module" and mk in ("form_module", "common_form"):
        return True
    if want == "register_module":
        if mk in (
            "register_module",
            "info_register_module",
            "accum_register_module",
            "accounting_register_module",
            "calc_register_module",
            "recordset_module",
        ):
            return True
        if top in KIND_FOLDERS.get("register_module", ()):
            return True
        return False
    # kind привязан к папке: document_module матчит любой модуль под Documents
    folders = KIND_FOLDERS.get(want)
    if folders and top in folders:
        # для form/object/manager — kind более узкий
        if want in (
            "document_module",
            "catalog_module",
            "dataprocessor_module",
            "report_module",
            "info_register_module",
            "accum_register_module",
            "accounting_register_module",
            "common_form",
            "common_module",
        ):
            return True
    if want in KIND_FOLDERS and top in KIND_FOLDERS[want]:
        return mk == want or want.endswith("_module")
    return False


# --- индекс путей (ускорение повторных find/search на ~10k файлов) ---
_INDEX_CACHE: dict[str, tuple[float, list[tuple[Path, Path]]]] = {}


def _index_files(root: Path) -> list[tuple[Path, Path]]:
    """Список (path, root); кэш по mtime корня."""
    try:
        key = str(root.resolve())
        mtime = root.stat().st_mtime
    except OSError:
        return []
    hit = _INDEX_CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    files = [(p, root) for p in _iter_code_files(root)]
    _INDEX_CACHE[key] = (mtime, files)
    return files


def iter_indexed(code_dir: str | Path | None = None) -> Iterator[tuple[Path, Path]]:
    cr = resolve_code_roots(code_dir)
    for root in cr.roots:
        yield from _index_files(root)


def _count_lines_fast(path: Path, cap: int = 50_000) -> int:
    try:
        n = 0
        with path.open("rb") as f:
            for _ in f:
                n += 1
                if n > cap:
                    return n
        return n
    except OSError:
        return -1


def _score_module(
    meta: dict[str, str],
    *,
    tokens: list[str],
    kind: str,
    folder: str,
    form_name: str = "",
    object_name: str = "",
    want_form_module: bool = False,
) -> int:
    """Чем выше — тем релевантнее фразе пользователя."""
    blob = (
        f"{meta.get('rel_path','')} {meta.get('owner','')} "
        f"{meta.get('object_name','')} {meta.get('form_name','')} "
        f"{meta.get('full_name_1c','')}"
    ).lower().replace("ё", "е")
    score = 0
    meta_kind = meta.get("kind") or ""
    meta_form = (meta.get("form_name") or "").lower().replace("ё", "е")
    meta_obj = (meta.get("object_name") or "").lower().replace("ё", "е")
    rel = (meta.get("rel_path") or "").lower()

    if kind and meta_kind == kind:
        score += 50
    elif kind == "form_module" and meta_kind == "form_module":
        score += 50
    elif kind and kind in meta_kind:
        score += 20
    if folder and folder.lower() in rel:
        score += 30

    # точное имя формы (ФормаДокумента ≠ ФормаВыбора)
    fn = (form_name or "").lower().replace("ё", "е")
    if fn:
        if meta_form == fn or fn in meta_form or meta_form in fn:
            score += 120
        elif f"/forms/{fn}/" in rel.replace("\\", "/") or f"/forms/{fn}." in rel.replace(
            "\\", "/"
        ):
            score += 120
        elif meta_kind == "form_module" and meta_form and fn not in meta_form and meta_form not in fn:
            score -= 80  # другая форма того же объекта

    on = (object_name or "").lower().replace("ё", "е")
    if on and on in meta_obj:
        score += 40

    for tok in tokens:
        tl = tok.lower().replace("ё", "е")
        if not tl:
            continue
        if tl in blob:
            score += 15
            if tl in meta_obj:
                score += 25
            if tl in meta_form or meta_form == tl:
                score += 40
            if tl in (meta.get("full_name_1c") or "").lower().replace("ё", "е"):
                score += 10
        else:
            stem = tl[: max(4, len(tl) - 1)]
            if len(stem) >= 4 and stem in blob:
                score += 8
            else:
                score -= 5

    if kind == "common_form" and meta_kind == "common_form":
        score += 10
    if kind == "form_module" or want_form_module:
        if meta_kind == "form_module":
            score += 35
        elif meta_kind in ("command_module",):
            score -= 60
        elif meta_kind in ("manager_module", "object_module") and form_name:
            score -= 40  # просили форму — не manager

    # «основная» форма по умолчанию (если имя формы не указано явно)
    if (want_form_module or kind == "form_module") and not fn:
        if meta_form in ("формадокумента", "формаэлемента"):
            score += 50  # основная форма объекта
        elif meta_form in ("формасписка",):
            score += 10
        elif meta_form in ("формавыбора",):
            score -= 25  # не путать с основной (алфавит: Выбора < Документа)
        elif meta_form.startswith("форма") and meta_form not in (
            "формадокумента",
            "формаэлемента",
            "формасписка",
        ):
            score -= 5  # вспомогательные (создания, настройки…)

    return score


def _module_item(path: Path, root: Path, meta: dict[str, str] | None = None) -> dict[str, Any]:
    meta = meta or classify_module(path, root)
    return {
        "path": str(path.resolve()),
        "rel_path": meta["rel_path"],
        "kind": meta["kind"],
        "kind_ru": meta.get("kind_ru")
        or KIND_INFO.get(meta["kind"], {}).get("ru", "")
        or meta.get("top_folder_ru")
        or "",
        "owner": meta.get("owner") or "",
        "object_name": meta.get("object_name") or "",
        "form_name": meta.get("form_name") or "",
        "full_name_1c": meta.get("full_name_1c") or "",
        "top_folder_en": meta.get("top_folder_en") or "",
        "top_folder_ru": meta.get("top_folder_ru") or "",
        "module_role": meta.get("module_role") or "",
        "path_explained": meta.get("path_explained") or meta["rel_path"],
        "lines": _count_lines_fast(path),
        "root": str(root),
    }


def _read_text_limited(path: Path, max_bytes: int = DEFAULT_MAX_FILE_BYTES) -> str | None:
    try:
        size = path.stat().st_size
        if size > max_bytes:
            # читаем только голову — для outline/search начало часто достаточно
            with path.open("rb") as f:
                raw = f.read(max_bytes)
            return raw.decode("utf-8", errors="replace")
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _clip_line(s: str, max_chars: int = DEFAULT_MAX_LINE_CHARS) -> str:
    s = (s or "").replace("\t", "  ").rstrip()
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1] + "…"


def code_status(code_dir: str | Path | None = None) -> dict[str, Any]:
    """Статус индекса кода. Runtime = только Qdrant (выгрузка для prepare)."""
    from vector.catalog import code_collection_ready, runtime_fs_allowed
    from vector.client import qdrant_status as _qst

    try:
        qdrant_info = _qst()
    except Exception as exc:  # noqa: BLE001
        qdrant_info = {"ok": False, "error": str(exc)}

    cq = code_collection_ready()
    q_ready = bool(cq.get("ready"))
    q_points = int(cq.get("points") or 0)

    if not q_ready:
        return {
            "ok": False,
            "ready": False,
            "source": "qdrant",
            "qdrant": qdrant_info,
            "qdrant_ready": False,
            "qdrant_points": 0,
            "roots": [],
            "filesystem_runtime": runtime_fs_allowed(),
            "hint": cq.get("hint")
            or (
                "Индекс Qdrant пуст/недоступен. "
                "1) Выгрузка → knowledge/код_конфигурации (только для prepare)\n"
                "2) python scripts/prepare_config_for_qdrant.py\n"
                "3) python scripts/index_qdrant.py --recreate\n"
                "Агент код_конфигурации в runtime не читает."
            ),
            "prefer": "Соберите индекс, затем search_code / find_modules.",
        }

    return {
        "ok": True,
        "ready": True,
        "source": "qdrant",
        "roots": [],  # runtime не использует FS
        "qdrant": qdrant_info,
        "qdrant_ready": True,
        "qdrant_points": q_points,
        "collection": cq.get("collection"),
        "filesystem_runtime": runtime_fs_allowed(),
        "note": (
            "Код только из Qdrant. "
            "knowledge/код_конфигурации — выгрузка для prepare/index, не runtime. "
            "find_modules / search_code / read_code / module_outline → Qdrant."
        ),
        "prefer": (
            "search_code(query=…) или find_modules(search=фраза целиком). "
            "path из ответа → module_outline / read_code."
        ),
        "limits": {
            "max_matches": DEFAULT_MAX_MATCHES,
            "read_max_lines": DEFAULT_READ_MAX_LINES,
            "outline_max": DEFAULT_OUTLINE_MAX,
            "max_result_chars": DEFAULT_MAX_RESULT_CHARS,
        },
        # code_dir игнорируется в runtime
        "code_dir_ignored": True,
    }


def find_modules(
    search: str = "",
    *,
    kind: str = "",
    limit: int = 30,
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    Поиск модулей по имени/пути/русской фразе (без тел).

    Runtime: Qdrant only (code_dir игнорируется, если fallback_filesystem=false).
    """
    from vector.catalog import find_modules_qdrant, runtime_fs_allowed

    parsed = parse_nl_query(search or "")
    kind_n = normalize_kind(kind) or parsed.get("kind") or ""
    form_name_w = str(parsed.get("form_name") or "")
    object_name_w = str(parsed.get("object_name") or "")
    want_form = bool(parsed.get("want_form_module"))
    if kind_n == "form_module" or normalize_kind(kind) == "form_module":
        want_form = True
        kind_n = "form_module"

    if not runtime_fs_allowed():
        return find_modules_qdrant(
            search or "",
            kind=kind_n or kind or "",
            limit=limit or 20,
            form_name=form_name_w,
            object_name=object_name_w,
            want_form_module=want_form,
        )

    cr = resolve_code_roots(code_dir)
    if not cr.roots:
        st = code_status(code_dir)
        return {**st, "modules": [], "count": 0}

    folder = parsed.get("folder") or ""
    # «Forms» в KIND_INFO — не корень выгрузки, а сегмент пути; не подставлять как folder_en
    # (иначе parsed выглядит как folder=Forms без Documents)
    if kind_n and not folder and kind_n in KIND_INFO:
        f = KIND_INFO[kind_n].get("folder") or ""
        if (
            f
            and "|" not in f
            and not f.endswith(".bsl")
            and f in DUMP_TYPES  # только корневые EN-папки классов
        ):
            folder = f

    tokens = list(parsed.get("tokens") or [])
    # если search — один идентификатор без NL
    raw_q = (search or "").strip()
    if raw_q and not tokens and not parsed.get("kind"):
        tokens = [raw_q]
    elif raw_q and len(tokens) == 0:
        tokens = [raw_q]

    # если kind задан явно как EN-папка
    if kind and kind in DUMP_TYPES and not folder:
        folder = kind
        kind_n = kind_n or str(DUMP_TYPES[kind]["kind"])
    if kind and not folder:
        nf = normalize_folder_filter(kind)
        if nf in DUMP_TYPES:
            folder = nf

    # form_module + Documents: не режем только Forms — folder Documents ок
    # при want_form фильтруем kind через kind_matches(form_module)
    limit = max(1, min(int(limit or 30), 80))
    scored: list[tuple[int, dict[str, Any]]] = []
    folders_seen: set[str] = set()

    for path, root in iter_indexed(code_dir):
        meta = classify_module(path, root)
        folders_seen.add(meta.get("top_folder_en") or "")
        # при запросе конкретной формы — только form modules (+ common form)
        if want_form or kind_n == "form_module":
            mk = meta.get("kind") or ""
            if mk not in ("form_module", "common_form"):
                continue
        elif kind_n and not kind_matches(
            meta.get("kind") or "", meta.get("top_folder_en") or "", kind_n
        ):
            continue
        if folder and folder.lower() not in (meta.get("rel_path") or "").lower():
            continue

        blob = (
            f"{meta['rel_path']} {meta.get('owner','')} {meta.get('filename','')} "
            f"{meta.get('full_name_1c','')} {meta.get('object_name','')} "
            f"{meta.get('form_name','')} {meta.get('top_folder_ru','')}"
        ).lower()

        if tokens:
            # object token обязателен; form name — если указан
            obj_tokens = [
                t
                for t in tokens
                if not _FORM_NAME_HINTS.match(t)
                and not t.lower().startswith("форма")
                and t.lower() not in ("form",)
            ]
            form_tokens = [
                t
                for t in tokens
                if _FORM_NAME_HINTS.match(t) or (t.lower().startswith("форма") and len(t) > 8)
            ]
            if object_name_w:
                obj_tokens = [object_name_w] + [t for t in obj_tokens if t != object_name_w]
            if form_name_w:
                form_tokens = [form_name_w] + [t for t in form_tokens if t != form_name_w]

            def _hit(tok: str) -> bool:
                tl = tok.lower().replace("ё", "е")
                stem = tl[: max(4, len(tl) - 1)] if len(tl) >= 4 else tl
                return tl in blob or stem in blob

            if obj_tokens and not any(_hit(t) for t in obj_tokens):
                continue
            # если форма указана явно — path/form_name должны совпасть
            if form_tokens or form_name_w:
                fl = (form_name_w or (form_tokens[0] if form_tokens else "")).lower()
                mf = (meta.get("form_name") or "").lower()
                if fl and mf and fl not in mf and mf not in fl and fl not in blob:
                    continue

        sc = _score_module(
            meta,
            tokens=tokens,
            kind=kind_n,
            folder=folder,
            form_name=form_name_w,
            object_name=object_name_w,
            want_form_module=want_form,
        )
        if tokens and sc < 10 and kind_n and not kind_matches(
            meta.get("kind") or "", meta.get("top_folder_en") or "", kind_n
        ):
            continue
        if not tokens and not kind_n and not folder:
            sc = 1
        item = _module_item(path, root, meta)
        item["score"] = sc
        scored.append((sc, item))

    scored.sort(key=lambda x: (-x[0], x[1].get("rel_path") or ""))
    if scored and scored[0][0] >= 40:
        top = scored[0][0]
        # не отсекать «близких» соседей слишком жёстко
        scored = [x for x in scored if x[0] >= max(20, top - 100)]
    items = [it for _, it in scored[:limit]]

    return {
        "ok": True,
        "search": search,
        "parsed": {
            "kind": kind_n,
            "folder_en": folder,
            "folder_ru": DUMP_FOLDER_RU.get(folder, folder),
            "tokens": tokens,
            "form_name": form_name_w,
            "object_name": object_name_w,
            "want_form_module": want_form,
        },
        "kind": kind_n,
        "count": len(items),
        "modules": items,
        "roots": [str(r) for r in cr.roots],
        "folders_scanned": sorted(f for f in folders_seen if f),
        "dump_hint": (
            "Ищем во ВСЕХ папках выгрузки. EN-путь — норма. "
            "Documents=Документы, DataProcessors=Обработки, "
            "InformationRegisters=РегистрыСведений, AccumulationRegisters=РегистрыНакопления, "
            "CommonForms=ОбщиеФормы. Смотри full_name_1c, kind_ru, top_folder_ru."
        ),
        "prefer": (
            "Покажи full_name_1c / kind_ru / path_explained. "
            "Дальше module_outline → search_code → read_code (окно). Не весь файл."
        ),
        "truncated": len(scored) > limit,
        "legend_mini": {
            en: str(DUMP_TYPES[en]["ru"])
            for en in (
                "Documents",
                "DataProcessors",
                "InformationRegisters",
                "AccumulationRegisters",
                "Catalogs",
                "CommonForms",
                "CommonModules",
                "Reports",
            )
            if en in DUMP_TYPES
        },
    }


def module_outline(
    path: str = "",
    module: str = "",
    *,
    max_symbols: int = DEFAULT_OUTLINE_MAX,
    name_filter: str = "",
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    Только сигнатуры Процедура/Функция (+ области) — без тел.
    name_filter — подстрока имени (печать/макет/…) чтобы не терять хвост модуля.
    Runtime: Qdrant (не FS).
    """
    from vector.catalog import module_outline_from_qdrant, runtime_fs_allowed

    if not runtime_fs_allowed():
        target = (path or module or "").strip()
        # path может быть abs Windows — свести к rel source_path
        if target and ("код_конфигурации" in target or "\\Documents\\" in target or "/Documents/" in target):
            for marker in ("код_конфигурации\\", "код_конфигурации/", "код_конфигурации"):
                if marker in target:
                    target = target.split(marker, 1)[-1].lstrip("\\/")
                    break
            target = target.replace("\\", "/")
        # нотация 1С / неполный путь выгрузки → реальный source_path (без подмены объекта)
        try:
            from tools.path_norm import parse_code_path

            if target and parse_code_path(target) is not None:
                rp, err = resolve_qdrant_code_path(target)
                if err is not None:
                    return err
                target = rp
        except Exception:
            pass
        return module_outline_from_qdrant(
            target,
            max_symbols=max_symbols or DEFAULT_OUTLINE_MAX,
            name_filter=name_filter or "",
        )

    target = _resolve_module_path(path=path, module=module, code_dir=code_dir)
    if target.get("error"):
        return target

    p = Path(target["path"])
    text = _read_text_limited(p)
    if text is None:
        return {"error": f"Не удалось прочитать: {p}"}

    max_symbols = max(5, min(int(max_symbols or DEFAULT_OUTLINE_MAX), 150))
    symbols: list[dict[str, Any]] = []
    for m in _PROC_RE.finditer(text):
        line_no = text.count("\n", 0, m.start()) + 1
        symbols.append(
            {
                "line": line_no,
                "type": m.group(1),
                "name": m.group(2),
            }
        )
        if len(symbols) >= max_symbols:
            break

    regions: list[dict[str, Any]] = []
    for m in _REGION_RE.finditer(text):
        line_no = text.count("\n", 0, m.start()) + 1
        regions.append({"line": line_no, "name": m.group(1).strip()[:80]})
        if len(regions) >= 40:
            break

    total_lines = text.count("\n") + (1 if text else 0)
    truncated_file = p.stat().st_size > DEFAULT_MAX_FILE_BYTES if p.exists() else False

    return {
        "ok": True,
        "path": str(p.resolve()),
        "rel_path": target.get("rel_path", p.name),
        "kind": target.get("kind", ""),
        "total_lines_est": total_lines,
        "file_truncated": truncated_file,
        "symbols_count": len(symbols),
        "symbols": symbols,
        "regions": regions[:20],
        "prefer": (
            "Выбери нужную процедуру → read_code(path, start_line=line, max_lines=40..80). "
            "Не читай модуль целиком (тысячи строк не влезут в 32k)."
        ),
        "truncated": len(symbols) >= max_symbols,
    }


def search_code(
    query: str,
    *,
    module_filter: str = "",
    kind: str = "",
    max_matches: int = DEFAULT_MAX_MATCHES,
    context_lines: int = DEFAULT_CONTEXT_LINES,
    case_sensitive: bool = False,
    regex: bool = False,
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    Поиск по коду конфигурации.

    1) Qdrant (семантика + payload) — основной путь после index_qdrant.py
    2) Grep по выгрузке BSL — fallback / regex=True

    module_filter / kind: «общие формы», CommonForms, common_form.
    query — что искать ВНУТРИ кода (имя формы → find_modules).
    """
    q = (query or "").strip()
    if not q:
        return {
            "error": "Пустой query",
            "hint": (
                "search_code(query='ДинамическийСписок'|'ТекстЗапроса'|'ОсновнаяТаблица'). "
                "Имя формы: сначала find_modules(search='общая форма список задач')."
            ),
        }

    max_matches = max(1, min(int(max_matches or DEFAULT_MAX_MATCHES), 25))
    context_lines = max(0, min(int(context_lines or 0), 5))

    from vector.catalog import runtime_fs_allowed, search_code_qdrant_only

    # --- Runtime: только Qdrant (hybrid / exact / regex-lite) ---
    if not runtime_fs_allowed():
        return search_code_qdrant_only(
            q,
            module_filter=module_filter or "",
            max_matches=max_matches,
            match_mode="exact" if regex else "",
            regex=bool(regex),
            case_sensitive=bool(case_sensitive),
        )

    # --- legacy FS path (только если fallback_filesystem=true) ---
    _qdrant_err = ""
    if not regex:
        try:
            from config import load_config
            from vector.search import search_code_vectors

            cfg = load_config()
            if (cfg.get("qdrant") or {}).get("enabled", True):
                vec = search_code_vectors(
                    q,
                    limit=max_matches,
                    module_filter=module_filter or "",
                    cfg=cfg,
                )
                if vec.get("ok") and vec.get("matches"):
                    return {
                        "ok": True,
                        "source": "qdrant",
                        "query": q,
                        "module_filter": module_filter or "",
                        "kind": kind or "",
                        "matches": vec["matches"],
                        "count": len(vec["matches"]),
                        "collection": vec.get("collection"),
                        "prefer": vec.get("prefer")
                        or "Ответь по matches[].snippet/location. read_code(path=…).",
                    }
        except Exception as exc:  # noqa: BLE001
            _qdrant_err = str(exc)

    cr = resolve_code_roots(code_dir)
    if not cr.roots:
        st = code_status(code_dir)
        if _qdrant_err:
            st["qdrant_error"] = _qdrant_err
        return st

    # NL в filter: «общая форма <имя>» → folder + tokens
    filt_raw = (module_filter or "").strip()
    parsed_f = parse_nl_query(filt_raw) if filt_raw else {}
    filt = normalize_folder_filter(filt_raw)
    if not filt and parsed_f.get("folder"):
        filt = parsed_f["folder"]
    # если filter — имя объекта, оставляем как подстроку пути
    if filt_raw and filt == filt_raw and parsed_f.get("tokens"):
        pass
    filt_l = filt.lower() if filt else ""
    extra_path_tokens = [
        t.lower()
        for t in (parsed_f.get("tokens") or [])
        if len(t) >= 3 and t.lower() not in (filt_l,)
    ]

    kind_n = normalize_kind(kind) or (parsed_f.get("kind") if parsed_f else "") or ""
    # если query похож на NL про форму — подсказка, но всё равно ищем как код
    parsed_q = parse_nl_query(q)

    try:
        if regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            pattern = re.compile(q, flags)
            needle = ""
        else:
            pattern = None
            needle = q if case_sensitive else q.lower()
    except re.error as e:
        return {"error": f"Некорректный regex: {e}"}

    matches: list[dict[str, Any]] = []
    files_scanned = 0
    files_hit = 0
    stopped_early = False

    for path, root in iter_indexed(code_dir):
        meta = classify_module(path, root)
        blob = (
            f"{meta['rel_path']} {meta.get('owner','')} "
            f"{meta.get('object_name','')} {meta.get('top_folder_ru','')}"
        ).lower()
        if filt_l and filt_l not in blob:
            continue
        if extra_path_tokens and not any(t in blob for t in extra_path_tokens):
            continue
        if kind_n and not kind_matches(
            meta.get("kind") or "", meta.get("top_folder_en") or "", kind_n
        ):
            continue

        files_scanned += 1
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue

        file_hits = 0
        for i, line in enumerate(lines):
            hay = line if case_sensitive else line.lower()
            ok = bool(pattern.search(line)) if pattern is not None else (needle in hay)
            if not ok:
                continue
            file_hits += 1
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            snippet_lines = []
            for j in range(start, end):
                mark = ">" if j == i else " "
                snippet_lines.append(
                    f"{mark}{j + 1}:{_clip_line(lines[j].rstrip(chr(10)).rstrip(chr(13)))}"
                )
            snippet = "\n".join(snippet_lines)
            if len(snippet) > DEFAULT_MAX_SNIPPET_CHARS:
                snippet = snippet[: DEFAULT_MAX_SNIPPET_CHARS - 1] + "…"

            matches.append(
                {
                    "path": str(path.resolve()),
                    "rel_path": meta["rel_path"],
                    "kind": meta["kind"],
                    "kind_ru": meta.get("kind_ru") or "",
                    "top_folder_en": meta.get("top_folder_en") or "",
                    "top_folder_ru": meta.get("top_folder_ru") or "",
                    "owner": meta.get("owner") or "",
                    "full_name_1c": meta.get("full_name_1c") or "",
                    "path_explained": meta.get("path_explained") or meta["rel_path"],
                    "line": i + 1,
                    "text": _clip_line(line),
                    "snippet": snippet,
                }
            )
            if len(matches) >= max_matches:
                stopped_early = True
                break
        if file_hits:
            files_hit += 1
        if stopped_early:
            break

    out: dict[str, Any] = {
        "ok": True,
        "query": query,
        "module_filter": module_filter or "",
        "module_filter_resolved": filt or "",
        "kind": kind_n or kind or "",
        "matches_count": len(matches),
        "files_scanned": files_scanned,
        "files_hit": files_hit,
        "matches": matches,
        "roots": [str(r) for r in cr.roots],
        "dump_hint": (
            "rel_path EN-норма. full_name_1c/kind_ru — по-русски. "
            "CommonForms=ОбщиеФормы."
        ),
        "prefer": (
            "Покажи full_name_1c + line + snippet. "
            "Дальше read_code(path, start_line=line-5, max_lines=50). "
            "НЕ целый модуль. Если искали имя формы — лучше find_modules."
        ),
        "truncated": stopped_early,
        "note": (
            "Контекст 32k: сниппеты урезаны. Уточни query/module_filter."
            if stopped_early
            else ""
        ),
    }
    if parsed_q.get("kind") and parsed_q.get("tokens") and files_hit == 0:
        out["hint"] = (
            "Похоже, query — фраза про форму, а не фрагмент кода. "
            f"Сначала find_modules(search={query!r}), "
            "потом search_code(query='ДинамическийСписок'|'Запрос', module_filter=path)."
        )
    # страховка по размеру JSON
    raw_len = len(str(out))
    if raw_len > DEFAULT_MAX_RESULT_CHARS and matches:
        keep = max(3, len(matches) // 2)
        while keep >= 3 and len(str({**out, "matches": matches[:keep]})) > DEFAULT_MAX_RESULT_CHARS:
            keep -= 1
        out["matches"] = matches[:keep]
        out["matches_count"] = len(out["matches"])
        out["truncated"] = True
        out["note"] = (out.get("note") or "") + " Урезано matches под budget tool result."
    return out


def _normalize_qdrant_path(path: str) -> str:
    """D:\\...\\код_конфигурации\\Documents\\... → Documents/..."""
    t = (path or "").strip().replace("\\", "/")
    for marker in (
        "код_конфигурации/",
        "knowledge/код_конфигурации/",
    ):
        if marker in t:
            t = t.split(marker, 1)[-1]
    # strip drive absolute leftovers that still contain Documents/
    for root in ("Documents/", "Catalogs/", "CommonModules/", "DataProcessors/", "Reports/"):
        if root in t and not t.startswith(root):
            idx = t.find(root)
            if idx >= 0:
                t = t[idx:]
                break
    return t.lstrip("/")


def _guarded_name_lookup(raw: str) -> tuple[str, dict[str, Any] | None]:
    """Имя модуля без класса («ОбщегоНазначения»): только результат с тем же именем объекта."""
    from vector.catalog import find_modules_qdrant

    try:
        found = find_modules_qdrant(raw, limit=10)
    except Exception:
        found = {}
    mods = list(found.get("modules") or [])
    raw_l = raw.lower().replace("ё", "е")
    toks = set(re.findall(r"[a-zа-я0-9_]+", raw_l))
    for m in mods:
        p = str(m.get("path") or "").replace("\\", "/")
        parts = p.split("/")
        on = str(m.get("object_name") or (parts[1] if len(parts) > 1 else "")).lower().replace("ё", "е")
        on = re.sub(r"\.(xml|bsl)$", "", on)
        if on and on in toks:
            return p, None
    return "", {
        "ok": False,
        "error": f"В Qdrant нет path/модуля: {raw}",
        "source": "qdrant",
        "requested_path": raw,
        "did_you_mean": [str(m.get("path") or "") for m in mods[:5] if m.get("path")],
        "hint": "Возьми точный path из did_you_mean / find_modules. Не подставляй код другого объекта.",
    }


def resolve_qdrant_code_path(raw: str) -> tuple[str, dict[str, Any] | None]:
    """
    Общая нормализация path для tools кода (read_code / inspect_code / module_outline).
    Нотация 1С и «неполные» пути выгрузки → реальный source_path в Qdrant.
    Возврат: (path, None) или ("", payload ok:false с did_you_mean). Чужой объект не отдаём.
    """
    from tools.path_norm import not_found_payload, resolve_code_path

    raw = (raw or "").strip()
    if not raw:
        return "", {"ok": False, "error": "Нужен path (source_path из find_modules / search_code)", "source": "qdrant"}
    res = resolve_code_path(raw)
    if res.get("path"):
        return str(res["path"]), None
    if res.get("spec"):
        return "", not_found_payload(raw, res)
    # не путь объекта (просто имя/фраза) — только совпадение по имени объекта
    return _guarded_name_lookup(_normalize_qdrant_path(raw))


def read_code(
    path: str = "",
    *,
    module: str = "",
    start_line: int = 1,
    max_lines: int = DEFAULT_READ_MAX_LINES,
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Прочитать окно строк модуля (не весь файл). Runtime: Qdrant."""
    from vector.catalog import read_module, runtime_fs_allowed
    from tools.epf_inspect import read_external_module

    # окно ≤120 строк: модель иначе читает модуль целиком по 200 строк и переполняет контекст
    max_lines = max(1, min(int(max_lines or DEFAULT_READ_MAX_LINES), 120))
    ext = read_external_module(path or module or "", start_line or 1, max_lines)
    if ext is not None:
        return _numbered_content(ext)

    if not runtime_fs_allowed():
        raw = (path or module or "").strip()
        sp, err = resolve_qdrant_code_path(raw)
        if err is not None:
            return err
        out = read_module(
            sp,
            start_line=start_line or 1,
            max_lines=max_lines or DEFAULT_READ_MAX_LINES,
        )
        if isinstance(out, dict) and out.get("ok") and sp != _normalize_qdrant_path(raw):
            out["resolved_from"] = raw
        return _numbered_content(out)

    target = _resolve_module_path(path=path, module=module, code_dir=code_dir)
    if target.get("error"):
        return target

    p = Path(target["path"])
    start_line = max(1, int(start_line or 1))
    max_lines = max(1, min(int(max_lines or DEFAULT_READ_MAX_LINES), 120))

    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            # 1-based window
            lines_out: list[str] = []
            total = 0
            for i, line in enumerate(f, start=1):
                total = i
                if i < start_line:
                    continue
                if i >= start_line + max_lines:
                    # дочитаем total? для больших файлов total будет start+max — ок, посчитаем отдельно
                    # быстрый подсчёт остатка дорого — оставим est
                    for _ in f:
                        total += 1
                        if total > start_line + 20000:
                            break
                    break
                num = f"{i}:"
                lines_out.append(num + _clip_line(line.rstrip("\n").rstrip("\r"), 300))
    except OSError as e:
        return {"error": str(e), "path": str(p)}

    content = "\n".join(lines_out)
    if len(content) > DEFAULT_MAX_RESULT_CHARS - 400:
        content = content[: DEFAULT_MAX_RESULT_CHARS - 420] + "\n…[truncated_for_32k]"

    end_line = start_line + len(lines_out) - 1 if lines_out else start_line - 1
    return {
        "ok": True,
        "path": str(p.resolve()),
        "rel_path": target.get("rel_path", p.name),
        "kind": target.get("kind", ""),
        "start_line": start_line,
        "end_line": end_line,
        "max_lines": max_lines,
        "total_lines_est": total,
        "content": content,
        "prefer": (
            "Процитируй только нужные строки. Если мало — сдвинь start_line / увеличь max_lines (≤120). "
            "Для обзора всего модуля: module_outline, не read_code с max_lines=10000."
        ),
        "truncated": total > end_line,
    }


def _numbered_content(out: Any) -> Any:
    """lines[{line,text}] (+ дубль text) → один content «N: код»: вдвое меньше токенов, окно влезает в бюджет."""
    if not (isinstance(out, dict) and out.get("ok") and out.get("lines")):
        return out
    out = dict(out)
    rows = out.pop("lines")
    out.pop("text", None)
    out["content"] = "\n".join(f"{r.get('line')}: {r.get('text', '')}" for r in rows)
    return out


def _resolve_module_path(
    *,
    path: str = "",
    module: str = "",
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    p_raw = (path or "").strip()
    if p_raw:
        p = Path(p_raw)
        if p.is_file():
            meta = classify_module(p, p.parent)
            return {
                "path": str(p.resolve()),
                "rel_path": meta.get("rel_path", p.name),
                "kind": meta.get("kind", ""),
            }
        # относительный от roots
        cr = resolve_code_roots(code_dir)
        for root in cr.roots:
            cand = (root / p_raw).resolve()
            if cand.is_file():
                meta = classify_module(cand, root)
                return {
                    "path": str(cand),
                    "rel_path": meta["rel_path"],
                    "kind": meta["kind"],
                }
        return {
            "error": f"Файл не найден: {p_raw}",
            "hint": "Возьми path из find_modules / search_code.matches[].path",
        }

    mod = (module or "").strip()
    if not mod:
        return {
            "error": "Нужен path или module",
            "hint": "find_modules(search='<имя>') → path",
        }

    found = find_modules(mod, limit=5, code_dir=code_dir)
    mods = found.get("modules") or []
    if not mods:
        if not found.get("ready", True) and found.get("hint"):
            return found
        return {
            "error": f"Модуль не найден по имени: {mod}",
            "hint": "find_modules(search=…) или search_code",
        }
    best = mods[0]
    return {
        "path": best["path"],
        "rel_path": best.get("rel_path", ""),
        "kind": best.get("kind", ""),
        "resolved_from": mod,
        "candidates": [
            {"path": m["path"], "rel_path": m.get("rel_path"), "kind": m.get("kind")}
            for m in mods[:5]
        ],
    }


# Паттерны «откуда данные» в формах/модулях 1С
_DATA_SOURCE_PATTERNS = (
    "ДинамическийСписок",
    "DynamicList",
    "ОсновнаяТаблица",
    "MainTable",
    "ТекстЗапроса",
    "QueryText",
    "Запрос.Текст",
    "Новый Запрос",
    "УстановитьТекстЗапроса",
    "ПроизвольныйЗапрос",
    "CustomQuery",
    "ВЫБРАТЬ",
    "РегистрСведений",
    "РегистрНакопления",
    "Справочник.",
    "Документ.",
    "ОбщегоНазначения",
)

# Вызов ОбщийМодуль.Метод( — чтобы подтянуть связанный common module
_COMMON_CALL_RE = re.compile(
    r"(?<![\wА-Яа-яЁё])"
    r"([А-ЯA-Z][А-Яа-яA-Za-zЁё0-9_]{4,})"
    r"\."
    r"([А-ЯA-Za-zа-яёЁ_][А-Яа-яA-Za-zЁё0-9_]*)"
    r"\s*\(",
)


def _camel_parts(name: str) -> list[str]:
    """РасчетСебестоимостиТоваров → ['Расчет', 'Себестоимости', 'Товаров']."""
    try:
        from tools.name_match import camel_parts

        return camel_parts(name)
    except Exception:
        s = (name or "").strip()
        if not s:
            return []
        return [
            p
            for p in re.findall(
                r"[A-ZА-ЯЁ][a-zа-яё0-9_]*|[A-ZА-ЯЁ]+(?![a-zа-яё])|[a-zа-яё0-9]+",
                s,
            )
            if p
        ]


def object_name_variants(stem: str, *, max_variants: int = 12) -> list[str]:
    """
    Варианты имён для поиска common modules / path.
    РасчетСебестоимостиТоваров → РасчетСебестоимости.

    Только морфология CamelCase (отрезать хвостовые части), БЕЗ доменных
    списков («Товаров/НДС/…» как whitelist).
    """
    raw = (stem or "").strip()
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def _add(x: str) -> None:
        x = (x or "").strip()
        # короткие стемы («Расчет») дают шум в find_modules
        if not x or len(x) < 8:
            return
        if x in seen:
            return
        seen.add(x)
        out.append(x)

    _add(raw)
    parts = _camel_parts(raw)
    if parts:
        # progressive prefixes: AB, ABC (не одиночный A)
        acc = ""
        for i, p in enumerate(parts):
            acc = f"{acc}{p}"
            if i >= 1:  # минимум 2 camel-части
                _add(acc)
        # отрезать 1..2 хвостовых Camel-части (универсально, не «Товаров»)
        if len(parts) >= 2:
            _add("".join(parts[:-1]))
        if len(parts) >= 3:
            _add("".join(parts[:-2]))
            mid = "".join(parts[1:])
            if len(mid) >= 8:
                _add(mid)

    return out[: max(1, int(max_variants or 12))]


# Common modules-шум: инфраструктура БСП (по *паттернам имени*, не по CF)
_NOISE_COMMON_EXACT = frozenset(
    {
        "общегоназначения",
        "общегоназначенияклиент",
        "общегоназначенияклиентсервер",
        "общегоназначенияповтисп",
        "общегоназначениявызываемый",
        "строковыефункцииклиентсервер",
        "пользователи",
        "пользователисервер",
        "стандартныеподсистемысервер",
        "обновлениеинформационнойбазы",
        "созданиенаоснованиипереопределяемый",
        "управлениедоступностью",
    }
)
# префиксы/суффиксы: *УТ, *ERP, *БП — варианты одной инфраструктуры в разных CF
_NOISE_COMMON_PREFIXES = (
    "общегоназначения",
    "проведениесервер",
    "вариантыотчетов",
    "стандартныеподсистемы",
    "строковыефункции",
    "пользователи",
    "обновлениеинформационнойбазы",
)
def _is_noise_common_path(path: str) -> bool:
    p = (path or "").replace("\\", "/").lower().replace("ё", "е")
    if "commonmodules/" not in p:
        return False
    parts = p.split("/")
    if len(parts) < 2:
        return False
    name = parts[1]
    if name in _NOISE_COMMON_EXACT:
        return True
    # ПроведениеСервер / ПроведениеСерверУТ / … — инфраструктура проведения
    if name.startswith("проведениесервер"):
        return True
    if any(name.startswith(pref) for pref in _NOISE_COMMON_PREFIXES):
        # ОбщегоНазначения* / ВариантыОтчетов* — шум для «запросов документа»
        if name.startswith("вариантыотчетов") or name.startswith("общегоназначения"):
            return True
    if name.endswith("переопределяемый") or name.endswith("повтисп"):
        if any(x in name for x in ("отчет", "печат", "вариант", "создание", "проведен")):
            return True
    # суффиксы бренда CF (УТ/ERP/БП/КА) на инфраструктурных модулях
    if re.search(r"(ут|erp|бп|ка)$", name) and any(
        name.startswith(p) for p in ("проведение", "общегоназнач", "вариантыотчет")
    ):
        return True
    return False


def _path_stem_affinity(path: str, stem_variants: list[str]) -> int:
    """Насколько path близок к stem документа (выше = лучше)."""
    low = (path or "").replace("\\", "/").lower().replace("ё", "е")
    best = 0
    for v in stem_variants:
        vl = v.lower().replace("ё", "е")
        if not vl or len(vl) < 6:
            continue
        if f"/{vl}/" in low or f"/{vl}." in low:
            best = max(best, 100 + len(vl))
        elif vl in low:
            best = max(best, 50 + len(vl) // 2)
    return best


def _related_common_module_names(text: str, *, limit: int = 12) -> list[str]:
    """Имена модулей из вызовов X.Y( в тексте Object/Manager module."""
    if not text:
        return []
    # служебные / платформенные — не common modules с запросами
    skip = {
        "строка",
        "число",
        "дата",
        "булево",
        "массив",
        "структура",
        "соответствие",
        "таблицазначений",
        "запрос",
        "результатзапроса",
        "выборка",
        "этотОбъект".lower(),
        "этотОбъект",
        "documents",
        "catalogs",
        "enums",
        "constants",
        "informationregisters",
        "accumulationregisters",
        "метаданные",
        "metadata",
        "общегоназначенияклиентсервер",
        "обновлениеинформационнойбазы",
        "пользователи",
        "стандартныеподсистемысервер",
    }
    found: list[str] = []
    seen: set[str] = set()
    for m in _COMMON_CALL_RE.finditer(text):
        mod = m.group(1)
        ml = mod.lower().replace("ё", "е")
        if ml in skip or ml.startswith("нстр") or len(mod) < 5:
            continue
        # инфраструктура проведения/БСП — не бизнес common module
        if ml.startswith("проведениесервер") or ml.startswith("общегоназначения"):
            continue
        # PascalCase-ish: хотя бы одна заглавная
        if mod[0].islower():
            continue
        if mod in seen:
            continue
        seen.add(mod)
        found.append(mod)
        if len(found) >= limit:
            break
    return found


# Паттерны рисков: блокировки / транзакции / тяжёлые запросы / циклы
_RISK_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("БлокировкаДанных", "managed_lock", "управляемая блокировка"),
    ("УстановитьБлокировку", "managed_lock", "установка блокировки"),
    ("ЗаблокироватьДанные", "edit_lock", "блокировка для редактирования"),
    ("БлокироватьДляИзменения", "lock_for_change", "БлокироватьДляИзменения"),
    ("ДЛЯ ИЗМЕНЕНИЯ", "for_update", "ДЛЯ ИЗМЕНЕНИЯ в запросе"),
    ("НачатьТранзакцию", "transaction", "начало транзакции"),
    ("ЗафиксироватьТранзакцию", "transaction", "фиксация транзакции"),
    ("ОтменитьТранзакцию", "transaction", "откат транзакции"),
    ("Новый Запрос", "query", "создание запроса"),
    ("Запрос.Текст", "query", "текст запроса"),
    ("Выполнить(", "query_exec", "выполнение (запрос/метод)"),
    ("Для Каждого", "loop", "цикл Для Каждого"),
    ("Пока ", "loop", "цикл Пока"),
    ("ЗаписатьНаборыЗаписей", "posting_write", "запись наборов движений"),
    ("Движения.", "movements", "работа с Движения"),
    ("Записать(", "write", "запись объекта/набора"),
)


def scan_module_risks(
    text: str,
    *,
    path: str = "",
    max_hits: int = 40,
) -> list[dict[str, Any]]:
    """Статический скан модуля на блокировки / транзакции / циклы / запросы."""
    if not text:
        return []
    lines = text.splitlines()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    lim = max(5, min(int(max_hits or 40), 80))
    for i, ln in enumerate(lines):
        if len(out) >= lim:
            break
        raw = ln.strip()
        if not raw or raw.startswith("//") or raw.startswith("|"):
            continue
        low = raw.lower().replace("ё", "е")
        for pat, kind, title in _RISK_PATTERNS:
            if pat.lower().replace("ё", "е") in low or pat in raw:
                key = f"{path}:{i+1}:{kind}"
                if key in seen:
                    continue
                seen.add(key)
                # эвристика: запрос внутри/рядом с циклом
                near_loop = False
                if kind in ("query", "query_exec", "query_text", "write"):
                    window = "\n".join(lines[max(0, i - 15) : i + 1])
                    if re.search(r"Для\s+Каждого|Пока\s+", window, re.I):
                        near_loop = True
                out.append(
                    {
                        "line": i + 1,
                        "kind": kind,
                        "title": title + (" (в/у цикла)" if near_loop else ""),
                        "pattern": pat,
                        "text": raw[:220],
                        "path": path,
                        "near_loop": near_loop,
                        "severity": (
                            "high"
                            if kind in ("managed_lock", "for_update", "edit_lock")
                            or near_loop
                            else "medium"
                            if kind in ("transaction", "posting_write", "query")
                            else "low"
                        ),
                    }
                )
                break
    return out


# фраза о событии → обработчики платформы (имена событий 1С, не объекты CF)
_EVENT_HANDLERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"при\s+записи|перед\s+записью|после\s+записи|при\s+сохранени|когда\s+записыва", ("ПередЗаписью", "ПриЗаписи", "ОбработкаПроверкиЗаполнения")),
    (r"при\s+проведени|перед\s+проведени|когда\s+провод|проведение\s+документа", ("ОбработкаПроведения", "ОбработкаПроверкиЗаполнения", "ПередЗаписью")),
    (r"(?:на|по)\s+основани|при\s+заполнени|ввод\w*\s+на\s+основ|заполнени\w*\s+по\s+основ", ("ОбработкаЗаполнения",)),
    (r"при\s+удалени|перед\s+удалени", ("ПередУдалением",)),
    (r"при\s+копировани", ("ПриКопировании",)),
    (r"при\s+отмене\s+проведени|отмена\s+проведени", ("ОбработкаУдаленияПроведения",)),
)
_FOLDER_BY_CLASS = {"Документ": "Documents", "Справочник": "Catalogs", "РегистрСведений": "InformationRegisters",
                    "РегистрНакопления": "AccumulationRegisters", "ПланВидовХарактеристик": "ChartsOfCharacteristicTypes",
                    "БизнесПроцесс": "BusinessProcesses", "Задача": "Tasks"}


def _object_event_handlers(req: str, *, max_lines: int = 45) -> dict[str, Any] | None:
    pl = (req or "").lower().replace("ё", "е")
    if re.search(r"форм|кнопк|запрос\w*\s+в\s+цикл|блокир|тормоз", pl):
        return None
    handlers: tuple[str, ...] = ()
    for pat, names in _EVENT_HANDLERS:
        if re.search(pat, pl):
            handlers = names
            break
    if not handlers:
        return None
    from tools.form_map import _ui_object_full_name
    from tools.name_match import resolve_object_full_name
    from vector.catalog import assemble_module_text, get_chunks_by_source_path

    fn = resolve_object_full_name(req, strict=True) or _ui_object_full_name(req)
    if not fn or "." not in fn:
        return None
    cls, name = fn.split(".", 1)
    folder = _FOLDER_BY_CLASS.get(cls)
    if not folder:
        return None
    out_procs: list[dict[str, Any]] = []
    paths_seen: list[str] = []
    for role in ("ObjectModule", "RecordSetModule", "ManagerModule"):
        path = f"{folder}/{name}/Ext/{role}.bsl"
        text = assemble_module_text(get_chunks_by_source_path(path))
        if not text:
            continue
        paths_seen.append(path)
        lines = text.splitlines()
        for h in handlers:
            for i, ln in enumerate(lines):
                if re.match(rf"\s*(?:Процедура|Функция)\s+{h}\s*\(", ln):
                    body = []
                    for j in range(i, min(len(lines), i + 400)):
                        body.append(lines[j])
                        if re.match(r"\s*Конец(?:Процедуры|Функции)", lines[j]):
                            break
                    calls = sorted(set(re.findall(r"([A-ZА-ЯЁ][\wА-Яа-яЁё]+(?:\.[A-ZА-ЯЁ][\wА-Яа-яЁё]+)?)\s*\(", "\n".join(body[1:]))))[:25]
                    out_procs.append(
                        {
                            "path": path,
                            "procedure": h,
                            "start_line": i + 1,
                            "lines_total": len(body),
                            "text": "\n".join(body[:max_lines]) + ("\n…" if len(body) > max_lines else ""),
                            "calls": calls,
                        }
                    )
                    break
    if not out_procs:
        return None
    return {
        "ok": True,
        "mode": "object_event_handlers",
        "request": req,
        "full_name": fn,
        "module": {"path": out_procs[0]["path"]},
        "modules_checked": paths_seen,
        "handlers_wanted": list(handlers),
        "procedures": out_procs,
        "prefer": (
            "Объясни по порядку, что делают найденные обработчики (procedures[].text) и какие вызовы (calls) важны. "
            "Тела вызываемых процедур — proc_index(procedure=…) / read_code(path, start_line). "
            "Также на событие могут быть подписки — explain_subscriptions(full_name=…)."
        ),
    }


# папки «общих» объектов: модуль относится к stem по имени (affinity), не по object_name
_COMMON_CODE_FOLDERS = ("CommonModules", "CommonForms", "CommonCommands", "CommonTemplates")


def _module_object_of(m: dict[str, Any]) -> tuple[str, str]:
    p = _normalize_qdrant_path(str(m.get("path") or m.get("rel_path") or ""))
    parts = [x for x in p.split("/") if x]
    if len(parts) < 2:
        return (parts[0] if parts else "", "")
    return parts[0], re.sub(r"\.(xml|bsl)$", "", parts[1], flags=re.I)


def _object_named_in_request(obj_stem: str, req: str) -> bool:
    """
    Объект ЯВНО назван во фразе (а не угадан по одному общему слову):
    имя целиком или большинство CamelCase-частей (по основе слова) есть в запросе.
    """
    rl = (req or "").lower().replace("ё", "е")
    sl = (obj_stem or "").lower().replace("ё", "е")
    if not sl or not rl:
        return False
    if sl in rl.replace(" ", ""):
        return True
    parts = [p for p in _camel_parts(obj_stem) if len(p) >= 3] or [obj_stem]
    words = re.findall(r"[a-zа-я0-9_]+", rl)
    hit = 0
    for p in parts:
        pl = p.lower().replace("ё", "е")
        stem = pl[: max(4, min(6, len(pl) - 2))]
        if any(w.startswith(stem) for w in words):
            hit += 1
    need = 1 if len(parts) == 1 else max(2, (len(parts) + 1) // 2)
    return hit >= need


def _prefer_requested_object(
    modules_sorted: list[dict[str, Any]],
    obj_stem: str,
    *,
    stem_variants: list[str],
    form_wanted: str,
    want_form: bool,
    form_explicit: bool,
    req: str,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """
    Если во фразе назван объект (obj_stem из resolve этой CF), основной результат —
    модуль ЭТОГО объекта. Модули других объектов уходят вниз; если у объекта нет
    подходящего кода в Qdrant — ok:false с уточнением (не подменяем чужим объектом).
    """
    stem_l = obj_stem.lower().replace("ё", "е")

    def _is_own(m: dict[str, Any]) -> bool:
        cls, on = _module_object_of(m)
        if cls in _COMMON_CODE_FOLDERS:
            p = str(m.get("path") or m.get("rel_path") or "")
            return _path_stem_affinity(p, stem_variants or [obj_stem]) >= 100
        return on.lower().replace("ё", "е") == stem_l

    def _is_form(m: dict[str, Any]) -> bool:
        p = str(m.get("path") or m.get("rel_path") or "")
        return "/Forms/" in p or m.get("kind") == "form_module"

    if not modules_sorted:
        return modules_sorted, None
    best = modules_sorted[0]
    if _is_own(best) and (not form_explicit or _is_form(best)):
        return modules_sorted, None

    own = [m for m in modules_sorted if _is_own(m)]
    others = [m for m in modules_sorted if not _is_own(m)]
    own_forms = [m for m in own if _is_form(m)]
    if own and (not form_explicit or own_forms):
        head = own_forms if form_explicit else own
        rest = [m for m in own if m not in head]
        return head + rest + others, None

    # модулей объекта среди кандидатов нет → взять из Qdrant по object_name (keyword)
    from tools.path_norm import CodePathSpec, _rank_suggestions, list_object_paths, parse_code_path

    obj_paths = list_object_paths(obj_stem)
    if not obj_paths:
        # регистр имени
        for m in modules_sorted:
            _, on = _module_object_of(m)
            if on.lower().replace("ё", "е") == stem_l and on != obj_stem:
                obj_paths = list_object_paths(on)
                break
    fw = (form_wanted or "").lower().replace("ё", "е")

    def _rank(p: str) -> tuple:
        pl = p.lower().replace("ё", "е")
        is_form = "/forms/" in pl
        same_form = bool(fw) and f"/forms/{fw}/" in pl
        return (
            0 if (form_explicit or want_form) and same_form else 1,
            0 if (form_explicit or want_form) and is_form else 1,
            0 if pl.endswith("objectmodule.bsl") else 1,
            0 if pl.endswith(".bsl") else 1,
            0 if pl.endswith("form.xml") else 1,
            len(p),
        )

    usable = [p for p in obj_paths if p.endswith(".bsl") or p.endswith("/Ext/Form.xml")]
    if form_explicit:
        usable = [p for p in usable if "/Forms/" in p]
    usable = sorted(usable, key=_rank)
    if usable:
        new_mods: list[dict[str, Any]] = []
        for p in usable[:6]:
            spec = parse_code_path(p)
            new_mods.append(
                {
                    "path": p,
                    "rel_path": p,
                    "score": 9000,
                    "object_name": spec.object_name if spec else obj_stem,
                    "form_name": spec.form_name if spec else "",
                    "full_name_1c": spec.full_name_1c if spec else "",
                    "kind": "form_module" if "/Forms/" in p else "module",
                    "resolved_by": "object_from_request",
                }
            )
        return new_mods + others, None

    foreign = []
    for m in others[:5]:
        fn = m.get("full_name_1c") or "/".join(_module_object_of(m))
        if fn and fn not in foreign:
            foreign.append(fn)
    spec0 = parse_code_path(obj_paths[0]) if obj_paths else None
    obj_full = spec0.object_full_name if spec0 else obj_stem
    if obj_paths:
        what = "форм" if form_explicit else "модулей кода"
        err = f"У объекта {obj_full} нет {what} в индексе Qdrant (запрос: {req})."
        hint = (
            "Вероятно обычная форма (Form.bin) — код/разметка не проиндексированы. "
            f"Состав формы: describe_object(full_name='{obj_full}'"
            + (f", form_name='{form_wanted}'" if form_wanted else "")
            + "). Код объекта — read_code(path из did_you_mean)."
            if form_explicit
            else "Возьми path из did_you_mean."
        )
    else:
        err = f"Объект «{obj_stem}» из запроса не найден в индексе кода Qdrant (запрос: {req})."
        hint = "Уточни точное имя объекта: resolve_object(request=…) / find_modules."
    out: dict[str, Any] = {
        "ok": False,
        "source": "qdrant",
        "request": req,
        "error": err,
        "object_stem": obj_stem,
        "did_you_mean": _rank_suggestions(
            obj_paths,
            CodePathSpec(class_en=spec0.class_en, object_name=spec0.object_name, form_name=form_wanted or ""),
        )[:5]
        if spec0
        else [],
        "rejected_other_objects": foreign,
        "need_clarification": True,
        "question": (
            f"Нужна форма/код именно {obj_full}? В индексе её нет — показать состав формы через describe_object "
            "или выбрать другой объект?"
            if obj_paths
            else f"Какой объект имеется в виду под «{obj_stem}»?"
        ),
        "options": foreign,
        "hint": hint,
        "prefer": (
            "НЕ отвечай по чужому объекту из rejected_other_objects как по запрошенному. "
            "Скажи честно, что кода/формы нет в индексе, и задай question."
        ),
    }
    if form_explicit and obj_paths:
        out["suggested_tools"] = ["describe_object"]
    return modules_sorted, out


def inspect_code(
    request: str = "",
    *,
    path: str = "",
    max_hits: int = 10,
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    ONE-SHOT: найти модуль по фразе + outline + сниппеты про источник данных.
    Runtime: Qdrant only.
    """
    from vector.catalog import (
        assemble_module_text,
        get_chunks_by_source_path,
        runtime_fs_allowed,
    )

    req = (request or "").strip()
    if not path and not req:
        return {
            "error": "Нужен request (фраза) или path",
            "hint": "inspect_code(request='общая форма список задач')",
        }

    # --- событие платформы + объект → обработчики в модуле объекта (быстро и точно) ---
    if not path and req:
        try:
            ev = _object_event_handlers(req, max_lines=45)
        except Exception:
            ev = None
        if ev:
            return ev

    # --- Qdrant path ---
    if not runtime_fs_allowed():
        modules: list[dict[str, Any]] = []
        if path:
            sp, perr = resolve_qdrant_code_path(path)
            if perr is not None:
                perr.setdefault("request", req)
                return perr
            modules = [
                {
                    "path": sp,
                    "rel_path": sp,
                    "score": 1000,
                    "kind": "form_module" if "/Forms/" in sp or "/forms/" in sp.lower() else "module",
                }
            ]
        else:
            found = find_modules(req, limit=15, code_dir=None)
            modules = list(found.get("modules") or [])

        parsed_req = parse_nl_query(req)
        form_wanted = (parsed_req.get("form_name") or "").lower().replace("ё", "е")
        want_form = bool(parsed_req.get("want_form_module") or form_wanted)
        # «откуда данные в форме» → form module
        pl_req = (req or "").lower().replace("ё", "е")
        if any(x in pl_req for x in ("форм", "откуда данн", "данные в форм", "на форме")):
            want_form = True
        # «какие запросы / текст запроса / ВЫБРАТЬ» → ObjectModule + common modules
        want_queries = any(
            x in pl_req
            for x in (
                "запрос",
                "запросы",
                "выбрать",
                "текст запрос",
                "текстзапрос",
                "скд",
                "схема компонов",
            )
        )
        want_posting = any(
            x in pl_req
            for x in (
                "провед",
                "движен",
                "обработкапроведения",
                "регистр",
            )
        )
        # «блокировки / оптимизация / проанализируй модуль» → multi-module risk scan
        want_risks = any(
            x in pl_req
            for x in (
                "блокиров",
                "оптимиз",
                "ускор",
                "тормоз",
                "производительн",
                "узк",
                "deadlock",
                "взаимн",
                "транзакц",
                "проанализир",
                "разбери модул",
                "антипаттерн",
                "аудит запрос",
                "ревью запрос",
            )
        )
        want_query_audit = False
        try:
            from tools.query_antipatterns import is_query_audit_question

            want_query_audit = is_query_audit_question(req)
        except Exception:
            want_query_audit = False
        if want_query_audit:
            want_queries = True
            want_risks = True
        # «модуль объекта» без «запрос» — тоже risk/posting path
        if re.search(r"модул\w*\s+объект|код\s+проведен|обработк\w*\s+проведен", pl_req):
            want_risks = True
            want_posting = True

        # resolve object stem for ranking (фраза → object_name из Qdrant этой CF)
        obj_stem = ""
        if path:
            # path задан явно: объект — из path, фраза не подменяет модуль
            obj_stem = _module_object_of({"path": sp})[1]
        else:
            try:
                from tools.object_movements import (
                    _resolve_object_from_phrase,
                )

                # выкинуть «откуда/данные/форме/документа»
                cleaned = re.sub(
                    r"\b(откуда|данные|данн\w*|форме|форма|формах|устроен[аоы]?|"
                    r"документ[аеу]?\w*|модул\w*|код\w*|покажи|найди|как|что|"
                    r"бер[её]тся|логик\w*|сумм\w*|поле|реквизит\w*|устройств\w*)\b",
                    " ",
                    req,
                    flags=re.I,
                )
                cleaned = re.sub(r"\s+", " ", cleaned).strip()
                obj_stem = (
                    _resolve_object_from_phrase(cleaned)
                    or _resolve_object_from_phrase(req)
                    or ""
                )
                # без хардкода CF: retry find_modules по стемам фразы (реализац/продаж/…)
                if not obj_stem:
                    stems = re.findall(
                        r"[а-яa-zё]{5,}",
                        (cleaned or pl_req).lower().replace("ё", "е"),
                    )
                    for stem in stems[:4]:
                        try:
                            found_s = find_modules(stem[:1].upper() + stem[1:], limit=6, code_dir=None)
                            mods = list(found_s.get("modules") or [])
                            if mods:
                                on = mods[0].get("object_name") or ""
                                if not on:
                                    p0 = str(mods[0].get("path") or "").replace("\\", "/")
                                    parts = p0.split("/")
                                    if len(parts) >= 2 and parts[0] in (
                                        "Documents",
                                        "Catalogs",
                                        "DataProcessors",
                                        "Reports",
                                    ):
                                        on = parts[1]
                                if on:
                                    obj_stem = on
                                    modules = mods
                                    break
                        except Exception:
                            pass
            except Exception:
                obj_stem = (parsed_req.get("object_name") or "").strip()

        # find_modules по длинной фразе часто 0 — retry по stem/форме
        if not modules and obj_stem:
            for qtry in (
                f"{obj_stem} ФормаДокумента",
                f"Documents/{obj_stem}",
                obj_stem,
                f"{obj_stem} Form Module",
            ):
                found2 = find_modules(qtry, limit=12, code_dir=None)
                modules = list(found2.get("modules") or [])
                if modules:
                    break
        if not modules and obj_stem:
            # path-шаблоны платформы (имя объекта — только из resolve/Qdrant)
            from vector.catalog import get_chunks_by_source_path

            form_paths = [
                f"Documents/{obj_stem}/Forms/ФормаДокумента/Ext/Form/Module.bsl",
                f"Documents/{obj_stem}/Forms/DocumentForm/Ext/Form/Module.bsl",
                f"Documents/{obj_stem}/Ext/ObjectModule.bsl",
                f"Documents/{obj_stem}/Ext/ManagerModule.bsl",
                f"Catalogs/{obj_stem}/Ext/ObjectModule.bsl",
                f"DataProcessors/{obj_stem}/Ext/ObjectModule.bsl",
            ]
            if want_form:
                form_paths = [
                    p for p in form_paths if "/Forms/" in p
                ] + [p for p in form_paths if "/Forms/" not in p]
            for alt in form_paths:
                if get_chunks_by_source_path(alt):
                    modules = [
                        {
                            "path": alt,
                            "rel_path": alt,
                            "score": 5000,
                            "object_name": obj_stem,
                            "kind": "form_module" if "/Forms/" in alt else "object_module",
                        }
                    ]
                    break
        if not modules:
            return {
                "ok": False,
                "error": f"Модуль не найден в Qdrant: {req}",
                "source": "qdrant",
                "hint": (
                    "Уточни имя объекта/формы из этой CF (resolve_object / find_modules); "
                    "проверь index_qdrant. Не выдумывай код и full_name из другой базы."
                ),
                "object_hint": obj_stem or None,
                "prefer": (
                    "Честно: модуль не найден. Предложи resolve_object(request=…) "
                    "и повторить inspect_code с точным именем. Не пиши фейковый код."
                ),
            }

        def _pick(m: dict[str, Any]) -> float:
            sc = float(m.get("score") or 0)
            rel = (m.get("path") or m.get("rel_path") or "").replace("\\", "/")
            low = rel.lower()
            if path and rel == sp:
                sc += 100000  # явно заданный path — всегда основной модуль
            fn = (m.get("form_name") or "").lower().replace("ё", "е")
            if form_wanted and (fn == form_wanted or form_wanted in fn):
                sc += 2000
            if want_form and ("/Forms/" in rel or m.get("kind") == "form_module"):
                sc += 500
            if want_form and "ФормаДокумента" in rel:
                sc += 800
            if obj_stem and obj_stem.lower() in low:
                sc += 3000
            # запросы/проведение/риски: ObjectModule и CommonModules важнее Manager
            stem_vars = object_name_variants(obj_stem) if obj_stem else []
            if want_queries or want_posting or want_risks:
                if "objectmodule" in low.replace("/", ""):
                    sc += 2500
                if "commonmodules" in low:
                    for v in stem_vars:
                        vl = v.lower().replace("ё", "е")
                        if vl and vl in low:
                            # точное совпадение имени модуля > префикс
                            if f"/{v.lower()}/" in low or f"/{v}/" in rel:
                                sc += 4500
                            else:
                                sc += 2000
                            break
                    else:
                        # частичный stem (первые 8+ букв)
                        if obj_stem and obj_stem.lower()[:8] in low:
                            sc += 1500
                if "managermodule" in low.replace("/", ""):
                    sc -= 800
            if "CommandModule" in rel or "Commands/" in rel:
                sc -= 400
            return sc

        # добрать ObjectModule + common module по stem (+ варианты имён)
        stem_variants = object_name_variants(obj_stem) if obj_stem else []
        if obj_stem and (want_queries or want_posting or want_risks or not modules):
            from vector.catalog import get_chunks_by_source_path as _gcp

            extra_paths: list[str] = [
                f"Documents/{obj_stem}/Ext/ObjectModule.bsl",
                f"Documents/{obj_stem}/Ext/ManagerModule.bsl",
            ]
            for v in stem_variants:
                extra_paths.extend(
                    [
                        f"CommonModules/{v}/Ext/Module.bsl",
                        f"CommonModules/{v}Сервер/Ext/Module.bsl",
                        f"CommonModules/{v}КлиентСервер/Ext/Module.bsl",
                    ]
                )
            seen_p = {
                str(m.get("path") or m.get("rel_path") or "").replace("\\", "/")
                for m in modules
            }
            for alt in extra_paths:
                if alt in seen_p:
                    continue
                if _gcp(alt):
                    is_cm = "CommonModules" in alt
                    modules.append(
                        {
                            "path": alt,
                            "rel_path": alt,
                            "score": 5000 if is_cm else (4000 if "ObjectModule" in alt else 2000),
                            "object_name": alt.split("/")[1] if is_cm else obj_stem,
                            "kind": (
                                "object_module"
                                if "ObjectModule" in alt
                                else "common_module"
                                if is_cm
                                else "manager_module"
                            ),
                        }
                    )
                    seen_p.add(alt)
            # common modules по вариантам stem через find_modules (длинные стемы первыми)
            for v in sorted(stem_variants, key=len, reverse=True)[:5]:
                try:
                    cm = find_modules(v, kind="common_module", limit=6, code_dir=None)
                    for m in cm.get("modules") or []:
                        p = str(m.get("path") or "").replace("\\", "/")
                        if not p or p in seen_p:
                            continue
                        if "CommonModules" not in p or _is_noise_common_path(p):
                            continue
                        # только если имя модуля близко к варианту (не чужие отчёты)
                        on = (m.get("object_name") or "").lower().replace("ё", "е")
                        vl = v.lower().replace("ё", "е")
                        if vl not in on and on not in vl and vl not in p.lower():
                            continue
                        # отсечь слабый match: «Расчет» в «Распределение…»
                        if len(vl) >= 8 and _path_stem_affinity(p, [v]) < 50:
                            continue
                        modules.append(m)
                        seen_p.add(p)
                except Exception:
                    pass
            # вызовы X.Y( из Object/Manager → связанные common modules
            if want_queries or want_posting or want_risks:
                for doc_p in (
                    f"Documents/{obj_stem}/Ext/ObjectModule.bsl",
                    f"Documents/{obj_stem}/Ext/ManagerModule.bsl",
                ):
                    try:
                        ch_doc = _gcp(doc_p)
                        if not ch_doc:
                            continue
                        txt_doc = assemble_module_text(ch_doc)
                        for cm_name in _related_common_module_names(txt_doc, limit=10):
                            alt = f"CommonModules/{cm_name}/Ext/Module.bsl"
                            if alt in seen_p or _is_noise_common_path(alt):
                                continue
                            if _gcp(alt):
                                modules.append(
                                    {
                                        "path": alt,
                                        "rel_path": alt,
                                        "score": 3500,
                                        "object_name": cm_name,
                                        "kind": "common_module",
                                        "linked_from": doc_p,
                                    }
                                )
                                seen_p.add(alt)
                    except Exception:
                        pass

        modules_sorted = sorted(modules, key=_pick, reverse=True)
        if not modules_sorted:
            return {
                "ok": False,
                "error": f"Ничего не найдено: {req}",
                "source": "qdrant",
            }
        # объект назван во фразе → результат по ЧУЖОМУ объекту не может быть основным
        if not path and obj_stem and _object_named_in_request(obj_stem, req):
            modules_sorted, obj_err = _prefer_requested_object(
                modules_sorted,
                obj_stem,
                stem_variants=stem_variants,
                form_wanted=parsed_req.get("form_name") or "",
                want_form=want_form,
                form_explicit=bool(form_wanted) or bool(re.search(r"(?<![а-яё])форм(?:а|ы|е|у|ой|ах|ами|ам)?(?![а-яё])", pl_req)),
                req=req,
            )
            if obj_err is not None:
                return obj_err
        best = modules_sorted[0]
        best_path = _normalize_qdrant_path(str(best.get("path") or best.get("rel_path") or ""))

        # --- multi-module scan for queries ---
        query_funcs: list[dict[str, Any]] = []
        related_modules: list[dict[str, Any]] = []
        risk_hits: list[dict[str, Any]] = []
        query_antipatterns: list[dict[str, Any]] = []
        thin_object_module = False
        if want_queries or want_posting or want_risks:
            paths_to_scan: list[str] = []
            for m in modules_sorted[:12]:
                p = _normalize_qdrant_path(str(m.get("path") or m.get("rel_path") or ""))
                if p and p not in paths_to_scan and p.endswith(".bsl"):
                    paths_to_scan.append(p)
            # гарантированно просканировать common modules по stem-вариантам
            for v in stem_variants[:6]:
                for alt in (
                    f"CommonModules/{v}/Ext/Module.bsl",
                    f"CommonModules/{v}Сервер/Ext/Module.bsl",
                ):
                    if alt not in paths_to_scan and get_chunks_by_source_path(alt):
                        paths_to_scan.append(alt)
            # ObjectModule всегда в scan
            if obj_stem:
                om = f"Documents/{obj_stem}/Ext/ObjectModule.bsl"
                if om not in paths_to_scan and get_chunks_by_source_path(om):
                    paths_to_scan.insert(0, om)

            qf_name_tokens = (
                "запрос",
                "текстзапрос",
                "текст",
                "query",
                "выбрать",
                "сформироватьзапрос",
            )
            post_name_tokens = (
                "провед",
                "движен",
                "отразить",
                "сформировать",
                "запрос",
            )
            for pth in paths_to_scan:
                if _is_noise_common_path(pth):
                    continue
                ol = module_outline(
                    path=pth,
                    max_symbols=120,
                    name_filter=(
                        "запрос текст сформир query"
                        if want_queries
                        else "провед запрос движен отразить"
                    ),
                )
                syms = ol.get("symbols") or []
                # если фильтр не сработал — взять symbols с токенами запроса
                if want_queries and not any(
                    any(
                        t in str(s.get("name") or "").lower().replace("ё", "е")
                        for t in ("запрос", "текст", "query")
                    )
                    for s in syms
                    if isinstance(s, dict)
                ):
                    ol_all = module_outline(path=pth, max_symbols=200, name_filter="")
                    syms = [
                        s
                        for s in (ol_all.get("symbols") or [])
                        if isinstance(s, dict)
                        and any(
                            t in str(s.get("name") or "").lower().replace("ё", "е")
                            for t in qf_name_tokens
                        )
                    ]
                related_modules.append(
                    {
                        "path": pth,
                        "symbols_count": len(syms) if syms else ol.get("symbols_count"),
                        "symbols_total": ol.get("symbols_total"),
                    }
                )
                for s in syms:
                    if not isinstance(s, dict):
                        continue
                    nm = str(s.get("name") or "")
                    nml = nm.lower().replace("ё", "е")
                    if want_queries and any(x in nml for x in qf_name_tokens):
                        # «текст» без «запрос» — только ТекстЗапроса* / QueryText
                        if "запрос" not in nml and "query" not in nml:
                            if not nml.startswith("текстзапрос") and "querytext" not in nml:
                                continue
                        query_funcs.append(
                            {
                                "name": nm,
                                "line": s.get("line"),
                                "type": s.get("type"),
                                "path": pth,
                            }
                        )
                    elif want_posting and any(x in nml for x in post_name_tokens):
                        query_funcs.append(
                            {
                                "name": nm,
                                "line": s.get("line"),
                                "type": s.get("type"),
                                "path": pth,
                            }
                        )
            # de-dupe; приоритет: affinity к stem + ТекстЗапроса*
            seen_qf: set[str] = set()
            uniq_qf: list[dict[str, Any]] = []

            def _qf_sort_key(x: dict[str, Any]) -> tuple:
                nm = str(x.get("name") or "").lower().replace("ё", "е")
                pth = str(x.get("path") or "")
                aff = _path_stem_affinity(pth, stem_variants)
                is_tq = 0 if nm.startswith("текстзапрос") or "текстзапроса" in nm else 1
                noise = 1 if _is_noise_common_path(pth) else 0
                return (-aff, noise, is_tq, pth, int(x.get("line") or 0))

            for qf in sorted(query_funcs, key=_qf_sort_key):
                k = f"{qf.get('path')}:{qf.get('name')}"
                if k in seen_qf:
                    continue
                if _is_noise_common_path(str(qf.get("path") or "")):
                    continue
                seen_qf.add(k)
                uniq_qf.append(qf)
            query_funcs = uniq_qf[:40]

            # best_path: max (affinity * count ТекстЗапроса), не «шумный» common
            if query_funcs:
                from collections import defaultdict

                scores: dict[str, float] = defaultdict(float)
                for q in query_funcs:
                    pth = str(q.get("path") or "")
                    if not pth or _is_noise_common_path(pth):
                        continue
                    nm = str(q.get("name") or "").lower().replace("ё", "е")
                    w = 3.0 if nm.startswith("текстзапрос") else 1.0
                    aff = max(1, _path_stem_affinity(pth, stem_variants))
                    scores[pth] += w * (1.0 + aff / 50.0)
                if scores:
                    best_path = max(scores.items(), key=lambda kv: kv[1])[0]
                    for m in modules_sorted:
                        mp = _normalize_qdrant_path(
                            str(m.get("path") or m.get("rel_path") or "")
                        )
                        if mp == best_path:
                            best = m
                            break
                    else:
                        best = {
                            "path": best_path,
                            "rel_path": best_path,
                            "object_name": best_path.split("/")[1]
                            if "/" in best_path
                            else obj_stem,
                            "kind": "common_module"
                            if "CommonModules" in best_path
                            else "object_module",
                            "score": 9000,
                        }

            # risk scan: locks / transactions / loops / queries across related modules
            if want_risks or want_posting:
                risk_paths = list(paths_to_scan)
                # thin ObjectModule? (только делегирование в ПроведениеСервер*)
                if obj_stem:
                    om_p = f"Documents/{obj_stem}/Ext/ObjectModule.bsl"
                    om_ch = get_chunks_by_source_path(om_p)
                    if om_ch:
                        om_txt = assemble_module_text(om_ch)
                        om_lines = [
                            ln
                            for ln in om_txt.splitlines()
                            if ln.strip()
                            and not ln.strip().startswith("//")
                            and not ln.strip().startswith("#")
                        ]
                        if len(om_lines) < 80 and not re.search(
                            r"Новый\s+Запрос|БлокировкаДанных|ВЫБРАТЬ",
                            om_txt,
                            re.I,
                        ):
                            thin_object_module = True
                        # linked common modules from ObjectModule always in risk scan
                        for cm_name in _related_common_module_names(om_txt, limit=12):
                            alt = f"CommonModules/{cm_name}/Ext/Module.bsl"
                            if (
                                alt not in risk_paths
                                and not _is_noise_common_path(alt)
                                and get_chunks_by_source_path(alt)
                            ):
                                risk_paths.append(alt)
                for rp in risk_paths[:10]:
                    if _is_noise_common_path(rp):
                        continue
                    # ПроведениеСервер* — инфраструктура; сканируем, но с пометкой
                    rch = get_chunks_by_source_path(rp)
                    if not rch:
                        continue
                    rtxt = assemble_module_text(rch)
                    # для огромных модулей — скан outline-окон по risk keywords
                    rh = scan_module_risks(rtxt, path=rp, max_hits=25)
                    # boost: query funcs nearby as optimization targets
                    if want_risks and "ТекстЗапроса" in rtxt:
                        for m in re.finditer(
                            r"(?im)^\s*(?:Функция|Процедура)\s+(ТекстЗапроса\w+)",
                            rtxt,
                        ):
                            line_no = rtxt.count("\n", 0, m.start()) + 1
                            rh.append(
                                {
                                    "line": line_no,
                                    "kind": "query_func",
                                    "title": "функция текста запроса (кандидат на оптимизацию)",
                                    "pattern": "ТекстЗапроса",
                                    "text": m.group(0).strip()[:220],
                                    "path": rp,
                                    "near_loop": False,
                                    "severity": "medium",
                                }
                            )
                    risk_hits.extend(rh)
                # de-dupe / sort
                seen_r: set[str] = set()
                uniq_r: list[dict[str, Any]] = []
                for rh in sorted(
                    risk_hits,
                    key=lambda x: (
                        0 if x.get("severity") == "high" else 1 if x.get("severity") == "medium" else 2,
                        0 if x.get("near_loop") else 1,
                        -_path_stem_affinity(str(x.get("path") or ""), stem_variants),
                        int(x.get("line") or 0),
                    ),
                ):
                    k = f"{rh.get('path')}:{rh.get('line')}:{rh.get('kind')}"
                    if k in seen_r:
                        continue
                    seen_r.add(k)
                    uniq_r.append(rh)
                risk_hits = uniq_r[:40]

            # антипаттерны ВЫБРАТЬ / BSL (каталог knowledge/антипаттерны_запросов.yaml)
            if want_query_audit or (want_risks and want_queries):
                try:
                    from tools.query_antipatterns import scan_bsl_module

                    scan_paths = paths_to_scan
                    if want_risks or want_posting:
                        try:
                            scan_paths = risk_paths  # type: ignore[name-defined]
                        except NameError:
                            scan_paths = paths_to_scan
                    for rp in scan_paths[:10]:
                        if _is_noise_common_path(rp):
                            continue
                        rch = get_chunks_by_source_path(rp)
                        if not rch:
                            continue
                        rtxt = assemble_module_text(rch)
                        query_antipatterns.extend(
                            scan_bsl_module(rtxt, path=rp, max_findings=20)
                        )
                    seen_qa: set[str] = set()
                    uniq_qa: list[dict[str, Any]] = []
                    for qa in sorted(
                        query_antipatterns,
                        key=lambda x: (
                            0
                            if x.get("severity") == "high"
                            else 1
                            if x.get("severity") == "medium"
                            else 2,
                            int(x.get("line") or 0),
                        ),
                    ):
                        k = f"{qa.get('id')}:{qa.get('path')}:{qa.get('line')}"
                        if k in seen_qa:
                            continue
                        seen_qa.add(k)
                        uniq_qa.append(qa)
                    query_antipatterns = uniq_qa[:40]
                except Exception:
                    query_antipatterns = []

        outline = module_outline(
            path=best_path,
            max_symbols=100,
            name_filter=(
                "запрос текст печат макет query"
                if want_queries
                else ("провед" if want_posting else "")
            ),
        )
        chunks = get_chunks_by_source_path(best_path)
        text = assemble_module_text(chunks) if chunks else ""
        # fallback: ObjectModule / common variants if manager empty / only commands
        if (not text or len(text) < 80 or (want_queries and not query_funcs)) and obj_stem:
            fallback_alts = [
                f"Documents/{obj_stem}/Ext/ObjectModule.bsl",
            ]
            for v in stem_variants[:6]:
                fallback_alts.append(f"CommonModules/{v}/Ext/Module.bsl")
            fallback_alts.extend(
                [
                    f"Documents/{obj_stem}/Ext/ManagerModule.bsl",
                    f"Documents/{obj_stem}/Forms/ФормаДокумента/Ext/Form/Module.bsl",
                ]
            )
            for alt in fallback_alts:
                ch2 = get_chunks_by_source_path(alt)
                if ch2:
                    if want_queries or want_posting or not text:
                        best_path = alt
                        chunks = ch2
                        text = assemble_module_text(ch2)
                        outline = module_outline(
                            path=alt,
                            max_symbols=100,
                            name_filter="запрос текст query" if want_queries else "",
                        )
                    break
        lines = text.splitlines()
        hits: list[dict[str, Any]] = []
        max_hits = max(3, min(int(max_hits or 10), 15))
        patterns = list(_DATA_SOURCE_PATTERNS)
        # расширить для «данные на форме»
        patterns.extend(
            [
                "Объект.",
                "ДанныеФормы",
                "ЗначениеВРеквизитФормы",
                "ЗначениеВДанныеФормы",
                "РеквизитФормыВЗначение",
                "ЗаполнитьЗначенияСвойств",
                "ДинамическийСписок",
                "Список.",
                "ПолучитьОбъект",
            ]
        )
        if want_queries:
            patterns = [
                "Запрос.Текст",
                "Новый Запрос",
                "ВЫБРАТЬ",
                "ТекстЗапроса",
                "УстановитьПараметр",
            ] + patterns
        # «сумма» / «откуда берётся» — доп. стемы
        if any(x in pl_req for x in ("сумм", "итог", "стоим", "цена", "количеств")):
            patterns.extend(
                [
                    "Сумма",
                    "СуммаДокумента",
                    "СуммаСНДС",
                    "СуммаНДС",
                    "Цена",
                    "Количество",
                    "Рассчит",
                    "Пересчет",
                    "Пересчёт",
                ]
            )
        # hits: сначала из best_path, при want_queries — и из модулей с query_funcs
        hit_paths = [best_path]
        if want_queries and query_funcs:
            for qf in query_funcs[:5]:
                qp = str(qf.get("path") or "")
                if qp and qp not in hit_paths:
                    hit_paths.append(qp)
        for hpath in hit_paths[:3]:
            if len(hits) >= max_hits:
                break
            h_chunks = (
                chunks if hpath == best_path else get_chunks_by_source_path(hpath)
            )
            h_text = assemble_module_text(h_chunks) if h_chunks else ""
            h_lines = h_text.splitlines() if h_text else []
            for pat in patterns:
                if len(hits) >= max_hits:
                    break
                for i, ln in enumerate(h_lines):
                    if pat.lower() in ln.lower():
                        hits.append(
                            {
                                "line": i + 1,
                                "pattern": pat,
                                "text": ln.strip()[:220],
                                "path": hpath,
                            }
                        )
                        if len(hits) >= max_hits:
                            break
        if not hits and not text and not query_funcs:
            return {
                "ok": False,
                "error": f"Ничего не найдено: {req}",
                "source": "qdrant",
                "hint": "Уточни имя формы/объекта; проверь index_qdrant.py",
                "tried_path": best_path,
            }
        # outline-only: модуль есть, но паттерны data_source не сработали
        empty_hits = len(hits) == 0 and not query_funcs and not risk_hits
        prefer = (
            "Ответь по outline + data_source_hits + query_functions + risk_hits. "
            "read_code(path=…, start_line=line). "
            "Код из Qdrant. НЕ спрашивай «хотите посмотреть?» — сразу факты."
        )
        if want_risks and (risk_hits or thin_object_module or query_funcs or query_antipatterns):
            prefer = (
                f"Анализ блокировок/оптимизации. thin_object_module={thin_object_module}. "
                f"risk_hits={len(risk_hits)}, query_functions={len(query_funcs)}, "
                f"query_antipatterns={len(query_antipatterns)}. "
                "1) Если thin_object_module — скажи что ObjectModule тонкий (делегирует), "
                "реальная логика в related_modules / path из risk_hits "
                "(не только инфраструктурный ПроведениеСервер*). "
                "2) Перечисли risk_hits (kind, path, line, title) — managed_lock / transaction / "
                "loop+query / posting_write / query_func. "
                "3) Для high/medium — read_code 2–4 места и дай рекомендации "
                "(сузить измерения, вынести запрос из цикла, короткая транзакция). "
                "4) query_functions (ТекстЗапроса*) — кандидаты на тяжёлые запросы. "
                "5) query_antipatterns (id, severity, path, line, better) — антипаттерны ВЫБРАТЬ; "
                "при необходимости audit_query_code(request=…). "
                "6) ЗАПРЕЩЕНО: «Хотите чтобы я нашёл/прочитал модуль?» — сразу tools и факты. "
                "7) READ-ONLY: НЕ «я/разработчик исправил», «уже оптимизировано». "
                "Факт: «в текущем коде X стоит до/в цикле». "
                "Пример BSL — только «Рекомендация (НЕ применено, CF не менялась)». "
                "8) analyze_locks(object_hint=object_stem) — доп. скан managed locks по CF."
            )
        elif query_funcs:
            prefer = (
                f"Найдено функций/процедур с запросами: {len(query_funcs)}. "
                "1) Перечисли query_functions (name, path, line) — это и есть ответ. "
                "2) Для 2–3 главных — read_code(path, start_line=line, max_lines=60) "
                "и покажи суть (ВЫБРАТЬ/таблицы). "
                "3) НЕ говори «в модуле менеджера запросов нет» — запросы в "
                "ObjectModule/CommonModules (см. path в query_functions). "
                "4) НЕ спрашивай «хотите посмотреть/продолжить?» — сразу список и код."
            )
        elif empty_hits:
            prefer = (
                f"Модуль: {best_path}. Явных Запрос.Текст не найдено в data_source_hits. "
                "Проверь ObjectModule + CommonModules (query_functions/related_modules). "
                "read_code ObjectModule.ОбработкаПроведения и связанный common module. "
                "НЕ спрашивай «хотите?» — сразу read_code."
            )
        return {
            "ok": True,
            "source": "qdrant",
            "request": req,
            "module": {
                "path": best_path,
                "rel_path": best_path,
                "full_name_1c": best.get("full_name_1c") or outline.get("full_name_1c"),
                "form_name": best.get("form_name") or outline.get("form_name"),
                "object_name": best.get("object_name") or outline.get("object_name") or obj_stem,
                "kind": best.get("kind") or outline.get("kind"),
                "score": best.get("score"),
            },
            "object_stem": obj_stem or None,
            "stem_variants": stem_variants[:8] if stem_variants else None,
            "thin_object_module": thin_object_module,
            "candidates": [
                {
                    "path": m.get("path"),
                    "form_name": m.get("form_name"),
                    "full_name_1c": m.get("full_name_1c"),
                    "object_name": m.get("object_name"),
                    "score": m.get("score"),
                }
                for m in modules_sorted[:10]
            ],
            "related_modules": related_modules[:10],
            "related_modules_total": len(related_modules),
            "modules_total": len(modules_sorted),
            "query_functions": query_funcs[:30],
            "query_functions_count": len(query_funcs),
            "risk_hits": risk_hits[:30],
            "risk_hits_count": len(risk_hits),
            "query_antipatterns": query_antipatterns[:30],
            "query_antipatterns_count": len(query_antipatterns),
            "outline": {
                "path": outline.get("path"),
                "symbols": (outline.get("symbols") or [])[:40],
                "symbols_count": outline.get("symbols_count"),
                "symbols_total": outline.get("symbols_total"),
            },
            "data_source_hits": hits,
            "hits": hits,
            "hits_count": len(hits),
            "empty_hits": empty_hits,
            "prefer": prefer,
        }

    modules = []
    if path:
        target = _resolve_module_path(path=path, module="", code_dir=code_dir)
        if target.get("error"):
            return target
        p = Path(target["path"])
        cr = resolve_code_roots(code_dir)
        root = cr.roots[0] if cr.roots else p.parent
        meta = classify_module(p, root)
        modules = [_module_item(p, root, meta)]
    else:
        found = find_modules(req, limit=15, code_dir=code_dir)
        if not found.get("ready", True) and found.get("hint") and not found.get("modules"):
            return found
        modules = list(found.get("modules") or [])
        if not modules:
            return {
                "ok": False,
                "error": f"Модуль не найден по: {req}",
                "parsed": found.get("parsed"),
                "hint": "Уточни имя или code_status",
                "prefer": (
                    "Скажи пользователю, что в выгрузке не нашёл; предложи другое имя."
                ),
            }

    # CommandModule хуже Manager/Form; длина модуля не должна перебивать имя
    req_l = req.lower().replace("ё", "е")
    parsed_req = parse_nl_query(req)
    form_wanted = (parsed_req.get("form_name") or "").lower().replace("ё", "е")
    want_form = bool(parsed_req.get("want_form_module") or form_wanted)
    req_tokens = [
        t
        for t in re.findall(r"[a-zA-Zа-яА-ЯёЁ]{4,}", req_l)
        if t
        not in (
            "отчет",
            "отчёт",
            "откуда",
            "берет",
            "данные",
            "форма",
            "формы",
            "модуль",
            "модуля",
        )
    ]
    # сохранить «формадокумента» как цельный токен
    for m in re.finditer(r"форма[а-яё]{3,}", req_l):
        if m.group(0) not in req_tokens:
            req_tokens.append(m.group(0))

    def _module_pick_score(m: dict[str, Any]) -> tuple:
        role = (m.get("module_role") or m.get("kind") or "").lower()
        rel = (m.get("rel_path") or "").lower().replace("\\", "/")
        form_n = (m.get("form_name") or "").lower().replace("ё", "е")
        name_blob = (
            f"{m.get('full_name_1c') or ''} {m.get('object_name') or ''} "
            f"{m.get('form_name') or ''} {m.get('rel_path') or ''}"
        ).lower().replace("ё", "е")
        lines = int(m.get("lines") or 0)
        base = int(m.get("score") or 0) * 10  # имя/релевантность find_modules — главное
        for t in req_tokens:
            if t in name_blob or t[:5] in name_blob:
                base += 300
        # точное имя формы критично
        if form_wanted:
            if form_n == form_wanted or form_wanted in form_n or form_n in form_wanted:
                base += 2000
            elif m.get("kind") == "form_module" and form_n:
                base -= 1500
        if want_form:
            if m.get("kind") == "form_module" or "/forms/" in rel:
                base += 200
            if "managermodule" in rel or role == "manager":
                base -= 500
            if "objectmodule" in rel or role == "object":
                base -= 400
        else:
            if "managermodule" in rel or role == "manager" or m.get("kind") == "manager_module":
                base += 80
            if "objectmodule" in rel or role == "object":
                base += 50
            if "/forms/" in rel or m.get("kind") == "form_module":
                base += 60
        if "commandmodule" in rel or role == "command":
            base -= 400
        base += min(lines, 800) // 100
        return (base, lines)

    modules_sorted = sorted(modules, key=_module_pick_score, reverse=True)
    best = modules_sorted[0]
    best_path = str(best["path"])
    outline = module_outline(path=best_path, max_symbols=40, code_dir=code_dir)

    max_hits = max(3, min(int(max_hits or 10), 15))
    try:
        lines = Path(best_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return {"error": str(e), "module": best}

    hits: list[dict[str, Any]] = []
    for pat in _DATA_SOURCE_PATTERNS:
        if len(hits) >= max_hits:
            break
        pl = pat.lower()
        for i, line in enumerate(lines):
            if pl not in line.lower():
                continue
            stripped = line.strip()
            if stripped.startswith("//") and "ВЫБРАТЬ" not in line.upper():
                if not any(
                    x in line
                    for x in ("Регистр", "Справочник", "Документ", "Запрос", "Таблица")
                ):
                    continue
            start = max(0, i - 1)
            end = min(len(lines), i + 2)
            snip_parts = []
            for j in range(start, end):
                mark = ">" if j == i else " "
                snip_parts.append(f"{mark}{j + 1}:{_clip_line(lines[j])}")
            hits.append(
                {
                    "pattern": pat,
                    "line": i + 1,
                    "text": _clip_line(line),
                    "snippet": "\n".join(snip_parts),
                    "file": best.get("rel_path") or best_path,
                }
            )
            if len(hits) >= max_hits:
                break

    # Отчёты 1С: запросы часто в Templates/*Схема*/Template.xml (СКД), не в Module.bsl
    skd_info = _scan_report_templates(Path(best_path), max_hits=max_hits)
    for h in skd_info.get("hits") or []:
        if len(hits) >= max_hits + 8:
            break
        hits.append(h)

    sources: list[str] = list(skd_info.get("sources") or [])
    src_re = re.compile(
        r"(РегистрСведений|РегистрНакопления|РегистрБухгалтерии|Справочник|Документ|"
        r"ПланСчетов)"
        r"\.([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)"
    )
    for h in hits:
        t = h.get("text") or ""
        for m in src_re.finditer(t):
            sources.append(f"{m.group(1)}.{m.group(2)}")
        if "ДинамическийСписок" in t or "DynamicList" in t:
            sources.append("ДинамическийСписок (форма)")
        if "ОсновнаяТаблица" in t or "MainTable" in t:
            sources.append("ОсновнаяТаблица динамического списка")
        if "НаборыДанных" in t:
            sources.append("Схема компоновки данных (НаборыДанных)")
        if "Хозрасчетный" in t:
            sources.append("РегистрБухгалтерии.Хозрасчетный")

    # весь ManagerModule / Form — доп. проход по регистрам
    for m in src_re.finditer("\n".join(lines)):
        sources.append(f"{m.group(1)}.{m.group(2)}")
    if "Хозрасчетный" in "\n".join(lines):
        sources.append("РегистрБухгалтерии.Хозрасчетный")

    seen_s: set[str] = set()
    sources_u: list[str] = []
    for s in sources:
        if s not in seen_s:
            seen_s.add(s)
            sources_u.append(s)

    result: dict[str, Any] = {
        "ok": True,
        "request": req,
        "module": {
            "path": best_path,
            "rel_path": best.get("rel_path"),
            "full_name_1c": best.get("full_name_1c"),
            "kind_ru": best.get("kind_ru"),
            "top_folder_en": best.get("top_folder_en"),
            "top_folder_ru": best.get("top_folder_ru"),
            "lines": best.get("lines"),
            "path_explained": best.get("path_explained"),
        },
        "candidates": [
            {
                "full_name_1c": m.get("full_name_1c"),
                "kind_ru": m.get("kind_ru"),
                "rel_path": m.get("rel_path"),
                "score": m.get("score"),
            }
            for m in modules_sorted[:5]
        ],
        "outline_sample": (outline.get("symbols") or [])[:25],
        "outline_sample_total": len(outline.get("symbols") or []),
        "regions": (outline.get("regions") or [])[:12],
        "regions_total": len(outline.get("regions") or []),
        "modules_total": len(modules_sorted),
        "data_source_hits": hits[: max_hits + 8],
        "likely_sources": sources_u[:15],
        "likely_sources_total": len(sources_u),
        "skd_templates": skd_info.get("templates") or [],
        "prefer": (
            "Ответь по likely_sources + data_source_hits + full_name_1c. "
            "Если нужен ПОЛНЫЙ текст ВЫБРАТЬ из СКД → read_skd_query(path=module.path) "
            "или read_skd_query(request=имя отчёта). "
            "Не говори «читаю только bsl / XML недоступен»."
        ),
        "note": (
            "ONE-SHOT. У отчётов запросы в Templates/.../Template.xml (СКД). "
            "Полный текст: tool read_skd_query."
        ),
    }

    # текст запроса / СКД — сразу приложить queries (универсально для любого отчёта)
    req_l = req.lower().replace("ё", "е")
    want_query = any(
        x in req_l
        for x in (
            "текст запрос",
            "запрос в скл",
            "запрос в скд",
            "шаблон скд",
            "шаблон скл",
            "скд",
            "скл",
            "схема компонов",
            "набор данных",
            "изучи запрос",
            "покажи запрос",
        )
    )
    # для отчётов всегда краткий skd summary
    is_report = (best.get("top_folder_en") or "") == "Reports" or str(
        best.get("full_name_1c") or ""
    ).startswith("Отчет.")
    if want_query or is_report:
        skd_q = extract_skd_queries(
            best_path,
            max_queries=4 if want_query else 3,
            max_chars_each=2800 if want_query else 1200,
            code_dir=code_dir,
        )
        if skd_q.get("queries"):
            result["skd_queries"] = {
                "queries_count": skd_q.get("queries_count"),
                "tables_all": skd_q.get("tables_all"),
                "summaries": [
                    {
                        "index": q.get("index"),
                        "tables": q.get("tables"),
                        "from_preview": q.get("from_preview"),
                        "text_preview": (q.get("text") or "")[
                            : (1200 if want_query else 500)
                        ],
                        "file": q.get("file"),
                        "line": q.get("line"),
                    }
                    for q in (skd_q.get("queries") or [])
                ],
            }
            if want_query:
                result["skd_queries"]["first_query_text"] = (
                    (skd_q.get("queries") or [{}])[0].get("text") or ""
                )[:2800]
                result["prefer"] = (
                    "Текст запросов СКД в skd_queries (first_query_text / summaries). "
                    "Разбери tables_all и поля. "
                    "Другой запрос: read_skd_query(path=module.path, query_index=N). "
                    "НЕ пиши, что XML/шаблон недоступен."
                )
            else:
                result["prefer"] = (
                    "likely_sources + skd_queries.summaries. "
                    "Полный ВЫБРАТЬ: read_skd_query(path=module.path). "
                    "Не говори «только bsl»."
                )

    return result


def _object_root_from_module(module_path: Path) -> Path | None:
    """Reports/Name/Ext/ManagerModule.bsl → Reports/Name."""
    p = module_path.resolve()
    cur = p.parent
    for _ in range(8):
        if (cur / "Templates").is_dir() or (cur / "Forms").is_dir():
            if cur.name not in ("Ext", "Forms", "Commands", "Templates"):
                return cur
        if cur.name in ("Ext", "Forms", "Commands") and cur.parent:
            cand = cur.parent
            if (cand / "Templates").is_dir() or cand.name not in ("Ext",):
                return cand
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def _unescape_xml_query(q: str) -> str:
    return (
        (q or "")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("\r\n", "\n")
        .strip()
    )


def extract_skd_queries(
    object_or_module_path: str | Path,
    *,
    max_queries: int = 6,
    max_chars_each: int = 3500,
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    Достать тексты ВЫБРАТЬ из Templates СКД.
    Runtime: сначала Qdrant (content_type=Запрос 1С / path Templates), иначе FS dump.
    """
    raw = str(object_or_module_path or "").strip()
    # нотация 1С («Отчет.X», «Отчет.X.Макет.Y») → папка объекта выгрузки
    if raw and "/" not in raw and "\\" not in raw:
        try:
            from tools.path_norm import parse_code_path

            _spec = parse_code_path(raw)
            if _spec is not None:
                raw = _spec.base
        except Exception:
            pass
    if not raw:
        return {
            "error": "Нужен path к модулю/папке отчёта или object name",
            "hint": "extract_skd_queries после inspect_code: path=module.path "
            "или find_modules → Reports/Имя",
        }

    # --- Qdrant-first (универсально) ---
    try:
        from vector.catalog import runtime_fs_allowed
        from vector.search import search_code_vectors

        if not runtime_fs_allowed():
            # filter by object name from path or string
            obj_hint = raw.replace("\\", "/").split("/")
            obj_name = ""
            for i, part in enumerate(obj_hint):
                if part in (
                    "Reports",
                    "DataProcessors",
                    "Documents",
                    "Catalogs",
                    "Отчеты",
                    "Обработки",
                ) and i + 1 < len(obj_hint):
                    obj_name = obj_hint[i + 1]
                    break
            if not obj_name and "/" not in raw and "\\" not in raw:
                obj_name = raw.split(".")[-1]
            q = "ВЫБРАТЬ"
            r = search_code_vectors(
                q,
                limit=max(6, max_queries * 2),
                match_mode="hybrid",
                content_type="Запрос 1С",
                module_filter=obj_name or raw.replace("\\", "/").split("/")[0:3][-1]
                if "/" in raw.replace("\\", "/")
                else obj_name,
            )
            if not r.get("ok") or not r.get("matches"):
                r = search_code_vectors(
                    f"ВЫБРАТЬ {obj_name}".strip(),
                    limit=max(6, max_queries * 2),
                    match_mode="hybrid",
                    module_filter=obj_name or "",
                )
            queries = []
            for i, m in enumerate((r.get("matches") or [])[:max_queries]):
                body = (m.get("snippet") or "")[:max_chars_each]
                queries.append(
                    {
                        "index": i + 1,
                        "file": m.get("path"),
                        "chars": len(body),
                        "text": body,
                        "source": "qdrant",
                        "match_kind": m.get("match_kind"),
                    }
                )
            return {
                "ok": bool(queries),
                "source": "qdrant",
                "object_hint": obj_name or raw,
                "queries_count": len(queries),
                "queries": queries,
                "prefer": (
                    "Покажи tables/поля из queries[].text. "
                    "read_skd_query / search_code(match exact ВЫБРАТЬ) для уточнения."
                ),
                "note": "СКД/запросы из Qdrant (не FS Templates).",
            }
    except Exception:
        pass

    p = Path(raw)
    if not p.exists():
        # попробовать как rel / имя объекта
        found = find_modules(raw, limit=5, code_dir=code_dir)
        mods = found.get("modules") or []
        if not mods:
            return {
                "error": f"Не найден путь/объект: {raw}",
                "hint": "inspect_code(request=…) → module.path; нужен index Qdrant с Query-чанками",
            }
        p = Path(mods[0]["path"])

    if p.is_file():
        root = _object_root_from_module(p)
    else:
        root = p if p.is_dir() else p.parent
    if root is None or not root.is_dir():
        return {"error": f"Не удалось найти корень объекта для {raw}"}

    tpl = root / "Templates"
    if not tpl.is_dir():
        return {
            "ok": False,
            "object_root": str(root),
            "queries": [],
            "note": "Папки Templates нет — у объекта может не быть СКД (только BSL).",
            "prefer": "read_code по ManagerModule / search_code(query='ВЫБРАТЬ').",
        }

    queries: list[dict[str, Any]] = []
    sources: list[str] = []
    src_re = re.compile(
        r"(РегистрСведений|РегистрНакопления|РегистрБухгалтерии|Справочник|Документ|"
        r"ПланСчетов)\.([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)"
    )
    xml_files = sorted(
        tpl.rglob("*.xml"),
        key=lambda x: x.stat().st_size if x.exists() else 0,
        reverse=True,
    )

    for xf in xml_files[:10]:
        if len(queries) >= max_queries:
            break
        try:
            if xf.stat().st_size > 4_000_000:
                continue
            text = xf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            cr = resolve_code_roots(code_dir)
            rel = (
                str(xf.relative_to(cr.roots[0])).replace("\\", "/")
                if cr.roots
                else xf.name
            )
        except Exception:
            rel = str(xf)

        for mi, m in enumerate(re.finditer(r"<query>(.*?)</query>", text, re.S | re.I)):
            if len(queries) >= max_queries:
                break
            body = _unescape_xml_query(m.group(1))
            if not body or len(body) < 20:
                continue
            if (
                "ВЫБРАТЬ" not in body.upper()
                and "SELECT" not in body.upper()
                and "ИЗ" not in body.upper()
            ):
                continue
            # номер строки в xml
            line_no = text.count("\n", 0, m.start()) + 1
            truncated = len(body) > max_chars_each
            shown = body[:max_chars_each] + ("\n…[truncated]" if truncated else "")
            tables = sorted({f"{a}.{b}" for a, b in src_re.findall(body)})
            if "Хозрасчетный" in body and "РегистрБухгалтерии.Хозрасчетный" not in tables:
                tables.append("РегистрБухгалтерии.Хозрасчетный")
            for t in tables:
                if t not in sources:
                    sources.append(t)
            # краткий preview — FROM/ИЗ
            iz = ""
            m_iz = re.search(
                r"\b(?:ИЗ|FROM)\b\s+(.+?)(?:\b(?:ГДЕ|WHERE|СГРУППИРОВАТЬ|GROUP|УПОРЯДОЧИТЬ|ORDER|;\s*$))",
                body,
                re.S | re.I,
            )
            if m_iz:
                iz = re.sub(r"\s+", " ", m_iz.group(1))[:200]
            queries.append(
                {
                    "index": len(queries) + 1,
                    "file": rel,
                    "line": line_no,
                    "chars": len(body),
                    "truncated": truncated,
                    "tables": tables[:20],
                    "tables_total": len(tables),
                    "from_preview": iz,
                    "text": shown,
                }
            )

    full_name = ""
    parts = root.as_posix().replace("\\", "/").split("/")
    if "Reports" in parts:
        i = parts.index("Reports")
        if i + 1 < len(parts):
            full_name = f"Отчет.{parts[i + 1]}"
    elif "DataProcessors" in parts:
        i = parts.index("DataProcessors")
        if i + 1 < len(parts):
            full_name = f"Обработка.{parts[i + 1]}"

    return {
        "ok": True,
        "full_name_1c": full_name,
        "object_root": str(root),
        "templates_dir": str(tpl),
        "queries_count": len(queries),
        "queries": queries,
        "tables_all": sources[:30],
        "tables_all_total": len(sources),
        "prefer": (
            "Покажи пользователю tables_all и ключевые фрагменты queries[].text "
            "(ИЗ / поля). Полный запрос может быть длинным — не копируй всё в чат, "
            "если >50 строк: таблица-источник + смысл полей. "
            "Текст УЖЕ в ответе tool — не говори «не могу прочитать XML»."
        ),
        "note": (
            "Тексты запросов СКД из Templates/**/Template.xml. "
            "Лимит символов на query — budget 32k."
        ),
    }


def read_skd_query(
    request: str = "",
    *,
    path: str = "",
    query_index: int = 0,
    max_chars: int = 6000,
    code_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    Прочитать текст запроса СКД. request=имя/фраза отчёта или path к модулю.
    query_index=1..N (0 = все кратко + первый полностью).
    """
    target = (path or "").strip()
    if not target and request:
        # найти отчёт/модуль
        found = find_modules(request, limit=8, code_dir=code_dir)
        mods = found.get("modules") or []
        # предпочесть ManagerModule / object under Reports
        def _score(m: dict[str, Any]) -> int:
            rel = (m.get("rel_path") or "").lower()
            sc = int(m.get("score") or 0)
            if "reports/" in rel or "dataprocessors/" in rel:
                sc += 50
            if "managermodule" in rel:
                sc += 40
            if "commandmodule" in rel:
                sc -= 30
            return sc

        mods = sorted(mods, key=_score, reverse=True)
        if not mods:
            # inspect fallback
            insp = inspect_code(request=request, code_dir=code_dir)
            if insp.get("module", {}).get("path"):
                target = str(insp["module"]["path"])
            else:
                # отчёта с таким именем нет — предложить похожие отчёты этой CF (каталог выгрузки)
                options: list[str] = []
                try:
                    from tools.object_catalog import resolve_full_name

                    rc = resolve_full_name(request, class_prefix="Отчет", top_k=5)
                    options = [f"{c['full_name']}" + (f" — {c['synonym']}" if c.get("synonym") else "") for c in rc.get("candidates") or []]
                except Exception:
                    options = []
                return {
                    "ok": False,
                    "error": f"Не найден объект для СКД: {request}",
                    "need_clarification": bool(options),
                    "question": "Такого отчёта в этой конфигурации нет. Возможно, имелся в виду один из этих?" if options else None,
                    "options": options,
                    "hint": "inspect_code → module.path, затем read_skd_query(path=…)",
                }
        else:
            target = mods[0]["path"]

    data = extract_skd_queries(
        target, max_queries=8, max_chars_each=max_chars, code_dir=code_dir
    )
    if data.get("error"):
        return data
    qs = data.get("queries") or []
    if not qs:
        return {
            **data,
            "prefer": (
                "СКД-запросов нет. Смотри ManagerModule BSL: "
                "read_code / search_code(query='ВЫБРАТЬ', module_filter=…)."
            ),
        }

    idx = int(query_index or 0)
    if idx > 0:
        # 1-based
        if idx > len(qs):
            return {
                "error": f"query_index={idx}, всего {len(qs)}",
                "queries_count": len(qs),
                "tables_all": data.get("tables_all"),
            }
        one = qs[idx - 1]
        return {
            "ok": True,
            "full_name_1c": data.get("full_name_1c"),
            "query": one,
            "tables_all": data.get("tables_all"),
            "prefer": (
                "Разбери text: таблицы ИЗ, поля ВЫБРАТЬ, смысл для пользователя. "
                "Не утверждай, что XML недоступен."
            ),
        }

    # все: краткие summaries + первый полный (урезанный)
    summaries = [
        {
            "index": q["index"],
            "file": q.get("file"),
            "line": q.get("line"),
            "chars": q.get("chars"),
            "tables": q.get("tables"),
            "from_preview": q.get("from_preview"),
            "text_preview": (q.get("text") or "")[:600]
            + ("…" if (q.get("chars") or 0) > 600 else ""),
        }
        for q in qs
    ]
    first_full = dict(qs[0])
    if len(first_full.get("text") or "") > max_chars:
        first_full["text"] = (first_full["text"] or "")[:max_chars] + "\n…[truncated]"
        first_full["truncated"] = True

    return {
        "ok": True,
        "full_name_1c": data.get("full_name_1c"),
        "object_root": data.get("object_root"),
        "queries_count": len(qs),
        "tables_all": data.get("tables_all"),
        "summaries": summaries,
        "first_query_full": first_full,
        "prefer": (
            "1) Перечисли tables_all. 2) По summaries[].from_preview — откуда данные. "
            "3) По first_query_full.text — поля и смысл. "
            "Другой запрос: read_skd_query(path=…, query_index=N). "
            "НЕ говори «инструменты читают только bsl» — текст запроса УЖЕ здесь."
        ),
        "note": data.get("note"),
    }


def _scan_report_templates(module_path: Path, *, max_hits: int = 10) -> dict[str, Any]:
    """Сканировать Templates СКД рядом с отчётом (XML с ВЫБРАТЬ / Регистр…)."""
    sources: list[str] = []
    hits: list[dict[str, Any]] = []
    templates: list[str] = []

    # Reports/Name/Ext/ManagerModule.bsl → Reports/Name
    p = module_path.resolve()
    object_root: Path | None = None
    for parent in [p.parent, p.parent.parent, p.parent.parent.parent]:
        if parent.name in ("Ext", "Forms", "Commands", "Templates"):
            continue
        # parent of Ext
        if (parent / "Templates").is_dir() or (parent / "Ext").is_dir():
            object_root = parent
            break
        if parent.parent and (parent.parent / "Templates").is_dir():
            object_root = parent.parent
            break
    # walk up until Templates found
    cur = p.parent
    for _ in range(6):
        if (cur / "Templates").is_dir():
            object_root = cur
            break
        if cur.parent == cur:
            break
        cur = cur.parent

    if object_root is None:
        return {"sources": [], "hits": [], "templates": []}

    tpl_root = object_root / "Templates"
    if not tpl_root.is_dir():
        return {"sources": [], "hits": [], "templates": []}

    src_re = re.compile(
        r"(РегистрСведений|РегистрНакопления|РегистрБухгалтерии|Справочник|Документ|"
        r"ПланСчетов)"
        r"\.([A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]*)"
    )
    xml_files = list(tpl_root.rglob("*.xml"))
    # largest first — main SKD
    xml_files.sort(key=lambda x: x.stat().st_size if x.exists() else 0, reverse=True)

    for xf in xml_files[:8]:
        try:
            size = xf.stat().st_size
            if size > 3_000_000:
                continue
            text = xf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(xf)
        try:
            cr = resolve_code_roots()
            if cr.roots:
                rel = str(xf.relative_to(cr.roots[0])).replace("\\", "/")
        except Exception:
            rel = xf.name
        templates.append(rel)

        for m in src_re.finditer(text):
            sources.append(f"{m.group(1)}.{m.group(2)}")
        if "Хозрасчетный" in text:
            sources.append("РегистрБухгалтерии.Хозрасчетный")
        if "<query>" in text.lower() or "ВЫБРАТЬ" in text:
            sources.append("Запрос СКД (схема компоновки данных)")

        # сниппеты строк с регистрами / ВЫБРАТЬ
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if len(hits) >= max_hits:
                break
            if not any(
                x in line
                for x in (
                    "РегистрБухгалтерии",
                    "РегистрСведений",
                    "РегистрНакопления",
                    "ВЫБРАТЬ",
                    "Хозрасчетный.Обороты",
                )
            ):
                continue
            # skip pure type xmlns noise
            if "xmlns" in line and "Регистр" not in line:
                continue
            hits.append(
                {
                    "pattern": "SKD/template",
                    "line": i + 1,
                    "text": _clip_line(line.replace("&amp;", "&"), 280),
                    "snippet": _clip_line(line.replace("&amp;", "&"), 280),
                    "file": rel,
                }
            )
        if len(hits) >= max_hits:
            break

    # unique sources
    seen: set[str] = set()
    sources_u = []
    for s in sources:
        if s not in seen:
            seen.add(s)
            sources_u.append(s)

    return {"sources": sources_u, "hits": hits, "templates": templates[:6]}
