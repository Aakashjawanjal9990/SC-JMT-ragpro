"""
Database Loader Module for Indian Supreme Court RAG Pipeline
Updated with the Official Architecture Schema for Google Cloud SQL (PostgreSQL) and Local SQLite.
Creates and populates all 9 parsing and ingestion tables:
1. judgments            (Master case metadata & citations)
2. source_files         (GCS PDF inventory & SHA-256 hashes)
3. ingestion_runs       (LOG TABLE: Pipeline orchestration, heartbeats, status)
4. extraction_runs      (LOG TABLE: Parser engine diagnostics & errors)
5. source_pages         (Page-level extraction method & raw text)
6. citations            (Statutory, constitutional & SCR citations)
7. chunks               (Page-aware text chunks with metadata & SHA-256)
8. embedding_models     (Registered embedding models: text-embedding-004, etc.)
9. chunk_embeddings     (Vector embeddings & sync status)

Author: Kedar (Data Ingestion Engineer)
"""

import os
import sys
import json
import uuid
import hashlib
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import create_engine, text

from config import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("DBLoader")

DEFAULT_SQLITE_URL = config.DATABASE_URL or "sqlite:///scj_rag.db"


class DatabaseLoader:
    def __init__(self, db_url: Optional[str] = None):
        """
        Initializes connection to PostgreSQL (Cloud SQL) or SQLite.
        Tuned with production connection pooling for PostgreSQL and WAL mode for SQLite.
        """
        self.db_url = db_url or os.environ.get("DATABASE_URL") or DEFAULT_SQLITE_URL
        self.is_sqlite = self.db_url.startswith("sqlite")
        self.is_postgres = self.db_url.startswith("postgresql") or self.db_url.startswith("postgres")

        logger.info(f"Connecting to database: {'SQLite (Local Dev)' if self.is_sqlite else 'PostgreSQL (Cloud SQL)'}")

        if self.is_postgres:
            self.engine = create_engine(
                self.db_url,
                pool_size=config.DB_POOL_SIZE,
                max_overflow=config.DB_MAX_OVERFLOW,
                pool_recycle=config.DB_POOL_RECYCLE,
                pool_pre_ping=True
            )
        else:
            self.engine = create_engine(self.db_url)

        self.init_schema()

    def init_schema(self):
        """Creates the 9 parsing-required tables and performance indexes."""
        with self.engine.begin() as conn:
            if self.is_postgres:
                # 1. Enable Required Extensions
                try:
                    conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'))
                    conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pg_trgm";'))
                    conn.execute(text('CREATE EXTENSION IF NOT EXISTS "vector";'))
                except Exception as e:
                    logger.warning(f"Could not enable PG extensions (requires superuser/cloud admin): {e}")

                # 2. Master judgments table
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS judgments (
                        judgment_uuid UUID PRIMARY KEY,
                        judgment_id TEXT NOT NULL UNIQUE,
                        court TEXT DEFAULT 'Supreme Court of India',
                        case_number TEXT,
                        judgment_date DATE,
                        citation_text TEXT,
                        reporter_citations TEXT,
                        legal_status TEXT DEFAULT 'REPORTED',
                        is_primary BOOLEAN NOT NULL DEFAULT FALSE,
                        overruled_by_judgment_uuid UUID REFERENCES judgments(judgment_uuid) ON DELETE SET NULL,
                        refresh_interval_days INTEGER,
                        last_refreshed_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                """))

                # 3. Source files table
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS source_files (
                        source_file_uuid UUID PRIMARY KEY,
                        judgment_uuid UUID NOT NULL REFERENCES judgments(judgment_uuid) ON DELETE CASCADE,
                        version_number TEXT NOT NULL DEFAULT 'v1.0',
                        is_current BOOLEAN NOT NULL DEFAULT TRUE,
                        original_filename TEXT NOT NULL,
                        storage_path TEXT NOT NULL,
                        doc_encryption_files JSONB,
                        page_count INTEGER,
                        ingestion_status TEXT NOT NULL DEFAULT 'COMPLETED',
                        pipeline_version TEXT DEFAULT 'scj-ingestion-v1',
                        created_by_user_uuid UUID,
                        content_sha256 CHAR(64),
                        file_size_bytes BIGINT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (judgment_uuid, version_number)
                    );
                """))

                # 4. Ingestion runs log table
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS ingestion_runs (
                        ingestion_run_uuid UUID PRIMARY KEY,
                        source_file_uuid UUID NOT NULL REFERENCES source_files(source_file_uuid) ON DELETE CASCADE,
                        pipeline_name TEXT NOT NULL DEFAULT 'scj-data-ingestion-v1',
                        processing_status TEXT NOT NULL,
                        error_message TEXT,
                        retry_count INTEGER NOT NULL DEFAULT 0,
                        max_attempts INTEGER NOT NULL DEFAULT 3,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        worker_id TEXT,
                        started_at TIMESTAMPTZ,
                        heartbeat_at TIMESTAMPTZ,
                        finished_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                """))

                # 5. Extraction runs log table
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS extraction_runs (
                        extraction_run_uuid UUID PRIMARY KEY,
                        source_file_uuid UUID NOT NULL REFERENCES source_files(source_file_uuid) ON DELETE CASCADE,
                        ingestion_run_uuid UUID REFERENCES ingestion_runs(ingestion_run_uuid) ON DELETE SET NULL,
                        pipeline_version TEXT NOT NULL DEFAULT 'pymupdf',
                        processing_status TEXT NOT NULL DEFAULT 'COMPLETED',
                        error_message TEXT,
                        retry_count INTEGER NOT NULL DEFAULT 0,
                        max_attempts INTEGER NOT NULL DEFAULT 3,
                        started_at TIMESTAMPTZ,
                        finished_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                """))

                # 6. Source pages table
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS source_pages (
                        source_page_uuid UUID PRIMARY KEY,
                        source_file_uuid UUID NOT NULL REFERENCES source_files(source_file_uuid) ON DELETE CASCADE,
                        page_number INTEGER NOT NULL,
                        extraction_method TEXT NOT NULL DEFAULT 'pymupdf',
                        content TEXT,
                        quality_score NUMERIC DEFAULT 0.95,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (source_file_uuid, page_number)
                    );
                """))

                # 7. Citations table
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS citations (
                        citation_uuid UUID PRIMARY KEY,
                        judgment_uuid UUID NOT NULL REFERENCES judgments(judgment_uuid) ON DELETE CASCADE,
                        citation_text TEXT NOT NULL,
                        citation_type TEXT NOT NULL DEFAULT 'REPORTER',
                        normalized_citation TEXT,
                        source_locator TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                """))

                # 8. Chunks table
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS chunks (
                        chunk_uuid UUID PRIMARY KEY,
                        chunk_id VARCHAR(120),
                        judgment_uuid UUID NOT NULL REFERENCES judgments(judgment_uuid) ON DELETE CASCADE,
                        source_file_uuid UUID NOT NULL REFERENCES source_files(source_file_uuid) ON DELETE CASCADE,
                        extraction_run_uuid UUID REFERENCES extraction_runs(extraction_run_uuid) ON DELETE SET NULL,
                        last_seen_ingestion_run_uuid UUID REFERENCES ingestion_runs(ingestion_run_uuid) ON DELETE SET NULL,
                        chunk_index INTEGER NOT NULL,
                        page_start INTEGER,
                        page_end INTEGER,
                        content TEXT NOT NULL,
                        section TEXT,
                        metadata JSONB,
                        content_sha256 CHAR(64),
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        embedding VECTOR(768),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (source_file_uuid, chunk_index)
                    );
                """))

                # 9. Embedding models table
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS embedding_models (
                        embedding_model_uuid UUID PRIMARY KEY,
                        model_id TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        version TEXT,
                        dimensions INTEGER NOT NULL,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (provider, model_id, version)
                    );
                """))

                # 10. Chunk embeddings table
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS chunk_embeddings (
                        chunk_embedding_uuid UUID PRIMARY KEY,
                        chunk_uuid UUID NOT NULL REFERENCES chunks(chunk_uuid) ON DELETE CASCADE,
                        embedding_model_uuid UUID REFERENCES embedding_models(embedding_model_uuid) ON DELETE SET NULL,
                        provider TEXT NOT NULL DEFAULT 'vertex_ai',
                        corpus_resource_name TEXT,
                        external_chunk_resource_name TEXT,
                        embedding_ref TEXT,
                        vector_store TEXT DEFAULT 'pgvector',
                        sync_status TEXT NOT NULL DEFAULT 'SYNCED',
                        last_synced_at TIMESTAMPTZ,
                        last_error TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (chunk_uuid, embedding_model_uuid)
                    );
                """))

                # Indexes
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_chunks_page ON chunks (source_file_uuid, page_start);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_chunks_sha256 ON chunks (content_sha256);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ingest_runs_status ON ingestion_runs (processing_status, created_at);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_extract_runs_source ON extraction_runs (source_file_uuid);"))

            else:
                # SQLite DDL (For zero-dependency local testing)
                conn.execute(text("PRAGMA journal_mode=WAL;"))
                conn.execute(text("PRAGMA busy_timeout=10000;"))

                # Check if legacy chunks table exists without source_file_uuid, and upgrade it
                try:
                    pragma_info = conn.execute(text("PRAGMA table_info(chunks);")).fetchall()
                    existing_cols = [r[1] for r in pragma_info]
                    if existing_cols and "source_file_uuid" not in existing_cols:
                        logger.info("Migrating local SQLite database to official 9-table schema...")
                        conn.execute(text("DROP VIEW IF EXISTS documents;"))
                        conn.execute(text("DROP TABLE IF EXISTS chunks;"))
                        conn.execute(text("DROP TABLE IF EXISTS documents;"))
                except Exception as e:
                    logger.debug(f"Migration check: {e}")

                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS judgments (
                        judgment_uuid TEXT PRIMARY KEY,
                        judgment_id TEXT NOT NULL UNIQUE,
                        court TEXT DEFAULT 'Supreme Court of India',
                        case_number TEXT,
                        judgment_date TEXT,
                        citation_text TEXT,
                        reporter_citations TEXT,
                        legal_status TEXT DEFAULT 'REPORTED',
                        is_primary INTEGER NOT NULL DEFAULT 1,
                        overruled_by_judgment_uuid TEXT,
                        refresh_interval_days INTEGER,
                        last_refreshed_at DATETIME,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (overruled_by_judgment_uuid) REFERENCES judgments(judgment_uuid) ON DELETE SET NULL
                    );
                """))

                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS source_files (
                        source_file_uuid TEXT PRIMARY KEY,
                        judgment_uuid TEXT NOT NULL,
                        version_number TEXT NOT NULL DEFAULT 'v1.0',
                        is_current INTEGER NOT NULL DEFAULT 1,
                        original_filename TEXT NOT NULL,
                        storage_path TEXT NOT NULL,
                        doc_encryption_files TEXT,
                        page_count INTEGER,
                        ingestion_status TEXT NOT NULL DEFAULT 'COMPLETED',
                        pipeline_version TEXT DEFAULT 'scj-ingestion-v1',
                        created_by_user_uuid TEXT,
                        content_sha256 TEXT,
                        file_size_bytes INTEGER,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (judgment_uuid) REFERENCES judgments(judgment_uuid) ON DELETE CASCADE,
                        UNIQUE (judgment_uuid, version_number)
                    );
                """))

                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS ingestion_runs (
                        ingestion_run_uuid TEXT PRIMARY KEY,
                        source_file_uuid TEXT NOT NULL,
                        pipeline_name TEXT NOT NULL DEFAULT 'scj-data-ingestion-v1',
                        processing_status TEXT NOT NULL,
                        error_message TEXT,
                        retry_count INTEGER NOT NULL DEFAULT 0,
                        max_attempts INTEGER NOT NULL DEFAULT 3,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        worker_id TEXT,
                        started_at DATETIME,
                        heartbeat_at DATETIME,
                        finished_at DATETIME,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (source_file_uuid) REFERENCES source_files(source_file_uuid) ON DELETE CASCADE
                    );
                """))

                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS extraction_runs (
                        extraction_run_uuid TEXT PRIMARY KEY,
                        source_file_uuid TEXT NOT NULL,
                        ingestion_run_uuid TEXT,
                        pipeline_version TEXT NOT NULL DEFAULT 'pymupdf',
                        processing_status TEXT NOT NULL DEFAULT 'COMPLETED',
                        error_message TEXT,
                        retry_count INTEGER NOT NULL DEFAULT 0,
                        max_attempts INTEGER NOT NULL DEFAULT 3,
                        started_at DATETIME,
                        finished_at DATETIME,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (source_file_uuid) REFERENCES source_files(source_file_uuid) ON DELETE CASCADE,
                        FOREIGN KEY (ingestion_run_uuid) REFERENCES ingestion_runs(ingestion_run_uuid) ON DELETE SET NULL
                    );
                """))

                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS source_pages (
                        source_page_uuid TEXT PRIMARY KEY,
                        source_file_uuid TEXT NOT NULL,
                        page_number INTEGER NOT NULL,
                        extraction_method TEXT NOT NULL DEFAULT 'pymupdf',
                        content TEXT,
                        quality_score REAL DEFAULT 0.95,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (source_file_uuid) REFERENCES source_files(source_file_uuid) ON DELETE CASCADE,
                        UNIQUE (source_file_uuid, page_number)
                    );
                """))

                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS citations (
                        citation_uuid TEXT PRIMARY KEY,
                        judgment_uuid TEXT NOT NULL,
                        citation_text TEXT NOT NULL,
                        citation_type TEXT NOT NULL DEFAULT 'REPORTER',
                        normalized_citation TEXT,
                        source_locator TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (judgment_uuid) REFERENCES judgments(judgment_uuid) ON DELETE CASCADE
                    );
                """))

                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS chunks (
                        chunk_uuid TEXT PRIMARY KEY,
                        chunk_id TEXT,
                        judgment_uuid TEXT NOT NULL,
                        source_file_uuid TEXT NOT NULL,
                        extraction_run_uuid TEXT,
                        last_seen_ingestion_run_uuid TEXT,
                        chunk_index INTEGER NOT NULL,
                        page_start INTEGER,
                        page_end INTEGER,
                        content TEXT NOT NULL,
                        section TEXT,
                        metadata TEXT,
                        content_sha256 TEXT,
                        is_active INTEGER NOT NULL DEFAULT 1,
                        embedding TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (judgment_uuid) REFERENCES judgments(judgment_uuid) ON DELETE CASCADE,
                        FOREIGN KEY (source_file_uuid) REFERENCES source_files(source_file_uuid) ON DELETE CASCADE,
                        FOREIGN KEY (extraction_run_uuid) REFERENCES extraction_runs(extraction_run_uuid) ON DELETE SET NULL,
                        FOREIGN KEY (last_seen_ingestion_run_uuid) REFERENCES ingestion_runs(ingestion_run_uuid) ON DELETE SET NULL,
                        UNIQUE (source_file_uuid, chunk_index)
                    );
                """))

                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS embedding_models (
                        embedding_model_uuid TEXT PRIMARY KEY,
                        model_id TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        version TEXT,
                        dimensions INTEGER NOT NULL,
                        is_active INTEGER NOT NULL DEFAULT 1,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (provider, model_id, version)
                    );
                """))

                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS chunk_embeddings (
                        chunk_embedding_uuid TEXT PRIMARY KEY,
                        chunk_uuid TEXT NOT NULL,
                        embedding_model_uuid TEXT,
                        provider TEXT NOT NULL DEFAULT 'vertex_ai',
                        corpus_resource_name TEXT,
                        external_chunk_resource_name TEXT,
                        embedding_ref TEXT,
                        vector_store TEXT DEFAULT 'pgvector',
                        sync_status TEXT NOT NULL DEFAULT 'SYNCED',
                        last_synced_at DATETIME,
                        last_error TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (chunk_uuid) REFERENCES chunks(chunk_uuid) ON DELETE CASCADE,
                        FOREIGN KEY (embedding_model_uuid) REFERENCES embedding_models(embedding_model_uuid) ON DELETE SET NULL,
                        UNIQUE (chunk_uuid, embedding_model_uuid)
                    );
                """))

                # Create backward-compatible documents view if not existing
                conn.execute(text("""
                    CREATE VIEW IF NOT EXISTS documents AS
                    SELECT 
                        j.judgment_id AS document_id,
                        s.storage_path AS file_path,
                        s.content_sha256 AS file_sha256,
                        j.case_number,
                        NULL AS appellant,
                        NULL AS respondent,
                        j.judgment_date AS date_of_judgment,
                        '[]' AS judges,
                        s.page_count AS total_pages,
                        (SELECT COUNT(*) FROM chunks c WHERE c.source_file_uuid = s.source_file_uuid) AS total_chunks,
                        s.pipeline_version AS parser_used,
                        s.ingestion_status AS status,
                        j.created_at,
                        j.updated_at
                    FROM judgments j
                    LEFT JOIN source_files s ON j.judgment_uuid = s.judgment_uuid;
                """))

                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_chunks_page ON chunks (source_file_uuid, page_start);"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_chunks_sha256 ON chunks (content_sha256);"))

        logger.info("All 9 parsing & ingestion tables initialized successfully.")

    def load_document_and_chunks(self, doc_json_path: str, chunks_json_path: str) -> Dict[str, Any]:
        """
        Atomically upserts records across all 9 tables:
        - judgments
        - source_files
        - ingestion_runs (LOG)
        - extraction_runs (LOG)
        - source_pages
        - citations
        - chunks
        Idempotent: Uses deterministic UUIDs so re-runs never duplicate records.
        """
        with open(doc_json_path, "r", encoding="utf-8") as f:
            doc_data = json.load(f)

        with open(chunks_json_path, "r", encoding="utf-8") as f:
            chunks_data = json.load(f)

        doc_id = doc_data["document_id"]
        file_sha256 = doc_data.get("file_sha256", "")
        file_path = doc_data.get("file_path", "")
        total_pages = doc_data.get("total_pages", 0)
        parser_used = doc_data.get("parser_used", "pymupdf")
        status = doc_data.get("status", "COMPLETED")
        meta = doc_data.get("aggregated_metadata") or {}

        # 1. Generate Deterministic UUIDs
        judgment_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"scj.judgment.{doc_id}"))
        source_file_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"scj.file.{doc_id}.{file_sha256}"))
        ingestion_run_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"scj.ingest.{doc_id}.{file_sha256}"))
        extraction_run_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"scj.extract.{doc_id}.{file_sha256}"))

        now_str = datetime.utcnow().isoformat()
        file_size = 0
        if file_path and os.path.exists(file_path):
            try:
                file_size = os.path.getsize(file_path)
            except OSError:
                pass

        with self.engine.begin() as conn:
            # 2. Upsert judgments
            if self.is_postgres:
                upsert_judgment = text("""
                    INSERT INTO judgments (
                        judgment_uuid, judgment_id, court, case_number,
                        judgment_date, citation_text, legal_status, is_primary
                    ) VALUES (
                        :judgment_uuid, :judgment_id, :court, :case_number,
                        NULL, :citation_text, 'REPORTED', TRUE
                    )
                    ON CONFLICT (judgment_id) DO UPDATE SET
                        case_number = EXCLUDED.case_number,
                        citation_text = EXCLUDED.citation_text,
                        updated_at = CURRENT_TIMESTAMP;
                """)
            else:
                upsert_judgment = text("""
                    INSERT INTO judgments (
                        judgment_uuid, judgment_id, court, case_number,
                        judgment_date, citation_text, legal_status, is_primary
                    ) VALUES (
                        :judgment_uuid, :judgment_id, :court, :case_number,
                        :judgment_date, :citation_text, 'REPORTED', 1
                    )
                    ON CONFLICT (judgment_id) DO UPDATE SET
                        case_number = excluded.case_number,
                        citation_text = excluded.citation_text,
                        updated_at = CURRENT_TIMESTAMP;
                """)

            conn.execute(upsert_judgment, {
                "judgment_uuid": judgment_uuid,
                "judgment_id": doc_id,
                "court": "Supreme Court of India",
                "case_number": meta.get("case_number"),
                "judgment_date": meta.get("date_of_judgment"),
                "citation_text": meta.get("case_number") or doc_id
            })

            # 3. Upsert source_files
            if self.is_postgres:
                upsert_file = text("""
                    INSERT INTO source_files (
                        source_file_uuid, judgment_uuid, version_number, is_current,
                        original_filename, storage_path, page_count, ingestion_status,
                        pipeline_version, content_sha256, file_size_bytes
                    ) VALUES (
                        :source_file_uuid, :judgment_uuid, 'v1.0', TRUE,
                        :original_filename, :storage_path, :page_count, :ingestion_status,
                        :pipeline_version, :content_sha256, :file_size_bytes
                    )
                    ON CONFLICT (judgment_uuid, version_number) DO UPDATE SET
                        content_sha256 = EXCLUDED.content_sha256,
                        page_count = EXCLUDED.page_count,
                        ingestion_status = EXCLUDED.ingestion_status,
                        updated_at = CURRENT_TIMESTAMP;
                """)
            else:
                upsert_file = text("""
                    INSERT INTO source_files (
                        source_file_uuid, judgment_uuid, version_number, is_current,
                        original_filename, storage_path, page_count, ingestion_status,
                        pipeline_version, content_sha256, file_size_bytes
                    ) VALUES (
                        :source_file_uuid, :judgment_uuid, 'v1.0', 1,
                        :original_filename, :storage_path, :page_count, :ingestion_status,
                        :pipeline_version, :content_sha256, :file_size_bytes
                    )
                    ON CONFLICT (judgment_uuid, version_number) DO UPDATE SET
                        content_sha256 = excluded.content_sha256,
                        page_count = excluded.page_count,
                        ingestion_status = excluded.ingestion_status,
                        updated_at = CURRENT_TIMESTAMP;
                """)

            conn.execute(upsert_file, {
                "source_file_uuid": source_file_uuid,
                "judgment_uuid": judgment_uuid,
                "original_filename": Path(file_path).name if file_path else f"{doc_id}.pdf",
                "storage_path": file_path,
                "page_count": total_pages,
                "ingestion_status": status,
                "pipeline_version": parser_used,
                "content_sha256": file_sha256,
                "file_size_bytes": file_size
            })

            # 4. Upsert ingestion_runs (LOG TABLE 1)
            upsert_ingest_run = text("""
                INSERT INTO ingestion_runs (
                    ingestion_run_uuid, source_file_uuid, pipeline_name,
                    processing_status, error_message, retry_count, max_attempts,
                    idempotency_key, worker_id, started_at, finished_at
                ) VALUES (
                    :ingestion_run_uuid, :source_file_uuid, 'scj-data-ingestion-v1',
                    :processing_status, :error_message, 0, 3,
                    :idempotency_key, :worker_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (idempotency_key) DO UPDATE SET
                    processing_status = EXCLUDED.processing_status,
                    finished_at = CURRENT_TIMESTAMP;
            """)
            conn.execute(upsert_ingest_run, {
                "ingestion_run_uuid": ingestion_run_uuid,
                "source_file_uuid": source_file_uuid,
                "processing_status": status,
                "error_message": doc_data.get("error_message"),
                "idempotency_key": f"{file_sha256}_v1.0",
                "worker_id": os.environ.get("HOSTNAME", "worker-main")
            })

            # 5. Upsert extraction_runs (LOG TABLE 2)
            conn.execute(text("DELETE FROM extraction_runs WHERE extraction_run_uuid = :e_uuid"), {"e_uuid": extraction_run_uuid})
            insert_extract_run = text("""
                INSERT INTO extraction_runs (
                    extraction_run_uuid, source_file_uuid, ingestion_run_uuid,
                    pipeline_version, processing_status, error_message
                ) VALUES (
                    :extraction_run_uuid, :source_file_uuid, :ingestion_run_uuid,
                    :pipeline_version, :processing_status, :error_message
                );
            """)
            conn.execute(insert_extract_run, {
                "extraction_run_uuid": extraction_run_uuid,
                "source_file_uuid": source_file_uuid,
                "ingestion_run_uuid": ingestion_run_uuid,
                "pipeline_version": parser_used,
                "processing_status": status,
                "error_message": doc_data.get("error_message")
            })

            # 6. Populate source_pages
            pages = doc_data.get("pages", [])
            ocr_pages = set(doc_data.get("ocr_applied_pages", []))
            for p in pages:
                p_num = p.get("original_page_number", 1)
                p_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"scj.page.{source_file_uuid}.{p_num}"))
                p_method = "tesseract_ocr" if p_num in ocr_pages else parser_used
                p_score = 0.70 if p_num in ocr_pages else 0.95
                p_content = "\n\n".join(c.get("content", "") for c in p.get("chunks", []))

                insert_page = text("""
                    INSERT INTO source_pages (
                        source_page_uuid, source_file_uuid, page_number,
                        extraction_method, content, quality_score
                    ) VALUES (
                        :p_uuid, :source_file_uuid, :p_num,
                        :p_method, :p_content, :p_score
                    )
                    ON CONFLICT (source_file_uuid, page_number) DO UPDATE SET
                        content = EXCLUDED.content,
                        quality_score = EXCLUDED.quality_score;
                """)
                conn.execute(insert_page, {
                    "p_uuid": p_uuid,
                    "source_file_uuid": source_file_uuid,
                    "p_num": p_num,
                    "p_method": p_method,
                    "p_content": p_content,
                    "p_score": p_score
                })

            # 7. Populate citations
            if meta.get("case_number"):
                cit_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"scj.cit.{judgment_uuid}.{meta.get('case_number')}"))
                conn.execute(text("DELETE FROM citations WHERE citation_uuid = :c_uuid"), {"c_uuid": cit_uuid})
                conn.execute(text("""
                    INSERT INTO citations (
                        citation_uuid, judgment_uuid, citation_text, citation_type, normalized_citation
                    ) VALUES (
                        :cit_uuid, :judgment_uuid, :citation_text, 'REPORTER', :norm_cit
                    );
                """), {
                    "cit_uuid": cit_uuid,
                    "judgment_uuid": judgment_uuid,
                    "citation_text": meta["case_number"],
                    "norm_cit": meta["case_number"].strip().upper()
                })

            # 8. Batch insert chunks
            conn.execute(text("DELETE FROM chunks WHERE source_file_uuid = :s_uuid"), {"s_uuid": source_file_uuid})

            insert_chunk_sql = text("""
                INSERT INTO chunks (
                    chunk_uuid, chunk_id, judgment_uuid, source_file_uuid,
                    extraction_run_uuid, last_seen_ingestion_run_uuid,
                    chunk_index, page_start, page_end, content, section,
                    metadata, content_sha256, is_active, embedding
                ) VALUES (
                    :chunk_uuid, :chunk_id, :judgment_uuid, :source_file_uuid,
                    :extraction_run_uuid, :last_seen_ingestion_run_uuid,
                    :chunk_index, :page_start, :page_end, :content, :section,
                    :metadata, :content_sha256, :is_active, :embedding
                );
            """)

            chunk_records = []
            for global_idx, c in enumerate(chunks_data, 1):
                c_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"scj.chunk.{c['chunk_id']}"))
                c_hash = hashlib.sha256(c["content"].encode("utf-8")).hexdigest()
                
                meta_dict = {
                    "case_number": c.get("case_number"),
                    "appellant": c.get("appellant"),
                    "respondent": c.get("respondent"),
                    "judges": c.get("judges", []),
                    "date_of_judgment": c.get("date_of_judgment"),
                    "page_chunk_index": c["chunk_index"],
                    "original_chunk_id": c["chunk_id"]
                }
                meta_json = json.dumps(meta_dict)
                emb_val = c.get("embedding")
                emb_str = json.dumps(emb_val) if isinstance(emb_val, list) else emb_val

                chunk_records.append({
                    "chunk_uuid": c_uuid,
                    "chunk_id": c["chunk_id"],
                    "judgment_uuid": judgment_uuid,
                    "source_file_uuid": source_file_uuid,
                    "extraction_run_uuid": extraction_run_uuid,
                    "last_seen_ingestion_run_uuid": ingestion_run_uuid,
                    "chunk_index": global_idx,
                    "page_start": c["original_page_number"],
                    "page_end": c["original_page_number"],
                    "content": c["content"],
                    "section": c.get("section_heading"),
                    "metadata": meta_json,
                    "content_sha256": c_hash,
                    "is_active": 1 if self.is_sqlite else True,
                    "embedding": emb_str
                })

            # Execute in batches of 500
            batch_size = config.DB_BATCH_SIZE
            for i in range(0, len(chunk_records), batch_size):
                conn.execute(insert_chunk_sql, chunk_records[i:i + batch_size])

        logger.info(f"Loaded {doc_id} into all 9 tables: 1 judgment, 1 file, {len(pages)} pages, {len(chunk_records)} chunks.")
        return {
            "document_id": doc_id,
            "status": "LOADED",
            "judgment_uuid": judgment_uuid,
            "source_file_uuid": source_file_uuid,
            "chunks_count": len(chunks_data),
            "pages_count": len(pages)
        }

    def load_all_from_output(self, output_dir: str = "output") -> Dict[str, Any]:
        """Scans output directory and loads all documents into the 9 tables."""
        docs_dir = Path(output_dir) / "documents"
        chunks_dir = Path(output_dir) / "chunks"

        if not docs_dir.exists():
            raise FileNotFoundError(f"Documents directory not found: {docs_dir}")

        doc_files = list(docs_dir.glob("*.json"))
        loaded_count = 0
        total_chunks = 0
        failures = []

        logger.info(f"Found {len(doc_files)} document summaries. Loading into 9-table schema...")

        for doc_file in doc_files:
            doc_id = doc_file.stem
            chunk_file = chunks_dir / f"{doc_id}_chunks.json"

            if not chunk_file.exists():
                logger.warning(f"Skipping {doc_id}: chunks file {chunk_file} not found.")
                failures.append({"document_id": doc_id, "error": "Missing chunks JSON file"})
                continue

            try:
                res = self.load_document_and_chunks(str(doc_file), str(chunk_file))
                loaded_count += 1
                total_chunks += res["chunks_count"]
            except Exception as e:
                logger.error(f"Failed to load {doc_id}: {e}")
                failures.append({"document_id": doc_id, "error": str(e)})

        summary = {
            "total_documents_found": len(doc_files),
            "documents_loaded": loaded_count,
            "total_chunks_loaded": total_chunks,
            "failed_count": len(failures),
            "failures": failures
        }
        logger.info(f"Bulk load complete: {loaded_count} docs, {total_chunks} chunks.")
        return summary

    def get_stats(self) -> Dict[str, Any]:
        """Returns row counts across all 9 tables and sample chunk details."""
        with self.engine.connect() as conn:
            j_count = conn.execute(text("SELECT COUNT(*) FROM judgments")).scalar()
            f_count = conn.execute(text("SELECT COUNT(*) FROM source_files")).scalar()
            c_count = conn.execute(text("SELECT COUNT(*) FROM chunks")).scalar()
            r_count = conn.execute(text("SELECT COUNT(*) FROM ingestion_runs")).scalar()
            e_count = conn.execute(text("SELECT COUNT(*) FROM extraction_runs")).scalar()
            p_count = conn.execute(text("SELECT COUNT(*) FROM source_pages")).scalar()

            sample = conn.execute(text("""
                SELECT chunk_id, chunk_uuid, judgment_uuid, page_start, section, substr(content, 1, 100) AS snippet
                FROM chunks LIMIT 1
            """)).mappings().first()

            return {
                "database_url": self.db_url,
                "total_documents": j_count,
                "total_judgments": j_count,
                "total_source_files": f_count,
                "total_chunks": c_count,
                "total_ingestion_runs": r_count,
                "total_extraction_runs": e_count,
                "total_source_pages": p_count,
                "sample_chunk": dict(sample) if sample else None
            }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Load Ingested Judgments into 9-Table Schema (Cloud SQL / SQLite)")
    parser.add_argument("--db", default=None, help=f"Database connection URL (default: {DEFAULT_SQLITE_URL} or env DATABASE_URL)")
    parser.add_argument("--doc-id", default=None, help="Specific document ID to load")
    parser.add_argument("--output-dir", default="output", help="Directory where pipeline wrote JSON deliverables")
    parser.add_argument("--stats", action="store_true", help="Print current database table counts")
    args = parser.parse_args()

    loader = DatabaseLoader(db_url=args.db)

    if args.stats:
        stats = loader.get_stats()
        print("\n=== Database Statistics (9 Parsing Tables) ===")
        print(json.dumps(stats, indent=2, default=str))
        return

    if args.doc_id:
        doc_path = Path(args.output_dir) / "documents" / f"{args.doc_id}.json"
        chunk_path = Path(args.output_dir) / "chunks" / f"{args.doc_id}_chunks.json"
        res = loader.load_document_and_chunks(str(doc_path), str(chunk_path))
        print(json.dumps(res, indent=2))
    else:
        summary = loader.load_all_from_output(args.output_dir)
        print("\n=== Database Load Summary ===")
        print(json.dumps(summary, indent=2))
        stats = loader.get_stats()
        print("\n=== Database Statistics ===")
        print(json.dumps(stats, indent=2, default=str))


if __name__ == "__main__":
    main()
