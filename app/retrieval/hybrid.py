"""Hybrid retrieval over the whole corpus: pgvector search + AlloyDB BM25, fused, then
reranked with the Vertex AI Ranking API.

1. Vector search (vector database) and BM25 search (AlloyDB) run in parallel, each
   returning candidate_k complete chunks (text, ids, pages). Each query is optimized
   first (app/retrieval/query_expansion.py), and either step falls back to the plain
   question if it fails:
   - vector: the question's embedding is mixed with the embedding of an LLM-written
     pseudo passage (pseudo query embedding);
   - BM25: terms from the top BM25 hits are added to the query and BM25 runs again
     (pseudo-relevance feedback).
2. The two lists are merged and fused (reciprocal rank fusion).
3. If a reranker is configured, every merged candidate is scored against the query
   and reordered by that score. If the Ranking API fails, the fused order is used.
4. Each further chunk from the same judgment has its score multiplied by doc_decay
   (1st x1, 2nd x0.85, 3rd x0.72, ...), so one long judgment cannot fill every slot,
   yet a strongly relevant extra chunk still beats a weak chunk from another
   judgment. The best top_k by that score are returned.
"""

import logging
import re
import time
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.retrieval.bm25_pg import PostgresBM25Index
from app.retrieval.config import load_config
from app.retrieval.db import Database, DatabaseConfig
from app.retrieval.embedder import VertexQueryEmbedder
from app.retrieval.llm import GroqChat
from app.retrieval.models import DOCUMENT_ID, RetrievedChunk
from app.retrieval.pg_store import PgVectorStore, embedding_dimensions, verify_schema
from app.retrieval.query_expansion import GroqPseudoQuery, prf_terms
from app.retrieval.reranker import VertexRanker
from app.retrieval.tokenizer import tokenize

logger = logging.getLogger(__name__)

_NON_ASCII_LETTER = re.compile(r"[^\W\d_a-zA-Z]")


@dataclass
class RetrievalResult:
    query: str
    chunks: list[RetrievedChunk]
    timings_ms: dict[str, float] = field(default_factory=dict)
    # For inspection only (derived from the question, so never logged).
    pseudo_passage: str | None = None
    expansion_terms: dict[str, float] = field(default_factory=dict)


