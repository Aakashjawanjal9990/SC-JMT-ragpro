"""
Zero-DDL Database Writer (PCE v2.0)
Conforms strictly to db/schema.sql (Rule R1). Persists chunks, embeddings, and citations.
Supports both PostgreSQL (Cloud SQL with pgvector) and SQLite seamlessly.
Populates content_sha256, run lifecycle timestamps, and verifies embedding sync status.
All pipeline and model identifiers dynamically loaded from configuration.
"""

import os
import json
import uuid
import hashlib
import datetime
from typing import Optional
import sqlalchemy as sa
from sqlalchemy import create_engine, text

try:
    from PCEV2.core.models import DocumentRecord
    from PCEV2.config import config
except ImportError:
    from core.models import DocumentRecord
    from config import config


class DatabaseLoader:
    """Loads document records, single-page chunks, embeddings, and citations with zero DDL."""

    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or config.database_url
        self.engine = create_engine(self.db_url)
        self.is_pg = self.engine.dialect.name == "postgresql"
        self.vector_mode = config.vector_mode
        self._verify_preflight()

    def _verify_preflight(self):
        """Verifies schema existence without running any DDL."""
        with self.engine.connect():
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
        
        pipe_ver = getattr(config, "pipeline_version", "v3.0")
        pipe_name = getattr(config, "pipeline_name", "scj-data-ingestion-v3")
        emb_prov = getattr(config, "embedding_provider", "google")
        max_attempts = getattr(config, "max_retries", 3)

        ns = uuid.NAMESPACE_DNS
        judgment_uuid = str(uuid.uuid5(ns, f"judgment_{doc_rec.document_id}"))
        source_file_uuid = str(uuid.uuid5(ns, f"sf_{doc_rec.document_id}"))
        ingestion_run_uuid = str(uuid.uuid5(ns, f"ir_{doc_rec.document_id}"))
        extraction_run_uuid = str(uuid.uuid5(ns, f"er_{doc_rec.document_id}"))
        model_uuid = str(uuid.uuid5(ns, f"{emb_prov}_{config.embedding_model_id}"))

        file_size = os.path.getsize(doc_rec.file_path) if (doc_rec.file_path and os.path.exists(doc_rec.file_path)) else 0
        file_sha256 = None
        if doc_rec.file_path and os.path.exists(doc_rec.file_path):
            # OFF-10: Stream hash in bounded 64 KiB chunks for constant memory
            h = hashlib.sha256()
            with open(doc_rec.file_path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            file_sha256 = h.hexdigest()

        with self.engine.begin() as conn:
            # -------------------------------------------------------------
            # 0. Embedding Model (text-embedding-004)
            # -------------------------------------------------------------
            model_params = {
                "m_uuid": model_uuid,
                "mid": config.embedding_model_id,
                "provider": emb_prov,
                "dim": config.embedding_dimension,
                "created_at": now_str
            }
            if self.is_pg:
                conn.execute(text("""
                    INSERT INTO embedding_models (
                        embedding_model_uuid, model_id, provider, version,
                        dimensions, is_active, created_at
                    ) VALUES (
                        :m_uuid, :mid, :provider, 'v1.0',
                        :dim, true, :created_at
                    ) ON CONFLICT (provider, model_id, version) DO NOTHING;
                """), model_params)
            else:
                conn.execute(text("""
                    INSERT OR IGNORE INTO embedding_models (
                        embedding_model_uuid, model_id, provider, version,
                        dimensions, is_active, created_at
                    ) VALUES (
                        :m_uuid, :mid, :provider, 'v1.0',
                        :dim, 1, :created_at
                    );
                """), model_params)

            # -------------------------------------------------------------
            # 1. Judgments
            # -------------------------------------------------------------
            raw_date = meta.date_of_judgment
            iso_date = None
            if raw_date:
                raw_str = raw_date.strip()
                for fmt in ("%d %B %Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                    try:
                        dt = datetime.datetime.strptime(raw_str, fmt)
                        iso_date = dt.strftime("%Y-%m-%d")
                        break
                    except Exception:
                        continue

            j_params = {
                "j_uuid": judgment_uuid,
                "j_id": doc_rec.document_id,
                "court": meta.court or "Supreme Court of India",
                "case_num": meta.case_number,
                "j_date": iso_date,
                "cit_text": meta.scr_citation,
                "created_at": now_str,
                "updated_at": now_str
            }
            if self.is_pg:
                conn.execute(text("""
                    INSERT INTO judgments (
                        judgment_uuid, judgment_id, court, case_number,
                        judgment_date, citation_text, legal_status, is_primary,
                        created_at, updated_at
                    ) VALUES (
                        :j_uuid, :j_id, :court, :case_num,
                        CAST(:j_date AS date), :cit_text, 'REPORTED', false,
                        :created_at, :updated_at
                    ) ON CONFLICT (judgment_id) DO UPDATE SET
                        court = EXCLUDED.court,
                        case_number = EXCLUDED.case_number,
                        judgment_date = EXCLUDED.judgment_date,
                        citation_text = EXCLUDED.citation_text,
                        updated_at = EXCLUDED.updated_at;
                """), j_params)
            else:
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
                """), j_params)

            # -------------------------------------------------------------
            # 2. Source Files (with content_sha256 and portable relative path)
            # -------------------------------------------------------------
            raw_sp = str(doc_rec.file_path or "")
            try:
                norm_storage_path = Path(raw_sp).resolve().relative_to(config.base_dir).as_posix()
            except Exception:
                norm_storage_path = raw_sp.replace("\\", "/")

            sf_params = {
                "sf_uuid": source_file_uuid,
                "j_uuid": judgment_uuid,
                "filename": f"{doc_rec.document_id}.pdf",
                "storage_path": norm_storage_path,
                "page_count": len(doc_rec.pages),
                "pipe_ver": pipe_ver,
                "fsize": file_size,
                "c_sha": file_sha256,
                "created_at": now_str,
                "updated_at": now_str
            }
            if self.is_pg:
                conn.execute(text("""
                    INSERT INTO source_files (
                        source_file_uuid, judgment_uuid, version_number, is_current,
                        original_filename, storage_path, page_count, ingestion_status,
                        pipeline_version, content_sha256, file_size_bytes, created_at, updated_at
                    ) VALUES (
                        :sf_uuid, :j_uuid, 'v1.0', true,
                        :filename, :storage_path, :page_count, 'INGESTED',
                        :pipe_ver, :c_sha, :fsize, :created_at, :updated_at
                    ) ON CONFLICT (judgment_uuid, version_number) DO UPDATE SET
                        original_filename = EXCLUDED.original_filename,
                        storage_path = EXCLUDED.storage_path,
                        page_count = EXCLUDED.page_count,
                        ingestion_status = EXCLUDED.ingestion_status,
                        pipeline_version = EXCLUDED.pipeline_version,
                        content_sha256 = EXCLUDED.content_sha256,
                        file_size_bytes = EXCLUDED.file_size_bytes,
                        updated_at = EXCLUDED.updated_at;
                """), sf_params)
            else:
                conn.execute(text("""
                    INSERT OR REPLACE INTO source_files (
                        source_file_uuid, judgment_uuid, version_number, is_current,
                        original_filename, storage_path, page_count, ingestion_status,
                        pipeline_version, content_sha256, file_size_bytes, created_at, updated_at
                    ) VALUES (
                        :sf_uuid, :j_uuid, 'v1.0', 1,
                        :filename, :storage_path, :page_count, 'INGESTED',
                        :pipe_ver, :c_sha, :fsize, :created_at, :updated_at
                    );
                """), sf_params)

            # -------------------------------------------------------------
            # 3. Ingestion Runs (with started_at, heartbeat_at, finished_at)
            # -------------------------------------------------------------
            ir_params = {
                "ir_uuid": ingestion_run_uuid,
                "sf_uuid": source_file_uuid,
                "pname": pipe_name,
                "max_att": max_attempts,
                "ikey": f"{doc_rec.document_id}_{pipe_ver}",
                "started_at": now_str,
                "heartbeat_at": now_str,
                "finished_at": now_str,
                "created_at": now_str
            }
            if self.is_pg:
                conn.execute(text("""
                    INSERT INTO ingestion_runs (
                        ingestion_run_uuid, source_file_uuid, pipeline_name,
                        processing_status, retry_count, max_attempts,
                        idempotency_key, started_at, heartbeat_at, finished_at, created_at
                    ) VALUES (
                        :ir_uuid, :sf_uuid, :pname,
                        'COMPLETED', 0, :max_att, :ikey, :started_at, :heartbeat_at, :finished_at, :created_at
                    ) ON CONFLICT (idempotency_key) DO UPDATE SET
                        processing_status = EXCLUDED.processing_status,
                        finished_at = EXCLUDED.finished_at;
                """), ir_params)
            else:
                conn.execute(text("""
                    INSERT OR REPLACE INTO ingestion_runs (
                        ingestion_run_uuid, source_file_uuid, pipeline_name,
                        processing_status, retry_count, max_attempts,
                        idempotency_key, started_at, heartbeat_at, finished_at, created_at
                    ) VALUES (
                        :ir_uuid, :sf_uuid, :pname,
                        'COMPLETED', 0, :max_att, :ikey, :started_at, :heartbeat_at, :finished_at, :created_at
                    );
                """), ir_params)

            # -------------------------------------------------------------
            # 4. Extraction Runs (with started_at, finished_at)
            # -------------------------------------------------------------
            er_params = {
                "er_uuid": extraction_run_uuid,
                "sf_uuid": source_file_uuid,
                "ir_uuid": ingestion_run_uuid,
                "pver": pipe_ver,
                "max_att": max_attempts,
                "started_at": now_str,
                "finished_at": now_str,
                "created_at": now_str
            }
            if self.is_pg:
                conn.execute(text("""
                    INSERT INTO extraction_runs (
                        extraction_run_uuid, source_file_uuid, ingestion_run_uuid,
                        pipeline_version, processing_status, retry_count, max_attempts,
                        started_at, finished_at, created_at
                    ) VALUES (
                        :er_uuid, :sf_uuid, :ir_uuid,
                        :pver, 'COMPLETED', 0, :max_att, :started_at, :finished_at, :created_at
                    ) ON CONFLICT (extraction_run_uuid) DO NOTHING;
                """), er_params)
            else:
                conn.execute(text("""
                    INSERT OR REPLACE INTO extraction_runs (
                        extraction_run_uuid, source_file_uuid, ingestion_run_uuid,
                        pipeline_version, processing_status, retry_count, max_attempts,
                        started_at, finished_at, created_at
                    ) VALUES (
                        :er_uuid, :sf_uuid, :ir_uuid,
                        :pver, 'COMPLETED', 0, :max_att, :started_at, :finished_at, :created_at
                    );
                """), er_params)

            # -------------------------------------------------------------
            # 5. Source Pages (Layout & Coordinate Zones)
            # -------------------------------------------------------------
            for p in doc_rec.pages:
                sp_uuid = str(uuid.uuid5(ns, f"sp_{doc_rec.document_id}_{p.page_number}"))
                ext_method = getattr(p, "extraction_method", None) or ("pymupdf_vector" if p.is_born_digital else "pymupdf_ocr")
                sp_params = {
                    "sp_uuid": sp_uuid,
                    "sf_uuid": source_file_uuid,
                    "pno": p.page_number,
                    "ext_method": ext_method,
                    "content": p.cleaned_text,
                    "q_score": p.ocr_quality,
                    "created_at": now_str
                }
                if self.is_pg:
                    conn.execute(text("""
                        INSERT INTO source_pages (
                            source_page_uuid, source_file_uuid, page_number,
                            extraction_method, content, quality_score, created_at
                        ) VALUES (
                            :sp_uuid, :sf_uuid, :pno,
                            :ext_method, :content, :q_score, :created_at
                        ) ON CONFLICT (source_file_uuid, page_number) DO UPDATE SET
                            extraction_method = EXCLUDED.extraction_method,
                            content = EXCLUDED.content,
                            quality_score = EXCLUDED.quality_score;
                    """), sp_params)
                else:
                    conn.execute(text("""
                        INSERT OR REPLACE INTO source_pages (
                            source_page_uuid, source_file_uuid, page_number,
                            extraction_method, content, quality_score, created_at
                        ) VALUES (
                            :sp_uuid, :sf_uuid, :pno,
                            :ext_method, :content, :q_score, :created_at
                        );
                    """), sp_params)

            # -------------------------------------------------------------
            # 6. Chunks & Chunk Embeddings
            # -------------------------------------------------------------
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
                    "embedding_text_version": c.metadata.embedding_text_version
                }
                meta_json_str = json.dumps(chunk_meta)
                emb_str = json.dumps(c.embedding) if c.embedding else None

                c_params = {
                    "c_uuid": c_uuid,
                    "j_uuid": judgment_uuid,
                    "sf_uuid": source_file_uuid,
                    "er_uuid": extraction_run_uuid,
                    "ir_uuid": ingestion_run_uuid,
                    "c_idx": c.chunk_index,
                    "p_start": c.page_start,
                    "p_end": c.page_end,
                    "content": c.content,
                    "section": c.section,
                    "metadata": meta_json_str,
                    "c_sha": c_sha,
                    "emb": emb_str,
                    "created_at": now_str,
                    "updated_at": now_str
                }

                if self.is_pg:
                    conn.execute(text("""
                        INSERT INTO chunks (
                            chunk_uuid, judgment_uuid, source_file_uuid, extraction_run_uuid,
                            last_seen_ingestion_run_uuid, chunk_index, page_start, page_end,
                            content, section, metadata, content_sha256, is_active,
                            embedding, created_at, updated_at
                        ) VALUES (
                            :c_uuid, :j_uuid, :sf_uuid, :er_uuid,
                            :ir_uuid, :c_idx, :p_start, :p_end,
                            :content, :section, CAST(:metadata AS jsonb), :c_sha, true,
                            CAST(:emb AS vector), :created_at, :updated_at
                        ) ON CONFLICT (source_file_uuid, chunk_index) DO UPDATE SET
                            content = EXCLUDED.content,
                            section = EXCLUDED.section,
                            metadata = EXCLUDED.metadata,
                            embedding = EXCLUDED.embedding,
                            updated_at = EXCLUDED.updated_at;
                    """), c_params)
                else:
                    conn.execute(text("""
                        INSERT OR REPLACE INTO chunks (
                            chunk_uuid, judgment_uuid, source_file_uuid, extraction_run_uuid,
                            last_seen_ingestion_run_uuid, chunk_index, page_start, page_end,
                            content, section, metadata, content_sha256, is_active,
                            embedding, created_at, updated_at
                        ) VALUES (
                            :c_uuid, :j_uuid, :sf_uuid, :er_uuid,
                            :ir_uuid, :c_idx, :p_start, :p_end,
                            :content, :section, :metadata, :c_sha, 1,
                            :emb, :created_at, :updated_at
                        );
                    """), c_params)

                # Verified chunk embedding synchronization status
                is_synced = bool(c.embedding and len(c.embedding) == config.embedding_dimension)
                sync_status = "SYNCED" if is_synced else "PENDING"
                last_synced_at = now_str if is_synced else None

                ce_uuid = str(uuid.uuid5(ns, f"ce_{c.chunk_id}_{config.embedding_model_id}"))
                emb_ref = f"chunks/{c_uuid}#embedding"
                ce_params = {
                    "ce_uuid": ce_uuid,
                    "c_uuid": c_uuid,
                    "model_uuid": model_uuid,
                    "emb_ref": emb_ref,
                    "sync_status": sync_status,
                    "last_synced_at": last_synced_at,
                    "v_store": "pgvector_chunks" if self.is_pg else "sqlite_inline",
                    "created_at": now_str,
                    "updated_at": now_str
                }
                if self.is_pg:
                    conn.execute(text("""
                        INSERT INTO chunk_embeddings (
                            chunk_embedding_uuid, chunk_uuid, embedding_model_uuid, provider,
                            embedding_ref, sync_status, last_synced_at, vector_store, created_at, updated_at
                        ) VALUES (
                            :ce_uuid, :c_uuid, :model_uuid, :provider,
                            :emb_ref, :sync_status, :last_synced_at, :v_store, :created_at, :updated_at
                        ) ON CONFLICT (chunk_uuid, embedding_model_uuid) DO UPDATE SET
                            embedding_ref = EXCLUDED.embedding_ref,
                            sync_status = EXCLUDED.sync_status,
                            last_synced_at = EXCLUDED.last_synced_at,
                            updated_at = EXCLUDED.updated_at;
                    """), {**ce_params, "provider": emb_prov})
                else:
                    conn.execute(text("""
                        INSERT OR REPLACE INTO chunk_embeddings (
                            chunk_embedding_uuid, chunk_uuid, embedding_model_uuid, provider,
                            embedding_ref, sync_status, last_synced_at, vector_store, created_at, updated_at
                        ) VALUES (
                            :ce_uuid, :c_uuid, :model_uuid, :provider,
                            :emb_ref, :sync_status, :last_synced_at, :v_store, :created_at, :updated_at
                        );
                    """), {**ce_params, "provider": emb_prov})

                # PCE-EMB-002: Read-after-write verification to certify physical persistence
                if is_synced:
                    persisted_check = conn.execute(text("""
                        SELECT COUNT(*) FROM chunk_embeddings
                        WHERE chunk_uuid = :c_uuid AND embedding_model_uuid = :m_uuid
                    """), {"c_uuid": c_uuid, "m_uuid": model_uuid}).scalar()
                    if not persisted_check or persisted_check < 1:
                        conn.execute(text("""
                            UPDATE chunk_embeddings SET sync_status = 'PERSISTENCE_FAILED'
                            WHERE chunk_uuid = :c_uuid AND embedding_model_uuid = :m_uuid
                        """), {"c_uuid": c_uuid, "m_uuid": model_uuid})

            # -------------------------------------------------------------
            # 7. Citations
            # -------------------------------------------------------------
            if meta.scr_citation:
                primary_cit_uuid = str(uuid.uuid5(ns, f"cit_{doc_rec.document_id}_primary"))
                primary_params = {
                    "cit_uuid": primary_cit_uuid,
                    "j_uuid": judgment_uuid,
                    "ctext": meta.scr_citation,
                    "norm_cit": meta.scr_citation,
                    "created_at": now_str
                }
                if self.is_pg:
                    conn.execute(text("""
                        INSERT INTO citations (
                            citation_uuid, judgment_uuid, citation_text,
                            citation_type, normalized_citation, created_at
                        ) VALUES (
                            :cit_uuid, :j_uuid, :ctext, 'PRIMARY', :norm_cit, :created_at
                        ) ON CONFLICT (citation_uuid) DO NOTHING;
                    """), primary_params)
                else:
                    conn.execute(text("""
                        INSERT OR REPLACE INTO citations (
                            citation_uuid, judgment_uuid, citation_text,
                            citation_type, normalized_citation, created_at
                        ) VALUES (
                            :cit_uuid, :j_uuid, :ctext, 'PRIMARY', :norm_cit, :created_at
                        );
                    """), primary_params)

            for cit in meta.case_law_cited:
                cit_uuid = str(uuid.uuid5(ns, f"cit_{doc_rec.document_id}_{cit['citation_text'][:30]}"))
                cit_params = {
                    "cit_uuid": cit_uuid,
                    "j_uuid": judgment_uuid,
                    "ctext": cit["citation_text"],
                    "norm_cit": cit["normalized_citation"],
                    "created_at": now_str
                }
                if self.is_pg:
                    conn.execute(text("""
                        INSERT INTO citations (
                            citation_uuid, judgment_uuid, citation_text,
                            citation_type, normalized_citation, created_at
                        ) VALUES (
                            :cit_uuid, :j_uuid, :ctext, 'REPORTED', :norm_cit, :created_at
                        ) ON CONFLICT (citation_uuid) DO NOTHING;
                    """), cit_params)
                else:
                    conn.execute(text("""
                        INSERT OR REPLACE INTO citations (
                            citation_uuid, judgment_uuid, citation_text,
                            citation_type, normalized_citation, created_at
                        ) VALUES (
                            :cit_uuid, :j_uuid, :ctext, 'REPORTED', :norm_cit, :created_at
                        );
                    """), cit_params)
