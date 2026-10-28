"""pgvector store over the source-of-truth database (Cloud SQL `scrag`).

The database holds a single `chunks` table (~146k rows) with the vectors in
chunks.embedding vector(768); there are no judgments, source_files or
embedding_models tables. Dense search, the active check and the filters run in one
SQL statement. `get()` re-reads chunks (e.g. BM25 hits) with the same check: text
always comes from chunks.content and ids/pages from the row itself.

"Citable" here = chunks.is_active. Source-file version and judgment consistency
cannot be checked without those tables; add them to _CITABLE if they appear.
"""

from collections.abc import Iterator, Sequence
from typing import Any

import numpy as np
from sqlalchemy import text

from app.retrieval.db import Database
from app.retrieval.filters import Filters
from app.retrieval.models import (
    DOCUMENT_ID,
    IS_ACTIVE,
    PAGE_END,
    PAGE_START,
    SOURCE_DOCUMENT_ID,
    SOURCE_IS_CURRENT,
    SOURCE_PDF_ID,
    Chunk,
    RetrievedChunk,
)
from app.retrieval.sql_filters import filters_to_sql

# metric -> (pgvector operator, distance -> similarity score)
_METRICS = {
    "cosine": ("<=>", lambda d: 1.0 - d),
    "ip": ("<#>", lambda d: -d),  # <#> is the negative inner product
    "l2": ("<->", lambda d: -d),
}

_CHUNK_COLUMNS = """
    c.chunk_uuid, c.content, c.judgment_uuid, c.source_file_uuid,
    c.page_start, c.page_end, c.is_active"""

_CITABLE = "c.is_active"

# No judgment date exists, so date filters are rejected (UnsupportedFilterError).
FILTER_COLUMNS = {
    DOCUMENT_ID: ("c.judgment_uuid", "uuid"),
    SOURCE_PDF_ID: ("c.source_file_uuid", "uuid"),
}


REQUIRED_CHUNK_COLUMNS = (
    "chunk_uuid",
    "judgment_uuid",
    "source_file_uuid",
    "page_start",
    "page_end",
    "content",
    "is_active",
    "embedding",
)


def verify_schema(db: Database) -> None:
    """Fail at startup, with a clear message, if the live schema does not match what
    this module queries (pgvector extension and the chunks columns above)."""
    if not db.fetch_all("SELECT 1 FROM pg_extension WHERE extname = 'vector'"):
        raise RuntimeError("The pgvector extension is not installed (CREATE EXTENSION vector)")
    present = {
        row[0]
        for row in db.fetch_all(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'chunks'"
        )
    }
    missing = [column for column in REQUIRED_CHUNK_COLUMNS if column not in present]
    if missing:
        raise RuntimeError(f"chunks table is missing columns required by retrieval: {missing}")


def embedding_dimensions(db: Database) -> int:
    """Dimension of chunks.embedding, read from the column type (vector(768) -> 768)."""
    rows = db.fetch_all(
        "SELECT atttypmod FROM pg_attribute "
        "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding' AND NOT attisdropped"
    )
    if not rows or rows[0][0] is None or rows[0][0] < 1:
        raise RuntimeError("chunks.embedding is missing or has no fixed dimension")
    return int(rows[0][0])


def _chunk_from_row(row: Any) -> Chunk:
    m = row._mapping
    document_id = str(m["judgment_uuid"])
    metadata = {
        DOCUMENT_ID: document_id,
        SOURCE_PDF_ID: str(m["source_file_uuid"]),
        PAGE_START: m["page_start"],
        PAGE_END: m["page_end"],
        IS_ACTIVE: m["is_active"],
        # Not checkable without source_files; the chunk's own references are used.
        SOURCE_IS_CURRENT: True,
        SOURCE_DOCUMENT_ID: document_id,
    }
    return Chunk(str(m["chunk_uuid"]), m["content"], metadata)


def _vector_literal(vector: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.8g}" for x in np.asarray(vector, dtype=np.float32).ravel()) + "]"


class PgVectorStore:
    def __init__(
        self,
        db: Database,
        *,
        metric: str = "cosine",
        ef_search: int | None = None,
        iterative_scan: str | None = None,
    ) -> None:
        if metric not in _METRICS:
            raise ValueError(f"Unsupported metric {metric!r}; expected one of {sorted(_METRICS)}")
        if iterative_scan not in (None, "strict_order", "relaxed_order"):
            raise ValueError("iterative_scan must be strict_order or relaxed_order")
        self.db = db
        self._operator, self._to_score = _METRICS[metric]
        self._ef_search = ef_search
        self._iterative_scan = iterative_scan

    def search(
        self, query_vector: np.ndarray, k: int, filters: Filters | None = None
    ) -> list[RetrievedChunk]:
        filter_sql, params = filters_to_sql(filters, FILTER_COLUMNS)
        distance = f"c.embedding {self._operator} CAST(:query_vector AS vector)"
        # SQL fragments are allowlisted constants or generated from fixed filter maps.
        sql = (
            f"SELECT {_CHUNK_COLUMNS}, {distance} AS distance "  # noqa: S608  # nosec B608
            "FROM chunks c "
            f"WHERE c.embedding IS NOT NULL AND {_CITABLE}{filter_sql} "
            f"ORDER BY {distance} LIMIT :k"
        )
        params.update(query_vector=_vector_literal(query_vector), k=k)
        with self.db.transaction() as connection:
            # SET LOCAL only lasts for this transaction; values are validated ints/enums.
            if self._ef_search:
                connection.exec_driver_sql(f"SET LOCAL hnsw.ef_search = {int(self._ef_search)}")
            if self._iterative_scan:
                connection.exec_driver_sql(
                    f"SET LOCAL hnsw.iterative_scan = {self._iterative_scan}"
                )
            rows = list(connection.execute(text(sql), params))
        results = []
        for row in rows:
            chunk = _chunk_from_row(row)
            score = float(self._to_score(float(row._mapping["distance"])))
            results.append(
                RetrievedChunk(chunk.id, chunk.text, chunk.metadata, score=score, dense_score=score)
            )
        return results

    def get(self, ids: Sequence[str], filters: Filters | None = None) -> list[Chunk]:
        if not ids:
            return []
        filter_sql, params = filters_to_sql(filters, FILTER_COLUMNS)
        params["ids"] = [str(i) for i in ids]
        query = (
            f"SELECT {_CHUNK_COLUMNS} FROM chunks c "  # noqa: S608  # nosec B608
            f"WHERE c.chunk_uuid = ANY(CAST(:ids AS uuid[])) AND {_CITABLE}{filter_sql}"
        )
        rows = self.db.fetch_all(query, params)
        by_id = {chunk.id: chunk for chunk in map(_chunk_from_row, rows)}
        return [by_id[i] for i in ids if i in by_id]

    def iter_chunks(self, batch_size: int = 2000) -> Iterator[Chunk]:
        """Every active chunk, in chunk_uuid order (used to build the in-memory BM25
        index at startup). Each batch is its own short query."""
        after = "00000000-0000-0000-0000-000000000000"
        while True:
            query = (
                f"SELECT {_CHUNK_COLUMNS} FROM chunks c "  # noqa: S608  # nosec B608
                f"WHERE {_CITABLE} AND c.chunk_uuid > CAST(:after AS uuid) "
                "ORDER BY c.chunk_uuid LIMIT :n"
            )
            rows = self.db.fetch_all(query, {"after": after, "n": batch_size})
            if not rows:
                return
            for row in rows:
                yield _chunk_from_row(row)
            after = str(rows[-1]._mapping["chunk_uuid"])
