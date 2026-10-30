"""In-memory BM25 index built at startup from the store's chunks (local runs against
the SQLite export). Same legal tokenizer and Lucene BM25 weighting as the AlloyDB
index, so results are comparable. Production uses bm25_pg.py instead.

Postings are kept as compact numpy arrays (term-sorted, CSC style), so ~146k chunks
need a few hundred MB and one pass over the text (a minute or two at startup).
"""

import logging
import time
from array import array
from collections import Counter
from collections.abc import Iterable
from typing import Any

import numpy as np

from app.retrieval.filters import Filters, matches
from app.retrieval.models import Chunk
from app.retrieval.tokenizer import tokenize

logger = logging.getLogger(__name__)


class InMemoryBM25:
    def __init__(
        self,
        ids: list[str],
        metadata: list[Any],
        vocab: dict[str, int],
        indptr: np.ndarray,
        rows: np.ndarray,
        weights: np.ndarray,
        idf: np.ndarray | None = None,
    ) -> None:
        self._ids = ids
        self._metadata = metadata
        self._vocab = vocab
        self._indptr = indptr
        self._rows = rows
        self._weights = weights
        self._idf = idf if idf is not None else np.zeros(len(vocab), dtype=np.float32)

    def __len__(self) -> int:
        return len(self._ids)

    @classmethod
    def build(cls, chunks: Iterable[Chunk], k1: float = 1.2, b: float = 0.75) -> "InMemoryBM25":
        started = time.perf_counter()
        vocab: dict[str, int] = {}
        ids: list[str] = []
        metadata: list[Any] = []
        term_ids, tfs, rows = array("i"), array("f"), array("i")
        doc_lens = array("i")
        for row, chunk in enumerate(chunks):
            tokens = tokenize(chunk.text)
            ids.append(chunk.id)
            metadata.append(chunk.metadata)
            doc_lens.append(len(tokens))
            for term, tf in Counter(tokens).items():
                term_ids.append(vocab.setdefault(term, len(vocab)))
                tfs.append(tf)
                rows.append(row)

        n = len(ids)
        t = np.frombuffer(term_ids, dtype=np.int32)
        tf = np.frombuffer(tfs, dtype=np.float32)
        r = np.frombuffer(rows, dtype=np.int32)
        if n == 0 or t.size == 0:
            logger.warning("BM25: no chunks or tokens to index; lexical search will be empty")
            return cls(ids, metadata, {}, np.zeros(1, dtype=np.int64), r.copy(), tf.copy())

        lengths = np.frombuffer(doc_lens, dtype=np.int32).astype(np.float32)
        avgdl = float(lengths.mean()) or 1.0
        df = np.bincount(t, minlength=len(vocab)).astype(np.float32)
        idf = np.log1p((n - df + 0.5) / (df + 0.5))  # Lucene variant, always > 0
        weights = idf[t] * tf * (k1 + 1) / (tf + k1 * (1 - b + b * lengths[r] / avgdl))

        order = np.argsort(t, kind="stable")
        indptr = np.zeros(len(vocab) + 1, dtype=np.int64)
        np.cumsum(np.bincount(t, minlength=len(vocab)), out=indptr[1:])
        index = cls(
            ids,
            metadata,
            vocab,
            indptr,
            r[order].copy(),
            weights[order].astype(np.float32),
            idf.astype(np.float32),
        )
        logger.info(
            "Built in-memory BM25: %d chunks, %d terms, %d postings in %.0fs",
            n,
            len(vocab),
            t.size,
            time.perf_counter() - started,
        )
        return index

    def search(self, query: str, k: int, filters: Filters | None = None) -> list[tuple[str, float]]:
        """Top-k (chunk_id, score) among chunks containing at least one query term."""
        return self.search_weighted({term: 1.0 for term in set(tokenize(query))}, k, filters)

    def idf(self, terms: list[str]) -> dict[str, float]:
        return {
            term: float(self._idf[index])
            for term in terms
            if (index := self._vocab.get(term)) is not None
        }

    def search_weighted(
        self, term_weights: dict[str, float], k: int, filters: Filters | None = None
    ) -> list[tuple[str, float]]:
        """Search with per-term query weights for pseudo-relevance feedback."""
        weighted_terms = [
            (self._vocab[term], float(weight))
            for term, weight in term_weights.items()
            if term in self._vocab and weight > 0
        ]
        if not weighted_terms or k <= 0:
            return []
        rows = np.concatenate(
            [self._rows[self._indptr[term] : self._indptr[term + 1]] for term, _ in weighted_terms]
        )
        weights = np.concatenate(
            [
                self._weights[self._indptr[term] : self._indptr[term + 1]] * query_weight
                for term, query_weight in weighted_terms
            ]
        )
        scores = np.bincount(rows, weights=weights, minlength=len(self._ids))
        candidates = np.unique(rows)
        candidate_scores = scores[candidates]
        if filters:
            results = []
            for i in np.argsort(-candidate_scores, kind="stable"):
                row = int(candidates[i])
                if matches(self._metadata[row], filters):
                    results.append((self._ids[row], float(candidate_scores[i])))
                    if len(results) == k:
                        break
            return results
        top = np.argpartition(-candidate_scores, min(k, candidates.size) - 1)[:k]
        top = top[np.lexsort((candidates[top], -candidate_scores[top]))]
        return [(self._ids[int(candidates[i])], float(candidate_scores[i])) for i in top]
