"""BM25 index stored in its own Postgres database (AlloyDB).

Tables (in `schema`, default "bm25"):
    meta(key, value)                       tokenizer version, corpus stats, source signature
    chunks(chunk_uuid, judgment_uuid, source_file_uuid, judgment_date, doc_len)
    postings(term, chunk_uuid, weight)     weight = precomputed Lucene BM25 contribution

The index only ranks: it returns chunk_uuids and scores. Text, pages and the
citable checks always come from the source database afterwards, so a stale index
can cost recall but can never put an inactive or superseded chunk in an answer.

Build: `python -m app.retrieval.build_bm25`. It reads citable chunks from the source
DB twice (document frequencies, then weights), loads a staging schema with COPY,
indexes it, then swaps it in atomically. Queries keep working during a rebuild.
"""

import csv
import io
import logging
import math
import re
import time
from collections import Counter
from collections.abc import Iterable, Iterator
from typing import Any

from app.retrieval.db import Database
from app.retrieval.filters import Filters
from app.retrieval.models import DECISION_DATE, DOCUMENT_ID, SOURCE_PDF_ID
from app.retrieval.sql_filters import filters_to_sql
from app.retrieval.tokenizer import TOKENIZER_VERSION, tokenize

logger = logging.getLogger(__name__)

_SCHEMA = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")
_COPY_BATCH_ROWS = 200_000


def _idf(df: int, n: int) -> float:
    return math.log(1 + (n - df + 0.5) / (df + 0.5))  # Lucene variant: always > 0


