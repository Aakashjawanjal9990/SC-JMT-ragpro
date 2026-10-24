# Supreme Court Judgment RAG — Database Schema & Embedding Specification

**Database Name**: `scrag` | **Instance**: `sc-rag-pg` (`34.93.53.176`, PostgreSQL 16)  
**Embedding Engine**: Google Cloud Vertex AI `text-embedding-004` (768 dimensions)  
**Author**: Kedar (Data Ingestion & Pipeline Engineer)  
**Target Dataset**: ~43,000 Indian Supreme Court Judgments (1950 – 2026)  

---

## 1. Embedding Model Specifications

| Property | Value | Description & Technical Rationale |
| :--- | :--- | :--- |
| **Model Name** | **`text-embedding-004`** | Google's premier dense embedding foundation model for semantic search. |
| **Provider** | **Google Cloud Vertex AI** | Executed in Google Cloud region `asia-south1` (Mumbai). |
| **Vector Dimensions** | **768 float32 dimensions** | Fixed dimension. Matches PostgreSQL `VECTOR(768)` column type. |
| **Normalization** | **$L_2$ Unit Normalization** | Vectors are scaled such that $\|v\|_2 = \sqrt{\sum_{i=1}^{768} v_i^2} = 1.000000$. |
| **Similarity Metric** | **Cosine Similarity / Cosine Distance** | In pgvector, queried with the `<=>` operator: `ORDER BY embedding <=> query_vector`. |
| **Task Type** | `RETRIEVAL_DOCUMENT` | Document indexing mode. Downstream user queries use `RETRIEVAL_QUERY`. |
| **API Batch Size** | **100 chunks / call** | Up to 100 chunks sent per Vertex AI request with exponential retry backoff. |

---

## 2. Chunking Specifications & Strategy

| Parameter | Configuration | Rule / Downstream Guarantee |
| :--- | :--- | :--- |
| **Target Chunk Size** | **$< 512$ tokens** | Strict ceiling. Fits within LLM context windows without truncation. |
| **Tokenizer / Encoding** | `cl100k_base` | Uses OpenAI `tiktoken` standard encoding for deterministic token counts. |
| **Sliding Window Overlap**| **50 tokens** | Preserves semantic continuity between consecutive chunks on the same page. |
| **Page-Boundary Rule** | **Strict Isolation (0 cross-page)** | **A chunk NEVER spans across two PDF pages.** Every chunk has an unambiguous `original_page_number`. Enables 100% verifiable citations (`document.pdf#page=N`). |
| **Deterministic Chunk ID** | `{doc_id}_p{page}_c{index}` | Example: `1950_1_30_63_EN_p1_c1`. Enables idempotent upserts (`ON CONFLICT DO UPDATE`). |
| **Legal Section Heading** | Context Inheritance | Detects legal section headers (`FACTS`, `ISSUES`, `ARGUMENTS`, `HELD`, `ORDER`) and propagates them to all child chunks under `section_heading`. |
| **Text Cleaning** | Legal Regex | Strips running court headers (*"SUPREME COURT REPORTS [1950]"*) and page footers. |

---

## 3. Database Schema: Complete 9-Table Definition (`scrag`)

