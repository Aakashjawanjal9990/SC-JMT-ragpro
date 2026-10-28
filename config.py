"""
Centralized Configuration for Supreme Court Judgment RAG Pipeline (PCE v2.0)
Scales across 43,000+ judgments across 1950 to present.
All pipeline, retrieval, storage, and verification parameters are loaded dynamically from .env.
"""

import os
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote_plus

# Load environment variables from .env file if available
try:
    from dotenv import load_dotenv
    PCEV2_ROOT = Path(__file__).resolve().parent
    # Prioritize project root .env, fallback to module .env
    if (PCEV2_ROOT / ".env").exists():
        load_dotenv(dotenv_path=PCEV2_ROOT / ".env")
    elif (PCEV2_ROOT.parent / ".env").exists():
        load_dotenv(dotenv_path=PCEV2_ROOT.parent / ".env")
    else:
        load_dotenv()
except ImportError:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent
PCEV2_ROOT = PROJECT_ROOT


def resolve_database_url(
    pcev2_root: Optional[Path] = None,
    target: Optional[str] = None,
    project_root: Optional[Path] = None
) -> str:
    """
    Resolves database URL from DATABASE_TARGET, DATABASE_URL, or individual DB_* credentials.
    Uses URL-encoding (quote_plus) to safely handle reserved characters (e.g., '@' in passwords).
    Enforces configurable TLS sslmode policy for remote PostgreSQL / AlloyDB connections.
    Defaults safely to local SQLite in output/scj_rag_v2.db when no cloud credentials provided.
    """
    root = project_root or pcev2_root or PROJECT_ROOT
    active_target = (target or os.getenv("DATABASE_TARGET", "")).lower()
    sslmode = os.getenv("DB_SSLMODE", "prefer")

    if active_target in ("alloydb", "postgres", "postgresql", "prod", "production"):
        alloy_url = os.getenv("ALLOYDB_DATABASE_URL")
        if alloy_url:
            return alloy_url
        db_user = os.getenv("DB_USER")
        db_pass = os.getenv("DB_PASSWORD")
        db_host = os.getenv("DB_HOST")
        db_port = os.getenv("DB_PORT", "5432")
        db_name = os.getenv("DB_NAME")
        if not (db_user and db_pass and db_host and db_name):
            raise ValueError("Target 'alloydb' requested but DB_USER, DB_PASSWORD, DB_HOST, or DB_NAME is missing in environment.")
        enc_pass = quote_plus(db_pass)
        return f"postgresql+psycopg2://{db_user}:{enc_pass}@{db_host}:{db_port}/{db_name}?sslmode={sslmode}"

    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return db_url

    # Check if individual AlloyDB / PostgreSQL credentials are provided
    db_user = os.getenv("DB_USER")
    db_pass = os.getenv("DB_PASSWORD")
    db_host = os.getenv("DB_HOST")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME")

    if db_user and db_pass and db_host and db_name:
        enc_pass = quote_plus(db_pass)
        return f"postgresql+psycopg2://{db_user}:{enc_pass}@{db_host}:{db_port}/{db_name}?sslmode={sslmode}"

    # Default to local SQLite database in output directory
    return f"sqlite:///{(root / 'output' / 'scj_rag_v2.db').as_posix()}"


