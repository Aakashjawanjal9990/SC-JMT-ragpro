# Supreme Court Judgment RAG System
## Database Integration & Schema Specification (Handoff Document)
**From**: Kedar (Data Ingestion Engineer)  
**To**: Anuradha (Database Engineer / Architect)  
**Coordinated With**: Pratik (Retrieval Engineer) & Nilesh (Backend Integration)  
**Workspace**: `E:\Dummy`

---

## 1. Executive Summary & Purpose

This specification defines the exact contracts, schemas, volume expectations, and indexing strategies for persisting ingested Indian Supreme Court judgment records into the database. 

The ingestion pipeline produces clean, structured JSON deliverables on disk (`output/chunks/` and `output/documents/`). This document provides Anuradha with all technical requirements to implement PostgreSQL schemas, constraints, foreign keys, migrations, and query performance optimizations.

---

## 2. Dataset Volume & Access Patterns

### 2.1 Sizing Estimates
* **Total Document Collection**: ~43,000 Indian Supreme Court judgment PDFs (1950 – Present).
* **Pages per Document**: 5 to 60 pages (Average: ~15 pages).
* **Chunks per Document**: 20 to 250 chunks (Average: ~80 chunks).
* **Total Estimated Chunks**: **~3,500,000 to 10,000,000 rows**.
* **Estimated Raw Text Storage**: ~15 GB to 35 GB.
* **Vector Index Storage (1536-dim embeddings)**: ~30 GB to 60 GB.

### 2.2 Workload Characteristics
* **Ingestion Phase (Write-Heavy)**: High-throughput batch `INSERT` / `COPY` operations per document.
* **Query Phase (Read-Heavy)**:
  * Low-latency point lookups by `chunk_id`.
  * Page-range retrieval by `(document_id, original_page_number)` for citation verification.
  * Hybrid search execution: Combining Full-Text Search (BM25 / GIN) with pgvector cosine similarity scans.

---

## 3. Physical Ingestion Artifacts & File Paths

The ingestion pipeline outputs the following persistent deliverables:

| Deliverable | Disk Location | Description | Primary Consumer |
| :--- | :--- | :--- | :--- |
| **Chunk Collections** | `output/chunks/{document_id}_chunks.json` | Array of all chunk records for a judgment, containing exact text, page numbers, section headings, and metadata. | **Anuradha (DB)** & **Pratik (Vector/BM25 Indexer)** |
| **Document Summaries** | `output/documents/{document_id}.json` | Document-level metadata: case number, parties, coram, total pages, total chunks, parser used, SHA-256 hash. | **Anuradha (DB)** |
| **Ingestion State** | `ingestion_state.json` | Key-value mapping of `file_sha256 -> document_id` and processing checkpoint statuses. | **Pipeline & DB Sync** |
| **Error Log** | `reports/status_error_report.json` | Chronological defect log for failed documents with tracebacks. | **QA / Kusuma** |

---

## 4. PostgreSQL Relational & Vector Schema (DDL)

```sql
-- ============================================================================
-- 1. EXTENSIONS
-- ============================================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";       -- Trigram indexes for party fuzzy search
CREATE EXTENSION IF NOT EXISTS "vector";        -- pgvector for semantic retrieval

-- ============================================================================
-- 2. DOCUMENTS TABLE (Parent Entity)
-- ============================================================================
CREATE TABLE IF NOT EXISTS documents (
    document_id VARCHAR(100) PRIMARY KEY,       -- e.g. '1950_1_15_25_EN' or 'INSC_2023_CA_1042'
    file_path TEXT NOT NULL,
    file_sha256 CHAR(64) NOT NULL UNIQUE,       -- Cryptographic hash for duplicate detection
    case_number VARCHAR(150),                   -- e.g. 'CIVIL APPEAL NO. 1042 OF 2023' or 'S.C.R.'
    appellant TEXT,                             -- e.g. 'RAM KRISHNA RAMNATH AGARWAL OF KAMPTEE'
    respondent TEXT,                            -- e.g. 'SECRETARY, MUNICIPAL COMMITTEE, KAMPTEE'
    date_of_judgment VARCHAR(50),               -- e.g. '1950 March 14' or '24th February, 2023'
    judges JSONB,                               -- Array: ["HARILAL KANIA C.J", "SAIYID FAZL ALI", ...]
    total_pages INT NOT NULL,
    total_chunks INT NOT NULL,
    parser_used VARCHAR(30) DEFAULT 'pymupdf',  -- 'llamaparse' or 'pymupdf'
    status VARCHAR(30) NOT NULL DEFAULT 'COMPLETED', -- 'COMPLETED', 'FAILED', 'PROCESSING'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 3. CHUNKS TABLE (Child Entity)
-- ============================================================================
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id VARCHAR(120) PRIMARY KEY,          -- Format: {document_id}_p{page}_c{chunk_index}
    document_id VARCHAR(100) NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    original_page_number INT NOT NULL,          -- Strict 1-based original PDF page number
    chunk_index INT NOT NULL,                   -- 1-based sequential position on that page
    content TEXT NOT NULL,                      -- Cleaned passage text
    section_heading VARCHAR(150),               -- e.g. 'FACTS AND BACKGROUND', 'JUDGMENT', null
    
    -- Reserved for Pratik (Retrieval Engineer)
    embedding VECTOR(1536),                     -- Dense embedding (1536 for OpenAI, 768 for legal-bert)
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
```

