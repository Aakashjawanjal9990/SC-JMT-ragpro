"""Environment-backed retrieval settings, following the backend's `Settings` pattern.

Database connections use the team's existing variable names for the source DB
(DB_NAME, DB_USER, DB_PASSWORD, INSTANCE_CONNECTION_NAME) and the same names with a
BM25_ prefix for the BM25 database. See .env.example.
"""

import os
from dataclasses import dataclass, field

from app.retrieval.db import DatabaseConfig


def _env_str(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _env_bool(name: str, default: bool) -> bool:
    value = _env_str(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int | None) -> int | None:
    value = _env_str(name)
    return default if value is None else int(value)


def _env_float(name: str, default: float | None) -> float | None:
    value = _env_str(name)
    return default if value is None else float(value)


@dataclass(frozen=True)
class RetrievalSettings:
    # Source of truth + dense search: "pgvector" (Cloud SQL, chunks.embedding) or
    # "memory" (local dev/tests).
    vector_store: str = "pgvector"
    source_db: DatabaseConfig = field(default_factory=DatabaseConfig)
    pg_ef_search: int | None = None  # HNSW recall/speed knob (pgvector default 40)
    pg_iterative_scan: str | None = None  # pgvector >= 0.8: relaxed_order keeps filtered k full
    metric: str = "cosine"
    vector_store_path: str | None = None  # "memory" store only

    # Query embedding. With pgvector the model comes from the active embedding_models
    # row; RETRIEVAL_EMBEDDING_MODEL is only used by the "memory" store.
    embedding_model: str | None = None
    normalize_embeddings: bool = True
    query_prefix: str = ""

    # Lexical search: "postgres" (own BM25 database, AlloyDB), "file" (bm25s on disk), "none".
    # The deployment currently has Cloud SQL only. Enable "postgres" only when
    # a separate BM25 database has been provisioned and configured.
    bm25_backend: str = "none"
    bm25_db: DatabaseConfig = field(default_factory=DatabaseConfig)
    bm25_schema: str = "bm25"
    bm25_postings_per_term: int = 2000
    bm25_index_path: str | None = None  # "file" backend only
    bm25_method: str = "lucene"  # "file" backend only

    # Optional CPU cross-encoder reranker, off by default.
    rerank: bool = False
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    reranker_max_length: int = 512

    top_k: int = 8
    candidate_k: int = 50
    rrf_k: int = 60
    dense_weight: float = 1.0
    bm25_weight: float = 1.0
    max_per_doc: int | None = 3
    min_score: float | None = None
    # Deadline for one retrieve() call from the query backend, including dependencies.
    timeout_seconds: float = 10.0

    @classmethod
    def from_environment(cls) -> "RetrievalSettings":
        defaults = cls()
        return cls(
            vector_store=_env_str("RETRIEVAL_VECTOR_STORE", defaults.vector_store),
            source_db=DatabaseConfig.from_environment(""),
            pg_ef_search=_env_int("RETRIEVAL_PG_EF_SEARCH", None),
            pg_iterative_scan=_env_str("RETRIEVAL_PG_ITERATIVE_SCAN"),
            metric=_env_str("RETRIEVAL_METRIC", defaults.metric),
            vector_store_path=_env_str("RETRIEVAL_VECTOR_STORE_PATH"),
            embedding_model=_env_str("RETRIEVAL_EMBEDDING_MODEL"),
            normalize_embeddings=_env_bool(
                "RETRIEVAL_NORMALIZE_EMBEDDINGS", defaults.normalize_embeddings
            ),
            # Not stripped: e5/bge prefixes end with a meaningful space or colon.
            query_prefix=os.getenv("RETRIEVAL_QUERY_PREFIX", defaults.query_prefix),
            bm25_backend=_env_str("RETRIEVAL_BM25_BACKEND", defaults.bm25_backend),
            bm25_db=DatabaseConfig.from_environment("BM25_"),
            bm25_schema=_env_str("RETRIEVAL_BM25_SCHEMA", defaults.bm25_schema),
            bm25_postings_per_term=_env_int(
                "RETRIEVAL_BM25_POSTINGS_PER_TERM", defaults.bm25_postings_per_term
            ),
            bm25_index_path=_env_str("RETRIEVAL_BM25_INDEX_PATH"),
            bm25_method=_env_str("RETRIEVAL_BM25_METHOD", defaults.bm25_method),
            rerank=_env_bool("RETRIEVAL_RERANK", defaults.rerank),
            reranker_model=_env_str("RETRIEVAL_RERANKER_MODEL", defaults.reranker_model),
            reranker_max_length=_env_int(
                "RETRIEVAL_RERANKER_MAX_LENGTH", defaults.reranker_max_length
            ),
            top_k=_env_int("RETRIEVAL_TOP_K", defaults.top_k),
            candidate_k=_env_int("RETRIEVAL_CANDIDATE_K", defaults.candidate_k),
            rrf_k=_env_int("RETRIEVAL_RRF_K", defaults.rrf_k),
            dense_weight=_env_float("RETRIEVAL_DENSE_WEIGHT", defaults.dense_weight),
            bm25_weight=_env_float("RETRIEVAL_BM25_WEIGHT", defaults.bm25_weight),
            max_per_doc=_env_int("RETRIEVAL_MAX_PER_DOC", defaults.max_per_doc) or None,
            min_score=_env_float("RETRIEVAL_MIN_SCORE", None),
            timeout_seconds=_env_float("RETRIEVAL_TIMEOUT_SECONDS", defaults.timeout_seconds),
        )

