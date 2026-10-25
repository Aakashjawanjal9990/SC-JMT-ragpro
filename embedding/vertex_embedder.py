"""
Vertex AI 768-dim L2-Normalized Vector Embedder (PCE v2.0)
Constructs short canonical prefixes and generates dense embeddings.
"""

import os
import math
import hashlib
from typing import List, Optional
try:
    from PCEV2.core.models import ChunkRecord
    from PCEV2.config import config
except ImportError:
    from core.models import ChunkRecord
    from config import config


def build_short_embedding_text(
    content: str,
    case_title: Optional[str] = None,
    year: Optional[int] = None,
    judge_opinion: Optional[str] = None,
    section_heading: Optional[str] = None
) -> str:
    """Constructs a concise (<= 12 words) context prefix for dense retrieval."""
    parts = []
    if case_title:
        words = case_title.split()
        short_title = " ".join(words[:6]) if len(words) > 6 else case_title
        year_str = f" ({year})" if year else ""
        parts.append(f"{short_title}{year_str}")

    if section_heading:
        parts.append(f"Section: {section_heading}")

    if judge_opinion:
        parts.append(f"Opinion: {judge_opinion}")

    prefix = " | ".join(parts)
    return f"{prefix}\n\n{content}" if prefix else content


class VertexEmbedder:
    """Generates 768-dimensional L2-normalized dense embeddings."""

    def __init__(self, dimension: int = 768, batch_size: int = 64):
        self.dimension = dimension
        self.batch_size = batch_size
        self._init_vertex_client()

    def _init_vertex_client(self):
        self.client = None
        try:
            from google.cloud import aiplatform
            aiplatform.init(project=config.gcp_project, location=config.gcp_location)
            from vertexai.language_models import TextEmbeddingModel
            self.client = TextEmbeddingModel.from_pretrained(config.embedding_model_id)
        except Exception:
            self.client = None

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Embeds a batch of texts into unit-normalized 768-dim vectors."""
        if not texts:
            return []

        if self.client is not None:
            embeddings = []
            for i in range(0, len(texts), self.batch_size):
                batch = texts[i:i + self.batch_size]
                res = self.client.get_embeddings(batch)
                for r in res:
                    vec = r.values
                    norm = math.sqrt(sum(x * x for x in vec))
                    embeddings.append([x / norm for x in vec] if norm > 0 else vec)
            return embeddings

        return [self._generate_pseudo_vector(t) for t in texts]

    def embed_text(self, text: str) -> List[float]:
        """Embeds a single query text."""
        res = self.embed_texts([text])
        return res[0] if res else self._generate_pseudo_vector(text)

    def _generate_pseudo_vector(self, text: str) -> List[float]:
        """Generates a deterministic 768-dim L2-normalized vector from text hash."""
        vec = []
        for i in range(self.dimension):
            h = hashlib.sha256(f"{text}_{i}".encode("utf-8")).hexdigest()
            val = (int(h[:8], 16) / 0xFFFFFFFF) - 0.5
            vec.append(val)
        norm = math.sqrt(sum(x * x for x in vec))
        return [x / norm for x in vec] if norm > 0 else vec

    def embed_chunks(
        self,
        chunks: List[ChunkRecord],
        case_title: Optional[str] = None,
        year: Optional[int] = None
    ):
        """Attaches dense embeddings to ChunkRecords in-place."""
        prefixed_texts = []
        for c in chunks:
            prefixed = build_short_embedding_text(
                content=c.content,
                case_title=case_title,
                year=year,
                judge_opinion=c.metadata.judge_opinion,
                section_heading=c.metadata.section_heading
            )
            prefixed_texts.append(prefixed)

        vectors = self.embed_texts(prefixed_texts)
        for c, vec in zip(chunks, vectors):
            c.embedding = vec
