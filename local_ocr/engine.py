"""
Local PaddleOCR & PP-StructureV3 Inference Engine
Executes local OCR on rendered PDF pages without external APIs.
Features:
- Safe Windows CPU execution: disables PIR oneDNN bug by setting run_mode='paddle'.
- Redirects cache to output/paddlex_cache inside workspace.
- Lazy model loading (0 models loaded at application import or startup).
- Fallback preservation: never crashes on corrupted images; preserves native text.
"""

import os
import sys
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Union

import fitz  # PyMuPDF
import numpy as np

from local_ocr.ocr_config import ocr_config, LocalOCRConfig


@dataclass
class OCRLine:
    text: str
    confidence: float
    bbox: Optional[List[float]] = None  # [x0, y0, x1, y1]


@dataclass
class LocalOCRResult:
    text: str
    lines: List[OCRLine] = field(default_factory=list)
    average_confidence: float = 0.0
    execution_time_ms: float = 0.0
    engine: str = "PaddleOCR"
    page_number: int = 1
    is_mock: bool = False
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "line_count": len(self.lines),
            "average_confidence": round(self.average_confidence, 4),
            "execution_time_ms": round(self.execution_time_ms, 2),
            "engine": self.engine,
            "page_number": self.page_number,
            "is_mock": self.is_mock,
            "error": self.error
        }


@dataclass
class PPStructureResult:
    markdown_text: str
    tables: List[Dict[str, Any]] = field(default_factory=list)
    regions: List[Dict[str, Any]] = field(default_factory=list)
    execution_time_ms: float = 0.0
    engine: str = "PPStructureV3"
    page_number: int = 1
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "markdown_text": self.markdown_text,
            "table_count": len(self.tables),
            "region_count": len(self.regions),
            "execution_time_ms": round(self.execution_time_ms, 2),
            "engine": self.engine,
            "page_number": self.page_number,
            "error": self.error
        }


