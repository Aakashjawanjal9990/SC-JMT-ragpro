"""
Configuration for Google Cloud Vision OCR Pipeline (PCE v2.0)
Centralizes Google Vision API settings, routing thresholds, rendering DPI, and fallback modes.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Set

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class GoogleVisionOCRConfig:
    # Google Cloud Vision Settings
    ocr_engine: str = "google_vision"
    feature_type: str = "DOCUMENT_TEXT_DETECTION"
    render_dpi: int = 200
    timeout_seconds: int = 20

    # Routing Evaluation Thresholds
    min_text_length: int = 30                    # Minimum character count to consider non-empty
    replacement_char_ratio_max: float = 0.01    # 1% threshold for corrupted/unprintable characters
    ocr_quality_floor: float = 0.35             # Below this is considered severe degradation
    ocr_quality_borderline: float = 0.60        # Between floor and borderline is flagged for review
    ocr_quality_reliable: float = 0.65          # Above this native text is reliable

    # Legal terminology whitelist for short valid legal orders
    legal_keywords: Set[str] = field(default_factory=lambda: {
        "appeal", "petition", "dismissed", "allowed", "costs", "order", "decree",
        "judgment", "bench", "coram", "counsel", "advocate", "solicitor", "respondent",
        "appellant", "writ", "application", "interim", "stay", "held", "stands",
        "accordingly", "disposed", "liberty", "registrar", "court", "signed",
        "section", "article", "clause", "act", "rule", "leave", "granted"
    })

    # Paths & Output Directories
    base_dir: Path = REPO_ROOT
    output_dir: Path = REPO_ROOT / "output" / "ocr"
    raw_dir: Path = REPO_ROOT / "output" / "ocr" / "raw"
    parsed_dir: Path = REPO_ROOT / "output" / "ocr" / "parsed"
    reports_dir: Path = REPO_ROOT / "output" / "ocr" / "reports"

    def __post_init__(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.parsed_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)


# Aliases for backwards compatibility
LocalOCRConfig = GoogleVisionOCRConfig
ocr_config = GoogleVisionOCRConfig()
