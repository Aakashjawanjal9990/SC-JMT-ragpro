"""Query embedding with the Vertex AI model that embedded chunks.embedding."""

import threading

import numpy as np


class VertexQueryEmbedder:
    """Query embedding with a Vertex AI model (e.g. text-embedding-004) via the
    google-genai SDK. Uses task type RETRIEVAL_QUERY, the query-side counterpart of the
    RETRIEVAL_DOCUMENT task used for chunks. Credentials come from Application Default
    Credentials (the service account on Cloud Run).
    """

    def __init__(
        self,
        model_name: str,
        project: str,
        location: str,
        *,
        normalize: bool = True,
        expected_dimensions: int | None = None,
        task_type: str = "RETRIEVAL_QUERY",
    ) -> None:
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

                self._client = genai.Client(vertexai=True, project=self.project, location=self.location)
        return self._client

    def warmup(self) -> None:
        """Open the client and check the model returns vectors of the indexed dimension."""
        vector = self.embed("warmup")
        if self.expected_dimensions and vector.shape[-1] != self.expected_dimensions:
            raise RuntimeError(
                f"{self.model_name} returned {vector.shape[-1]}-d vectors, "
                f"index expects {self.expected_dimensions}-d"
            )

    def embed(self, query: str) -> np.ndarray:
        return self._embed(query, self.task_type)

    def embed_document(self, text: str) -> np.ndarray:
        """Embed text as a document (RETRIEVAL_DOCUMENT, like the chunks), e.g. a
        pseudo passage written to look like a judgment."""
        return self._embed(text, "RETRIEVAL_DOCUMENT")

    def _embed(self, text: str, task_type: str) -> np.ndarray:
        from google.genai import types

        response = (self._client or self._get_client()).models.embed_content(
            model=self.model_name,
            contents=text,
            config=types.EmbedContentConfig(
                task_type=task_type, output_dimensionality=self.expected_dimensions
            ),
        )
        vector = np.asarray(response.embeddings[0].values, dtype=np.float32)
        if self.normalize:
            vector /= max(float(np.linalg.norm(vector)), 1e-12)
        return vector
