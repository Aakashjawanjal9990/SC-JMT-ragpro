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


class VertexQueryEmbedder:
    """Query embedding with a Vertex AI model (e.g. text-embedding-004, 768-d) via the
    google-genai SDK. Uses task type RETRIEVAL_QUERY, the query-side counterpart of the
    RETRIEVAL_DOCUMENT task used for chunks. Credentials come from Application Default
    Credentials (GOOGLE_APPLICATION_CREDENTIALS locally, the service account on Cloud Run).
    """

    def __init__(
        self,
        model_name: str = "text-embedding-004",
        project: str | None = None,
        location: str = "us-central1",
        normalize: bool = True,
        expected_dimensions: int | None = None,
        task_type: str = "RETRIEVAL_QUERY",
    ) -> None:
        if not project:
            raise ValueError("Set GCP_PROJECT for Vertex AI query embedding")
        self.model_name = model_name
        self.project = project
        self.location = location
        self.normalize = normalize
        self.expected_dimensions = expected_dimensions
        self.task_type = task_type
        self._client = None
        self._lock = threading.Lock()

    def _get_client(self):
        with self._lock:
            if self._client is None:
                from google import genai

                self._client = genai.Client(
                    vertexai=True, project=self.project, location=self.location
                )
        return self._client

    def warmup(self) -> None:
        vector = self.embed("warmup")
        if self.expected_dimensions and vector.shape[-1] != self.expected_dimensions:
            raise RuntimeError(
                f"{self.model_name} returned {vector.shape[-1]}-d vectors, "
                f"index expects {self.expected_dimensions}-d"
            )

    def embed(self, query: str) -> np.ndarray:
        from google.genai import types

        response = (self._client or self._get_client()).models.embed_content(
            model=self.model_name,
            contents=query,
            config=types.EmbedContentConfig(
                task_type=self.task_type, output_dimensionality=self.expected_dimensions
            ),
        )
        vector = np.asarray(response.embeddings[0].values, dtype=np.float32)
        if self.normalize:
            vector /= max(float(np.linalg.norm(vector)), 1e-12)
        return vector

    def embed_document(self, text: str) -> np.ndarray:
        """Embedding used for HyDE pseudo-passages, matching the stored document vectors."""
        from google.genai import types

        response = (self._client or self._get_client()).models.embed_content(
            model=self.model_name,
            contents=text,
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_DOCUMENT",
                output_dimensionality=self.expected_dimensions,
            ),
        )
        vector = np.asarray(response.embeddings[0].values, dtype=np.float32)
        if self.normalize:
            vector /= max(float(np.linalg.norm(vector)), 1e-12)
        return vector


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


VERTEX_PROVIDERS = {"vertex", "vertexai", "vertex_ai", "google", "gemini"}


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
    """ "local" (sentence-transformers on CPU), Vertex, or test-only hash vectors."""
    provider = (provider or "local").lower()
    if provider in {"test", "deterministic-test"}:
        return DeterministicTestEmbedder(expected_dimensions or 768, normalize)
    if provider in VERTEX_PROVIDERS:
        return VertexQueryEmbedder(
            model_name,
            project=project,
            location=location,
            normalize=normalize,
            expected_dimensions=expected_dimensions,
        )
    if provider in {"local", "sentence-transformers", "sentence_transformers", "huggingface", "hf"}:
        return QueryEmbedder(
            model_name,
            normalize=normalize,
            query_prefix=query_prefix,
            expected_dimensions=expected_dimensions,
        )
    raise NotImplementedError(f"Query embedding provider {provider!r} is not supported")
