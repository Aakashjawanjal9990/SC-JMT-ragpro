"""Local Conditional OCR and Document Structure Recovery Package.

Provides local PaddleOCR and PP-StructureV3 routing, inference, and
legal layout recovery for Supreme Court of India judgment PDFs.
"""

from local_ocr.ocr_config import ocr_config, LocalOCRConfig
from local_ocr.layout_recovery import LegalStructureRecovery, purge_ocr_running_headers
from local_ocr.router import (
    LocalOCRRouter,
    PageRoutingDecision,
    DocumentEvaluation,
    PageEvaluation,
)
from local_ocr.engine import (
    LocalOCREngine,
    LocalOCRResult,
    OCRLine,
    PPStructureResult,
)

# Backwards compatibility aliases
ConditionalOCRRouter = LocalOCRRouter
RoutingDecision = PageRoutingDecision
RoutingResult = DocumentEvaluation
OCRResult = LocalOCRResult

local_ocr_config = ocr_config

__all__ = [
    "ocr_config",
    "local_ocr_config",
    "LocalOCRConfig",
    "LocalOCRRouter",
    "ConditionalOCRRouter",
    "PageRoutingDecision",
    "RoutingDecision",
    "DocumentEvaluation",
    "RoutingResult",
    "PageEvaluation",
    "LocalOCREngine",
    "LocalOCRResult",
    "OCRResult",
    "OCRLine",
    "PPStructureResult",
    "LegalStructureRecovery",
    "purge_ocr_running_headers",
]
