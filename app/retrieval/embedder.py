"""CPU query embedding with the same model, prefix and normalization as indexing."""

import os
import threading

import numpy as np

# The query path is PyTorch-only; skip TensorFlow so a broken TF/Keras install
# elsewhere on the machine cannot break model loading.
os.environ.setdefault("USE_TF", "0")


class QueryEmbedder:
    def __init__(
        self,
        model_name: str,
        normalize: bool = True,
        query_prefix: str = "",
        expected_dimensions: int | None = None,
    ) -> None:
        if not model_name:
            raise ValueError(
                "Set RETRIEVAL_EMBEDDING_MODEL to the model used at indexing time"
            )
        self.model_name = model_name
        self.normalize = normalize
        self.query_prefix = query_prefix
        self.expected_dimensions = expected_dimensions
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self.model_name, device="cpu")
        return self._model

    def warmup(self) -> None:
        """Load the model and check it produces vectors of the indexed dimension."""
        vector = self.embed("warmup")
        if self.expected_dimensions and vector.shape[-1] != self.expected_dimensions:
            raise RuntimeError(
                f"{self.model_name} produces {vector.shape[-1]}-d vectors but the index "
                f"expects {self.expected_dimensions}-d (embedding_models.dimensions)"
            )

    def embed(self, query: str) -> np.ndarray:
        model = self._model or self._load()
        vector = model.encode(
            self.query_prefix + query,
            normalize_embeddings=self.normalize,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(vector, dtype=np.float32)
