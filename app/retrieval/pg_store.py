"""pgvector search over the vector database, the source of truth for chunks.

The database holds a `chunks` table with the vectors in chunks.embedding vector(N).
Dense search and the active check run in one SQL statement over the whole corpus;
text comes from chunks.content and ids/pages from the row itself.

"Citable" here = chunks.is_active.
"""

from typing import Any

import numpy as np
from sqlalchemy import text

from app.retrieval.db import Database
from app.retrieval.models import (
    DOCUMENT_ID,
    IS_ACTIVE,
    PAGE_END,
    PAGE_START,
    SOURCE_PDF_ID,
    Chunk,
    RetrievedChunk,
)

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

REQUIRED_CHUNK_COLUMNS = (
    "chunk_uuid", "judgment_uuid", "source_file_uuid", "page_start", "page_end",
    "content", "is_active", "embedding",
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
    metadata = {
        DOCUMENT_ID: str(m["judgment_uuid"]),
        SOURCE_PDF_ID: str(m["source_file_uuid"]),
        PAGE_START: m["page_start"],
        PAGE_END: m["page_end"],
        IS_ACTIVE: m["is_active"],
    }
    return Chunk(str(m["chunk_uuid"]), m["content"], metadata)


def _vector_literal(vector: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.8g}" for x in np.asarray(vector, dtype=np.float32).ravel()) + "]"


class PgVectorStore:
    def __init__(
        self,
        db: Database,
        *,
        metric: str,
        ef_search: int | None = None,
    ) -> None:
        if metric not in _METRICS:
            raise ValueError(f"Unsupported metric {metric!r}; expected one of {sorted(_METRICS)}")
        self.db = db
        self._operator, self._to_score = _METRICS[metric]
        self._ef_search = ef_search

    def search(self, query_vector: np.ndarray, k: int) -> list[RetrievedChunk]:
        """Top-k active chunks by similarity (higher score is better)."""
        distance = f"c.embedding {self._operator} CAST(:query_vector AS vector)"
        sql = f"""
            SELECT {_CHUNK_COLUMNS}, {distance} AS distance
            FROM chunks c
            WHERE c.embedding IS NOT NULL AND {_CITABLE}
            ORDER BY {distance}
            LIMIT :k"""
        params = {"query_vector": _vector_literal(query_vector), "k": k}
        # HNSW returns at most ef_search rows, so it must be at least k.
        ef_search = max(int(self._ef_search or 40), k)  # 40 = pgvector default
        with self.db.transaction() as connection:
            # SET LOCAL only lasts for this transaction; the value is a validated int.
            connection.exec_driver_sql(f"SET LOCAL hnsw.ef_search = {min(ef_search, 1000)}")
            rows = list(connection.execute(text(sql), params))
        results = []
        for row in rows:
            chunk = _chunk_from_row(row)
            score = float(self._to_score(float(row._mapping["distance"])))
            results.append(
                RetrievedChunk(chunk.id, chunk.text, chunk.metadata, score=score, dense_score=score)
            )
        return results
