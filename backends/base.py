"""Абстрактный бэкенд 1С — единый контракт для любых конфигураций."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


# Классы метаданных (как в дереве конфигуратора). query_prefix — для ВЫБРАТЬ где применимо.
# dump_folder — имя папки выгрузки (EN), для fallback list из dump.
METADATA_TYPES: dict[str, dict[str, str]] = {
    "Подсистемы": {
        "query_prefix": "Подсистема",
        "full_name_prefix": "Подсистема",
        "com_attr": "Subsystems",
        "com_ru": "Подсистемы",
        "dump_folder": "Subsystems",
    },
    "Справочники": {
        "query_prefix": "Справочник",
        "full_name_prefix": "Справочник",
        "com_attr": "Catalogs",
        "com_ru": "Справочники",
        "dump_folder": "Catalogs",
    },
    "Документы": {
        "query_prefix": "Документ",
        "full_name_prefix": "Документ",
        "com_attr": "Documents",
        "com_ru": "Документы",
        "dump_folder": "Documents",
    },
    "ЖурналыДокументов": {
        "query_prefix": "ЖурналДокументов",
        "full_name_prefix": "ЖурналДокументов",
        "com_attr": "DocumentJournals",
        "com_ru": "ЖурналыДокументов",
        "dump_folder": "DocumentJournals",
    },
    "РегистрыСведений": {
        "query_prefix": "РегистрСведений",
        "full_name_prefix": "РегистрСведений",
        "com_attr": "InformationRegisters",
        "com_ru": "РегистрыСведений",
        "dump_folder": "InformationRegisters",
    },
    "РегистрыНакопления": {
        "query_prefix": "РегистрНакопления",
        "full_name_prefix": "РегистрНакопления",
        "com_attr": "AccumulationRegisters",
        "com_ru": "РегистрыНакопления",
        "dump_folder": "AccumulationRegisters",
    },
    "РегистрыБухгалтерии": {
        "query_prefix": "РегистрБухгалтерии",
        "full_name_prefix": "РегистрБухгалтерии",
        "com_attr": "AccountingRegisters",
        "com_ru": "РегистрыБухгалтерии",
        "dump_folder": "AccountingRegisters",
    },
    "РегистрыРасчета": {
        "query_prefix": "РегистрРасчета",
        "full_name_prefix": "РегистрРасчета",
        "com_attr": "CalculationRegisters",
        "com_ru": "РегистрыРасчета",
        "dump_folder": "CalculationRegisters",
    },
    "Перечисления": {
        "query_prefix": "Перечисление",
        "full_name_prefix": "Перечисление",
        "com_attr": "Enums",
        "com_ru": "Перечисления",
        "dump_folder": "Enums",
    },
    "ПланыВидовХарактеристик": {
        "query_prefix": "ПланВидовХарактеристик",
        "full_name_prefix": "ПланВидовХарактеристик",
        "com_attr": "ChartsOfCharacteristicTypes",
        "com_ru": "ПланыВидовХарактеристик",
        "dump_folder": "ChartsOfCharacteristicTypes",
    },
    "ПланыСчетов": {
        "query_prefix": "ПланСчетов",
        "full_name_prefix": "ПланСчетов",
        "com_attr": "ChartsOfAccounts",
        "com_ru": "ПланыСчетов",
        "dump_folder": "ChartsOfAccounts",
    },
    "ПланыВидовРасчета": {
        "query_prefix": "ПланВидовРасчета",
        "full_name_prefix": "ПланВидовРасчета",
        "com_attr": "ChartsOfCalculationTypes",
        "com_ru": "ПланыВидовРасчета",
        "dump_folder": "ChartsOfCalculationTypes",
    },
    "БизнесПроцессы": {
        "query_prefix": "БизнесПроцесс",
        "full_name_prefix": "БизнесПроцесс",
        "com_attr": "BusinessProcesses",
        "com_ru": "БизнесПроцессы",
        "dump_folder": "BusinessProcesses",
    },
    "Задачи": {
        "query_prefix": "Задача",
        "full_name_prefix": "Задача",
        "com_attr": "Tasks",
        "com_ru": "Задачи",
        "dump_folder": "Tasks",
    },
    "ОбщиеМодули": {
        "query_prefix": "ОбщийМодуль",
        "full_name_prefix": "ОбщийМодуль",
        "com_attr": "CommonModules",
        "com_ru": "ОбщиеМодули",
        "dump_folder": "CommonModules",
    },
    "ОбщиеФормы": {
        "query_prefix": "ОбщаяФорма",
        "full_name_prefix": "ОбщаяФорма",
        "com_attr": "CommonForms",
        "com_ru": "ОбщиеФормы",
        "dump_folder": "CommonForms",
    },
    "ОбщиеКоманды": {
        "query_prefix": "ОбщаяКоманда",
        "full_name_prefix": "ОбщаяКоманда",
        "com_attr": "CommonCommands",
        "com_ru": "ОбщиеКоманды",
        "dump_folder": "CommonCommands",
    },
    "ОбщиеРеквизиты": {
        "query_prefix": "ОбщийРеквизит",
        "full_name_prefix": "ОбщийРеквизит",
        "com_attr": "CommonAttributes",
        "com_ru": "ОбщиеРеквизиты",
        "dump_folder": "CommonAttributes",
    },
    "ОбщиеМакеты": {
        "query_prefix": "ОбщийМакет",
        "full_name_prefix": "ОбщийМакет",
        "com_attr": "CommonTemplates",
        "com_ru": "ОбщиеМакеты",
        "dump_folder": "CommonTemplates",
    },
    "ОбщиеКартинки": {
        "query_prefix": "ОбщаяКартинка",
        "full_name_prefix": "ОбщаяКартинка",
        "com_attr": "CommonPictures",
        "com_ru": "ОбщиеКартинки",
        "dump_folder": "CommonPictures",
    },
    "Отчеты": {
        "query_prefix": "Отчет",
        "full_name_prefix": "Отчет",
        "com_attr": "Reports",
        "com_ru": "Отчеты",
        "dump_folder": "Reports",
    },
    "Обработки": {
        "query_prefix": "Обработка",
        "full_name_prefix": "Обработка",
        "com_attr": "DataProcessors",
        "com_ru": "Обработки",
        "dump_folder": "DataProcessors",
    },
    "Роли": {
        "query_prefix": "Роль",
        "full_name_prefix": "Роль",
        "com_attr": "Roles",
        "com_ru": "Роли",
        "dump_folder": "Roles",
    },
    "Константы": {
        "query_prefix": "Константа",
        "full_name_prefix": "Константа",
        "com_attr": "Constants",
        "com_ru": "Константы",
        "dump_folder": "Constants",
    },
    "ПараметрыСеанса": {
        "query_prefix": "ПараметрСеанса",
        "full_name_prefix": "ПараметрСеанса",
        "com_attr": "SessionParameters",
        "com_ru": "ПараметрыСеанса",
        "dump_folder": "SessionParameters",
    },
    "ПланыОбмена": {
        "query_prefix": "ПланОбмена",
        "full_name_prefix": "ПланОбмена",
        "com_attr": "ExchangePlans",
        "com_ru": "ПланыОбмена",
        "dump_folder": "ExchangePlans",
    },
    "КритерииОтбора": {
        "query_prefix": "КритерийОтбора",
        "full_name_prefix": "КритерийОтбора",
        "com_attr": "FilterCriteria",
        "com_ru": "КритерииОтбора",
        "dump_folder": "FilterCriteria",
    },
    "ПодпискиНаСобытия": {
        "query_prefix": "ПодпискаНаСобытие",
        "full_name_prefix": "ПодпискаНаСобытие",
        "com_attr": "EventSubscriptions",
        "com_ru": "ПодпискиНаСобытия",
        "dump_folder": "EventSubscriptions",
    },
    "РегламентныеЗадания": {
        "query_prefix": "РегламентноеЗадание",
        "full_name_prefix": "РегламентноеЗадание",
        "com_attr": "ScheduledJobs",
        "com_ru": "РегламентныеЗадания",
        "dump_folder": "ScheduledJobs",
    },
    "ФункциональныеОпции": {
        "query_prefix": "ФункциональнаяОпция",
        "full_name_prefix": "ФункциональнаяОпция",
        "com_attr": "FunctionalOptions",
        "com_ru": "ФункциональныеОпции",
        "dump_folder": "FunctionalOptions",
    },
    "ОпределяемыеТипы": {
        "query_prefix": "ОпределяемыйТип",
        "full_name_prefix": "ОпределяемыйТип",
        "com_attr": "DefinedTypes",
        "com_ru": "ОпределяемыеТипы",
        "dump_folder": "DefinedTypes",
    },
    "ХранилищаНастроек": {
        "query_prefix": "ХранилищеНастроек",
        "full_name_prefix": "ХранилищеНастроек",
        "com_attr": "SettingsStorages",
        "com_ru": "ХранилищаНастроек",
        "dump_folder": "SettingsStorages",
    },
    "HTTPСервисы": {
        "query_prefix": "HTTPСервис",
        "full_name_prefix": "HTTPСервис",
        "com_attr": "HTTPServices",
        "com_ru": "HTTPСервисы",
        "dump_folder": "HTTPServices",
    },
    "WebСервисы": {
        "query_prefix": "WebСервис",
        "full_name_prefix": "WebСервис",
        "com_attr": "WebServices",
        "com_ru": "WebСервисы",
        "dump_folder": "WebServices",
    },
    "WSСсылки": {
        "query_prefix": "WSСсылка",
        "full_name_prefix": "WSСсылка",
        "com_attr": "WSReferences",
        "com_ru": "WSСсылки",
        "dump_folder": "WSReferences",
    },
    "ПакетыXDTO": {
        "query_prefix": "ПакетXDTO",
        "full_name_prefix": "ПакетXDTO",
        "com_attr": "XDTOPackages",
        "com_ru": "ПакетыXDTO",
        "dump_folder": "XDTOPackages",
    },
    "Языки": {
        "query_prefix": "Язык",
        "full_name_prefix": "Язык",
        "com_attr": "Languages",
        "com_ru": "Языки",
        "dump_folder": "Languages",
    },
    "Последовательности": {
        "query_prefix": "Последовательность",
        "full_name_prefix": "Последовательность",
        "com_attr": "Sequences",
        "com_ru": "Последовательности",
        "dump_folder": "Sequences",
    },
    "ГруппыКоманд": {
        "query_prefix": "ГруппаКоманд",
        "full_name_prefix": "ГруппаКоманд",
        "com_attr": "CommandGroups",
        "com_ru": "ГруппыКоманд",
        "dump_folder": "CommandGroups",
    },
    "ЭлементыСтиля": {
        "query_prefix": "ЭлементСтиля",
        "full_name_prefix": "ЭлементСтиля",
        "com_attr": "StyleItems",
        "com_ru": "ЭлементыСтиля",
        "dump_folder": "StyleItems",
    },
}

# Классы выгрузки, у которых бывает Ext/ObjectModule|ManagerModule|RecordSetModule
# (папки конфигуратора, не имена объектов CF).
OBJECT_MODULE_DUMP_FOLDERS: tuple[str, ...] = tuple(
    str(METADATA_TYPES[k]["dump_folder"])
    for k in (
        "Документы",
        "Справочники",
        "Обработки",
        "Отчеты",
        "РегистрыСведений",
        "РегистрыНакопления",
        "РегистрыБухгалтерии",
        "РегистрыРасчета",
        "ПланыВидовХарактеристик",
        "ПланыСчетов",
        "ПланыВидовРасчета",
        "БизнесПроцессы",
        "Задачи",
        "ПланыОбмена",
        "Последовательности",
    )
    if k in METADATA_TYPES
)


def dump_folder_for_full_name_prefix(prefix: str) -> str:
    """Документ → Documents. Пусто, если префикс не из METADATA_TYPES."""
    p = (prefix or "").strip().replace("Отчёт", "Отчет")
    if not p:
        return ""
    for meta in METADATA_TYPES.values():
        if meta.get("full_name_prefix") == p:
            return str(meta.get("dump_folder") or "")
    return ""


def full_name_prefix_for_dump_folder(folder: str) -> str:
    """Documents → Документ."""
    f = (folder or "").strip()
    if not f:
        return ""
    for meta in METADATA_TYPES.values():
        if meta.get("dump_folder") == f:
            return str(meta.get("full_name_prefix") or "")
    return ""


def is_typed_full_name(name: str) -> bool:
    """Есть класс из METADATA_TYPES: Документ.X / Справочник.Y / … — не голое имя."""
    s = (name or "").strip()
    if "." not in s:
        return False
    pref, rest = s.split(".", 1)
    return bool(rest.strip()) and bool(dump_folder_for_full_name_prefix(pref))


def typed_full_name_from_path(source_path: str, object_name: str = "") -> str:
    """Documents/Имя/Ext/ObjectModule.bsl → Документ.Имя (класс из папки выгрузки)."""
    sp = (source_path or "").replace("\\", "/").strip()
    parts = [p for p in sp.split("/") if p]
    if len(parts) < 2:
        return ""
    pref = full_name_prefix_for_dump_folder(parts[0])
    on = (object_name or parts[1]).strip()
    if pref and on:
        return f"{pref}.{on}"
    return ""


# алиасы «единственное число / EN» → канонический ключ METADATA_TYPES
METADATA_TYPE_ALIASES: dict[str, str] = {
    "подсистема": "Подсистемы",
    "подсистемы": "Подсистемы",
    "subsystems": "Подсистемы",
    "subsystem": "Подсистемы",
    "документ": "Документы",
    "документы": "Документы",
    "documents": "Документы",
    "справочник": "Справочники",
    "справочники": "Справочники",
    "catalogs": "Справочники",
    "отчет": "Отчеты",
    "отчёт": "Отчеты",
    "отчеты": "Отчеты",
    "отчёты": "Отчеты",
    "reports": "Отчеты",
    "обработка": "Обработки",
    "обработки": "Обработки",
    "dataprocessors": "Обработки",
    "роль": "Роли",
    "роли": "Роли",
    "roles": "Роли",
    "общиймодуль": "ОбщиеМодули",
    "общиемодули": "ОбщиеМодули",
    "commonmodules": "ОбщиеМодули",
    "общаяформа": "ОбщиеФормы",
    "общиеформы": "ОбщиеФормы",
    "commonforms": "ОбщиеФормы",
    "регистрсведений": "РегистрыСведений",
    "регистрысведений": "РегистрыСведений",
    "informationregisters": "РегистрыСведений",
    "регистрнакопления": "РегистрыНакопления",
    "регистрынакопления": "РегистрыНакопления",
    "accumulationregisters": "РегистрыНакопления",
    "регистрбухгалтерии": "РегистрыБухгалтерии",
    "регистрыбухгалтерии": "РегистрыБухгалтерии",
    "accountingregisters": "РегистрыБухгалтерии",
    "константа": "Константы",
    "константы": "Константы",
    "constants": "Константы",
    "параметрсеанса": "ПараметрыСеанса",
    "параметрысеанса": "ПараметрыСеанса",
    "sessionparameters": "ПараметрыСеанса",
    "httpservices": "HTTPСервисы",
    "httpсервис": "HTTPСервисы",
    "httpсервисы": "HTTPСервисы",
    "планобмена": "ПланыОбмена",
    "планыобмена": "ПланыОбмена",
    "exchangeplans": "ПланыОбмена",
    "регламентноезадание": "РегламентныеЗадания",
    "регламентныезадания": "РегламентныеЗадания",
    "scheduledjobs": "РегламентныеЗадания",
    "журналыдокументов": "ЖурналыДокументов",
    "журналдокументов": "ЖурналыДокументов",
    "documentjournals": "ЖурналыДокументов",
}


def normalize_metadata_type(name: str) -> str | None:
    """Канонический ключ METADATA_TYPES или None."""
    raw = (name or "").strip()
    if not raw:
        return None
    if raw in METADATA_TYPES:
        return raw
    key = raw.lower().replace("ё", "е").replace(" ", "").replace("_", "")
    return METADATA_TYPE_ALIASES.get(key)


class OneCBackend(ABC):
    """Единый интерфейс доступа к данным 1С (не зависит от конкретной конфигурации)."""

    @abstractmethod
    def test_connection(self) -> dict[str, Any]:
        """Проверка связи и базовая информация о базе."""

    @abstractmethod
    def list_objects(self, metadata_type: str) -> dict[str, Any]:
        """Список объектов одного класса метаданных."""

    @abstractmethod
    def find_metadata(
        self,
        search_query: str = "",
        object_type: str = "Все",
        limit: int = 100,
    ) -> dict[str, Any]:
        """Поиск объектов по имени/синониму."""

    @abstractmethod
    def get_object_structure(self, full_name: str) -> dict[str, Any]:
        """Структура объекта: реквизиты, ТЧ, измерения, ресурсы."""

    @abstractmethod
    def execute_query(self, query: str, limit: int = 50) -> dict[str, Any]:
        """Выполнение запроса языка запросов 1С (ВЫБРАТЬ)."""

    def query_event_log(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Журнал регистрации платформы (опционально).
        filters: date_from, date_to, level, user, event, comment, metadata, q, limit.
        """
        return {
            "error": "Журнал регистрации не поддерживается этим backend",
            "hint": "Используйте HTTP backend + POST /eventlog (Module.bsl eventlogPOST)",
        }

    def list_sessions(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Активные сеансы / пользователи ИБ (опционально)."""
        return {
            "error": "Список сеансов не поддерживается этим backend",
            "hint": "HTTP backend + GET /sessions (Module.bsl sessionsGET)",
        }

    def close(self) -> None:
        """Закрыть HTTP/COM-ресурсы. По умолчанию ничего."""

    def list_metadata_types(self) -> dict[str, Any]:
        """Справочник поддерживаемых классов метаданных."""
        return {
            "types": [
                {
                    "type": name,
                    "query_prefix": meta["query_prefix"],
                    "full_name_example": f"{meta['full_name_prefix']}.ИмяОбъекта",
                }
                for name, meta in METADATA_TYPES.items()
            ],
            "note": (
                "full_name для запросов: Тип.Имя из find_metadata/list_* этой базы. "
                "Имена зависят от конфигурации — сначала find_metadata / list_objects."
            ),
        }