---

## 5. Indexing Strategy & Query Optimization

```sql
-- 1. Fast Page Citation Lookups (Used by Nilesh & Tanvi to fetch all chunks on a specific PDF page)
CREATE INDEX IF NOT EXISTS idx_chunks_doc_page 
ON chunks (document_id, original_page_number);

-- 2. Full-Text Search Index (Used by Pratik for BM25/keyword retrieval)
CREATE INDEX IF NOT EXISTS idx_chunks_content_tsv 
ON chunks USING GIN (to_tsvector('english', content));

-- 3. Case Metadata Indexes (Fast exact & prefix lookups)
CREATE INDEX IF NOT EXISTS idx_docs_case_number 
ON documents (case_number);

-- 4. Fuzzy Trigram Indexes for Party Searches (Handles spelling variations)
CREATE INDEX IF NOT EXISTS idx_docs_appellant_trgm 
ON documents USING GIN (appellant gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_docs_respondent_trgm 
ON documents USING GIN (respondent gin_trgm_ops);

-- 5. HNSW Vector Index (Cosine distance for Pratik's semantic similarity queries)
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw 
ON chunks USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

---

## 6. Data Integrity & Reprocessing Consistency Rules

As mandated in the project engineering rules:
1. **Cascade Deletes**:
   `ON DELETE CASCADE` guarantees that if a document is deleted or reprocessed, its previous chunks are immediately purged without leaving orphaned records.
2. **Atomic Reprocessing (Idempotency)**:
   When re-ingesting a document (e.g. via `--force`), wrap operations in an atomic transaction:
   ```sql
   BEGIN;
   -- 1. Upsert document header
   INSERT INTO documents (document_id, file_path, file_sha256, ...)
   VALUES (%s, %s, %s, ...)
   ON CONFLICT (document_id) DO UPDATE SET
       file_sha256 = EXCLUDED.file_sha256,
       total_pages = EXCLUDED.total_pages,
       total_chunks = EXCLUDED.total_chunks,
       updated_at = CURRENT_TIMESTAMP;

   -- 2. Clear old chunks (in case chunk count or boundaries changed)
   DELETE FROM chunks WHERE document_id = %s;

   -- 3. Bulk insert fresh chunks
   INSERT INTO chunks (chunk_id, document_id, original_page_number, chunk_index, content, section_heading)
   VALUES (%s, %s, %s, %s, %s, %s);
   COMMIT;
   ```
3. **Traceability Invariant**:
   The primary key format `{document_id}_p{page}_c{chunk_index}` must never be modified by downstream tables, ensuring end-to-end traceability from the UI citation back to the physical PDF page.

---

## 7. Sample Input Payloads for Database Loaders

### 7.1 Chunk Record Payload (`output/chunks/1950_1_15_25_EN_chunks.json`)
```json
{
  "chunk_id": "1950_1_15_25_EN_p2_c1",
  "document_id": "1950_1_15_25_EN",
  "original_page_number": 2,
  "chunk_index": 1,
  "content": "3. The respondent contractor entered into a concession agreement with the appellant on 12 June 2018 for the construction of a state highway bypass...",
  "section_heading": "FACTS AND BACKGROUND",
  "case_number": "S.C.R.",
  "appellant": "RAM KRISHNA RAMNATH AGARWAL OF KAMPTEE",
  "respondent": "SECRETARY, MUNICIPAL COMMITTEE, KAMP TEE",
  "date_of_judgment": "1950 March 14",
  "judges": [
    "HARILAL KANIA C.J",
    "SAIYID FAZL ALI",
    "PATANJALI SASTRI",
    "MEHR CHAND MAHAJAN",
    "MuKHERJEA",
    "S.R. DAS JJ"
  ]
}
```

### 7.2 Document Summary Payload (`output/documents/1950_1_15_25_EN.json`)
```json
{
  "document_id": "1950_1_15_25_EN",
  "file_path": "data\\sample_pdfs\\year=1950\\english\\1950_1_15_25_EN.pdf",
  "file_sha256": "9789e45cda05cbea4886888e54277fd5831220afa3976a62d3d69025114ae7a8",
  "total_pages": 11,
  "total_chunks": 31,
  "status": "COMPLETED",
  "parser_used": "pymupdf",
  "aggregated_metadata": {
    "case_number": "S.C.R.",
    "appellant": "RAM KRISHNA RAMNATH AGARWAL OF KAMPTEE",
    "respondent": "SECRETARY, MUNICIPAL COMMITTEE, KAMP TEE",
    "date_of_judgment": "1950 March 14",
    "judges": [
      "HARILAL KANIA C.J",
      "SAIYID FAZL ALI",
      "PATANJALI SASTRI",
      "MEHR CHAND MAHAJAN",
      "MuKHERJEA",
      "S.R. DAS JJ"
    ]
  }
}
```

---

## 8. Reference Python DB Loader Script for Anuradha

Below is a reference script demonstrating how Anuradha can bulk load Kedar's JSON deliverables into PostgreSQL:

```python
import json
from pathlib import Path
import psycopg2
from psycopg2.extras import execute_batch

