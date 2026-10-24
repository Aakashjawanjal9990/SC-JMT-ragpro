"""Optional CPU cross-encoder reranker."""

import os
import threading
from collections.abc import Sequence

os.environ.setdefault("USE_TF", "0")


class CrossEncoderReranker:
    def __init__(
        self,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        max_length: int = 512,
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
