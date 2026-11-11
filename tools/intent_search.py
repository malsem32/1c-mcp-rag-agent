"""
Универсальный поиск объектов метаданных по смыслу задачи менеджера.

Словари — ПОЛНЫЕ слова (нормальная форма / словарь), не «реализ»/«поступлен».
Сопоставление с именами 1С (CamelCase, склонения) — через word_stems() автоматически.
Имена Документ.X конкретной конфигурации нигде не зашиты.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Callable, Iterable


def _norm(s: str) -> str:
    return (s or "").lower().replace("ё", "е")


def word_stems(word: str) -> list[str]:
    """
    Автоматические основы слова (склонения/спряжения).
    «реализации» → реализация, реализаци, реализ…
    «покупателю» → покупател…
    Не хардкод «реализ» в словарях — только здесь при сравнении.
    """
    w = _norm(word)
    if len(w) < 3:
        return [w] if w else []
    stems: set[str] = {w}
    for suf in (
        "иями",
        "ями",
        "ами",
        "ией",
        "ью",
        "иями",
        "ия",
        "ие",
        "ий",
        "ой",
        "ая",
        "ое",
        "ые",
        "ых",
        "ов",
        "ев",
        "ем",
        "ом",
        "ам",
        "ах",
        "ях",
        "ую",
        "ию",
        "ью",
        "ть",
        "ти",
        "ла",
        "ли",
        "ло",
        "но",
        "на",
        "ны",
        "ю",
        "я",
        "ь",
        "ы",
        "и",
        "е",
        "а",
        "о",
        "у",
    ):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            stems.add(w[: -len(suf)])
    # укорочения для CamelCase (минимум 4 буквы основы)
    if len(w) >= 6:
        stems.add(w[:-1])
    if len(w) >= 7:
        stems.add(w[:-2])
    if len(w) >= 8:
        stems.add(w[:-3])
    return [s for s in stems if len(s) >= 3]


@lru_cache(maxsize=4096)
def _match_forms(word: str) -> tuple[str, ...]:
    """Полное слово + все авто-основы для поиска в тексте/имени."""
    w = _norm(word).strip()
    if not w:
        return ()
    forms = {w, w.replace("-", ""), w.replace(" ", "")}
    for part in re.findall(r"[a-zа-яё0-9]+", w):
        forms.add(part)
        forms.update(word_stems(part))
    # длинные основы (≥4) достаточны для CamelCase
    return tuple(sorted(forms, key=len, reverse=True))


def has_word(text: str, word: str, *, min_stem: int = 4) -> bool:
    """
    Есть ли словарное слово (полная форма) в тексте/имени метаданных.
    Сравнивает через авто-стемы, не через обрезанные константы.
    """
    if not word or not text:
        return False
    blob = _norm(text)
    blob_compact = re.sub(r"[\s_\-.\"]+", "", blob)
    for form in _match_forms(word):
        if len(form) < min_stem and form != _norm(word):
            # короткие основы только если совпало полное слово
            if form == _norm(word) and (form in blob or form in blob_compact):
                return True
            continue
        if form in blob or form in blob_compact:
            return True
    return False


def has_any(text: str, words: Iterable[str], *, min_stem: int = 4) -> bool:
    return any(has_word(text, w, min_stem=min_stem) for w in words)


# --- Словари из knowledge/business_vocab.yaml (не хардкод в коде) ---


def _vocab() -> dict[str, Any]:
    from kb.store import get_business_vocab

    return get_business_vocab()


def _intent_keywords() -> dict[str, list[str]]:
    return _vocab().get("intents") or {}


def _action_words() -> dict[str, list[str]]:
    return _vocab().get("actions") or {}


def _phrase_boosts() -> list[tuple[tuple[str, ...], tuple[str, ...], int]]:
    return list(_vocab().get("phrase_boosts") or [])


def _specialized_docs() -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    return list(_vocab().get("specialized_docs") or [])


def _doc_action_words() -> list[str]:
    return list(_vocab().get("doc_action_words") or [])


def _noise_re() -> re.Pattern[str]:
    parts = _vocab().get("noise_substrings") or []
    if not parts:
        return re.compile(r"(присоединенн|удалить|шаблон|настройк|служебн)", re.I)
    return re.compile("|".join(re.escape(p) for p in parts), re.I)


# Ленивые алиасы для старого кода (модульный уровень — свойства через __getattr__)
def __getattr__(name: str) -> Any:
    if name == "INTENT_KEYWORDS":
        return _intent_keywords()
    if name in ("ACTION_WORDS", "ACTION_STEMS"):
        return _action_words()
    if name == "PHRASE_NAME_BOOSTS":
        return _phrase_boosts()
    if name == "DOC_ACTION_WORDS":
        return tuple(_doc_action_words())
    raise AttributeError(name)


def expand_search_queries(phrase: str, *, max_extra: int = 8) -> list[str]:
    """
    Универсальное расширение поисковой фразы → несколько запросов find_metadata.
    Любые токены + compound (для CamelCase имён метаданных).
    Не привязано к конкретному Документ.X.
    """
    p = (phrase or "").strip()
    if not p:
        return []
    seen: set[str] = set()
    out: list[str] = []

    def add(q: str) -> None:
        q = (q or "").strip()
        if not q:
            return
        key = q.lower().replace("ё", "е")
        if key in seen:
            return
        seen.add(key)
        out.append(q)

    add(p)
    pl = p.lower().replace("ё", "е")

    # токены ≥3 букв
    tokens = re.findall(r"[a-zA-Zа-яА-ЯёЁ]{3,}", pl)
    stop = {
        "сколько",
        "какая",
        "какой",
        "какие",
        "было",
        "были",
        "нужно",
        "можешь",
        "можно",
        "покажи",
        "показать",
        "посмотри",
        "посмотреть",
        "выведи",
        "сделай",
        "сравни",
        "только",
        "месяц",
        "года",
        "году",
        "день",
        "данные",
        "отчёт",
        "отчет",
        "excel",
        "файл",
        "есть",
        "наш",
        "наши",
    }
    content_tokens = [t for t in tokens if t not in stop and not t.isdigit()]
    for t in content_tokens[:6]:
        add(t)

    # compound: «требование накладная» → «требованиенакладная» (как в имени метаданных)
    if len(content_tokens) >= 2:
        joined = "".join(content_tokens[:4])
        add(joined)
        camel = "".join(w[:1].upper() + w[1:] for w in content_tokens[:4])
        add(camel)
        for i in range(len(content_tokens) - 1):
            add(f"{content_tokens[i]} {content_tokens[i + 1]}")
            add(content_tokens[i] + content_tokens[i + 1])
            # стем: перемещения+товаров → перемещениетовар
            a, b = content_tokens[i], content_tokens[i + 1]
            if len(a) >= 5:
                add(a[:-1] + b)  # перемещения → перемещение
                add(a[:-2] + b if len(a) >= 6 else a + b)

    # действия документа из knowledge (полные слова)
    has_doc_action = has_any(pl, _doc_action_words())

    # intent keywords: только без явного действия документа
    if not has_doc_action and len(content_tokens) <= 1:
        for _intent, keys in _intent_keywords().items():
            if has_any(pl, keys):
                for k in keys[:3]:
                    add(k)
                break

    # actions из knowledge — только «основные»
    primary_actions = ("sell", "buy", "pay", "order", "invoice", "return", "waybill", "stock")
    actions = _action_words()
    for act in primary_actions:
        words = actions.get(act) or []
        if has_any(pl, words):
            for w in words[:3]:
                add(w)
            break

    # морфология заказа из слов фразы (клиент/поставщик), без алиасов УТ
    if has_word(pl, "заказ") and not has_any(pl, ["реализация", "продажа"]):
        add("заказ")
        if has_any(pl, ["покупател", "клиент", "продаж"]):
            add("заказ покупателя")
        if has_any(pl, ["поставщик", "закуп", "приобрет"]):
            add("заказ поставщику")

    return out[: max_extra + 1]


def rank_items_by_phrase(items: list[dict[str, Any]], phrase: str) -> list[dict[str, Any]]:
    """
    Универсальное ранжирование candidates по фразе (стемы, не hardcode Документ.X).
    """
    p = (phrase or "").lower().replace("ё", "е")
    tokens = re.findall(r"[a-zа-яё]{3,}", p)
    stop = {
        "сколько",
        "какая",
        "какой",
        "какие",
        "было",
        "были",
        "только",
        "нужно",
        "можешь",
        "есть",
        "наш",
        "наши",
        "покажи",
        "посмотри",
        "конец",
        "начала",
        "текущ",
        "данные",
    }
    tokens = [t for t in tokens if t not in stop]
    # контекст «данные» — долг/остатки/обороты → регистры накопления
    want_debt = has_any(
        p,
        [
            "задолжен",
            "неоплач",
            "долг",
            "дебитор",
            "кредитор",
            "взаиморасч",
            "остатк",
        ],
    )
    want_turn = has_any(p, ["оборот", "помесяч", "динамик"])
    want_balances = want_debt or has_any(
        p, ["остатк", "осталось", "остались", "наличие", "на складе", "на склад"]
    )
    prefer_accum = want_debt or want_turn or want_balances
    # compound без пробелов — как ИмяМетаданных
    compound = "".join(tokens) if tokens else ""

    want_purchase = has_any(
        p, ["поступление", "закупка", "приобретение", "приход", "купля", "поставщик"]
    )
    want_sales = has_any(p, ["реализация", "продажа", "отгрузка", "выручка"])
    # узкие домены — сильный boost имени (универсальные стемы, не full_name CF)
    want_order = has_any(p, ["заказ"]) and not has_any(p, ["заказчик"])  # заказ клиента/поставщика
    want_invoice = has_any(p, ["счет на", "счёт на", "счет-фактур", "счёт-фактур"]) or (
        has_any(p, ["счет", "счёт"]) and has_any(p, ["оплат", "покупател", "клиент", "поставщик"])
    )
    want_spec = has_any(p, ["спецификац"])
    want_transport = has_any(p, ["перевозк", "доставк", "рейс", "транспортн"])
    want_nomenclature = has_any(p, ["номенклатур", "артикул", "супертарикул", "тарикул"])
    want_money = has_any(p, ["оплата", "платеж", "деньги", "касса", "банк", "поручение"])
    money_inflow = (
        "на расчет" in p
        or "на р/с" in p
        or (
            want_money
            and has_any(p, ["поступление", "приход"])
            and has_any(p, ["расчет", "банк", "касса", "деньги"])
        )
    )
    goods_receipt = want_purchase and not money_inflow and not (
        want_money and not has_any(p, ["закупка", "поступление", "приобретение", "приход"])
    )
    pay_ops = want_money and not (
        "счет на" in p or "счёт на" in p or has_word(p, "invoice")
    )
    want_invoice = has_any(p, ["счет", "счёт", "invoice"]) and not pay_ops

    ranked: list[dict[str, Any]] = []
    for raw in items:
        it = dict(raw)
        fn = str(it.get("full_name") or "")
        n = fn.lower().replace("ё", "е")
        syn = str(it.get("synonym") or "").lower().replace("ё", "е")
        blob_n = f"{n} {syn}"
        name_only = n.split(".")[-1] if "." in n else n
        try:
            sc = int(float(it.get("score") or 0))
        except (TypeError, ValueError):
            sc = 0

        for pkeys, nkeys, boost in _phrase_boosts():
            if has_any(p, pkeys) and has_any(blob_n, nkeys):
                sc += boost

        def _tok_in(blob: str, tok: str) -> bool:
            if len(tok) >= 4 and tok in blob:
                return True
            # стемы: перемещения ≈ перемещение, товаров ≈ товар
            for cut in (1, 2, 3):
                if len(tok) > 4 + cut and tok[:-cut] in blob:
                    return True
            return len(tok) >= 3 and tok in blob

        for tok in tokens:
            if _tok_in(blob_n, tok):
                sc += 14 if len(tok) >= 4 else 8

        # --- универсально: совпадение НЕСКОЛЬКИХ слов фразы в имени ---
        name_compact = name_only.replace(" ", "").replace("-", "").replace("_", "")
        token_hits_name = sum(1 for tok in tokens if len(tok) >= 4 and _tok_in(name_compact, tok))
        token_hits_blob = sum(1 for tok in tokens if len(tok) >= 4 and _tok_in(blob_n, tok))
        if len(tokens) >= 2:
            if token_hits_name >= len(tokens):
                sc += 180
            elif token_hits_name >= 2:
                sc += 90 * token_hits_name
            elif token_hits_name == 1 and has_any(
                name_compact,
                [
                    "перемещение",
                    "реализация",
                    "поступление",
                    "требование",
                    "накладная",
                    "оплата",
                    "платеж",
                ],
            ):
                sc += 100
            elif token_hits_blob >= 2 and token_hits_name == 0:
                sc += 15
            if compound and len(compound) >= 6:
                # сравнить стемы compound с именем
                c = compound
                for cut in range(0, 4):
                    if len(c) > 6 and c[: len(c) - cut] in name_compact:
                        sc += 130
                        break
                    # перемещениятоваров vs перемещениетоваров
                    if cut and c[:-cut] in name_compact:
                        sc += 130
                        break

        # противоречия направление потока
        if want_sales and has_any(
            blob_n, ["поступление", "приобретение", "закупка", "приход"]
        ):
            sc -= 55
        if want_purchase and has_any(blob_n, ["реализация", "продажа"]):
            sc -= 55
        if has_word(blob_n, "передача") and not has_any(p, ["передача", "перемещение"]):
            sc -= 40
        if has_word(blob_n, "корректировка") and not has_word(p, "корректировка"):
            sc -= 70
        if has_word(p, "покупатель") and has_word(blob_n, "поставщик") and not has_word(
            blob_n, "покупатель"
        ):
            sc -= 30
        if has_word(p, "поставщик") and has_word(blob_n, "покупатель") and not has_word(
            blob_n, "поставщик"
        ):
            sc -= 30

        if has_any(p, ["остаток", "склад", "запас"]):
            if has_any(blob_n, ["егаис", "ветис", "гисм", "алкоголь", "маркированный"]) and not has_any(
                p, ["егаис", "ветис", "гисм", "алкоголь", "маркированный"]
            ):
                sc -= 120
            if "вводначальных" in blob_n.replace(" ", "") and "ввод" not in p:
                sc -= 40

        for name_marks, phrase_marks in _specialized_docs():
            if has_any(blob_n, name_marks) and not has_any(p, phrase_marks):
                sc -= 90

        is_goods_services = (
            ("товар" in name_only and "услуг" in name_only)
            or "товаровуслуг" in name_only.replace(" ", "")
            or "товарыиуслуг" in name_only.replace(" ", "")
        )
        if is_goods_services and (want_purchase or want_sales):
            sc += 95
        elif is_goods_services and not want_money and len(tokens) <= 1:
            sc += 40

        # долг/остатки/обороты → регистры накопления с «расчет/взаиморасчет/…»
        is_accum = n.startswith("регистрнакопления.") or "накопления" in str(
            it.get("type") or ""
        ).lower()
        is_info_reg = n.startswith("регистрсведений.")
        is_doc = n.startswith("документ.")
        is_cat = n.startswith("справочник.")
        is_report = n.startswith("отчет.") or n.startswith("отчёт.")
        is_enum = n.startswith("перечисление.")
        is_dataproc = n.startswith("обработка.")
        # Прайс-лист / обработки UI — почти никогда не data-отчёт продаж/остатков
        if is_dataproc or is_report:
            if prefer_accum or want_balances or want_sales or want_purchase or want_debt:
                sc -= 600
            else:
                sc -= 200
            if has_any(blob_n, ["прайс", "price", "печатн", "загрузк"]):
                if not has_any(p, ["прайс", "price", "печатн", "загрузк"]):
                    sc -= 400

        # --- доменные стемы (заказ / счёт / спецификация / перевозка / номенклатура) ---
        if want_order:
            if is_doc and "заказ" in name_only:
                sc += 350
                if has_any(p, ["клиент", "покупател"]) and has_any(
                    blob_n, ["клиент", "покупател"]
                ):
                    sc += 120
                if has_any(p, ["поставщик"]) and "поставщик" in blob_n:
                    sc += 120
            # «заказ клиента» ≠ счёт на оплату
            if is_doc and has_any(name_only, ["счет", "счёт"]) and "заказ" not in name_only:
                sc -= 500
            if is_doc and "реализац" in name_only:
                sc -= 100
        if want_invoice and is_doc and has_any(name_only, ["счет", "счёт", "оплат"]):
            sc += 180
            if "фактур" in name_only and "фактур" not in p:
                sc -= 80
        if want_purchase:
            if is_doc and has_any(
                name_only, ["поступление", "приобретение", "закупк"]
            ):
                sc += 500
            if is_doc and "реализац" in name_only:
                sc -= 500
            if is_doc and "заказ" in name_only and "поставщик" not in blob_n:
                sc -= 50
        if want_sales and is_doc and "реализац" in name_only:
            sc += 120
            # не форсировать УТ-имя *ТоваровУслуг*, если в фразе нет товаров/услуг
            if any(x in name_only for x in ("товар", "услуг", "продук")) and not any(
                x in p for x in ("товар", "услуг", "продук", "номенклатур")
            ):
                sc -= 100
            # при равном стеме — короче имя
            sc += max(0, 28 - len(name_only)) * 0.4
        if want_spec:
            if has_any(blob_n, ["спецификац"]):
                sc += 400
            if is_doc and "реализац" in name_only and "спецификац" not in name_only:
                sc -= 350
            if is_cat and "номенклатур" in name_only and "спецификац" not in blob_n:
                sc -= 50
        if want_transport:
            if has_any(
                blob_n, ["перевозк", "доставк", "транспорт", "рейс", "задани"]
            ):
                sc += 350
            if is_enum and "статус" in name_only:
                sc -= 120
            if is_doc or has_any(blob_n, ["задан", "заявк"]):
                sc += 80
            if is_doc and "реализац" in name_only:
                sc -= 200
        if want_nomenclature and is_cat and has_any(
            name_only, ["номенклатур", "артикул", "тарикул"]
        ):
            sc += 200
        if want_nomenclature and "супертарикул" in p:
            if "тарикул" in blob_n or "артикул" in blob_n:
                sc += 250
            if is_doc and "реализац" in name_only:
                sc -= 300

        if prefer_accum:
            if is_accum:
                sc += 120
                if has_any(
                    blob_n,
                    [
                        "расчет",
                        "взаиморасчет",
                        "задолжен",
                        "оплат",
                        "долг",
                        "выручк",
                        "себестоим",
                        "товар",
                        "склад",
                        "продаж",
                    ],
                ):
                    sc += 140
                # «расчеты с клиентами» > планы/заказы при долге
                if want_debt and has_any(blob_n, ["расчет", "взаиморасчет"]):
                    sc += 80
                if want_debt and has_any(blob_n, ["заказ"]) and not has_any(
                    blob_n, ["расчет", "взаиморасчет", "оплат"]
                ):
                    sc -= 90
                if want_debt and has_any(blob_n, ["план"]) and not has_any(
                    blob_n, ["расчет"]
                ):
                    sc -= 40
            elif is_info_reg and want_balances:
                sc += 20
            elif is_doc and want_debt:
                sc -= 120  # долг → не документ реализации
            elif is_report:
                sc -= 220  # типовой Отчет.* не для linked_query data
            if is_dataproc or n.startswith("справочник."):
                if want_debt or want_balances:
                    sc -= 200
            # остатки: приоритет «товар+склад/запас», defer «к отбору/отгрузке/в пути»
            # (подстроки full_name из ТЕКУЩЕЙ CF, без привязки к одному conf-name)
            if want_balances or has_any(p, ["склад", "запас", "налич"]):
                if is_accum:
                    name_c = name_only.replace(" ", "").replace("_", "")
                    if (
                        ("товар" in name_c or "номенклатур" in name_c)
                        and (
                            "склад" in name_c
                            or "запас" in name_c
                            or "остат" in name_c
                            or "организац" in name_c
                        )
                        and not has_any(
                            name_c,
                            [
                                "котбору",
                                "котгрузке",
                                "кпоступлению",
                                "впути",
                                "вячейк",
                                "кперемещ",
                            ],
                        )
                    ):
                        sc += 450
                    elif has_any(
                        name_c,
                        [
                            "котбору",
                            "котгрузке",
                            "кпоступлению",
                            "впути",
                            "вячейк",
                            "кперемещ",
                        ],
                    ) or has_any(
                        blob_n,
                        ["к отбору", "к отгрузке", "в ячейках", "в пути"],
                    ):
                        sc -= 280
                    elif has_any(blob_n, ["товар", "склад", "запас", "остат"]):
                        sc += 120
                if is_doc and not has_any(blob_n, ["инвентар", "оприходован", "списан"]):
                    sc -= 60

        if goods_receipt:
            if has_word(blob_n, "поступление") and is_goods_services:
                sc += 50
            if has_any(
                blob_n,
                [
                    "на расчетный",
                    "расчетный счет",
                    "денежный документ",
                    "платежный",
                ],
            ):
                sc -= 80
            if has_any(blob_n, ["нма", "лизинг"]):
                sc -= 100

        if pay_ops and not want_invoice:
            if has_any(
                blob_n,
                [
                    "платежное поручение",
                    "платежное требование",
                    "списание с расчет",
                    "поступление на расчет",
                    "приходный кассовый",
                    "расходный кассовый",
                ],
            ) or any(
                x in name_only
                for x in (
                    "платежноепоручен",
                    "списаниесрасчет",
                    "поступлениенарасчет",
                    "приходныйкассов",
                    "расходныйкассов",
                )
            ):
                sc += 100
            if has_word(blob_n, "оплата") and "платежн" in blob_n and "карт" not in blob_n:
                sc += 40
            if "платежнойкарт" in blob_n or "оплатаплатежнойкарт" in name_only:
                if not has_word(p, "карта"):
                    sc -= 150
            if "счетнаоплат" in name_only or (
                has_word(name_only, "счет")
                and has_word(name_only, "оплата")
                and has_word(name_only, "покупатель")
            ):
                if not has_any(p, ["счет", "счёт"]):
                    sc -= 50
            if has_any(blob_n, ["ндс бюджет", "налогоплательщик", "иностранец"]):
                sc -= 80

        if want_invoice:
            if has_word(name_only, "счет") and has_word(name_only, "оплата"):
                sc += 60
            if has_word(blob_n, "платеж") and not has_word(name_only, "счет"):
                sc -= 40

        if has_word(p, "заказ") and not has_any(p, ["реализация", "продажа", "отгрузка"]):
            if has_word(name_only, "заказ"):
                sc += 200
            elif has_word(name_only, "счет") and has_word(name_only, "оплата"):
                sc += 180
                if has_word(name_only, "покупатель") or has_word(p, "покупатель"):
                    sc += 40
            elif has_word(name_only, "реализация"):
                sc -= 200
            if has_word(name_only, "реализация") and sc > 400:
                sc = 400 + (sc - 400) // 4

        if has_word(p, "перемещение"):
            if has_word(name_only, "перемещение"):
                sc += 200
                if has_word(name_only, "товар") or has_word(p, "товар"):
                    sc += 40
            elif n.startswith("справочник."):
                sc -= 100
            elif has_word(name_only, "номенклатура"):
                sc -= 120

        if has_any(p, ["накладная", "требование"]):
            if n.startswith("справочник."):
                sc -= 80
            if has_word(name_only, "требование") and has_word(name_only, "накладная"):
                sc += 160
            elif has_word(name_only, "накладная"):
                sc += 80

        if has_any(p, _doc_action_words()):
            if n.startswith("документ."):
                sc += 40
            if n.startswith("справочник."):
                sc -= 60

        hits = sum(1 for t in tokens if len(t) >= 4 and t in blob_n)
        if hits >= 2:
            sc += 20 * (hits - 1)
        if has_word(blob_n, "поступление") and not has_word(blob_n, "корректировка"):
            sc += 15
            if is_goods_services:
                sc += 25

        # штраф: совпадение только по общему слову в синониме, не в имени
        if len(tokens) >= 2 and token_hits_name == 0 and token_hits_blob >= 1:
            sc -= 50

        it["_phrase_score"] = sc
        ranked.append(it)

    ranked.sort(
        key=lambda x: (
            -int(x.get("_phrase_score") or 0),
            # при равном score предпочитаем *ТоваровУслуг и более короткие «основные» имена
            0
            if (
                "товар" in str(x.get("full_name") or "").lower()
                and "услуг" in str(x.get("full_name") or "").lower()
            )
            else 1,
            len(str(x.get("full_name") or "")),
        )
    )
    return ranked


def tokenize_query(text: str) -> list[str]:
    return re.findall(r"[a-zA-Zа-яА-ЯёЁ]{3,}", text or "")


def name_matches_search(full_name: str, search: str) -> bool:
    """
    Гибкое совпадение имени метаданных с search:
    • «покупатель» ∈ «…Покупателю» (стем)
    • «счет на оплату» → все значимые токены
    • «счет» ∈ имя объекта, содержащее «Счет…»
    """
    q = _norm(search).strip()
    if not q:
        return True
    blob = _norm(full_name)
    # прямое вхождение фразы / без пробелов
    if q in blob or q.replace(" ", "") in blob.replace(" ", ""):
        return True
    tokens = tokenize_query(q)
    if not tokens:
        return q in blob

    def _token_in_blob(tok: str) -> bool:
        if tok in blob:
            return True
        for st in word_stems(tok):
            if len(st) >= 4 and st in blob:
                return True
            # короткие (счет) — точное вхождение основы
            if len(st) >= 3 and st in blob:
                return True
        return False

    if len(tokens) == 1:
        return _token_in_blob(tokens[0])
    # несколько слов: все должны найтись (AND)
    return all(_token_in_blob(t) for t in tokens)


def score_name_for_search(full_name: str, search: str) -> int:
    """Чем выше — тем релевантнее для list/find ranking."""
    if not name_matches_search(full_name, search):
        return -1
    q = _norm(search)
    blob = _norm(full_name)
    name = blob.split(".")[-1] if "." in blob else blob
    score = 10
    tokens = tokenize_query(q)
    for t in tokens:
        stems = word_stems(t)
        if any(s in name for s in stems):
            score += 40
        elif any(s in blob for s in stems):
            score += 15
    # Семантика «счет + покупатель» vs «оплата картой» / «счет-фактура» (без имён конфигурации)
    has_schet = any(x in q for x in ("счет", "счёт", "invoice"))
    has_pokup = any(x in q for x in ("покупател", "клиент", "заказчик"))
    has_postav = any(x in q for x in ("поставщик", "vendor"))
    compact = name.replace(" ", "")
    if has_schet:
        # «счет … оплату …» в имени важнее голой «оплата…»
        if "счет" in name and "оплат" in name:
            score += 120
            if has_pokup and "покупател" in name:
                score += 80
            if has_postav and "поставщик" in name:
                score += 80
        if "счетфактур" in compact or "счётфактур" in compact:
            score -= 80  # счёт-фактура ≠ счёт на оплату
        if name.startswith("оплата") or ("платежн" in name and "счет" not in name):
            score -= 100
        if "расчетн" in name and not ("оплат" in name and "счет" in name):
            score -= 40
    if has_pokup and "покупател" in name:
        score += 50
    if has_postav and "поставщик" in name:
        score += 50
    # больше совпавших стем-токенов — выше
    score -= min(len(name), 40) // 8
    return score


def detect_intents(text: str) -> list[str]:
    """Определяет бизнес-намерения по фразе менеджера."""
    t = _norm(text)
    if not t.strip():
        return []

    found: list[str] = []
    for intent, keys in _intent_keywords().items():
        for k in keys:
            if _norm(k) in t:
                if intent not in found:
                    found.append(intent)
                break
    # эвристики на разговорный язык
    extra = [
        (r"\b(клиент|покупател|контрагент)\b", "клиенты"),
        (r"\b(товар|номенклатур|продукт)\b", "товары"),
        (r"\b(продаж|отгруз|выручк)\b", "продажи"),
        (r"\b(закуп|поставк|приход)\b", "закупки"),
        (r"\b(оплат|деньг|платеж|касс|банк)\b", "деньги"),
        (r"\b(склад|остатк)\b", "склады"),
        (r"\b(заказ)\b", "заказы"),
        # «счет / счета / счёт покупателя» — не путать с расчётным счётом
        (r"\bсч[её]т(а|у|ом|ов|е)?\b", "счета"),
        (r"invoice", "счета"),
    ]
    for pattern, intent in extra:
        if re.search(pattern, t) and intent not in found:
            found.append(intent)
    # «счет покупателя» важнее общего «деньги/оплата»
    if "счета" in found and "деньги" in found:
        if "оплат" not in t or "счет" in t or "счёт" in t:
            # оставить оба, но ranking в rank_metadata_hit разведёт
            pass
    return found


def keywords_for_query(text: str) -> list[str]:
    """Собирает уникальные подстроки для find_metadata."""
    intents = detect_intents(text)
    keys: list[str] = []
    for intent in intents:
        for k in _intent_keywords().get(intent, []):
            if k not in keys and len(k) >= 3:
                keys.append(k)

    # слова из фразы длиннее 3 букв (кириллица/латиница)
    for word in re.findall(r"[a-zA-Zа-яА-ЯёЁ]{4,}", text or ""):
        w = word.lower().replace("ё", "е")
        if w not in keys and w not in (
            "документ",
            "справочник",
            "покажи",
            "найди",
            "сколько",
            "какая",
            "какие",
            "нужно",
            "пожалуйста",
            "период",
            "месяц",
            "сегодня",
        ):
            keys.append(w)

    return keys[:12]


def rank_metadata_hit(
    item: dict[str, Any],
    query_keys: list[str],
    *,
    prefer_documents: bool = False,
) -> int:
    """Оценка релевантности кандидата из find_metadata."""
    name = str(item.get("name") or "")
    synonym = str(item.get("synonym") or "")
    full = str(item.get("full_name") or name)
    mtype = str(item.get("type") or "")
    name_l = name.lower().replace("ё", "е")
    syn_l = synonym.lower().replace("ё", "е")
    full_l = full.lower().replace("ё", "е")
    blob = f"{name_l} {syn_l} {full_l}"

    score = 0
    for k in query_keys:
        k = k.lower().replace("ё", "е")
        if not k:
            continue
        if name_l == k:
            score += 120
        elif k in name_l:
            score += 70
        elif k in syn_l:
            score += 45
        elif k in blob:
            score += 20

    # Тип объекта
    is_doc = full.startswith("Документ.") or mtype in ("Документы", "Документ")
    is_cat = full.startswith("Справочник.") or mtype in ("Справочники", "Справочник")
    is_reg = full.startswith("Регистр") or "регистр" in mtype.lower()

    # Семантика по ПОДСТРОКАМ (любая конфигурация), не по фиксированным Документ.X
    # Без бонуса «Товаров/Услуг» — иначе всегда побеждает УТ-имя РеализацияТоваровУслуг.
    qjoin = " ".join(query_keys).lower().replace("ё", "е")
    if "реализац" in qjoin or "продаж" in qjoin or "отгруз" in qjoin:
        if is_doc and "реализац" in name_l:
            score += 120
        elif is_doc and "продаж" in name_l:
            score += 100
        elif is_doc and ("реализац" in syn_l or "продаж" in syn_l):
            score += 90
        if is_reg or "ндс" in name_l:
            score -= 400
        if "присоединен" in name_l or "удалить" in name_l:
            score -= 300
        if "план" in name_l and "план" not in qjoin:
            score -= 200

    if any(x in qjoin for x in ("счет", "счёт", "invoice")):
        if is_doc and "счет" in name_l and "оплат" in name_l:
            score += 400
            if any(x in qjoin for x in ("покупател", "клиент")) and "покупател" in name_l:
                score += 150
            if "поставщик" in qjoin and "поставщик" in name_l:
                score += 150
        elif is_doc and "счет" in name_l:
            score += 200
        if is_doc and "счетфактур" in name_l:
            score -= 120
        if is_doc and (name_l.startswith("оплата") or ("платежн" in name_l and "счет" not in name_l)):
            score -= 250
        if is_reg:
            score -= 100

    if prefer_documents or any(
        x in qjoin
        for x in (
            "документ",
            "выгруз",
            "отчет",
            "отчёт",
            "excel",
            "эксель",
            "счет",
            "счёт",
        )
    ):
        if is_doc:
            score += 80
        if is_reg:
            score -= 150

    # справочники: совпадение стем-ключа в имени, без жёсткого «Номенклатура»
    if any(x in qjoin for x in ("номенклатур", "товар", "справочник")):
        if is_cat:
            score += 80
            if "номенклатур" in qjoin and "номенклатур" in name_l:
                score += 300
        if is_reg:
            score -= 100

    if _noise_re().search(full):
        score -= 80
    score -= min(len(name), 50) // 10
    return score


def suggest_objects(
    text: str,
    find_fn: Callable[..., dict[str, Any]],
    *,
    object_type: str = "Все",
    limit: int = 15,
) -> dict[str, Any]:
    """
    Универсальный подбор объектов конфигурации под фразу менеджера.

    find_fn(search_query, object_type, limit) → ответ find_metadata.
    """
    intents = detect_intents(text)
    keys = keywords_for_query(text)
    if not keys:
        keys = [text.strip()] if text and text.strip() else []

    text_l = (text or "").lower().replace("ё", "е")
    prefer_docs = any(
        x in text_l
        for x in (
            "документ",
            "выгруз",
            "отчет",
            "отчёт",
            "excel",
            "эксель",
            "реализац",
            "продаж",
            "заказ",
            "счет",
            "счёт",
            "оплат",
        )
    )
    # для «реализация/продажи/счета» ищем в первую очередь среди Документов
    search_types: list[str]
    if object_type and object_type != "Все":
        search_types = [object_type]
    elif prefer_docs and any(
        x in text_l
        for x in ("реализац", "продаж", "заказ", "поступлен", "счет", "счёт", "покупател")
    ):
        search_types = ["Документы", "Все"]
    else:
        search_types = ["Все"]

    # доп. стемы из фразы (без имён конкретной конфигурации)
    if any(x in text_l for x in ("счет", "счёт")) and "покупател" in text_l:
        for k in ("счет", "оплат", "покупател"):
            if k not in keys:
                keys.insert(0, k)
    elif any(x in text_l for x in ("счет", "счёт")):
        for k in ("счет", "оплат"):
            if k not in keys:
                keys.insert(0, k)

    merged: dict[str, dict[str, Any]] = {}
    searched: list[str] = []

    for key in keys:
        if not key:
            continue
        searched.append(key)
        for otype in search_types:
            raw = find_fn(key, otype, 50)
            if not isinstance(raw, dict):
                continue
            # raw может быть slim {items} или backend {results}
            items = raw.get("results") or raw.get("items") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                full = str(item.get("full_name") or "")
                if not full:
                    continue
                sc = rank_metadata_hit(item, keys, prefer_documents=prefer_docs)
                prev = merged.get(full)
                if prev is None or sc > prev.get("_score", 0):
                    row = dict(item)
                    row["_score"] = sc
                    merged[full] = row

    ranked = sorted(merged.values(), key=lambda x: -x.get("_score", 0))
    clean = [r for r in ranked if r.get("_score", 0) > 0]
    if not clean:
        clean = ranked

    results = []
    for r in clean[:limit]:
        results.append(
            {
                "full_name": r.get("full_name"),
                "name": r.get("name"),
                "synonym": r.get("synonym") or "",
                "type": r.get("type") or "",
                "score": r.get("_score", 0),
            }
        )

    return {
        "query": text,
        "detected_intents": intents,
        "search_keywords_used": searched,
        "found_count": len(results),
        "candidates": results,
        "note": (
            "Имена зависят от конфигурации (БП / УТ / ERP / самописная). "
            "Возьми full_name из candidates, затем resolve_business_fields / search_fields."
        ),
        "next_steps": [
            "Выбери подходящий full_name из candidates",
            "resolve_business_fields(full_name) — поля этой конфигурации",
            "get_object_structure → execute_query",
        ],
    }
