"""Retrieval pipeline from Pratik's branch, adapted to the backend storage ports.

It uses HyDE for dense queries, pseudo-relevance feedback for BM25, reciprocal-rank
fusion, reranking and per-judgment score decay. BM25-only hits are re-read from the
source store so passage text and citation metadata remain source-of-truth values.
"""

import logging
import os
import re
import time
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from app.retrieval.bm25_memory import InMemoryBM25
from app.retrieval.bm25_pg import PostgresBM25Index
from app.retrieval.config import RetrievalSettings
from app.retrieval.db import Database
from app.retrieval.embedder import make_query_embedder
from app.retrieval.filters import Filters
from app.retrieval.llm import GroqChat
from app.retrieval.models import DOCUMENT_ID, Chunk, RetrievedChunk, format_citation
from app.retrieval.pg_store import PgVectorStore, embedding_dimensions, verify_schema
from app.retrieval.query_expansion import GroqPseudoQuery, prf_terms
from app.retrieval.reranker import make_reranker
from app.retrieval.sqlite_store import DEFAULT_CHUNKS_SQL, SqliteVectorStore
from app.retrieval.tokenizer import tokenize
from app.retrieval.vector_store import VectorStore, open_vector_store

logger = logging.getLogger(__name__)

_NON_ASCII_LETTER = re.compile(r"[^\W\d_a-zA-Z]")


class BM25Searcher(Protocol):
    def search(
        self, query: str, k: int, filters: Filters | None = None
    ) -> list[tuple[str, float]]: ...

    def search_weighted(
        self, term_weights: dict[str, float], k: int, filters: Filters | None = None
    ) -> list[tuple[str, float]]: ...

    def idf(self, terms: list[str]) -> dict[str, float]: ...


@dataclass
class RetrievalResult:
    query: str
    chunks: list[RetrievedChunk]
    timings_ms: dict[str, float] = field(default_factory=dict)