@dataclass
class PipelineConfig:
    # Storage & DB
    DATABASE_URL: str = resolve_database_url()
    database_url: str = DATABASE_URL
    vector_mode: str = os.getenv("VECTOR_MODE", "external")
    allow_mock_embeddings: bool = os.getenv("ALLOW_MOCK_EMBEDDINGS", "false").lower() in ("true", "1", "yes")

    # Google Cloud Platform (GCP) & Cloud Storage (GCS)
    gcp_project: str = os.getenv("GCP_PROJECT", "sc-judgments-dev")
    gcp_location: str = os.getenv("GCP_LOCATION", "us-central1")
    gcs_bucket: str = os.getenv("GCS_BUCKET", "sc-judgments-dev-data")
    gcs_prefix: str = os.getenv("GCS_PREFIX", "raw_pdfs/pdfs")

    # Vertex AI Embedding Engine
    embedding_model_id: str = os.getenv("EMBEDDING_MODEL_ID", "text-embedding-004")
    embedding_dimension: int = int(os.getenv("EMBEDDING_DIMENSION", "768"))
    embedding_provider: str = os.getenv("EMBEDDING_PROVIDER", "google")
    batch_size: int = int(os.getenv("BATCH_SIZE", "64"))
    max_retries: int = int(os.getenv("MAX_RETRIES", "3"))

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
    data_dir: Path = PCEV2_ROOT / "data"

    # Pipeline Constants & Quality Thresholds
    MIN_TEXT_LENGTH_THRESHOLD: int = int(os.getenv("MIN_TEXT_THRESHOLD", "50"))
    MAX_CONTEXT_TOKENS: int = int(os.getenv("MAX_CONTEXT_TOKENS", "1500"))
    target_chunk_chars: int = int(os.getenv("TARGET_CHUNK_CHARS", "1500"))
    max_chunk_chars: int = int(os.getenv("MAX_CHUNK_CHARS", "2000"))
    min_chunk_chars: int = int(os.getenv("MIN_CHUNK_CHARS", "150"))
    min_alpha_chars: int = int(os.getenv("MIN_ALPHA_CHARS", "15"))
    max_overlap_chars: int = int(os.getenv("MAX_OVERLAP_CHARS", "300"))
    CHUNK_OVERLAP_CHARS: int = int(os.getenv("CHUNK_OVERLAP_CHARS", "150"))
    OCR_QUALITY_FLOOR: float = float(os.getenv("OCR_QUALITY_FLOOR", "0.60"))
    LABEL_CONFIDENCE_THRESHOLD: float = float(os.getenv("LABEL_CONFIDENCE_THRESHOLD", "0.75"))

    # Quality Verification Thresholds
    retrieval_min_recall: float = float(os.getenv("RETRIEVAL_MIN_RECALL", "0.25"))
    vector_norm_tolerance: float = float(os.getenv("VECTOR_NORM_TOLERANCE", "0.10"))
    audit_sample_limit: int = int(os.getenv("AUDIT_SAMPLE_LIMIT", "500"))
    vector_check_sample_limit: int = int(os.getenv("VECTOR_CHECK_SAMPLE_LIMIT", "50"))

    # Layout Zone Thresholds
    historical_gap_threshold: float = float(os.getenv("HISTORICAL_GAP_THRESHOLD", "15.0"))
    digital_gap_threshold: float = float(os.getenv("DIGITAL_GAP_THRESHOLD", "13.5"))
    header_y_max_born_digital: float = float(os.getenv("HEADER_Y_MAX_BORN_DIGITAL", "85.0"))
    header_y_max_historical: float = float(os.getenv("HEADER_Y_MAX_HISTORICAL", "75.0"))

    # Ingestion & Version Identity
    pipeline_name: str = os.getenv("PIPELINE_NAME", "scj-data-ingestion-v3")
    pipeline_version: str = os.getenv("PIPELINE_VERSION", "v3.0")
    chunking_version: str = os.getenv("CHUNKING_VERSION", "v3.0")
    cleaning_version: str = os.getenv("CLEANING_VERSION", "v3.0")
    embedding_version: str = os.getenv("EMBEDDING_VERSION", "v3.0_short")
    default_ingestion_workers: int = int(os.getenv("DEFAULT_INGESTION_WORKERS", "6"))
    overlap_sentences: int = 1
    recurrence_threshold: float = 0.30

    def __post_init__(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logss_dir.mkdir(parents=True, exist_ok=True)
        self.chunks_dir.mkdir(parents=True, exist_ok=True)
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)


config = PipelineConfig()
