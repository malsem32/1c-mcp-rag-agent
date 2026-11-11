# -*- coding: utf-8 -*-
"""Локализация путей выгрузки конфигурации 1С (EN → RU)."""

from __future__ import annotations

from pathlib import Path

# Корневые классы метаданных + типичные подпапки выгрузки
TRANSLATE_PATH: dict[str, str] = {
    # --- классы ---
    "AccountingRegisters": "РегистрыБухгалтерии",
    "AccumulationRegisters": "РегистрыНакопления",
    "Catalogs": "Справочники",
    "ChartsOfAccounts": "ПланыСчетов",
    "ChartsOfCalculationTypes": "ПланыВидовРасчета",
    "ChartsOfCharacteristicTypes": "ПланыВидовХарактеристик",
    "CommandGroups": "ГруппыКоманд",
    "CommonAttributes": "ОбщиеРеквизиты",
    "CommonCommands": "ОбщиеКоманды",
    "CommonForms": "ОбщиеФормы",
    "CommonModules": "ОбщиеМодули",
    "CommonPictures": "ОбщиеКартинки",
    "CommonTemplates": "ОбщиеМакеты",
    "Constants": "Константы",
    "DataProcessors": "Обработки",
    "DefinedTypes": "ОпределяемыеТипы",
    "DocumentJournals": "ЖурналыДокументов",
    "DocumentNumerators": "НумераторыДокументов",
    "Documents": "Документы",
    "Enums": "Перечисления",
    "EventSubscriptions": "ПодпискиНаСобытия",
    "ExchangePlans": "ПланыОбмена",
    "FilterCriteria": "КритерииОтбора",
    "FunctionalOptions": "ФункциональныеОпции",
    "FunctionalOptionsParameters": "ПараметрыФункциональныхОпций",
    "HTTPServices": "HTTPСервисы",
    "InformationRegisters": "РегистрыСведений",
    "Languages": "Языки",
    "Reports": "Отчеты",
    "Roles": "Роли",
    "ScheduledJobs": "РегламентныеЗадания",
    "Sequences": "Последовательности",
    "SessionParameters": "ПараметрыСеанса",
    "SettingsStorages": "ХранилищаНастроек",
    "StyleItems": "ЭлементыСтиля",
    "Subsystems": "Подсистемы",
    "WebServices": "WebСервисы",
    "WSReferences": "WSСсылки",
    "XDTOPackages": "XDTOПакеты",
    # --- подпапки объекта / приложения ---
    "Ext": "Модули",
    "Forms": "Формы",
    "Form": "Форма",
    "Templates": "Макеты",
    "Commands": "Команды",
    "Help": "Справка",
    "Flowcharts": "КартыМаршрута",
    "Tasks": "Задачи",
    "BusinessProcesses": "БизнесПроцессы",
    "ExternalDataSources": "ВнешниеИсточникиДанных",
    "IntegrationServices": "СервисыИнтеграции",
    "BotModules": "МодулиБотов",
    # --- имена модулей (stem) ---
    "Module": "Модуль",
    "ObjectModule": "МодульОбъекта",
    "ManagerModule": "МодульМенеджера",
    "RecordSetModule": "МодульНабораЗаписей",
    "CommandModule": "МодульКоманды",
    "ValueManagerModule": "МодульМенеджераЗначения",
    "ManagedApplicationModule": "МодульУправляемогоПриложения",
    "OrdinaryApplicationModule": "МодульОбычногоПриложения",
    "SessionModule": "МодульСеанса",
    "ExternalConnectionModule": "МодульВнешнегоСоединения",
    "Template": "Макет",
}



def translate_segment(name: str) -> str:
    """Перевести один сегмент пути (папку или stem файла)."""
    if not name:
        return name
    if name in TRANSLATE_PATH:
        return TRANSLATE_PATH[name]
    # Module.bsl → Модуль.bsl
    p = Path(name)
    stem, suf = p.stem, p.suffix
    if stem in TRANSLATE_PATH:
        return f"{TRANSLATE_PATH[stem]}{suf}"
    return name


def localize_parts(parts: list[str]) -> tuple[str, str, str, str]:
    """
    parts — относительный путь от корня выгрузки.

    Returns:
      class_en, class_ru, object_name, location (RU, « -> »)
    """
    if not parts:
        return "", "", "", ""
    class_en = parts[0]
    class_ru = TRANSLATE_PATH.get(class_en, class_en)
    object_name = ""
    if len(parts) >= 2:
        second = parts[1]
        object_name = Path(second).stem if "." in second else second
    display = [translate_segment(p) for p in parts]
    # первый сегмент уже class_ru
    if display:
        display[0] = class_ru
    location = " -> ".join(display)
    return class_en, class_ru, object_name, location


def relative_parts(file_path: Path, source_root: Path) -> list[str]:
    try:
        rel = file_path.resolve().relative_to(source_root.resolve())
    except ValueError:
        rel = Path(file_path.name)
    return list(rel.as_posix().split("/"))
