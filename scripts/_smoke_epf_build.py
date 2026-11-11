# -*- coding: utf-8 -*-
"""
Смоук сборки .epf: python scripts/_smoke_epf_build.py [--platform] [--cf=D:/conf_test]
Собирает обработку (реквизиты всех типов + ТЧ + форма с полями), разбирает своим парсером; с --platform
дополнительно выгружает файл Конфигуратором (платформа 1С должна быть установлена).
Открытие в клиенте проверяется руками: 1cv8.exe ENTERPRISE /F <ИБ> /RunModeOrdinaryApplication /Execute <файл.epf>
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.epf_build import BuildError, control_types, lint_bsl, write_epf  # noqa: E402

SPEC = {
    "name": "СмоукСборки",
    "synonym": "Смоук сборки",
    "module": 'Процедура ВыполнитьОбработку() Экспорт\n\tСообщить("Имя=" + Имя + "; строк=" + Товары.Количество());\nКонецПроцедуры\n',
    "attributes": [
        {"name": "Имя", "synonym": "Имя клиента", "type": "Строка(50)"},
        {"name": "Сумма", "type": "Число(15,2)"},
        {"name": "Период", "type": "Дата"},
        {"name": "Флаг", "type": "Булево"},
        {"name": "Любой", "type": "Произвольный"},
    ],
    "tabular_sections": [{"name": "Товары", "columns": [{"name": "Номенклатура", "type": "Строка(100)"}, {"name": "Цена", "type": "Число(15,2)"}]}],
}


def _check_bindings() -> int:
    """Привязки сторон (bind/anchor/fill): у каждой привязки {2,X,сторона} есть обратная ссылка {0,id,сторона} в списке
    стороны элемента X, а привязка к форме — в реестре формы. Сверено по 4670 формам выгрузки (0 пропусков)."""
    from tools import brace
    from tools import epf_build as eb
    from tools.ordinary_form import ELEMENT_TYPES, _layout

    ctl = [{"type": "Надпись", "name": "Заг", "left": 16, "top": 100, "width": 200, "height": 20},
           {"type": "ПолеВвода", "name": "Под", "left": 16, "top": 128, "width": 300, "height": 21,
            "bind": {"top": "Заг", "right": "form"}},
           {"type": "ПолеТабличногоДокумента", "name": "Табл", "left": 16, "top": 160, "width": 360, "height": 150,
            "anchor": True},
           {"type": "Надпись", "name": "Рядом", "left": 400, "top": 100, "width": 60, "height": 20,
            "bind": {"left": "Заг.право"}}]
    out = 0
    for label, panels, cs in (("панели", True, ctl), ("без панелей", False, ctl[:2] + [{**ctl[2], "anchor": False, "fill": True}])):
        text = eb._form_with_fields("Ф", "00000000-0000-0000-0000-000000000009", [], None, [], "", cs, 560, 420, panels)
        els: list = []

        def walk(n):
            if isinstance(n, list):
                if n and isinstance(n[0], str) and n[0] in ELEMENT_TYPES and len(n) > 3:
                    els.append(n)
                for c in n:
                    walk(c)
        walk(brace.parse(text))
        lists, pairs = {}, {}
        for e in els:
            lay = _layout(e)
            t, i, ls = lay[12:], 0, []
            for _ in range(4):
                n = int(t[i]); i += 1
                ls.append({(x[1], x[2]) for x in t[i:i + n]}); i += n
            lists[e[1]], pairs[e[1]] = ls, lay[6:10]
        miss = [(E, k) for E, ps in pairs.items() for k, p in enumerate(ps) for b in p[1:]
                if b[1] in lists and (E, str(k)) not in lists[b[1]][int(b[2])]]
        form_bound = {(E, str(k), b[2]) for E, ps in pairs.items() for k, p in enumerate(ps) for b in p[1:] if b[1] == "0"}
        reg = text.split("},26,", 1)[1].split("{8,1,", 1)[0] if "},26," in text else ""
        reg_miss = [f for f in form_bound if f[0] not in ("2", "3") and f"{{0,{f[0]},{f[2]}}}" not in reg]
        ok = bool(els) and not miss and not reg_miss
        print(f"привязки ({label}): {'OK' if ok else f'FAIL нет обратных ссылок {miss}, нет в реестре формы {reg_miss}'}")
        out += not ok
    return out


def main() -> int:
    use_platform = "--platform" in sys.argv
    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        res = write_epf(SPEC, Path(tmp), platform_check=use_platform)
        v = res["verify"]
        ok = v.get("parsed_ok") and v.get("attributes") == [a["name"] for a in SPEC["attributes"]] and v.get("tabular_sections") == ["Товары"]
        print("сборка + разбор:", "OK" if ok else f"FAIL {v}", f"({res['size']} байт)")
        bad += not ok
        if use_platform:
            p = v.get("platform") or {}
            print("платформа:", "OK" if p.get("ok") else f"FAIL {p}")
            bad += not p.get("ok")
    for name, spec in {
        "плохое имя": {"name": "1x", "module": ""},
        "неизвестный тип": {"name": "Т", "module": "", "attributes": [{"name": "А", "type": "РегистрСведенийЗапись.Х"}]},
        "дубль реквизита": {"name": "Т", "module": "", "attributes": [{"name": "А"}, {"name": "а"}]},
    }.items():
        try:
            write_epf(spec, Path(tempfile.gettempdir()) / "epf_smoke_neg", platform_check=False)
            print(f"негатив «{name}»: FAIL (ошибки нет)")
            bad += 1
        except BuildError as e:
            print(f"негатив «{name}»: OK ({str(e)[:50]}…)")
    cf = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--cf=")), "")
    if cf:  # ссылочные типы: GUID из выгрузки CF (в runtime — зеркало Qdrant)
        ref_spec = {"name": "СмоукСсылок", "module": "", "attributes": [
            {"name": "Номенклатура", "type": "СправочникСсылка.Номенклатура"},
            {"name": "Смешанный", "type": "Строка(20), СправочникСсылка.Контрагенты"}]}
        with tempfile.TemporaryDirectory() as tmp:
            v = write_epf(ref_spec, Path(tmp), platform_check=False, cf_root=cf)["verify"]
        ok = v.get("types") == {"Номенклатура": "Справочник.Номенклатура", "Смешанный": "Строка(20), Справочник.Контрагенты"}
        print("ссылочные типы:", "OK" if ok else f"FAIL {v.get('types')}")
        bad += not ok
        try:
            write_epf({"name": "Т", "module": "", "attributes": [{"name": "А", "type": "СправочникСсылка.НетТакого"}]},
                      Path(tempfile.gettempdir()) / "epf_smoke_neg", platform_check=False, cf_root=cf)
            print("негатив «нет объекта»: FAIL")
            bad += 1
        except BuildError as e:
            print(f"негатив «нет объекта»: OK ({str(e)[:50]}…)")
    # все элементы палитры: на основной форме (под полями и ТЧ) и на доп. форме, с обработчиками событий
    ctl_spec = {**SPEC, "name": "СмоукЭлементов",
                "controls": [{"type": t, "name": f"Эл{t}", "title": f"Заголовок {t}"} for t in control_types()],
                "form_module": "Процедура ЭлКнопкаНажатие(Элемент)\n\tСообщить(1);\nКонецПроцедуры\n"
                               "Процедура ЭлФлажокПриИзменении(Элемент)\nКонецПроцедуры\n",
                "extra_forms": [{"name": "ФормаДиаграммы", "width": 700,
                                 "controls": [{"type": "ДиаграммаГанта", "name": "Диаграмма", "height": 400}]}]}
    with tempfile.TemporaryDirectory() as tmp:
        res = write_epf(ctl_spec, Path(tmp), platform_check=use_platform)
        v = res["verify"]
        print(f"элементы палитры ({len(control_types())} типов):", "OK" if v.get("parsed_ok") else f"FAIL {v}", f"({res['size']} байт)")
        bad += not v.get("parsed_ok")
        if use_platform:
            p = v.get("platform") or {}
            print("элементы — платформа:", "OK" if p.get("ok") else f"FAIL {p}")
            bad += not p.get("ok")
    try:
        write_epf({"name": "Т", "module": "", "controls": [{"type": "Слайдер"}]},
                  Path(tempfile.gettempdir()) / "epf_smoke_neg", platform_check=False)
        print("негатив «неизвестный элемент»: FAIL")
        bad += 1
    except BuildError as e:
        print(f"негатив «неизвестный элемент»: OK ({str(e)[:50]}…)")
    bad += _check_bindings()
    lint_ok = lint_bsl("Процедура А()\nЕсли Истина Тогда\nКонецПроцедуры") and not lint_bsl(SPEC["module"])
    print("lint BSL:", "OK" if lint_ok else "FAIL")
    bad += not lint_ok
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
