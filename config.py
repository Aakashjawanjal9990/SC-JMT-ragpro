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
    PCEV2_ROOT = Path(__file__).resolve().parent


def resolve_database_url(pcev2_root: Optional[Path] = None, target: Optional[str] = None) -> str:
    """Resolve a database URL without silently crossing local/production targets.

    `target="local"` always resolves to SQLite unless an explicit SQLite URL is
    supplied. Production PostgreSQL requires an explicit URL or complete
    credential set; there are no baked-in production credentials.
    """
    root = pcev2_root or PCEV2_ROOT
    if target is None:
        target = os.getenv("DATABASE_TARGET", "local")
    target = target.strip().lower()
    active_target = target
    sqlite_default = f"sqlite:///{(root / 'output' / 'scj_rag_v2.db').as_posix()}"

    if target in ("local", "sqlite", "development", "dev", "test"):
        configured = os.getenv("DATABASE_URL", "").strip()
        # Never let a PostgreSQL DATABASE_URL silently redirect a local run.
        if configured.startswith("sqlite:"):
            return configured
        return sqlite_default

    if target in ("alloydb", "postgres", "postgresql", "prod", "production"):
        alloy_url = os.getenv("ALLOYDB_DATABASE_URL", "").strip()
        if alloy_url:
            if not alloy_url.startswith(("postgresql://", "postgres://", "postgresql+psycopg2://")):
                raise ValueError("ALLOYDB_DATABASE_URL must be a PostgreSQL URL.")
            return alloy_url

        db_user = os.getenv("DB_USER")
        db_pass = os.getenv("DB_PASSWORD")
        db_host = os.getenv("DB_HOST")
        db_port = os.getenv("DB_PORT", "5432")
        db_name = os.getenv("DB_NAME")
        if not all((db_user, db_pass, db_host, db_name)):
            raise ValueError(
                "AlloyDB target requires ALLOYDB_DATABASE_URL or explicit DB_USER, "
                "DB_PASSWORD, DB_HOST, and DB_NAME environment variables."
            )
        from urllib.parse import quote_plus
        enc_user = quote_plus(db_user)
        enc_pass = quote_plus(db_pass)
        sslmode = os.getenv("DB_SSLMODE", "require").strip().lower()
        allowed_sslmodes = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
        if sslmode not in allowed_sslmodes:
            raise ValueError(f"Unsupported DB_SSLMODE value: {sslmode!r}")
        if sslmode == "disable":
            raise ValueError("TLS cannot be disabled for the AlloyDB target.")
        return f"postgresql+psycopg2://{enc_user}:{enc_pass}@{db_host}:{db_port}/{db_name}?sslmode={sslmode}"

    raise ValueError(f"Unsupported database target: {active_target!r}")


@dataclass
class PipelineConfig:
    # Storage & DB
    DATABASE_URL: str = resolve_database_url()
    database_url: str = DATABASE_URL
    vector_mode: str = os.getenv("VECTOR_MODE", "external")
    allow_mock_embeddings: bool = os.getenv("ALLOW_MOCK_EMBEDDINGS", "false").lower() in ("true", "1", "yes")

    # Google Cloud Platform (GCP) & Cloud Storage (GCS)
    gcp_project: str = os.getenv("GCP_PROJECT", "").strip()
    gcp_location: str = os.getenv("GCP_LOCATION", "us-central1")
    gcs_bucket: str = os.getenv("GCS_BUCKET", "").strip()
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

    def validate_runtime(self, target: str = "local", require_gcs: bool = False) -> None:
        """Validate deployment-critical configuration before ingestion starts.

        Local SQLite runs may use development defaults. Cloud/production runs
        must explicitly configure their cloud resources and real embedding model.
        """
        active_target = (target or "local").strip().lower()
        production = active_target in {"alloydb", "postgres", "postgresql", "prod", "production"}
        errors = []

        if require_gcs or production:
            if not self.gcp_project or self.gcp_project.lower().startswith(("your-", "<", "replace-me")):
                errors.append("GCP_PROJECT must contain a real project ID for GCS or production ingestion.")
            if not self.gcs_bucket or self.gcs_bucket.lower().startswith(("your-", "<", "replace-me")):
                errors.append("GCS_BUCKET must contain a real bucket name for GCS or production ingestion.")
        if production:
            required_env = {
                "EMBEDDING_PROVIDER": self.embedding_provider,
                "EMBEDDING_MODEL_ID": self.embedding_model_id,
                "EMBEDDING_DIMENSION": str(self.embedding_dimension),
            }
            for key, value in required_env.items():
                if not os.getenv(key, "").strip():
                    errors.append(f"{key} must be explicitly configured for production.")
            if self.allow_mock_embeddings:
                errors.append("ALLOW_MOCK_EMBEDDINGS must be false for production ingestion.")
        if errors:
            raise ValueError("Invalid runtime configuration: " + " ".join(errors))

    def __post_init__(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logss_dir.mkdir(parents=True, exist_ok=True)
        self.chunks_dir.mkdir(parents=True, exist_ok=True)
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)


config = PipelineConfig()
