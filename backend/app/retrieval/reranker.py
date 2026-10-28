"""Rerankers that score (question, chunk) pairs. Pick one with RETRIEVAL_RERANKER:

none           no reranker; candidates are ordered by rank fusion (default)
cross-encoder  local CPU cross-encoder; needs the optional sentence-transformers
"""

import os
import threading
from collections.abc import Sequence

os.environ.setdefault("USE_TF", "0")


class CrossEncoderReranker:
    def __init__(
        self,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        max_length: int = 256,
        batch_size: int = 32,
    ) -> None:
        self.model_name = model_name
        self.max_length = max_length
        self.batch_size = batch_size
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                from sentence_transformers import CrossEncoder

                self._model = CrossEncoder(
                    self.model_name, max_length=self.max_length, device="cpu"
                )
        return self._model

    def warmup(self) -> None:
        self.score("warmup", ["warmup"])

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        model = self._model or self._load()
        scores = model.predict(
            [(query, text) for text in texts],
            batch_size=self.batch_size,
            show_progress_bar=False,
        )
        return [float(s) for s in scores]


def make_reranker(
    provider: str | None,
    *,
    model_name: str | None = None,
    max_length: int = 256,
    project: str | None = None,
    location: str = "global",
):
    """Reranker for RETRIEVAL_RERANKER, or None for "none"."""
    provider = (provider or "none").lower()
    if provider == "none":
        return None
    if provider == "cross-encoder":
        return CrossEncoderReranker(
            model_name or "cross-encoder/ms-marco-MiniLM-L-6-v2", max_length=max_length
        )
    raise ValueError(f"Unknown RETRIEVAL_RERANKER {provider!r}; use none or cross-encoder")
