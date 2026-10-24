"""Hybrid dense + BM25 retrieval with Reciprocal Rank Fusion and optional reranking."""

import logging
import re
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from app.retrieval.bm25_index import BM25Index
from app.retrieval.bm25_pg import PostgresBM25Index
from app.retrieval.config import RetrievalSettings
from app.retrieval.db import Database
from app.retrieval.embedder import QueryEmbedder
from app.retrieval.filters import Filters
from app.retrieval.models import DOCUMENT_ID, RetrievedChunk, format_citation
from app.retrieval.pg_store import PgVectorStore, load_active_embedding_model
from app.retrieval.reranker import CrossEncoderReranker
from app.retrieval.tokenizer import tokenize
from app.retrieval.vector_store import VectorStore, open_vector_store

logger = logging.getLogger(__name__)

Mode = Literal["hybrid", "dense", "bm25"]
MODES: tuple[str, ...] = ("hybrid", "dense", "bm25")

# embedding_models.provider values whose models are API-served, not loadable locally.
_REMOTE_EMBEDDING_PROVIDERS = {"vertex", "vertexai", "vertex_ai", "google", "gemini", "openai"}


class BM25Searcher(Protocol):
    def search(
        self, query: str, k: int, filters: Filters | None = None
    ) -> list[tuple[str, float]]: ...

_UNSET: Any = object()
_NON_ASCII_LETTER = re.compile(r"[^\W\d_a-zA-Z]")


@dataclass
class RetrievalResult:
    query: str
    mode: str
    chunks: list[RetrievedChunk]
    timings_ms: dict[str, float] = field(default_factory=dict)


