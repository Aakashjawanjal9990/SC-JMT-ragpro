"""Adapter from HybridRetriever to the query backend's `RetrievalPort`.

The backend (backend/app/ports.py) calls `await retrieve(question, filters, limit)`
with a validated `QueryFilters` and expects `RetrievedPassage` objects back. Pass its
`RetrievedPassage` class as `passage_factory` when wiring:

    retrieval = RetrievalPortAdapter.from_settings(RetrievedPassage)
    app = create_app(retrieval=retrieval, ...)

Contract enforced here:
- Supported filters: date_from / date_to (judgment decision date, inclusive) and
  document_ids (judgment UUIDs). Anything else set, including case_name, raises
  UnsupportedFilterError; filters are never silently dropped.
- At most `limit` passages, best first, with a finite score and rank 1..n.
- Only citable chunks: active, current source version, source file of the same
  judgment, valid UUIDs, non-empty text that fits the passage model, and a valid
  one-based page_start..page_end range. Text is returned exactly as stored.
- No candidates -> empty list. Candidates that all fail integrity checks ->
  RetrievalFailure (a data problem, not an empty result). Deadline exceeded ->
  RetrievalTimeoutError. Any other dependency error -> RetrievalFailure.
"""

import asyncio
import logging
import math
import uuid
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any

from app.retrieval.config import RetrievalSettings
from app.retrieval.errors import (
    InvalidFilterValueError,
    RetrievalError,
    RetrievalFailure,
    RetrievalTimeoutError,
    UnsupportedFilterError,
)
from app.retrieval.filters import Filters
from app.retrieval.hybrid import HybridRetriever, RetrievalResult
from app.retrieval.models import (
    DECISION_DATE,
    DOCUMENT_ID,
    SOURCE_PDF_ID,
    RetrievedChunk,
    is_citable,
    page_range,
)

logger = logging.getLogger(__name__)

SUPPORTED_FILTERS = frozenset({"date_from", "date_to", "document_ids"})
MAX_PASSAGE_CHARS = 20_000  # RetrievedPassage.text max_length
MAX_PAGES = 100  # RetrievedPassage.original_page_numbers max_length


def _set_filter_fields(filters: Any) -> dict[str, Any]:
    if filters is None:
        return {}
    if hasattr(filters, "model_dump"):
        values = filters.model_dump()
    else:
        values = dict(vars(filters))
    return {key: value for key, value in values.items() if value not in (None, [], "", ())}


def _canonical_uuid(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def filters_from_query(filters: Any) -> Filters:
    """Translate the backend's QueryFilters into a metadata filter spec, rejecting
    anything that cannot be applied."""
    values = _set_filter_fields(filters)
    for name in values:
        if name not in SUPPORTED_FILTERS:
            raise UnsupportedFilterError(name)

    spec: dict[str, Any] = {}
    date_range: dict[str, str] = {}
    for name, operator in (("date_from", "gte"), ("date_to", "lte")):
        if name in values:
            if not isinstance(values[name], date):
                raise InvalidFilterValueError(name)
            date_range[operator] = values[name].isoformat()
    if date_range:
        spec[DECISION_DATE] = date_range

    if "document_ids" in values:
        ids = [_canonical_uuid(value) for value in values["document_ids"]]
        if any(value is None for value in ids):
            raise InvalidFilterValueError("document_ids")
        spec[DOCUMENT_ID] = ids
    return spec


def passage_fields(chunk: RetrievedChunk, rank: int) -> dict[str, Any] | None:
    """RetrievedPassage fields for a citable chunk, or None if it is not citable."""
    metadata = chunk.metadata
    chunk_id = _canonical_uuid(chunk.id)
    document_id = _canonical_uuid(metadata.get(DOCUMENT_ID))
    source_pdf_id = _canonical_uuid(metadata.get(SOURCE_PDF_ID))
    pages = page_range(metadata)
    if (
        chunk_id is None
        or document_id is None
        or source_pdf_id is None
        or not is_citable(metadata)
        or pages is None
        or len(pages) > MAX_PAGES
        or not chunk.text
        or len(chunk.text) > MAX_PASSAGE_CHARS
    ):
        return None
    return {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "source_pdf_id": source_pdf_id,
        "text": chunk.text,
        "original_page_numbers": pages,
        "score": chunk.score if math.isfinite(chunk.score) else None,
        "rank": rank,
    }


class RetrievalPortAdapter:
    def __init__(
        self,
        retriever: HybridRetriever,
        passage_factory: Callable[..., Any] = dict,
        timeout_seconds: float | None = 10.0,
    ) -> None:
        self._retriever = retriever
        self._passage_factory = passage_factory
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(
        cls, passage_factory: Callable[..., Any], settings: RetrievalSettings | None = None
    ) -> "RetrievalPortAdapter":
        settings = settings or RetrievalSettings.from_environment()
        return cls(
            HybridRetriever.from_settings(settings),
            passage_factory,
            timeout_seconds=settings.timeout_seconds,
        )

    def warmup(self) -> None:
        """Load the embedding (and reranker, if enabled) models at startup and check the
        query vector dimension, so the first request does not pay for it."""
        self._retriever.warmup()

    def close(self) -> None:
        """Release database connections; call on application shutdown."""
        self._retriever.close()

    async def healthcheck(self) -> bool:
        """Check source/BM25 database connections for the backend readiness probe."""
        try:
            return await asyncio.to_thread(self._retriever.healthcheck)
        except Exception:
            return False

    async def retrieve(self, question: str, filters: Any, limit: int) -> Sequence[Any]:
        spec = filters_from_query(filters)
        if limit < 1:
            return []
        try:
            result: RetrievalResult = await asyncio.wait_for(
                asyncio.to_thread(
                    self._retriever.search, question, top_k=limit, filters=spec or None
                ),
                timeout=self._timeout_seconds,
            )
        except RetrievalError:
            raise
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise RetrievalTimeoutError("Retrieval exceeded its deadline") from exc
        except Exception as exc:
            raise RetrievalFailure("Retrieval dependency failed") from exc

        # Stage timings only; the question and passage text are never logged.
        logger.debug("Retrieval mode=%s timings_ms=%s", result.mode, result.timings_ms)
        return passages_from_result(result, limit, self._passage_factory)


def passages_from_result(
    result: RetrievalResult,
    limit: int,
    passage_factory: Callable[..., Any] = dict,
) -> list[Any]:
    """Convert RetrievalResult.chunks into the backend's RetrievedPassage format:
    chunk_id, document_id, source_pdf_id (UUIDs), exact text, original_page_numbers
    (page_start..page_end), score and rank 1..n, at most `limit` items.

    Chunks failing integrity checks are dropped and later ranks close the gap. If
    there were candidates but none survived, that is a data problem, not an empty
    result, so RetrievalFailure is raised. No candidates returns [].
    """
    passages: list[Any] = []
    seen: set[str] = set()
    rejected = 0
    for chunk in result.chunks:
        if len(passages) == limit:
            break
        fields = passage_fields(chunk, rank=len(passages) + 1)
        if fields is None or fields["chunk_id"] in seen:
            rejected += 1
            continue
        seen.add(fields["chunk_id"])
        passages.append(passage_factory(**fields))
    if rejected:
        logger.warning("Dropped %d retrieved chunks that failed integrity checks", rejected)
        if not passages:
            raise RetrievalFailure("All retrieved chunks failed integrity checks")
    return passages

