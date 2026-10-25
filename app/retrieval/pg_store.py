"""pgvector store over the source-of-truth database (Cloud SQL `scrag`).

Vectors live in chunks.embedding (vector(768) in the confirmed schema). Dense
search, the citable checks and the filters run in one SQL statement, so only
active chunks of current source files, belonging to the right judgment, can rank.
`get()` re-reads chunks (e.g. BM25 hits) from the same tables with the same checks:
text always comes from chunks.content and ids/pages from the row itself.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from sqlalchemy import text

from app.retrieval.db import Database
from app.retrieval.filters import Filters
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
from app.retrieval.sql_filters import filters_to_sql

# metric -> (pgvector operator, distance -> similarity score)
_METRICS = {
    "cosine": ("<=>", lambda d: 1.0 - d),
    "ip": ("<#>", lambda d: -d),  # <#> is the negative inner product
    "l2": ("<->", lambda d: -d),
}

_CHUNK_COLUMNS = """
    c.chunk_uuid, c.content, c.judgment_uuid, c.source_file_uuid, c.page_start, c.page_end,
    c.is_active, sf.is_current AS source_is_current, sf.judgment_uuid AS source_judgment_uuid,
    j.judgment_date, j.citation_text, j.case_number, j.judgment_id"""

_CHUNK_JOINS = """
    JOIN source_files sf ON sf.source_file_uuid = c.source_file_uuid
    JOIN judgments j ON j.judgment_uuid = c.judgment_uuid"""

_CITABLE = "c.is_active AND sf.is_current AND sf.judgment_uuid = c.judgment_uuid"

FILTER_COLUMNS = {
    DOCUMENT_ID: ("c.judgment_uuid", "uuid"),
    SOURCE_PDF_ID: ("c.source_file_uuid", "uuid"),
    DECISION_DATE: ("j.judgment_date", "date"),
}


@dataclass(frozen=True)
class EmbeddingModelInfo:
    uuid: str
    model_id: str
    provider: str | None
    version: str | None
    dimensions: int | None


def load_active_embedding_model(db: Database) -> EmbeddingModelInfo:
    """The single active row of embedding_models; queries must use this model."""
    rows = db.fetch_all(
        "SELECT embedding_model_uuid, model_id, provider, version, dimensions "
        "FROM embedding_models WHERE is_active"
    )
    if len(rows) != 1:
        raise RuntimeError(
            f"Expected exactly one active embedding model, found {len(rows)}; "
            "refusing to guess which model the stored vectors came from"
        )
    row = rows[0]
    return EmbeddingModelInfo(str(row[0]), row[1], row[2], row[3], row[4])


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
        SOURCE_IS_CURRENT: m["source_is_current"],
        SOURCE_DOCUMENT_ID: str(m["source_judgment_uuid"]),
        DECISION_DATE: m["judgment_date"].isoformat() if m["judgment_date"] else None,
        # Prompt label only; judgments has no case-name column.
        CASE_NAME: m["citation_text"] or m["case_number"] or m["judgment_id"],
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
        sql = f"""
            SELECT {_CHUNK_COLUMNS}, {distance} AS distance
            FROM chunks c {_CHUNK_JOINS}
            WHERE c.embedding IS NOT NULL AND {_CITABLE}{filter_sql}
            ORDER BY {distance}
            LIMIT :k"""
        params.update(query_vector=_vector_literal(query_vector), k=k)
        with self.db.transaction() as connection:
            # SET LOCAL only lasts for this transaction; values are validated ints/enums.
            if self._ef_search:
                connection.exec_driver_sql(f"SET LOCAL hnsw.ef_search = {int(self._ef_search)}")
            if self._iterative_scan:
                connection.exec_driver_sql(f"SET LOCAL hnsw.iterative_scan = {self._iterative_scan}")
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
        rows = self.db.fetch_all(
            f"""SELECT {_CHUNK_COLUMNS} FROM chunks c {_CHUNK_JOINS}
                WHERE c.chunk_uuid = ANY(CAST(:ids AS uuid[])) AND {_CITABLE}{filter_sql}""",
            params,
        )
        by_id = {chunk.id: chunk for chunk in map(_chunk_from_row, rows)}
        return [by_id[i] for i in ids if i in by_id]

    def iter_chunks(self, batch_size: int = 2000) -> Iterator[Chunk]:
        """Every citable chunk in chunk_uuid order (keyset pagination)."""
        after = "00000000-0000-0000-0000-000000000000"
        while True:
            rows = self.db.fetch_all(
                f"""SELECT {_CHUNK_COLUMNS} FROM chunks c {_CHUNK_JOINS}
                    WHERE {_CITABLE} AND c.chunk_uuid > CAST(:after AS uuid)
                    ORDER BY c.chunk_uuid LIMIT :n""",
                {"after": after, "n": batch_size},
            )
            if not rows:
                return
            for row in rows:
                yield _chunk_from_row(row)
            after = str(rows[-1]._mapping["chunk_uuid"])

    def source_signature(self) -> str:
        """Changes whenever the set of citable chunks changes; stored with the BM25
        index to detect staleness."""
        row = self.db.fetch_all(
            f"""SELECT count(*), max(greatest(c.updated_at, sf.updated_at))
                FROM chunks c {_CHUNK_JOINS} WHERE {_CITABLE}"""
        )[0]
        return f"{row[0]}|{row[1].isoformat() if row[1] else ''}"
