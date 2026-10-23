# Technical Implementation Guide: Document Parsing & Legal Chunking
**System**: Indian Supreme Court Judgment RAG Pipeline  
**Author / Module Owner**: Kedar (Data Ingestion Engineer)  
**Workspace**: `E:\Dummy`  
**Dataset Target**: ~43,000 Supreme Court Judgments (1950 – Present)

---

## 1. Executive Summary

This document explains the technical implementation of the **Document Parsing** and **Legal Chunking** subsystems within Kedar's Data Ingestion Pipeline. 

Unlike generic document processing pipelines that treat documents as continuous streams of text, this system is engineered specifically for **Indian Supreme Court judgments**. It guarantees:
1. **Strict 1:1 Page-Boundary Isolation**: Chunks never cross physical PDF page boundaries.
2. **Contextual Heading Preservation**: Legal sections (`FACTS`, `ISSUES`, `JUDGMENT`, `FAZL ALI J. -`) are tracked as persistent metadata.
3. **High-Accuracy Dual-Engine Parsing**: Combines cloud-based **LlamaParse (v2 REST API)** with local **PyMuPDF (`fitz`)** and 200 DPI OCR fallback.
4. **Deterministic Traceability**: Every chunk receives a predictable, unique identifier (`{document_id}_p{page}_c{index}`).

---

## 2. Document Parsing Deep Dive

Parsing Indian Supreme Court judgments poses severe technical hurdles:
* Modern digital judgments (2020+) contain watermarks, digital stamps, and multi-column tables.
* Historical judgments (1950–1980 Supreme Court Reports) are scanned physical prints with font distortions, uneven margins, and running headers.

To solve this, the pipeline implements a **Dual-Engine Architecture with Automatic Failover** ([`extractor.py`](extractor.py)).

```
                                  [ PDF Input ]
                                        │
                         Is --use-llamaparse enabled?
                                ┌───────┴───────┐
                                │ YES           │ NO
                                ▼               ▼
                      [ LlamaParse v2 API ]   [ PyMuPDF Local ]
                                │               │
                        Did it succeed?         │
                         ┌──────┴──────┐        │
                         │ YES         │ NO     │
                         ▼             ▼        ▼
                   [ Markdown ]   [ Check Text Density ]
                   [ Output   ]   [ (len >= 50 chars)  ]
                                       ┌────────┴────────┐
                                       │ YES             │ NO (<50 chars)
                                       ▼                 ▼
                                 [ Selectable Text ] [ 200 DPI OCR ]
                                 [ Output          ] [ Fallback    ]
```

