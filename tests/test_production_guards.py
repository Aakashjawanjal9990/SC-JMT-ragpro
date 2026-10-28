"""Regression tests for production-readiness findings; no cloud credentials required."""
import json
import os
import tempfile
import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fitz

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config as config_module
from config import resolve_database_url
from core.models import ChunkRecord, ChunkMetadata
from local_ocr.router import PageRoutingDecision
from storage.stage_tracker import DocumentStageTracker
from storage.gcs_loader import GCSDataLoader
from audit_quality import run_quality_audit


class ConfigSafetyTests(unittest.TestCase):
    def test_local_target_does_not_use_postgres_database_url(self):
        with patch.dict(os.environ, {
            "DATABASE_URL": "postgresql+psycopg2://user:pass@db.example/test",
            "DATABASE_TARGET": "alloydb",
        }, clear=False):
            url = resolve_database_url(Path(tempfile.gettempdir()), target="local")
        self.assertTrue(url.startswith("sqlite:///"))

    def test_alloydb_fails_closed_without_explicit_credentials(self):
        keys = ["ALLOYDB_DATABASE_URL", "DB_USER", "DB_PASSWORD", "DB_HOST", "DB_NAME"]
        with patch.dict(os.environ, {k: "" for k in keys}, clear=False):
            with self.assertRaises(ValueError):
                resolve_database_url(Path(tempfile.gettempdir()), target="alloydb")

    def test_production_runtime_requires_explicit_cloud_and_embedding_config(self):
        cfg = config_module.PipelineConfig.__new__(config_module.PipelineConfig)
        cfg.gcp_project = ""
        cfg.gcs_bucket = ""
        cfg.embedding_provider = "google"
        cfg.embedding_model_id = "text-embedding-004"
        cfg.embedding_dimension = 768
        cfg.allow_mock_embeddings = False
        with patch.dict(os.environ, {"EMBEDDING_PROVIDER": "", "EMBEDDING_MODEL_ID": "", "EMBEDDING_DIMENSION": ""}, clear=False):
            with self.assertRaisesRegex(ValueError, "GCP_PROJECT.*GCS_BUCKET"):
                cfg.validate_runtime(target="alloydb", require_gcs=False)

    def test_gcs_loader_rejects_missing_bucket_or_project(self):
        with patch.object(config_module.config, "gcs_bucket", ""), patch.object(config_module.config, "gcp_project", ""):
            with self.assertRaisesRegex(ValueError, "GCS_BUCKET and GCP_PROJECT"):
                GCSDataLoader()

    def test_alloydb_password_reserved_characters_are_encoded(self):
        env = {
            "ALLOYDB_DATABASE_URL": "",
            "DB_USER": "ingest_user",
            "DB_PASSWORD": "p@ss:/word?#",
            "DB_HOST": "private-db.example",
            "DB_PORT": "5432",
            "DB_NAME": "judgments",
            "DB_SSLMODE": "require",
        }
        with patch.dict(os.environ, env, clear=False):
            url = resolve_database_url(Path(tempfile.gettempdir()), target="alloydb")
        self.assertIn("p%40ss%3A%2Fword%3F%23", url)
        self.assertIn("sslmode=require", url)


class TrackingAndGateTests(unittest.TestCase):
    def test_tracker_accepts_pipeline_constructor_and_tracks_provenance(self):
        tracker = DocumentStageTracker(document_id="sample", year=2024)
        self.assertIn("provenance", tracker.stages)
        self.assertEqual(tracker.case_title, "sample")

    def test_gcs_loader_contract_contains_batch_method(self):
        self.assertTrue(callable(getattr(GCSDataLoader, "list_available_pdfs", None)))

    def test_quality_audit_fails_closed_for_missing_database_and_empty_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            chunks = Path(tmp) / "chunks"
            chunks.mkdir()
            result = run_quality_audit(chunks_dir=chunks, db_url=f"sqlite:///{Path(tmp) / 'missing.db'}")
        self.assertFalse(result["all_passed"])
        statuses = {k: v["status"] for k, v in result["quality_checks"].items()}
        self.assertEqual(statuses["AUDITABLE_CORPUS_PRESENT"], "FAILED")
        self.assertEqual(statuses["DATABASE_PERSISTENCE"], "FAILED")
        self.assertEqual(statuses["EMBEDDING_FIDELITY"], "FAILED")


