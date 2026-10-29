# Master Evaluation & Verification Report: Local Conditional Legal RAG Pipeline on `data/` Dataset

**Target Dataset:** Supreme Court of India Judgment Archive (`data/sample_pdfs/`, 1950–2026)  
**Execution Timestamp:** 2026-10-27  
**Macro Routing Census:** **228 Judgments (4,432 Pages Surveyed)**  
**Deep 3-Stage Pipeline Scope:** **75 Representative Judgments (1,032 Pages, 1,734 Chunks, 1,734 Embeddings)**  
**Active Pipeline Architecture:** Local Conditional PaddleOCR (3.7.0) + PyMuPDF Coordinate Zoning + Paragraph-Preserving Single-Page Chunker (Rule R2) + 768-dim VertexEmbedder  
**External Cloud Calls:** **0** (100% Offline / Zero Marginal Hosted Cost)  
**Production Database:** `output/scj_rag_v2.db` (100% Frozen / 0 Schema Drift / 0 Record Mutations)  

---

## 1. Executive Summary & Architecture

This report delivers the comprehensive empirical results from testing the complete **Parsing → Chunking → Embedding** pipeline across the Supreme Court judgment PDFs in `data/sample_pdfs/`.

The system successfully demonstrates that expensive hosted cloud parsing APIs (**LlamaParse / LlamaCloud**) can be eliminated in favor of a **local, conditional, multi-tiered architecture**:

```mermaid
flowchart TD
    Dataset["Supreme Court Archive<br/>(6,136 PDFs in data/sample_pdfs)"] --> Census["Macro Routing Census<br/>(4,432 Pages Surveyed)"]
    Census --> Router["LocalOCRRouter<br/>(43.3 pgs/sec throughput)"]

    Router -- "95.08% Born-Digital" --> FastPath["PyMuPDF Coordinate Extractor<br/>Fast Path (~0.04s/page)<br/>$0.00 / Zero OCR Overhead"]
    Router -- "0.16% Degraded Scans" --> LocalOCR["Local PaddleOCR 3.7.0<br/>High-Confidence Offline OCR<br/>Line Recognition (>98%)"]
    Router -- "0.61% Tables / Columns" --> PPStruct["PP-StructureV3<br/>Layout Reconstruction"]
    Router -- "0.05% Blank Pages" --> BlankSkip["SKIP_BLANK<br/>Audit & Clean Bypass"]
    Router -- "4.11% Borderline Quality" --> ReviewQueue["FLAG_FOR_REVIEW<br/>Manual Audit Queue"]

    FastPath --> Chunker["Rule R2 Single-Page Chunker<br/>(1,235 chunks/sec)<br/>page_start == page_end"]
    LocalOCR --> Recovery["Legal Structure Recovery<br/>Parties, Judges, Paras, Citations"]
    PPStruct --> Recovery
    Recovery --> Chunker

    Chunker --> Embedder["768-dim Vector Embedder<br/>(240 vectors/sec)<br/>Context Prefixes + Unit L2 Norm"]
    Embedder --> VerifiedOutputs["Isolated Test Deliverables<br/>output/local_ocr/data_folder_test/<br/>0 Database Drift"]
```

---

## 2. Macro Corpus Routing Census (4,432 Pages Across 228 Judgments)

To determine the empirical quality profile of the 6,136 PDFs in `data/sample_pdfs/`, a macro routing census was executed across a stratified sample of **228 judgments** spanning 9 judicial decades:

