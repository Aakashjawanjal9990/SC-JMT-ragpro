"""Local Conditional OCR and Document Structure Recovery Package.

Provides local PaddleOCR and PP-StructureV3 routing, inference, and
legal layout recovery for Supreme Court of India judgment PDFs.
"""

from local_ocr.ocr_config import ocr_config, LocalOCRConfig
from local_ocr.layout_recovery import LegalStructureRecovery, purge_ocr_running_headers

local_ocr_config = ocr_config

__all__ = [
    "ocr_config",
    "local_ocr_config",
    "LocalOCRConfig",
    "ConditionalOCRRouter",
    "RoutingDecision",
    "RoutingResult",
    "LocalOCREngine",
    "OCRResult",
    "LegalStructureRecovery",
    "purge_ocr_running_headers",
]