class OCRPipelineIntegrationTests(unittest.TestCase):
    """Use a synthetic one-page PDF and fake OCR to prove OCR reaches chunking."""

    def _run_pipeline(self, ocr_text, ocr_error=None):
        from pipeline import IngestionPipeline

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "2024_1_10_20_EN.pdf"
            pdf = fitz.open()
            pdf.new_page(width=595, height=842)  # image-only/blank page; router is controlled below
            pdf.save(pdf_path)
            pdf.close()

            long_text = ocr_text

            class FakeRouter:
                def evaluate_page(self, **kwargs):
                    return SimpleNamespace(
                        decision=PageRoutingDecision.USE_PADDLE_OCR,
                        reasons=["synthetic scanned-page regression"],
                    )

            class FakeEngine:
                def ocr_page(self, *args, **kwargs):
                    return SimpleNamespace(
                        text=long_text,
                        average_confidence=0.91 if long_text else 0.0,
                        error=ocr_error,
                        is_mock=False,
                    )

            class FakeChunker:
                def chunk_page(self, document_id, page_number, cleaned_text, **kwargs):
                    if not cleaned_text.strip():
                        return []
                    return [ChunkRecord(
                        chunk_id=f"{document_id}_p{page_number}_c0",
                        document_id=document_id,
                        page_start=page_number,
                        page_end=page_number,
                        chunk_index=0,
                        content=cleaned_text,
                        metadata=ChunkMetadata(),
                    )]

            class FakeEmbedder:
                def embed_chunks(self, chunks, **kwargs):
                    for chunk in chunks:
                        chunk.embedding = [1.0] + [0.0] * 767
                    return chunks

            output_dirs = {
                "chunks_dir": tmp_path / "output" / "chunks",
                "documents_dir": tmp_path / "output" / "documents",
                "logss_dir": tmp_path / "output" / "logss",
                "reports_dir": tmp_path / "output" / "reports",
            }
            for directory in output_dirs.values():
                directory.mkdir(parents=True, exist_ok=True)

            with patch.multiple(config_module.config, **output_dirs):
                pipeline = IngestionPipeline(enable_db=False)
                pipeline._router = FakeRouter()
                pipeline._engine = FakeEngine()
                pipeline.chunker = FakeChunker()
                pipeline.embedder = FakeEmbedder()
                return pipeline.process_pdf(str(pdf_path), year=2024, save_db=False)

    def test_unknown_year_is_rejected_instead_of_fabricated(self):
        from pipeline import IngestionPipeline
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "judgment_without_year.pdf"
            pdf = fitz.open()
            pdf.new_page()
            pdf.save(pdf_path)
            pdf.close()
            with self.assertRaisesRegex(ValueError, "year is unknown"):
                IngestionPipeline(enable_db=False).process_pdf(str(pdf_path), save_db=False)

    def test_scanned_page_ocr_text_reaches_chunk(self):
        text = (
            "The appeal is dismissed. The operative order and reasoning are preserved. "
            "The court considered the submissions and finds no merit in the appeal. "
            "The petition is accordingly dismissed with no order as to costs."
        )
        record = self._run_pipeline(text)
        self.assertEqual(len(record.pages), 1)
        self.assertEqual(record.pages[0].extraction_method, "paddle_ocr")
        self.assertIn("operative order", record.chunks[0].content)
        self.assertEqual(record.chunks[0].page_start, record.chunks[0].page_end)

    def test_required_ocr_failure_does_not_return_a_successful_record(self):
        with self.assertRaisesRegex(RuntimeError, "Required OCR failed|No verified text"):
            self._run_pipeline("", ocr_error="synthetic OCR engine failure")


if __name__ == "__main__":
    unittest.main(verbosity=2)
