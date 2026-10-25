"""Build the optional retrieval port adapter from the backend's configuration."""

from dataclasses import replace
from typing import Any, Callable

from sqlalchemy.engine import make_url

from app.retrieval.config import RetrievalSettings
from app.retrieval.db import DatabaseConfig
from app.retrieval.port_adapter import RetrievalPortAdapter


def create_backend_retrieval_adapter(
    passage_factory: Callable[..., Any], database_url: str | None
) -> RetrievalPortAdapter:
    """Create Pratik's adapter, reusing DATABASE_URL for the Cloud SQL source DB.

    Explicit DB_* retrieval settings take precedence. The backend currently has no
    separate AlloyDB BM25 deployment, so the adapter defaults to dense retrieval.
    """
    settings = RetrievalSettings.from_environment()
    if not settings.source_db.configured:
        if not database_url:
            raise ValueError("DATABASE_URL is required for retrieval.")
        url = make_url(database_url)
        host: Any = url.query.get("host") or url.host
        if isinstance(host, tuple):
            host = host[-1] if host else None
        if not url.database or not host:
            raise ValueError(
                "DATABASE_URL must identify the PostgreSQL database and host or Cloud SQL socket."
            )
        source_db = DatabaseConfig(
            name=url.database,
            user=url.username,
            password=url.password,
            host=str(host),
            port=url.port or 5432,
        )
        settings = replace(settings, source_db=source_db)

    return RetrievalPortAdapter.from_settings(passage_factory, settings)