class LocalOCREngine:
    """Manages local PaddleOCR and PP-StructureV3 predictors with lazy initialization."""

    def __init__(self, config: Optional[LocalOCRConfig] = None):
        self.config = config or ocr_config
        self._ocr_predictor = None
        self._structure_predictor = None
        self._initialized = False

    def _setup_environment_and_patches(self):
        """Sets up workspace cache and safe Windows CPU execution settings."""
        if self._initialized:
            return

        # Cache paths
        cache_str = str(self.config.cache_dir.resolve())
        os.environ["PADDLE_PDX_CACHE_HOME"] = cache_str
        os.environ["PADDLE_HOME"] = cache_str
        os.environ["MPLCONFIGDIR"] = str(self.config.cache_dir / "matplotlib")
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        os.environ["FLAGS_enable_pir_api"] = "0"
        os.environ["FLAGS_use_onednn"] = "0"
        os.environ["FLAGS_onednn_verbose"] = "0"

        # Prevent modelscope from trying to load conflicting torch shm.dll
        sys.modules['torch'] = None

        # Patch PaddlePredictorOption to use 'paddle' run_mode on Windows CPU
        try:
            from paddlex.inference.models import PaddlePredictorOption
            orig_set_default = PaddlePredictorOption.setdefault_by_model_name

            def safe_set_default(opt_self, model_name):
                orig_set_default(opt_self, model_name)
                opt_self._cfg["run_mode"] = "paddle"

            PaddlePredictorOption.setdefault_by_model_name = safe_set_default
        except Exception:
            pass

        self._initialized = True

    def _get_ocr_predictor(self):
        """Lazy-loads the PaddleOCR predictor."""
        if self._ocr_predictor is None:
            self._setup_environment_and_patches()
            from paddleocr import PaddleOCR
            self._ocr_predictor = PaddleOCR(
                use_textline_orientation=False,
                lang=self.config.lang
            )
        return self._ocr_predictor

    def _get_structure_predictor(self):
        """Lazy-loads the PPStructureV3 predictor."""
        if self._structure_predictor is None:
            self._setup_environment_and_patches()
            from paddleocr import PPStructureV3
            self._structure_predictor = PPStructureV3(
                use_textline_orientation=False,
                lang=self.config.lang
            )
        return self._structure_predictor

    @staticmethod
    def render_page_to_numpy(page: Any, dpi: int = 150) -> np.ndarray:
        """Renders a PyMuPDF page to RGB numpy array."""
        pix = page.get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            img = img[:, :, :3]
        return img

    def ocr_page(
        self,
        page_or_image: Union[Any, np.ndarray],
        page_number: int = 1,
        allow_mock: bool = False
    ) -> LocalOCRResult:
        """
        Executes local PaddleOCR on a page or image array.
        If allow_mock=True, returns a simulated result for unit tests.
        """
        t0 = time.time()

        if allow_mock:
            mock_text = (
                "SUPREME COURT OF INDIA\n"
                "Petitioner v. State of Orissa\n"
                "[Mukherjea, J.]\n"
                "1. The application under Article 32 is allowed. "
                "The provisions of sections 38 and 39 of the Act are invalid."
            )
            mock_lines = [
                OCRLine(text="SUPREME COURT OF INDIA", confidence=0.98),
                OCRLine(text="Petitioner v. State of Orissa", confidence=0.96),
                OCRLine(text="[Mukherjea, J.]", confidence=0.95),
                OCRLine(text="1. The application under Article 32 is allowed.", confidence=0.97),
                OCRLine(text="The provisions of sections 38 and 39 of the Act are invalid.", confidence=0.94)
            ]
            return LocalOCRResult(
                text=mock_text,
                lines=mock_lines,
                average_confidence=0.96,
                execution_time_ms=(time.time() - t0) * 1000,
                engine="PaddleOCR_Mock",
                page_number=page_number,
                is_mock=True
            )

        try:
            # Resolve image array
            if hasattr(page_or_image, "get_pixmap"):
                img = self.render_page_to_numpy(page_or_image, dpi=150)
            elif isinstance(page_or_image, np.ndarray):
                img = page_or_image
            else:
                raise ValueError(f"Unsupported input type for OCR: {type(page_or_image)}")

            predictor = self._get_ocr_predictor()
            results = predictor.predict(img)

            ocr_lines: List[OCRLine] = []
            extracted_texts: List[str] = []
            scores: List[float] = []

            if results:
                for res in results:
                    rec_texts = res.get("rec_texts", []) if hasattr(res, "get") else getattr(res, "rec_texts", [])
                    rec_scores = res.get("rec_scores", []) if hasattr(res, "get") else getattr(res, "rec_scores", [])
                    dt_polys = res.get("dt_polys", []) if hasattr(res, "get") else getattr(res, "dt_polys", [])

                    for i, t in enumerate(rec_texts):
                        sc = float(rec_scores[i]) if i < len(rec_scores) else 0.90
                        poly = dt_polys[i].tolist() if (i < len(dt_polys) and hasattr(dt_polys[i], "tolist")) else None
                        ocr_lines.append(OCRLine(text=t, confidence=sc, bbox=poly))
                        extracted_texts.append(t)
                        scores.append(sc)

            full_text = "\n".join(extracted_texts).strip()
            avg_conf = float(np.mean(scores)) if scores else 0.0
            elapsed_ms = (time.time() - t0) * 1000

            return LocalOCRResult(
                text=full_text,
                lines=ocr_lines,
                average_confidence=avg_conf,
                execution_time_ms=elapsed_ms,
                engine="PaddleOCR",
                page_number=page_number,
                is_mock=False
            )

        except Exception as e:
            elapsed_ms = (time.time() - t0) * 1000
            # Preserves native extraction on error; returns error object
            return LocalOCRResult(
                text="",
                lines=[],
                average_confidence=0.0,
                execution_time_ms=elapsed_ms,
                engine="PaddleOCR",
                page_number=page_number,
                error=str(e)
            )

    def structure_page(
        self,
        page_or_image: Union[Any, np.ndarray],
        page_number: int = 1,
        allow_mock: bool = False
    ) -> PPStructureResult:
        """
        Executes local PP-StructureV3 for layout recovery and table extraction.
        """
        t0 = time.time()

        if allow_mock:
            mock_md = (
                "# Supreme Court of India\n\n"
                "## Order of the Court\n\n"
                "| Schedule No. | Section Ref | Status |\n"
                "| --- | --- | --- |\n"
                "| 1 | Section 38 | Invalid |\n"
                "| 2 | Section 39 | Invalid |\n"
            )
            return PPStructureResult(
                markdown_text=mock_md,
                tables=[{"table_id": 1, "rows": 2}],
                regions=[{"type": "table", "bbox": [50, 100, 400, 250]}],
                execution_time_ms=(time.time() - t0) * 1000,
                engine="PPStructureV3_Mock",
                page_number=page_number
            )

        try:
            if hasattr(page_or_image, "get_pixmap"):
                img = self.render_page_to_numpy(page_or_image, dpi=150)
            elif isinstance(page_or_image, np.ndarray):
                img = page_or_image
            else:
                raise ValueError(f"Unsupported input type for PP-Structure: {type(page_or_image)}")

            predictor = self._get_structure_predictor()
            results = predictor.predict(img)

            md_blocks = []
            tables = []
            regions = []

            if results:
                for res in results:
                    # Collect markdown or layout text
                    if hasattr(res, "get"):
                        html_tables = res.get("html_tables", [])
                        for t in html_tables:
                            tables.append({"html": t})
                            md_blocks.append(t)
                        texts = res.get("rec_texts", [])
                        md_blocks.extend(texts)
                    elif hasattr(res, "markdown"):
                        md_blocks.append(res.markdown)

            full_md = "\n\n".join(md_blocks).strip()
            elapsed_ms = (time.time() - t0) * 1000

            return PPStructureResult(
                markdown_text=full_md,
                tables=tables,
                regions=regions,
                execution_time_ms=elapsed_ms,
                engine="PPStructureV3",
                page_number=page_number
            )

        except Exception as e:
            elapsed_ms = (time.time() - t0) * 1000
            return PPStructureResult(
                markdown_text="",
                tables=[],
                regions=[],
                execution_time_ms=elapsed_ms,
                engine="PPStructureV3",
                page_number=page_number,
                error=str(e)
            )

