# MASTER PRODUCTION PROMPT: 43K+ SUPREME COURT JUDGMENTS RAG PIPELINE
## High-Throughput, Zero-Defect Parsing, Chunking & Embedding System (v2.0 Architecture)

---

## 1. Executive Mission & Scale

You are building the production ingestion engine for the entire corpus of the **Supreme Court of India (~43,000+ judgments spanning 1950 to present, ~350,000 pages, ~500,000 chunks, ~150M tokens)**.

This system will process thousands of historical OCR scanned volumes (1950â€“1990s) alongside modern born-digital publications (2000sâ€“2026). At this scale, manual review of individual outputs is impossible. The pipeline must be **mathematically robust, deterministic, fault-tolerant, modular, and self-auditing**, guaranteeing zero vector pollution, zero hallucinated metadata, and zero schema drift.

---

## 2. Hard Invariants & Production Constraints (Non-Negotiable)

1. **Rule R1: Database Schema is Frozen & Final**:
   - `db/schema.sql` is 100% frozen. The pipeline executes **ZERO DDL** (`CREATE`, `ALTER`, `DROP`). Any DDL attempt immediately halts execution.
   - Exact 9 tables: `judgments`, `source_files`, `ingestion_runs`, `extraction_runs`, `source_pages`, `chunks`, `chunk_embeddings`, `citations`, `embedding_models`.
   - All enhanced domain attributes (`page_shared_with`, `footnotes`, `margin_notes`, `page_header`, `label_confidence`, `layout_zones`) MUST be persisted in `chunks.metadata` (JSONB) and `source_pages.quality_score`.

2. **Rule R2: Single-Page Citation Invariant**:
   - Every chunk MUST strictly satisfy `chunks.page_start == chunks.page_end`. Chunks never span across physical page boundaries, ensuring exact pin-point legal citation.
   - Chunk ID format: `{document_id}_p{page}_c{n}` with a contiguous 0-based `chunk_index` per document.

3. **Rule R3: Embedding Vector Integrity**:
   - Real Google Vertex AI 768-dimensional $L_2$-normalized dense embeddings (`text-embedding-004`).
   - ZERO mock vectors or random noise in persistent storage. In testing/offline environments where external APIs are disabled, vectors must be explicitly flagged and isolated.

4. **Rule R4: Security & Zero Hardcoded Secrets**:
   - Zero credentials in code. GCP project, dataset, bucket names, and database URLs resolve from environment variables.

5. **Rule R5: 100% Page Accounting**:
   - Invariant: `pages_in == extracted_pages + quarantined_pages`. No page is silently dropped.

6. **The Guiding Principle**:
   - **"An empty value is acceptable; a wrong one is not."**
   - If a judge name cannot be confirmed against that case's bench list, leave `judge_opinion = None`.
   - If an opinion boundary is ambiguous, do not guess; decay confidence score.

---

## 3. Scale-Specific Requirements (43,000+ PDFs)

At 43K+ PDF scale, the pipeline must incorporate:
1. **Idempotent Resumability & Checkpointing**:
   - The pipeline must check if a document has already been processed and synced (`sync_status = 'SYNCED'`).
   - If an error occurs on document 15,234, restarting the pipeline must seamlessly resume without re-extracting or re-embedding prior documents.
2. **Chunk-Level Atomic Operations**:
   - Processing is partitioned per document. All chunks and database transactions for a document commit in a single atomic transaction.
3. **Memory Safety & Resource Management**:
   - PyMuPDF documents and PyTorch/Vertex batch buffers must be explicitly closed/flushed to prevent memory leaks during 24-hour long-running batch jobs.
4. **4-Stage Lifecycle Tracking**:
   - Track each document through 4 distinct stages: `parsing` $\rightarrow$ `chunking` $\rightarrow$ `embedding` $\rightarrow$ `vector_db`.
   - Write year-partitioned audit status files (`output/logss/year={year}/{doc_id}_status.json`) and summary verification CSVs.

---

## 4. The 5 Core Parsing & Chunking Challenges Solved

### A. Alternating Margin Columns in Scanned SCR Volumes
- **Symptom**: Historical 1950â€“1990s volumes feature narrow trim (~410â€“445 pt) with side margin notes (Year `1950`/`I9l50`, case titles, judge names). Naive text extraction reads margin notes into the middle of body sentences (`"of the Central I9l50 Excises"`, `"specified in l950 the First Schedule"`).
- **Solution**: Geometric zone classification using PyMuPDF `dict` span bboxes BEFORE reflow:
  - Odd book pages: Body is $x_1 \le 335\text{ pt}$. Spans with $x_0 \ge 335\text{ pt}$ (or $x_0 \ge 320\text{ pt}$ with font size $\le 8.5\text{ pt}$) are right margin notes.
  - Even book pages: Body is $x_0 \ge 95\text{ pt}$. Spans with $x_1 \le 95\text{ pt}$ (or $x_1 \le 105\text{ pt}$ with font size $\le 8.5\text{ pt}$) are left margin notes.
  - Move margin notes to `chunks.metadata.margin_notes`; zero bleed into body text.

### B. Headnote Disambiguation & Multi-Judge Opinion Tracking
- **Symptom**: In multi-judge cases (e.g. *Advani*, *Gopalan*), multi-page editor headnotes summarize holdings by quoting judges (`"KANIA C. J.â€”Though a writ..."`, `"FAZL ALI J.â€”The mere fact..."`). Naive string matching misidentifies these headnotes as real opinion starts.
- **Solution**:
  - Headnotes always appear BEFORE the formal jurisdiction block (`APPELLATE JURISDICTION` / `ORIGINAL JURISDICTION`) and counsel appearances. All text before the judgment heading is strictly `HEADNOTE` with `judge_opinion = None`.
  - The real opinion begins only after counsel appearances and the formal delivery line (`"The Court delivered judgment as follows:â€”"` or `"The following Judgments were delivered."`).
  - When subsequent judges deliver separate opinions mid-page (e.g. Fazl Ali J., Patanjali Sastri J., Mahajan J., Mukherjea J., Das J.), split paragraphs at the boundary so no chunk contains text from two opinions.
  - Confidence score starts at `0.95` on confirmed start and decays by `0.02` per subsequent page.

