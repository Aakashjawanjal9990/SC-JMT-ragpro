"""
Conditional Google Cloud Vision OCR Routing Evaluator
Evaluates native PyMuPDF text and coordinate layout signals to route pages to:
- SKIP_NATIVE_OK: High-quality native text and reliable layout (no OCR needed).
- SKIP_BLANK: Genuinely blank page (no text, no images, no drawings).
- USE_GOOGLE_OCR: Scanned image-only page, degraded print, or corrupted encoding.
- FLAG_FOR_REVIEW: Borderline quality requiring inspection.

Guarantees that character count alone never triggers unnecessary OCR.
"""

import hashlib
import re
from enum import Enum
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

import fitz  # PyMuPDF

from local_ocr.ocr_config import ocr_config, LocalOCRConfig
from parsing.zone_extractor import CoordinateZoneExtractor


class PageRoutingDecision(str, Enum):
    SKIP_NATIVE_OK = "skip_native_ok"             # Readable native text & reliable layout
    SKIP_BLANK = "skip_blank"                     # Genuinely blank page
    USE_GOOGLE_OCR = "use_google_ocr"             # Image-only scan, degraded print, or corrupted encoding (Google Vision OCR)
    USE_OCR = "use_google_ocr"                    # Standard alias
    USE_PADDLE_OCR = "use_google_ocr"             # Backwards compatibility alias
    USE_PP_STRUCTURE = "use_google_ocr"           # Layout recovery handled by Google Vision hierarchy
    FLAG_FOR_REVIEW = "flag_for_review"           # Borderline / uncertain signals


@dataclass
class PageEvaluation:
    page_number: int
    total_raw_chars: int
    total_clean_chars: int
    replacement_chars_count: int
    replacement_char_ratio: float
    ocr_quality: float
    image_count: int
    drawing_count: int
    is_short_legal: bool
    decision: PageRoutingDecision
    reasons: List[str] = field(default_factory=list)
    native_snippet: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page_number": self.page_number,
            "decision": self.decision.value,
            "reasons": self.reasons,
            "total_clean_chars": self.total_clean_chars,
            "replacement_char_ratio": round(self.replacement_char_ratio, 4),
            "ocr_quality": round(self.ocr_quality, 4),
            "image_count": self.image_count,
            "drawing_count": self.drawing_count,
            "is_short_legal": self.is_short_legal,
            "native_snippet": self.native_snippet[:120]
        }


@dataclass
class DocumentEvaluation:
    document_id: str
    file_path: str
    sha256_hash: str
    total_pages: int
    page_evaluations: List[PageEvaluation] = field(default_factory=list)
    ocr_candidate_pages: List[int] = field(default_factory=list)
    structure_candidate_pages: List[int] = field(default_factory=list)
    skipped_pages: List[int] = field(default_factory=list)
    review_pages: List[int] = field(default_factory=list)
    summary_counts: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "file_path": self.file_path,
            "sha256_hash": self.sha256_hash,
            "total_pages": self.total_pages,
            "ocr_candidate_pages": self.ocr_candidate_pages,
            "structure_candidate_pages": self.structure_candidate_pages,
            "skipped_pages": self.skipped_pages,
            "review_pages": self.review_pages,
            "summary_counts": self.summary_counts,
            "pages": [p.to_dict() for p in self.page_evaluations]
        }