def rank_fusion(*rankings: Sequence[str], k: int = 60) -> dict[str, float]:
    """Reciprocal rank fusion: score = sum of 1 / (k + rank) over the lists a chunk is in."""
    scores: dict[str, float] = {}
    for ids in rankings:
        for rank, chunk_id in enumerate(ids, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


def format_context(chunks: Sequence[RetrievedChunk]) -> str:
    """Numbered evidence block for the LLM prompt, each tagged "(<doc>, p.<page>)"."""
    return "\n\n".join(
        f"[{i}] {format_citation(chunk.metadata)}\n{chunk.text.strip()}"
        for i, chunk in enumerate(chunks, start=1)
    )


def _has_content(query: str) -> bool:
    # Stopword-only English queries carry no signal for either retriever; non-Latin
    # text yields no BM25 tokens but can still be embedded.
    return bool(tokenize(query)) or bool(_NON_ASCII_LETTER.search(query))


def _ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


class HybridRetriever:
    def __init__(
        self,
        store: VectorStore,
        embedder: Any,
        reranker: Any | None = None,
        bm25: BM25Searcher | None = None,
        *,
        top_k: int = 8,
        candidate_k: int = 20,
        max_per_doc: int | None = 3,
        pseudo_query: GroqPseudoQuery | None = None,
        pseudo_weight: float = 0.5,
        prf_docs: int = 0,
        prf_terms_count: int = 10,
        prf_weight: float = 0.3,
        doc_decay: float = 0.85,
        rrf_k: int = 60,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.reranker = reranker
        self.bm25 = bm25
        self.top_k = top_k
        self.candidate_k = candidate_k
        self.max_per_doc = max_per_doc
        if not 0 <= pseudo_weight <= 1:
            raise ValueError("pseudo_weight must be between 0 and 1")
        if not 0 < doc_decay <= 1:
            raise ValueError("doc_decay must be greater than 0 and at most 1")
        self.pseudo_query = pseudo_query
        self.pseudo_weight = pseudo_weight
        self.prf_docs = prf_docs
        self.prf_terms_count = prf_terms_count
        self.prf_weight = prf_weight
        self.doc_decay = doc_decay
        self.rrf_k = rrf_k
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="retrieval")
        self._llm_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pseudo")
        self._databases: list[Database] = []

    @classmethod
    def from_settings(cls, settings: RetrievalSettings | None = None) -> "HybridRetriever":
        settings = settings or RetrievalSettings.from_environment()
        databases: list[Database] = []
        embedding_options = dict(
            normalize=settings.normalize_embeddings,
            query_prefix=settings.query_prefix,
            project=settings.gcp_project,
            location=settings.gcp_location,
        )
        if settings.vector_store == "pgvector":
            if not settings.embedding_model:
                raise ValueError(
                    "Set RETRIEVAL_EMBEDDING_MODEL to the model that embedded chunks.embedding"
                )
            source_db = Database(settings.source_db, "source database")
            databases.append(source_db)
            verify_schema(source_db)
            store: VectorStore = PgVectorStore(
                source_db,
                metric=settings.metric,
                ef_search=settings.pg_ef_search,
                iterative_scan=settings.pg_iterative_scan,
            )
            embedder = make_query_embedder(
                settings.embedding_provider,
                settings.embedding_model,
                # Query vectors must fit the column (vector(768) in the confirmed schema).
                expected_dimensions=embedding_dimensions(source_db),
                **embedding_options,
            )
        elif settings.vector_store == "sqlite":
            if not settings.sqlite_path:
                raise ValueError("Set RETRIEVAL_SQLITE_PATH to the SQLite export")
            store = SqliteVectorStore(
                settings.sqlite_path,
                settings.sqlite_chunks_sql or DEFAULT_CHUNKS_SQL,
                metric="dot" if settings.metric == "ip" else "cosine",
                dimensions=settings.embedding_dimensions,
            )
            if not settings.embedding_model:
                raise ValueError(
                    "Set RETRIEVAL_EMBEDDING_MODEL to the model that embedded the SQLite chunks"
                )
            embedder = make_query_embedder(
                settings.embedding_provider,
                settings.embedding_model,
                expected_dimensions=settings.embedding_dimensions,
                **embedding_options,
            )
        else:
            if not settings.embedding_model:
                raise ValueError("Set RETRIEVAL_EMBEDDING_MODEL")
            store = open_vector_store(settings)
            embedder = make_query_embedder(
                settings.embedding_provider, settings.embedding_model, **embedding_options
            )

        bm25: BM25Searcher | None = None
        if settings.bm25_backend == "postgres":
            bm25_db = Database(settings.bm25_db, "BM25 database")
            databases.append(bm25_db)
            bm25 = PostgresBM25Index(bm25_db, settings.bm25_schema, settings.bm25_postings_per_term)
        elif settings.bm25_backend == "memory":
            if not hasattr(store, "iter_chunks"):
                raise ValueError(
                    "RETRIEVAL_BM25_BACKEND=memory needs a store that can list its chunks"
                )
            bm25 = InMemoryBM25.build(store.iter_chunks())
        elif settings.bm25_backend != "none":
            raise ValueError(f"Unknown RETRIEVAL_BM25_BACKEND {settings.bm25_backend!r}")

        pseudo_query = (
            GroqPseudoQuery(
                GroqChat(
                    settings.pseudo_query_model,
                    os.getenv("GROQ_API_KEY", ""),
                    max_tokens=settings.pseudo_query_max_tokens,
                    timeout_seconds=settings.pseudo_query_timeout_seconds,
                    reasoning_effort=settings.pseudo_query_reasoning_effort,
                )
            )
            if settings.pseudo_query_enabled
            else None
        )
        retriever = cls(
            store,
            embedder,
            make_reranker(
                settings.reranker,
                model_name=settings.reranker_model,
                max_length=settings.reranker_max_length,
                project=settings.gcp_project,
                location=settings.reranker_location,
            ),
            bm25,
            top_k=settings.top_k,
            candidate_k=settings.candidate_k,
            max_per_doc=settings.max_per_doc,
            pseudo_query=pseudo_query,
            pseudo_weight=settings.pseudo_query_weight,
            prf_docs=settings.prf_feedback_docs if settings.prf_enabled else 0,
            prf_terms_count=settings.prf_expansion_terms,
            prf_weight=settings.prf_expansion_weight,
            doc_decay=settings.doc_decay,
        )
        retriever._databases = databases
        return retriever

    def warmup(self) -> None:
        """Load models / open clients up front so the first query is fast."""
        self.embedder.warmup()
        if self.pseudo_query is not None:
            self.pseudo_query.warmup()
        if self.reranker is not None:
            self.reranker.warmup()

    def healthcheck(self) -> bool:
        """Report whether the configured source has usable retrieval data."""
        store_healthcheck = getattr(self.store, "healthcheck", None)
        if store_healthcheck is not None:
            return bool(store_healthcheck())
        if hasattr(self.store, "__len__"):
            return len(self.store) > 0
        return True

    def close(self) -> None:
        self._executor.shutdown(wait=False)
        self._llm_executor.shutdown(wait=False)
        if self.pseudo_query is not None:
            self.pseudo_query.chat.close()
        for database in self._databases:
            database.close()

    def __enter__(self) -> "HybridRetriever":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _query_vector(
        self, query: str, pseudo_job: Future[str] | None
    ) -> tuple[np.ndarray, dict[str, float]]:
        timings: dict[str, float] = {}
        start = time.perf_counter()
        vector = np.asarray(self.embedder.embed(query), dtype=np.float32)
        timings["embed"] = _ms(start)
        if pseudo_job is None or self.pseudo_query is None:
            return vector, timings
        try:
            start = time.perf_counter()
            passage = pseudo_job.result(timeout=self.pseudo_query.timeout_seconds)
            timings["pseudo_llm_wait"] = _ms(start)
            start = time.perf_counter()
            embed_document = getattr(self.embedder, "embed_document", self.embedder.embed)
            pseudo_vector = np.asarray(embed_document(passage), dtype=np.float32)
            timings["pseudo_embed"] = _ms(start)
            mixed = (1 - self.pseudo_weight) * vector + self.pseudo_weight * pseudo_vector
            return (mixed / max(float(np.linalg.norm(mixed)), 1e-12)).astype(np.float32), timings
        except Exception as exc:
            pseudo_job.cancel()
            logger.warning("HyDE query failed; using the original question: %r", exc)
            return vector, timings

    def _vector_search(
        self, query: str, k: int, filters: Filters | None, pseudo_job: Future[str] | None
    ):
        vector, timings = self._query_vector(query, pseudo_job)
        start = time.perf_counter()
        results = self.store.search(vector, k, filters)
        return results, timings, _ms(start)

    def _bm25_search(self, query: str, k: int, filters: Filters | None):
        start = time.perf_counter()
        terms = sorted(set(tokenize(query)))
        weights = {term: 1.0 for term in terms}
        hits = self.bm25.search_weighted(weights, k, filters)
        timings = {"bm25": _ms(start)}
        if not self.prf_docs or not hits:
            return hits, timings

        start = time.perf_counter()
        try:
            scores = dict(hits[: self.prf_docs])
            feedback_chunks = self.store.get(list(scores), filters)
            feedback = [
                RetrievedChunk(
                    id=chunk.id,
                    text=chunk.text,
                    metadata=chunk.metadata,
                    score=scores[chunk.id],
                    bm25_score=scores[chunk.id],
                )
                for chunk in feedback_chunks
                if chunk.id in scores
            ]
            expansion = prf_terms(
                terms,
                feedback,
                self.bm25.idf,
                num_terms=self.prf_terms_count,
            )
            if expansion:
                weights.update(
                    {term: self.prf_weight * weight for term, weight in expansion.items()}
                )
                hits = self.bm25.search_weighted(weights, k, filters)
        except Exception as exc:
            logger.warning(
                "BM25 pseudo-relevance feedback failed; using first-pass results: %r", exc
            )
        timings["prf"] = _ms(start)
        return hits, timings

    def search(
        self, query: str, *, top_k: int | None = None, filters: Filters | None = None
    ) -> RetrievalResult:
        """Best top_k chunks for the query. `score` is the reranker score, or the rank
        fusion score when no reranker is configured."""
        total_start = time.perf_counter()
        top_k = self.top_k if top_k is None else top_k
        candidate_k = max(self.candidate_k, top_k)
        timings: dict[str, float] = {}
        if top_k <= 0 or not _has_content(query):
            return RetrievalResult(query, [], {"total": _ms(total_start)})

        # 1. BM25 and the pseudo-passage start in the background while dense search runs.
        pseudo_job = (
            self._llm_executor.submit(self.pseudo_query.generate, query)
            if self.pseudo_query is not None
            else None
        )
        vector_job = self._executor.submit(
            self._vector_search, query, candidate_k, filters, pseudo_job
        )
        bm25_job = (
            self._executor.submit(self._bm25_search, query, candidate_k, filters)
            if self.bm25
            else None
        )
        vector_hits, vector_timings, timings["vector"] = vector_job.result()
        timings.update(vector_timings)
        bm25_hits: list[tuple[str, float]] = []
        if bm25_job is not None:
            bm25_hits, bm25_timings = bm25_job.result()
            timings.update(bm25_timings)

        # 2. Merge, keeping each chunk once.
        candidates: dict[str, Chunk] = {hit.id: hit for hit in vector_hits}

        # 3. Re-read BM25-only hits from the source of truth (drops inactive/stale ones).
        bm25_only = [chunk_id for chunk_id, _ in bm25_hits if chunk_id not in candidates]
        if bm25_only:
            start = time.perf_counter()
            for chunk in self.store.get(bm25_only, filters):
                candidates[chunk.id] = chunk
            timings["reread"] = _ms(start)
        if not candidates:
            timings["total"] = _ms(total_start)
            return RetrievalResult(query, [], timings)

        # 4. Order candidates: reranker if configured, else rank fusion of both lists.
        start = time.perf_counter()
        pool = list(candidates.values())
        fused = rank_fusion(
            [hit.id for hit in vector_hits],
            [cid for cid, _ in bm25_hits],
            k=self.rrf_k,
        )
        scores = [fused[chunk.id] for chunk in pool]
        timings["fusion"] = _ms(start)
        rerank_succeeded = False
        if self.reranker is not None and pool:
            start = time.perf_counter()
            try:
                scores = self.reranker.score(query, [chunk.text for chunk in pool])
                rerank_succeeded = True
            except Exception as exc:
                logger.warning("Ranking API failed; using rank fusion order: %r", exc)
            timings["rerank"] = _ms(start)
        rerank_scores = (
            {chunk.id: score for chunk, score in zip(pool, scores, strict=True)}
            if rerank_succeeded
            else {}
        )
        ranked = sorted(zip(pool, scores, strict=True), key=lambda item: -item[1])

        # Pratik's score decay keeps strong passages from the same judgment but gives
        # passages from other judgments a better chance to appear in the final list.
        decay_counts: dict[Any, int] = {}
        decayed = []
        for chunk, score in ranked:
            doc_id = chunk.metadata.get(DOCUMENT_ID, chunk.id)
            seen = decay_counts.get(doc_id, 0)
            decay_counts[doc_id] = seen + 1
            decayed.append((chunk, score * self.doc_decay**seen))
        ranked = sorted(decayed, key=lambda item: -item[1])

        # 5. Best top_k, at most max_per_doc chunks per judgment.
        vector_scores = {hit.id: hit.score for hit in vector_hits}
        bm25_scores = dict(bm25_hits)
        selected: list[RetrievedChunk] = []
        per_doc: dict[Any, int] = {}
        for chunk, score in ranked:
            if self.max_per_doc:
                doc_id = chunk.metadata.get(DOCUMENT_ID, chunk.id)
                if per_doc.get(doc_id, 0) >= self.max_per_doc:
                    continue
                per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
            selected.append(
                RetrievedChunk(
                    id=chunk.id,
                    text=chunk.text,
                    metadata=chunk.metadata,
                    score=score,
                    dense_score=vector_scores.get(chunk.id),
                    bm25_score=bm25_scores.get(chunk.id),
                    rerank_score=rerank_scores.get(chunk.id),
                )
            )
            if len(selected) == top_k:
                break
        timings["total"] = _ms(total_start)
        return RetrievalResult(query, selected, timings)

    def retrieve(self, query: str, **kwargs: Any) -> list[RetrievedChunk]:
        return self.search(query, **kwargs).chunks

    def retrieve_context(self, query: str, **kwargs: Any) -> str:
        return format_context(self.retrieve(query, **kwargs))