def rank_fusion(*rankings: Sequence[str], k: int = 60) -> dict[str, float]:
    """Reciprocal rank fusion: score = sum of 1 / (k + rank) over the lists a chunk is in."""
    scores: dict[str, float] = {}
    for ids in rankings:
        for rank, chunk_id in enumerate(ids, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


def _has_content(query: str) -> bool:
    # Stopword-only English queries carry no signal for either retriever; non-Latin
    # text yields no BM25 tokens but can still be embedded.
    return bool(tokenize(query)) or bool(_NON_ASCII_LETTER.search(query))


def _ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


class HybridRetriever:
    def __init__(
        self,
        store: PgVectorStore,
        bm25: PostgresBM25Index,
        embedder: Any,
        reranker: Any | None = None,
        *,
        top_k: int,
        candidate_k: int,
        doc_decay: float,
        rrf_k: int,
        pseudo_query: Any | None = None,
        pseudo_weight: float = 0.5,
        prf_docs: int = 0,
        prf_terms: int = 10,
        prf_weight: float = 0.3,
        bm25_workers: int = 4,
        databases: Sequence[Database] = (),
    ) -> None:
        self.store = store
        self.bm25 = bm25
        self.embedder = embedder
        self.reranker = reranker
        self.top_k = top_k
        self.candidate_k = candidate_k
        if not 0 < doc_decay <= 1:
            raise ValueError(f"doc_decay must be in (0, 1], got {doc_decay}")
        self.doc_decay = doc_decay
        self.rrf_k = rrf_k
        if not 0 <= pseudo_weight <= 1:
            raise ValueError(f"pseudo_weight must be in [0, 1], got {pseudo_weight}")
        self.pseudo_query = pseudo_query
        self.pseudo_weight = pseudo_weight
        self.prf_docs = prf_docs  # 0 = no pseudo-relevance feedback
        self.prf_terms = prf_terms
        self.prf_weight = prf_weight
        # Vector search runs on the caller's thread; BM25 and the pseudo passage are
        # produced here in parallel. Sized to the BM25 connection pool so concurrent
        # requests do not queue behind each other.
        self._executor = ThreadPoolExecutor(max_workers=bm25_workers, thread_name_prefix="bm25")
        self._llm_executor = ThreadPoolExecutor(max_workers=bm25_workers, thread_name_prefix="pseudo")
        self._databases = list(databases)

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> "HybridRetriever":
        """Connect to both databases from config.yaml and check their schemas. Fails
        fast at startup."""
        config = config or load_config()
        embedding = config["embedding"]
        vector_search = config["vector_search"]
        retrieval = config["retrieval"]
        databases: list[Database] = []
        try:
            vector_db = Database(DatabaseConfig(**config["databases"]["vector"]), "vector database")
            databases.append(vector_db)
            verify_schema(vector_db)
            store = PgVectorStore(
                vector_db,
                metric=vector_search["metric"],
                ef_search=vector_search.get("ef_search"),
            )
            embedder = VertexQueryEmbedder(
                embedding["model"],
                embedding["project"],
                embedding["location"],
                normalize=embedding["normalize"],
                # Query vectors must fit the column (e.g. vector(768)).
                expected_dimensions=embedding_dimensions(vector_db),
            )

            bm25_db = Database(DatabaseConfig(**config["databases"]["bm25"]), "BM25 database")
            databases.append(bm25_db)
            bm25 = PostgresBM25Index(
                bm25_db, config["bm25"]["schema"], config["bm25"]["postings_per_term"]
            )
        except Exception:
            for database in databases:
                database.close()
            raise

        rerank = config["reranker"]
        reranker = (
            VertexRanker(rerank["project"], rerank["location"], rerank["model"], rerank["timeout_seconds"])
            if rerank["enabled"]
            else None
        )
        pseudo, prf = config["query_expansion"]["pseudo_query"], config["query_expansion"]["prf"]
        pseudo_query = (
            GroqPseudoQuery(
                GroqChat(
                    pseudo["model"],
                    pseudo["api_key_env"],
                    max_tokens=pseudo["max_tokens"],
                    timeout_seconds=pseudo["timeout_seconds"],
                    reasoning_effort=pseudo.get("reasoning_effort"),
                )
            )
            if pseudo["enabled"]
            else None
        )
        return cls(
            store,
            bm25,
            embedder,
            reranker,
            top_k=retrieval["top_k"],
            candidate_k=retrieval["candidate_k"],
            doc_decay=retrieval["doc_decay"],
            rrf_k=retrieval["rrf_k"],
            pseudo_query=pseudo_query,
            pseudo_weight=pseudo["weight"],
            prf_docs=prf["feedback_docs"] if prf["enabled"] else 0,
            prf_terms=prf["expansion_terms"],
            prf_weight=prf["expansion_weight"],
            bm25_workers=bm25_db.config.pool_size,
            databases=databases,
        )

    def warmup(self) -> None:
        """Open the embedding, LLM and ranking clients up front so the first query is fast."""
        self.embedder.warmup()
        if self.pseudo_query is not None:
            self.pseudo_query.warmup()
        if self.reranker is not None:
            self.reranker.warmup()

    def close(self) -> None:
        self._executor.shutdown(wait=False)
        self._llm_executor.shutdown(wait=False)
        for database in self._databases:
            database.close()

    def __enter__(self) -> "HybridRetriever":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _bm25_search(self, query: str, k: int):
        """BM25 hits, timings and the expansion terms used (empty if PRF is off or
        found nothing). If feedback fails, the first-pass hits are returned."""
        timings: dict[str, float] = {}
        start = time.perf_counter()
        weights = {term: 1.0 for term in tokenize(query)}
        hits = self.bm25.search_weighted(weights, k)
        timings["bm25"] = _ms(start)
        if not self.prf_docs or not hits:
            return hits, timings, {}
        start = time.perf_counter()
        try:
            expansion = prf_terms(
                list(weights), hits[: self.prf_docs], self.bm25.idf, num_terms=self.prf_terms
            )
            if expansion:
                expanded = dict(weights)
                expanded.update({term: self.prf_weight * score for term, score in expansion.items()})
                hits = self.bm25.search_weighted(expanded, k)
        except Exception as exc:
            logger.warning("BM25 pseudo-relevance feedback failed; using first-pass hits: %r", exc)
            expansion = {}
        timings["prf"] = _ms(start)
        return hits, timings, expansion

    def _query_vector(self, query: str, pseudo_job: Future | None, timings: dict[str, float]):
        """The question's embedding, mixed with the pseudo passage's document embedding
        when one was produced in time. Returns (vector, passage or None)."""
        start = time.perf_counter()
        vector = self.embedder.embed(query)
        timings["embed"] = _ms(start)
        if pseudo_job is None:
            return vector, None
        start = time.perf_counter()
        try:
            passage = pseudo_job.result(timeout=self.pseudo_query.timeout_seconds)
            timings["pseudo_llm_wait"] = _ms(start)
            start = time.perf_counter()
            pseudo_vector = self.embedder.embed_document(passage)
            timings["pseudo_embed"] = _ms(start)
        except Exception as exc:
            pseudo_job.cancel()
            logger.warning("Pseudo query embedding failed; using the question only: %r", exc)
            return vector, None
        mixed = (1 - self.pseudo_weight) * vector + self.pseudo_weight * pseudo_vector
        return (mixed / max(float(np.linalg.norm(mixed)), 1e-12)).astype(np.float32), passage

    def _rerank(self, query: str, pool: list[RetrievedChunk]) -> list[float] | None:
        """Ranking API scores for `pool`, or None (fused order is kept) if it fails."""
        try:
            return self.reranker.score(query, [chunk.text for chunk in pool])
        except Exception as exc:
            logger.warning("Ranking API failed; using rank fusion order: %r", exc)
            return None

    def search(self, query: str, *, top_k: int | None = None) -> RetrievalResult:
        """Best top_k chunks for the query. `score` is the Ranking API score in [0, 1]
        when reranking succeeded, otherwise the rank fusion score, times the
        same-judgment decay (both are non-negative, so the decay only lowers it)."""
        total_start = time.perf_counter()
        top_k = self.top_k if top_k is None else top_k
        candidate_k = max(self.candidate_k, top_k)
        timings: dict[str, float] = {}
        if top_k <= 0 or not _has_content(query):
            return RetrievalResult(query, [], {"total": _ms(total_start)})

        # 1. BM25 (+ feedback) and the pseudo passage in the background, vector search
        #    on this thread.
        bm25_job = self._executor.submit(self._bm25_search, query, candidate_k)
        pseudo_job = (
            self._llm_executor.submit(self.pseudo_query.generate, query)
            if self.pseudo_query is not None
            else None
        )
        try:
            vector, passage = self._query_vector(query, pseudo_job, timings)
            start = time.perf_counter()
            vector_hits = self.store.search(vector, candidate_k)
            timings["vector"] = _ms(start)
        except BaseException:
            bm25_job.cancel()
            if pseudo_job is not None:
                pseudo_job.cancel()
            raise
        bm25_hits, bm25_timings, expansion = bm25_job.result()
        timings.update(bm25_timings)

        # 2. Merge (each chunk once) and fuse both rankings.
        start = time.perf_counter()
        candidates = {hit.id: hit for hit in bm25_hits}
        candidates.update({hit.id: hit for hit in vector_hits})
        fused = rank_fusion(
            [hit.id for hit in vector_hits], [hit.id for hit in bm25_hits], k=self.rrf_k
        )
        pool = sorted(candidates.values(), key=lambda chunk: -fused[chunk.id])
        scores = [fused[chunk.id] for chunk in pool]
        timings["fusion"] = _ms(start)

        # 3. Rerank every candidate against the query (cross-encoder).
        rerank_scores = None
        if self.reranker is not None and pool:
            start = time.perf_counter()
            rerank_scores = self._rerank(query, pool)
            timings["rerank"] = _ms(start)
            if rerank_scores is not None:
                scores = rerank_scores
        ranked = sorted(zip(pool, scores), key=lambda item: -item[1])  # stable: ties keep fused order

        # 4. Decay further chunks of the same judgment, then take the best top_k.
        per_doc: dict[Any, int] = {}
        decayed = []
        for chunk, score in ranked:
            doc_id = chunk.metadata.get(DOCUMENT_ID, chunk.id)
            seen = per_doc.get(doc_id, 0)
            per_doc[doc_id] = seen + 1
            decayed.append((chunk, score, score * self.doc_decay**seen))
        decayed.sort(key=lambda item: -item[2])

        vector_scores = {hit.id: hit.dense_score for hit in vector_hits}
        bm25_scores = {hit.id: hit.bm25_score for hit in bm25_hits}
        selected = [
            RetrievedChunk(
                id=chunk.id,
                text=chunk.text,
                metadata=chunk.metadata,
                score=final,
                dense_score=vector_scores.get(chunk.id),
                bm25_score=bm25_scores.get(chunk.id),
                fusion_score=fused[chunk.id],
                rerank_score=score if rerank_scores is not None else None,
            )
            for chunk, score, final in decayed[:top_k]
        ]
        timings["total"] = _ms(total_start)
        return RetrievalResult(query, selected, timings, passage, expansion)