The pipeline writes to **9 tables** in PostgreSQL 16 on instance `sc-rag-pg`:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           9-TABLE RELATIONAL MAP                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   judgments (Master Case Catalog)                                          │
│      │                                                                      │
│      ├──────► citations (Precedents & Statutory References)                 │
│      │                                                                      │
│      └──────► source_files (PDF Storage & SHA-256 Hashes)                   │
│                  │                                                          │
│                  ├──────► source_pages (Raw Extracted Page Text)            │
│                  │                                                          │
│                  ├──────► ingestion_runs (Pipeline Audit & Heartbeats)      │
│                  │           │                                              │
│                  │           └──────► extraction_runs (Parser Diagnostics)   │
│                  │                                                          │
│                  └──────► chunks (Page-Isolated Text Chunks + VECTOR 768)   │
│                              │                                              │
│                              └──────► chunk_embeddings (Vector Sync Status) │
│                                          ▲                                  │
│   embedding_models (Model Registry) ─────┘                                  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### TABLE 1: `public.judgments`
*Master catalog of Supreme Court judgments, parties, dates, and primary citations.*

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `judgment_uuid` | `UUID` | `PRIMARY KEY` | Globally unique identifier (UUIDv5 derived from `judgment_id`). |
| `judgment_id` | `TEXT` | `NOT NULL UNIQUE` | Standardized ID, e.g. `'1950_1_30_63_EN'`. |
| `court` | `TEXT` | `DEFAULT 'Supreme Court of India'` | Court name. |
| `case_number` | `TEXT` | — | Official docket number, e.g. `'Civil Appeal No. 30'`. |
| `judgment_date` | `DATE` | — | Date judgment was delivered (`YYYY-MM-DD`). |
| `citation_text` | `TEXT` | — | Primary citation, e.g. `'[1950] 2 S.C.R. 30'`. |
| `reporter_citations`| `TEXT` | — | Secondary citations (AIR, SCC, SCR). |
| `legal_status` | `TEXT` | `DEFAULT 'REPORTED'` | `'REPORTED'`, `'UNREPORTED'`, `'OVERRULED'`. |
| `is_primary` | `BOOLEAN` | `DEFAULT TRUE` | Primary reporting flag. |
| `overruled_by_judgment_uuid` | `UUID` | `REFERENCES judgments(judgment_uuid)` | Nullable link to overruling precedent. |
| `created_at` | `TIMESTAMPTZ`| `DEFAULT CURRENT_TIMESTAMP` | Row creation timestamp. |
| `updated_at` | `TIMESTAMPTZ`| `DEFAULT CURRENT_TIMESTAMP` | Row update timestamp. |

---

### TABLE 2: `public.source_files`
*Manifest of PDF files ingested from GCS, recording byte sizes, versions, and SHA-256 hashes.*

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `source_file_uuid` | `UUID` | `PRIMARY KEY` | Unique file tracking UUID. |
| `judgment_uuid` | `UUID` | `REFERENCES judgments ON DELETE CASCADE` | Foreign key to parent judgment. |
| `version_number` | `TEXT` | `DEFAULT 'v1.0'` | Pipeline run version. |
| `is_current` | `BOOLEAN` | `DEFAULT TRUE` | Active file flag. |
| `original_filename`| `TEXT` | `NOT NULL` | PDF filename, e.g. `'1950_1_30_63_EN.pdf'`. |
| `storage_path` | `TEXT` | `NOT NULL` | Full GCS URI, e.g. `'gs://.../year=1950/...'`. |
| `page_count` | `INTEGER` | — | Total physical PDF pages. |
| `ingestion_status` | `TEXT` | `DEFAULT 'COMPLETED'` | `'COMPLETED'`, `'FAILED'`, `'SKIPPED_CORRUPT'`. |
| `pipeline_version` | `TEXT` | `DEFAULT 'scj-ingestion-v1'` | Ingestion software version. |
| `content_sha256` | `CHAR(64)` | — | SHA-256 hash of PDF for duplicate detection. |
| `file_size_bytes` | `BIGINT` | — | PDF file size in bytes. |
| `created_at` | `TIMESTAMPTZ`| `DEFAULT CURRENT_TIMESTAMP` | Ingestion timestamp. |
| `updated_at` | `TIMESTAMPTZ`| `DEFAULT CURRENT_TIMESTAMP` | Last modified timestamp. |

---

