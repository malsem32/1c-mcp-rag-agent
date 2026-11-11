# -*- coding: utf-8 -*-
"""Быстрый unit-check retrieval helpers (без Qdrant)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from vector.embed import Embedder, strip_context_header, _meta_prefix_from_header
    from vector.search import _rrf_merge, _lexical_query_variants, _light_rerank
    from config import load_config

    sample = (
        "// --- НАЧАЛО КОНТЕКСТА ---\n"
        "// Тип контента: Модуль 1С\n"
        "// Класс метаданных: Документы\n"
        "// Объект: ТестДокумент\n"
        "// Расположение: Documents/ТестДокумент/Ext/ObjectModule.bsl\n"
        "// --- КОНЕЦ КОНТЕКСТА ---\n"
        "\nПроцедура ОбработкаПроведения(Отказ, Режим)\n"
        "  // body\n"
        "КонецПроцедуры\n"
    )
    body = strip_context_header(sample)
    assert "НАЧАЛО КОНТЕКСТА" not in body
    assert "ОбработкаПроведения" in body
    meta = _meta_prefix_from_header(sample)
    assert "ТестДокумент" in meta

    cfg = load_config()
    emb = Embedder(cfg)
    passage = emb.prepare_passage(sample)
    assert "НАЧАЛО КОНТЕКСТА" not in passage
    assert "ОбработкаПроведения" in passage or "ТестДокумент" in passage

    variants = _lexical_query_variants(
        "почему не заполнился Контрагент в документе"
    )
    assert variants[0].startswith("почему")
    assert any("Контрагент" == v for v in variants)
    assert any("заполнился" == v or "заполнил" in v.lower() for v in variants)

    a = [
        {"path": "A", "id": "1", "score": 90, "match_kind": "exact", "snippet": "x"},
        {"path": "B", "id": "2", "score": 80, "match_kind": "exact", "snippet": "y"},
    ]
    b = [
        {"path": "B", "id": "2", "score": 50, "match_kind": "semantic", "snippet": "y"},
        {"path": "C", "id": "3", "score": 40, "match_kind": "semantic", "snippet": "z"},
    ]
    m = _rrf_merge(a, b, limit=3)
    assert m[0]["path"] == "B"  # в обеих списках
    assert m[0]["match_kind"] == "hybrid"

    rr = _light_rerank(
        [
            {
                "path": "CommonModules/Foo",
                "score": 1.0,
                "proc_names": ["Bar"],
                "snippet": "zzz",
                "object_name": "Foo",
            },
            {
                "path": "Documents/X",
                "score": 1.0,
                "proc_names": ["ОбработкаПроведения"],
                "snippet": "ОбработкаПроведения",
                "object_name": "X",
            },
        ],
        "ОбработкаПроведения",
    )
    assert rr[0]["proc_names"][0] == "ОбработкаПроведения"

    print("OK retrieval helpers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
