"""Sparse BM25 index over the same chunks (and chunk ids) as the vector store.

Uses bm25s (sparse-matrix scoring) rather than rank_bm25, whose per-query cost is a
Python loop over every chunk and is too slow at ~1M chunks.
"""

import json
import logging
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import bm25s
import numpy as np

from app.retrieval.filters import Filters, matches
from app.retrieval.models import DOCUMENT_ID, Chunk
from app.retrieval.tokenizer import TOKENIZER_VERSION, tokenize

logger = logging.getLogger(__name__)

_MANIFEST = "retrieval_manifest.json"
_CHUNK_IDS = "chunk_ids.json"
_METADATA = "chunk_metadata.jsonl"


class _TokenIdLists(list):
    """Holds compact int32 arrays but yields them as the lists bm25s expects.

    Subclasses list because bm25s type-checks the corpus container. Keeping int32
    arrays instead of Python int lists cuts build memory several-fold at our size.
    """

    def __getitem__(self, index):  # type: ignore[override]
        return super().__getitem__(index).tolist()

    def __iter__(self):
        return (array.tolist() for array in super().__iter__())


class BM25Index:
    def __init__(
        self,
        model: bm25s.BM25,
        chunk_ids: list[str],
        metadata: list[Mapping[str, Any]],
        method: str,
    ) -> None:
        if len(chunk_ids) != len(metadata) or len(chunk_ids) != model.scores["num_docs"]:
            raise ValueError("BM25 index, chunk ids and metadata have different lengths")
        self._model = model
        self._vocab: dict[str, int] = model.vocab_dict
        self._chunk_ids = chunk_ids
        self._metadata = metadata
        self._method = method
        self._row_by_id = {chunk_id: row for row, chunk_id in enumerate(chunk_ids)}
        self._rows_by_doc: dict[str, list[int]] = {}
        for row, item in enumerate(metadata):
            doc_id = item.get(DOCUMENT_ID)
            if doc_id is not None:
                self._rows_by_doc.setdefault(doc_id, []).append(row)

    def __len__(self) -> int:
        return len(self._chunk_ids)

    @classmethod
    def build(
        cls, chunks: Iterable[Chunk], method: str = "lucene", show_progress: bool = False
    ) -> "BM25Index":
        vocab: dict[str, int] = {}
        token_ids: list[np.ndarray] = []
        chunk_ids: list[str] = []
        metadata: list[Mapping[str, Any]] = []
        seen: set[str] = set()
        for chunk in chunks:
            if chunk.id in seen:
                raise ValueError(f"Duplicate chunk id in corpus: {chunk.id!r}")
            seen.add(chunk.id)
            ids = [vocab.setdefault(token, len(vocab)) for token in tokenize(chunk.text)]
            token_ids.append(np.asarray(ids, dtype=np.int32))
            chunk_ids.append(chunk.id)
            metadata.append(dict(chunk.metadata))
        if not chunk_ids:
            raise ValueError("Cannot build a BM25 index from an empty corpus")
        if not vocab:
            raise ValueError("Corpus produced no tokens; cannot build a BM25 index")

        model = bm25s.BM25(method=method)
        model.index(
            (_TokenIdLists(token_ids), vocab),
            create_empty_token=False,
            show_progress=show_progress,
        )
        return cls(model, chunk_ids, metadata, method)

    def _matching_mask(self, token_ids: list[int]) -> np.ndarray:
        """Boolean mask of rows containing at least one query term, from the postings.

        Score > 0 is not a reliable test: Okapi/Robertson IDF is 0 for terms in more
        than half the corpus, but those chunks still contain the term. A mask avoids
        sorting (np.unique) long posting lists for common terms.
        """
        indptr = self._model.scores["indptr"]
        indices = self._model.scores["indices"]
        mask = np.zeros(len(self._chunk_ids), dtype=bool)
        for t in token_ids:
            mask[indices[indptr[t] : indptr[t + 1]]] = True
        return mask

    def search(self, query: str, k: int, filters: Filters | None = None) -> list[tuple[str, float]]:
        """Top-k (chunk_id, score) among chunks sharing a term with the query."""
        token_ids = sorted({self._vocab[t] for t in tokenize(query) if t in self._vocab})
        if not token_ids or k <= 0:
            return []
        mask = self._matching_mask(token_ids)
        doc_filter = filters.get(DOCUMENT_ID) if filters else None
        if isinstance(doc_filter, (str, list, tuple, set)):
            # Fast path: only consider rows of the filtered judgments.
            wanted = [doc_filter] if isinstance(doc_filter, str) else doc_filter
            allowed = np.unique(
                np.asarray(
                    [r for d in wanted for r in self._rows_by_doc.get(d, ())], dtype=np.int64
                )
            )
            rows = allowed[mask[allowed]]
        else:
            rows = np.flatnonzero(mask)
        if rows.size == 0:
            return []

        row_scores = self._model.get_scores_from_ids(token_ids)[rows]
        if filters:
            # Walk rows best-first and check filters lazily, so a broad term does not
            # cost a Python-level filter check on every matching chunk.
            order = np.argsort(-row_scores, kind="stable")
            results: list[tuple[str, float]] = []
            for position in order:
                row = int(rows[position])
                if matches(self._metadata[row], filters):
                    results.append((self._chunk_ids[row], float(row_scores[position])))
                    if len(results) == k:
                        break
            return results

        if rows.size > k:
            top = np.argpartition(-row_scores, k - 1)[:k]
        else:
            top = np.arange(rows.size)
        top = top[np.lexsort((rows[top], -row_scores[top]))]  # score desc, then row for ties
        return [(self._chunk_ids[int(rows[i])], float(row_scores[i])) for i in top]

    def metadata_for(self, chunk_id: str) -> Mapping[str, Any] | None:
        row = self._row_by_id.get(chunk_id)
        return None if row is None else self._metadata[row]

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        self._model.save(str(path))
        (path / _CHUNK_IDS).write_text(json.dumps(self._chunk_ids), encoding="utf-8")
        with (path / _METADATA).open("w", encoding="utf-8") as handle:
            for item in self._metadata:
                handle.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
        manifest = {
            "tokenizer_version": TOKENIZER_VERSION,
            "method": self._method,
            "num_chunks": len(self._chunk_ids),
        }
        (path / _MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path, mmap: bool = True) -> "BM25Index":
        path = Path(path)
        manifest_path = path / _MANIFEST
        if not manifest_path.exists():
            raise FileNotFoundError(f"No BM25 index at {path} (missing {_MANIFEST})")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["tokenizer_version"] != TOKENIZER_VERSION:
            raise ValueError(
                f"BM25 index at {path} was built with tokenizer v{manifest['tokenizer_version']}, "
                f"current is v{TOKENIZER_VERSION}; rebuild it with app.retrieval.build_bm25"
            )
        model = bm25s.BM25.load(str(path), mmap=mmap)
        chunk_ids = json.loads((path / _CHUNK_IDS).read_text(encoding="utf-8"))
        with (path / _METADATA).open(encoding="utf-8") as handle:
            metadata = [json.loads(line) for line in handle]
        logger.info("Loaded BM25 index with %d chunks from %s", len(chunk_ids), path)
        return cls(model, chunk_ids, metadata, manifest["method"])
