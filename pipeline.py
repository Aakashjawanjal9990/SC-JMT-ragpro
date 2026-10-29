"""
Production Ingestion Pipeline Orchestrator (PCE v2.0)
Scales across 43,000+ Supreme Court judgments (1950 historical scans & 2026 digital).
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# Add repo root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from config import config
from core.models import DocumentRecord
from parsing.zone_extractor import CoordinateZoneExtractor
from parsing.case_boundaries import CaseBoundaryDetector
from parsing.metadata_extractor import LegalMetadataExtractor
from parsing.structure_tracker import LawReportStructureTracker
from chunking.page_chunker import PageChunker
from embedding.vertex_embedder import VertexEmbedder
from storage.db_loader import DatabaseLoader
from storage.stage_tracker import DocumentStageTracker, export_verification_csv
from storage.gcs_loader import GCSDataLoader
from local_ocr.router import LocalOCRRouter, PageRoutingDecision
from local_ocr.engine import LocalOCREngine
from local_ocr.layout_recovery import purge_ocr_running_headers


class IngestionPipeline:
    """Orchestrates layout-aware parsing, conditional OCR, chunking, embedding, and storage."""

    def __init__(self, db_url: Optional[str] = None, enable_db: bool = True):
        self.loader = DatabaseLoader(db_url=db_url) if enable_db else None
        self.embedder = VertexEmbedder()
        self.chunker = PageChunker(
            target_chunk_chars=config.target_chunk_chars,
            max_chunk_chars=config.max_chunk_chars,
            min_chunk_chars=config.min_chunk_chars
        )
        self._router = None
        self._engine = None

    @property
    def router(self) -> LocalOCRRouter:
        if self._router is None:
            self._router = LocalOCRRouter()
        return self._router

    @property
    def engine(self) -> LocalOCREngine:
        if self._engine is None:
            self._engine = LocalOCREngine()
        return self._engine

    def process_pdf(
        self,
        pdf_path: str,
        year: Optional[int] = None,
        save_db: bool = True
    ) -> Optional[DocumentRecord]:
        """
        Processes a single judgment PDF end-to-end through PCE v2.0:
        1. Parsing & Coordinate Zoning (PyMuPDF)
        2. 8-Rule Conditional OCR Routing & Google Cloud Vision OCR layout extraction
        3. Rule R2 Single-Page Paragraph-Preserving Chunking (page_start == page_end)
        4. Contextual 768-dim Vector Embedding (Vertex AI text-embedding-004)
        5. Persistence to AlloyDB (PostgreSQL 17.10) / SQLite and structured JSON deliverables.
        """
        temp_file_to_clean = None
        doc = None

        if pdf_path.startswith("gs://"):
            gcs_loader = GCSDataLoader()
            local_tmp, detected_year = gcs_loader.download_to_temp(pdf_path)
            temp_file_to_clean = local_tmp
            active_path = local_tmp
            if year is None:
                year = detected_year
        else:
            active_path = pdf_path

        p_obj = Path(active_path)
        doc_id = p_obj.stem
        if year is None:
            parts = doc_id.split("_")
            year = int(parts[0]) if parts and parts[0].isdigit() else 2026

        tracker = DocumentStageTracker(
            document_id=doc_id,
            file_path=str(active_path),
            year=year,
            case_title="Supreme Court Judgment"
        )

        try:
            tracker.start_stage("provenance")
            doc = fitz.open(active_path)
            total_pages = len(doc)
            if total_pages == 0:
                logger.warning(f"Empty document: {active_path}")
                return None

            # Extract Global Document Metadata
            doc_meta = LegalMetadataExtractor.extract_metadata_from_pdf(active_path, doc_id=doc_id)
            case_title = doc_meta.case_title or "Supreme Court Judgment"
            tracker.case_title = case_title
            tracker.complete_stage("provenance")

            # -------------------------------------------------------------
            # STAGE 1: Parsing, Coordinate Zoning & Conditional OCR
            # -------------------------------------------------------------
            tracker.start_stage("parsing")
            structure_tracker = LawReportStructureTracker(bench_judges=doc_meta.judges or [])
            id_parts = doc_id.split("_")
            start_book_p = int(id_parts[2]) if len(id_parts) >= 4 and id_parts[2].isdigit() else 1

            pages_data = []
            shared_with_map = {}
            for pno in range(1, total_pages + 1):
                page_obj = doc[pno - 1]
                pz = CoordinateZoneExtractor.extract_page_zones(
                    page=page_obj,
                    page_number=pno,
                    total_pages=total_pages,
                    start_book_page=start_book_p,
                    document_id=doc_id
                )
                # Handle shared pages on first and last page
                cl_text = pz.cleaned_text
                sh_doc = None
                if pno == 1:
                    cl_text, is_sh = CaseBoundaryDetector.split_shared_page(cl_text, is_end_of_doc=False)
                    if is_sh:
                        sh_doc = CaseBoundaryDetector.resolve_shared_document_id(doc_id, is_end_of_doc=False)
                        shared_with_map[pno] = sh_doc
                elif pno == total_pages:
                    cl_text, is_sh = CaseBoundaryDetector.split_shared_page(cl_text, is_end_of_doc=True)
                    if is_sh:
                        sh_doc = CaseBoundaryDetector.resolve_shared_document_id(doc_id, is_end_of_doc=True)
                        shared_with_map[pno] = sh_doc

                pz.cleaned_text = cl_text

                # Evaluate page routing via 8-Rule Conditional OCR Router
                routing_res = self.router.evaluate_page(
                    page=page_obj,
                    page_number=pno,
                    total_pages=total_pages,
                    document_id=doc_id
                )

                if routing_res.decision in (
                    PageRoutingDecision.USE_GOOGLE_OCR,
                    PageRoutingDecision.FLAG_FOR_REVIEW
                ):
                    try:
                        ocr_res = self.engine.ocr_page(page_obj, page_number=pno, allow_mock=config.allow_mock_embeddings)
                        raw_text = (ocr_res.text or "").strip() if ocr_res else ""
                        if raw_text:
                            cleaned = purge_ocr_running_headers(raw_text, is_first_page=(pno == 1))
                            pz.cleaned_text = CoordinateZoneExtractor.heal_ocr_degradations(cleaned)
                            pz.extraction_method = "google_vision_ocr"
                            pz.ocr_quality = max(0.70, getattr(ocr_res, "average_confidence", 0.95))
                        elif ocr_res and ocr_res.error:
                            logger.warning(f"Google Vision OCR warning on {doc_id} p.{pno}: {ocr_res.error}")
                            pz.extraction_method = "ocr_failed"
                            pz.ocr_quality = 0.0
                    except Exception as e:
                        logger.error(f"Google Vision OCR invocation failure on {doc_id} p.{pno}: {e}")
                        pz.extraction_method = "ocr_failed"
                        pz.ocr_quality = 0.0

                elif routing_res.decision == PageRoutingDecision.SKIP_BLANK:
                    pz.extraction_method = "verified_blank"
                    pz.ocr_quality = 1.0
                    pz.cleaned_text = ""

                elif routing_res.decision == PageRoutingDecision.SKIP_NATIVE_OK:
                    if not pz.cleaned_text.strip() and routing_res.reasons and "rescued_raw_text" in routing_res.reasons[0]:
                        pz.cleaned_text = (page_obj.get_text("text") or "").strip()
                        pz.extraction_method = "pymupdf_fallback"

                # Final fallback guard if text is still empty
                if not pz.cleaned_text.strip():
                    images = page_obj.get_images() if hasattr(page_obj, "get_images") else []
                    raw_chars = len((page_obj.get_text("text") or "").strip())
                    if images and pz.extraction_method not in ("ocr_failed", "google_vision_ocr", "google_vision_structure"):
                        try:
                            ocr_res = self.engine.ocr_page(page_obj, page_number=pno, allow_mock=config.allow_mock_embeddings)
                            raw_text = (ocr_res.text or "").strip() if ocr_res else ""
                            if raw_text:
                                cleaned = purge_ocr_running_headers(raw_text, is_first_page=(pno == 1))
                                pz.cleaned_text = CoordinateZoneExtractor.heal_ocr_degradations(cleaned)
                                pz.extraction_method = "google_vision_ocr"
                                pz.ocr_quality = max(0.70, getattr(ocr_res, "average_confidence", 0.95))
                            else:
                                pz.extraction_method = "ocr_failed"
                                pz.ocr_quality = 0.0
                        except Exception as ocr_err:
                            logger.warning(f"Fallback OCR rescue failed on {doc_id} p.{pno}: {ocr_err}")
                            pz.extraction_method = "ocr_failed"
                            pz.ocr_quality = 0.0
                    elif raw_chars > 0 and pz.extraction_method != "ocr_failed":
                        pz.cleaned_text = (page_obj.get_text("text") or "").strip()
                        pz.extraction_method = "pymupdf_fallback"
                        pz.ocr_quality = CoordinateZoneExtractor.calculate_ocr_quality(pz.cleaned_text, pz.is_born_digital)
                    elif pz.extraction_method != "ocr_failed":
                        pz.extraction_method = "verified_blank"
                        pz.ocr_quality = 1.0

                pages_data.append(pz)

            tracker.complete_stage("parsing")

            # -------------------------------------------------------------
            # STAGE 2: Paragraph-Preserving Single-Page Chunking
            # -------------------------------------------------------------
            tracker.start_stage("chunking")
            all_chunks = []
            prev_ended_mid = False

            for pno, pz in enumerate(pages_data, 1):
                p_chunks = self.chunker.chunk_page(
                    document_id=doc_id,
                    page_number=pno,
                    cleaned_text=pz.cleaned_text,
                    structure_tracker=structure_tracker,
                    printed_page_number=pz.printed_page_number,
                    ocr_quality=pz.ocr_quality,
                    prev_page_ended_mid=prev_ended_mid,
                    page_shared_with=shared_with_map.get(pno),
                    footnotes=pz.footnotes,
                    margin_notes=pz.margin_notes,
                    page_header=pz.page_header
                )
                if p_chunks:
                    prev_ended_mid = p_chunks[-1].metadata.ends_mid_sentence
                for c in p_chunks:
                    c.chunk_index = len(all_chunks)
                    all_chunks.append(c)

            tracker.complete_stage("chunking")

            # -------------------------------------------------------------
            # STAGE 3: Contextual 768-dim Vector Embedding
            # -------------------------------------------------------------
            tracker.start_stage("embedding")
            self.embedder.embed_chunks(all_chunks, case_title=case_title, year=year)
            tracker.complete_stage("embedding")

            # -------------------------------------------------------------
            # STAGE 4: Database Ingestion & Deliverable Persistence
            # -------------------------------------------------------------
            tracker.start_stage("vector_db")
            doc_record = DocumentRecord(
                document_id=doc_id,
                file_path=str(pdf_path),
                year=year,
                metadata=doc_meta,
                pages=pages_data,
                chunks=all_chunks
            )
            if save_db and self.loader:
                self.loader.save_document_record(doc_record)

            # Persist chunk JSON for external auditing
            chunk_json_path = config.chunks_dir / f"{doc_id}_chunks.json"
            with open(chunk_json_path, "w", encoding="utf-8") as f:
                json.dump([{
                    "chunk_id": c.chunk_id,
                    "document_id": c.document_id,
                    "page_start": c.page_start,
                    "page_end": c.page_end,
                    "chunk_index": c.chunk_index,
                    "content": c.content,
                    "section": c.section,
                    "embedding_dim": len(c.embedding) if c.embedding else 0,
                    "metadata": c.metadata.__dict__
                } for c in all_chunks], f, separators=(',', ':'))

            # Persist document JSON
            doc_json_path = config.documents_dir / f"{doc_id}.json"
            with open(doc_json_path, "w", encoding="utf-8") as f:
                json.dump({
                    "document_id": doc_id,
                    "year": year,
                    "case_title": case_title,
                    "total_pages": total_pages,
                    "total_chunks": len(all_chunks),
                    "metadata": doc_meta.__dict__
                }, f, indent=2)

            tracker.complete_stage("vector_db")
            # Finalize Tracker & Save Logs
            avg_q = sum(p.ocr_quality for p in pages_data) / max(1, len(pages_data))

            ocr_failed_pages = [p.page_number for p in pages_data if p.extraction_method == "ocr_failed"]
            if ocr_failed_pages:
                ocr_err_msg = f"Unrecoverable OCR failure on {len(ocr_failed_pages)} page(s): {ocr_failed_pages}"
                logger.error(f"Document {doc_id}: {ocr_err_msg}")
                tracker.finalize(
                    total_pages=total_pages,
                    total_chunks=len(all_chunks),
                    ocr_quality=avg_q,
                    error=ocr_err_msg
                )
                if os.getenv("STRICT_OCR_GATE", "true").lower() in ("true", "1", "yes"):
                    raise RuntimeError(f"Strict OCR Failure Gate: {ocr_err_msg} in {doc_id}")
            else:
                tracker.finalize(total_pages=total_pages, total_chunks=len(all_chunks), ocr_quality=avg_q)

            try:
                export_verification_csv(config.logss_dir / "pipeline_verification_master.csv")
            except Exception as e:
                logger.warning(f"Failed to export verification CSV: {e}")

        except Exception as e:
            logger.error(f"Pipeline error processing {doc_id}: {e}")
            tracker.finalize(
                total_pages=total_pages if "total_pages" in locals() else 0,
                total_chunks=len(all_chunks) if "all_chunks" in locals() else 0,
                ocr_quality=0.0,
                error=str(e)
            )
            try:
                export_verification_csv(config.logss_dir / "pipeline_verification_master.csv")
            except Exception:
                pass
            raise e

        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass
            if temp_file_to_clean and os.path.exists(temp_file_to_clean):
                try:
                    os.remove(temp_file_to_clean)
                except OSError as e:
                    logger.warning(f"Could not remove temp file {temp_file_to_clean}: {e}")

        return doc_record
