# Supreme Court Judgment RAG System — Data Ingestion Pipeline

[![Python](https://img.shields.io/badge/Python-3.10.0-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![PyMuPDF](https://img.shields.io/badge/PyMuPDF-1.25.3-FF6F00)](https://pymupdf.readthedocs.io/)
[![LlamaParse](https://img.shields.io/badge/LlamaParse-0.6.94-7B2CBF)](https://cloud.llamaindex.ai/)
[![Pydantic](https://img.shields.io/badge/Pydantic-2.13.4-E92063?logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15%20%7C%2016-336791?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![pgvector](https://img.shields.io/badge/pgvector-%3E%3D0.7.0-blue)](https://github.com/pgvector/pgvector)
[![Tests](https://img.shields.io/badge/Tests-8%20Passed-success)](tests/test_ingestion.py)

**Owner / Module**: Kedar (Data Ingestion Engineer)  
**Target Dataset**: ~43,000 Indian Supreme Court judgment PDFs (1950 – Present)  
**Repository**: [https://github.com/Kedarlimbalkar/parsing](https://github.com/Kedarlimbalkar/parsing)

---

## 1. Project Overview

The **Data Ingestion Module** is the foundational data processing engine of the Supreme Court Judgment RAG system. It transforms raw, scanned, or complex legal PDF judgments into clean, page-aware, and structured JSON contracts for downstream database persistence, hybrid retrieval (dense vectors + BM25), and verifiable citations.

### Key Capabilities
- **Dual-Engine Extraction**: High-fidelity cloud parsing via **LlamaParse (v2 REST API)** with automatic zero-downtime failover to local **PyMuPDF (`fitz`)** and 200 DPI OCR fallback.
- **Strict Page-Boundary Isolation**: Text is chunked strictly page-by-page. A chunk never crosses page boundaries, guaranteeing 100% verifiable page citations (`original_page_number`).
- **Heading-Preserving Logical Chunking**: Automatically identifies legal sections (`FACTS`, `ISSUES`, `SUBMISSIONS`, `JUDGMENT`, `ORDER`, and 1950s judicial opinion headers like `FAZL ALI J. -`) and tags child paragraphs under `section_heading`.
- **Historical (1950+ S.C.R.) & Modern Judgment Extraction**: Accurately extracts multi-line uppercase party blocks (`v.` or `VERSUS`), bracketed Corams (`[SHRI HARILAL KANIA C.J., ... JJ.]`), case citations (`[1950] S.C.R. 15`), and judgment dates.
- **Production Resilience**: SHA-256 duplicate detection, exponential backoff retries via `tenacity`, and resumable checkpoint state (`ingestion_state.json`).

---

## 2. Tech Stack & Version Specification

### Core Software & Libraries

| Technology / Package | Exact Version | Role in Project |
| :--- | :--- | :--- |
| **Python** | **`3.10.0`** | Primary programming language & runtime. |
| **`llama-parse`** | **`0.6.94`** | Cloud-based PDF parser (v2 REST API) for markdown, complex layouts, and tables. |
| **`llama-cloud`** | **`0.1.46`** | LlamaCloud client library. |
| **`llama-index-core`** | **`0.14.25`** | Core document and node abstraction framework. |
| **`PyMuPDF` (`fitz`)** | **`1.25.3`** | Fast local selectable text extraction, page parsing, and OCR image rendering. |
| **`pydantic`** | **`2.13.4` (v2)** | Strict schema validation and data contracts ([`models.py`](models.py)). |
| **`httpx`** | **`0.28.1`** | Asynchronous/synchronous HTTP client for LlamaParse v2 upload & polling. |
| **`tenacity`** | **`8.5.0`** | Exponential backoff retry engine for network & extraction fault tolerance. |
| **`python-dotenv`** | **`1.0.1`** | Environment variable management (`LLAMA_CLOUD_API_KEY`). |
| **`reportlab`** | **`4.4.1`** | PDF generation toolkit for realistic Supreme Court sample tests. |
| **`pytest`** | **`9.1.1`** | Test runner for unit, contract, and pipeline tests. |

### Shared GCP Cloud & Database Stack
*(Coordinated with **Prathmesh** for Cloud Infra & **Anuradha** for Database Architecture)*

| GCP Service / Component | Version / Specification | Usage in this System |
| :--- | :--- | :--- |
| **Cloud SQL for PostgreSQL** | **PostgreSQL `15` or `16`** | Relational persistence of `documents` and `chunks` tables. |
| **`pgvector` Extension** | **`pgvector >= 0.7.0`** | Vector column (`VECTOR(1536)`) for Pratik's dense embeddings. |
| **`pg_trgm` Extension** | Built-in module | Trigram GIN indexes for fuzzy search on `appellant` and `respondent` names. |
| **Google Cloud Storage (GCS)** | Standard Regional Bucket | Object storage for raw PDFs and JSON deliverable archives. |
| **Google Secret Manager** | GCP Native Service | Secure storage and access to `LLAMA_CLOUD_API_KEY`. |
| **Compute / Container** | **Cloud Run** or **GCE (`e2-standard-4`)** | Worker environment running batch ingestion. |

---

## 3. Architecture & Data Flow

```
[ Raw Judgment PDF ]
        │
        ▼
[ SHA-256 Duplicate Check ] ──(Duplicate found)──► Skip & Log
        │ (New File)
        ├─────────────────────────────┐
        ▼                             ▼
[ LlamaParse Cloud v2 API ]   [ PyMuPDF Local + OCR ]
(Markdown / Complex Tables)   (Fast Selectable Text)
        │                             │
        └──────────────┬──────────────┘
                       │
                       ▼
          [ Legal Text Cleaner ]
          - Fix hyphenated line wraps (repre- \nsentative -> representative)
          - OCR typo correction (C0urt -> Court, Jncome-tax -> Income-tax)
                       │
                       ▼
        [ Legal Metadata Extractor ]
        - Case No / SCR Citation, Parties, Coram / Judges, Date
                       │
                       ▼
        [ Page-Aware Logical Chunker ]
        - Heading isolation & inheritance (FACTS, JUDGMENT, FAZL ALI J. -)
        - Sub-split on \n\n and numbered items (1., 2., (a), [1])
        - Assign deterministic chunk_id: {doc_id}_p{page}_c{index}
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
[ output/chunks/{id}_chunks.json ]  [ output/documents/{id}.json ]
(For Anuradha's DB & Pratik's RAG)  (Document rollup & status)
```

---

## 4. Team Dependencies & Handoffs

| Teammate | Role | What You Provide | Primary Spec File |
| :--- | :--- | :--- | :--- |
| **Anuradha** | Database Engineer | PostgreSQL DDL tables, sizing (~43k docs / ~10M chunks), cascade foreign keys, and batch loader script | [`DATABASE_INTEGRATION_SPEC.md`](DATABASE_INTEGRATION_SPEC.md) |
| **Pratik** | Retrieval Engineer | `chunk_id`, clean `content`, headings, case metadata, chunk sizing guarantees (<512 tokens) for BM25 + Dense vector search | `output/chunks/` & [`DATA_INGESTION_ARCHITECTURE.md`](DATA_INGESTION_ARCHITECTURE.md) |
| **Prathmesh** | Cloud Infrastructure | Secret key (`LLAMA_CLOUD_API_KEY`), outbound HTTPS egress URL, worker CPU/RAM specs, GCS bucket sizing | `.env.example`, [`README.md`](README.md) |
| **Nilesh** | RAG Integrator | Strict page-level traceability, citation format, section heading context | [`DATA_INGESTION_ARCHITECTURE.md`](DATA_INGESTION_ARCHITECTURE.md) |
| **Tanvi** | Frontend Engineer | `document_id` and `original_page_number` for clickable PDF citations | [`models.py`](models.py) |
| **Kusuma** | QA & Supporting APIs | Document metadata API contract, `status_error_report.json`, sample test PDFs | `output/documents/`, `reports/` |
| **Aakash** | CI/CD & Final Report | `pytest` test command, architecture documentation, throughput metrics | `tests/`, [`DATA_INGESTION_ARCHITECTURE.md`](DATA_INGESTION_ARCHITECTURE.md) |

---

## 5. Quick Start & Execution Guide

### 5.1 Installation & Setup
```bash
# Clone the repository
git clone https://github.com/Kedarlimbalkar/parsing.git
cd parsing

# Create and activate virtual environment
python -m venv venv
# On Windows (CMD):
venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
# (or pip install llama-parse pymupdf pydantic httpx tenacity python-dotenv reportlab pytest)

# Setup environment key (optional, for LlamaParse cloud parser)
copy .env.example .env
# Edit .env and insert your LLAMA_CLOUD_API_KEY
```

### 5.2 Running Tests
```bash
python -m pytest tests
# ============================== 8 passed in ~3s ==============================
```

### 5.3 Batch Processing the 1950 Supreme Court PDFs (`data/sample_pdfs/year=1950/english`)

#### A. Quick Test (Process first 3 PDFs):
```bash
python main.py test-1950 --limit 3
```

#### B. Process all 43 PDFs in the 1950 collection:
```bash
python main.py test-1950 --limit 0
```

#### C. Process using LlamaParse Cloud Engine:
```bash
python main.py test-1950 --limit 2 --use-llamaparse
```

#### D. Force Reprocess (Bypassing duplicate cache):
```bash
python main.py test-1950 --limit 3 --force
```

### 5.4 Ingesting Any Custom PDF
```bash
python main.py process-pdf --file "data/sample_pdfs/year=1950/english/1950_1_15_25_EN.pdf" --force
```

### 5.5 Single-Page JSON Contract Extraction CLI
```bash
python main.py process-page --doc-id "INSC_2023_CA_1042" --page 1 --text "raw text string or path"
```

---

## 6. Project Structure

```
├── .env.example                    # Template for environment variables (LLAMA_CLOUD_API_KEY)
├── .gitignore                      # Excludes .env, caches, and local environments
├── README.md                       # Main project documentation and run guide
├── DATA_INGESTION_ARCHITECTURE.md  # In-depth parsing, cleaning, and chunking technical spec
├── DATABASE_INTEGRATION_SPEC.md    # Handoff spec for Database Architect (DDL & Indexes)
├── cleaner_chunker.py              # Text cleaner, regex metadata extractor, page-aware chunker
├── extractor.py                    # LlamaParse v2 cloud parser + PyMuPDF & OCR fallback
├── models.py                       # Pydantic data contracts (PageIngestionContract, etc.)
├── pipeline.py                     # Batch orchestrator, SHA-256 deduplication, checkpointing
├── main.py                         # CLI tool for batch runs, single-page tests, and sample demo
├── sample_generator.py             # Realistic Indian Supreme Court PDF generator (ReportLab)
├── tests/
│   └── test_ingestion.py           # 8 automated unit & contract tests
├── data/
│   └── sample_pdfs/
│       ├── INSC_2023_CA_1042.pdf   # Generated modern sample judgment
│       └── year=1950/english/      # 43 historical 1950 Supreme Court Reports (S.C.R.) PDFs
├── output/
│   ├── chunks/                     # JSON chunk records formatted for DB & Retrieval
│   └── documents/                  # JSON document summary & rollup metadata
├── reports/
│   └── status_error_report.json    # Isolated failure tracking log
└── ingestion_state.json            # Persistent hash & status checkpoint for crash recovery
```

---

## 7. Deliverables & Data Contracts

### A. Single-Page Extraction Contract
Conforms strictly to Kedar's single-page contract:
```json
{
  "document_id": "1950_1_15_25_EN",
  "original_page_number": 1,
  "page_metadata": {
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
  },
  "chunks": [
    {
      "chunk_index": 1,
      "content": "...",
      "chunk_metadata": {
        "section_heading": null
      }
    }
  ]
}
```

### B. Downstream Ingestion Contract (For Anuradha & Pratik)
Stored in `output/chunks/{document_id}_chunks.json`:
```json
[
  {
    "chunk_id": "1950_1_15_25_EN_p1_c1",
    "document_id": "1950_1_15_25_EN",
    "original_page_number": 1,
    "chunk_index": 1,
    "content": "...",
    "section_heading": null,
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
]
```

---

## 8. License & Contribution

Maintained by **Kedar Limbalkar** as part of the **Supreme Court Judgment RAG System** project.  
Contributions, improvements, and contract revisions are tracked via GitHub issues and pull requests.
