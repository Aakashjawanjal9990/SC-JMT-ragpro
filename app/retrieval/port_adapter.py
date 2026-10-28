"""Adapter from HybridRetriever to the query backend's `RetrievalPort`.

The backend (backend/app/ports.py) calls `await retrieve(question, filters, limit)`
with a validated `QueryFilters` and expects `RetrievedPassage` objects back. Pass its
`RetrievedPassage` class as `passage_factory` when wiring:

    retrieval = RetrievalPortAdapter.from_config(RetrievedPassage)  # reads config.yaml
    retrieval.warmup()   # at startup
    app = create_app(retrieval=retrieval, ...)
    retrieval.close()    # at shutdown

Contract enforced here:
- No filters: retrieval always searches the whole corpus. `filters` is accepted only
  to match the port signature; any field set on it raises UnsupportedFilterError
  (422), so a filter is never silently ignored.
- At most `limit` passages, best first, with a finite score and rank 1..n.
- Only citable chunks: active (chunks.is_active), valid UUIDs, non-empty text that fits
  the passage model, and a valid one-based page_start..page_end range. Text is
  returned exactly as stored.
- No candidates -> empty list. Candidates that all fail integrity checks ->
  RetrievalFailure (a data problem, not an empty result). Deadline exceeded ->
  RetrievalTimeoutError. Any other dependency error -> RetrievalFailure.
"""

import asyncio
import logging
import math
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from app.retrieval.config import load_config
from app.retrieval.errors import (
    RetrievalError,
    RetrievalFailure,
    RetrievalTimeoutError,
    UnsupportedFilterError,
)
from app.retrieval.hybrid import HybridRetriever, RetrievalResult
from app.retrieval.models import (
    DOCUMENT_ID,
    SOURCE_PDF_ID,
    RetrievedChunk,
    is_citable,
    page_range,
)

logger = logging.getLogger(__name__)

MAX_PASSAGE_CHARS = 20_000  # RetrievedPassage.text max_length


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


def reject_filters(filters: Any) -> None:
    """Retrieval takes no filters; raise for the first field set on QueryFilters."""
    for name in _set_filter_fields(filters):
        raise UnsupportedFilterError(name)


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
        timeout_seconds: float | None = None,
    ) -> None:
        self._retriever = retriever
        self._passage_factory = passage_factory
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_config(
        cls, passage_factory: Callable[..., Any], config: dict[str, Any] | None = None
    ) -> "RetrievalPortAdapter":
        config = config or load_config()
        return cls(
            HybridRetriever.from_config(config),
            passage_factory,
            timeout_seconds=config["retrieval"]["timeout_seconds"],
        )

    def warmup(self) -> None:
        """Open the embedding and ranking clients at startup and check the query vector
        dimension, so the first request does not pay for it."""
        self._retriever.warmup()

    def close(self) -> None:
        """Release database connections; call on application shutdown."""
        self._retriever.close()

    async def retrieve(self, question: str, filters: Any, limit: int) -> Sequence[Any]:
        reject_filters(filters)
        if limit < 1:
            return []
        try:
            result: RetrievalResult = await asyncio.wait_for(
                asyncio.to_thread(self._retriever.search, question, top_k=limit),
                timeout=self._timeout_seconds,
            )
        except RetrievalError:
            raise
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise RetrievalTimeoutError("Retrieval exceeded its deadline") from exc
        except Exception as exc:
            raise RetrievalFailure("Retrieval dependency failed") from exc

        # Stage timings only; the question and passage text are never logged.
        logger.debug("Retrieval timings_ms=%s", result.timings_ms)
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
