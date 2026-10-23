# Technical Approach: PDF Parsing and Legal Document Chunking
**Project**: Indian Supreme Court Judgment RAG System  
**Dataset Scope**: ~43,000 Judgments (1950 – Present)  
**Author**: Kedar (Data Ingestion Engineer)  
**Document Version**: 1.0 (Production Blueprint)  

---

### 1. Parsing Approach: Dual-Engine with Dynamic Routing
* **Core Objective**: Reliably extract text from both modern digital judgments and 70+ year old scanned/typewritten SCR records.
* **Engine A (Local Primary) - PyMuPDF (`fitz`) Text Extraction**:
  - Extracts native digital text layers with block-level bounding boxes and font metadata.
  - Zero cloud latency, local execution, and high throughput (~0.05 seconds per page).
* **Dynamic OCR Fallback Mechanism**:
  - Ingestion evaluates text density per page: if character count is below 50, page is flagged as scanned.
  - Renders page pixmap at 200 DPI resolution (calibrated balance between OCR accuracy and compute overhead).
  - Executes PyMuPDF / Tesseract OCR on the rasterized image to recover degraded typewritten text.
* **Engine B (Cloud Secondary) - LlamaParse v2 REST API**:
  - Direct multipart integration with `POST https://api.cloud.llamaindex.ai/api/v2/parse/upload`.
  - Polls `/api/v2/parse/{job_id}?expand=markdown` with exponential backoff.
  - Selected for documents with complex multi-column legal layouts, tables of evidence, or severe skew.
* **Page-Preserving Invariant**:
  - Text is strictly extracted page-by-page as `(page_num, raw_text, is_ocr, engine_used)`.
  - Never concatenates full PDF into a single unstructured string, preserving physical page boundaries.

---

### 2. OCR Cleaning & Text Normalization Approach
* **Hyphenation Reassembly**:
  - Recombines split words across line breaks (e.g. `repre- \n sentative` → `representative`).
  - Protects legitimate hyphenated legal terms (e.g. `quasi-judicial`, `sub-section`, `suo-motu`).
* **Spaced-Out Legal Typography Normalization**:
  - Collapses expanded uppercase headings (e.g. `J U D G M E N T` → `JUDGMENT`, `O R D E R` → `ORDER`).
* **Legal Character & OCR Typo Corrections**:
  - Replaces common OCR scanning misreads (`C0urt` → `Court`, `Jncome-tax` → `Income-tax`, `Appe11ant` → `Appellant`).
* **Boilerplate & Noise Stripping**:
  - Removes running headers/footers (`SUPREME COURT REPORTS`, `[1950] S.C.R.`) to eliminate vector noise.

---

### 3. Metadata Extraction Approach (Historical SCR & Modern Formats)
* **Multi-Line Uppercase Party Extraction**:
  - Captures party names spanning 2–5 lines before and after `v.`, `vs.`, `VERSUS`, or historical `v`.
  - Normalizes text casing to title/clean format (e.g. `RAM KRISHNA RAMNATH AGARWAL OF KAMPTEE`).
* **Coram / Bench Extraction**:
  - Historical 1950s SCR format: Parses bracketed judge blocks `[SHRI HARILAL KANIA C.J., ... JJ.]`.
  - Modern format: Regex patterns detecting `CORAM:`, `HON'BLE MR. JUSTICE`, and multi-judge lists.
* **Case Number & Legal Citations**:
  - Matches modern filings (`CIVIL APPEAL NO. 1042 OF 2023`) and historical citations (`[1950] S.C.R. 15`).
* **Judgment Date Parsing**:
  - Normalizes varied Indian legal date conventions (`1950 March 14`, `24th February, 2023`).

---

### 4. Chunking Approach: Page-Aware, Heading-Preserving Logical Chunking
* **Strict Physical Page Boundary Invariant**:
  - A chunk NEVER crosses page boundaries; every chunk belongs to exactly one physical PDF page.
  - Guarantees 100% faithful citation linking from downstream RAG answers back to the exact PDF page.
* **Hierarchical Legal Heading Detection & State Machine**:
  - Identifies structural sections: `FACTS`, `ISSUES`, `ARGUMENTS`, `RATIO DECIDENDI`, `DISPOSITION`.
  - Propagates active section heading forward to subsequent chunks on that page until next heading appears.
* **4-Stage Multi-Level Text Splitting**:
  - Level 1: Split page text by detected structural section headings.
  - Level 2: Split by legal paragraph boundaries (`\n\n`) and numbered clauses (`1.`, `2.`, `(a)`, `(i)`).
  - Level 3: Sentence-level tokenization for lengthy legal paragraphs exceeding token limits.
  - Level 4: Rolling character accumulator with sliding overlap.
* **Token Sizing & Overlap Strategy**:
  - Target chunk size: 1,200 – 1,500 characters (~350 – 450 tokens), ideal for 512-token embedding models.
  - Sliding overlap: 150 characters (~35 tokens) to maintain cross-chunk semantic continuity.
  - Prevents breaking legal statutes, ratio decidendi statements, or statutory section numbers.

---

### 5. Deterministic Traceability & Output Delivery
* **Traceable Chunk ID Convention**:
  - Encoded as `{document_id}_p{original_page_number}_c{chunk_index}` (e.g. `1950_1_15_25_EN_p2_c1`).
  - Allows downstream UI to pinpoint exact PDF page and sequence index immediately.
* **Enriched Downstream Payload Contract**:
  - Each chunk embeds: `chunk_id`, `document_id`, `original_page_number`, `chunk_index`, `content`,
    `section_heading`, `case_number`, `appellant`, `respondent`, `date_of_judgment`, and `judges`.
  - Downstream vector retriever (Pratik) receives complete contextual metadata alongside raw text.
* **Why This Approach Outperforms Naive Fixed-Character Chunking**:
  - Eliminates chopped legal citations (e.g. "Section 302 of the Indian Penal Code" remains intact).
  - Eliminates hallucinations by tying every vector embedding to its verifiable physical PDF page.
  - Preserves legal hierarchy by binding chunks to their judicial section (Ratio vs. Obiter Dicta).
