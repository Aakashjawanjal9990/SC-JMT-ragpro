"""Read-only BM25 search over the index stored in its own Postgres database (AlloyDB).

The index is built and refreshed by a separate cloud job, not by this module. This
module only queries it, and only ranks: it returns chunk_uuids and scores. Text,
pages and the citable checks always come from the source database afterwards, so a
stale index can cost recall but can never put an inactive or superseded chunk in an
answer.

Index contract the build job must produce (schema `RETRIEVAL_BM25_SCHEMA`, default "bm25"):

    meta(key text PRIMARY KEY, value text)
        must contain tokenizer_version = app.retrieval.tokenizer.TOKENIZER_VERSION;
        optional num_chunks and other stats
    chunks(chunk_uuid uuid PRIMARY KEY, judgment_uuid uuid, source_file_uuid uuid,
           judgment_date date, doc_len int)
        one row per citable chunk (used for filters)
    postings(term text, chunk_uuid uuid, weight real)
        weight = BM25 contribution of `term` to that chunk (e.g. Lucene idf * tf-norm);
        PRIMARY KEY (term, chunk_uuid); index on (term, weight DESC) INCLUDE (chunk_uuid)

The reference builder is bm25_builder/ at the repository root (a Cloud Run Job).

Terms MUST be produced by app.retrieval.tokenizer.tokenize(chunks.content), the same
function used on queries here; otherwise legal references like "s.438" will not match.
Swap a rebuilt index in atomically (e.g. build into a staging schema, then rename).
"""

import math
import re
from collections.abc import Mapping

from app.retrieval.db import Database
from app.retrieval.filters import Filters
from app.retrieval.models import DECISION_DATE, DOCUMENT_ID, SOURCE_PDF_ID
from app.retrieval.sql_filters import filters_to_sql
from app.retrieval.tokenizer import TOKENIZER_VERSION, tokenize

_SCHEMA = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")


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
            # Schema is validated against _SCHEMA before any identifier interpolation.
            rows = self.db.fetch_all(
                f"SELECT key, value FROM {self.schema}.meta"  # noqa: S608  # nosec B608
            )
        except Exception as exc:
            raise RuntimeError(
                f"No BM25 index in schema {self.schema!r}; run the BM25 build job first"
            ) from exc
        meta = {key: value for key, value in rows}
        if meta.get("tokenizer_version") != TOKENIZER_VERSION:
            raise RuntimeError(
                f"BM25 index uses tokenizer v{meta.get('tokenizer_version')}, current is "
                f"v{TOKENIZER_VERSION}; rebuild it with the same tokenizer version"
            )
        return meta

    def __len__(self) -> int:
        return int(self.meta.get("num_chunks", 0))

    def search(self, query: str, k: int, filters: Filters | None = None) -> list[tuple[str, float]]:
        """Top-k (chunk_uuid, score). Only chunks containing a query term can match."""
        return self.search_weighted({term: 1.0 for term in set(tokenize(query))}, k, filters)

    def idf(self, terms: list[str]) -> dict[str, float]:
        """Lucene idf values used by pseudo-relevance feedback."""
        terms = sorted(set(terms))
        if not terms:
            return {}
        n = len(self)
        rows = self.db.fetch_all(
            f"SELECT term, df FROM {self.schema}.terms "  # noqa: S608  # nosec B608
            "WHERE term = ANY(CAST(:terms AS text[]))",
            {"terms": terms},
        )
        return {term: math.log(1 + (n - df + 0.5) / (df + 0.5)) for term, df in rows}

    def search_weighted(
        self,
        term_weights: Mapping[str, float],
        k: int,
        filters: Filters | None = None,
    ) -> list[tuple[str, float]]:
        """Search with per-term query weights for pseudo-relevance feedback."""
        terms = sorted(term for term, weight in term_weights.items() if weight > 0)
        if not terms or k <= 0:
            return []
        weights = [float(term_weights[term]) for term in terms]
        s = self.schema
        filter_sql, params = filters_to_sql(filters, self._columns)
        if filters or not self.postings_per_term:
            sql = (
                f"SELECT p.chunk_uuid, SUM(p.weight * q.qw) AS score "  # noqa: S608  # nosec B608
                f"FROM unnest(CAST(:terms AS text[]), CAST(:weights AS float8[])) AS q(term, qw) "
                f"JOIN {s}.postings p ON p.term = q.term "
                f"JOIN {s}.chunks c ON c.chunk_uuid = p.chunk_uuid "
                f"WHERE true{filter_sql} "
                "GROUP BY p.chunk_uuid ORDER BY score DESC, p.chunk_uuid LIMIT :k"
            )
        else:
            params = {"m": self.postings_per_term}
            sql = (
                "SELECT t.chunk_uuid, SUM(t.weight * q.qw) AS score "  # noqa: S608  # nosec B608
                "FROM unnest(CAST(:terms AS text[]), CAST(:weights AS float8[])) AS q(term, qw) "
                "CROSS JOIN LATERAL ("
                f"SELECT p.chunk_uuid, p.weight FROM {s}.postings p "
                "WHERE p.term = q.term ORDER BY p.weight DESC LIMIT :m"
                ") AS t GROUP BY t.chunk_uuid ORDER BY score DESC, t.chunk_uuid LIMIT :k"
            )
        params.update(terms=terms, weights=weights, k=k)
        return [(str(row[0]), float(row[1])) for row in self.db.fetch_all(sql, params)]
