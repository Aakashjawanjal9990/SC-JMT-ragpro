"""Vector store adapters.

Every adapter returns chunks whose id is the chunk UUID, whose text is chunks.content
unmodified, and whose metadata follows the record contract in app.retrieval.models.
Adapters must only return citable chunks (models.is_citable): active, current source
version, source file belonging to the same judgment. Apply that inside the DB query
so excluded rows do not use up top-k slots.

Production uses PgVectorStore (pg_store.py) on the source database; the in-memory
store is for local development and tests.
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import numpy as np

from app.retrieval.config import RetrievalSettings
from app.retrieval.filters import Filters, matches
from app.retrieval.models import Chunk, RetrievedChunk, is_citable


class VectorStore(Protocol):
    def search(
        self, query_vector: np.ndarray, k: int, filters: Filters | None = None
    ) -> list[RetrievedChunk]:
        """Top-k chunks by similarity (higher is better), with text and metadata."""
        ...

    def get(self, ids: Sequence[str], filters: Filters | None = None) -> list[Chunk]:
        """Re-read citable chunks matching `filters` by id, in the requested order;
        others are skipped."""
        ...


class InMemoryVectorStore:
    """Brute-force numpy store over an exported corpus.

    Directory layout: chunks.jsonl (one {"id", "text", "metadata"} object per line)
    and embeddings.npy (float32, row i belongs to line i).
    """

    def __init__(
        self, chunks: Sequence[Chunk], embeddings: np.ndarray, metric: str = "cosine"
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings have different lengths")
        if metric not in {"cosine", "dot"}:
            raise ValueError(f"Unsupported metric for InMemoryVectorStore: {metric!r}")
        self._chunks = list(chunks)
        self._metric = metric
        vectors = np.asarray(embeddings, dtype=np.float32)
        if metric == "cosine":
            vectors = vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
        self._vectors = vectors
        self._row_by_id = {chunk.id: row for row, chunk in enumerate(self._chunks)}
        if len(self._row_by_id) != len(self._chunks):
            raise ValueError("Duplicate chunk ids in vector store")
        self._citable = np.fromiter(
            (is_citable(c.metadata) for c in self._chunks), bool, len(self._chunks)
        )

    @classmethod
    def load(cls, path: str | Path, metric: str = "cosine") -> "InMemoryVectorStore":
        path = Path(path)
        with (path / "chunks.jsonl").open(encoding="utf-8") as handle:
            records = [json.loads(line) for line in handle if line.strip()]
        chunks = [Chunk(r["id"], r["text"], r.get("metadata", {})) for r in records]
        return cls(chunks, np.load(path / "embeddings.npy"), metric=metric)

    def search(
        self, query_vector: np.ndarray, k: int, filters: Filters | None = None
    ) -> list[RetrievedChunk]:
        query = np.asarray(query_vector, dtype=np.float32).ravel()
        if self._metric == "cosine":
            query = query / max(float(np.linalg.norm(query)), 1e-12)
        scores = np.where(self._citable, self._vectors @ query, -np.inf)
        if filters:
            allowed = np.fromiter(
                (matches(c.metadata, filters) for c in self._chunks), bool, len(self._chunks)
            )
            scores = np.where(allowed, scores, -np.inf)
        order = np.argsort(-scores, kind="stable")[:k]
        return [
            RetrievedChunk(
                id=self._chunks[i].id,
                text=self._chunks[i].text,
                metadata=self._chunks[i].metadata,
                score=float(scores[i]),
                dense_score=float(scores[i]),
            )
            for i in order
            if np.isfinite(scores[i])
        ]

    def get(self, ids: Sequence[str], filters: Filters | None = None) -> list[Chunk]:
        rows = (self._row_by_id.get(i) for i in ids)
        return [
            self._chunks[r]
            for r in rows
            if r is not None and self._citable[r] and matches(self._chunks[r].metadata, filters)
        ]


def open_vector_store(settings: RetrievalSettings) -> VectorStore:
    if settings.vector_store == "memory":
        if not settings.vector_store_path:
            raise ValueError("Set RETRIEVAL_VECTOR_STORE_PATH to the exported corpus directory")
        return InMemoryVectorStore.load(settings.vector_store_path, metric=settings.metric)
    raise ValueError(
        f"No adapter for vector store {settings.vector_store!r}; add one in "
        "app/retrieval/vector_store.py"
    )
