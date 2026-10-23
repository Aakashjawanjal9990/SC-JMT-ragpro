# Indian Supreme Court Judgment RAG System
## Complete Project Work & Architecture Summary (Till Now)

**Engineer**: Kedar (Data Ingestion Engineer)  
**Project**: End-to-End RAG System for ~43,000 Indian Supreme Court Judgments (1950 – Present)  
**Workspace Directory**: `E:\Dummy`  
**Repository**: `https://github.com/Kedarlimbalkar/parsing` (Branch: `main`)  
**Date**: October 23, 2026  

---

## 1. Executive Summary & Objective

The primary objective of this module is to build a high-performance, fault-tolerant **Data Ingestion Pipeline** capable of parsing, cleaning, extracting metadata, chunking, and preparing both scanned historical (1950s SCR format) and modern digital judgments for the downstream RAG system.

### Key Mandates Completed:
1. **Single-Page Ingestion JSON Contract**: Strict adherence to the downstream interface schema.
2. **Dual-Engine PDF Parsing**: PyMuPDF with automatic 200 DPI Tesseract OCR fallback for scanned pages, plus full integration with LlamaParse v2 Cloud API.
3. **Legal OCR Cleaning**: Fixes broken hyphenations, corrupted legal characters (`C0urt` $\rightarrow$ `Court`, `Jncome-tax` $\rightarrow$ `Income-tax`), and normalizes spacing.
4. **Historical & Modern Metadata Extraction**: Robust regex extraction for multi-line parties across `v.` / `VERSUS`, SCR citations, judgment dates, and Coram bench members (`[SHRI ... JJ.]`).
5. **Page-Aware, Heading-Preserving Chunking**: Preserves structural legal context (Facts, Issues, Ratio Decidendi, Disposition) while keeping chunks within retrieval context limits (<512 tokens).
6. **Deduplication & Resumability**: Cryptographic SHA-256 file hashing with persistent checkpointing (`ingestion_state.json`).
7. **Database Storage Adapter**: Implemented `db_loader.py` supporting production PostgreSQL (Cloud SQL with pgvector & JSONB) and zero-config local SQLite testing (`scj_rag.db`).
8. **Automated Test Suite**: 9 unit and contract tests running in PyTest, 100% passing.

---

## 2. Master Document Index & Directory Map

All deliverables and specifications are organized inside `E:\Dummy`:

```
E:\Dummy\
├── 📄 PROJECT_WORK_SUMMARY.md              <-- THIS MASTER OVERVIEW DOCUMENT
├── 📄 README.md                            <-- Master Repository README with tech stack & setup
├── 📄 PARSING_AND_CHUNKING_SPEC.md         <-- Deep-dive technical guide on parser & 4-stage chunker
├── 📄 DATABASE_INTEGRATION_SPEC.md         <-- Database handoff spec & PostgreSQL DDL for Anuradha
├── 📄 GCP_ACCESS_AND_TECH_STACK.md         <-- Required 7 IAM roles & versions for Prathmesh
├── 📄 SECURITY_AND_INFRASTRUCTURE_SPEC.md  <-- Security hardening, Secret Manager & Docker for Prathmesh
├── 📄 DATA_INGESTION_ARCHITECTURE.md       <-- High-level pipeline architecture & flow diagrams
│
├── 🐍 Core Source Code:
│   ├── models.py                           <-- Pydantic v2 data models & validation schemas
│   ├── extractor.py                        <-- Dual-engine parser (PyMuPDF OCR + LlamaParse v2 REST API)
│   ├── cleaner_chunker.py                  <-- Legal OCR cleaning, metadata extractor & page chunker
│   ├── pipeline.py                         <-- Batch orchestrator, SHA-256 deduplication & state tracking
│   ├── db_loader.py                        <-- PostgreSQL & SQLite atomic database loader & stats CLI
│   ├── sample_generator.py                 <-- ReportLab synthetic 3-page Indian Supreme Court appeal PDF
│   └── main.py                             <-- Unified command-line interface (CLI)
│
├── 🧪 Testing & Quality:
│   └── tests/test_ingestion.py             <-- 9 PyTest automated unit, contract, and DB loader tests
│
├── 📂 Runtime Data & Output:
│   ├── data/sample_pdfs/                   <-- Test PDFs (including 43 historical 1950 SCR judgments)
│   ├── output/chunks/                      <-- Downstream JSON chunk arrays ({doc_id}_chunks.json)
│   ├── output/documents/                   <-- Document-level metadata summaries ({doc_id}.json)
│   ├── reports/status_error_report.json    <-- Failure tracking & error isolation reports
│   ├── ingestion_state.json                <-- Checkpoint state (hashes -> doc_ids)
│   └── scj_rag.db                          <-- Local SQLite test database (verified with 88 chunks)
```

