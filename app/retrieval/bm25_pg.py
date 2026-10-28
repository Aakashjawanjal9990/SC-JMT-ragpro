"""Read-only BM25 search over the index stored in its own Postgres database (AlloyDB).

The index is built and refreshed by a separate cloud job (bm25_builder/), not by this
module. It stores each chunk's text and pages, so search returns complete chunks,
like the vector search, with no lookup in the vector database.

Index contract the build job must produce (schema `bm25.schema` in config.yaml):

    meta(key text PRIMARY KEY, value text)
        must contain tokenizer_version = app.retrieval.tokenizer.TOKENIZER_VERSION and
        index_format = INDEX_FORMAT; optional num_chunks and other stats
    chunks(chunk_uuid uuid PRIMARY KEY, judgment_uuid uuid, source_file_uuid uuid,
           judgment_date date, page_start int, page_end int, content text, doc_len int)
        one row per active chunk
    postings(term text, chunk_uuid uuid, weight real)
        weight = BM25 contribution of `term` to that chunk (e.g. Lucene idf * tf-norm);
        PRIMARY KEY (term, chunk_uuid); index on (term, weight DESC) INCLUDE (chunk_uuid)
    terms(term text PRIMARY KEY, df int)
        number of chunks containing each term (idf for pseudo-relevance feedback)

Terms MUST be produced by app.retrieval.tokenizer.tokenize(chunks.content), the same
function used on queries here; otherwise legal references like "s.438" will not match.
Swap a rebuilt index in atomically (e.g. build into a staging schema, then rename).
"""

import math
import re
from collections.abc import Iterable, Mapping

from app.retrieval.db import Database
from app.retrieval.models import (
    DOCUMENT_ID,
    IS_ACTIVE,
    PAGE_END,
    PAGE_START,
    SOURCE_PDF_ID,
    RetrievedChunk,
)
from app.retrieval.tokenizer import TOKENIZER_VERSION, tokenize

_SCHEMA = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")

INDEX_FORMAT = "3"  # chunks carries content and pages, plus terms(df); must match bm25_builder/


class PostgresBM25Index:
    def __init__(self, db: Database, schema: str, postings_per_term: int) -> None:
        if not _SCHEMA.match(schema):
            raise ValueError(f"Invalid BM25 schema name: {schema!r}")
        self.db = db
        self.schema = schema
        # Per-term cap on postings read (highest weights first); 0 = score every
        # posting exactly.
        self.postings_per_term = postings_per_term
        self.meta = self._load_meta()

    def _load_meta(self) -> dict[str, str]:
        try:
            rows = self.db.fetch_all(f"SELECT key, value FROM {self.schema}.meta")
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
        if meta.get("index_format") != INDEX_FORMAT:
            raise RuntimeError(
                f"BM25 index format is {meta.get('index_format')!r}, expected {INDEX_FORMAT!r} "
                "(chunk text, pages and term frequencies); rerun the BM25 build job"
            )
        return meta

    def __len__(self) -> int:
        return int(self.meta.get("num_chunks", 0))

    def search(self, query: str, k: int) -> list[RetrievedChunk]:
        """Top-k chunks with text, pages and BM25 score. Only chunks containing a query
        term can match."""
        return self.search_weighted({term: 1.0 for term in tokenize(query)}, k)

    def idf(self, terms: Iterable[str]) -> dict[str, float]:
        """Lucene idf of each term in the index (terms not in the index are left out)."""
        terms = sorted(set(terms))
        if not terms:
            return {}
        n = int(self.meta.get("num_chunks", 0))
        rows = self.db.fetch_all(
            f"SELECT term, df FROM {self.schema}.terms WHERE term = ANY(CAST(:terms AS text[]))",
            {"terms": terms},
        )
        return {term: math.log(1 + (n - df + 0.5) / (df + 0.5)) for term, df in rows}

    def search_weighted(self, term_weights: Mapping[str, float], k: int) -> list[RetrievedChunk]:
        """Like search(), with a weight per query term: a chunk's score is the sum of
        weight * BM25 contribution over the terms it contains."""
        terms = sorted(term for term, weight in term_weights.items() if weight > 0)
        if not terms or k <= 0:
            return []
        weights = [float(term_weights[term]) for term in terms]
        s = self.schema
        query_terms = "unnest(CAST(:terms AS text[]), CAST(:weights AS float8[])) AS q(term, qw)"
        if not self.postings_per_term:
            params = {}
            hits = f"""
                SELECT p.chunk_uuid, SUM(p.weight * q.qw) AS score
                FROM {query_terms}
                JOIN {s}.postings p ON p.term = q.term
                GROUP BY p.chunk_uuid
                ORDER BY score DESC, p.chunk_uuid
                LIMIT :k"""
        else:
            params = {"m": self.postings_per_term}
            hits = f"""
                SELECT t.chunk_uuid, SUM(t.weight * q.qw) AS score
                FROM {query_terms}
                CROSS JOIN LATERAL (
                    SELECT p.chunk_uuid, p.weight FROM {s}.postings p
                    WHERE p.term = q.term ORDER BY p.weight DESC LIMIT :m
                ) t
                GROUP BY t.chunk_uuid
                ORDER BY score DESC, t.chunk_uuid
                LIMIT :k"""
        sql = f"""
            WITH hits AS ({hits})
            SELECT h.chunk_uuid, h.score, c.judgment_uuid, c.source_file_uuid,
                   c.page_start, c.page_end, c.content
            FROM hits h JOIN {s}.chunks c ON c.chunk_uuid = h.chunk_uuid
            ORDER BY h.score DESC, h.chunk_uuid"""
        params.update(terms=terms, weights=weights, k=k)
        results = []
        for row in self.db.fetch_all(sql, params):
            m = row._mapping
            score = float(m["score"])
            metadata = {
                DOCUMENT_ID: str(m["judgment_uuid"]),
                SOURCE_PDF_ID: str(m["source_file_uuid"]),
                PAGE_START: m["page_start"],
                PAGE_END: m["page_end"],
                IS_ACTIVE: True,  # the index holds active chunks only
            }
            results.append(
                RetrievedChunk(str(m["chunk_uuid"]), m["content"], metadata, score=score, bm25_score=score)
            )
        return results
