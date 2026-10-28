"""
Unit Tests for Image-Only Page Detection and OCR Quality Calculation
Validates:
- Defect H-04: Image-only pages with empty text are accurately classified as 'image_only_pending_ocr'
- Blank pages are accurately classified as 'verified_blank'
- Defect H-05: Real dictionary word ratio OCR quality calculation
"""

from unittest.mock import MagicMock
from parsing.zone_extractor import CoordinateZoneExtractor, calculate_ocr_quality
from core.models import ExtractedPageZones


def test_calculate_ocr_quality_continuous_scoring():
    """Verifies that calculate_ocr_quality produces distinct, non-hardcoded scores."""
    # High-quality legal text
    legal_text = (
        "The Supreme Court of India dismissed the criminal appeal on the ground that "
        "the petitioner failed to substantiate the allegations of arbitrariness under Article 14."
    )
    score_legal = calculate_ocr_quality(legal_text, is_born_digital=True)
    assert 0.88 <= score_legal <= 1.0

    # Low-quality garbled OCR text
    garbled_text = "xkz 19#% qwwer plkjh mmbvz @@@ 99238"
    score_garbled = calculate_ocr_quality(garbled_text, is_born_digital=False)
    assert score_garbled <= 0.35

    # Empty text
    assert calculate_ocr_quality("", is_born_digital=True) == 0.0


def test_image_only_page_classification():
    """Verifies that pages with images but 0 extracted text are marked image_only_pending_ocr."""
    mock_page = MagicMock()
    mock_page.rect.width = 595.0
    mock_page.rect.height = 842.0
    mock_page.get_drawings.return_value = []
    # Dict mode returns empty text blocks
    mock_page.get_text.return_value = {"blocks": []}
    # Page contains 1 embedded image scan
    mock_page.get_images.return_value = [("xref1", 0, 100, 100, 8, "DeviceRGB", "", "img1", "FlateDecode")]

    pz = CoordinateZoneExtractor.extract_page_zones(
        page=mock_page,
        page_number=12,
        total_pages=20,
        document_id="2011_12_1106_1127_EN"
    )

    assert pz.cleaned_text == ""
    assert pz.extraction_method == "image_only_pending_ocr"
    assert pz.ocr_quality == 0.0


def test_verified_blank_page_classification():
    """Verifies that completely blank pages (no images, no text) are marked verified_blank."""
    mock_page = MagicMock()
    mock_page.rect.width = 595.0
    mock_page.rect.height = 842.0
    mock_page.get_drawings.return_value = []
    mock_page.get_text.return_value = {"blocks": []}
    mock_page.get_images.return_value = []  # No images

    pz = CoordinateZoneExtractor.extract_page_zones(
        page=mock_page,
        page_number=15,
        total_pages=20,
        document_id="2011_12_1106_1127_EN"
    )

    assert pz.cleaned_text == ""
    assert pz.extraction_method == "verified_blank"
    assert pz.ocr_quality == 1.0

