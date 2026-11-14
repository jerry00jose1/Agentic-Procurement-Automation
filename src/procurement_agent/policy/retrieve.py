"""Retrieve policy sections. The embedder only encodes text; it does not decide."""

from __future__ import annotations

import math
from typing import Protocol

from procurement_agent.config import get_settings
from procurement_agent.policy.corpus import PolicyChunk, load_policy

_CACHE: dict[tuple[int, tuple[str, ...]], list[tuple[PolicyChunk, list[float]]]] = {}


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


def shared_embedder() -> FastEmbedder:
    global _shared
    if _shared is None:
        _shared = FastEmbedder()
    return _shared


_shared: FastEmbedder | None = None


class FastEmbedder:
    """Local BAAI/bge-small encoder. Loaded on the first policy question."""

    def __init__(self) -> None:
        self._model: object | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=get_settings().embedding_model)
        encoded = self._model.embed(texts)  # type: ignore[attr-defined]
        return [[float(value) for value in vector] for vector in encoded]


def retrieve(
    query: str,
    embedder: Embedder,
    *,
    chunks: list[PolicyChunk] | None = None,
    limit: int = 3,
) -> list[PolicyChunk]:
    sections = chunks if chunks is not None else load_policy()
    if not sections or limit <= 0:
        return []
    key = (id(embedder), tuple(section.id for section in sections))
    indexed = _CACHE.get(key)
    if indexed is None:
        vectors = embedder.embed([section.text for section in sections])
        indexed = list(zip(sections, vectors, strict=True))
        _CACHE[key] = indexed
    query_vector = embedder.embed([query])[0]
    ranked = sorted(indexed, key=lambda pair: _cosine(query_vector, pair[1]), reverse=True)
    return [section for section, _vector in ranked[:limit]]


def _cosine(left: list[float], right: list[float]) -> float:
    product = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return product / (left_norm * right_norm)
