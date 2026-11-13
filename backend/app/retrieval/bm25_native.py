"""BM25 search through the native pg_textsearch index on the source database (AlloyDB).

The DB team indexes chunks.content directly:

    CREATE EXTENSION pg_textsearch;
    CREATE INDEX idx_chunks_bm25 ON chunks USING bm25 (content) WITH (text_config = english);

Inserts update the index in place, so there is no build job and no separate BM25
database. The query form is `content <@> to_bm25query(text, index_name)`, which
returns the BM25 score negated (ascending order = most relevant first); scores are
flipped back to positive here. The extension has no `bm25_score()` function or `<@`
operator.

The index tokenizes with Postgres' `english` text search config, not
app.retrieval.tokenizer, so the raw question is sent rather than our legal tokens
("s.438" does not exist in this index; "section" and "438" do).

pg_textsearch takes a plain query string with no per-term weights and does not
expose document frequencies to non-superusers, so pseudo-relevance feedback is not
supported: idf() returns {} and HybridRetriever skips expansion.
"""

import re
from collections.abc import Mapping

from app.retrieval.db import Database
from app.retrieval.filters import Filters
from app.retrieval.pg_store import FILTER_COLUMNS
from app.retrieval.sql_filters import filters_to_sql

_INDEX_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class NativeBM25:
    def __init__(self, db: Database, index_name: str = "idx_chunks_bm25") -> None:
        if not _INDEX_NAME.match(index_name):
            raise ValueError(f"Invalid BM25 index name: {index_name!r}")
        self.db = db
        self.index_name = index_name
        self._verify()

    def _verify(self) -> None:
        rows = self.db.fetch_all(
            "SELECT am.amname FROM pg_class i JOIN pg_am am ON am.oid = i.relam "
            "WHERE i.relkind = 'i' AND i.relname = :name",
            {"name": self.index_name},
        )
        if not rows or rows[0][0] != "bm25":
            raise RuntimeError(
                f"No pg_textsearch bm25 index named {self.index_name!r} "
                "(CREATE INDEX ... ON chunks USING bm25 (content))"
            )

    def search(self, query: str, k: int, filters: Filters | None = None) -> list[tuple[str, float]]:
        """Top-k (chunk_uuid, score) among active chunks matching the query."""
        if not query.strip() or k <= 0:
            return []
        filter_sql, params = filters_to_sql(filters, FILTER_COLUMNS)
        # Unfiltered: an index scan ordered by score. Filtered by judgment: the planner
        # reads those judgments' chunks and sorts. The outer filter drops rows sharing
        # no term with the query (score 0), which the sorted path can return.
        sql = (
            "SELECT chunk_uuid, -score FROM ("  # noqa: S608  # nosec B608
            "SELECT c.chunk_uuid, c.content <@> to_bm25query(:query, :index_name) AS score "
            f"FROM chunks c WHERE c.is_active{filter_sql} "
            "ORDER BY score LIMIT :k"  # no tiebreaker: it would stop the index scan
            ") AS t WHERE t.score < 0"
        )
        params.update(query=query, index_name=self.index_name, k=k)
        return [(str(row[0]), float(row[1])) for row in self.db.fetch_all(sql, params)]

    def search_weighted(
        self, term_weights: Mapping[str, float], k: int, filters: Filters | None = None
    ) -> list[tuple[str, float]]:
        """Weights cannot be applied natively; searches the positive-weight terms."""
        terms = sorted(term for term, weight in term_weights.items() if weight > 0)
        return self.search(" ".join(terms), k, filters)

    def idf(self, terms: list[str]) -> dict[str, float]:
        return {}