class PostgresBM25Index:
    def __init__(self, db: Database, schema: str = "bm25", postings_per_term: int = 2000) -> None:
        if not _SCHEMA.match(schema):
            raise ValueError(f"Invalid BM25 schema name: {schema!r}")
        self.db = db
        self.schema = schema
        # Per-term cap on postings read for unfiltered queries (highest weights first).
        # 0 = score every posting exactly.
        self.postings_per_term = postings_per_term
        self._columns = {
            DOCUMENT_ID: ("c.judgment_uuid", "uuid"),
            SOURCE_PDF_ID: ("c.source_file_uuid", "uuid"),
            DECISION_DATE: ("c.judgment_date", "date"),
        }
        self.meta = self._load_meta()

    def _load_meta(self) -> dict[str, str]:
        try:
            rows = self.db.fetch_all(f"SELECT key, value FROM {self.schema}.meta")
        except Exception as exc:
            raise RuntimeError(
                f"No BM25 index in schema {self.schema!r}; run python -m app.retrieval.build_bm25"
            ) from exc
        meta = {key: value for key, value in rows}
        if meta.get("tokenizer_version") != TOKENIZER_VERSION:
            raise RuntimeError(
                f"BM25 index uses tokenizer v{meta.get('tokenizer_version')}, current is "
                f"v{TOKENIZER_VERSION}; rebuild it with python -m app.retrieval.build_bm25"
            )
        return meta

    def __len__(self) -> int:
        return int(self.meta.get("num_chunks", 0))

    def is_stale(self, source_signature: str) -> bool:
        return self.meta.get("source_signature") != source_signature

    def search(self, query: str, k: int, filters: Filters | None = None) -> list[tuple[str, float]]:
        """Top-k (chunk_uuid, score). Only chunks containing a query term can match."""
        terms = sorted(set(tokenize(query)))
        if not terms or k <= 0:
            return []
        s = self.schema
        if filters or not self.postings_per_term:
            filter_sql, params = filters_to_sql(filters, self._columns)
            join = f"JOIN {s}.chunks c ON c.chunk_uuid = p.chunk_uuid" if filter_sql else ""
            sql = f"""
                SELECT p.chunk_uuid, SUM(p.weight) AS score
                FROM {s}.postings p {join}
                WHERE p.term = ANY(CAST(:terms AS text[])){filter_sql}
                GROUP BY p.chunk_uuid
                ORDER BY score DESC, p.chunk_uuid
                LIMIT :k"""
        else:
            params = {"m": self.postings_per_term}
            sql = f"""
                SELECT t.chunk_uuid, SUM(t.weight) AS score
                FROM unnest(CAST(:terms AS text[])) AS q(term)
                CROSS JOIN LATERAL (
                    SELECT p.chunk_uuid, p.weight FROM {s}.postings p
                    WHERE p.term = q.term ORDER BY p.weight DESC LIMIT :m
                ) t
                GROUP BY t.chunk_uuid
                ORDER BY score DESC, t.chunk_uuid
                LIMIT :k"""
        params.update(terms=terms, k=k)
        return [(str(row[0]), float(row[1])) for row in self.db.fetch_all(sql, params)]

    @classmethod
    def build(
        cls,
        source,  # PgVectorStore (or any store with iter_chunks() and source_signature())
        db: Database,
        schema: str = "bm25",
        k1: float = 1.2,
        b: float = 0.75,
    ) -> dict[str, Any]:
        if not _SCHEMA.match(schema):
            raise ValueError(f"Invalid BM25 schema name: {schema!r}")
        started = time.perf_counter()
        signature = source.source_signature()  # taken first: changes during the build mark it stale

        # Pass 1: document frequencies and lengths.
        df: Counter[str] = Counter()
        chunk_rows: list[tuple[Any, ...]] = []
        total_len = 0
        for chunk in source.iter_chunks():
            tokens = tokenize(chunk.text)
            df.update(set(tokens))
            total_len += len(tokens)
            m = chunk.metadata
            chunk_rows.append((chunk.id, m[DOCUMENT_ID], m[SOURCE_PDF_ID], m.get(DECISION_DATE), len(tokens)))
        n = len(chunk_rows)
        if n == 0:
            raise RuntimeError("Source database has no citable chunks to index")
        avgdl = total_len / n or 1.0
        logger.info("Pass 1: %d chunks, %d terms, avgdl %.1f", n, len(df), avgdl)

        staging = f"{schema}_build"
        with db.transaction() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {staging} CASCADE")
            connection.exec_driver_sql(f"CREATE SCHEMA {staging}")
            connection.exec_driver_sql(f"CREATE TABLE {staging}.meta (key text PRIMARY KEY, value text NOT NULL)")
            connection.exec_driver_sql(
                f"""CREATE TABLE {staging}.chunks (
                    chunk_uuid uuid PRIMARY KEY, judgment_uuid uuid NOT NULL,
                    source_file_uuid uuid NOT NULL, judgment_date date, doc_len int NOT NULL)"""
            )
            connection.exec_driver_sql(
                # Logged on purpose: AlloyDB read pools do not see unlogged tables.
                f"CREATE TABLE {staging}.postings "
                "(term text NOT NULL, chunk_uuid uuid NOT NULL, weight real NOT NULL)"
            )
        _copy_rows(db, f"{staging}.chunks", chunk_rows)
        del chunk_rows

        # Pass 2: weights. Terms first seen after pass 1 (content changed mid-build) are skipped.
        def postings() -> Iterator[tuple[str, str, float]]:
            for chunk in source.iter_chunks():
                tokens = tokenize(chunk.text)
                norm = k1 * (1 - b + b * len(tokens) / avgdl)
                for term, tf in Counter(tokens).items():
                    if term in df:
                        yield term, chunk.id, _idf(df[term], n) * tf * (k1 + 1) / (tf + norm)

        num_postings = _copy_rows(db, f"{staging}.postings", postings())
        meta = {
            "tokenizer_version": TOKENIZER_VERSION,
            "num_chunks": str(n),
            "num_terms": str(len(df)),
            "num_postings": str(num_postings),
            "avgdl": f"{avgdl:.4f}",
            "k1": str(k1),
            "b": str(b),
            "source_signature": signature,
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _copy_rows(db, f"{staging}.meta", meta.items())

        with db.transaction() as connection:
            connection.exec_driver_sql("SET LOCAL statement_timeout = 0")
            connection.exec_driver_sql(
                f"CREATE INDEX ON {staging}.postings (term, weight DESC) INCLUDE (chunk_uuid)"
            )
            connection.exec_driver_sql(f"CREATE INDEX ON {staging}.postings (chunk_uuid)")
            connection.exec_driver_sql(f"CREATE INDEX ON {staging}.chunks (judgment_uuid)")
            connection.exec_driver_sql(f"CREATE INDEX ON {staging}.chunks (judgment_date)")
            connection.exec_driver_sql(f"ANALYZE {staging}.postings")
            connection.exec_driver_sql(f"ANALYZE {staging}.chunks")
        with db.transaction() as connection:  # atomic swap
            connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            connection.exec_driver_sql(f"ALTER SCHEMA {staging} RENAME TO {schema}")
        meta["seconds"] = f"{time.perf_counter() - started:.0f}"
        return meta


def _copy_rows(db: Database, table: str, rows: Iterable[tuple[Any, ...]]) -> int:
    """Stream rows into `table` with COPY in batches; returns the row count."""
    count = 0
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def flush() -> None:
        if not buffer.tell():
            return
        buffer.seek(0)
        connection = db.engine.raw_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("SET LOCAL statement_timeout = 0")  # this transaction only
            cursor.execute(f"COPY {table} FROM STDIN WITH (FORMAT csv)", stream=buffer)
            connection.commit()
        finally:
            connection.close()
        buffer.seek(0)
        buffer.truncate()

    for row in rows:
        writer.writerow(["" if value is None else value for value in row])
        count += 1
        if count % _COPY_BATCH_ROWS == 0:
            flush()
    flush()
    return count