def load_document_and_chunks(db_conn, doc_json_path: str, chunks_json_path: str):
    with open(doc_json_path, "r", encoding="utf-8") as f:
        doc = json.load(f)

    with open(chunks_json_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    cur = db_conn.cursor()
    try:
        # 1. Upsert document
        meta = doc.get("aggregated_metadata", {})
        cur.execute("""
            INSERT INTO documents (
                document_id, file_path, file_sha256, case_number, 
                appellant, respondent, date_of_judgment, judges, 
                total_pages, total_chunks, parser_used, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (document_id) DO UPDATE SET
                file_sha256 = EXCLUDED.file_sha256,
                total_pages = EXCLUDED.total_pages,
                total_chunks = EXCLUDED.total_chunks,
                updated_at = CURRENT_TIMESTAMP;
        """, (
            doc["document_id"],
            doc["file_path"],
            doc["file_sha256"],
            meta.get("case_number"),
            meta.get("appellant"),
            meta.get("respondent"),
            meta.get("date_of_judgment"),
            json.dumps(meta.get("judges", [])),
            doc["total_pages"],
            doc["total_chunks"],
            doc.get("parser_used", "pymupdf"),
            doc["status"]
        ))

        # 2. Clear previous chunks if reprocessing
        cur.execute("DELETE FROM chunks WHERE document_id = %s;", (doc["document_id"],))

        # 3. Batch insert chunks
        chunk_tuples = [
            (
                c["chunk_id"],
                c["document_id"],
                c["original_page_number"],
                c["chunk_index"],
                c["content"],
                c.get("section_heading")
            )
            for c in chunks
        ]

        execute_batch(cur, """
            INSERT INTO chunks (
                chunk_id, document_id, original_page_number, 
                chunk_index, content, section_heading
            ) VALUES (%s, %s, %s, %s, %s, %s);
        """, chunk_tuples)

        db_conn.commit()
        print(f"Loaded {doc['document_id']}: {len(chunk_tuples)} chunks committed.")
    except Exception as e:
        db_conn.rollback()
        raise e
    finally:
        cur.close()
```