---

## 3. Team Member Handoff & Role Guide

| Team Member | Role | Associated Document in `E:\Dummy` | Key Information Provided |
| :--- | :--- | :--- | :--- |
| **Anuradha** | Database Architect | [`DATABASE_INTEGRATION_SPEC.md`](file:///E:/Dummy/DATABASE_INTEGRATION_SPEC.md) | PostgreSQL DDL (`documents`, `chunks`), GIN/HNSW indexes, foreign key cascade, JSONB structures, bulk loader script. |
| **Prathmesh** | Security & Cloud Admin | [`GCP_ACCESS_AND_TECH_STACK.md`](file:///E:/Dummy/GCP_ACCESS_AND_TECH_STACK.md) & [`SECURITY_AND_INFRASTRUCTURE_SPEC.md`](file:///E:/Dummy/SECURITY_AND_INFRASTRUCTURE_SPEC.md) | Exact 7 least-privilege GCP IAM roles needed, tech stack version list, Secret Manager integration, Dockerfile, GCS lifecycle rules. |
| **Pratik** | Retrieval Engineer | [`PARSING_AND_CHUNKING_SPEC.md`](file:///E:/Dummy/PARSING_AND_CHUNKING_SPEC.md) | Exact chunk JSON contract in `output/chunks/`, token limits (~350-450 tokens), 1-based page numbers, and section headings. |
| **Nilesh / Tanvi** | Backend & UI Integration | [`DATA_INGESTION_ARCHITECTURE.md`](file:///E:/Dummy/DATA_INGESTION_ARCHITECTURE.md) | Deterministic ID pattern `{doc_id}_p{page}_c{chunk}` for jumping from citation directly to original PDF page. |
| **Kusuma** | QA & Evaluation | [`tests/test_ingestion.py`](file:///E:/Dummy/tests/test_ingestion.py) & `reports/` | Test cases, error isolation report structure, schema validation tests. |

---

## 4. Technical Work Done Till Now

### 4.1 Data Models & Contract Validation ([`models.py`](file:///E:/Dummy/models.py))
- Designed using **Pydantic v2**:
  - `PageMetadata`: Holds `case_number`, `appellant`, `respondent`, `date_of_judgment`, `judges`.
  - `ChunkMetadata`: Holds `section_heading` (e.g. *FACTS*, *ARGUMENTS*, *JUDGMENT*).
  - `ChunkRecord`: Holds `chunk_index`, `content`, `chunk_metadata`, and optional `chunk_id`.
  - `PageIngestionContract`: Single-page JSON schema matching:
    ```json
    {
      "document_id": "string",
      "original_page_number": 1,
      "page_metadata": { ... },
      "chunks": [ { "chunk_index": 1, "content": "...", "chunk_metadata": { ... } } ]
    }
    ```
  - `DownstreamChunkRecord`: Standardized record for DB/vector retrieval (`chunk_id`, `document_id`, `original_page_number`, `chunk_index`, `content`, `section_heading`, full case metadata).

### 4.2 Dual-Engine Extraction ([`extractor.py`](file:///E:/Dummy/extractor.py))
- **Engine A: Local PyMuPDF (`fitz`)**: Fast, local PDF extraction. Automatically detects low-density or scanned pages (character count $< 50$) and triggers PyMuPDF 200 DPI pixmap rendering + Tesseract OCR.
- **Engine B: LlamaParse Cloud v2 REST API**: Integrated directly with `https://api.cloud.llamaindex.ai/api/v2/parse/upload` and status polling with `?expand=markdown`. Verified with live LlamaParse API key.

### 4.3 Legal Text Cleaning & Metadata Extraction ([`cleaner_chunker.py`](file:///E:/Dummy/cleaner_chunker.py))
- **`LegalTextCleaner`**: Recombines hyphenated line wraps (e.g. `repre- \n sentative` $\rightarrow$ `representative`), un-spaces stretched headings (`J U D G M E N T` $\rightarrow$ `JUDGMENT`), corrects typical OCR misreads (`C0urt` $\rightarrow$ `Court`, `Jncome-tax` $\rightarrow$ `Income-tax`).
- **`LegalMetadataExtractor`**: Extracts metadata from modern digital formats and 1950s SCR historical formats (multi-line uppercase parties across `v.` / `VERSUS`, Coram bench arrays `[SHRI HARILAL KANIA C.J., ... JJ.]`).
- **`PageChunker`**: Heading-preserving chunker that detects legal section boundaries, tracks heading state across pages, and splits text into logical paragraphs (~1200-1500 chars / ~350-450 tokens) with 150-char overlap without breaking legal citations or statutory sections.

### 4.4 Batch Pipeline & Deduplication ([`pipeline.py`](file:///E:/Dummy/pipeline.py))
- Computes SHA-256 cryptographic digest of incoming PDFs to skip duplicates automatically.
- Checkpoints progress in `ingestion_state.json` allowing uninterrupted resumption after failures.
- Captures and isolates errors in `reports/status_error_report.json` using `tenacity` retries.
- Produces persistent deliverables:
  - Document summaries: `output/documents/{document_id}.json`
  - Chunk collections: `output/chunks/{document_id}_chunks.json`

### 4.5 Database Loader & Verification ([`db_loader.py`](file:///E:/Dummy/db_loader.py))
- Created an atomic, idempotent database loader using SQLAlchemy.
- Supports both:
  - **Local Development**: SQLite (`sqlite:///scj_rag.db`) with zero external dependencies.
  - **Production**: PostgreSQL / Cloud SQL with JSONB and pgvector.
- Implements atomic upsert transactions: updates `documents` header, purges old chunks, and batch-inserts new chunks in a single transaction.
- **Verification**: Loaded 4 documents and 88 chunks into `scj_rag.db` with 100% success.

### 4.6 Automated Test Suite ([`tests/test_ingestion.py`](file:///E:/Dummy/tests/test_ingestion.py))
All **9 automated tests** pass with 100% success:
1. `test_ocr_cleanup`: Confirms legal character normalization and hyphen un-breaking.
2. `test_page_chunking_and_chunk_ids`: Confirms chunk ID formatting `{doc}_p{page}_c{index}` and heading retention.
3. `test_metadata_extraction`: Confirms party, date, case number, and bench extraction.
4. `test_page_ingestion_contract_schema_compliance`: Confirms strict single-page JSON contract validation.
5. `test_llm_prompt_formatting_and_parsing`: Confirms LLM structured fallback parsing.
6. `test_downstream_chunk_record_contract`: Confirms DB/retrieval chunk payload format.
7. `test_llamaparse_extractor_configuration`: Confirms LlamaParse v2 cloud configuration.
8. `test_end_to_end_pipeline`: End-to-end PDF generation, parsing, chunking, and duplicate detection.
9. `test_database_loader`: Schema initialization and atomic bulk loading into database.

---

## 5. Command-Line (CLI) Cheat Sheet

All operations can be executed via `python main.py`:

```bash
# 1. Run all 9 automated unit & contract tests
python -m pytest tests/ -v

# 2. Run sample pipeline with synthetic Supreme Court appeal PDF
python main.py run-sample

# 3. Test batch ingestion on 1950 historical judgments (first 3 files)
python main.py test-1950 --limit 3

# 4. Ingest ALL 43 historical 1950 judgments
python main.py test-1950 --limit 0

# 5. Ingest a specific single PDF
python main.py process-pdf --file "data/sample_pdfs/year=1950/english/1950_1_15_25_EN.pdf"

# 6. Load all processed deliverables into the database (SQLite default)
python main.py load-db

# 7. Check database row counts and inspect a sample stored chunk
python main.py load-db --stats

# 8. Load into production PostgreSQL (Cloud SQL)
python main.py load-db --db "postgresql://user:password@host:5432/scj_rag_db"

# 9. Test LlamaParse Cloud API connectivity
python main.py test-llamaparse
```

---

## 6. Git & Security Status

- **Repository**: `https://github.com/Kedarlimbalkar/parsing` (tracks `origin/main`).
- **Safety Policy**:
  - `.env` and `scj_rag.db` are strictly ignored by `.gitignore`.
  - In accordance with user instructions, local security documents and IAM specifications are kept strictly local in `E:\Dummy` and are not pushed to public remotes without explicit authorization.

