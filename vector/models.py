# -*- coding: utf-8 -*-
"""Каталог моделей эмбеддингов: характеристики, рекомендации по чанкам, проверка окна по токенизатору."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROBE_FILE = ROOT / "exports" / "model_probe.json"
CHARS_PER_TOKEN = 4.0  # измерено на BSL-коде: 128 токенов ≈ 500–600 символов

# speed — относительная скорость индексации на CPU (оценка; MiniLM = 1). Реальные цифры даёт tune_embedding.
MODELS: list[dict[str, Any]] = [
    {
        "key": "minilm", "name": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", "provider": "fastembed",
        "title": "MiniLM-L12 multilingual", "dim": 384, "size_gb": 0.22, "window_tokens": 128, "speed": 1,
        "langs": "~50 языков, русский хорошо", "tier": "быстрая",
        "notes": "Текущая по умолчанию. Окно 128 токенов (≈500 симв.) — в fastembed заявлено 512, замер показал 128.",
    },
    {
        "key": "mpnet", "name": "sentence-transformers/paraphrase-multilingual-mpnet-base-v2", "provider": "fastembed",
        "title": "mpnet-base multilingual", "dim": 768, "size_gb": 1.0, "window_tokens": 384, "speed": 4,
        "langs": "~50 языков, русский хорошо", "tier": "средняя",
        "notes": "Точнее MiniLM, но заметно медленнее. Окно 384 — по каталогу fastembed, не проверено: нажмите «Проверить окно».",
    },
    {
        "key": "e5", "name": "intfloat/multilingual-e5-large", "provider": "fastembed",
        "title": "multilingual-e5-large", "dim": 1024, "size_gb": 2.24, "window_tokens": 512, "speed": 15,
        "langs": "~100 языков, русский отлично", "tier": "медленная, самая точная",
        "notes": "Окно 512 токенов (≈2000 симв.). На CPU индексация очень долгая; разумна при GPU или большом бюджете времени.",
    },
    {
        "key": "jina3", "name": "jinaai/jina-embeddings-v3", "provider": "fastembed",
        "title": "jina-embeddings-v3", "dim": 1024, "size_gb": 2.29, "window_tokens": 1024, "speed": 18,
        "langs": "~100 языков", "tier": "медленная, длинное окно",
        "notes": "Длинное окно (в каталоге fastembed — 1024). В проекте не проверялась — используйте после замера «Подбор настроек».",
    },
    {
        "key": "jina-code", "name": "jinaai/jina-embeddings-v2-base-code", "provider": "fastembed",
        "title": "jina-embeddings-v2-base-code", "dim": 768, "size_gb": 0.64, "window_tokens": 8192, "speed": 5,
        "langs": "английский + языки программирования", "tier": "для кода, не для русских запросов",
        "notes": "Заточена под код, но запросы на русском она понимает слабо. Имеет смысл только для англоязычных конфигураций.",
    },
    {
        "key": "openai-small", "name": "text-embedding-3-small", "provider": "openai",
        "title": "OpenAI text-embedding-3-small (API)", "dim": 1536, "size_gb": 0, "window_tokens": 8191, "speed": 0.5,
        "langs": "многоязычная", "tier": "API, платно",
        "notes": "Быстро, без нагрузки на CPU, но код уходит во внешний сервис и тарифицируется. Нужны embedding.base_url и ключ.",
    },
]
BY_NAME = {m["name"]: m for m in MODELS}


def load_probe() -> dict[str, int]:
    try:
        return json.loads(PROBE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def window_chars(tokens: int) -> int:
    return int(tokens * CHARS_PER_TOKEN)


def recommend(model: dict[str, Any], window_tokens: int | None = None) -> dict[str, dict[str, int]]:
    """Три пресета (chunk, overlap, passage) под окно модели."""
    wt = window_tokens or model["window_tokens"]
    wc = max(300, min(window_chars(wt), 3200))
    return {
        "fast": {"chunk": 2800, "overlap": 150, "passage": min(wc, 2800)},
        "balanced": {"chunk": max(1200, min(2 * wc, 2800)), "overlap": 150, "passage": min(wc, 2800)},
        "precise": {"chunk": max(600, min(wc, 2800)), "overlap": 100, "passage": min(wc, 2800)},
    }


def catalog() -> list[dict[str, Any]]:
    probe = load_probe()
    out = []
    for m in MODELS:
        verified = probe.get(m["name"])
        wt = verified or m["window_tokens"]
        out.append({**m, "window_tokens": wt, "window_verified": bool(verified),
                    "window_chars": window_chars(wt), "presets": recommend(m, wt)})
    return out


def probe_window(name: str) -> int:
    """Реальное окно по токенизатору (скачивает модель при первом вызове)."""
    from vector.embed import _fastembed_model

    m = _fastembed_model(name, 0)
    trunc = getattr(m.model.tokenizer, "truncation", None) or {}
    n = int(trunc.get("max_length") or 0)
    if not n:
        raise RuntimeError("токенизатор не сообщил max_length")
    data = load_probe()
    data[name] = n
    PROBE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROBE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return n


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    if len(sys.argv) > 2 and sys.argv[1] == "probe":
        print(f"Проверяю окно модели {sys.argv[2]} (первый раз — скачивание)…", flush=True)
        n = probe_window(sys.argv[2])
        print(f"Окно: {n} токенов ≈ {window_chars(n)} символов кода", flush=True)
