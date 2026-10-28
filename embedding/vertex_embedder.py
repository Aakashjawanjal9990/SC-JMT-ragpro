"""
Vertex AI 768-dim L2-Normalized Vector Embedder (PCE v2.0)
Constructs short canonical prefixes and generates dense embeddings.
Guarantees fail-closed behavior in production, strict vector dimension/finite validation,
and explicit mock mode isolation.
"""

import math
import time
import logging
import hashlib
from typing import List, Optional

logger = logging.getLogger(__name__)

try:
    from core.models import ChunkRecord
    from config import config
except ImportError:
    from PCEV2.core.models import ChunkRecord
    from PCEV2.config import config


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
    """Generates 768-dimensional L2-normalized dense embeddings with fail-closed production safety."""

    def __init__(
        self,
        dimension: int = 768,
        batch_size: int = 64,
        allow_mock: Optional[bool] = None,
        max_retries: int = 3
    ):
        self.dimension = dimension
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.allow_mock = allow_mock if allow_mock is not None else getattr(config, "allow_mock_embeddings", False)
        self.client = None
        self.init_error: Optional[str] = None
        self.is_mock_active = False
        self._init_vertex_client()

    def _init_vertex_client(self):
        """Initializes Vertex AI TextEmbeddingModel client."""
        try:
            from google.cloud import aiplatform
            aiplatform.init(project=config.gcp_project, location=config.gcp_location)
            from vertexai.language_models import TextEmbeddingModel
            self.client = TextEmbeddingModel.from_pretrained(config.embedding_model_id)
            self.is_mock_active = False
            logger.info(f"Initialized Vertex AI embedding client for model '{config.embedding_model_id}'.")
        except Exception as e:
            self.client = None
            self.init_error = str(e)
            if self.allow_mock:
                self.is_mock_active = True
                logger.warning(
                    f"Vertex AI initialization failed ({e}). Mock embeddings explicitly enabled (ALLOW_MOCK_EMBEDDINGS=true). "
                    "Mock vectors must never be used in production."
                )
            else:
                logger.error(f"Vertex AI initialization failed ({e}). Mock embeddings disabled (ALLOW_MOCK_EMBEDDINGS=false).")

    def _validate_vector(self, vec: List[float], text_snippet: str = "") -> List[float]:
        """Validates vector dimensions, finiteness, non-zero norm, and applies L2 normalization."""
        if len(vec) != self.dimension:
            raise ValueError(
                f"Vector dimension mismatch: expected {self.dimension}, got {len(vec)} for text '{text_snippet[:30]}'."
            )
        for val in vec:
            if not math.isfinite(val):
                raise ValueError(
                    f"Vector contains non-finite value (NaN or Infinity): {val} for text '{text_snippet[:30]}'."
                )
        norm_sq = sum(x * x for x in vec)
        if norm_sq <= 1e-12:
            raise ValueError(f"Vector has zero or near-zero norm ({norm_sq}): invalid for cosine similarity.")
        norm = math.sqrt(norm_sq)
        return [x / norm for x in vec]

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Embeds a batch of texts into unit-normalized 768-dim vectors."""
        if not texts:
            return []

        if self.client is not None:
            embeddings: List[List[float]] = []
            for i in range(0, len(texts), self.batch_size):
                batch = texts[i:i + self.batch_size]
                res = None
                for attempt in range(1, self.max_retries + 1):
                    try:
                        res = self.client.get_embeddings(batch)
                        break
                    except Exception as e:
                        if attempt == self.max_retries:
                            raise RuntimeError(f"Vertex AI embedding request failed after {self.max_retries} attempts: {e}") from e
                        backoff = 2 ** (attempt - 1)
                        logger.warning(f"Vertex AI embedding retry {attempt}/{self.max_retries} after {backoff}s: {e}")
                        time.sleep(backoff)

                if res is None:
                    raise RuntimeError(f"Failed to retrieve embeddings for batch of size {len(batch)}.")

                if len(res) != len(batch):
                    raise ValueError(f"Returned vector count ({len(res)}) does not match input batch size ({len(batch)}).")

                for text_str, r in zip(batch, res):
                    vec = self._validate_vector(list(r.values), text_snippet=text_str)
                    embeddings.append(vec)

            if len(embeddings) != len(texts):
                raise ValueError(f"Total embeddings count ({len(embeddings)}) does not match texts count ({len(texts)}).")
            return embeddings

        # Fallback handling: strictly check allow_mock
        if not self.allow_mock:
            raise RuntimeError(
                f"Vertex AI client is not available ({self.init_error or 'Not initialized'}). "
                "Production embedding generation failed closed. "
                "Set ALLOW_MOCK_EMBEDDINGS=true only in development or unit tests."
            )

        # Explicit Mock Generation (deterministic SHA-256 for test/dev only)
        mock_embeddings = []
        for t in texts:
            vec = self._generate_pseudo_vector(t)
            mock_embeddings.append(self._validate_vector(vec, text_snippet=t))
        return mock_embeddings

    def embed_text(self, text: str) -> List[float]:
        """Embeds a single query text."""
        res = self.embed_texts([text])
        if not res:
            raise RuntimeError("Embedding returned empty result.")
        return res[0]

    def _generate_pseudo_vector(self, text: str) -> List[float]:
        """Generates a deterministic 768-dim L2-normalized vector from text hash (TEST/DEV ONLY)."""
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
        """Attaches dense embeddings to ChunkRecords in-place with strict 1-to-1 mapping checks."""
        if not chunks:
            return

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
        if len(chunks) != len(vectors):
            raise ValueError(
                f"Embedding count mismatch: received {len(vectors)} vectors for {len(chunks)} chunks. "
                "Silent truncation prevented."
            )

        for c, vec in zip(chunks, vectors):
            c.embedding = vec
