"""Chunk types, the canonical chunk-record contract, and citation formatting.

Every vector store adapter returns chunk metadata with these keys, mapped from the
database's own columns:

    document_id         judgment UUID the chunk belongs to
    source_pdf_id       source-file UUID the chunk was extracted from
    page_start/page_end inclusive, one-based physical PDF pages (chunks.page_start/end)
    is_active           chunk is active
    source_is_current   the source file is the judgment's current version
    source_document_id  judgment UUID recorded on the source-file row
    decision_date       ISO date of the judgment, for date-range filters
    case_name           display label for prompt citations only (not filterable)

The chunk's own id is the chunk UUID and its text is chunks.content, unmodified.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

DOCUMENT_ID = "document_id"
SOURCE_PDF_ID = "source_pdf_id"
PAGE_START = "page_start"
PAGE_END = "page_end"
IS_ACTIVE = "is_active"
SOURCE_IS_CURRENT = "source_is_current"
SOURCE_DOCUMENT_ID = "source_document_id"
CASE_NAME = "case_name"
DECISION_DATE = "decision_date"

MAX_PAGES = 100  # backend RetrievedPassage.original_page_numbers max_length


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievedChunk:
    """A chunk with its final score; per-stage scores are kept for debugging."""

    id: str
    text: str
    metadata: Mapping[str, Any]
    score: float
    dense_score: float | None = None
    bm25_score: float | None = None
    rerank_score: float | None = None

    @property
    def document_id(self) -> str | None:
        return self.metadata.get(DOCUMENT_ID)

    @property
    def pages(self) -> list[int]:
        return page_range(self.metadata) or []

    @property
    def citation(self) -> str:
        return format_citation(self.metadata)


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
    """Active chunk of the current source version, whose source file belongs to the
    same judgment. Missing flags count as not citable."""
    return (
        metadata.get(IS_ACTIVE) is True
        and metadata.get(SOURCE_IS_CURRENT) is True
        and metadata.get(DOCUMENT_ID) is not None
        and metadata.get(SOURCE_DOCUMENT_ID) == metadata.get(DOCUMENT_ID)
    )


def format_pages(pages: Sequence[int]) -> str:
    if not pages:
        return "p.?"
    pages = sorted(set(pages))
    if len(pages) == 1:
        return f"p.{pages[0]}"
    if pages == list(range(pages[0], pages[-1] + 1)):
        return f"pp.{pages[0]}-{pages[-1]}"
    return "pp." + ", ".join(str(page) for page in pages)


def format_citation(metadata: Mapping[str, Any]) -> str:
    label = metadata.get(CASE_NAME) or metadata.get(DOCUMENT_ID) or "unknown"
    return f"({label}, {format_pages(page_range(metadata) or [])})"
