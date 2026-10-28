"""
Unit and Regression Test Suite for PCE-PAGE-001 Closure.
Guarantees that substantive PDF text is never lost and pages with raw text
are never falsely classified as 'verified_blank' or routed to 'skip_blank'.
"""

import pytest
from pathlib import Path
import fitz

from parsing.zone_extractor import CoordinateZoneExtractor
from local_ocr.router import LocalOCRRouter, PageRoutingDecision
from chunking.page_chunker import PageChunker
from parsing.structure_tracker import LawReportStructureTracker


FIXTURE_PATH = Path("tests/fixtures/2026_1_183_190_EN.pdf")


def test_pce_page_001_raw_text_pages_never_verified_blank():
    """
    Acceptance Criterion 1 & 2 for PCE-PAGE-001:
    - Raw text that is non-empty must not become 'verified_blank'.
    - Pages 2 and 3 of 2026_1_183_190_EN.pdf retain their substantive text.
    """
    assert FIXTURE_PATH.exists(), f"Missing fixture at {FIXTURE_PATH}"
    doc = fitz.open(FIXTURE_PATH)
    assert len(doc) == 3

    # --- Test Page 2 ---
    pz_p2 = CoordinateZoneExtractor.extract_page_zones(
        page=doc[1],
        page_number=2,
        total_pages=3,
        start_book_page=183,
        document_id="2026_1_183_190_EN"
    )
    assert pz_p2.extraction_method != "verified_blank", "Page 2 must NOT be marked verified_blank"
    assert len(pz_p2.cleaned_text.strip()) > 50, f"Page 2 text was truncated: {pz_p2.cleaned_text}"
    assert "Leave granted" in pz_p2.cleaned_text, "Page 2 must retain 'Leave granted'"
    assert "Section 138" in pz_p2.cleaned_text, "Page 2 must retain 'Section 138'"
    assert pz_p2.ocr_quality >= 0.85, f"Expected high quality score, got {pz_p2.ocr_quality}"

    # --- Test Page 3 ---
    pz_p3 = CoordinateZoneExtractor.extract_page_zones(
        page=doc[2],
        page_number=3,
        total_pages=3,
        start_book_page=183,
        document_id="2026_1_183_190_EN"
    )
    assert pz_p3.extraction_method != "verified_blank", "Page 3 must NOT be marked verified_blank"
    assert len(pz_p3.cleaned_text.strip()) > 30, f"Page 3 text was truncated: {pz_p3.cleaned_text}"
    assert "appeal is accordingly allowed" in pz_p3.cleaned_text, "Page 3 must retain 'appeal is accordingly allowed'"
    assert pz_p3.ocr_quality >= 0.85, f"Expected high quality score, got {pz_p3.ocr_quality}"


def test_pce_page_001_router_routing_decision_not_skip_blank():
    """
    Guarantees that LocalOCRRouter does not route pages with raw text to SKIP_BLANK.
    """
    router = LocalOCRRouter()
    doc = fitz.open(FIXTURE_PATH)

    eval_p2 = router.evaluate_page(doc[1], page_number=2, total_pages=3, document_id="2026_1_183_190_EN")
    assert eval_p2.decision != PageRoutingDecision.SKIP_BLANK, "Router must not route Page 2 to SKIP_BLANK"
    assert eval_p2.total_clean_chars > 50

    eval_p3 = router.evaluate_page(doc[2], page_number=3, total_pages=3, document_id="2026_1_183_190_EN")
    assert eval_p3.decision != PageRoutingDecision.SKIP_BLANK, "Router must not route Page 3 to SKIP_BLANK"
    assert eval_p3.total_clean_chars > 30


def test_pce_page_001_downstream_chunks_produced():
    """
    Guarantees that downstream chunker produces valid chunks for both pages
    adhering strictly to Rule R2 (page_start == page_end).
    """
    doc = fitz.open(FIXTURE_PATH)
    tracker = LawReportStructureTracker(bench_judges=["Sanjay Kumar", "Alok Aradhe"])
    chunker = PageChunker()

    pz_p2 = CoordinateZoneExtractor.extract_page_zones(doc[1], 2, 3, 183, "2026_1_183_190_EN")
    chunks_p2 = chunker.chunk_page(
        document_id="2026_1_183_190_EN",
        page_number=2,
        cleaned_text=pz_p2.cleaned_text,
        structure_tracker=tracker
    )
    assert len(chunks_p2) >= 1, "Page 2 must produce at least 1 chunk"
    for c in chunks_p2:
        assert c.page_start == 2 and c.page_end == 2
        assert "Leave granted" in c.content

    pz_p3 = CoordinateZoneExtractor.extract_page_zones(doc[2], 3, 3, 183, "2026_1_183_190_EN")
    chunks_p3 = chunker.chunk_page(
        document_id="2026_1_183_190_EN",
        page_number=3,
        cleaned_text=pz_p3.cleaned_text,
        structure_tracker=tracker
    )
    assert len(chunks_p3) >= 1, "Page 3 must produce at least 1 chunk"
    for c in chunks_p3:
        assert c.page_start == 3 and c.page_end == 3
        assert "appeal is accordingly allowed" in c.content
