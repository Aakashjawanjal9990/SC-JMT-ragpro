"""Rerankers that score (question, chunk) pairs. Pick one with RETRIEVAL_RERANKER:

none           no reranker; candidates are ordered by rank fusion (default)
vertex         Vertex AI Ranking API (Discovery Engine), managed, no local model
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


class VertexRanker:
    """Vertex AI Ranking API (google-cloud-discoveryengine). Requires the Discovery
    Engine API enabled on the project and roles/discoveryengine.viewer (or user)."""

    def __init__(
        self,
        project: str | None,
        model_name: str = "semantic-ranker-default@latest",
        location: str = "global",
    ) -> None:
        if not project:
            raise ValueError("Set GCP_PROJECT for the Vertex AI Ranking API")
        self.project = project
        self.model_name = model_name
        self.location = location
        self._client = None
        self._lock = threading.Lock()

    def _get_client(self):
        with self._lock:
            if self._client is None:
                from google.cloud import discoveryengine_v1

                self._client = discoveryengine_v1.RankServiceClient()
        return self._client

    def warmup(self) -> None:
        self.score("warmup", ["warmup"])

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        from google.cloud import discoveryengine_v1

        client = self._client or self._get_client()
        response = client.rank(
            request=discoveryengine_v1.RankRequest(
                ranking_config=client.ranking_config_path(
                    project=self.project,
                    location=self.location,
                    ranking_config="default_ranking_config",
                ),
                model=self.model_name,
                query=query,
                top_n=len(texts),
                records=[
                    discoveryengine_v1.RankingRecord(id=str(i), content=text)
                    for i, text in enumerate(texts)
                ],
            )
        )
        scores = {record.id: float(record.score) for record in response.records}
        return [scores.get(str(i), 0.0) for i in range(len(texts))]


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
    if provider == "vertex":
        return VertexRanker(project, model_name or "semantic-ranker-default@latest", location)
    if provider == "cross-encoder":
        return CrossEncoderReranker(
            model_name or "cross-encoder/ms-marco-MiniLM-L-6-v2", max_length=max_length
        )
    raise ValueError(f"Unknown RETRIEVAL_RERANKER {provider!r}; use none, vertex or cross-encoder")
