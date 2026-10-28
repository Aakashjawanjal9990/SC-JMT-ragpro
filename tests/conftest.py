"""Pytest configuration for PCE v2.0."""
import sys
import os
from pathlib import Path

# Set root directory to repository root
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

# Enable mock embeddings explicitly for testing suite to avoid paid Vertex AI API calls
os.environ.setdefault("ALLOW_MOCK_EMBEDDINGS", "true")

# Ensure test SQLite database exists with canonical schema
from storage.init_db import init_database
db_path = root_dir / "output" / "scj_rag_v2.db"
if not db_path.exists():
    init_database(db_path)
