"""
PDF Text, OCR & LlamaParse Extraction Module
Extracts selectable text and falls back to OCR or LlamaParse cloud parser for scanned, low-quality, or complex pages.
Preserves original page boundaries strictly.
"""

import os
import io
import time
import json
import logging
from pathlib import Path
from typing import Tuple, Optional, List, Dict, Any

import fitz  # PyMuPDF
import httpx
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

logger = logging.getLogger(__name__)

# Minimum character count to consider selectable text valid (below this triggers OCR fallback)
MIN_TEXT_LENGTH_THRESHOLD = 50

# Default LlamaParse Cloud API Key from environment
DEFAULT_LLAMA_CLOUD_API_KEY = os.environ.get("LLAMA_CLOUD_API_KEY", "")


class LlamaParseExtractor:
    """
    Handles high-accuracy cloud-based PDF parsing via LlamaParse v2 API.
    Supports complex legal formatting, multi-column judgments, footnotes, and tables.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("LLAMA_CLOUD_API_KEY") or DEFAULT_LLAMA_CLOUD_API_KEY
        self.base_url = "https://api.cloud.llamaindex.ai/api/v2/parse"

    def is_available(self) -> bool:
        """Checks if API key is present and valid."""
        return bool(self.api_key and self.api_key.startswith("llx-"))

    def parse_pdf_pages(self, file_path: str, timeout_seconds: int = 120) -> List[Tuple[int, str]]:
        """
        Submits PDF to LlamaParse v2 API, polls for completion, and returns page-by-page markdown.
        Returns:
            List of tuples: (page_number: int, page_markdown: str)
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }

        logger.info(f"Uploading {file_path} to LlamaParse v2 API...")
        file_name = Path(file_path).name

        with open(file_path, "rb") as f:
            files = {
                "file": (file_name, f, "application/pdf")
            }
            config = json.dumps({
                "tier": "cost_effective",
                "version": "latest"
            })
            upload_res = httpx.post(
                f"{self.base_url}/upload",
                headers=headers,
                files=files,
                data={"configuration": config},
                timeout=60
            )

        if upload_res.status_code not in (200, 201):
            raise RuntimeError(f"LlamaParse upload failed ({upload_res.status_code}): {upload_res.text}")

        job_info = upload_res.json()
        job_id = job_info.get("id")
        if not job_id:
            raise RuntimeError(f"No job_id returned by LlamaParse: {upload_res.text}")

        logger.info(f"LlamaParse parse job initiated: {job_id}. Polling for completion...")

        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            time.sleep(2)
            poll_res = httpx.get(
                f"{self.base_url}/{job_id}?expand=markdown",
                headers=headers,
                timeout=30
            )

            if poll_res.status_code != 200:
                logger.warning(f"LlamaParse polling warning ({poll_res.status_code}): {poll_res.text}")
                continue

            poll_data = poll_res.json()
            status = poll_data.get("job", {}).get("status")

            if status == "COMPLETED":
                pages_data = poll_data.get("markdown", {}).get("pages", [])
                results: List[Tuple[int, str]] = []
                for idx, page_item in enumerate(pages_data):
                    p_num = page_item.get("page_number") or (idx + 1)
                    p_text = page_item.get("markdown") or ""
                    results.append((int(p_num), p_text.strip()))

                logger.info(f"LlamaParse successfully completed job {job_id} ({len(results)} pages).")
                return results

            elif status == "FAILED":
                err = poll_data.get("job", {}).get("error_message") or "Unknown error"
                raise RuntimeError(f"LlamaParse job {job_id} failed: {err}")

        raise TimeoutError(f"LlamaParse job {job_id} timed out after {timeout_seconds}s")


class PDFExtractor:
    """
    Coordinates page-by-page text extraction with support for:
    - LlamaParse cloud parsing (high fidelity markdown & legal layout)
    - Local PyMuPDF selectable text
    - Local OCR fallback (Tesseract / PyMuPDF OCR)
    """

    def __init__(
        self,
        ocr_enabled: bool = True,
        use_llamaparse: bool = False,
        llama_api_key: Optional[str] = None
    ):
        self.ocr_enabled = ocr_enabled
        self.use_llamaparse = use_llamaparse
        self.llama_extractor = LlamaParseExtractor(api_key=llama_api_key)

    def extract_document_pages(self, file_path: str) -> List[Tuple[int, str, bool, str]]:
        """
        Extracts all pages from a document.
        Returns:
            List of tuples: (page_num, page_text, used_ocr, engine_name)
        """
        # 1. Attempt LlamaParse if enabled and available
        if self.use_llamaparse and self.llama_extractor.is_available():
            try:
                llama_pages = self.llama_extractor.parse_pdf_pages(file_path)
                if llama_pages:
                    logger.info(f"Successfully extracted {len(llama_pages)} pages via LlamaParse.")
                    return [
                        (p_num, p_text, True, "llamaparse")
                        for p_num, p_text in llama_pages
                    ]
            except Exception as e:
                logger.warning(f"LlamaParse encountered error: {e}. Falling back to PyMuPDF/OCR.")

        # 2. Local PyMuPDF + OCR fallback per page
        doc = fitz.open(file_path)
        pages_out = []
        for idx in range(len(doc)):
            page_num = idx + 1
            page = doc[idx]
            text, used_ocr = self.extract_page_text(page, page_num)
            pages_out.append((page_num, text, used_ocr, "pymupdf_ocr" if used_ocr else "pymupdf"))
        doc.close()
        return pages_out

    def extract_page_text(self, page: fitz.Page, page_num: int) -> Tuple[str, bool]:
        """
        Extracts text from a single PyMuPDF page.
        Returns:
            Tuple of (raw_text, used_ocr: bool)
        """
        # Step 1: Attempt standard selectable text extraction
        text = page.get_text("text").strip()

        # If selectable text is sufficiently dense and readable, return it
        if len(text) >= MIN_TEXT_LENGTH_THRESHOLD:
            return text, False

        # Step 2: Fallback to OCR if selectable text is insufficient
        if self.ocr_enabled:
            logger.info(f"Page {page_num}: text length ({len(text)}) below threshold. Invoking OCR fallback...")
            ocr_text = self._perform_ocr(page)
            if ocr_text and len(ocr_text.strip()) > 0:
                return ocr_text.strip(), True

        # Return whatever was found (even if sparse) if OCR is unavailable or yielded nothing
        return text, False

    def _perform_ocr(self, page: fitz.Page) -> str:
        """
        Performs OCR on the page raster image.
        Uses pytesseract if available, otherwise checks for PyMuPDF's built-in OCR.
        """
        # Option A: PyMuPDF OCR (if Tesseract data is linked)
        try:
            tp = page.get_textpage_ocr(flags=0, dpi=200, full=True)
            text = tp.extractText().strip()
            if text:
                return text
        except Exception:
            pass

        # Option B: pytesseract fallback using page pixmap
        try:
            import pytesseract
            from PIL import Image

            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
            image = Image.open(io.BytesIO(img_bytes))
            ocr_text = pytesseract.image_to_string(image)
            return ocr_text
        except ImportError:
            logger.warning("pytesseract not installed or tesseract binary not found on PATH.")
        except Exception as e:
            logger.warning(f"OCR execution failed: {e}")

        return ""

    def get_document_page_count(self, file_path: str) -> int:
        """Returns the total number of pages in the PDF."""
        doc = fitz.open(file_path)
        count = len(doc)
        doc.close()
        return count
