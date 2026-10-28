"""Reranking with the Vertex AI Ranking API (Discovery Engine semantic ranker).

A managed cross-encoder: it reads the question together with each candidate's text
and returns a relevance score in [0, 1]. Needs the Discovery Engine API
(discoveryengine.googleapis.com) enabled on the project and the service account
granted roles/discoveryengine.viewer. Credentials come from Application Default
Credentials, like the query embedder.
"""

import threading
from collections.abc import Sequence

MAX_RECORDS_PER_REQUEST = 200  # Ranking API limit per request


class VertexRanker:
    def __init__(self, project: str, location: str, model: str, timeout_seconds: float) -> None:
        self.project = project
        self.location = location
        self.model = model
        self.timeout_seconds = timeout_seconds
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
        """Relevance score per text, in the same order as `texts`. Larger inputs are
        sent in batches; scores are absolute, so batches are comparable."""
        scores: list[float] = []
        for start in range(0, len(texts), MAX_RECORDS_PER_REQUEST):
            scores.extend(self._score_batch(query, texts[start : start + MAX_RECORDS_PER_REQUEST]))
        return scores

    def _score_batch(self, query: str, texts: Sequence[str]) -> list[float]:
        from google.cloud import discoveryengine_v1

        client = self._client or self._get_client()
        response = client.rank(
            request=discoveryengine_v1.RankRequest(
                ranking_config=client.ranking_config_path(
                    project=self.project,
                    location=self.location,
                    ranking_config="default_ranking_config",
                ),
                model=self.model,
                query=query,
                top_n=len(texts),
                records=[
                    discoveryengine_v1.RankingRecord(id=str(i), content=text)
                    for i, text in enumerate(texts)
                ],
                # We already hold the text; only ids and scores are needed back.
                ignore_record_details_in_response=True,
            ),
            timeout=self.timeout_seconds,
        )
        scores = {record.id: float(record.score) for record in response.records}
        return [scores.get(str(i), 0.0) for i in range(len(texts))]
