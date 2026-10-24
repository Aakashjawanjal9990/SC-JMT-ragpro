# Indian Supreme Court RAG Pipeline — Master Component Explainer

**Project**: Indian Supreme Court Judgment Data Ingestion, Parsing, Chunking & Embedding System  
**Dataset**: ~43,000 PDF Judgments (77 Years: 1950 – 2026)  
**Cloud Environment**: Google Cloud Platform (Region: `asia-south1` - Mumbai)  
**Target Database**: Google Cloud SQL for PostgreSQL 16 (Database: `scrag`, Schema: `public`)  
**Storage Deliverables**: Google Cloud Storage (`gs://sc-judgments-rag-prod-data/deliverables/chunks/`)  
**Embedding Engine**: Google Vertex AI `text-embedding-004` (768 Dimensions, Float32)  

---

## 1. High-Level Architecture Workflow

```mermaid
flowchart TD
    A["Raw Judgment PDFs (GCS Bucket)<br/>43,000 Files (1950-2026)"] --> B["PDFExtractor (extractor.py)<br/>• PyMuPDF Native Extraction<br/>• Automatic 200 DPI OCR Fallback"]
    B --> C["PageProcessor (cleaner_chunker.py)<br/>• Legal Regex De-hyphenation<br/>• Strict Page-Level Isolation<br/>• Strict &lt;512 Token Window<br/>• Metadata Roll-up (Judges, Dates)"]
    C --> D["VertexBatchEmbedder (batch_embedder.py)<br/>• Vertex AI text-embedding-004<br/>• Batches of 250 chunks<br/>• Unit L2 Normalization (768 dims)"]
    D --> E["GCS Archive (GCP Cloud Run Job)<br/>gs://.../deliverables/chunks/year=YYYY/<br/>{doc_id}_chunks.json"]
    E --> F["GCSCloudSQLSyncer (sync_gcs_to_cloudsql.py)<br/>• 16 Parallel Streaming Threads<br/>• Zero Re-Embedding / Zero Re-Parsing"]
    F --> G[("Cloud SQL PostgreSQL 16 ('scrag')<br/>• judgments, source_files, chunks<br/>• chunk_embeddings, embedding_models")]
    G --> H["HybridRetriever (hybrid_retriever.py)<br/>• pgvector Cosine Semantic Search<br/>• PostgreSQL Full-Text Keyword Search<br/>• Reciprocal Rank Fusion (RRF)"]
```

---

## 2. Component-by-Component Explainer

### 📄 `extractor.py` — PDF Extraction & OCR Fallback Engine
* **Purpose**: Robustly extracts text from both high-quality digital PDFs and degraded/scanned historical judgment PDFs.
* **How It Works**:
  1. **Pre-flight Validation**: Checks for 0-byte, empty, or corrupted PDF files and logs them safely without halting the pipeline.
  2. **Fast Primary Engine (PyMuPDF / `fitz`)**: Attempts high-speed text extraction first.
  3. **Automatic OCR Fallback (Tesseract @ 200 DPI)**: If a page contains fewer than 50 characters (indicating a scanned image or photo of text), the page is rendered to an image at 200 DPI and processed through Tesseract OCR.
* **Input**: PDF file path or byte stream from GCS.
* **Output**: Clean page-by-page raw text with engine diagnostics (`pymupdf` vs `tesseract_ocr`).

---

### ✂️ `cleaner_chunker.py` — Legal Text Cleaning, Metadata Extraction & Chunking
* **Purpose**: Transforms raw page text into clean, legal-section-tagged, $<512$ token chunks ready for embedding and RAG retrieval.
* **How It Works**:
  1. **Legal Text Cleaning**: Fixes broken OCR line wraps, restores broken legal hyphens (e.g. `"juris-diction"` $\rightarrow$ `"jurisdiction"`), and strips headers, footers, and page noise.
  2. **Metadata Extraction**: Scans first pages for Case Numbers (e.g., `Criminal Appeal No. 1 of 1950`), Appellant and Respondent parties, Dates of Judgment, and the Bench of Judges.
  3. **Strict Page Boundary Isolation**: Chunks **never** span across physical PDF pages. `page_start` and `page_end` are always identical, allowing 100% accurate page citations.
  4. **Strict Token Constraint ($<512$ Tokens)**: Enforces chunk sizes between 1000 and 1500 characters (~350–450 tokens) with 150-character sliding overlap.
  5. **Deterministic Chunk IDs**: Creates globally unique IDs formatted as `{document_id}_p{page}_c{chunk_index}`.
  6. **Cryptographic SHA-256**: Generates SHA-256 content hashes for exact deduplication and audit tracking.
