"""
Unit Tests for PageChunker Bounds & Noise Filtering
Validates:
- Defect H-01: Paragraphs > 2,000 characters are strictly split into chunks <= max_chunk_chars.
- Defect H-03: Standalone punctuation and noise (< 15 alpha chars) are never emitted as separate chunks.
- Single-page invariant: page_start == page_end for all generated chunks.
"""

import pytest
from chunking.page_chunker import PageChunker, split_legal_sentences, split_large_paragraph


def test_split_legal_sentences_citation_protection():
    """Verifies that citations with dots (v., s., Art., AIR, SCC) are not fractured."""
    text = (
        "In Kesavananda Bharati v. State of Kerala, AIR 1973 SC 1461, the basic structure doctrine was established. "
        "The Court interpreted Art. 368 and s. 21 of the Act."
    )
    sentences = split_legal_sentences(text)
    assert len(sentences) == 2
    assert "Kesavananda Bharati v. State of Kerala, AIR 1973 SC 1461" in sentences[0]
    assert "Art. 368 and s. 21" in sentences[1]


def test_oversized_paragraph_split_within_max_bounds():
    """Verifies that a paragraph exceeding 2,500 characters is split into chunks <= 2,000 chars."""
    chunker = PageChunker(target_chunk_chars=1200, max_chunk_chars=2000, min_chunk_chars=150)
    
    # Construct a 3,500-character legal paragraph
    sentence_unit = (
        "The High Court committed a manifest error of law in disregarding the statutory mandate "
        "laid down under Section 138 of the Negotiable Instruments Act 1881. "
    )
    large_paragraph = sentence_unit * 25  # ~3,500 chars
    assert len(large_paragraph) > 3000

    chunks = chunker.chunk_page(
        document_id="TEST_LARGE_P",
        page_number=5,
        cleaned_text=large_paragraph,
        section_heading="OPINION"
    )

    assert len(chunks) >= 2
    for c in chunks:
        assert len(c.content) <= 2000, f"Chunk exceeded max_chunk_chars: {len(c.content)}"
        assert c.page_start == c.page_end == 5


def test_noise_and_punctuation_chunks_filtered():
    """Verifies that standalone punctuation or single-letter lines do not form independent chunks."""
    chunker = PageChunker(target_chunk_chars=1000, max_chunk_chars=2000, min_chunk_chars=150)
    noisy_page = (
        "This is a legitimate opening paragraph of the judgment discussing the legal issue.\n\n"
        "\"\n\n"
        "'\n\n"
        "J.\n\n"
        "This is the concluding paragraph where the Court dismisses the appeal."
    )

    chunks = chunker.chunk_page(
        document_id="TEST_NOISE",
        page_number=2,
        cleaned_text=noisy_page,
        section_heading="OPINION"
    )

    for c in chunks:
        # Every chunk must have at least 15 alphabetical characters
        alpha_count = sum(1 for ch in c.content if ch.isalpha())
        assert alpha_count >= 15, f"Noise chunk emitted: '{c.content}'"
        assert c.content.strip() not in ('"', "'", "J.")

