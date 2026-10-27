"""
Configuration for Local Conditional PaddleOCR & PP-StructureV3 Pipeline
Centralizes local cache paths, routing thresholds, CPU/GPU settings, and output directories.
Does not perform large model downloads on import.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Set, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class LocalOCRConfig:
    # Local Inference & Cache Paths
    cache_dir: Path = REPO_ROOT / "output" / "paddlex_cache"
    use_gpu: bool = False
    lang: str = "en"
    ocr_version: str = "PP-OCRv4"
    enable_pp_structure: bool = True

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
    output_dir: Path = REPO_ROOT / "output" / "local_ocr"
    raw_dir: Path = REPO_ROOT / "output" / "local_ocr" / "raw"
    parsed_dir: Path = REPO_ROOT / "output" / "local_ocr" / "parsed"
    reports_dir: Path = REPO_ROOT / "output" / "local_ocr" / "reports"
    fixtures_dir: Path = REPO_ROOT / "tests" / "fixtures"
    sample_pdfs_dir: Path = REPO_ROOT / "data" / "sample_pdfs"

    def __post_init__(self):
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

