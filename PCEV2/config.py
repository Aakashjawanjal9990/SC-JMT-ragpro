"""
Centralized Configuration for Supreme Court Judgment RAG Pipeline (PCE v2.0)
Scales across 43,000+ judgments across 1950 to present.
Compatible with legacy and modern pipeline components.
"""

import os
from pathlib import Path
from dataclasses import dataclass


PCEV2_ROOT = Path(__file__).resolve().parent


@dataclass
class PipelineConfig:
    # Storage & DB
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{(PCEV2_ROOT / 'output' / 'scj_rag_v2.db').as_posix()}")
    database_url: str = DATABASE_URL
    vector_mode: str = os.getenv("VECTOR_MODE", "external")
    allow_mock_embeddings: bool = os.getenv("ALLOW_MOCK_EMBEDDINGS", "true").lower() == "true"
    
    # Vertex AI Embedding
    gcp_project: str = os.getenv("GCP_PROJECT", "sc-judgments-rag-prod")
    gcp_location: str = os.getenv("GCP_LOCATION", "us-central1")
    embedding_model_id: str = "text-embedding-004"
    embedding_dimension: int = 768
    batch_size: int = 64

    # Roots & Output Directories
    BASE_DIR: Path = PCEV2_ROOT
    base_dir: Path = BASE_DIR
    OUTPUT_DIR: Path = PCEV2_ROOT / "output"
    output_dir: Path = OUTPUT_DIR
    CHUNKS_DIR: Path = OUTPUT_DIR / "chunks"
    chunks_dir: Path = CHUNKS_DIR
    DOCUMENTS_DIR: Path = OUTPUT_DIR / "documents"
    documents_dir: Path = DOCUMENTS_DIR
    REPORTS_DIR: Path = OUTPUT_DIR / "reports"
    reports_dir: Path = REPORTS_DIR
    logss_dir: Path = OUTPUT_DIR / "logss"
    fixtures_dir: Path = Path("tests/fixtures")
    data_dir: Path = PCEV2_ROOT / "data" / "sample_pdfs"

    # Pipeline Constants & Thresholds
    MIN_TEXT_LENGTH_THRESHOLD: int = int(os.getenv("MIN_TEXT_THRESHOLD", "50"))
    MAX_CONTEXT_TOKENS: int = int(os.getenv("MAX_CONTEXT_TOKENS", "1500"))
    MAX_CHUNK_CHARS: int = int(os.getenv("MAX_CHUNK_CHARS", "1500"))
    CHUNK_OVERLAP_CHARS: int = int(os.getenv("CHUNK_OVERLAP_CHARS", "150"))
    OCR_QUALITY_FLOOR: float = float(os.getenv("OCR_QUALITY_FLOOR", "0.60"))
    LABEL_CONFIDENCE_THRESHOLD: float = float(os.getenv("LABEL_CONFIDENCE_THRESHOLD", "0.75"))

    chunking_version: str = "v3.0"
    cleaning_version: str = "v3.0"
    embedding_version: str = "v3.0_short"
    target_chunk_chars: int = 1500
    max_chunk_chars: int = 2000
    min_chunk_chars: int = 150
    overlap_sentences: int = 1
    recurrence_threshold: float = 0.30

    def __post_init__(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logss_dir.mkdir(parents=True, exist_ok=True)
        self.chunks_dir.mkdir(parents=True, exist_ok=True)
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        # Create alias folder o_p as requested by user
        (self.base_dir / "o_p").mkdir(parents=True, exist_ok=True)


config = PipelineConfig()