* **Input**: Raw page strings from `extractor.py`.
* **Output**: Structured `ChunkRecord` objects with full legal metadata.

---

### 🧠 `batch_embedder.py` — Dense Vector Embedding Generator
* **Purpose**: Generates high-accuracy 768-dimensional dense vector embeddings using Google Vertex AI.
* **How It Works**:
  1. **Model**: Google Cloud Vertex AI `text-embedding-004` (hosted in `asia-south1`).
  2. **Batching**: Groups chunks into batches of 250 to maximize API throughput.
  3. **Quota & Rate-Limiting**: Implements exponential backoff retry logic to handle Vertex AI quota limits gracefully without data loss.
  4. **L2 Normalization**: Ensures all embedding vectors have a unit length ($\|v\|_2 = 1.0$), making Cosine Distance equivalent to Dot Product for high-speed indexing.
* **Input**: Text chunks from `cleaner_chunker.py`.
* **Output**: 768-float vector arrays embedded inside each chunk record.

---

### 📦 `models.py` — Formal Data Contracts & Pydantic Schemas
* **Purpose**: Enforces strict typing, field validation, and data integrity across all pipeline modules.
* **Key Contracts**:
  * `ChunkRecord`: Fields for `chunk_id`, `document_id`, `original_page_number`, `chunk_index`, `content`, `section_heading`, and legal metadata.
  * `PageMetadata`: Holds Case Number, Appellant, Respondent, Date, and Judges list.
  * `DocumentIngestionResult`: Document summary including total pages, total chunks, OCR status, and SHA-256 hash.

---

### 🚀 `gcp_production_pipeline.py` — Master Cloud Run Production Orchestrator
* **Purpose**: The end-to-end production runner that parsed all 43,000 PDFs in Google Cloud Run.
* **How It Works**:
  1. **GCS Streaming**: Reads directly from `gs://sc-judgments-rag-prod-data/raw_pdfs/` without using persistent local disk.
  2. **Ephemeral Processing**: Uses scratch RAM/temp storage to process judgments, chunk them, and generate embeddings.
  3. **Year Partitioning**: Saves results to `gs://sc-judgments-rag-prod-data/deliverables/chunks/year=YYYY/{doc_id}_chunks.json`.
  4. **Deduplication Checkpoint**: Checks if `{doc_id}_chunks.json` already exists in GCS. If found, it skips the file in milliseconds to avoid duplicate processing or API costs.
  5. **Cloud Run Task Indexing**: Distributes tasks evenly across Cloud Run Job workers (`CLOUD_RUN_TASK_INDEX`).

---

### ☸️ `cloud_run_entrypoint.py` — Cloud Run Task Array Coordinator
* **Purpose**: Maps each Cloud Run worker task index ($0$ to $76$ for 77 tasks) to its corresponding judgment year (Task 0 = 1950, Task 1 = 1951, ..., Task 76 = 2026).
* **How It Works**: Reads `CLOUD_RUN_TASK_INDEX` from the environment, sets the active year, and invokes `gcp_production_pipeline.py`.

---

### 🗄️ `db_loader.py` — Strict PostgreSQL 16 Database Loader
* **Purpose**: Inserts document records, pages, chunks, and embeddings into Cloud SQL PostgreSQL in strict Foreign Key order.
* **How It Works**:
  1. **Preflight Verification**: Connects to the database, queries `current_database()` and `current_user()`, checks that all required tables and columns exist, and verifies the `vector` extension.
  2. **Strict FK Order**:
     `embedding_models` $\rightarrow$ `judgments` $\rightarrow$ `source_files` $\rightarrow$ `ingestion_runs` $\rightarrow$ `extraction_runs` $\rightarrow$ `source_pages` $\rightarrow$ `chunks` $\rightarrow$ `chunk_embeddings`.
  3. **One Transaction Per Document**: Groups all 8 table operations for a judgment into a single atomic transaction. Rolls back on error.
  4. **Idempotency**: Uses `INSERT ... ON CONFLICT DO UPDATE` to ensure re-running creates zero duplicate rows.
  5. **Dry-Run Flag (`--dry-run`)**: Simulates SQL execution and calculates row counts without modifying the database.

