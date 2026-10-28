"""
Data Quality and Integrity Dynamic Verification & Negative Testing Suite
Tests all quality verification checks under both passing and failing conditions.
Guarantees zero hardcoded True gates and zero synthetic assertions.
"""

import sqlite3
import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

from check_gates import verify_schema_integrity, evaluate_retrieval_benchmark
from storage.init_db import init_database


@pytest.fixture
def clean_test_db(tmp_path):
    """Creates a temporary isolated SQLite database with the canonical schema."""
    db_file = tmp_path / "test_quality.db"
    init_database(db_file)
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_engine(db_url)
    return engine, db_url


def test_schema_integrity_passes_on_canonical_schema(clean_test_db):
    """Verifies that schema integrity verification passes on an unmodified canonical schema."""
    engine, _ = clean_test_db
    passed, detail = verify_schema_integrity(engine)
    assert passed is True
    assert "0 schema drift" in detail


def test_schema_integrity_fails_when_table_missing(clean_test_db):
    """Verifies that schema integrity verification fails if a required table is dropped."""
    engine, _ = clean_test_db
    with engine.connect() as conn:
        conn.execute(sa.text("DROP TABLE chunk_embeddings;"))
        conn.commit()

    passed, detail = verify_schema_integrity(engine)
    assert passed is False
    assert "missing required tables" in detail
    assert "chunk_embeddings" in detail


def test_schema_integrity_fails_when_column_missing(clean_test_db, tmp_path):
    """Verifies that schema integrity verification fails if a required column is missing from a table."""
    drift_db_file = tmp_path / "drift.db"
    conn = sqlite3.connect(str(drift_db_file))
    # Create chunks table without content column
    conn.execute("""
        CREATE TABLE chunks (
            chunk_uuid TEXT PRIMARY KEY,
            judgment_uuid TEXT NOT NULL,
            source_file_uuid TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            page_start INTEGER,
            page_end INTEGER
        );
    """)
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite:///{drift_db_file.as_posix()}")
    passed, detail = verify_schema_integrity(engine)
    assert passed is False
    assert "Schema drift" in detail


def test_retrieval_benchmark_fails_on_empty_database(clean_test_db):
    """Verifies that retrieval benchmark evaluation fails closed when there are zero chunks in the database."""
    engine, _ = clean_test_db
    passed, recall, detail = evaluate_retrieval_benchmark(engine)
    assert passed is False
    assert recall == 0.0
    assert "Zero active chunks" in detail


def test_retrieval_benchmark_passes_when_relevant_chunks_exist(clean_test_db):
    """Verifies that retrieval benchmark evaluation passes when relevant legal chunks are ingested."""
    engine, _ = clean_test_db
    with engine.connect() as conn:
        conn.execute(sa.text("""
            INSERT INTO judgments (judgment_uuid, judgment_id) VALUES ('j1', 'TEST_JUDGMENT_01');
        """))
        conn.execute(sa.text("""
            INSERT INTO source_files (source_file_uuid, judgment_uuid, version_number, original_filename, storage_path, ingestion_status)
            VALUES ('sf1', 'j1', 'v1.0', 'test.pdf', '/test.pdf', 'INGESTED');
        """))
        # Insert chunks covering benchmark domains
        conn.execute(sa.text("""
            INSERT INTO chunks (chunk_uuid, judgment_uuid, source_file_uuid, chunk_index, page_start, page_end, content, is_active)
            VALUES ('c1', 'j1', 'sf1', 0, 1, 1, 'Article 21 protects life and personal liberty except according to procedure established by law.', 1);
        """))
        conn.execute(sa.text("""
            INSERT INTO chunks (chunk_uuid, judgment_uuid, source_file_uuid, chunk_index, page_start, page_end, content, is_active)
            VALUES ('c2', 'j1', 'sf1', 1, 2, 2, 'High Court jurisdiction under Article 226 for the issuance of writ of certiorari or mandamus.', 1);
        """))
        conn.commit()

    passed, recall, detail = evaluate_retrieval_benchmark(engine, min_recall_threshold=0.25)
    assert passed is True
    assert recall >= 0.25
    assert "Domain recall:" in detail