### TABLE 3: `public.chunks`
*The core retrieval payload storing page-isolated text chunks, metadata, and 768-dim embeddings.*

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `chunk_uuid` | `UUID` | `PRIMARY KEY` | Unique chunk UUID (UUIDv5 derived from `chunk_id`). |
| `chunk_id` | `VARCHAR(120)` | `NOT NULL UNIQUE` | Deterministic ID: `{doc_id}_p{page}_c{index}`. |
| `judgment_uuid` | `UUID` | `REFERENCES judgments ON DELETE CASCADE` | Link to judgment record. |
| `source_file_uuid` | `UUID` | `REFERENCES source_files ON DELETE CASCADE`| Link to source PDF file. |
| `chunk_index` | `INTEGER` | `NOT NULL` | Global sequential chunk number within document. |
| `page_start` | `INTEGER` | `NOT NULL` | Starting physical PDF page. |
| `page_end` | `INTEGER` | `NOT NULL` | Ending physical PDF page (`page_start == page_end`). |
| `content` | `TEXT` | `NOT NULL` | Clean text chunk ($< 512$ tokens) for LLM context. |
| `section` | `TEXT` | — | Tagged legal section (`FACTS`, `ISSUES`, `HELD`, etc.). |
| `metadata` | `JSONB` | `DEFAULT '{}'` | Case metadata JSON (`appellant`, `respondent`, `judges`). |
| `content_sha256` | `CHAR(64)` | `NOT NULL` | SHA-256 hash of chunk text for deduplication. |
| `is_active` | `BOOLEAN` | `DEFAULT TRUE` | Search filter flag. |
| `embedding` | **`VECTOR(768)`** | — | **Vertex AI `text-embedding-004` dense vector.** |
| `created_at` | `TIMESTAMPTZ`| `DEFAULT CURRENT_TIMESTAMP` | Timestamp created. |
| `updated_at` | `TIMESTAMPTZ`| `DEFAULT CURRENT_TIMESTAMP` | Timestamp updated. |

---

### TABLE 4: `public.chunk_embeddings`
*Dedicated vector management table tracking synchronization with multi-store registries.*

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `chunk_embedding_uuid` | `UUID` | `PRIMARY KEY` | Unique embedding record UUID. |
| `chunk_uuid` | `UUID` | `REFERENCES chunks ON DELETE CASCADE` | Foreign key to `chunks`. |
| `embedding_model_uuid` | `UUID` | `REFERENCES embedding_models` | Foreign key to model registry. |
| `provider` | `TEXT` | `DEFAULT 'vertex_ai'` | Model provider (`vertex_ai`). |
| `vector_store` | `TEXT` | `DEFAULT 'pgvector'` | Destination store (`pgvector`). |
| `sync_status` | `TEXT` | `DEFAULT 'SYNCED'` | `'PENDING'`, `'SYNCED'`, `'FAILED'`. |
| `last_synced_at` | `TIMESTAMPTZ`| — | Timestamp of last synchronization. |
| `last_error` | `TEXT` | — | Error message if sync failed. |
| `created_at` | `TIMESTAMPTZ`| `DEFAULT CURRENT_TIMESTAMP` | Creation timestamp. |
| `updated_at` | `TIMESTAMPTZ`| `DEFAULT CURRENT_TIMESTAMP` | Update timestamp. |

---

### TABLE 5: `public.embedding_models`
*Registry of supported and active embedding foundation models.*

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `embedding_model_uuid` | `UUID` | `PRIMARY KEY` | Fixed UUID (`...768`). |
| `model_id` | `TEXT` | `NOT NULL` | `'text-embedding-004'`. |
| `provider` | `TEXT` | `NOT NULL` | `'vertex_ai'`. |
| `version` | `TEXT` | — | `'v1'`. |
| `dimensions` | `INTEGER` | `NOT NULL DEFAULT 768` | Fixed **768 dimensions**. |
| `is_active` | `BOOLEAN` | `DEFAULT TRUE` | Active model flag. |
| `created_at` | `TIMESTAMPTZ`| `DEFAULT CURRENT_TIMESTAMP` | Creation timestamp. |

---

### TABLE 6: `public.source_pages`
*Page-by-page raw text extraction archive for deep-link preview and citation verification.*

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `source_page_uuid` | `UUID` | `PRIMARY KEY` | Page record UUID. |
| `source_file_uuid` | `UUID` | `REFERENCES source_files ON DELETE CASCADE` | Foreign key to source PDF. |
| `page_number` | `INTEGER` | `NOT NULL` | Physical 1-based page index. |
| `extraction_method`| `TEXT` | `DEFAULT 'pymupdf'` | `'pymupdf'` or `'tesseract_ocr'`. |
| `content` | `TEXT` | — | Full raw page text. |
| `quality_score` | `NUMERIC` | `DEFAULT 0.95` | OCR quality confidence score. |
| `created_at` | `TIMESTAMPTZ`| `DEFAULT CURRENT_TIMESTAMP` | Timestamp created. |

---

### TABLE 7: `public.citations`
*Precedents, constitutional articles, and statutes cited within judgments.*

