# -*- coding: utf-8 -*-
"""Эмбеддинги: fastembed (локально) или OpenAI-compatible API."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any, Sequence

_CONTEXT_END = "// --- КОНЕЦ КОНТЕКСТА ---"
# Версия формата текста для эмбеддинга: входит в content_hash, смена → инкрементальный пересчёт векторов.
EMBED_TEXT_VERSION = "p3-dedup"
# Модель MiniLM видит ~128 токенов (≈600 симв.): список процедур даёт семантике весь чанк, а не его начало.
PROCS_MAX_CHARS = 260
_META_LINE_RE = re.compile(
    r"^//\s*(Тип контента|Класс метаданных|Объект|Расположение):\s*(.+)$",
    re.M,
)


@lru_cache(maxsize=4)
def _fastembed_model(model_name: str, threads: int = 0):
    from fastembed import TextEmbedding

    # по умолчанию кэш в %TEMP%\<сеанс RDP> — чистится при выходе, модель качается заново (~1 мин)
    cache_dir = os.environ.get("FASTEMBED_CACHE_PATH") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache", "fastembed")
    n = int(threads or 0)
    if n > 0:
        try:
            return TextEmbedding(model_name=model_name, threads=n, cache_dir=cache_dir)
        except TypeError:
            pass
    return TextEmbedding(model_name=model_name, cache_dir=cache_dir)


_SECTION_LINE_RE = re.compile(r"^Раздел:\s*\S+\s*$")


def dedup_headings(text: str) -> str:
    """Статьи справки начинаются с заголовка, повторённого 3 раза + «Раздел: …» — это съедает окно модели."""
    seen: set[str] = set()
    out: list[str] = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if _SECTION_LINE_RE.match(s):
            continue
        if s.startswith("# "):
            if s in seen:
                continue
            seen.add(s)
        out.append(ln)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))


def strip_context_header(text: str) -> str:
    t = text or ""
    if _CONTEXT_END in t:
        return t.split(_CONTEXT_END, 1)[-1].lstrip("\n")
    return t


def _meta_prefix_from_header(text: str) -> str:
    """Короткая строка метаданных для passage (без длинного header-блока)."""
    head = (text or "").split(_CONTEXT_END, 1)[0] if _CONTEXT_END in (text or "") else ""
    if not head:
        return ""
    parts: list[str] = []
    for m in _META_LINE_RE.finditer(head):
        label, val = m.group(1), (m.group(2) or "").strip()
        if not val or val == "—":
            continue
        if label == "Объект":
            parts.append(f"объект={val}")
        elif label == "Класс метаданных":
            parts.append(f"класс={val}")
        elif label == "Расположение":
            parts.append(f"где={val[:120]}")
        elif label == "Тип контента":
            parts.append(val)
    return "; ".join(parts)


class Embedder:
    def __init__(self, cfg: dict[str, Any]) -> None:
        emb = cfg.get("embedding") or {}
        self.provider = (emb.get("provider") or "fastembed").lower()
        self.model = (
            emb.get("model")
            or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )
        self.batch_size = int(emb.get("batch_size") or 64)
        # 0 = все ядра (ONNX/OpenMP)
        self.threads = int(emb.get("threads") or os.environ.get("RAG_EMBED_THREADS") or 0)
        # e5-base ~512 tok ≈ 1500–1800 chars; не кормим весь 6k-чанк
        self.passage_max_chars = int(emb.get("passage_max_chars") or 1400)
        self._openai = None
        self._dim: int | None = emb.get("vector_size")
        self._is_e5 = "e5" in (self.model or "").lower()

        if self.provider in ("openai", "aigate", "openrouter", "lmstudio", "ollama"):
            from openai import OpenAI

            llm = cfg.get("llm") or {}
            base = (emb.get("base_url") or llm.get("base_url") or "").rstrip("/")
            key = emb.get("api_key") or llm.get("api_key") or "not-needed"
            self._openai = OpenAI(base_url=base, api_key=key)
            if not self.model or self.model.startswith("intfloat/"):
                self.model = emb.get("model") or "text-embedding-3-small"
                self._is_e5 = "e5" in (self.model or "").lower()
        elif self.provider != "fastembed":
            raise ValueError(
                f"Неизвестный embedding.provider={self.provider!r}. "
                "Используйте fastembed | openai"
            )

    @property
    def vector_size(self) -> int:
        if self._dim:
            return int(self._dim)
        v = self.embed_one("probe")
        self._dim = len(v)
        return int(self._dim)

    def embed_one(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        cleaned = [(t or "").strip() or " " for t in texts]
        if self.provider == "fastembed":
            model = _fastembed_model(self.model, self.threads)
            out: list[list[float]] = []
            for vec in model.embed(cleaned, batch_size=self.batch_size):
                out.append([float(x) for x in vec])
            return out

        assert self._openai is not None
        resp = self._openai.embeddings.create(model=self.model, input=list(cleaned))
        data = sorted(resp.data, key=lambda d: d.index)
        return [list(d.embedding) for d in data]

    def embed_query(self, text: str) -> list[float]:
        q = (text or "").strip() or " "
        if self.provider == "fastembed" and self._is_e5:
            model = _fastembed_model(self.model, self.threads)
            vec = next(model.embed([f"query: {q}"], batch_size=1))
            return [float(x) for x in vec]
        return self.embed_one(q)

    def prepare_passage(
        self, text: str, *, max_chars: int | None = None, procs: Sequence[str] = ()
    ) -> str:
        """
        Текст для эмбеддинга: body без длинного context-header.
        Полный text остаётся в payload.
        """
        limit = self.passage_max_chars if max_chars is None else max_chars
        raw = (text or "").strip() or " "
        meta = _meta_prefix_from_header(raw)
        body = dedup_headings(strip_context_header(raw)).strip() or raw
        plist = ", ".join(procs)
        if len(plist) > PROCS_MAX_CHARS:
            plist = plist[:PROCS_MAX_CHARS].rsplit(", ", 1)[0]
        head = "\n".join(x for x in (meta, f"процедуры: {plist}" if plist else "") if x)
        t = f"{head}\n{body}" if head else body
        if limit > 0 and len(t) > limit:
            t = t[:limit]
        if self.provider == "fastembed" and self._is_e5:
            if not t.lower().startswith("passage:"):
                return f"passage: {t}"
        return t
