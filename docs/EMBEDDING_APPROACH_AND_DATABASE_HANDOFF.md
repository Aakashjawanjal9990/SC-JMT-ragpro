# Vector Embedding Architecture & Database Handoff Specification

**Document Title**: Supreme Court Judgment RAG — Vector Embedding Architecture & Database Schema Handoff  
**Target Audience**: Anuradha (Database Architect) & Database Engineering Team  
**Author**: Kedar (Data Ingestion & Pipeline Engineer)  
**Date**: October 2026  
**System Scale**: ~43,000 Supreme Court PDFs (1950–Present) $\rightarrow$ ~3.5 Million Legal Text Chunks  

---

## 1. Executive Summary

This specification explains how text embeddings are generated, validated, and persisted for the Supreme Court of India Judgment RAG platform. 

To ensure stability across ~3.5 million chunks, the system uses a **decoupled two-stage architecture**:
1. **Stage 1 (CPU/RAM-bound)**: PDF parsing, OCR cleanup, legal header/footer stripping, and section-aware chunking ($<512$ tokens). Chunks are written to Google Cloud Storage (`gs://sc-judgments-rag-prod-data/deliverables/chunks/`) and staged into the database.
2. **Stage 2 (Network I/O-bound)**: High-throughput batch vectorization using Google Cloud Vertex AI and upsertion into Cloud SQL PostgreSQL (`pgvector`).

---

## 2. Model & Vector Specifications

| Attribute | Value / Specification | Notes for Database Architect |
| :--- | :--- | :--- |
| **Foundation Model** | `text-embedding-004` | Google Vertex AI second-generation dense embeddings |
| **GCP Region** | `asia-south1` (Mumbai) | In-region compliance with data localization mandates |
| **Embedding Dimensions** | **768** | Fixed dense float32 array; unit L2-normalized |
| **L2 Norm** | $\|v\|_2 = 1.0 \pm 10^{-5}$ | Cosine distance $\Leftrightarrow$ Dot product $\Leftrightarrow$ Euclidean distance |
| **Batching Size** | **100 chunks / API call** | Optimal throughput for Vertex AI quota management |
| **Max Token Input** | 2,048 tokens | Our chunking enforces strict $<512$ tokens to prevent truncations |
| **Local Testing Fallback** | Deterministic 768-dim generator | Uses SHA-256 seeding for offline validation without cloud costs |

---

## 3. Database Schema & pgvector Mapping

In accordance with our unified 9-table schema, embeddings are persisted and audited across two primary tables:

### 3.1. `chunks` Table (Primary Vector Storage)

Each text chunk stores its 768-dimensional vector alongside its structural and legal metadata:

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS chunks (
    chunk_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_id TEXT UNIQUE NOT NULL,                          -- e.g., 'INSC_2023_CA_1042_p1_c1'
    judgment_uuid UUID NOT NULL REFERENCES judgments(judgment_uuid) ON DELETE CASCADE,
    source_file_uuid UUID NOT NULL REFERENCES source_files(source_file_uuid) ON DELETE CASCADE,
    extraction_run_uuid UUID REFERENCES extraction_runs(extraction_run_uuid) ON DELETE SET NULL,
    last_seen_ingestion_run_uuid UUID REFERENCES ingestion_runs(ingestion_run_uuid) ON DELETE SET NULL,
    chunk_index INTEGER NOT NULL,                           -- Sequential index (1, 2, 3...) per document
    page_start INTEGER NOT NULL,                            -- Physical PDF start page
    page_end INTEGER NOT NULL,                              -- Physical PDF end page
    content TEXT NOT NULL,                                  -- Clean Markdown legal payload (<512 tokens)
    section TEXT,                                           -- Legal section heading (e.g. 'APPEARANCES', 'JUDGMENT')
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,            -- Case number, judges, appellant, respondent, date
    content_sha256 CHAR(64) NOT NULL,                       -- Checksum for idempotency & deduplication
    embedding VECTOR(768),                                  -- 768-dim dense embedding
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_file_uuid, chunk_index)
);
```

### 3.2. `chunk_embeddings` Table (Sync Status & Audit Trail)

Tracks synchronization status across vector stores and embedding models (e.g., during future model re-indexing):

```sql
CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_embedding_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_uuid UUID NOT NULL REFERENCES chunks(chunk_uuid) ON DELETE CASCADE,
    embedding_model_uuid UUID REFERENCES embedding_models(embedding_model_uuid) ON DELETE SET NULL,
    provider TEXT NOT NULL DEFAULT 'vertex_ai',
    corpus_resource_name TEXT,
    external_chunk_resource_name TEXT,
    embedding_ref TEXT,                                     -- Optional reference pointer or raw vector backup
    vector_store TEXT DEFAULT 'pgvector',                   -- 'pgvector' in Cloud SQL / 'sqlite_json' locally
    sync_status TEXT NOT NULL DEFAULT 'SYNCED',             -- 'PENDING', 'SYNCED', 'FAILED'
    last_synced_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (chunk_uuid, embedding_model_uuid)
);
```

---

## 4. Indexing Strategy for 3.5 Million Chunks

For similarity search in PostgreSQL Cloud SQL with 3.5 million chunks, we recommend **HNSW (Hierarchical Navigable Small World)** with `vector_cosine_ops`:

```sql
-- Recommended production vector index
CREATE INDEX idx_chunks_embedding_hnsw 
ON chunks USING hnsw (embedding vector_cosine_ops) 
WITH (m = 16, ef_construction = 64);

