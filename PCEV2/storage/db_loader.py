"""
Zero-DDL Database Writer (PCE v2.0)
Conforms strictly to db/schema.sql (Rule R1). Persists chunks, embeddings, and citations.
"""

import os
import json
import uuid
import hashlib
import datetime
from typing import List, Optional
import sqlalchemy as sa
from sqlalchemy import create_engine, text

try:
    from PCEV2.core.models import DocumentRecord, ChunkRecord
    from PCEV2.config import config
except ImportError:
    from core.models import DocumentRecord, ChunkRecord
    from config import config


class DatabaseLoader:
    """Loads document records, single-page chunks, embeddings, and citations with zero DDL."""

    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or config.database_url
        self.engine = create_engine(self.db_url)
        self.vector_mode = config.vector_mode
        self._verify_preflight()

    def _verify_preflight(self):
        """Verifies schema existence without running any DDL."""
        with self.engine.connect() as conn:
            insp = sa.inspect(self.engine)
            tables = set(insp.get_table_names())
            required = {
                "embedding_models", "judgments", "source_files",
                "ingestion_runs", "extraction_runs", "source_pages",
                "chunks", "chunk_embeddings", "citations"
            }
            if not required.issubset(tables):
                raise RuntimeError(f"Database missing required tables from db/schema.sql: {required - tables}")

    def save_document_record(self, doc_rec: DocumentRecord):
        """Commits full document lifecycle records inside an atomic transaction."""
        meta = doc_rec.metadata
        now_str = datetime.datetime.utcnow().isoformat()
        
        ns = uuid.NAMESPACE_DNS
        judgment_uuid = str(uuid.uuid5(ns, f"judgment_{doc_rec.document_id}"))
        source_file_uuid = str(uuid.uuid5(ns, f"sf_{doc_rec.document_id}"))
        ingestion_run_uuid = str(uuid.uuid5(ns, f"ir_{doc_rec.document_id}"))
        extraction_run_uuid = str(uuid.uuid5(ns, f"er_{doc_rec.document_id}"))
        model_uuid = str(uuid.uuid5(ns, "vertex_text_embedding_004"))

        file_size = os.path.getsize(doc_rec.file_path) if (doc_rec.file_path and os.path.exists(doc_rec.file_path)) else 0

        with self.engine.begin() as conn:
            # 0. Ensure embedding model entry exists
            conn.execute(text("""
                INSERT OR REPLACE INTO embedding_models (
                    embedding_model_uuid, model_id, provider, version, dimensions, is_active, created_at
                ) VALUES (
                    :uuid, 'text-embedding-004', 'google', 'v1', 768, 1, :created_at
                );
            """), {"uuid": model_uuid, "created_at": now_str})

            # 1. Judgments
            conn.execute(text("""
                INSERT OR REPLACE INTO judgments (
                    judgment_uuid, judgment_id, court, case_number,
                    judgment_date, citation_text, legal_status, is_primary,
                    created_at, updated_at
                ) VALUES (
                    :j_uuid, :j_id, :court, :case_num,
                    :j_date, :cit_text, 'REPORTED', 0,
                    :created_at, :updated_at
                );
            """), {
                "j_uuid": judgment_uuid,
                "j_id": doc_rec.document_id,
                "court": meta.court,
                "case_num": meta.case_number or f"SCR/{doc_rec.document_id}",
                "j_date": meta.date_of_judgment,
                "cit_text": meta.scr_citation or f"{meta.appellant or 'Appellant'} v. {meta.respondent or 'Respondent'}",
                "created_at": now_str,
                "updated_at": now_str
            })

            # 2. Source Files
            conn.execute(text("""
                INSERT OR REPLACE INTO source_files (
                    source_file_uuid, judgment_uuid, version_number, is_current,
                    original_filename, storage_path, page_count, ingestion_status,
                    pipeline_version, file_size_bytes, created_at, updated_at
                ) VALUES (
                    :sf_uuid, :j_uuid, 'v1.0', 1,
                    :filename, :storage_path, :page_count, 'INGESTED',
                    'v3.0', :fsize, :created_at, :updated_at
                );
            """), {
                "sf_uuid": source_file_uuid,
                "j_uuid": judgment_uuid,
                "filename": f"{doc_rec.document_id}.pdf",
                "storage_path": doc_rec.file_path,
                "page_count": len(doc_rec.pages),
                "fsize": file_size,
                "created_at": now_str,
                "updated_at": now_str
            })

            # 3. Ingestion Runs (Log Table)
            conn.execute(text("""
                INSERT OR REPLACE INTO ingestion_runs (
                    ingestion_run_uuid, source_file_uuid, pipeline_name,
                    processing_status, retry_count, max_attempts,
                    idempotency_key, created_at
                ) VALUES (
                    :ir_uuid, :sf_uuid, 'scj-data-ingestion-v3',
                    'COMPLETED', 0, 3, :ikey, :created_at
                );
            """), {
                "ir_uuid": ingestion_run_uuid,
                "sf_uuid": source_file_uuid,
                "ikey": f"{doc_rec.document_id}_v3.0",
                "created_at": now_str
            })

            # 4. Extraction Runs
            conn.execute(text("""
                INSERT OR REPLACE INTO extraction_runs (
                    extraction_run_uuid, source_file_uuid, ingestion_run_uuid,
                    pipeline_version, processing_status, retry_count, max_attempts, created_at
                ) VALUES (
                    :er_uuid, :sf_uuid, :ir_uuid,
                    'v3.0', 'COMPLETED', 0, 3, :created_at
                );
            """), {
                "er_uuid": extraction_run_uuid,
                "sf_uuid": source_file_uuid,
                "ir_uuid": ingestion_run_uuid,
                "created_at": now_str
            })

            # 5. Source Pages
            for p in doc_rec.pages:
                sp_uuid = str(uuid.uuid5(ns, f"sp_{doc_rec.document_id}_{p.page_number}"))
                conn.execute(text("""
                    INSERT OR REPLACE INTO source_pages (
                        source_page_uuid, source_file_uuid, page_number,
                        extraction_method, content, quality_score, created_at
                    ) VALUES (
                        :sp_uuid, :sf_uuid, :pno,
                        :ext_method, :content, :q_score, :created_at
                    );
                """), {
                    "sp_uuid": sp_uuid,
                    "sf_uuid": source_file_uuid,
                    "pno": p.page_number,
                    "ext_method": "pymupdf_vector" if p.is_born_digital else "pymupdf_ocr",
                    "content": p.cleaned_text,
                    "q_score": p.ocr_quality,
                    "created_at": now_str
                })

            # 6. Chunks & Chunk Embeddings
            conn.execute(text("DELETE FROM chunk_embeddings WHERE chunk_uuid IN (SELECT chunk_uuid FROM chunks WHERE judgment_uuid = :j_uuid);"), {"j_uuid": judgment_uuid})
            conn.execute(text("DELETE FROM chunks WHERE judgment_uuid = :j_uuid;"), {"j_uuid": judgment_uuid})

            for c in doc_rec.chunks:
                c_uuid = str(uuid.uuid5(ns, f"chunk_{c.chunk_id}"))
                c_sha = hashlib.sha256(c.content.encode("utf-8")).hexdigest()
                chunk_meta = {
                    "chunk_id": c.chunk_id,
                    "judge_opinion": c.metadata.judge_opinion,
                    "label_confidence": c.metadata.label_confidence,
                    "ends_mid_sentence": c.metadata.ends_mid_sentence,
                    "continues_from_prev_page": c.metadata.continues_from_prev_page,
                    "printed_page_number": c.metadata.printed_page_number,
                    "page_shared_with": c.metadata.page_shared_with,
                    "footnotes": c.metadata.footnotes,
                    "margin_notes": c.metadata.margin_notes,
                    "page_header": c.metadata.page_header,
                    "chunking_version": c.metadata.chunking_version,
                    "cleaning_version": c.metadata.cleaning_version,
                    "embedding_text_version": c.metadata.embedding_text_version,
                }

                conn.execute(text("""
                    INSERT OR REPLACE INTO chunks (
                        chunk_uuid, judgment_uuid, source_file_uuid,
                        extraction_run_uuid, last_seen_ingestion_run_uuid,
                        chunk_index, page_start, page_end, content, section,
                        metadata, content_sha256, is_active, created_at, updated_at
                    ) VALUES (
                        :c_uuid, :j_uuid, :sf_uuid,
                        :er_uuid, :ir_uuid,
                        :c_idx, :p_start, :p_end, :content, :section,
                        :meta, :sha, 1, :created_at, :updated_at
                    );
                """), {
                    "c_uuid": c_uuid,
                    "j_uuid": judgment_uuid,
                    "sf_uuid": source_file_uuid,
                    "er_uuid": extraction_run_uuid,
                    "ir_uuid": ingestion_run_uuid,
                    "p_start": c.page_start,
                    "p_end": c.page_end,
                    "c_idx": c.chunk_index,
                    "content": c.content,
                    "section": c.section,
                    "meta": json.dumps(chunk_meta),
                    "sha": c_sha,
                    "created_at": now_str,
                    "updated_at": now_str
                })

                emb_ref = json.dumps(c.embedding) if c.embedding else f"vertex_vs_{c.chunk_id}"
                ce_uuid = str(uuid.uuid5(ns, f"ce_{c.chunk_id}"))
                conn.execute(text("""
                    INSERT OR REPLACE INTO chunk_embeddings (
                        chunk_embedding_uuid, chunk_uuid, embedding_model_uuid, provider,
                        embedding_ref, sync_status, vector_store, created_at, updated_at
                    ) VALUES (
                        :ce_uuid, :c_uuid, :model_uuid, 'google',
                        :emb_ref, 'SYNCED', :v_store, :created_at, :updated_at
                    );
                """), {
                    "ce_uuid": ce_uuid,
                    "c_uuid": c_uuid,
                    "model_uuid": model_uuid,
                    "emb_ref": emb_ref,
                    "v_store": "inline_json" if c.embedding else "vertex_vector_search",
                    "created_at": now_str,
                    "updated_at": now_str
                })

            # 7. Citations
            for cit in meta.case_law_cited:
                cit_uuid = str(uuid.uuid5(ns, f"cit_{doc_rec.document_id}_{cit['citation_text'][:30]}"))
                conn.execute(text("""
                    INSERT OR REPLACE INTO citations (
                        citation_uuid, judgment_uuid, citation_text,
                        citation_type, normalized_citation, created_at
                    ) VALUES (
                        :cit_uuid, :j_uuid, :ctext, 'REPORTED', :norm_cit, :created_at
                    );
                """), {
                    "cit_uuid": cit_uuid,
                    "j_uuid": judgment_uuid,
                    "ctext": cit["citation_text"],
                    "norm_cit": cit["normalized_citation"],
                    "created_at": now_str
                })
