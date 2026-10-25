"""
Comprehensive Unit & Regression Tests for PCE v2.0 Pipeline
Tests all core invariants: single-page chunks, zero DDL, margin zoning, headnote isolation, and embeddings.
"""

import re
import pytest
from pathlib import Path
import fitz

from PCEV2.core.models import ChunkRecord, ChunkMetadata
from PCEV2.parsing.zone_extractor import CoordinateZoneExtractor
from PCEV2.parsing.case_boundaries import CaseBoundaryDetector
from PCEV2.parsing.metadata_extractor import LegalMetadataExtractor
from PCEV2.parsing.structure_tracker import LawReportStructureTracker
from PCEV2.chunking.page_chunker import PageChunker
from PCEV2.pipeline import IngestionPipeline
from PCEV2.config import config


def test_zone_extractor_margins_1950():
    """Verifies that 1950 margin notes are cleanly isolated into metadata."""
    pdf_path = Path("tests/fixtures/1950_1_15_25_EN.pdf")
    doc = fitz.open(pdf_path)

    # Page 3: Central I9l50 Excises test
    pz3 = CoordinateZoneExtractor.extract_page_zones(
        page=doc[2],
        page_number=3,
        total_pages=len(doc),
        start_book_page=15,
        document_id="1950_1_15_25_EN"
    )
    # Body text must have zero margin notes
    assert "I9l50" not in pz3.cleaned_text
    assert re.search(r"Central\s+Excises\s+and\s+Salt\s+Act", pz3.cleaned_text)
    # Margin notes must be preserved in metadata
    assert any("I9l50" in m for m in pz3.margin_notes)
    doc.close()


def test_case_boundaries_shared_page():
    """Verifies that shared boundary pages are partitioned without cross-contamination."""
    # Simulating shared page 25 containing end of Ram Krishna and start of Bhawanipore
    shared_sample = (
        "This was an appeal from Nagpur. Appeal dismissed.\n"
        "Agent for the appellants: Rajinder Narain.\n"
        "BHAWANIPORE BANKING CORPORATION, LTD.\n"
        "v.\n"
        "Gouri Shankar Sharma\n"
        "[SHRI HARILAL KANIA C.J., SAIYID FAZL ALI JJ.]\n"
        "Headnotes: Limitation Act s. 48..."
    )

    # Outgoing document (Ram Krishna)
    rk_part, is_rk_sh = CaseBoundaryDetector.split_shared_page(shared_sample, is_end_of_doc=True)
    assert is_rk_sh is True
    assert "BHAWANIPORE" not in rk_part
    assert "Appeal dismissed." in rk_part

    # Incoming document (Bhawanipore)
    bh_part, is_bh_sh = CaseBoundaryDetector.split_shared_page(shared_sample, is_end_of_doc=False)
    assert is_bh_sh is True
    assert "Rajinder Narain" not in bh_part
    assert "BHAWANIPORE BANKING CORPORATION" in bh_part


def test_metadata_extractor_born_digital():
    """Verifies deterministic metadata extraction from modern born-digital judgments."""
    pdf_path = Path("tests/fixtures/2026_1_183_190_EN.pdf")
    doc = fitz.open(pdf_path)
    head_text = "\n".join(doc[i].get_text() for i in range(min(3, len(doc))))

    meta = LegalMetadataExtractor.extract_metadata(head_text)
    assert meta.scr_citation == "[2026] 1 S.C.R. 183"
    assert meta.case_number == "Criminal Appeal No. 75 of 2026"
    assert meta.date_of_judgment == "06 January 2026"
    assert "S. Nagesh" in (meta.appellant or "")
    assert "Shobha S. Aradhya" in (meta.respondent or "")
    # Author judge extracted from asterisk '*'
    assert meta.author == "Sanjay Kumar"
    assert meta.judges == ["Sanjay Kumar", "Alok Aradhe"]
    # Case law cited and counsel parsed
    assert len(meta.case_law_cited) >= 1
    assert any("(2014) 9 SCC 129" in c["normalized_citation"] for c in meta.case_law_cited)
    assert len(meta.counsel) >= 1
    doc.close()


def test_structure_tracker_pre_reflow_and_orders():
    """Verifies pre-reflow delivery lines, front-matter isolation, and operative order detection."""
    tracker = LawReportStructureTracker(bench_judges=["Harilal Kania", "Fazl Ali"])

    # 1. Front matter page
    sec1, j1, conf1 = tracker.update_from_text("Issue for Consideration\nWhether appeal is maintainable.", page_number=1)
    assert sec1 == "HEADNOTE"
    assert j1 is None
    assert conf1 == 0.50

    # 2. Pre-reflow opinion preamble line
    opinion_text = "The Judgment of the Court was delivered by KANIA C. J.-This is an appeal from the High Court."
    sec2, j2, conf2 = tracker.update_from_text(opinion_text, page_number=2)
    assert sec2 == "OPINION"
    assert "Kania" in j2
    assert conf2 == 0.95

    # 3. Confidence decay over subsequent pages
    _, _, conf3 = tracker.update_from_text("The learned counsel argued further...", page_number=5)
    assert conf3 == pytest.approx(0.89, 0.01)

    # 4. Operative order recognition
    assert LawReportStructureTracker.is_operative_order_paragraph("The result is that the appeal is dismissed.") is True
    assert LawReportStructureTracker.is_operative_order_paragraph("The petitioner made a representation.") is False


def test_page_chunker_single_page_invariant():
    """Verifies Rule R2: chunks never cross page boundaries and operative orders form dedicated chunks."""
    chunker = PageChunker(target_chunk_chars=300)
    page_text = (
        "Paragraph one discusses the legal history of the Preventive Detention Act.\n\n"
        "Paragraph two analyses the scope of Article 21 and the procedure established by law.\n\n"
        "The appeal is accordingly allowed."
    )

    chunks = chunker.chunk_page(
        document_id="TEST_DOC_1950",
        page_number=4,
        cleaned_text=page_text,
        section_heading="OPINION",
        judge_opinion="Kania C.J."
    )

    assert len(chunks) >= 2
    # Single-page invariant holds
    for c in chunks:
        assert c.page_start == c.page_end == 4
        assert c.chunk_id.startswith("TEST_DOC_1950_p4_c")

    # Operative order paragraph forms a dedicated chunk with section ORDER
    order_chunk = chunks[-1]
    assert order_chunk.section == "ORDER"
    assert "appeal is accordingly allowed" in order_chunk.content


def test_end_to_end_pipeline_execution():
    """Verifies full execution of IngestionPipeline across all 4 stages."""
    pipeline = IngestionPipeline()
    fixture_pdf = Path("tests/fixtures/1950_1_25_29_EN.pdf")

    doc_record = pipeline.process_pdf(str(fixture_pdf), year=1950)
    assert doc_record.document_id == "1950_1_25_29_EN"
    assert len(doc_record.chunks) >= 5

    # Verify all chunks strictly satisfy page_start == page_end
    for c in doc_record.chunks:
        assert c.page_start == c.page_end
        assert c.embedding is not None
        assert len(c.embedding) == 768
        assert c.metadata.chunking_version == "v3.0"
