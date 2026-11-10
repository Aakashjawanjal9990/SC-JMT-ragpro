"""CPU query embedding with the same model, prefix and normalization as indexing."""

import hashlib
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
            raise ValueError("Set RETRIEVAL_EMBEDDING_MODEL to the model used at indexing time")
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


class DeterministicTestEmbedder:
    """Hash-based vectors for local wiring tests only; these are not semantic embeddings."""

    def __init__(self, dimensions: int, normalize: bool = True) -> None:
        self.dimensions = dimensions
        self.normalize = normalize

    def warmup(self) -> None:
        self.embed("warmup")

    def embed(self, query: str) -> np.ndarray:
        vector = np.asarray(
            [
                (int(hashlib.sha256(f"{query}_{index}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF)
                - 0.5
                for index in range(self.dimensions)
            ],
            dtype=np.float32,
        )
        if self.normalize:
            vector /= max(float(np.linalg.norm(vector)), 1e-12)
        return vector


def make_query_embedder(
    provider: str | None,
    model_name: str,
    *,
    normalize: bool = True,
    query_prefix: str = "",
    expected_dimensions: int | None = None,
    project: str | None = None,
    location: str = "us-central1",
):
    """Local sentence-transformer or test-only hash-vector embedder."""
    provider = (provider or "local").lower()
    if provider in {"test", "deterministic-test"}:
        return DeterministicTestEmbedder(expected_dimensions or 768, normalize)
    if provider in {"local", "sentence-transformers", "sentence_transformers", "huggingface", "hf"}:
        return QueryEmbedder(
            model_name,
            normalize=normalize,
            query_prefix=query_prefix,
            expected_dimensions=expected_dimensions,
        )
    raise NotImplementedError(f"Query embedding provider {provider!r} is not supported")