class LocalOCRRouter:
    """Evaluates page quality and routes pages conditionally to native digital extraction or Google Cloud Vision OCR."""

    def __init__(self, config: Optional[LocalOCRConfig] = None):
        self.config = config or ocr_config

    @staticmethod
    def compute_sha256(path: Path) -> str:
        """Computes SHA-256 hash of a file for provenance auditability."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

    def check_is_valid_short_legal_page(self, text: str, ocr_quality: float) -> Tuple[bool, List[str]]:
        """
        Determines whether a page with short character count is actually a legitimate
        legal endorsement or short operative order rather than a corrupted extraction.
        """
        stripped = text.strip().lower()
        if not stripped:
            return False, []

        tokens = set(re.findall(r"\b[a-z]{3,}\b", stripped))
        matched_legal_keywords = list(tokens.intersection(self.config.legal_keywords))

        is_valid = (
            len(matched_legal_keywords) >= 1 and
            ocr_quality >= self.config.ocr_quality_floor and
            len(stripped) < 200
        )
        return is_valid, matched_legal_keywords

    def check_has_complex_layout_or_table(self, page: Any, text: str) -> bool:
        """Detects presence of tables, multi-column blocks, or complex layout requiring PP-Structure."""
        # 1. Check for tabular structures (multiple lines with tabs or multiple vertical alignments)
        lines = text.splitlines()
        tab_count = sum(1 for l in lines if "\t" in l or "   " in l and len(re.findall(r"\d+", l)) >= 2)
        if tab_count >= 3:
            return True

        # 2. Check for vector line drawing grids (tables)
        drawings = page.get_drawings() if hasattr(page, "get_drawings") else []
        horiz_lines = 0
        vert_lines = 0
        for dr in drawings:
            r = dr.get("rect")
            if r:
                if r.height <= 3.0 and r.width >= 100.0:
                    horiz_lines += 1
                elif r.width <= 3.0 and r.height >= 50.0:
                    vert_lines += 1
        if horiz_lines >= 3 and vert_lines >= 2:
            return True

        return False

    def evaluate_page(
        self,
        page: Any,
        page_number: int,
        total_pages: int,
        document_id: str
    ) -> PageEvaluation:
        """Evaluates a single PyMuPDF page against local routing rules."""
        raw_text = page.get_text("text") or ""
        total_raw_chars = len(raw_text)

        # Count replacement characters and unprintable control characters
        replacement_count = sum(
            1 for ch in raw_text
            if ch == "\ufffd" or (ord(ch) < 32 and ch not in "\n\r\t")
        )
        replacement_ratio = replacement_count / max(1, total_raw_chars)

        # Inspect visual content: images and vector drawings
        images = page.get_images() if hasattr(page, "get_images") else []
        drawings = page.get_drawings() if hasattr(page, "get_drawings") else []
        image_count = len(images)
        drawing_count = len(drawings)

        # Coordinate zone extraction using existing pipeline logic
        id_parts = document_id.split("_")
        start_book_p = int(id_parts[2]) if len(id_parts) >= 4 and id_parts[2].isdigit() else 1
        pz = CoordinateZoneExtractor.extract_page_zones(
            page=page,
            page_number=page_number,
            total_pages=total_pages,
            start_book_page=start_book_p,
            document_id=document_id
        )

        clean_text = pz.cleaned_text.strip()
        total_clean_chars = len(clean_text)
        ocr_q = pz.ocr_quality

        # Test for legitimate short legal page
        is_short_legal, legal_hits = self.check_is_valid_short_legal_page(clean_text, ocr_q)

        # Test for complex layout / tables requiring PP-Structure
        has_complex_layout = self.check_has_complex_layout_or_table(page, clean_text)

        reasons = []
        decision = PageRoutingDecision.SKIP_NATIVE_OK

        # -------------------------------------------------------------
        # RULE 1: Genuinely Blank Page
        # A page is genuinely blank ONLY IF both raw chars == 0 AND clean chars == 0,
        # with zero images and negligible drawings.
        # -------------------------------------------------------------
        if total_raw_chars == 0 and total_clean_chars == 0 and image_count == 0 and drawing_count <= 2:
            decision = PageRoutingDecision.SKIP_BLANK
            reasons.append("genuinely_blank_page_no_text_or_images")

        # -------------------------------------------------------------
        # RULE 1B: Rescued Raw-Text Anomaly
        # If clean text was empty but raw text exists and no images, preserve native text
        # -------------------------------------------------------------
        elif total_clean_chars == 0 and total_raw_chars > 0 and image_count == 0:
            decision = PageRoutingDecision.SKIP_NATIVE_OK
            reasons.append(f"rescued_raw_text_page (raw_chars={total_raw_chars})")

        # -------------------------------------------------------------
        # RULE 2: Empty Native Text with Visible Content (Scanned Page)
        # -------------------------------------------------------------
        elif total_clean_chars < self.config.min_text_length and (image_count > 0 or drawing_count > 8):
            decision = PageRoutingDecision.USE_GOOGLE_OCR
            reasons.append(f"empty_or_sparse_text_with_visible_scanned_content (images={image_count}, drawings={drawing_count})")

        # -------------------------------------------------------------
        # RULE 3: Short but Valid Legal Page (DO NOT TRIGGER OCR SOLELY ON LENGTH)
        # -------------------------------------------------------------
        elif is_short_legal and replacement_ratio < self.config.replacement_char_ratio_max:
            decision = PageRoutingDecision.SKIP_NATIVE_OK
            reasons.append(f"valid_short_legal_page (chars={total_clean_chars}, legal_keywords={legal_hits[:3]})")

        # -------------------------------------------------------------
        # RULE 4: Corrupted Text / Excessive Replacement Characters
        # -------------------------------------------------------------
        elif replacement_ratio >= self.config.replacement_char_ratio_max:
            decision = PageRoutingDecision.USE_GOOGLE_OCR
            reasons.append(f"excessive_replacement_chars (ratio={replacement_ratio:.3f} >= threshold={self.config.replacement_char_ratio_max:.3f})")

        # -------------------------------------------------------------
        # RULE 5: Severe OCR Degradation
        # -------------------------------------------------------------
        elif ocr_q < self.config.ocr_quality_floor and total_clean_chars >= self.config.min_text_length:
            decision = PageRoutingDecision.USE_GOOGLE_OCR
            reasons.append(f"severe_ocr_degradation (score={ocr_q:.3f} < floor={self.config.ocr_quality_floor:.3f})")

        # -------------------------------------------------------------
        # RULE 6: Complex Layout or Tables requiring Structure Recovery
        # -------------------------------------------------------------
        elif has_complex_layout:
            decision = PageRoutingDecision.USE_GOOGLE_OCR
            reasons.append("complex_layout_or_table_detected_requires_google_vision_ocr")

        # -------------------------------------------------------------
        # RULE 7: Borderline / Confused Quality -> Trigger Google Cloud Vision OCR
        # -------------------------------------------------------------
        elif self.config.ocr_quality_floor <= ocr_q < self.config.ocr_quality_borderline:
            decision = PageRoutingDecision.USE_GOOGLE_OCR
            reasons.append(f"borderline_or_confused_ocr_quality_triggering_google_ocr (score={ocr_q:.3f} in [{self.config.ocr_quality_floor:.2f}, {self.config.ocr_quality_borderline:.2f}))")

        # -------------------------------------------------------------
        # RULE 8: Readable Native Text & Reliable Layout
        # -------------------------------------------------------------
        else:
            decision = PageRoutingDecision.SKIP_NATIVE_OK
            reasons.append(f"reliable_native_text (ocr_quality={ocr_q:.3f}, chars={total_clean_chars})")

        return PageEvaluation(
            page_number=page_number,
            total_raw_chars=total_raw_chars,
            total_clean_chars=total_clean_chars,
            replacement_chars_count=replacement_count,
            replacement_char_ratio=replacement_ratio,
            ocr_quality=ocr_q,
            image_count=image_count,
            drawing_count=drawing_count,
            is_short_legal=is_short_legal,
            decision=decision,
            reasons=reasons,
            native_snippet=clean_text[:120].replace("\n", " ")
        )

    def evaluate_document(self, pdf_path: Path) -> DocumentEvaluation:
        """Evaluates an entire PDF document page by page."""
        path = Path(pdf_path)
        doc_id = path.stem
        sha256_hash = self.compute_sha256(path)

        doc = fitz.open(path)
        try:
            total_pages = len(doc)
            page_evals: List[PageEvaluation] = []
            ocr_candidates: List[int] = []
            structure_candidates: List[int] = []
            skipped: List[int] = []
            review: List[int] = []
            summary: Dict[str, int] = {
                PageRoutingDecision.SKIP_NATIVE_OK.value: 0,
                PageRoutingDecision.SKIP_BLANK.value: 0,
                PageRoutingDecision.USE_GOOGLE_OCR.value: 0,
                PageRoutingDecision.FLAG_FOR_REVIEW.value: 0
            }

            for pno in range(1, total_pages + 1):
                page = doc[pno - 1]
                ev = self.evaluate_page(page, pno, total_pages, doc_id)
                page_evals.append(ev)
                dec_val = ev.decision.value if isinstance(ev.decision, PageRoutingDecision) else str(ev.decision)
                summary[dec_val] = summary.get(dec_val, 0) + 1

                if ev.decision in (PageRoutingDecision.USE_GOOGLE_OCR, PageRoutingDecision.USE_PADDLE_OCR):
                    ocr_candidates.append(pno)
                elif ev.decision in (PageRoutingDecision.SKIP_NATIVE_OK, PageRoutingDecision.SKIP_BLANK):
                    skipped.append(pno)
                elif ev.decision == PageRoutingDecision.FLAG_FOR_REVIEW:
                    review.append(pno)

            return DocumentEvaluation(
                document_id=doc_id,
                file_path=str(path.resolve()),
                sha256_hash=sha256_hash,
                total_pages=total_pages,
                page_evaluations=page_evals,
                ocr_candidate_pages=ocr_candidates,
                structure_candidate_pages=structure_candidates,
                skipped_pages=skipped,
                review_pages=review,
                summary_counts=summary
            )
        finally:
            doc.close()