### 2.1 Engine A: LlamaParse Cloud Parser (v2 REST API)
* **Implementation Class**: `LlamaParseExtractor` in [`extractor.py`](extractor.py#L28-L109).
* **Tier**: `cost_effective` (optimized for high speed and legal markdown accuracy).
* **Workflow**:
  1. **Multipart Upload**: The PDF is posted to `https://api.cloud.llamaindex.ai/api/v2/parse/upload` with Bearer authentication and configuration:
     ```json
     {
       "configuration": "{\"tier\": \"cost_effective\", \"version\": \"latest\"}"
     }
     ```
  2. **Job Tracking**: The API returns a unique job identifier (e.g. `pjb-gkrhiknd1...`).
  3. **Asynchronous Polling**: The extractor polls `https://api.cloud.llamaindex.ai/api/v2/parse/{job_id}?expand=markdown` every 2 seconds until status is `COMPLETED`.
  4. **Page Extraction**: Extracts the `markdown.pages[]` array. Each entry provides an explicit 1-based `page_number` and its full `markdown` string.
* **Why It’s Superior for Legal Judgments**:
  * Extracts multi-column judgment texts into correct reading order.
  * Preserves statutory tables and schedules as standard markdown tables.
  * Preserves bold headings (`# **JUDGMENT**`) without stripping semantic hierarchy.

### 2.2 Engine B: Local PyMuPDF (`fitz`) & OCR Fallback
* **Implementation Class**: `PDFExtractor` in [`extractor.py`](extractor.py#L112-L224).
* **Role**: Fast local extraction and zero-downtime offline fallback if internet or API limits occur.
* **Step 1: Selectable Text Check**:
  Extracts text per page using `page.get_text("text")`.
* **Step 2: Density Thresholding**:
  If character count $\ge 50$ (`MIN_TEXT_LENGTH_THRESHOLD = 50`), selectable text is accepted.
* **Step 3: OCR Fallback**:
  If character count $< 50$ (indicating an image scan or corrupted font embedding):
  * Renders a 200 DPI pixmap from the PDF page.
  * Passes image bytes to Tesseract OCR via `pytesseract.image_to_string` (or PyMuPDF's built-in OCR `page.get_textpage_ocr(dpi=200)`).
  * Automatically records the page number in `ocr_applied_pages` for QA auditing.

---

## 3. Legal Text Cleaning & Normalization

Implemented in `LegalTextCleaner` ([`cleaner_chunker.py`](cleaner_chunker.py#L48-L82)):

### 3.1 Line-Break Hyphenation Reassembly
OCR scans frequently break legal words across line breaks (e.g., `repre- \nsentative`). The cleaner uses boundary-aware regex to reassemble words before chunking:
```python
text = re.sub(r'(\b[A-Za-z]+)-\s*\n\s*([A-Za-z]+\b)', r'\1\2', text)
```

### 3.2 OCR Typo Normalization (Without Semantic Alteration)
Corrects scanning artifacts specific to Indian Supreme Court prints while strictly preserving legal meaning:
* `C0urt` / `C0URT` $\rightarrow$ `Court` / `COURT`
* `Judgm3nt` / `JUDGM3NT` $\rightarrow$ `Judgment` / `JUDGMENT`
* `Appe3llant` / `App3llant` $\rightarrow$ `Appellant`
* `Resp0ndent` $\rightarrow$ `Respondent`
* `Jncome-tax` $\rightarrow$ `Income-tax`
* `Govl!rnment` $\rightarrow$ `Government`
* `0entral` $\rightarrow$ `Central`
* `Exciieli` $\rightarrow$ `Excise`

### 3.3 Whitespace Normalization
* Converts all carriage returns (`\r\n`) to clean `\n`.
* Compresses 3 or more consecutive newlines down to 2 (`\n\n`) to preserve distinct paragraph breaks without runaway blank lines.
* Trims intra-line multiple spaces/tabs to a single space.

---

## 4. Metadata Extraction (Modern & Historical 1950 S.C.R.)

Implemented in `LegalMetadataExtractor` ([`cleaner_chunker.py`](cleaner_chunker.py#L85-L215)):

| Metadata Field | Extraction Strategy | Example Extracted Value |
| :--- | :--- | :--- |
| **Case Number / Citation** | Multi-pattern regex for `CIVIL/CRIMINAL APPEAL`, `SLP`, `WRIT PETITION`, and historical `[1950] S.C.R. 15`. | `CIVIL APPEAL NO. 1042 OF 2023`<br>`[1950] S.C.R. 15` |
| **Parties (Appellant / Respondent)** | Multi-line uppercase party block scanning before and after `v.` or `VERSUS`. | **Appellant**: `RAM KRISHNA RAMNATH AGARWAL OF KAMPTEE`<br>**Respondent**: `SECRETARY, MUNICIPAL COMMITTEE, KAMPTEE` |
| **Bench / Judges Coram** | Bracketed Coram lists `[SHRI HARILAL KANIA C.J., ... JJ.]` and modern Coram headers (`Hon'ble Mr. Justice ...`, `..., CJI`). | `["HARILAL KANIA C.J", "SAIYID FAZL ALI", "PATANJALI SASTRI", "MEHR CHAND MAHAJAN", "MuKHERJEA", "S.R. DAS JJ"]` |
| **Date of Judgment** | Matches `DATED: ...`, `Pronounced on ...`, standard dates, and 1950 SCR date stamps (`1950 March 14`). | `1950 March 14`<br>`24th February, 2023` |

---

## 5. The Chunking Strategy: Page-Aware Logical Chunking

Implemented in `PageChunker` ([`cleaner_chunker.py`](cleaner_chunker.py#L218-L325)):

### 5.1 Why Naive Fixed-Character Chunking Fails in Legal RAG
Generic chunkers slice documents at arbitrary character boundaries (e.g., every 500 characters with 50-character overlap). In legal RAG, this creates critical failure modes:
1. **Broken Legal Reasoning**: Cuts statutory citations (e.g. `Section 11(6) of the Arbitration Act`) or judicial propositions in half.
2. **Page Citation Hallucinations**: Crosses page boundaries, making it impossible to give a verifiable page citation to the user.
3. **Loss of Contextual Weight**: An argument made by an Appellant is severed from its heading, leading the LLM to mistake a party's claim for the judge's final verdict.

### 5.2 The 4-Stage Logical Chunking Algorithm

```
                  [ Cleaned Page Text ]
                            │
                            ▼
      ┌───────────────────────────────────────────┐
      │ Stage 1: Line-by-Line Heading Detection   │
      │ Match lines against HEADING_PATTERNS      │
      └─────────────────────┬─────────────────────┘
                            │
                            ▼
      ┌───────────────────────────────────────────┐
      │ Stage 2: Section Heading Inheritance      │
      │ Current heading stored in state machine;  │
      │ assigned to all subsequent chunks.        │
      └─────────────────────┬─────────────────────┘
                            │
                            ▼
      ┌───────────────────────────────────────────┐
      │ Stage 3: Numbered-Point & Paragraph Split │
      │ 1. Split on \n\n (Paragraph boundaries)   │
      │ 2. Sub-split on: \n(?=\d+\.\s+|\[\d+\])   │
      └─────────────────────┬─────────────────────┘
                            │
                            ▼
      ┌───────────────────────────────────────────┐
      │ Stage 4: Deterministic Traceability IDs   │
      │ {document_id}_p{page_num}_c{chunk_index}  │
      └───────────────────────────────────────────┘
```

#### Stage 1: Heading Isolation
The chunker strips markdown tokens (`#`, `##`, `**`) and matches lines against structural legal headings:
```python
HEADING_PATTERNS = [
    r'^(?:J\s*U\s*D\s*G\s*M\s*E\s*N\s*T|JUDGMENT)$',
    r'^(?:O\s*R\s*D\s*E\s*R|ORDER)$',
    r'^(?:BACKGROUND|FACTS\s+AND\s+BACKGROUND|FACTS|ISSUES|SUBMISSIONS(?:\s+OF\s+COUNSEL)?|ANALYSIS(?:\s+AND\s+CONCLUSION)?|DISCUSSION|CONCLUSION|VERDICT|OPERATIVE ORDER)$',
    r'^(?:PART\s+[A-Z0-9]+|SECTION\s+[A-Z0-9]+|POINT\s+[A-Z0-9]+)$',
    r'^[A-Z\s.]+(?:C\.J\.|JJ\.|J\.)(?:\s*[-—:].*)?$',  # 1950s judicial opinion headers e.g. "FAZL ALI J. -"
]
```

#### Stage 2: Section Heading State Inheritance
When a heading line is detected (e.g. `FACTS AND BACKGROUND`), the chunker:
* Updates the active `current_heading = "FACTS AND BACKGROUND"`.
* Does **not** emit an isolated, useless 1-line chunk for the title.
* Automatically tags every subsequent chunk on that page with `section_heading: "FACTS AND BACKGROUND"` until a new section begins.

#### Stage 3: Numbered Legal Paragraph Sub-Splitting
Judgments structure their operative analysis in numbered points. The chunker sub-splits blocks using regex positive lookahead:
$$\text{Lookahead Regex: }\verb|\n(?=\d+\.\s+|\[\d+\]\s+|\([a-z0-9]+\)\s+)|$$
* Example: A text block containing `1. Leave granted...` and `2. The primary question...` is split into two distinct, complete chunks.

#### Stage 4: Deterministic Key Assignment
Each chunk is assigned an immutable, collision-free key:
$$\text{chunk\_id} = \text{document\_id} + \text{"\_p"} + \text{page\_num} + \text{"\_c"} + \text{chunk\_index}$$
* Example: `1950_1_15_25_EN_p2_c1` = Document `1950_1_15_25_EN`, Page 2, Chunk 1.

---

## 6. Exact Data Output Contracts

### 6.1 Downstream Handoff Payload (For Database & Retrieval Indexing)
Persisted in `output/chunks/{document_id}_chunks.json` conforming to [`DownstreamChunkRecord`](models.py#L106-L124):

```json
{
  "chunk_id": "1950_1_15_25_EN_p2_c1",
  "document_id": "1950_1_15_25_EN",
  "original_page_number": 2,
  "chunk_index": 1,
  "content": "3. The respondent contractor entered into a concession agreement with the appellant on 12 June 2018 for the construction of a state highway bypass. The completion timeline specified a period of thirty-six months from the appointment date.",
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

### 6.2 Single-Page Prompt Contract
Conforms strictly to Kedar's single-page extraction contract:
```json
{
  "document_id": "1950_1_15_25_EN",
  "original_page_number": 2,
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
      "content": "3. The respondent contractor entered into a concession agreement...",
      "chunk_metadata": {
        "section_heading": "FACTS AND BACKGROUND"
      }
    }
  ]
}
```

---

## 7. Concrete Validation Examples

### Example 1: Historical 1950 Supreme Court Judgment
* **File**: `data/sample_pdfs/year=1950/english/1950_1_15_25_EN.pdf`
* **Pages**: 11 pages
* **Total Chunks Produced**: 31 chunks
* **Case Metadata Extracted**:
  * `case_number`: `S.C.R.`
  * `appellant`: `RAM KRISHNA RAMNATH AGARWAL OF KAMPTEE`
  * `respondent`: `SECRETARY, MUNICIPAL COMMITTEE, KAMP TEE`
  * `date_of_judgment`: `1950 March 14`
  * `judges`: 6 judges of the First Bench of India (`HARILAL KANIA C.J`, `SAIYID FAZL ALI`, `PATANJALI SASTRI`, `MEHR CHAND MAHAJAN`, `MuKHERJEA`, `S.R. DAS JJ`).
* **Chunk Structure**: Chunks on page 1 contain preamble and statutes; subsequent chunks inherit judicial opinions (`FAZL ALI J.`, etc.).

### Example 2: Modern Supreme Court Judgment (ReportLab)
* **File**: `data/sample_pdfs/INSC_2023_CA_1042.pdf`
* **Pages**: 3 pages
* **Total Chunks Produced**: 8 chunks (PyMuPDF) / 16 chunks (LlamaParse)
* **Section Headings Captured**:
  * Page 1 Chunks: `section_heading: "JUDGMENT"`
  * Page 2 Chunks: `section_heading: "FACTS AND BACKGROUND"` and `"SUBMISSIONS OF COUNSEL"`
  * Page 3 Chunks: `section_heading: "ANALYSIS AND CONCLUSION"`

---

## 8. Verification Commands

Run these commands from Command Prompt (`CMD`) in `E:\Dummy`:

```cmd
cd /d E:\Dummy

:: 1. Run full unit and contract test suite (8 tests)
python -m pytest tests

:: 2. Test parsing and chunking on first 3 files of 1950 collection
python main.py test-1950 --limit 3

:: 3. Test parsing with LlamaParse Cloud Engine
python main.py test-1950 --limit 2 --use-llamaparse

:: 4. Process all 43 PDFs in 1950 collection
python main.py test-1950 --limit 0
```