-- Recommended partial index for active chunks only
CREATE INDEX idx_chunks_active_embedding 
ON chunks USING hnsw (embedding vector_cosine_ops) 
WHERE is_active = TRUE;
```

### Resource Sizing for 3.5M Vectors (768 Dimensions)
- **Raw Vector Footprint**: $3,500,000 \times 768 \times 4\text{ bytes} \approx 10.75\text{ GB}$.
- **HNSW Index Overhead**: $\approx 1.2 \times \text{Raw Vector Size} \approx 13\text{ GB}$.
- **Total In-Memory Target**: ~24 GB RAM allocated to PostgreSQL `shared_buffers` and maintenance work memory to ensure the entire graph index resides in RAM for sub-10ms query latency.

---

## 5. Sample Retrieval Queries

### 5.1. Pure Semantic Vector Nearest-Neighbor Search
```sql
SELECT 
    c.chunk_id,
    c.page_start,
    c.content,
    1 - (c.embedding <=> :query_vector::vector) AS cosine_similarity
FROM chunks c
WHERE c.is_active = TRUE
ORDER BY c.embedding <=> :query_vector::vector ASC
LIMIT 10;
```

### 5.2. Hybrid Filtered Vector Search (e.g. By Year & Judge)
```sql
SELECT 
    j.judgment_id,
    j.case_number,
    j.judgment_date,
    c.chunk_id,
    c.page_start,
    c.content,
    1 - (c.embedding <=> :query_vector::vector) AS similarity_score
FROM chunks c
JOIN judgments j ON c.judgment_uuid = j.judgment_uuid
WHERE c.is_active = TRUE
  AND j.judgment_date >= '2020-01-01'
  AND c.metadata->'judges' @> '["Dr. Dhananjaya Y. Chandrachud, CJI"]'::jsonb
ORDER BY c.embedding <=> :query_vector::vector ASC
LIMIT 10;
```

---

## 6. How to Run the Verification Script

Anuradha can execute the standalone demonstration script locally without cloud dependencies:

```bash
# Run standalone verification demo
python embedding_demo_for_anuradha.py
```

### Output of the Script:
1. Generates 768-dimensional dense vectors on sample legal judgment text.
2. Validates L2 normalization ($\|v\|_2 = 1.000000$).
3. Executes a live semantic cosine similarity comparison.
4. Confirms that all 88 chunks in the local database have non-null vectors synced with `sync_status = 'SYNCED'`.
5. Verifies the JSON deliverable at [`output/sample_embedded_chunk_for_anuradha.json`](file:///E:/Dummy/output/sample_embedded_chunk_for_anuradha.json).

---

## 7. Deliverables Summary

| Deliverable | File Path | Status |
| :--- | :--- | :--- |
| **Architecture Specification** | `EMBEDDING_APPROACH_AND_DATABASE_HANDOFF.md` | Complete |
| **Interactive Demo Script** | `embedding_demo_for_anuradha.py` | Complete & Tested |
| **Batch Embedding Engine** | `batch_embedder.py` | Complete (Production Ready) |
| **Database Loader (9 Tables)** | `db_loader.py` | Complete (Postgres & SQLite) |
| **Sample JSON Deliverable** | `output/sample_embedded_chunk_for_anuradha.json` | Generated (15.7 KB) |
| **Full Local Chunks** | `output/chunks/*.json` (4 files, 88 chunks) | Vectorized & Injected |

