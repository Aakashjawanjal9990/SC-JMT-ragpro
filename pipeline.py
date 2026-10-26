"""
Production Ingestion Pipeline Orchestrator (PCE v2.0)
Scales across 43,000+ Supreme Court judgments (1950 historical scans & 2026 digital).
"""

import os
import sys
import json
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

import fitz  # PyMuPDF

# Add parent dir to sys.path so PCEV2 can be run standalone or imported
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

try:
    from PCEV2.config import config
    from PCEV2.core.models import DocumentRecord
    from PCEV2.parsing.zone_extractor import CoordinateZoneExtractor
    from PCEV2.parsing.case_boundaries import CaseBoundaryDetector
    from PCEV2.parsing.metadata_extractor import LegalMetadataExtractor
    from PCEV2.parsing.structure_tracker import LawReportStructureTracker
    from PCEV2.chunking.page_chunker import PageChunker
    from PCEV2.embedding.vertex_embedder import VertexEmbedder
    from PCEV2.storage.db_loader import DatabaseLoader
    from PCEV2.storage.stage_tracker import DocumentStageTracker, export_verification_csv
    from PCEV2.storage.gcs_loader import GCSDataLoader
except ImportError:
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


class IngestionPipeline:
    """Orchestrates layout-aware parsing, chunking, embedding, and storage."""

    def __init__(self, db_url: Optional[str] = None, enable_db: bool = True):
        self.loader = DatabaseLoader(db_url=db_url) if enable_db else None
        self.embedder = VertexEmbedder()
        self.chunker = PageChunker(
            target_chunk_chars=config.target_chunk_chars,
            max_chunk_chars=config.max_chunk_chars,
            min_chunk_chars=config.min_chunk_chars
        )

    def process_pdf(self, pdf_path: str, year: Optional[int] = None, save_db: bool = True) -> DocumentRecord:
        """Processes a single PDF document through all 4 stages idempotently. Supports local paths and gs:// URIs."""
        temp_file_to_clean = None
        is_gcs = str(pdf_path).startswith("gs://")
        
        if is_gcs:
            gcs_loader = GCSDataLoader()
            local_pdf_path, detected_year = gcs_loader.download_to_temp(str(pdf_path))
            temp_file_to_clean = local_pdf_path
            path = Path(local_pdf_path)
            year = year or detected_year
            doc_id = Path(pdf_path).stem
        else:
            path = Path(pdf_path)
            doc_id = path.stem
            year = year or 2026

        t0 = time.time()
        doc = fitz.open(path)
        try:
            total_pages = len(doc)
            if total_pages == 0:
                raise ValueError(f"Empty PDF document: {pdf_path}")

            # Extract opening metadata from first page
            p0_text = doc[0].get_text()
            p0_clean, is_sh_p0 = CaseBoundaryDetector.split_shared_page(p0_text, is_end_of_doc=False)
            doc_meta = LegalMetadataExtractor.extract_metadata(p0_clean)
            case_title = f"{doc_meta.appellant or 'Appellant'} v. {doc_meta.respondent or 'Respondent'}"

            # Initialize 4-Stage Lifecycle Tracker
            tracker = DocumentStageTracker(
                document_id=doc_id,
                file_path=str(pdf_path),
                year=year,
                case_title=case_title
            )

            # -------------------------------------------------------------
            # STAGE 1: Parsing & Coordinate Zone Extraction
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
            tracker.finalize(total_pages=total_pages, total_chunks=len(all_chunks), ocr_quality=avg_q)
            try:
                export_verification_csv(config.logss_dir / "pipeline_verification_master.csv")
            except Exception:
                pass

        finally:
            doc.close()
            if temp_file_to_clean and os.path.exists(temp_file_to_clean):
                try:
                    os.remove(temp_file_to_clean)
                except OSError:
                    pass

        return doc_record
