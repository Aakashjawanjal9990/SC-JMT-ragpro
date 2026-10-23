# Supreme Court Judgment RAG System — Data Ingestion Module
**Role**: Data Ingestion Engineer (Kedar)  
**Location**: `E:\Dummy`

---

## 1. Scope & Ownership
According to the project allocation and team ownership boundaries:
- **Main Ownership**: PDF extraction, LlamaParse cloud parsing, OCR fallback, text cleaning, page-aware chunking, metadata preservation, batching, retries, duplicate detection, failure tracking, and resumable state.
- **Key Principle**: Every chunk is strictly traceable to its source document ID and original PDF page number.
- **Historical Support**: Full support for both modern judgments and 1950+ Supreme Court Reports (S.C.R.) judicial formats.
- **Downstream Coordination**:
  - **Anuradha (Database Engineer)**: Supplies structured document summaries (`output/documents/`) and chunk records (`output/chunks/`) conforming to relational/vector storage schemas.
  - **Pratik (Retrieval Engineer)**: Supplies chunk records containing `chunk_id`, `document_id`, `original_page_number`, `content`, `section_heading`, and case metadata for hybrid search (dense + BM25).

---

## 2. Testing the 1950 Judgment PDFs (`year=1950\english`)

### A. Quick Test (Process First 3 PDFs)
Open Windows Command Prompt (`CMD`), navigate to `E:\Dummy`, and run:
```cmd
cd /d E:\Dummy
python main.py test-1950 --limit 3
```

### B. Process All 43 PDFs in the Folder
```cmd
cd /d E:\Dummy
python main.py test-1950 --limit 0
```

### C. Run with LlamaParse Cloud Parser
```cmd
cd /d E:\Dummy
python main.py test-1950 --limit 3 --use-llamaparse
```

### D. Generic Batch Command (Custom Directory)
```cmd
cd /d E:\Dummy
python main.py batch --dir "data\sample_pdfs\year=1950\english" --limit 5
```

---

## 3. Data Contracts

### A. Single-Page Extraction Contract (Kedar's Core Contract)
Conforms strictly to the prompt contract:
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
Persisted in `output/chunks/{document_id}_chunks.json`:
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

## 4. Deliverables Checklist
- [x] **Ingestion Pipeline**: End-to-end PDF processing with batch handling (`pipeline.py`).
- [x] **1950 SCR Extraction**: Multi-line uppercase party detection, bracketed Coram parsing, and judgment date detection (`cleaner_chunker.py`).
- [x] **LlamaParse Integration**: High-accuracy cloud parsing for complex legal layouts, multi-column pages, and tables (`extractor.py`).
- [x] **OCR Fallback**: Selectable text extraction via PyMuPDF with automatic OCR fallback for scanned/low-density pages (`extractor.py`).
- [x] **Duplicate Detection**: SHA-256 hash tracking prevents re-processing identical files.
- [x] **Resumable State**: State saved to `ingestion_state.json` allowing uninterrupted resumption after failures.
- [x] **Unit & Contract Tests**: Comprehensive test suite with 100% pass rate (`tests/test_ingestion.py`).
