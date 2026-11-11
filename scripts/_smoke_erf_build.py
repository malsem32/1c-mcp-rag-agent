# -*- coding: utf-8 -*-
"""
Смоук сборки .erf (отчёт на СКД): python scripts/_smoke_erf_build.py [--platform] [--cf=D:/conf_test]
Собирает отчёт, разбирает своим парсером и (с --platform) выгружает Конфигуратором; затем негативы и разбор запроса.
Открытие в клиенте — руками: Файл → Открыть (через /Execute отчёты не открываются).
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.epf_build import BuildError  # noqa: E402
from tools.erf_build import query_fields, query_parameters, write_erf  # noqa: E402

QUERY = """ВЫБРАТЬ
	Т.Ссылка КАК Документ,
	Т.Дата КАК Период,
	ВЫБОР КОГДА Т.Проведен ТОГДА 1 ИНАЧЕ 0 КОНЕЦ КАК Проведен,
	СУММА(Т.Сумма) КАК Сумма
ИЗ
	Документ.Любой КАК Т
ГДЕ
	Т.Дата МЕЖДУ &ДатаС И &ДатаПо И Т.Номер <> "&Нет, КАК Х"
СГРУППИРОВАТЬ ПО Т.Ссылка"""

SPEC = {
    "name": "СмоукОтчёта", "synonym": "Смоук отчёта", "query": QUERY, "resources": ["Сумма"], "group_by": ["Документ"],
    "order": ["Сумма Убыв"], "module": "Процедура ПриКомпоновкеРезультата(Рез, Данные, СтандартнаяОбработка)\nКонецПроцедуры\n",
    "parameters": [
        {"name": "ДатаС", "title": "Дата с", "type": "Дата", "value": "Начало месяца"},
        {"name": "ДатаПо", "title": "Дата по", "type": "Дата", "value": "2026-12-31"},
    ],
}


def main() -> int:
    bad = 0
    use_platform = "--platform" in sys.argv
    f = query_fields("ВЫБРАТЬ А.x КАК Один, Б.Два, ВЫРАЗИТЬ(А.y КАК Число(15, 2)) КАК Три ПОМЕСТИТЬ вт ИЗ т КАК А;\n"
                     "ВЫБРАТЬ РАЗЛИЧНЫЕ вт.Один, вт.Три КАК Итого ИЗ вт КАК вт ОБЪЕДИНИТЬ ВСЕ ВЫБРАТЬ 1, 2")
    ok = f == ["Один", "Итого"] and query_fields(QUERY) == ["Документ", "Период", "Проведен", "Сумма"] and query_parameters(QUERY) == ["ДатаС", "ДатаПо"]
    print("разбор запроса:", "OK" if ok else f"FAIL {f}")
    bad += not ok
    with tempfile.TemporaryDirectory() as tmp:
        v = write_erf(SPEC, Path(tmp), platform_check=use_platform)["verify"]
    ok = bool(v.get("parsed_ok")) and v.get("name") == "СмоукОтчёта"
    print("сборка + разбор:", "OK" if ok else f"FAIL {v}")
    bad += not ok
    if use_platform:
        p = v.get("platform") or {}
        print("платформа:", "OK" if p.get("ok") else f"FAIL {p}")
        bad += not p.get("ok")
    neg = {
        "нет запроса": {"name": "Т"},
        "параметр не описан": {"name": "Т", "query": "ВЫБРАТЬ 1 КАК А ГДЕ 1 = &П"},
        "звёздочка": {"name": "Т", "query": "ВЫБРАТЬ * ИЗ Справочник.Х"},
        "колонка без псевдонима": {"name": "Т", "query": "ВЫБРАТЬ 1 + 2 ИЗ Справочник.Х"},
        "группировка по чужому полю": {"name": "Т", "query": "ВЫБРАТЬ 1 КАК А", "group_by": ["Б"]},
        "плохой тип": {"name": "Т", "query": "ВЫБРАТЬ &П КАК А", "parameters": [{"name": "П", "type": "Хрень"}]},
    }
    for name, spec in neg.items():
        try:
            write_erf(spec, Path(tempfile.gettempdir()) / "erf_smoke_neg", platform_check=False)
            print(f"негатив «{name}»: FAIL (ошибки нет)")
            bad += 1
        except BuildError as e:
            print(f"негатив «{name}»: OK ({str(e)[:50]}…)")
    ms = {
        "name": "СмоукНаборов", "synonym": "Смоук наборов",
        "datasets": [
            {"name": "Продажи", "query": "ВЫБРАТЬ Т.Ссылка КАК Ключ, Т.Сумма КАК Сумма ИЗ Документ.Любой КАК Т"},
            {"name": "Оплаты", "query": "ВЫБРАТЬ Т.Ссылка КАК КлючОплаты, Т.Сумма КАК Оплачено ИЗ Документ.Другой КАК Т ГДЕ Т.Дата > &ДатаС"},
        ],
        "links": [{"source": "Продажи", "dest": "Оплаты", "source_expression": "Ключ", "dest_expression": "КлючОплаты"}],
        "calculated_fields": [{"name": "Долг", "title": "Долг", "expression": "Сумма - Оплачено"}],
        "parameters": [{"name": "ДатаС", "type": "Дата", "value": "Начало года"}],
        "resources": ["Сумма", "Оплачено", "Долг"], "group_by": ["Ключ"], "order": ["Долг Убыв"],
    }
    with tempfile.TemporaryDirectory() as tmp:
        v = write_erf(ms, Path(tmp), platform_check=use_platform)["verify"]
    ok = bool(v.get("parsed_ok")) and (not use_platform or (v.get("platform") or {}).get("ok"))
    print("наборы + связь + вычисляемое:", "OK" if ok else f"FAIL {v}")
    bad += not ok
    for name, extra in {
        "связь на чужой набор": {"links": [{"source": "Продажи", "dest": "Нет", "source_expression": "a", "dest_expression": "b"}]},
        "вычисляемое = колонка": {"calculated_fields": [{"name": "Сумма", "expression": "1"}]},
        "вычисляемое без выражения": {"calculated_fields": [{"name": "Х"}]},
        "дубль набора": {"datasets": [ms["datasets"][0], ms["datasets"][0]]},
    }.items():
        try:
            write_erf({**ms, **extra}, Path(tempfile.gettempdir()) / "erf_smoke_neg", platform_check=False)
            print(f"негатив «{name}»: FAIL (ошибки нет)")
            bad += 1
        except BuildError as e:
            print(f"негатив «{name}»: OK ({str(e)[:50]}…)")
    fl = {**SPEC, "name": "СмоукОтборов", "filters": [
        {"field": "Сумма", "op": ">", "value": 0}, {"field": "Документ", "op": "заполнено"},
        {"field": "Проведен", "op": "=", "value": "Истина", "use": False}]}
    with tempfile.TemporaryDirectory() as tmp:
        v = write_erf(fl, Path(tmp), platform_check=use_platform)["verify"]
    ok = bool(v.get("parsed_ok")) and (not use_platform or (v.get("platform") or {}).get("ok"))
    print("отборы по умолчанию:", "OK" if ok else f"FAIL {v}")
    bad += not ok
    for name, flt in {"отбор по чужому полю": [{"field": "Нет", "value": 1}],
                      "отбор без значения": [{"field": "Сумма", "op": ">"}],
                      "неизвестное сравнение": [{"field": "Сумма", "op": "~", "value": 1}]}.items():
        try:
            write_erf({**SPEC, "filters": flt}, Path(tempfile.gettempdir()) / "erf_smoke_neg", platform_check=False)
            print(f"негатив «{name}»: FAIL (ошибки нет)")
            bad += 1
        except BuildError as e:
            print(f"негатив «{name}»: OK ({str(e)[:50]}…)")
    at = {**SPEC, "name": "СмоукРеквизитов",
          "attributes": [{"name": "Орг", "type": "Строка(50)"}, {"name": "Дата1", "type": "Дата"}, {"name": "Флаг", "type": "Булево"}],
          "tabular_sections": [{"name": "Табл", "columns": [{"name": "Сумма", "type": "Число(15,2)"}, {"name": "Текст", "type": "Строка(20)"}]}]}
    with tempfile.TemporaryDirectory() as tmp:
        v = write_erf(at, Path(tmp), platform_check=use_platform)["verify"]
    ok = bool(v.get("parsed_ok")) and v.get("attributes") == ["Орг", "Дата1", "Флаг"] and v.get("tabular_sections") == ["Табл"]         and (not use_platform or (v.get("platform") or {}).get("ok"))
    print("реквизиты и ТЧ отчёта:", "OK" if ok else f"FAIL {v}")
    bad += not ok
    for name, extra in {"дубль реквизита": {"attributes": [{"name": "А"}, {"name": "а"}]},
                        "ТЧ без колонок": {"tabular_sections": [{"name": "Т", "columns": []}]},
                        "дубль колонки": {"tabular_sections": [{"name": "Т", "columns": [{"name": "К"}, {"name": "К"}]}]}}.items():
        try:
            write_erf({**SPEC, **extra}, Path(tempfile.gettempdir()) / "erf_smoke_neg", platform_check=False)
            print(f"негатив «{name}»: FAIL (ошибки нет)")
            bad += 1
        except BuildError as e:
            print(f"негатив «{name}»: OK ({str(e)[:50]}…)")
    fm = {**at, "name": "СмоукФормы", "form_fields": True, "form_width": 640,
          "form_module": "Процедура ОргПриИзменении(Э)\nКонецПроцедуры\nПроцедура ПриОткрытии()\nКонецПроцедуры\nПроцедура КнопкаНажатие(Э)\nКонецПроцедуры\n",
          "controls": [{"type": "Кнопка", "name": "Кнопка", "title": "Проверка"}],
          "extra_forms": [{"name": "ФормаДоп", "panels": False, "controls": [{"type": "Надпись", "name": "Метка"}]}]}
    with tempfile.TemporaryDirectory() as tmp:
        v = write_erf(fm, Path(tmp), platform_check=use_platform)["verify"]
    ok = bool(v.get("parsed_ok")) and (not use_platform or (v.get("platform") or {}).get("ok"))
    print("своя форма отчёта (поля, ТЧ, кнопка, доп. форма):", "OK" if ok else f"FAIL {v}")
    bad += not ok
    # настройки варианта: платформа разбирает макет и выгружает его заново — по выгрузке видно, что она приняла
    qd = ("ВЫБРАТЬ Т.Организация КАК Организация, Т.Контрагент КАК Контрагент, Т.Период КАК Период, "
          "Т.Приход КАК Приход, Т.Расход КАК Расход ИЗ РегистрНакопления.Любой.Обороты(&ДатаС, &ДатаПо, Месяц, ) КАК Т")
    ds = {"name": "СмоукНастроек", "query": qd, "resources": ["Приход", "Расход"], "group_by": ["Организация", "Контрагент"],
          "parameters": [{"name": "ДатаС", "type": "Дата", "value": "Начало года"}, {"name": "ДатаПо", "type": "Дата", "value": "Начало следующего месяца"}],
          "user_fields": [{"name": "Знак", "cases": [{"filters": [{"field": "Приход", "op": ">", "value": 0}], "value": "Приход"}]},
                          {"name": "Сальдо", "expression": "Приход - Расход"}],
          "appearance": [{"fields": ["Расход"], "filters": [{"field": "Расход", "op": ">", "value": 1000}], "text_color": "красный", "bold": True},
                         {"filters": [{"field": "Приход", "op": "=", "value": 0}], "back_color": "#F0F0F0", "format": "ЧЦ=15; ЧДЦ=2"}],
          "output": {"ВыводитьОтбор": "DontOutput", "ТипЗаголовкаПолей": "Short"},
          "variants": [{"name": "ПоМесяцам", "structure": [{"table": {"rows": ["Организация", "Контрагент"], "columns": ["Период"]}}]},
                       {"name": "Диаграмма", "structure": [{"chart": {"points": ["Период"], "series": ["Организация"]}}]},
                       {"name": "Плоский", "group_by": [], "order": ["Период"]}]}
    if use_platform:
        from _erf_dump import dump_template
        with tempfile.TemporaryDirectory() as tmp:
            r = write_erf(ds, Path(tmp), platform_check=True)
            t = dump_template(r["path"])
        want = {"<settingsVariant>": 4, "StructureItemTable": 1, "StructureItemChart": 1, "UserFieldCase": 1, "UserFieldExpression": 1,
                "ЦветТекста": 1, "ЦветФона": 1, "ВыводитьОтбор": 1, "ТипЗаголовкаПолей": 1, "<dcsset:column>": 1, "<dcsset:row>": 1}
        miss = {k: (t.count(k), n) for k, n in want.items() if t.count(k) != n}
        ok = bool((r["verify"].get("platform") or {}).get("ok")) and not miss
        print("настройки (варианты, таблица, диаграмма, оформление, польз. поля, вывод):", "OK" if ok else f"FAIL {miss}")
        bad += not ok
    tm = {**SPEC, "name": "СмоукМакетаОтчета", "templates": [{
        "name": "Шапка", "synonym": "Шапка", "columns": [120, 120],
        "styles": {"ш": {"bold": True, "size": 12, "align": "center"}, "я": {"border": "all"}},
        "rows": [{"cells": [{"text": "Отчёт за [Период]", "style": "ш", "colspan": 2}]},
                 {"cells": [{"text": "Организация", "style": "я"}, {"param": "Организация", "style": "я"}]}],
        "areas": [{"name": "Заголовок", "rows": [0, 1]}]}]}
    with tempfile.TemporaryDirectory() as tmp:
        v = write_erf(tm, Path(tmp), platform_check=use_platform)["verify"]
    ok = bool(v.get("parsed_ok")) and (not use_platform or (v.get("platform") or {}).get("ok"))
    print("макет оформления в отчёте:", "OK" if ok else f"FAIL {v}")
    bad += not ok
    bb = {**SPEC, "name": "СмоукКнопокПанели", "form_module": "Процедура КнопкаАНажатие(Кнопка)\nКонецПроцедуры\n",
          "bar_buttons": [{"name": "КнопкаА", "title": "Кнопка А"}, {"name": "КнопкаБ", "handler": "ДругаяПроцедура"}]}
    with tempfile.TemporaryDirectory() as tmp:
        v = write_erf(bb, Path(tmp), platform_check=use_platform)["verify"]
    ok = bool(v.get("parsed_ok")) and (not use_platform or (v.get("platform") or {}).get("ok"))
    print("кнопки командной панели отчёта:", "OK" if ok else f"FAIL {v}")
    bad += not ok
    try:
        write_erf({**SPEC, "bar_buttons": [{"name": "К"}, {"name": "к"}]}, Path(tempfile.gettempdir()) / "erf_smoke_neg", platform_check=False)
        print("негатив «дубль кнопки панели»: FAIL (ошибки нет)")
        bad += 1
    except BuildError as e:
        print(f"негатив «дубль кнопки панели»: OK ({str(e)[:50]}…)")
    ch = {"name": "СмоукДиаграмм", "query": qd, "resources": ["Приход", "Расход"],
          "parameters": [{"name": "ДатаС", "type": "Дата", "value": "Начало года"}, {"name": "ДатаПо", "type": "Дата", "value": "Начало следующего месяца"}],
          "structure": [{"chart": {"points": ["Период"], "series": ["Организация"], "select": ["Приход"],
                                   "output": {"ТипДиаграммы": "Гистограмма объёмная", "ТипДиаграммы.ВидПодписей": "Значение", "ТипДиаграммы.ЦветФона": "#F0F8FF",
                                              "ТипДиаграммы.Шрифт": {"font": {"bold": True, "size": 9, "name": "Arial"}}, "ТипДиаграммы.МинимальноеЗначение": 0,
                                              "НеСуществует": 1}}}]}
    if use_platform:
        with tempfile.TemporaryDirectory() as tmp:
            r = write_erf(ch, Path(tmp), platform_check=True)
        ok = bool((r["verify"].get("platform") or {}).get("ok")) and r["verify"].get("dropped_parameters") == ["НеСуществует"]
        print("настройка диаграммы (тип, подписи, цвет, шрифт, мин. значение; отброшенные параметры):", "OK" if ok else f"FAIL {r['verify']}")
        bad += not ok
    sk = {"name": "СмоукНаборов2",
          "datasets": [{"name": "Внешние", "object": "ИсходныеДанные",
                        "fields": [{"name": "Код", "type": "Число(10,0)"}, {"name": "Сумма", "type": "Число(15,2)"}, {"name": "Примечание", "type": "Строка(100)"}]},
                       {"name": "Объединённые", "union": [
                           {"name": "Приходы", "query": "ВЫБРАТЬ Т.Контрагент КАК Контрагент, Т.Приход КАК Оборот ИЗ РегистрНакопления.Любой.Обороты(&ДатаС, &ДатаПо, , ) КАК Т"},
                           {"name": "Расходы", "query": "ВЫБРАТЬ Т.Контрагент КАК Контрагент, -Т.Расход КАК Оборот ИЗ РегистрНакопления.Любой.Обороты(&ДатаС, &ДатаПо, , ) КАК Т"}]}],
          "parameters": [{"name": "ДатаС", "type": "Дата", "value": "Начало года"}, {"name": "ДатаПо", "type": "Дата", "value": "Начало следующего месяца"}],
          "resources": ["Оборот", "Сумма"], "group_by": ["Контрагент"]}
    if use_platform:
        from _erf_dump import dump_template
        with tempfile.TemporaryDirectory() as tmp:
            r = write_erf(sk, Path(tmp), platform_check=True)
            t = dump_template(r["path"])
        want = {"DataSetObject": 1, "DataSetUnion": 1, "<objectName>ИсходныеДанные": 1, "DataSetQuery": 2}
        miss = {k: (t.count(k), n) for k, n in want.items() if t.count(k) != n}
        ok = bool((r["verify"].get("platform") or {}).get("ok")) and not miss
        print("наборы: объект и объединение:", "OK" if ok else f"FAIL {miss}")
        bad += not ok
    for name, extra in {"набор-объект без полей": {"datasets": [{"name": "О", "object": "Х"}]},
                        "объединение из одного": {"datasets": [{"name": "О", "union": [{"name": "А", "query": "ВЫБРАТЬ 1 КАК А"}]}]}}.items():
        try:
            write_erf({"name": "Т", **extra}, Path(tempfile.gettempdir()) / "erf_smoke_neg", platform_check=False)
            print(f"негатив «{name}»: FAIL (ошибки нет)")
            bad += 1
        except BuildError as e:
            print(f"негатив «{name}»: OK ({str(e)[:50]}…)")
    fg = {"name": "СмоукОтборов2", "query": "ВЫБРАТЬ Т.Контрагент КАК Контрагент, Т.Сумма КАК Сумма, Т.Приход КАК Приход ИЗ РегистрНакопления.ВзаиморасчетыСКонтрагентами КАК Т ГДЕ Т.Контрагент <> &Исключить",
          "parameters": [{"name": "Исключить", "type": "СправочникСсылка.Контрагенты", "value": "Справочник.Контрагенты.ПустаяСсылка"}],
          "resources": ["Сумма"],
          "filters": [{"group": "ИЛИ", "items": [{"field": "Сумма", "op": ">", "value": 1000},
                                                 {"group": "И", "items": [{"field": "Приход", "op": "заполнено"},
                                                                          {"field": "Контрагент", "op": "<>", "value": "Справочник.Контрагенты.ПустаяСсылка"}]}]},
                      {"field": "Контрагент", "op": "в списке", "values": ["Справочник.Контрагенты.ПустаяСсылка"], "use": False},
                      {"field": "Сумма", "op": "в списке"},
                      {"field": "Приход", "op": ">=", "value": {"field": "Сумма"}, "view": "обычный"}]}
    if use_platform:
        from _erf_dump import dump_template
        with tempfile.TemporaryDirectory() as tmp:
            r = write_erf(fg, Path(tmp), platform_check=True)
            t = dump_template(r["path"])
        want = {"FilterItemGroup": 2, "OrGroup": 1, "AndGroup": 1, "InList": 2, "DesignTimeValue": 4, "<dcsset:viewMode>": 1}
        miss = {k: (t.count(k), n) for k, n in want.items() if t.count(k) != n}
        ok = bool((r["verify"].get("platform") or {}).get("ok")) and not miss
        print("группы отборов И/ИЛИ, список, ссылочные значения:", "OK" if ok else f"FAIL {miss}")
        bad += not ok
    for name, extra in {"ссылочный отбор: нет объекта": {"filters": [{"field": "Сумма", "op": "=", "value": "Справочник.НетТакого.ПустаяСсылка"}]},
                        "группа без элементов": {"filters": [{"group": "И"}]},
                        "группа неизвестная": {"filters": [{"group": "ЛИБО", "items": [{"field": "Сумма", "op": "заполнено"}]}]}}.items():
        try:
            write_erf({**SPEC, **extra}, Path(tempfile.gettempdir()) / "erf_smoke_neg", platform_check=False)
            print(f"негатив «{name}»: FAIL (ошибки нет)")
            bad += 1
        except BuildError as e:
            print(f"негатив «{name}»: OK ({str(e)[:50]}…)")
    for name, extra in {
                        "значение параметра вывода": {"output": {"ВыводитьОтбор": "Нечто"}},
                        "цвет": {"appearance": [{"text_color": "радужный"}]},
                        "оформление пустое": {"appearance": [{"fields": ["Сумма"]}]},
                        "поле пользовательское без выражения": {"user_fields": [{"name": "Х"}]},
                        "дубль варианта": {"variants": [{"name": "А"}, {"name": "А"}]}}.items():
        try:
            write_erf({**SPEC, **extra}, Path(tempfile.gettempdir()) / "erf_smoke_neg", platform_check=False)
            print(f"негатив «{name}»: FAIL (ошибки нет)")
            bad += 1
        except BuildError as e:
            print(f"негатив «{name}»: OK ({str(e)[:50]}…)")
    cf = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--cf=")), "")
    if cf:  # ссылочный параметр: GUID из выгрузки CF
        spec = {"name": "СмоукСсылки", "query": "ВЫБРАТЬ &Н КАК Н", "parameters": [{"name": "Н", "type": "СправочникСсылка.Номенклатура"}]}
        with tempfile.TemporaryDirectory() as tmp:
            res = write_erf(spec, Path(tmp), platform_check=use_platform, cf_root=cf)
        ok = bool(res["verify"].get("parsed_ok"))
        print("ссылочный параметр:", "OK" if ok else f"FAIL {res['verify']}")
        bad += not ok
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