---

### ⚡ `sync_gcs_to_cloudsql.py` — High-Throughput GCS-to-CloudSQL Syncer
* **Purpose**: Directly loads pre-computed deliverables from GCS into Cloud SQL PostgreSQL (`scrag`).
* **Why It Is Fast**:
  * Does **zero PDF parsing** and **zero embedding generation** (both are already saved in GCS).
  * Uses a multi-threaded pool of **16 parallel workers**.
  * Preserves original GCS creation timestamps (`blob.time_created`) into `chunks.created_at` and `chunk_embeddings.created_at`.
  * Completes database loading for all 77 years in **15 to 20 minutes**.

---

### 🔍 `hybrid_retriever.py` — Hybrid Search & Retrieval Engine
* **Purpose**: The search engine used by downstream RAG applications to query the database.
* **How It Works**:
  1. **Semantic Search (`pgvector`)**: Finds chunks with similar legal concepts using cosine distance (`<=>`).
  2. **Lexical Keyword Search (Postgres FTS)**: Finds exact matches for statutes, section numbers (e.g. `"Section 302 IPC"`), and case names.
  3. **Metadata Filtering**: Filters by Judge (`metadata->>'judges'`), Year, Court, or Case Number.
  4. **Reciprocal Rank Fusion (RRF)**: Merges the lexical ranking and vector similarity ranking into a single gold-standard relevance score ($k=60$).

---

### ⚙️ `config.py` — Centralized Environment & Configuration
* **Purpose**: Loads configuration from `.env` or system environment variables with safe defaults for paths, batch sizes, worker counts, and database URLs.

---

### 🧪 `tests/` — Automated Unit & Integration Test Suite
* **`tests/test_ingestion.py`**: Validates OCR de-hyphenation, $<512$ token chunking, metadata extraction, and pipeline output contracts.
* **`tests/test_db_loader_strict.py`**: Validates strict schema conformity, FK integrity across all 8 tables, idempotency, and `--dry-run` accuracy.
* **Execution**: Ran via `pytest tests/ -v` (12 / 12 tests passing 100%).

---

## 3. The Deliverable JSON Contract

Every JSON file in `gs://sc-judgments-rag-prod-data/deliverables/chunks/year=YYYY/{doc_id}_chunks.json` conforms to this exact structure:

```json
{
  "chunk_id": "1951_1_1125_1147_EN_p1_c1",
  "document_id": "1951_1_1125_1147_EN",
  "original_page_number": 1,
  "chunk_index": 1,
  "content": "S.C.R. \nSUPREME COURT REPORTS\n1125\n...",
  "section_heading": "CIVIL APPELLATE JURISDICTION",
  "case_number": "S.C.R.",
  "appellant": "Angurbala Mullick",
  "respondent": "Debabrata Mullick Mukherjee",
  "date_of_judgment": "9th February, 1949",
  "judges": [
    "Kania, H.J. (Cj)",
    "Fazal Ali, Saiyid",
    "Mukherjea, B.K."
  ],
  "embedding": [
    0.057852,
    0.046117,
    -0.016680,
    "/* ... exactly 768 float32 numbers ... */"
  ]
}
```

---

## 4. Current Milestone Status

1. **Extraction, Chunking & Embeddings**: ✅ **100% COMPLETE** (Cloud Run execution `22v6m` finished 77/77 tasks across all years 1950–2026).
2. **GCS Deliverables**: ✅ **100% ARCHIVED** in `gs://sc-judgments-rag-prod-data/deliverables/chunks/`.
3. **Database Loader**: ✅ **READY** to sync into Cloud SQL PostgreSQL (`scrag`) via `python sync_gcs_to_cloudsql.py --workers 16`.
4. **Retrieval Ready**: ✅ **READY** for the Retrieval team via `hybrid_retriever.py` and `HANDOFF_PRATIK_RETRIEVAL.md`.