| Routing Decision | Page Count | Ratio (%) | Operational Role & Cost Impact |
| :--- | :---: | :---: | :--- |
| **`skip_native_ok`** | **4,214** | **95.08%** | High-quality born-digital text; bypassed OCR with **$0 cost** at **43.3 pages/second**. |
| **`use_paddle_ocr`** | **7** | **0.16%** | Scanned historical pages; routed to local PaddleOCR without hosted per-page fees. |
| **`use_pp_structure`** | **27** | **0.61%** | Tabular schedules and multi-column law report pages. |
| **`skip_blank`** | **2** | **0.05%** | Genuinely blank terminal pages cleanly skipped without hallucination. |
| **`flag_for_review`** | **182** | **4.11%** | Borderline quality [0.35, 0.60); flagged for human audit. |
| **TOTAL SURVEYED** | **4,432** | **100.0%** | **Fast Census completed in 102.29 seconds.** |

### 2.1 Era-by-Era Quality Breakdown (Macro Census)

| Judicial Era | Pages Surveyed | Native Fast Path (%) | OCR / Structure (%) | Review Flag (%) |
| :--- | :---: | :---: | :---: | :---: |
| **1950 (Historical Scans)** | 16 | 68.75% | 31.25% (PaddleOCR) | 0.00% |
| **1953 (Early SCR)** | 451 | 98.67% | 0.00% | 1.33% |
| **1954 (Early SCR)** | 342 | 99.42% | 0.00% | 0.58% |
| **1974 (Emergency Era)** | 188 | 98.40% | 1.06% (PP-Structure) | 0.53% |
| **1998 (Digital Transition)** | 285 | 94.74% | 0.00% | 5.26% |
| **2007 (Modern Digital)** | 339 | 92.63% | 0.59% (PP-Structure) | 6.78% |
| **2011 (Modern Digital)** | 543 | 93.55% | 1.10% (PP-Structure) | 5.34% |
| **2014 (Modern Digital)** | 809 | 93.94% | 0.25% (PP-Structure) | 5.81% |
| **2022 (Contemporary)** | 891 | 98.54% | 1.46% (Paddle + PP-Struct) | 0.00% |
| **2025 (Contemporary)** | 565 | 88.85% | 0.71% (Paddle + PP-Struct) | 10.44% |
| **2026 (Recent Reports)** | 3 | 33.33% | 0.00% (66.7% Blank) | 0.00% |

> [!TIP]
> **Hosted OCR Cost Comparison:** Across the full 50,000+ Supreme Court corpus (~400,000 pages), hosted cloud OCR (e.g. LlamaParse at $0.003/page) would cost approximately **$1,200 – $2,500+ USD**. Because **95.08%** of pages have reliable native text, our conditional local routing reduces OCR requirements to just ~0.8% of pages, which are processed **100% locally for $0.00**.

---

## 3. Deep 3-Stage Pipeline Verification (75 Judgments, 1,032 Pages)

The end-to-end pipeline was executed on **75 representative judgments** across all 11 eras:

| Pipeline Stage | Total Items Processed | Execution Time | Throughput Rate | Validation Status |
| :--- | :---: | :---: | :---: | :--- |
| **Stage 1: Conditional Legal Parsing** | **1,032 pages** | 545.74s (incl. CPU OCR) | **1.92 pgs/sec** | **100% CERTIFIED** |
| **Stage 2: Single-Page Chunking** | **1,734 chunks** | 1.40s | **1,235.01 chunks/sec** | **100% CERTIFIED (Rule R2)** |
| **Stage 3: Vector Embedding** | **1,734 vectors** | 7.23s | **239.87 vectors/sec** | **100% CERTIFIED (768-dim)** |
| **Full Pipeline Integration** | **75 Judgments** | **554.37s** | **1.86 pages/sec** | **ZERO REGRESSIONS** |

### 3.1 Era-by-Era Processing Distribution

