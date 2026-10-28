"""
Database Schema Initializer for PCE v2.0
Initializes clean SQLite database conforming strictly to db/schema.sql with zero DDL drift.
"""

import sqlite3
from pathlib import Path


SQLITE_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS embedding_models (
    embedding_model_uuid TEXT PRIMARY KEY,
    model_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    version TEXT,
    dimensions INTEGER NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (provider, model_id, version)
);

CREATE TABLE IF NOT EXISTS judgments (
    judgment_uuid TEXT PRIMARY KEY,
    judgment_id TEXT NOT NULL UNIQUE,
    court TEXT,
    case_number TEXT,
    judgment_date TEXT,
    citation_text TEXT,
    reporter_citations TEXT,
    legal_status TEXT,
    is_primary INTEGER NOT NULL DEFAULT 0,
    overruled_by_judgment_uuid TEXT,
    refresh_interval_days INTEGER,
    last_refreshed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (overruled_by_judgment_uuid) REFERENCES judgments(judgment_uuid)
);

CREATE TABLE IF NOT EXISTS source_files (
    source_file_uuid TEXT PRIMARY KEY,
    judgment_uuid TEXT NOT NULL,
    version_number TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 0,
    original_filename TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    doc_encryption_files TEXT,
    page_count INTEGER,
    ingestion_status TEXT NOT NULL,
    pipeline_version TEXT,
    created_by_user_uuid TEXT,
    content_sha256 TEXT,
    file_size_bytes INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (judgment_uuid) REFERENCES judgments(judgment_uuid) ON DELETE CASCADE,
    UNIQUE (judgment_uuid, version_number)
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    ingestion_run_uuid TEXT PRIMARY KEY,
    source_file_uuid TEXT NOT NULL,
    pipeline_name TEXT NOT NULL,
    processing_status TEXT NOT NULL,
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    idempotency_key TEXT NOT NULL UNIQUE,
    worker_id TEXT,
    started_at TIMESTAMP,
    heartbeat_at TIMESTAMP,
    finished_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_file_uuid) REFERENCES source_files(source_file_uuid) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS extraction_runs (
    extraction_run_uuid TEXT PRIMARY KEY,
    source_file_uuid TEXT NOT NULL,
    ingestion_run_uuid TEXT,
    pipeline_version TEXT NOT NULL,
    processing_status TEXT NOT NULL,
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_file_uuid) REFERENCES source_files(source_file_uuid) ON DELETE CASCADE,
    FOREIGN KEY (ingestion_run_uuid) REFERENCES ingestion_runs(ingestion_run_uuid) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS source_pages (
    source_page_uuid TEXT PRIMARY KEY,
    source_file_uuid TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    extraction_method TEXT NOT NULL,
    content TEXT,
    quality_score REAL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_file_uuid) REFERENCES source_files(source_file_uuid) ON DELETE CASCADE,
    UNIQUE (source_file_uuid, page_number)
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_uuid TEXT PRIMARY KEY,
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
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (judgment_uuid) REFERENCES judgments(judgment_uuid) ON DELETE CASCADE,
    FOREIGN KEY (source_file_uuid) REFERENCES source_files(source_file_uuid) ON DELETE CASCADE,
    FOREIGN KEY (extraction_run_uuid) REFERENCES extraction_runs(extraction_run_uuid) ON DELETE SET NULL,
    FOREIGN KEY (last_seen_ingestion_run_uuid) REFERENCES ingestion_runs(ingestion_run_uuid) ON DELETE SET NULL,
    UNIQUE (source_file_uuid, chunk_index)
);

CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_embedding_uuid TEXT PRIMARY KEY,
    chunk_uuid TEXT NOT NULL,
    embedding_model_uuid TEXT,
    provider TEXT NOT NULL,
    corpus_resource_name TEXT,
    external_chunk_resource_name TEXT,
    embedding_ref TEXT,
    vector_store TEXT,
    sync_status TEXT NOT NULL,
    last_synced_at TIMESTAMP,
    last_error TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (chunk_uuid) REFERENCES chunks(chunk_uuid) ON DELETE CASCADE,
    FOREIGN KEY (embedding_model_uuid) REFERENCES embedding_models(embedding_model_uuid) ON DELETE SET NULL,
    UNIQUE (chunk_uuid, embedding_model_uuid)
);

CREATE TABLE IF NOT EXISTS citations (
    citation_uuid TEXT PRIMARY KEY,
    judgment_uuid TEXT NOT NULL,
    citation_text TEXT NOT NULL,
    citation_type TEXT NOT NULL DEFAULT 'REPORTED',
    normalized_citation TEXT,
    source_locator TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (judgment_uuid) REFERENCES judgments(judgment_uuid) ON DELETE CASCADE
);
"""


def init_database(db_path: Path) -> None:
    """Creates a new SQLite database with the frozen production schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute('PRAGMA journal_mode = WAL;')
        conn.execute('PRAGMA synchronous = NORMAL;')
        conn.executescript(SQLITE_SCHEMA_DDL)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("PCEV2/output/scj_rag_v2.db")
    init_database(target)
    print(f"Initialized clean schema in: {target}")
