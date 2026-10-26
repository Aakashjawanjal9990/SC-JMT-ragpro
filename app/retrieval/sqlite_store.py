"""SQLite vector store for running retrieval against the ingestion team's local export
(e.g. output/scj_rag_v2.db: ~146k chunks with 768-d vectors).

The database schema is mapped with ONE SELECT (RETRIEVAL_SQLITE_CHUNKS_SQL) that must
return citable chunks only (active, current source version) with these column aliases:

    chunk_id, document_id, source_pdf_id, page_start, page_end, content, embedding
    optional: decision_date (ISO date text), case_name (citation label for prompts)

`embedding` may be a float32 or float64 BLOB, or a JSON array. All vectors are loaded
into memory once (146k x 768 float32 ~ 450 MB) and searched with numpy; chunk text
is read from SQLite only for the chunks that are returned.
"""

import json
import logging
import sqlite3
from collections.abc import Iterator, Sequence
from typing import Any

import numpy as np

from app.retrieval.filters import Filters, matches
from app.retrieval.models import (
    CASE_NAME,
    DECISION_DATE,
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

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = (
    "chunk_id", "document_id", "source_pdf_id", "page_start", "page_end", "content", "embedding",
)

# Assumes the same table/column names as the Postgres schema; override with
# RETRIEVAL_SQLITE_CHUNKS_SQL if the export differs.
DEFAULT_CHUNKS_SQL = """
    SELECT c.chunk_uuid AS chunk_id, c.judgment_uuid AS document_id,
           c.source_file_uuid AS source_pdf_id, c.page_start, c.page_end,
           c.content, c.embedding
    FROM chunks c
    WHERE c.is_active = 1 AND c.embedding IS NOT NULL"""


def _decode_vector(value: Any, dimensions: int) -> np.ndarray:
    """Decode by exact size: dims*4 bytes = float32 (sqlite-vec, most exporters),
    dims*8 bytes = float64 (numpy default), or a JSON array."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        if raw[:1] == b"[":
            vector = np.asarray(json.loads(raw), dtype=np.float32)
        elif len(raw) == dimensions * 4:
            vector = np.frombuffer(raw, dtype=np.float32)
        elif len(raw) == dimensions * 8:
            vector = np.frombuffer(raw, dtype=np.float64).astype(np.float32)
        else:
            raise ValueError(
                f"Embedding BLOB of {len(raw)} bytes is neither float32 nor float64 x {dimensions}; "
                "check RETRIEVAL_EMBEDDING_DIMENSIONS"
            )
    elif isinstance(value, str):
        vector = np.asarray(json.loads(value), dtype=np.float32)
    else:
        raise ValueError(f"Unsupported embedding storage type: {type(value).__name__}")
    if vector.shape != (dimensions,):
        raise ValueError(f"Embedding has shape {vector.shape}, expected ({dimensions},)")
    return vector


class SqliteVectorStore:
    def __init__(
        self,
        path: str,
        chunks_sql: str = DEFAULT_CHUNKS_SQL,
        metric: str = "cosine",
        dimensions: int = 768,
    ) -> None:
        if metric not in {"cosine", "dot"}:
            raise ValueError(f"Unsupported metric for SqliteVectorStore: {metric!r}")
        self._path = path
        self._sql = chunks_sql.strip().rstrip(";")
        self._metric = metric
        self._ids: list[str] = []
        self._raw_ids: list[Any] = []  # original values, so SQLite type affinity matches
        self._metadata: list[dict[str, Any]] = []
        vectors: list[np.ndarray] = []

        with self._connect() as connection:
            cursor = connection.execute(self._sql)
            columns = [d[0].lower() for d in cursor.description]
            missing = [c for c in REQUIRED_COLUMNS if c not in columns]
            if missing:
                raise ValueError(
                    f"RETRIEVAL_SQLITE_CHUNKS_SQL must return columns {list(REQUIRED_COLUMNS)}; "
                    f"missing {missing}. Got {columns}"
                )
            index = {name: i for i, name in enumerate(columns)}
            while rows := cursor.fetchmany(5000):
                for row in rows:
                    vectors.append(_decode_vector(row[index["embedding"]], dimensions))
                    self._raw_ids.append(row[index["chunk_id"]])
                    self._ids.append(str(row[index["chunk_id"]]))
                    self._metadata.append(self._metadata_from_row(row, index))

        if not vectors:
            logger.warning("SQLite chunks query returned no rows; every search will be empty")
            self._vectors = np.zeros((0, 0), dtype=np.float32)
        else:
            matrix = np.vstack(vectors)
            del vectors
            if metric == "cosine":
                matrix /= np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)
            self._vectors = matrix
        self._row_by_id = {chunk_id: row for row, chunk_id in enumerate(self._ids)}
        if len(self._row_by_id) != len(self._ids):
            raise ValueError("Duplicate chunk_id values returned by RETRIEVAL_SQLITE_CHUNKS_SQL")
        logger.info("Loaded %d chunk vectors (dim %s) from %s", len(self._ids), self.dimensions, path)

    def _connect(self) -> sqlite3.Connection:
        # Read-only; a new connection per call keeps this safe across threads.
        return sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)

    @staticmethod
    def _metadata_from_row(row: Sequence[Any], index: dict[str, int]) -> dict[str, Any]:
        def value(name: str) -> Any:
            return row[index[name]] if name in index else None

        document_id = str(value("document_id"))
        page_start, page_end = value("page_start"), value("page_end")
        return {
            DOCUMENT_ID: document_id,
            SOURCE_PDF_ID: str(value("source_pdf_id")),
            PAGE_START: int(page_start) if page_start is not None else None,
            PAGE_END: int(page_end) if page_end is not None else None,
            # The SELECT is required to return citable chunks only.
            IS_ACTIVE: True,
            SOURCE_IS_CURRENT: True,
            SOURCE_DOCUMENT_ID: document_id,
            DECISION_DATE: str(value("decision_date")) if value("decision_date") else None,
            CASE_NAME: value("case_name"),
        }

    @property
    def dimensions(self) -> int | None:
        return int(self._vectors.shape[1]) if self._vectors.size else None

    def __len__(self) -> int:
        return len(self._ids)

    def _texts(self, rows: Sequence[int]) -> dict[str, str]:
        texts: dict[str, str] = {}
        raw = [self._raw_ids[r] for r in rows]
        with self._connect() as connection:
            for start in range(0, len(raw), 500):  # stay under SQLite's variable limit
                batch = raw[start : start + 500]
                placeholders = ",".join("?" * len(batch))
                for chunk_id, content in connection.execute(
                    f"SELECT chunk_id, content FROM ({self._sql}) WHERE chunk_id IN ({placeholders})",
                    batch,
                ):
                    texts[str(chunk_id)] = content
        return texts

    def search(
        self, query_vector: np.ndarray, k: int, filters: Filters | None = None
    ) -> list[RetrievedChunk]:
        if not self._ids or k <= 0:
            return []
        query = np.asarray(query_vector, dtype=np.float32).ravel()
        if query.shape[0] != self._vectors.shape[1]:
            raise ValueError(
                f"Query vector has {query.shape[0]} dims, stored vectors have {self._vectors.shape[1]}"
            )
        if self._metric == "cosine":
            query = query / max(float(np.linalg.norm(query)), 1e-12)
        scores = self._vectors @ query
        if filters:
            rows = []
            for row in np.argsort(-scores, kind="stable"):
                if matches(self._metadata[row], filters):
                    rows.append(int(row))
                    if len(rows) == k:
                        break
        else:
            top = np.argpartition(-scores, min(k, len(scores)) - 1)[:k]
            rows = [int(r) for r in top[np.argsort(-scores[top], kind="stable")]]
        texts = self._texts(rows)
        return [
            RetrievedChunk(
                id=self._ids[r],
                text=texts[self._ids[r]],
                metadata=self._metadata[r],
                score=float(scores[r]),
                dense_score=float(scores[r]),
            )
            for r in rows
            if self._ids[r] in texts
        ]

    def get(self, ids: Sequence[str], filters: Filters | None = None) -> list[Chunk]:
        rows = [
            self._row_by_id[i]
            for i in ids
            if i in self._row_by_id and matches(self._metadata[self._row_by_id[i]], filters)
        ]
        texts = self._texts(rows)
        return [
            Chunk(self._ids[r], texts[self._ids[r]], self._metadata[r])
            for r in rows
            if self._ids[r] in texts
        ]

    def iter_chunks(self) -> Iterator[Chunk]:
        """Stream every chunk (used to build the in-memory BM25 index at startup)."""
        with self._connect() as connection:
            for chunk_id, content in connection.execute(f"SELECT chunk_id, content FROM ({self._sql})"):
                row = self._row_by_id.get(str(chunk_id))
                if row is not None:
                    yield Chunk(self._ids[row], content or "", self._metadata[row])