### C. Shared-Page Boundary Partitioning (Two Cases on One Page)
- **Symptom**: In law-report volumes, short cases end and new cases begin on the same physical sheet (e.g. page 25 holds the end of *Ram Krishna* and start of *Bhawanipore*; page 741 holds the end of *Advani* and start of *Co-operative Society*).
- **Solution**:
  - Detect boundary: end-of-case marker (`"Appeal dismissed."`, `"Agent for the appellants:"`) followed by all-caps title block and bench bracket.
  - Split physical lines: outgoing case gets pre-boundary text; incoming case gets post-boundary text.
  - Record reciprocal link `page_shared_with: '<other_doc_id>'`. Extract incoming metadata cleanly without inheriting previous case data.

### D. Substantive Text & Citation Protection
- **Symptom**: Regex cleaners accidentally stripped `"v."` between parties, removed `"S.C.R."` from volume citations (`[2026] 1 S.C.R. 183` became `[2026] 1 183`), and deleted statutory sections (`section 26-A`, `sections 4-A, 4-B`) mistaking them for printer sheet marks.
- **Solution**:
  - Protect `"v."` between party names.
  - Anchor running header regexes to standalone lines so they never touch citations.
  - Anchor collation sheet cleaners to standalone lines (`(?m)^\s*\d{1,2}-[A-Za-z]\s*$`).

### E. Vector Rule-Line Footnote Isolation
- **Symptom**: Footnotes at page bottoms glue onto the end of sentences, breaking grammar and vector semantic similarity.
- **Solution**:
  - Scan `page.get_drawings()` for horizontal separator rule lines ($y_0 \ge h \times 0.50$, height $\le 3.0\text{ pt}$). All text lines below the rule line are moved to `chunks.metadata.footnotes`.
  - Fallback to small-font bottom strip ($y_0 \ge h - 150\text{ pt}$, font size $\le 7.5\text{ pt}$).

---

## 5. Modular Repository Architecture (<200 lines per file)

Construct the codebase with strict single-responsibility modularity:

```text
PCEV2/
â”œâ”€â”€ config.py                 # Corpus settings, paths, batch sizes, thresholds (<80 lines)
â”œâ”€â”€ core/
â”‚   â”œâ”€â”€ __init__.py
â”‚   â””â”€â”€ models.py             # Domain models (PageRecord, ChunkRecord, DocumentMeta) (<120 lines)
â”œâ”€â”€ parsing/
â”‚   â”œâ”€â”€ __init__.py
â”‚   â”œâ”€â”€ zone_extractor.py     # PyMuPDF coordinate-aware layout zoning (<200 lines)
â”‚   â”œâ”€â”€ case_boundaries.py    # Shared-page boundary detection & splitting (<120 lines)
â”‚   â”œâ”€â”€ metadata_extractor.py # Deterministic 1st-page metadata (citations, bench, author) (<180 lines)
â”‚   â””â”€â”€ structure_tracker.py  # Pre-reflow opinion detection, ORDER isolation, decay (<180 lines)
â”œâ”€â”€ chunking/
â”‚   â”œâ”€â”€ __init__.py
â”‚   â””â”€â”€ page_chunker.py       # Paragraph-preserving single-page chunker & continuity (<180 lines)
â”œâ”€â”€ embedding/
â”‚   â”œâ”€â”€ __init__.py
â”‚   â””â”€â”€ vertex_embedder.py    # Short canonical prefix builder & Vertex AI embedder (<150 lines)
â”œâ”€â”€ storage/
â”‚   â”œâ”€â”€ __init__.py
â”‚   â”œâ”€â”€ db_loader.py          # Zero-DDL database writer (chunks, embeddings, citations) (<200 lines)
â”‚   â””â”€â”€ stage_tracker.py      # 4-stage lifecycle tracker & CSV export (<150 lines)
â”œâ”€â”€ pipeline.py               # Clean end-to-end orchestration pipeline (<180 lines)
â”œâ”€â”€ audit_quality.py          # Quality evaluator & review sheet generator (<220 lines)
â”œâ”€â”€ check_gates.py            # Automated strict quality gate harness (G1-G11) (<140 lines)
â””â”€â”€ tests/
    â”œâ”€â”€ conftest.py
    â””â”€â”€ test_pipeline.py      # Regression tests across 1950 and 2026 fixtures (<250 lines)
```

---

## 6. Execution Order & Certification Steps

1. **Setup Core Models**: Implement dataclasses and Pydantic schemas in `core/models.py`.
2. **Implement Parsing & Zoning**: Build `zone_extractor.py`, `case_boundaries.py`, `metadata_extractor.py`, and `structure_tracker.py`.
3. **Implement Chunking**: Build `page_chunker.py` ensuring single-page invariant and continuity tracking.
4. **Implement Embedding & Storage**: Build `vertex_embedder.py`, `db_loader.py`, and `stage_tracker.py`.
5. **Orchestrate Pipeline**: Connect modules in `pipeline.py`.
6. **Execute Verification**:
   - Run test suite: `pytest PCEV2/tests/test_pipeline.py`.
   - Run quality gate audit: `python PCEV2/check_gates.py`.
   - Verify all 7 strict quality gates pass (G1, G2, G3, G7, G9, G10, G11) with **0 header leaks across all chunks**.

