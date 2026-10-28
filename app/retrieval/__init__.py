"""Retrieval layer: pgvector + AlloyDB BM25 hybrid search over chunked Supreme Court
judgments, combined with reciprocal rank fusion, plus grounded answer generation. The
backend uses AnswerPortAdapter (LLM answer + passages) or RetrievalPortAdapter
(passages only)."""

from app.retrieval.answer_adapter import AnswerPortAdapter
from app.retrieval.config import load_config
from app.retrieval.hybrid import HybridRetriever, RetrievalResult
from app.retrieval.models import RetrievedChunk
from app.retrieval.port_adapter import RetrievalPortAdapter

__all__ = [
    "AnswerPortAdapter",
    "HybridRetriever",
    "RetrievalPortAdapter",
    "RetrievalResult",
    "RetrievedChunk",
    "load_config",
]
