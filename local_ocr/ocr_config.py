"""
Configuration for Local Conditional PaddleOCR & PP-StructureV3 Pipeline
Centralizes local cache paths, routing thresholds, CPU/GPU settings, and output directories.
Does not perform large model downloads on import.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Set

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class LocalOCRConfig:
    # Local Inference & Cache Paths
    cache_dir: Path = field(default_factory=lambda: Path(os.getenv("PADDLE_PDX_CACHE_HOME", str(REPO_ROOT / "output" / "paddlex_cache"))).expanduser())
    use_gpu: bool = field(default_factory=lambda: os.getenv("PADDLE_OCR_USE_GPU", "false").strip().lower() in {"1", "true", "yes"})
    lang: str = field(default_factory=lambda: os.getenv("PADDLE_OCR_LANG", "en").strip())
    ocr_version: str = field(default_factory=lambda: os.getenv("PADDLE_OCR_VERSION", "PP-OCRv4").strip())
    enable_pp_structure: bool = field(default_factory=lambda: os.getenv("ENABLE_PP_STRUCTURE", "true").strip().lower() in {"1", "true", "yes"})

    # Routing Evaluation Thresholds
    min_text_length: int = field(default_factory=lambda: int(os.getenv("OCR_MIN_TEXT_LENGTH", "30")))
    replacement_char_ratio_max: float = field(default_factory=lambda: float(os.getenv("OCR_REPLACEMENT_CHAR_RATIO_MAX", "0.01")))
    ocr_quality_floor: float = field(default_factory=lambda: float(os.getenv("OCR_QUALITY_FLOOR", "0.35")))
    ocr_quality_borderline: float = field(default_factory=lambda: float(os.getenv("OCR_QUALITY_BORDERLINE", "0.60")))
    ocr_quality_reliable: float = field(default_factory=lambda: float(os.getenv("OCR_QUALITY_RELIABLE", "0.65")))

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
    output_dir: Path = REPO_ROOT / "output" / "local_ocr"
    raw_dir: Path = REPO_ROOT / "output" / "local_ocr" / "raw"
    parsed_dir: Path = REPO_ROOT / "output" / "local_ocr" / "parsed"
    reports_dir: Path = REPO_ROOT / "output" / "local_ocr" / "reports"
    fixtures_dir: Path = REPO_ROOT / "tests" / "fixtures"
    sample_pdfs_dir: Path = REPO_ROOT / "data" / "sample_pdfs"

    def validate(self) -> None:
        if not self.lang:
            raise ValueError("PADDLE_OCR_LANG cannot be empty.")
        if self.min_text_length < 1:
            raise ValueError("OCR_MIN_TEXT_LENGTH must be >= 1.")
        if not 0 <= self.replacement_char_ratio_max <= 1:
            raise ValueError("OCR_REPLACEMENT_CHAR_RATIO_MAX must be between 0 and 1.")
        if not (0 <= self.ocr_quality_floor <= self.ocr_quality_borderline <= self.ocr_quality_reliable <= 1):
            raise ValueError("OCR quality thresholds must satisfy 0 <= floor <= borderline <= reliable <= 1.")

    def __post_init__(self):
        self.validate()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.parsed_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        # Apply environment settings for PaddlePaddle & PaddleX inside sandbox
        os.environ["PADDLE_PDX_CACHE_HOME"] = str(self.cache_dir)
        os.environ["PADDLE_HOME"] = str(self.cache_dir)
        os.environ["MPLCONFIGDIR"] = str(self.cache_dir / "matplotlib")
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        os.environ["FLAGS_enable_pir_api"] = "0"
        os.environ["FLAGS_use_onednn"] = "0"
        os.environ["FLAGS_onednn_verbose"] = "0"


ocr_config = LocalOCRConfig()