| Column | Type | Constraints | Description |
| :--- | :--- | :--- | :--- |
| `citation_uuid` | `UUID` | `PRIMARY KEY` | Citation record UUID. |
| `judgment_uuid` | `UUID` | `REFERENCES judgments ON DELETE CASCADE` | Foreign key to citing judgment. |
| `citation_text` | `TEXT` | `NOT NULL` | Raw citation string, e.g. `'AIR 1950 SC 124'`. |
| `citation_type` | `TEXT` | `DEFAULT 'REPORTER'` | `'REPORTER'`, `'STATUTE'`, `'CONSTITUTION'`. |
| `normalized_citation`| `TEXT` | — | Clean normalized format. |
| `created_at` | `TIMESTAMPTZ`| `DEFAULT CURRENT_TIMESTAMP` | Timestamp extracted. |

---

### TABLE 8: `public.ingestion_runs` (AUDIT LOG TABLE)
*Tracks pipeline batch execution heartbeats, worker IDs, and overall run statuses.*

| Column | Type | Description |
| :--- | :--- | :--- |
| `ingestion_run_uuid` | `UUID PRIMARY KEY` | Audit run tracking ID. |
| `source_file_uuid` | `UUID REFERENCES source_files` | Ingested PDF reference. |
| `pipeline_name` | `TEXT DEFAULT 'scj-data-ingestion-v1'` | Ingestion software pipeline tag. |
| `processing_status`| `TEXT NOT NULL` | `'COMPLETED'`, `'FAILED'`. |
| `idempotency_key` | `TEXT UNIQUE` | Prevents redundant duplicate executions. |
| `worker_id` | `TEXT` | Cloud Run container task ID. |
| `started_at` | `TIMESTAMPTZ` | Start timestamp. |
| `finished_at` | `TIMESTAMPTZ` | Completion timestamp. |

---

### TABLE 9: `public.extraction_runs` (PARSER AUDIT TABLE)
*Records parser-specific telemetry, OCR transitions, and error traces per document.*

| Column | Type | Description |
| :--- | :--- | :--- |
| `extraction_run_uuid`| `UUID PRIMARY KEY` | Parser diagnostic run ID. |
| `source_file_uuid` | `UUID REFERENCES source_files` | Ingested PDF reference. |
| `pipeline_version` | `TEXT` | Engine used: `'pymupdf'`, `'tesseract_ocr'`. |
| `processing_status`| `TEXT NOT NULL` | Status of parser execution. |
| `error_message` | `TEXT` | Stack trace if extraction failed. |
| `started_at` | `TIMESTAMPTZ` | Parser start time. |
| `finished_at` | `TIMESTAMPTZ` | Parser finish time. |

---

## 4. Vector Indexes & Search Optimization

To achieve **< 25 ms** similarity search across ~3.5 million chunks:

```sql
-- 1. Primary HNSW Cosine Index for pgvector
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw 
ON chunks USING hnsw (embedding vector_cosine_ops) 
WITH (m = 16, ef_construction = 64);

-- 2. Partial HNSW Index (Active chunks only)
CREATE INDEX IF NOT EXISTS idx_chunks_active_embedding 
ON chunks USING hnsw (embedding vector_cosine_ops) 
WHERE is_active = TRUE;

-- 3. Fast Trigram GIN Indexes for Case Number and Citation lookups
CREATE INDEX IF NOT EXISTS idx_judgments_case_number_trgm ON judgments USING gin (case_number gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_judgments_citation_text_trgm ON judgments USING gin (citation_text gin_trgm_ops);
```

---

## 5. Downstream Consumer Summary

| Teammate | Workstream | Consumed Table(s) & Fields |
| :--- | :--- | :--- |
| **Anuradha** | Database Architect | Manages `scrag` schema, `VECTOR(768)` indexing, autovacuum tuning. |
| **Pratik** | Retrieval Engineer | Queries `chunks.embedding` using `<=>` cosine distance, filters by `section`. |
| **Nilesh** | RAG Integrator | Reads `chunks.content`, `page_start`, formats context prompt for Gemini. |
| **Tanvi** | Frontend Engineer | Uses `original_page_number` to render clickable PDF citations (`#page=N`). |

