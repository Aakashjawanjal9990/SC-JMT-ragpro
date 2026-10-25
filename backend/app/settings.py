"""Small environment-backed settings object; no secret values are logged."""

import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    environment: str = "development"
    log_level: str = "INFO"
    cors_origins: tuple[str, ...] = ()
    max_context_chars: int = 80_000
    request_timeout_seconds: float = 20.0
    database_url: str | None = None
    redis_url: str | None = None
    auth_challenge_secret: str | None = None
    session_cookie_name: str = "nm_session"
    session_cookie_samesite: str = "lax"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_starttls: bool = True
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    google_cloud_project: str | None = None
    google_cloud_location: str | None = None
    vertex_model_id: str | None = None
    vertex_embedding_model_id: str | None = None
    rate_limit_requests: int = 30
    rate_limit_window_seconds: int = 60

    @classmethod
    def from_environment(cls) -> "Settings":
        environment = os.getenv("APP_ENV", "development").strip() or "development"
        if len(environment) > 64 or not re.fullmatch(r"[A-Za-z0-9._-]+", environment):
            raise ValueError("APP_ENV must contain only letters, numbers, '.', '_' or '-'.")
        log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
        if log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError("LOG_LEVEL must be one of CRITICAL, ERROR, WARNING, INFO or DEBUG.")

        raw_origins = os.getenv("CORS_ORIGINS", "")
        origins = tuple(
            dict.fromkeys(
                origin.strip().rstrip("/")
                for origin in raw_origins.split(",")
                if origin.strip()
            )
        )
        for origin in origins:
            try:
                parsed = urlsplit(origin)
                port = parsed.port
            except ValueError as exc:
                raise ValueError("CORS_ORIGINS contains a malformed origin URL.") from exc
            if (
                origin == "*"
                or "*" in origin
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or not parsed.hostname
                or (port is not None and not 1 <= port <= 65535)
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("CORS_ORIGINS must be a comma-separated list of origin URLs.")

        raw_context_limit = os.getenv("MAX_CONTEXT_CHARS", "80000")
        try:
            max_context_chars = int(raw_context_limit)
        except ValueError as exc:
            raise ValueError("MAX_CONTEXT_CHARS must be an integer.") from exc
        if not 1_000 <= max_context_chars <= 1_000_000:
            raise ValueError("MAX_CONTEXT_CHARS must be between 1000 and 1000000.")

        try:
            request_timeout_seconds = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))
        except ValueError as exc:
            raise ValueError("REQUEST_TIMEOUT_SECONDS must be a number.") from exc
        if not 1 <= request_timeout_seconds <= 120:
            raise ValueError("REQUEST_TIMEOUT_SECONDS must be between 1 and 120.")

        try:
            rate_limit_requests = int(os.getenv("RATE_LIMIT_REQUESTS", "30"))
            rate_limit_window_seconds = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
        except ValueError as exc:
            raise ValueError("Rate-limit settings must be integers.") from exc
        if not 1 <= rate_limit_requests <= 10_000:
            raise ValueError("RATE_LIMIT_REQUESTS must be between 1 and 10000.")
        if not 1 <= rate_limit_window_seconds <= 3600:
            raise ValueError("RATE_LIMIT_WINDOW_SECONDS must be between 1 and 3600.")

        database_url = os.getenv("DATABASE_URL") or None
        redis_url = os.getenv("REDIS_URL") or None
        auth_challenge_secret = os.getenv("AUTH_CHALLENGE_SECRET") or None
        session_cookie_name = os.getenv("SESSION_COOKIE_NAME", "nm_session").strip()
        session_cookie_samesite = os.getenv("SESSION_COOKIE_SAMESITE", "lax").strip().lower()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session_cookie_name):
            raise ValueError("SESSION_COOKIE_NAME must contain only letters, numbers, '_' or '-'.")
        if session_cookie_samesite not in {"lax", "strict", "none"}:
            raise ValueError("SESSION_COOKIE_SAMESITE must be Lax, Strict, or None.")
        if (
            session_cookie_samesite == "none"
            and environment.lower() in {"development", "test"}
        ):
            raise ValueError(
                "SESSION_COOKIE_SAMESITE=None requires HTTPS; use Lax for local HTTP development."
            )
        smtp_host = os.getenv("SMTP_HOST") or None
        smtp_username = os.getenv("SMTP_USERNAME") or None
        smtp_password = os.getenv("SMTP_PASSWORD") or None
        smtp_from_email = os.getenv("SMTP_FROM_EMAIL") or None
        smtp_starttls = os.getenv("SMTP_STARTTLS", "true").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        try:
            smtp_port = int(os.getenv("SMTP_PORT", "587"))
        except ValueError as exc:
            raise ValueError("SMTP_PORT must be an integer.") from exc
        if not 1 <= smtp_port <= 65535:
            raise ValueError("SMTP_PORT must be between 1 and 65535.")
        oidc_issuer = os.getenv("OIDC_ISSUER") or None
        oidc_audience = os.getenv("OIDC_AUDIENCE") or None
        oidc_jwks_url = os.getenv("OIDC_JWKS_URL") or None
        google_cloud_project = os.getenv("GOOGLE_CLOUD_PROJECT") or None
        google_cloud_location = os.getenv("GOOGLE_CLOUD_LOCATION") or None
        vertex_model_id = os.getenv("VERTEX_MODEL_ID") or None
        vertex_embedding_model_id = os.getenv("VERTEX_EMBEDDING_MODEL_ID") or None
        if environment.lower() in {"prod", "production"}:
            required = {
                "DATABASE_URL": database_url,
                "REDIS_URL": redis_url,
                "AUTH_CHALLENGE_SECRET": auth_challenge_secret,
                "SMTP_HOST": smtp_host,
                "SMTP_FROM_EMAIL": smtp_from_email,
                "GOOGLE_CLOUD_PROJECT": google_cloud_project,
                "GOOGLE_CLOUD_LOCATION": google_cloud_location,
                "VERTEX_MODEL_ID": vertex_model_id,
                "VERTEX_EMBEDDING_MODEL_ID": vertex_embedding_model_id,
                "CORS_ORIGINS": origins,
            }
            missing = [name for name, value in required.items() if not value]
            placeholders = [
                name
                for name, value in required.items()
                if (isinstance(value, str) and "replace-with" in value.lower())
                or (
                    isinstance(value, tuple)
                    and any("replace-with" in origin.lower() for origin in value)
                )
            ]
            if missing:
                raise ValueError(
                    "Production configuration is incomplete: " + ", ".join(missing)
                )
            if placeholders:
                raise ValueError(
                    "Replace deployment placeholders before production: "
                    + ", ".join(placeholders)
                )
            if not database_url.startswith("postgresql"):
                raise ValueError("Production DATABASE_URL must use PostgreSQL.")
            if not database_url.startswith("postgresql+asyncpg://"):
                raise ValueError("DATABASE_URL must use the postgresql+asyncpg driver.")
            if auth_challenge_secret and len(auth_challenge_secret.encode("utf-8")) < 32:
                raise ValueError("AUTH_CHALLENGE_SECRET must contain at least 32 bytes.")
            if any(not origin.startswith("https://") for origin in origins):
                raise ValueError("Production CORS origins must use HTTPS.")

        return cls(
            environment=environment,
            log_level=log_level,
            cors_origins=origins,
            max_context_chars=max_context_chars,
            request_timeout_seconds=request_timeout_seconds,
            database_url=database_url,
            redis_url=redis_url,
            auth_challenge_secret=auth_challenge_secret,
            session_cookie_name=session_cookie_name,
            session_cookie_samesite=session_cookie_samesite,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_username=smtp_username,
            smtp_password=smtp_password,
            smtp_from_email=smtp_from_email,
            smtp_starttls=smtp_starttls,
            oidc_issuer=oidc_issuer,
            oidc_audience=oidc_audience,
            oidc_jwks_url=oidc_jwks_url,
            google_cloud_project=google_cloud_project,
            google_cloud_location=google_cloud_location,
            vertex_model_id=vertex_model_id,
            vertex_embedding_model_id=vertex_embedding_model_id,
            rate_limit_requests=rate_limit_requests,
            rate_limit_window_seconds=rate_limit_window_seconds,
        )
