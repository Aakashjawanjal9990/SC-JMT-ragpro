"""Offline integration check for ingestion SQLite -> retrieval -> citation provenance."""

import json
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "ingestion"))

from app.adapters.sqlite_sources import SqliteSourceRecordAdapter  # noqa: E402
from app.models import RetrievedPassage  # noqa: E402
from app.retrieval.embedder import DeterministicTestEmbedder  # noqa: E402
from app.retrieval.hybrid import HybridRetriever  # noqa: E402
from app.retrieval.port_adapter import passages_from_result  # noqa: E402
from app.retrieval.sqlite_store import DEFAULT_CHUNKS_SQL, SqliteVectorStore  # noqa: E402
from storage.init_db import init_database  # noqa: E402


def _seed_ingestion_database(path: Path) -> None:
    init_database(path)
    judgment_id = str(uuid.uuid4())
    source_file_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    source_page_id = str(uuid.uuid4())
    text = "Article 21 requires procedure established by law before depriving life or liberty."
    vector = DeterministicTestEmbedder(8).embed(text).tolist()

    import sqlite3

    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO judgments
               (judgment_uuid, judgment_id, case_number, judgment_date, citation_text)
               VALUES (?, ?, ?, ?, ?)""",
            (judgment_id, "integration-sample", "Sample v. State", "2020-01-02", "(2020) 1 SCR 1"),
        )
        connection.execute(
            """INSERT INTO source_files
               (source_file_uuid, judgment_uuid, version_number, is_current,
                original_filename, storage_path, ingestion_status)
               VALUES (?, ?, '1', 1, 'sample.pdf', 'local://sample.pdf', 'COMPLETE')""",
            (source_file_id, judgment_id),
        )
        connection.execute(
            """INSERT INTO source_pages
               (source_page_uuid, source_file_uuid, page_number, extraction_method, content)
               VALUES (?, ?, 12, 'test', ?)""",
            (source_page_id, source_file_id, text),
        )
        connection.execute(
            """INSERT INTO chunks
               (chunk_uuid, judgment_uuid, source_file_uuid, chunk_index, page_start,
                page_end, content, is_active, embedding)
               VALUES (?, ?, ?, 0, 12, 12, ?, 1, ?)""",
            (chunk_id, judgment_id, source_file_id, text, json.dumps(vector)),
        )
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def test_sqlite_ingestion_retrieval_and_citation_provenance(tmp_path: Path) -> None:
    database_path = tmp_path / "scj_rag_v2.db"
    _seed_ingestion_database(database_path)

    store = SqliteVectorStore(
        str(database_path),
        chunks_sql=DEFAULT_CHUNKS_SQL,
        dimensions=8,
    )
    retriever = HybridRetriever(
        store,
        DeterministicTestEmbedder(8),
        bm25=None,
        top_k=4,
        candidate_k=4,
    )
    result = retriever.search("What does Article 21 require?", top_k=4)
    passages = passages_from_result(result, 4, passage_factory=RetrievedPassage)
    source_records = SqliteSourceRecordAdapter(str(database_path))
    resolved = source_records._resolve_many(passages)

    assert len(passages) == 1
    assert len(resolved) == 1
    assert resolved[0].chunk_id == passages[0].chunk_id
    assert resolved[0].document_id == passages[0].document_id
    assert resolved[0].source_pdf_id == passages[0].source_pdf_id
    assert resolved[0].text == passages[0].text
    assert resolved[0].original_page_numbers == [12]
    assert resolved[0].source.citation == "(2020) 1 SCR 1"
