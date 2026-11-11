# -*- coding: utf-8 -*-
"""Smoke: функции языка запросов 1С на живой базе (без hardcode CF — resolve metadata)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backends import create_backend
from config import load_config
from tools.universal import UniversalTools, is_safe_select_query


def _fields(t: UniversalTools, fn: str):
    st = t.get_object_structure(fn) if fn else {}
    dims = [
        d.get("name")
        for d in (st.get("dimensions") or [])
        if isinstance(d, dict) and d.get("name")
    ] or list(st.get("dimension_names") or [])
    res = [
        r.get("name")
        for r in (st.get("resources") or [])
        if isinstance(r, dict) and r.get("name")
    ] or list(st.get("resource_names") or [])
    return dims, res, st


def _first_meta(t: UniversalTools, q: str, otype: str, pred) -> str | None:
    try:
        m = t.find_metadata(q, otype, 8)
    except Exception:
        return None
    for it in m.get("items") or []:
        fn = (it.get("full_name") or "") if isinstance(it, dict) else ""
        if pred(fn):
            return fn
    return None


def main() -> int:
    cfg = load_config()
    b = create_backend(cfg)
    t = UniversalTools(b, read_only=True)

    doc = None
    for q in ("реализация", "продажа", "поступление"):
        doc = _first_meta(
            t, q, "Документы", lambda fn: fn.startswith("Документ.")
        )
        if doc:
            break

    reg_debt = None
    for q in ("расчет клиент", "расчеты", "взаиморасчет"):
        reg_debt = _first_meta(
            t,
            q,
            "РегистрыНакопления",
            lambda fn: fn.startswith("РегистрНакопления.")
            and ("расчет" in fn.lower() or "клиент" in fn.lower()),
        )
        if reg_debt:
            break
    if not reg_debt:
        reg_debt = _first_meta(
            t,
            "расчет",
            "РегистрыНакопления",
            lambda fn: fn.startswith("РегистрНакопления."),
        )

    reg_rev = None
    for q in ("выручка", "себестоимость", "продаж"):
        reg_rev = _first_meta(
            t,
            q,
            "РегистрыНакопления",
            lambda fn: fn.startswith("РегистрНакопления.")
            and ("выруч" in fn.lower() or "себест" in fn.lower() or "продаж" in fn.lower()),
        )
        if reg_rev:
            break

    cat = None
    for q in ("номенклатура", "контрагент"):
        cat = _first_meta(
            t, q, "Справочники", lambda fn: fn.startswith("Справочник.")
        )
        if cat:
            break

    reg2 = _first_meta(
        t,
        "поставщик",
        "РегистрыНакопления",
        lambda fn: fn.startswith("РегистрНакопления.") and fn != reg_debt,
    )

    print("DOC", doc)
    print("REG_DEBT", reg_debt)
    print("REG_REV", reg_rev)
    print("CAT", cat)
    print("REG2", reg2)

    d_dims, d_res, _ = _fields(t, reg_debt) if reg_debt else ([], [], {})
    r_dims, r_res, _ = _fields(t, reg_rev) if reg_rev else ([], [], {})
    print("debt dims/res", d_dims[:6], d_res[:6])
    print("rev dims/res", r_dims[:6], r_res[:6])

    money_d = next((x for x in d_res if "сумм" in x.lower()), d_res[0] if d_res else None)
    dim_d = d_dims[0] if d_dims else None
    money_r = next(
        (x for x in r_res if "выруч" in x.lower() or "сумм" in x.lower()),
        r_res[0] if r_res else None,
    )
    cost_r = next(
        (x for x in r_res if "стоим" in x.lower() or "себест" in x.lower()),
        r_res[1] if len(r_res) > 1 else None,
    )
    mgr = next(
        (x for x in r_dims if "менедж" in x.lower()),
        r_dims[0] if r_dims else None,
    )

    as_of = "ДАТАВРЕМЯ(2015,12,31,23,59,59)"
    start = "ДАТАВРЕМЯ(2015,1,1,0,0,0)"
    end = as_of

    tests: list[tuple[str, str, list[str]]] = []

    if doc:
        tests.append(
            (
                "STR",
                f"ВЫБРАТЬ ПЕРВЫЕ 3 Т.Номер КАК Номер, "
                f"ПОДСТРОКА(Т.Номер, 1, 5) КАК Префикс, "
                f"ВРЕГ(Т.Номер) КАК Верх, НРЕГ(Т.Номер) КАК Низ, "
                f"ЛЕВ(Т.Номер, 4) КАК Лев4, ПРАВ(Т.Номер, 4) КАК Прав4, "
                f"ДЛИНАСТРОКИ(Т.Номер) КАК Длина, СОКРЛП(Т.Номер) КАК Сокр, "
                f"СТРОКА(Т.Дата) КАК ДатаСтр "
                f"ИЗ {doc} КАК Т ГДЕ Т.Проведен = ИСТИНА УПОРЯДОЧИТЬ ПО Т.Дата УБЫВ",
                [
                    "ПОДСТРОКА",
                    "ВРЕГ",
                    "НРЕГ",
                    "ЛЕВ",
                    "ПРАВ",
                    "ДЛИНАСТРОКИ",
                    "СОКРЛП",
                    "СТРОКА",
                ],
            )
        )
        tests.append(
            (
                "DATE_PART",
                f"ВЫБРАТЬ ПЕРВЫЕ 3 Т.Номер КАК Номер, Т.Дата КАК Дата, "
                f"ГОД(Т.Дата) КАК Год, КВАРТАЛ(Т.Дата) КАК Квартал, "
                f"МЕСЯЦ(Т.Дата) КАК Месяц, ДЕНЬ(Т.Дата) КАК День, "
                f"ДЕНЬНЕДЕЛИ(Т.Дата) КАК ДеньНед, "
                f"НАЧАЛОПЕРИОДА(Т.Дата, МЕСЯЦ) КАК НачМес, "
                f"КОНЕЦПЕРИОДА(Т.Дата, МЕСЯЦ) КАК КонМес, "
                f"РАЗНОСТЬДАТ(Т.Дата, {as_of}, ДЕНЬ) КАК ДнейДоКонца2015 "
                f"ИЗ {doc} КАК Т ГДЕ Т.Проведен = ИСТИНА УПОРЯДОЧИТЬ ПО Т.Дата УБЫВ",
                [
                    "ГОД",
                    "КВАРТАЛ",
                    "МЕСЯЦ",
                    "ДЕНЬ",
                    "ДЕНЬНЕДЕЛИ",
                    "НАЧАЛОПЕРИОДА",
                    "КОНЕЦПЕРИОДА",
                    "РАЗНОСТЬДАТ",
                ],
            )
        )
        # ДОБАВИТЬКДАТЕ: нужна перезаливка onec_http/Module.bsl (старый blacklist режет «ДОБАВИТЬ»)
        tests.append(
            (
                "DATE_ADD",
                f"ВЫБРАТЬ ПЕРВЫЕ 2 Т.Номер КАК Номер, "
                f"ДОБАВИТЬКДАТЕ(Т.Дата, ДЕНЬ, 7) КАК Плюс7 "
                f"ИЗ {doc} КАК Т ГДЕ Т.Проведен = ИСТИНА",
                ["ДОБАВИТЬКДАТЕ"],
            )
        )
        tests.append(
            (
                "CAST_UID",
                f"ВЫБРАТЬ ПЕРВЫЕ 3 ВЫРАЗИТЬ(Т.Номер КАК СТРОКА(30)) КАК Ном, "
                f"УНИКАЛЬНЫЙИДЕНТИФИКАТОР(Т.Ссылка) КАК Уид "
                f"ИЗ {doc} КАК Т ГДЕ Т.Проведен = ИСТИНА",
                ["ВЫРАЗИТЬ", "УНИКАЛЬНЫЙИДЕНТИФИКАТОР"],
            )
        )
        # Контрагент/СуммаДокумента — частые, но проверяем structure
        st_doc = t.get_object_structure(doc)
        attr_names = set()
        for a in st_doc.get("attributes") or st_doc.get("fields") or []:
            if isinstance(a, dict) and a.get("name"):
                attr_names.add(str(a["name"]))
            elif isinstance(a, str):
                attr_names.add(a)
        for std in ("Номер", "Дата", "Проведен", "ПометкаУдаления", "Ссылка"):
            attr_names.add(std)
        has_kontr = "Контрагент" in attr_names or any(
            "контрагент" in x.lower() for x in attr_names
        )
        has_sum = "СуммаДокумента" in attr_names or any(
            "сумма" in x.lower() for x in attr_names
        )
        kontr = "Контрагент" if "Контрагент" in attr_names else next(
            (x for x in attr_names if "контрагент" in x.lower()), None
        )
        summ = "СуммаДокумента" if "СуммаДокумента" in attr_names else next(
            (x for x in attr_names if x.lower().startswith("сумма")), None
        )
        if has_kontr and has_sum and kontr and summ:
            tests.append(
                (
                    "LOGIC",
                    f"ВЫБРАТЬ ПЕРВЫЕ 5 Т.Номер КАК Номер, Т.Дата КАК Дата, "
                    f"ПРЕДСТАВЛЕНИЕ(Т.{kontr}) КАК Контрагент, "
                    f"ЕСТЬNULL(Т.{summ}, 0) КАК Сумма "
                    f"ИЗ {doc} КАК Т "
                    f"ГДЕ Т.Дата МЕЖДУ {start} И {end} "
                    f"И Т.Проведен = ИСТИНА "
                    f"И НЕ Т.ПометкаУдаления "
                    f"И (ЕСТЬNULL(Т.{summ}, 0) > 100 ИЛИ ЕСТЬNULL(Т.{summ}, 0) = 0) "
                    f"УПОРЯДОЧИТЬ ПО Т.Дата УБЫВ",
                    [
                        "ПРЕДСТАВЛЕНИЕ",
                        "ЕСТЬNULL",
                        "МЕЖДУ",
                        "ДАТАВРЕМЯ",
                        "И",
                        "ИЛИ",
                        "НЕ",
                    ],
                )
            )

    if reg_debt and dim_d and money_d:
        tests.append(
            (
                "NULL_PRES_VYBOR",
                f"ВЫБРАТЬ РАЗРЕШЕННЫЕ ПЕРВЫЕ 5 "
                f"ПРЕДСТАВЛЕНИЕ(Т.{dim_d}) КАК Измерение, "
                f"ЕСТЬNULL(Т.{money_d}Остаток, 0) КАК Остаток, "
                f"ВЫБОР КОГДА ЕСТЬNULL(Т.{money_d}Остаток, 0) > 0 "
                f'ТОГДА "Долг" ИНАЧЕ "Нет" КОНЕЦ КАК Признак '
                f"ИЗ {reg_debt}.Остатки({as_of}, ) КАК Т "
                f"ГДЕ Т.{money_d}Остаток <> 0 "
                f"УПОРЯДОЧИТЬ ПО Остаток УБЫВ",
                ["ПРЕДСТАВЛЕНИЕ", "ЕСТЬNULL", "ВЫБОР", "Остатки", "УПОРЯДОЧИТЬ"],
            )
        )
        tests.append(
            (
                "PERIOD",
                f"ВЫБРАТЬ ПЕРВЫЕ 6 "
                f"НАЧАЛОПЕРИОДА(Т.Период, Месяц) КАК Период, "
                f"ГОД(НАЧАЛОПЕРИОДА(Т.Период, Месяц)) КАК Год, "
                f"МЕСЯЦ(НАЧАЛОПЕРИОДА(Т.Период, Месяц)) КАК Мес, "
                f"СУММА(ЕСТЬNULL(Т.{money_d}Оборот, 0)) КАК СуммаОборот "
                f"ИЗ {reg_debt}.Обороты({start}, {end}, Месяц, ) КАК Т "
                f"СГРУППИРОВАТЬ ПО НАЧАЛОПЕРИОДА(Т.Период, Месяц), "
                f"ГОД(НАЧАЛОПЕРИОДА(Т.Период, Месяц)), "
                f"МЕСЯЦ(НАЧАЛОПЕРИОДА(Т.Период, Месяц)) "
                f"УПОРЯДОЧИТЬ ПО Период",
                [
                    "НАЧАЛОПЕРИОДА",
                    "ГОД",
                    "МЕСЯЦ",
                    "СУММА",
                    "ЕСТЬNULL",
                    "Обороты",
                    "СГРУППИРОВАТЬ",
                ],
            )
        )

    if reg_rev and mgr and money_r:
        cost_sel = (
            f", СУММА(ЕСТЬNULL(Т.{cost_r}Оборот, 0)) КАК Себест"
            if cost_r
            else ""
        )
        cost_tot = ", СУММА(Себест)" if cost_r else ""
        tests.append(
            (
                "AGG",
                f"ВЫБРАТЬ ПРЕДСТАВЛЕНИЕ(Т.{mgr}) КАК Менеджер, "
                f"СУММА(ЕСТЬNULL(Т.{money_r}Оборот, 0)) КАК Выручка, "
                f"МИНИМУМ(ЕСТЬNULL(Т.{money_r}Оборот, 0)) КАК МинОборот, "
                f"МАКСИМУМ(ЕСТЬNULL(Т.{money_r}Оборот, 0)) КАК МаксОборот, "
                f"СРЕДНЕЕ(ЕСТЬNULL(Т.{money_r}Оборот, 0)) КАК СредОборот, "
                f"КОЛИЧЕСТВО(*) КАК Строк{cost_sel} "
                f"ИЗ {reg_rev}.Обороты({start}, {end}, , ) КАК Т "
                f"СГРУППИРОВАТЬ ПО Т.{mgr} "
                f"УПОРЯДОЧИТЬ ПО Выручка УБЫВ "
                f"ИТОГИ СУММА(Выручка){cost_tot}, СУММА(Строк) ПО ОБЩИЕ",
                [
                    "СУММА",
                    "МИНИМУМ",
                    "МАКСИМУМ",
                    "СРЕДНЕЕ",
                    "КОЛИЧЕСТВО",
                    "ИТОГИ",
                    "ПРЕДСТАВЛЕНИЕ",
                ],
            )
        )
        tests.append(
            (
                "NUM",
                f"ВЫБРАТЬ ПЕРВЫЕ 5 ПРЕДСТАВЛЕНИЕ(Т.{mgr}) КАК Менеджер, "
                f"СУММА(ЕСТЬNULL(Т.{money_r}Оборот, 0)) КАК Выручка, "
                f"ОКР(СУММА(ЕСТЬNULL(Т.{money_r}Оборот, 0)) / 1000, 2) КАК Тыс, "
                f"ЦЕЛ(СУММА(ЕСТЬNULL(Т.{money_r}Оборот, 0)) / 1000) КАК ТысЦел, "
                f"СУММА(ЕСТЬNULL(Т.{money_r}Оборот, 0)) * 12 / 10 КАК СНаценкой "
                f"ИЗ {reg_rev}.Обороты({start}, {end}, , ) КАК Т "
                f"СГРУППИРОВАТЬ ПО Т.{mgr} УПОРЯДОЧИТЬ ПО Выручка УБЫВ",
                ["ОКР", "ЦЕЛ", "СУММА", "ЕСТЬNULL", "*"],
            )
        )
        dim2 = next((x for x in r_dims if x != mgr), None)
        if dim2:
            tests.append(
                (
                    "COUNT_DIST",
                    f"ВЫБРАТЬ ПЕРВЫЕ 5 ПРЕДСТАВЛЕНИЕ(Т.{mgr}) КАК Менеджер, "
                    f"КОЛИЧЕСТВО(РАЗЛИЧНЫЕ Т.{dim2}) КАК УникИзм, "
                    f"СУММА(ЕСТЬNULL(Т.{money_r}Оборот, 0)) КАК Выручка "
                    f"ИЗ {reg_rev}.Обороты({start}, {end}, , ) КАК Т "
                    f"СГРУППИРОВАТЬ ПО Т.{mgr} УПОРЯДОЧИТЬ ПО Выручка УБЫВ",
                    ["КОЛИЧЕСТВО(РАЗЛИЧНЫЕ)", "СУММА"],
                )
            )

    if cat:
        tests.append(
            (
                "CAT_LIKE",
                f"ВЫБРАТЬ ПЕРВЫЕ 5 Т.Наименование КАК Наим, "
                f"ВРЕГ(Т.Наименование) КАК Верх, "
                f"ДЛИНАСТРОКИ(Т.Наименование) КАК Длина "
                f"ИЗ {cat} КАК Т "
                f"ГДЕ Т.ПометкаУдаления = ЛОЖЬ "
                f'И Т.Наименование ПОДОБНО "%а%" '
                f"УПОРЯДОЧИТЬ ПО Т.Наименование",
                ["ВРЕГ", "ДЛИНАСТРОКИ", "ПОДОБНО"],
            )
        )

    if reg_debt and reg2 and dim_d and money_d:
        d2, r2, _ = _fields(t, reg2)
        common = [x for x in d_dims if x in d2]
        money2 = next(
            (x for x in r2 if "сумм" in x.lower()), r2[0] if r2 else None
        )
        if common and money2:
            k = common[0]
            tests.append(
                (
                    "JOIN2",
                    f"ВЫБРАТЬ РАЗРЕШЕННЫЕ ПЕРВЫЕ 5 "
                    f"ПРЕДСТАВЛЕНИЕ(А.{k}) КАК Ключ, "
                    f"ЕСТЬNULL(А.{money_d}Остаток, 0) КАК А_Ост, "
                    f"ЕСТЬNULL(Б.{money2}Остаток, 0) КАК Б_Ост, "
                    f"ЕСТЬNULL(А.{money_d}Остаток, 0) + ЕСТЬNULL(Б.{money2}Остаток, 0) КАК СуммаОбоих, "
                    f"ВЫБОР КОГДА ЕСТЬNULL(А.{money_d}Остаток, 0) > ЕСТЬNULL(Б.{money2}Остаток, 0) "
                    f'ТОГДА "A>B" ИНАЧЕ "A<=B" КОНЕЦ КАК Сравн '
                    f"ИЗ {reg_debt}.Остатки({as_of}, ) КАК А "
                    f"ЛЕВОЕ СОЕДИНЕНИЕ {reg2}.Остатки({as_of}, ) КАК Б "
                    f"ПО А.{k} = Б.{k} "
                    f"ГДЕ А.{money_d}Остаток <> 0 "
                    f"УПОРЯДОЧИТЬ ПО А_Ост УБЫВ",
                    [
                        "ПРЕДСТАВЛЕНИЕ",
                        "ЕСТЬNULL",
                        "ВЫБОР",
                        "ЛЕВОЕ СОЕДИНЕНИЕ",
                        "+",
                        "Остатки",
                    ],
                )
            )

    # builders path
    print("\n=== BUILDERS ===")
    for label, kwargs in (
        ("linked_debt", {"request": "задолженность клиентов на конец 2015", "execute": True, "limit": 3}),
        ("linked_turn", {"request": "обороты расчетов с клиентами помесячно за 2015", "execute": True, "limit": 3}),
        ("margin", None),
    ):
        if label == "margin":
            r = t.margin_report(request="маржа по менеджерам за 2015", period="2015", limit=3)
            print(
                label,
                "fn",
                r.get("functions_used"),
                "rows",
                len(r.get("rows") or []),
                "err",
                str(r.get("error") or "")[:80],
            )
            print("  q", (r.get("query") or "")[:200])
            continue
        r = t.linked_query(**kwargs)
        er = r.get("execute_result") if isinstance(r.get("execute_result"), dict) else {}
        print(
            label,
            "role",
            r.get("plan_role"),
            "fn",
            r.get("functions_used"),
            "rows",
            len(r.get("rows") or []),
            "err",
            str(r.get("error") or er.get("error") or "")[:80],
        )
        print("  q", (r.get("query") or "")[:220])

    print(f"\n=== RAW TESTS ({len(tests)}) ===")
    results: list[tuple[str, str, str, int, list[str]]] = []
    for tag, q, fns in tests:
        ok_safe, why = is_safe_select_query(q)
        if not ok_safe:
            print(f"{tag}: SAFE_BLOCK {why}")
            results.append((tag, "SAFE_BLOCK", why, 0, fns))
            continue
        try:
            raw = b.execute_query(q, 20)
        except Exception as e:
            print(f"{tag}: EXC {e}")
            results.append((tag, "EXC", str(e)[:120], 0, fns))
            continue
        err = raw.get("error") if isinstance(raw, dict) else str(raw)
        rows = raw.get("data") or raw.get("rows") if isinstance(raw, dict) else None
        n = len(rows) if isinstance(rows, list) else 0
        status = "OK" if not err else "ERR"
        print(f"{tag}: {status} rows={n} err={str(err)[:180] if err else ''}")
        if rows and n and isinstance(rows[0], dict):
            print("  keys:", list(rows[0].keys())[:14])
            sample = {k: rows[0][k] for k in list(rows[0].keys())[:5]}
            print("  sample:", sample)
        results.append((tag, status, str(err)[:120] if err else "", n, fns))

    print("\n==== SUMMARY ====")
    ok = sum(1 for x in results if x[1] == "OK")
    print(f"{ok}/{len(results)} OK")
    for tag, st, err, n, fns in results:
        print(f"  {tag:18} {st:10} rows={n:<4} fns={','.join(fns[:6])}")
        if st != "OK" and err:
            print(f"    -> {err}")
    return 0 if ok == len(results) and results else 1


if __name__ == "__main__":
    raise SystemExit(main())
