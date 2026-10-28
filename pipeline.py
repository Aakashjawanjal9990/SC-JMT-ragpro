"""
Production Ingestion Pipeline Orchestrator (PCE v2.0)
Scales across Supreme Court judgments from configured historical and current datasets.
"""

import os
import sys
import json
import time
import logging
import hashlib
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Optional

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
from storage.stage_tracker import DocumentStageTracker, export_verification_csv, safe_filename
from storage.gcs_loader import GCSDataLoader
from local_ocr.router import LocalOCRRouter, PageRoutingDecision
from local_ocr.engine import LocalOCREngine


def _sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    """Hash source bytes in bounded memory for local and downloaded GCS PDFs."""
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: Any, *, indent: Optional[int] = None) -> None:
    """Write JSON via a same-directory temporary file to avoid partial artifacts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=indent, separators=None if indent else (",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


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
        2. 8-Rule Conditional OCR Routing & Local PaddleOCR layout extraction
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
        if pdf_path.startswith("gs://"):
            # Temporary download names are random; identity must come from the
            # stable object path so repeated GCS ingestions remain idempotent.
            gcs_object = pdf_path[5:].split("/", 1)[1] if "/" in pdf_path[5:] else ""
            doc_id = Path(gcs_object).stem if gcs_object else p_obj.stem
        else:
            doc_id = p_obj.stem
        if year is None:
            parts = doc_id.split("_")
            candidate_year = int(parts[0]) if parts and parts[0].isdigit() else None
            year = candidate_year if candidate_year and 1800 <= candidate_year <= 2200 else None
        if year is None:
            if temp_file_to_clean and os.path.exists(temp_file_to_clean):
                os.remove(temp_file_to_clean)
            raise ValueError(
                f"Judgment year is unknown for {pdf_path!r}. Pass --year or use a filename/GCS path containing the four-digit judgment year; refusing to assign a fabricated year."
            )

        tracker = DocumentStageTracker(document_id=doc_id, file_path=str(pdf_path), year=year, case_title=doc_id)
        source_file_size = 0
        source_sha256 = None

        try:
            tracker.start_stage("provenance")
            source_file_size = os.path.getsize(active_path)
            source_sha256 = _sha256_file(active_path)
            doc = fitz.open(active_path)
            total_pages = len(doc)
            if total_pages == 0:
                raise ValueError(f"PDF contains no pages: {active_path}")

            # Extract metadata from native front matter. Scanned first pages may
            # initially have no native text; metadata is enriched again after OCR.
            native_front_matter = "\n".join(
                (doc[i].get_text("text") or "") for i in range(min(2, total_pages))
            )
            doc_meta = LegalMetadataExtractor.extract_metadata(native_front_matter)
            tracker.complete_stage("provenance")

            # -------------------------------------------------------------
            # STAGE 1: Parsing, Coordinate Zoning & Conditional OCR
            # -------------------------------------------------------------
            tracker.start_stage("parsing")
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

                # Evaluate page routing. A page requiring OCR must never silently
                # flow downstream as an empty/verified-blank page when OCR fails.
                routing_res = self.router.evaluate_page(
                    page=page_obj,
                    page_number=pno,
                    total_pages=total_pages,
                    document_id=doc_id
                )
                native_text = (pz.cleaned_text or "").strip()
                decision = routing_res.decision
                ocr_required = decision == PageRoutingDecision.USE_PADDLE_OCR
                ocr_attempted = decision in (
                    PageRoutingDecision.USE_PADDLE_OCR,
                    PageRoutingDecision.USE_PP_STRUCTURE,
                    PageRoutingDecision.FLAG_FOR_REVIEW,
                )

                if decision == PageRoutingDecision.SKIP_BLANK:
                    pz.cleaned_text = ""
                    pz.extraction_method = "verified_blank"
                    pz.ocr_quality = 1.0
                elif decision == PageRoutingDecision.SKIP_NATIVE_OK:
                    if not native_text:
                        raw_text = (page_obj.get_text("text") or "").strip()
                        if raw_text:
                            pz.cleaned_text = raw_text
                            pz.extraction_method = "pymupdf_fallback"
                            pz.ocr_quality = CoordinateZoneExtractor.calculate_ocr_quality(
                                raw_text, pz.is_born_digital
                            )
                        else:
                            # Router/native disagreement is not proof of a blank page.
                            pz.extraction_method = "needs_review"
                            pz.ocr_quality = 0.0
                elif ocr_attempted:
                    ocr_res = None
                    try:
                        if decision == PageRoutingDecision.USE_PP_STRUCTURE:
                            structure_res = self.engine.structure_page(
                                page_obj, page_number=pno,
                                allow_mock=config.allow_mock_embeddings
                            )
                            structured_text = (getattr(structure_res, "markdown_text", "") or "").strip()
                            if structured_text:
                                pz.cleaned_text = structured_text
                                pz.extraction_method = "pp_structure"
                                pz.ocr_quality = 1.0
                            elif native_text:
                                # Preserve valid native text if layout recovery yields nothing.
                                pz.cleaned_text = native_text
                                pz.extraction_method = "native_layout_fallback_review"
                                pz.ocr_quality = min(float(getattr(pz, "ocr_quality", 0.0) or 0.0), 0.6)
                            else:
                                ocr_res = self.engine.ocr_page(
                                    page_obj, page_number=pno,
                                    allow_mock=config.allow_mock_embeddings
                                )
                        else:
                            ocr_res = self.engine.ocr_page(
                                page_obj, page_number=pno,
                                allow_mock=config.allow_mock_embeddings
                            )

                        if ocr_res is not None:
                            ocr_text = (getattr(ocr_res, "text", "") or "").strip()
                            ocr_error = getattr(ocr_res, "error", None)
                            if ocr_text:
                                # OCR replaces native text only when routing says OCR is
                                # required or native text is absent. Otherwise preserve the
                                # native text to avoid throwing away better extraction.
                                if ocr_required or not native_text:
                                    pz.cleaned_text = ocr_text
                                    pz.extraction_method = "paddle_ocr"
                                    confidence = float(getattr(ocr_res, "average_confidence", 0.0) or 0.0)
                                    pz.ocr_quality = max(0.0, min(1.0, confidence))
                                elif native_text:
                                    pz.cleaned_text = native_text
                                    pz.extraction_method = "native_text_ocr_reviewed"
                            elif ocr_error:
                                logger.warning("OCR failed for %s page %s: %s", doc_id, pno, ocr_error)
                                if native_text and not ocr_required:
                                    pz.cleaned_text = native_text
                                    pz.extraction_method = "native_text_ocr_failed_review"
                                    pz.ocr_quality = min(float(getattr(pz, "ocr_quality", 0.0) or 0.0), 0.5)
                                else:
                                    pz.extraction_method = "ocr_failed"
                                    pz.ocr_quality = 0.0
                            elif ocr_required:
                                pz.extraction_method = "ocr_failed"
                                pz.ocr_quality = 0.0
                    except Exception:
                        logger.exception("OCR/layout processing failed for %s page %s", doc_id, pno)
                        if native_text and not ocr_required:
                            pz.cleaned_text = native_text
                            pz.extraction_method = "native_text_ocr_failed_review"
                            pz.ocr_quality = min(float(getattr(pz, "ocr_quality", 0.0) or 0.0), 0.5)
                        else:
                            pz.extraction_method = "ocr_failed"
                            pz.ocr_quality = 0.0

                    if not pz.cleaned_text.strip() and decision != PageRoutingDecision.SKIP_BLANK:
                        if ocr_required or not native_text:
                            # A missing OCR result or an unclassified empty page is not
                            # evidence that the page is blank. Do not persist an incomplete
                            # judgment; the batch layer records the failure.
                            raise RuntimeError(
                                f"No verified text for document {doc_id}, physical page {pno} "
                                f"(routing={decision.value}, extraction={pz.extraction_method}); "
                                "document was not persisted."
                            )
                        pz.extraction_method = "native_text_ocr_failed_review"
                        pz.ocr_quality = min(float(getattr(pz, "ocr_quality", 0.0) or 0.0), 0.5)

                # Last-resort native extraction is allowed only when actual text exists.
                # An empty page is considered blank only when the router explicitly says so.
                if not pz.cleaned_text.strip() and pz.extraction_method not in ("verified_blank", "ocr_failed"):
                    raw_text = (page_obj.get_text("text") or "").strip()
                    if raw_text:
                        pz.cleaned_text = raw_text
                        pz.extraction_method = "pymupdf_fallback"
                        pz.ocr_quality = CoordinateZoneExtractor.calculate_ocr_quality(
                            raw_text, pz.is_born_digital
                        )
                    else:
                        pz.extraction_method = "needs_review"
                        pz.ocr_quality = 0.0

                pages_data.append(pz)

            unresolved_pages = [
                p.page_number for p in pages_data
                if not (p.cleaned_text or "").strip()
                and p.extraction_method != "verified_blank"
            ]
            if unresolved_pages:
                raise RuntimeError(
                    f"Document {doc_id} has unresolved empty pages: {unresolved_pages[:20]}. "
                    "The document was not persisted."
                )

            # Re-extract metadata using OCR-enriched text where native front matter
            # was absent. Preserve previously found fields rather than replacing them
            # with lower-confidence/missing values.
            enriched_front_matter = "\n".join(
                (p.cleaned_text or "") for p in pages_data[:2]
            )
            enriched_meta = LegalMetadataExtractor.extract_metadata(enriched_front_matter)
            for field_name in (
                "case_number", "appellant", "respondent", "date_of_judgment",
                "year", "scr_citation", "author",
            ):
                if not getattr(doc_meta, field_name, None) and getattr(enriched_meta, field_name, None):
                    setattr(doc_meta, field_name, getattr(enriched_meta, field_name))
            if not doc_meta.judges and enriched_meta.judges:
                doc_meta.judges = enriched_meta.judges
            if not doc_meta.counsel and enriched_meta.counsel:
                doc_meta.counsel = enriched_meta.counsel
            if not doc_meta.case_law_cited and enriched_meta.case_law_cited:
                doc_meta.case_law_cited = enriched_meta.case_law_cited
            if doc_meta.year:
                year = doc_meta.year if year is None else year
            case_title = " v. ".join(
                value for value in (doc_meta.appellant, doc_meta.respondent) if value
            ) or doc_id or "Supreme Court Judgment"
            tracker.case_title = case_title
            structure_tracker = LawReportStructureTracker(bench_judges=doc_meta.judges or [])
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

            if not all_chunks:
                raise RuntimeError(f"Document {doc_id} produced no retrievable text chunks; refusing to certify it as ingested.")
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
                chunks=all_chunks,
                source_sha256=source_sha256,
                file_size_bytes=source_file_size,
            )
            if save_db and self.loader:
                self.loader.save_document_record(doc_record)
            else:
                # In multiprocessing mode, the parent process owns DB persistence.
                # Do not claim the database stage succeeded before that commit occurs.
                tracker.complete_stage("vector_db", status="DEFERRED")

            # Persist chunk JSON for external auditing
            chunk_json_path = config.chunks_dir / f"{safe_filename(doc_id)}_chunks.json"
            _atomic_write_json(chunk_json_path, [{
                "chunk_id": c.chunk_id,
                "document_id": c.document_id,
                "page_start": c.page_start,
                "page_end": c.page_end,
                "chunk_index": c.chunk_index,
                "content": c.content,
                "section": c.section,
                "embedding_dim": len(c.embedding) if c.embedding else 0,
                "metadata": c.metadata.__dict__
            } for c in all_chunks])

            # Persist document JSON atomically as well.
            doc_json_path = config.documents_dir / f"{safe_filename(doc_id)}.json"
            _atomic_write_json(doc_json_path, {
                "document_id": doc_id,
                "year": year,
                "case_title": case_title,
                "total_pages": total_pages,
                "total_chunks": len(all_chunks),
                "source_sha256": source_sha256,
                "file_size_bytes": source_file_size,
                "metadata": doc_meta.__dict__
            }, indent=2)

            if save_db and self.loader:
                tracker.complete_stage("vector_db")
            # Finalize Tracker & Save Logs
            avg_q = sum(p.ocr_quality for p in pages_data) / max(1, len(pages_data))
            tracker.finalize(total_pages=total_pages, total_chunks=len(all_chunks), ocr_quality=avg_q)
            try:
                export_verification_csv(config.logss_dir / "pipeline_verification_master.csv")
            except Exception as e:
                logger.warning(f"Failed to export verification CSV: {e}")

        except Exception as exc:
            if "tracker" in locals():
                try:
                    tracker.fail_stage()
                    # Record an explicit failed lifecycle state; do not mask the
                    # original exception if the status-file write also fails.
                    tracker.finalize(
                        total_pages=(len(doc) if doc is not None else 0),
                        total_chunks=0,
                        ocr_quality=0.0,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                except Exception:
                    logger.exception("Unable to persist failed lifecycle status for %s", doc_id)
            logger.exception("Ingestion failed for %s", pdf_path)
            raise
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    logger.debug("Failed to close PDF handle for %s", pdf_path, exc_info=True)
            if temp_file_to_clean and os.path.exists(temp_file_to_clean):
                try:
                    os.remove(temp_file_to_clean)
                except OSError as e:
                    logger.warning("Could not remove temporary file %s: %s", temp_file_to_clean, e)

        return doc_record