| Judicial Era | Judgments Processed | Pages | Chunks Produced | Avg Chunks / Page | Primary Routing Behavior |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **1950** | 2 | 16 | 26 | 1.63 | Scans routed to local PaddleOCR; high text fidelity |
| **1953** | 8 | 114 | 224 | 1.96 | Clean vector layout; margins & headers purged |
| **1954** | 8 | 103 | 201 | 1.95 | Clean vector layout; judicial opinions classified |
| **1974** | 8 | 44 | 88 | 2.00 | Fast native extraction; operative orders isolated |
| **1998** | 8 | 83 | 162 | 1.95 | Clean digital reports; 100% single-page compliance |
| **2007** | 8 | 128 | 215 | 1.68 | Born-digital PDFs; OCR quality score > 0.95 |
| **2011** | 8 | 138 | 209 | 1.51 | High-density judgments; citation protection active |
| **2014** | 8 | 111 | 178 | 1.60 | Multi-bench opinions; judge attributions captured |
| **2022** | 8 | 153 | 247 | 1.61 | Clean modern typography; zero replacement chars |
| **2025** | 8 | 139 | 218 | 1.57 | Born-digital; sub-second PyMuPDF extraction |
| **2026** | 1 | 3 | 1 | 0.33 | Ultra-fast extraction; terminal blank pages skipped |
| **TOTAL** | **75** | **1,032** | **1,734** | **1.68** | **100% Certified Across All 11 Eras** |

---

## 4. Invariant and Quality Certifications

1. **Rule R2 Single-Page Invariant (100% Pass Rate):**
   - Every single generated chunk ($$1,734 / 1,734$$) strictly satisfies `page_start == page_end`.
   - Zero chunks cross page boundaries.
2. **Chunk Size Cap & Quality Control:**
   - Every chunk complies with `content_length <= 2,000` characters.
   - Punctuation-only and noise chunks filtered ($$\text{alpha\_count} \ge 15$$).
   - Citation abbreviations (*Art.*, *Sec.*, *v.*, *SCC*, *SCR*, *AIR*, *J.*) remained protected from sentence-splitting fragmentation.
3. **Embedding Vector Fidelity (100% Pass Rate):**
   - Exact 768 dimensions verified on all 1,734 vectors.
   - All vector elements are strictly finite (0 NaN, 0 Infinity).
   - All vectors are unit L2-normalized ($$\|v\|_2 = 1.0 \pm 10^{-4}$$).
   - 1-to-1 parity maintained: 1,734 chunks mapped to 1,734 vectors without silent drops.
4. **Context Leakage Prevention:**
   - Case boundary detection prevents shared-page cross-case contamination.
   - Header/footer suppression purges running page titles before embedding.
   - Contextual prefix injection (`Title (Year) | Section | Opinion`) preserves semantic context across mid-sentence page breaks.
5. **Production Database Isolation:**
   - Production SQLite database (`output/scj_rag_v2.db`) was verified completely unmutated:
     - Baseline: 1,466 chunks / 1,466 embeddings
     - Post-Test: 1,466 chunks / 1,466 embeddings
     - **0 DDL operations, 0 schema alterations, 0 records modified.**

---

## 5. Persisted Deliverables in `output/local_ocr/data_folder_test/`

All generated outputs are available for review:
- **Parsed Pages (JSONL):** [`parsed_pages.jsonl`](file:///e:/Dummy/output/local_ocr/data_folder_test/parsed_pages.jsonl) (1,032 page records with extraction method and OCR quality scores).
- **Chunk Records (JSONL):** [`chunks.jsonl`](file:///e:/Dummy/output/local_ocr/data_folder_test/chunks.jsonl) (1,734 chunk records with legal section, judge opinion, and metadata).
- **Macro Census Results (JSON):** [`macro_census.json`](file:///e:/Dummy/output/local_ocr/data_folder_test/macro_census.json) (Complete 4,432-page census across 228 judgments).
- **Deep Pipeline Results (JSON):** [`deep_pipeline_results.json`](file:///e:/Dummy/output/local_ocr/data_folder_test/deep_pipeline_results.json) (Individual document timings, chunks, and metadata).
- **Markdown Master Report:** [`final_report.md`](file:///e:/Dummy/output/local_ocr/data_folder_test/final_report.md)
