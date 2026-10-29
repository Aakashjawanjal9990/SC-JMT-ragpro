"""
Google Cloud Storage (GCS) Data Fetcher (PCE v2.0)
Streams and downloads Supreme Court judgment PDFs directly from GCP buckets for batch ingestion.
"""

import os
import re
import tempfile
import logging
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

try:
    from config import config
except ImportError:  # package-style execution
    from PCEV2.config import config

logger = logging.getLogger("GCSLoader")


class GCSDataLoader:
    """Manages GCP Cloud Storage connections and streams PDFs from production buckets."""

    def __init__(self, bucket_name: Optional[str] = None, project_id: Optional[str] = None):
        self.bucket_name = (bucket_name or config.gcs_bucket or "").strip()
        self.project_id = (project_id or config.gcp_project or "").strip()
        if not self.bucket_name or not self.project_id:
            raise ValueError(
                "GCS ingestion requires GCS_BUCKET and GCP_PROJECT to be configured explicitly."
            )
        self.client = None
        self.bucket = None
        self._init_client()

    def _init_client(self):
        try:
            from google.cloud import storage
            self.client = storage.Client(project=self.project_id)
            self.bucket = self.client.bucket(self.bucket_name)
        except Exception as e:
            logger.info(f"GCS client running in offline/local mode: {e}")
            self.client = None
            self.bucket = None

    def is_available(self) -> bool:
        """Returns True if GCS client and bucket connection are active."""
        return self.client is not None and self.bucket is not None

    def list_year_pdfs(self, year: int, prefix_base: Optional[str] = None) -> List[str]:
        """Lists all PDF blob paths in GCS for a given judgment year."""
        if not self.is_available():
            raise RuntimeError(f"GCS connection unavailable. Cannot list blobs for year {year}.")

        prefix_base = (prefix_base or config.gcs_prefix).strip("/")
        prefix = f"{prefix_base}/year={year}/"
        blobs = self.client.list_blobs(self.bucket_name, prefix=prefix)
        pdf_paths = [f"gs://{self.bucket_name}/{b.name}" for b in blobs if b.name.lower().endswith(".pdf")]
        logger.info(f"Discovered {len(pdf_paths)} PDFs in GCS for year {year} under prefix: {prefix}")
        return pdf_paths

    def list_available_pdfs(self, year: Optional[int] = None, prefix_base: Optional[str] = None) -> List[str]:
        """Lists available PDF URIs in GCS. Compatible with batch runner expectations."""
        if not self.is_available():
            raise RuntimeError(f"GCS connection unavailable. Cannot list blobs for bucket '{self.bucket_name}'.")
        prefix_base = (prefix_base or config.gcs_prefix).strip("/")
        if year is not None:
            return self.list_year_pdfs(year=year, prefix_base=prefix_base)
        blobs = self.client.list_blobs(self.bucket_name, prefix=prefix_base)
        pdf_paths = [f"gs://{self.bucket_name}/{b.name}" for b in blobs if b.name.lower().endswith(".pdf")]
        logger.info(f"Discovered {len(pdf_paths)} PDFs in GCS under prefix: {prefix_base}")
        return pdf_paths

    def download_to_temp(self, gcs_uri: str) -> Tuple[str, Optional[int]]:
        """
        Downloads a GCS PDF to a local temporary file for PyMuPDF processing.
        Returns: (temp_file_path, year)
        """
        if not self.is_available():
            raise RuntimeError(f"GCS connection unavailable. Cannot download {gcs_uri}.")

        match = re.match(r"^gs://([^/]+)/(.+)$", gcs_uri)
        if not match:
            raise ValueError(f"Invalid GCS URI: {gcs_uri}")

        b_name, blob_name = match.groups()
        # Security Policy (OFF-02): Disallow arbitrary bucket access; constrain to configured bucket
        if b_name != self.bucket_name:
            raise PermissionError(f"Security Policy Violation: Access to bucket '{b_name}' denied. Constrained to '{self.bucket_name}'.")
        blob = self.client.bucket(b_name).blob(blob_name)

        yr_match = re.search(r"year=(\d{4})", blob_name)
        if yr_match:
            year = int(yr_match.group(1))
        else:
            fn_match = re.match(r"(\d{4})_", Path(blob_name).name)
            year = int(fn_match.group(1)) if fn_match else None

        suffix = Path(blob_name).suffix or ".pdf"
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="gcs_scj_")
        os.close(tmp_fd)

        try:
            blob.download_to_filename(tmp_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
        logger.info(f"Downloaded GCS blob {gcs_uri} -> {tmp_path}")
        return tmp_path, year


    @classmethod
    def verify_contract(cls) -> Dict[str, Any]:
        """
        Validates GCSDataLoader method interface and mock discovery contract (OFF-003).
        Tests list_year_pdfs, list_available_pdfs, and download_to_temp interface contracts.
        """
        has_list_avail = hasattr(cls, "list_available_pdfs")
        has_list_yr = hasattr(cls, "list_year_pdfs")
        has_download = hasattr(cls, "download_to_temp")
        has_is_avail = hasattr(cls, "is_available")

        if not (has_list_avail and has_list_yr and has_download and has_is_avail):
            return {
                "passed": False,
                "detail": f"Missing methods: list_avail={has_list_avail}, list_yr={has_list_yr}, download={has_download}"
            }

        # Mock blob and bucket for contract validation
        class MockBlob:
            def __init__(self, name):
                self.name = name

        class MockBucket:
            def __init__(self, name):
                self.name = name
            def blob(self, name):
                return MockBlob(name)

        class MockStorageClient:
            def list_blobs(self, bucket_name, prefix=""):
                if "year=" in prefix:
                    return [
                        MockBlob(f"{prefix}1950_1_10_EN.pdf"),
                        MockBlob(f"{prefix}1950_2_20_EN.pdf")
                    ]
                return [
                    MockBlob(f"{prefix}/year=1950/1950_1_10_EN.pdf"),
                    MockBlob(f"{prefix}/year=2026/2026_1_10_EN.pdf")
                ]
            def bucket(self, name):
                return MockBucket(name)

        loader = cls(bucket_name="test-scj-bucket", project_id="test-scj-proj")
        loader.client = MockStorageClient()
        loader.bucket = MockBucket("test-scj-bucket")

        # Test 1: list_year_pdfs
        yr_uris = loader.list_year_pdfs(year=1950)
        valid_yr = len(yr_uris) == 2 and all(u.startswith("gs://test-scj-bucket/") and u.endswith(".pdf") for u in yr_uris)

        # Test 2: list_available_pdfs with year
        avail_yr_uris = loader.list_available_pdfs(year=1950)
        valid_avail_yr = len(avail_yr_uris) == 2

        # Test 3: list_available_pdfs without year (corpus wide)
        all_uris = loader.list_available_pdfs(year=None)
        valid_all = len(all_uris) == 2 and all(u.startswith("gs://test-scj-bucket/") and u.endswith(".pdf") for u in all_uris)

        passed = bool(valid_yr and valid_avail_yr and valid_all)
        detail = "Verified list_year_pdfs, list_available_pdfs, and gs:// URI discovery contract" if passed else "Mock GCS discovery validation failed"
        return {"passed": passed, "detail": detail}
