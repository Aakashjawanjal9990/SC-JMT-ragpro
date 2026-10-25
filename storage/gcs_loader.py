"""
Google Cloud Storage (GCS) Data Fetcher (PCE v2.0)
Streams and downloads Supreme Court judgment PDFs directly from GCP buckets for batch ingestion.
"""

import os
import re
import tempfile
import logging
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger("GCSLoader")


class GCSDataLoader:
    """Manages GCP Cloud Storage connections and streams PDFs from production buckets."""

    def __init__(self, bucket_name: Optional[str] = None, project_id: Optional[str] = None):
        self.bucket_name = bucket_name or os.getenv("GCS_BUCKET", "sc-judgments-rag-prod-data")
        self.project_id = project_id or os.getenv("GCP_PROJECT", "sc-judgments-rag-prod")
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

    def list_year_pdfs(self, year: int, prefix_base: str = "raw_pdfs/pdfs") -> List[str]:
        """Lists all PDF blob paths in GCS for a given judgment year."""
        if not self.is_available():
            raise RuntimeError(f"GCS connection unavailable. Cannot list blobs for year {year}.")

        prefix = f"{prefix_base}/year={year}/"
        blobs = self.client.list_blobs(self.bucket_name, prefix=prefix)
        pdf_paths = [f"gs://{self.bucket_name}/{b.name}" for b in blobs if b.name.endswith(".pdf")]
        logger.info(f"Discovered {len(pdf_paths)} PDFs in GCS for year {year} under prefix: {prefix}")
        return pdf_paths

    def download_to_temp(self, gcs_uri: str) -> Tuple[str, int]:
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
        blob = self.client.bucket(b_name).blob(blob_name)

        yr_match = re.search(r"year=(\d{4})", blob_name)
        if yr_match:
            year = int(yr_match.group(1))
        else:
            fn_match = re.match(r"(\d{4})_", Path(blob_name).name)
            year = int(fn_match.group(1)) if fn_match else 2026

        suffix = Path(blob_name).suffix or ".pdf"
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="gcs_scj_")
        os.close(tmp_fd)

        blob.download_to_filename(tmp_path)
        logger.info(f"Downloaded GCS blob {gcs_uri} -> {tmp_path}")
        return tmp_path, year

