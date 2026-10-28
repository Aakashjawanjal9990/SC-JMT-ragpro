"""Chunk types and the canonical chunk-record contract.

Chunk metadata uses these keys, mapped from the vector database's chunks table:

    document_id         judgment UUID the chunk belongs to (chunks.judgment_uuid)
    source_pdf_id       source-file UUID the chunk was extracted from (chunks.source_file_uuid)
    page_start/page_end inclusive, one-based physical PDF pages (chunks.page_start/end)
    is_active           chunk is active (chunks.is_active)

The chunk's own id is the chunk UUID and its text is chunks.content, unmodified.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

DOCUMENT_ID = "document_id"
SOURCE_PDF_ID = "source_pdf_id"
PAGE_START = "page_start"
PAGE_END = "page_end"
IS_ACTIVE = "is_active"

MAX_PAGES = 100  # backend RetrievedPassage.original_page_numbers max_length


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievedChunk:
    """A chunk with its final score (rerank score, or fusion score without a reranker);
    per-stage scores are kept for debugging."""

    id: str
    text: str
    metadata: Mapping[str, Any]
    score: float
    dense_score: float | None = None
    bm25_score: float | None = None
    fusion_score: float | None = None
    rerank_score: float | None = None


def page_range(metadata: Mapping[str, Any]) -> list[int] | None:
    """One-based pages from the inclusive page_start..page_end range, or None if invalid
    (missing, not integers, below 1, reversed, or more than MAX_PAGES pages)."""
    start, end = metadata.get(PAGE_START), metadata.get(PAGE_END)
    if (
        not isinstance(start, int)
        or not isinstance(end, int)
        or isinstance(start, bool)
        or isinstance(end, bool)
        or start < 1
        or end < start
        or end - start + 1 > MAX_PAGES  # checked before building the list
    ):
        return None
    return list(range(start, end + 1))


def is_citable(metadata: Mapping[str, Any]) -> bool:
    """Active chunk that belongs to a judgment. Missing flags count as not citable."""
    return metadata.get(IS_ACTIVE) is True and metadata.get(DOCUMENT_ID) is not None