def reciprocal_rank_fusion(
    rankings: Mapping[str, Sequence[str]],
    weights: Mapping[str, float] | None = None,
    k: int = 60,
) -> list[tuple[str, float]]:
    """Fuse ranked id lists: score(d) = sum over lists of weight / (k + rank(d)).

    Ties are broken by the best single-list rank, then by first appearance.
    """
    scores: dict[str, float] = {}
    best_rank: dict[str, int] = {}
    for name, ids in rankings.items():
        weight = 1.0 if weights is None else weights.get(name, 1.0)
        for rank, chunk_id in enumerate(ids, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (k + rank)
            best_rank[chunk_id] = min(rank, best_rank.get(chunk_id, rank))
    order = {chunk_id: i for i, chunk_id in enumerate(scores)}
    return sorted(
        scores.items(), key=lambda item: (-item[1], best_rank[item[0]], order[item[0]])
    )


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
        embedder: QueryEmbedder | None = None,
        bm25: "BM25Searcher | None" = None,
        reranker: CrossEncoderReranker | None = None,
        *,
        top_k: int = 8,
        candidate_k: int = 50,
        rrf_k: int = 60,
        dense_weight: float = 1.0,
        bm25_weight: float = 1.0,
        max_per_doc: int | None = None,
        min_score: float | None = None,
        rerank: bool = False,
    ) -> None:
        if embedder is None and bm25 is None:
            raise ValueError("HybridRetriever needs a query embedder, a BM25 index, or both")
        if rerank and reranker is None:
            raise ValueError("rerank=True requires a reranker")
        self.store = store
        self.embedder = embedder
        self.bm25 = bm25
        self.reranker = reranker
        self.top_k = top_k
        self.candidate_k = candidate_k
        self.rrf_k = rrf_k
        self.dense_weight = dense_weight
        self.bm25_weight = bm25_weight
        self.max_per_doc = max_per_doc
        self.min_score = min_score
        self.rerank = rerank
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="retrieval")
        self._databases: list[Database] = []

    @classmethod
    def from_settings(cls, settings: RetrievalSettings | None = None) -> "HybridRetriever":
        settings = settings or RetrievalSettings.from_environment()
        databases: list[Database] = []
        if settings.vector_store == "pgvector":
            source_db = Database(settings.source_db, "source database")
            databases.append(source_db)
            model = load_active_embedding_model(source_db)
            if (model.provider or "").lower() in _REMOTE_EMBEDDING_PROVIDERS:
                raise NotImplementedError(
                    f"Active embedding model {model.model_id!r} is served by {model.provider!r}; "
                    "only local sentence-transformers query embedding is implemented"
                )
            store: VectorStore = PgVectorStore(
                source_db,
                model.uuid,
                embedding_table=settings.pg_embedding_table,
                embedding_column=settings.pg_embedding_column,
                metric=settings.metric,
                sync_status=settings.pg_sync_status,
                ef_search=settings.pg_ef_search,
                iterative_scan=settings.pg_iterative_scan,
            )
            embedder = QueryEmbedder(
                model.model_id,
                normalize=settings.normalize_embeddings,
                query_prefix=settings.query_prefix,
                expected_dimensions=model.dimensions,
            )
        else:
            store = open_vector_store(settings)
            embedder = (
                QueryEmbedder(
                    settings.embedding_model,
                    normalize=settings.normalize_embeddings,
                    query_prefix=settings.query_prefix,
                )
                if settings.embedding_model
                else None
            )

        bm25: BM25Searcher | None = None
        if settings.bm25_backend == "postgres":
            bm25_db = Database(settings.bm25_db, "BM25 database")
            databases.append(bm25_db)
            bm25 = PostgresBM25Index(
                bm25_db, settings.bm25_schema, settings.bm25_postings_per_term
            )
            if isinstance(store, PgVectorStore) and bm25.is_stale(store.source_signature()):
                logger.warning(
                    "BM25 index is older than the source chunks; rebuild it with "
                    "python -m app.retrieval.build_bm25 (results stay correct, recall may drop)"
                )
        elif settings.bm25_backend == "file":
            bm25 = BM25Index.load(settings.bm25_index_path)
        elif settings.bm25_backend != "none":
            raise ValueError(f"Unknown RETRIEVAL_BM25_BACKEND {settings.bm25_backend!r}")

        retriever = cls(
            store,
            embedder=embedder,
            bm25=bm25,
            # Constructed always (the model loads lazily) so callers can opt in per query.
            reranker=CrossEncoderReranker(
                settings.reranker_model, max_length=settings.reranker_max_length
            ),
            top_k=settings.top_k,
            candidate_k=settings.candidate_k,
            rrf_k=settings.rrf_k,
            dense_weight=settings.dense_weight,
            bm25_weight=settings.bm25_weight,
            max_per_doc=settings.max_per_doc,
            min_score=settings.min_score,
            rerank=settings.rerank,
        )
        retriever._databases = databases
        return retriever

    @property
    def default_mode(self) -> str:
        if self.embedder is not None and self.bm25 is not None:
            return "hybrid"
        return "dense" if self.embedder is not None else "bm25"

    def warmup(self) -> None:
        """Load models up front so the first query does not pay for it."""
        if self.embedder is not None:
            self.embedder.warmup()
        if self.rerank and self.reranker is not None:
            self.reranker.warmup()

    def close(self) -> None:
        self._executor.shutdown(wait=False)
        for database in self._databases:
            database.close()

    def __enter__(self) -> "HybridRetriever":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _dense(self, query: str, k: int, filters: Filters | None):
        start = time.perf_counter()
        vector = self.embedder.embed(query)
        embed_ms = _ms(start)
        start = time.perf_counter()
        hits = self.store.search(vector, k, filters)
        return hits, embed_ms, _ms(start)

    def _bm25(self, query: str, k: int, filters: Filters | None):
        start = time.perf_counter()
        hits = self.bm25.search(query, k, filters)
        return hits, _ms(start)

    def search(
        self,
        query: str,
        *,
        mode: Mode | None = None,
        top_k: int | None = None,
        candidate_k: int | None = None,
        filters: Filters | None = None,
        max_per_doc: int | None = _UNSET,
        min_score: float | None = _UNSET,
        rerank: bool | None = None,
    ) -> RetrievalResult:
        """Retrieve chunks for a query.

        The final `score` (and so `min_score`) depends on the pipeline: the
        cross-encoder logit when reranking, otherwise the RRF score in hybrid mode,
        cosine/dot similarity in dense mode, or the raw BM25 score in bm25 mode.
        Pass max_per_doc=None or min_score=None to disable the configured defaults.
        """
        total_start = time.perf_counter()
        mode = mode or self.default_mode
        if mode not in MODES:
            raise ValueError(f"Unknown retrieval mode {mode!r}; expected one of {MODES}")
        if mode in ("hybrid", "dense") and self.embedder is None:
            raise ValueError(f"Mode {mode!r} needs a query embedder (RETRIEVAL_EMBEDDING_MODEL)")
        if mode in ("hybrid", "bm25") and self.bm25 is None:
            raise ValueError(f"Mode {mode!r} needs a BM25 index (RETRIEVAL_BM25_INDEX_PATH)")
        top_k = self.top_k if top_k is None else top_k
        candidate_k = max(self.candidate_k if candidate_k is None else candidate_k, top_k)
        max_per_doc = self.max_per_doc if max_per_doc is _UNSET else max_per_doc
        min_score = self.min_score if min_score is _UNSET else min_score
        rerank = self.rerank if rerank is None else rerank
        if rerank and self.reranker is None:
            raise ValueError("rerank=True requires a reranker")

        timings: dict[str, float] = {}
        if top_k <= 0 or not query.strip() or not _has_content(query):
            timings["total"] = _ms(total_start)
            return RetrievalResult(query, mode, [], timings)

        dense_hits: list[RetrievedChunk] = []
        bm25_hits: list[tuple[str, float]] = []
        if mode == "hybrid":
            dense_future = self._executor.submit(self._dense, query, candidate_k, filters)
            bm25_future = self._executor.submit(self._bm25, query, candidate_k, filters)
            dense_hits, timings["embed"], timings["dense"] = dense_future.result()
            bm25_hits, timings["bm25"] = bm25_future.result()
        elif mode == "dense":
            dense_hits, timings["embed"], timings["dense"] = self._dense(
                query, candidate_k, filters
            )
        else:
            bm25_hits, timings["bm25"] = self._bm25(query, candidate_k, filters)

        start = time.perf_counter()
        if mode == "hybrid":
            ranked = reciprocal_rank_fusion(
                {"dense": [h.id for h in dense_hits], "bm25": [cid for cid, _ in bm25_hits]},
                weights={"dense": self.dense_weight, "bm25": self.bm25_weight},
                k=self.rrf_k,
            )
        elif mode == "dense":
            ranked = [(h.id, h.score) for h in dense_hits]
        else:
            ranked = list(bm25_hits)
        timings["fusion"] = _ms(start)

        texts: dict[str, str] = {h.id: h.text for h in dense_hits}
        metadata: dict[str, Mapping[str, Any]] = {h.id: h.metadata for h in dense_hits}
        dense_scores = {h.id: h.dense_score for h in dense_hits}
        bm25_scores = dict(bm25_hits)

        # Source-of-truth re-read: BM25 only ranks, so every BM25-only candidate is
        # re-read from the store (one query) with the citable checks and filters.
        # Anything stale, deactivated or superseded drops out here.
        missing = [cid for cid, _ in ranked if cid not in texts]
        if missing:
            start = time.perf_counter()
            for chunk in self.store.get(missing, filters):
                texts[chunk.id] = chunk.text
                metadata[chunk.id] = chunk.metadata
            timings["hydrate"] = _ms(start)
            dropped = sum(cid not in texts for cid in missing)
            if dropped:
                logger.info("%d BM25 candidates are no longer citable; index may be stale", dropped)
            ranked = [(cid, score) for cid, score in ranked if cid in texts]

        rerank_scores: dict[str, float] = {}
        if rerank and ranked:
            pool = [cid for cid, _ in ranked[:candidate_k]]
            start = time.perf_counter()
            scores = self.reranker.score(query, [texts[cid] for cid in pool])
            rerank_scores = dict(zip(pool, scores))
            ranked = sorted(rerank_scores.items(), key=lambda item: -item[1])
            timings["rerank"] = _ms(start)

        selected: list[tuple[str, float]] = []
        per_doc: dict[Any, int] = {}
        for chunk_id, score in ranked:
            if min_score is not None and score < min_score:
                continue
            if max_per_doc is not None:
                doc_id = metadata[chunk_id].get(DOCUMENT_ID, chunk_id)
                if per_doc.get(doc_id, 0) >= max_per_doc:
                    continue
                per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
            selected.append((chunk_id, score))
            if len(selected) == top_k:
                break

        chunks = [
            RetrievedChunk(
                id=chunk_id,
                text=texts[chunk_id],
                metadata=metadata[chunk_id],
                score=score,
                dense_score=dense_scores.get(chunk_id),
                bm25_score=bm25_scores.get(chunk_id),
                rerank_score=rerank_scores.get(chunk_id),
            )
            for chunk_id, score in selected
        ]
        timings["total"] = _ms(total_start)
        return RetrievalResult(query, mode, chunks, timings)

    def retrieve(self, query: str, **kwargs: Any) -> list[RetrievedChunk]:
        return self.search(query, **kwargs).chunks

    def retrieve_context(self, query: str, **kwargs: Any) -> str:
        return format_context(self.retrieve(query, **kwargs))
