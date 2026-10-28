"""
Comprehensive Tests for Vertex AI Embedder & Fail-Closed Safety
Validates Priority 0 requirements:
- Production fail-closed behavior when Vertex client is unavailable
- Mock embeddings require explicit opt-in
- Vector dimension (768), finite floats (no NaN/Inf), and non-zero norm validation
- Strict 1-to-1 chunk-to-vector mapping (no silent truncation)
- Deterministic text prefixing
"""

import math
import pytest
from unittest.mock import MagicMock, patch

from core.models import ChunkRecord, ChunkMetadata
from embedding.vertex_embedder import VertexEmbedder, build_short_embedding_text


def test_build_short_embedding_text():
    """Verifies prefix structure and concise context assembly."""
    text = build_short_embedding_text(
        content="This is the main judgment text regarding Article 21.",
        case_title="A.K. Gopalan v. State of Madras and Others",
        year=1950,
        judge_opinion="Kania C.J.",
        section_heading="OPINION"
    )
    assert "A.K. Gopalan v. State of Madras (1950)" in text
    assert "Section: OPINION" in text
    assert "Opinion: Kania C.J." in text
    assert "This is the main judgment text" in text


def test_production_fail_closed_when_vertex_unavailable():
    """Verifies that in production mode (allow_mock=False), failure to connect to Vertex raises RuntimeError."""
    with patch("embedding.vertex_embedder.config") as mock_cfg:
        mock_cfg.allow_mock_embeddings = False
        embedder = VertexEmbedder(allow_mock=False)
        # Client will be None because we have no real GCP auth in this test environment
        assert embedder.client is None
        assert embedder.is_mock_active is False

        with pytest.raises(RuntimeError, match="Production embedding generation failed closed"):
            embedder.embed_texts(["Legal text to embed"])


def test_explicit_mock_mode_allowed():
    """Verifies that mock embeddings succeed when explicitly requested."""
    embedder = VertexEmbedder(allow_mock=True)
    assert embedder.is_mock_active is True

    vectors = embedder.embed_texts(["Text A", "Text B"])
    assert len(vectors) == 2
    for vec in vectors:
        assert len(vec) == 768
        # Unit normalized
        norm = math.sqrt(sum(x * x for x in vec))
        assert pytest.approx(norm, 1e-4) == 1.0


def test_reject_dimension_mismatch():
    """Verifies that vectors with dimensions != 768 are rejected."""
    embedder = VertexEmbedder(allow_mock=True)
    wrong_dim_vec = [0.1] * 512
    with pytest.raises(ValueError, match="Vector dimension mismatch"):
        embedder._validate_vector(wrong_dim_vec, "test")


def test_reject_nan_and_infinity():
    """Verifies that NaN and Infinity values in vectors are strictly rejected."""
    embedder = VertexEmbedder(allow_mock=True)
    nan_vec = [0.0] * 768
    nan_vec[0] = float("nan")
    with pytest.raises(ValueError, match="non-finite value"):
        embedder._validate_vector(nan_vec, "test")

    inf_vec = [0.0] * 768
    inf_vec[10] = float("inf")
    with pytest.raises(ValueError, match="non-finite value"):
        embedder._validate_vector(inf_vec, "test")


def test_reject_zero_norm_vector():
    """Verifies that all-zero vectors are strictly rejected."""
    embedder = VertexEmbedder(allow_mock=True)
    zero_vec = [0.0] * 768
    with pytest.raises(ValueError, match="zero or near-zero norm"):
        embedder._validate_vector(zero_vec, "test")


def test_strict_chunk_to_vector_mapping():
    """Verifies that embed_chunks prevents silent truncation if vector count != chunk count."""
    embedder = VertexEmbedder(allow_mock=True)
    chunks = [
        ChunkRecord(
            chunk_id="doc_p1_c1", document_id="doc", page_start=1, page_end=1,
            chunk_index=0, content="Content 1"
        ),
        ChunkRecord(
            chunk_id="doc_p1_c2", document_id="doc", page_start=1, page_end=1,
            chunk_index=1, content="Content 2"
        ),
    ]

    # Mock embed_texts to return only 1 vector for 2 chunks (silent truncation simulation)
    with patch.object(embedder, "embed_texts", return_value=[[0.1] * 768]):
        with pytest.raises(ValueError, match="Embedding count mismatch"):
            embedder.embed_chunks(chunks)


def test_successful_embed_chunks_mapping():
    """Verifies that valid embeddings are attached to ChunkRecords in exact order."""
    embedder = VertexEmbedder(allow_mock=True)
    chunks = [
        ChunkRecord(
            chunk_id="doc_p1_c1", document_id="doc", page_start=1, page_end=1,
            chunk_index=0, content="Content 1"
        ),
        ChunkRecord(
            chunk_id="doc_p1_c2", document_id="doc", page_start=1, page_end=1,
            chunk_index=1, content="Content 2"
        ),
    ]

    embedder.embed_chunks(chunks, case_title="Test v. State", year=2020)
    for c in chunks:
        assert c.embedding is not None
        assert len(c.embedding) == 768
        norm = math.sqrt(sum(x * x for x in c.embedding))
        assert pytest.approx(norm, 1e-4) == 1.0

