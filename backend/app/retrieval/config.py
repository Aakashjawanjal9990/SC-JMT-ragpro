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
    # Source of truth + dense search: "pgvector" (Cloud SQL, chunks.embedding),
    # "sqlite" (ingestion team's local export, e.g. output/scj_rag_v2.db) or
    # "memory" (exported chunks.jsonl + embeddings.npy).
    vector_store: str = "pgvector"
    source_db: DatabaseConfig = field(default_factory=DatabaseConfig)
    pg_ef_search: int | None = None  # HNSW recall/speed knob (pgvector default 40)
    pg_iterative_scan: str | None = None  # pgvector >= 0.8: relaxed_order keeps filtered k full
    metric: str = "cosine"
    vector_store_path: str | None = None  # "memory" store only
    sqlite_path: str | None = None  # "sqlite" store only
    sqlite_chunks_sql: str | None = None  # SELECT mapping the export's schema; see sqlite_store.py

    # Query embedding (no embedding_models table exists, so always from these settings).
    # Must match the model that
    # embedded the chunks: e.g. local BAAI/bge-base-en-v1.5 (query prefix
    # "Represent this sentence for searching relevant passages: ").
    embedding_model: str | None = None
    embedding_provider: str = "local"  # optional sentence-transformers
    embedding_dimensions: int = 768
    normalize_embeddings: bool = True
    query_prefix: str = ""

    # Lexical search: "native" (pg_textsearch bm25 index on chunks.content in the
    # source database; needs vector_store=pgvector), "memory" (built at startup from
    # the store's chunks; no extra service), "postgres" (index in a separate AlloyDB
    # database, built by bm25_builder/) or "none" (vector search only).
    bm25_backend: str = "memory"
    bm25_index_name: str = "idx_chunks_bm25"  # "native" only
    bm25_db: DatabaseConfig = field(default_factory=DatabaseConfig)
    bm25_schema: str = "bm25"
    bm25_postings_per_term: int = 2000

    # Query expansion settings from Pratik's config.yaml.
    pseudo_query_enabled: bool = True
    pseudo_query_model: str = "openai/gpt-oss-120b"
    pseudo_query_reasoning_effort: str = "low"
    pseudo_query_max_tokens: int = 400
    pseudo_query_timeout_seconds: float = 3.0
    pseudo_query_weight: float = 0.5
    prf_enabled: bool = True
    prf_feedback_docs: int = 10
    prf_expansion_terms: int = 10
    prf_expansion_weight: float = 0.3
    doc_decay: float = 0.85

    # Reranker for the merged candidates: "none" or local "cross-encoder".
    reranker: str = "none"
    reranker_model: str | None = None  # provider default if unset
    reranker_max_length: int = 256  # cross-encoder only

    top_k: int = 8  # chunks returned
    candidate_k: int = 20  # chunks taken from EACH of vector search and BM25 before reranking
    max_per_doc: int | None = 3  # cap per judgment; 0 = no cap
    # Deadline for one retrieve() call from the query backend, including dependencies.
    timeout_seconds: float = 10.0
    # Require chunk/judgment/source ids to be UUIDs (production). Set false for local
    # exports whose ids are not UUIDs.
    require_uuids: bool = True

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
            sqlite_path=_env_str("RETRIEVAL_SQLITE_PATH"),
            sqlite_chunks_sql=_env_str("RETRIEVAL_SQLITE_CHUNKS_SQL"),
            embedding_model=_env_str("RETRIEVAL_EMBEDDING_MODEL"),
            embedding_provider=_env_str(
                "RETRIEVAL_EMBEDDING_PROVIDER", defaults.embedding_provider
            ),
            embedding_dimensions=_env_int(
                "RETRIEVAL_EMBEDDING_DIMENSIONS", defaults.embedding_dimensions
            ),
            normalize_embeddings=_env_bool(
                "RETRIEVAL_NORMALIZE_EMBEDDINGS", defaults.normalize_embeddings
            ),
            # Not stripped: e5/bge prefixes end with a meaningful space or colon.
            query_prefix=os.getenv("RETRIEVAL_QUERY_PREFIX", defaults.query_prefix),
            bm25_backend=_env_str("RETRIEVAL_BM25_BACKEND", defaults.bm25_backend),
            bm25_index_name=_env_str("RETRIEVAL_BM25_INDEX_NAME", defaults.bm25_index_name),
            bm25_db=DatabaseConfig.from_environment("BM25_"),
            bm25_schema=_env_str("RETRIEVAL_BM25_SCHEMA", defaults.bm25_schema),
            bm25_postings_per_term=_env_int(
                "RETRIEVAL_BM25_POSTINGS_PER_TERM", defaults.bm25_postings_per_term
            ),
            pseudo_query_enabled=_env_bool(
                "RETRIEVAL_PSEUDO_QUERY_ENABLED", defaults.pseudo_query_enabled
            ),
            pseudo_query_model=_env_str(
                "RETRIEVAL_PSEUDO_QUERY_MODEL", defaults.pseudo_query_model
            ),
            pseudo_query_reasoning_effort=_env_str(
                "RETRIEVAL_PSEUDO_QUERY_REASONING_EFFORT",
                defaults.pseudo_query_reasoning_effort,
            ),
            pseudo_query_max_tokens=_env_int(
                "RETRIEVAL_PSEUDO_QUERY_MAX_TOKENS", defaults.pseudo_query_max_tokens
            ),
            pseudo_query_timeout_seconds=_env_float(
                "RETRIEVAL_PSEUDO_QUERY_TIMEOUT_SECONDS", defaults.pseudo_query_timeout_seconds
            ),
            pseudo_query_weight=_env_float(
                "RETRIEVAL_PSEUDO_QUERY_WEIGHT", defaults.pseudo_query_weight
            ),
            prf_enabled=_env_bool("RETRIEVAL_PRF_ENABLED", defaults.prf_enabled),
            prf_feedback_docs=_env_int(
                "RETRIEVAL_PRF_FEEDBACK_DOCS", defaults.prf_feedback_docs
            ),
            prf_expansion_terms=_env_int(
                "RETRIEVAL_PRF_EXPANSION_TERMS", defaults.prf_expansion_terms
            ),
            prf_expansion_weight=_env_float(
                "RETRIEVAL_PRF_EXPANSION_WEIGHT", defaults.prf_expansion_weight
            ),
            doc_decay=_env_float("RETRIEVAL_DOC_DECAY", defaults.doc_decay),
            reranker=_env_str("RETRIEVAL_RERANKER", defaults.reranker),
            reranker_model=_env_str("RETRIEVAL_RERANKER_MODEL"),
            reranker_max_length=_env_int(
                "RETRIEVAL_RERANKER_MAX_LENGTH", defaults.reranker_max_length
            ),
            top_k=_env_int("RETRIEVAL_TOP_K", defaults.top_k),
            candidate_k=_env_int("RETRIEVAL_CANDIDATE_K", defaults.candidate_k),
            max_per_doc=_env_int("RETRIEVAL_MAX_PER_DOC", defaults.max_per_doc) or None,
            timeout_seconds=_env_float("RETRIEVAL_TIMEOUT_SECONDS", defaults.timeout_seconds),
            require_uuids=_env_bool("RETRIEVAL_REQUIRE_UUIDS", defaults.require_uuids),
        )
