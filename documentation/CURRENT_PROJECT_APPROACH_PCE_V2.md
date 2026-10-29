# Current Project Approach & Architecture (PCE v2.0)

**Project**: Supreme Court of India Judgment Legal RAG Ingestion Pipeline  
**Corpus**: 43,000+ Supreme Court Judgments (1950 Historical Scanned Reports to 2026 Born-Digital Judgments)  
**Location**: `documentation/`

---

## 1. Executive Summary & Core Architectural Enhancements

### 1.1 Complete Purge of Legacy OCR & Implementation of Google Cloud Vision OCR
- Purged legacy PaddleOCR and heavy 3.5 GB C++ runtime dependencies to achieve ultra-fast Cloud Run cold starts (<1s) and high OCR accuracy.
- Implemented **Google Cloud Vision OCR** (`DOCUMENT_TEXT_DETECTION`) backed by an intelligent 4-tier fallback:
  - **Tier 1 (Primary)**: Google Cloud Vision API via gRPC/IAM/ADC.
  - **Tier 2 (Fallback 1)**: Google Cloud Vision REST API via `GOOGLE_API_KEY`.
  - **Tier 3 (Fallback 2)**: PyMuPDF embedded vector and layout text stream rescue.
  - **Tier 4 (Fallback 3)**: Local Tesseract OCR / Deterministic test mode (prohibited in production).
- Implemented **Strict OCR Gate**: If any scanned page fails OCR across all tiers, the pipeline fails closed, preventing partial or silent document drops.

### 1.2 Active OCR on Borderline & Degraded Scanned Pages
- **Continuous Quality Heuristic**: Whenever the extraction pipeline encounters degraded scans, low OCR quality ($ocr\_q < 0.65$), or corrupted replacement characters ($\ge 1\%$), it triggers Google Cloud Vision OCR to transcribe the physical page rather than using garbled native text.
- Recovered clean operative orders and titles in historical scanned judgments (such as 1950s decisions) with **0.98+ average confidence**.

### 1.3 Citation Harvesting Across the Global Indian Legal Space
- Solved the issue where only **1 citation** was stored in the database by identifying and fixing 4 critical root causes:
  1. **Footnote Inclusion:** Classical SCR judgments (1950–1975) placed citations in footnotes. We integrated `pz.footnotes` into the citation scanning text.
  2. **Global Reporter Regex Coverage:** Expanded regex patterns across 11 Indian reporter families spanning 1950–2026: SCR, SCC, AIR, INSC (Neutral Citation), ILR (Indian Law Reports), SCALE, JT, ITR, Comp Cas, FCR (Federal Court Reports), and regional High Court reports (CWN, Bom LR).
  3. **OCR Typo & Bracket Healing:** Added normalization for OCR scanning artifacts such as mismatched brackets `(1953]`, letter errors (`F.O.R.` / `F.0.R.` $\rightarrow$ `F.C.R.`, `A.LR.` $\rightarrow$ `A.I.R.`), and missing dots (`SCR` vs `S.C.R.`).
  4. **Relational Persistence:** Persisted both the judgment's primary citation (`PRIMARY`) and all reported in-text precedent citations (`REPORTED`) into the database.

### 1.4 Modern Born-Digital vs. Historical Scan Disambiguation
- Disambiguates modern A5-sized digital PDFs (2000–2026) from historical scanned paper pages.
- Verifies embedded vector fonts and $\text{year} \ge 1990$, allowing clean born-digital pages to bypass OCR while pinpointing genuine scanned paper volumes for Google Cloud Vision OCR transcription.

### 1.5 Strict Rule R2 Single-Page Chunking & Vector Invariants
- Enforced single-page chunking (**Rule R2: `page_start == page_end`**) across all chunks with **0 cross-page splits**.
- Dynamic chunk bounds:
  - **Target Chunk Size**: `1,500` characters ($\approx 300 - 375$ tokens).
  - **Max Chunk Size**: `2,000` characters ($\approx 400 - 500$ tokens, hard upper limit).
  - **Min Chunk Size**: `150` characters ($\approx 30 - 38$ tokens).
  - **Min Alpha Chars**: `15` alphabetic characters (eliminates stray noise).
  - **Max Overlap**: `300` characters (sliding overlap between sub-paragraphs on the same page).
- Generated 768-dimensional L2-normalized unit vectors ($||\vec{v}||_2 = 1.0$) using Vertex AI `text-embedding-004`.

---

## 2. Directory Layout & Core Components

```
SC-JMT-ragpro-demo/
├── chunking/
│   └── page_chunker.py            # Rule R2 single-page chunking (target: 1500, max: 2000, min: 150)
├── local_ocr/
│   ├── engine.py                  # GoogleVisionOCREngine with 4-tier fallback
│   ├── ocr_config.py              # GoogleVisionOCRConfig thresholds & settings
│   └── router.py                  # 8-rule routing decision matrix
├── embedding/
│   └── vertex_embedder.py         # Vertex AI 768-dim L2-normalized vector embedder
├── parsing/
│   ├── case_boundaries.py         # Multi-case boundary detection on shared pages
│   ├── metadata_extractor.py      # Legal metadata extraction (case title, bench, citations)
│   ├── structure_tracker.py       # Legal zone classification (HEADER, CASE_INFO, OPINION, ORDER)
│   └── zone_extractor.py          # Coordinate bounding box zoning & header stripping
├── storage/
│   ├── db_loader.py               # Batch AlloyDB / PostgreSQL loader
│   ├── gcs_loader.py              # Google Cloud Storage downloader & discoverer
│   └── stage_tracker.py           # Per-document stage lifecycle tracker
├── pipeline.py                    # Orchestrator running discovery -> OCR -> zoning -> chunking -> embedding -> DB
├── audit_quality.py               # Fail-closed quality audit script
├── run_batch_ingestion.py         # Multi-process parallel batch ingestion entry point
└── documentation/
    ├── INDEX.md                   # Master index of all 28 documentation files
```
