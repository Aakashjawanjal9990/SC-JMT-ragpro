"""FastAPI entry point and composition root for the query service."""

import asyncio
import inspect
import logging
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.adapters.fallback_generation import FallbackAnswerGeneration
from app.adapters.local_test import LocalTestAnswerGeneration
from app.adapters.postgres import PostgresQueryAdapter
from app.adapters.pratik_generation import PratikAnswerGenerationAdapter
from app.adapters.sqlite_sources import SqliteSourceRecordAdapter
from app.adapters.vertex import GeminiAnswerAdapter, VertexAIAdapter
from app.api.compat import router as compat_router
from app.api.errors import register_error_handlers
from app.api.routes import router
from app.auth_service import AuthService
from app.email import SMTPEmailSender
from app.logging_config import configure_logging
from app.middleware import RequestBodyLimitMiddleware, RequestLoggingMiddleware
from app.models import QueryFilters, RetrievedPassage
from app.orchestrator import QueryOrchestrator
from app.ports import (
    AnswerGenerationPort,
    RetrievalPort,
    SourceRecordPort,
    UnconfiguredAnswerGeneration,
    UnconfiguredRetrieval,
    UnconfiguredSourceRecords,
)
from app.rate_limit import RedisRateLimiter
from app.retrieval.config import RetrievalSettings
from app.retrieval.integration import create_backend_retrieval_adapter
from app.security import OIDCVerifier
from app.settings import Settings

logger = logging.getLogger(__name__)


class _RetrievalSlot:
    """Stable orchestrator port whose implementation can be selected at startup."""

    def __init__(self, adapter: RetrievalPort) -> None:
        self._adapter = adapter

    @property
    def configured(self) -> bool:
        return not isinstance(self._adapter, UnconfiguredRetrieval)

    def replace(self, adapter: RetrievalPort) -> None:
        self._adapter = adapter

    async def retrieve(
        self, question: str, filters: QueryFilters, limit: int
    ) -> Sequence[RetrievedPassage]:
        return await self._adapter.retrieve(question, filters, limit)

    async def healthcheck(self) -> bool:
        if not self.configured:
            return False
        probe = getattr(self._adapter, "healthcheck", None)
        if probe is None:
            return False
        try:
            if inspect.iscoroutinefunction(probe):
                result = await asyncio.wait_for(probe(), timeout=3)
            else:
                result = await asyncio.wait_for(asyncio.to_thread(probe), timeout=3)
                if inspect.isawaitable(result):
                    result = await asyncio.wait_for(result, timeout=3)
            return bool(result)
        except Exception:
            return False


def _configured_answer_generation(settings: Settings) -> AnswerGenerationPort:
    """The answer model from ANSWER_GENERATION_PROVIDER, wrapped with the
    ANSWER_GENERATION_FALLBACK model when one is set."""
    if settings.answer_generation_provider == "groq":
        return PratikAnswerGenerationAdapter(settings)
    if not GeminiAnswerAdapter.is_configured(settings):
        if settings.answer_generation_fallback == "groq":
            logger.warning("Gemini answer generation is not configured; using Groq only")
            return PratikAnswerGenerationAdapter(settings)
        return UnconfiguredAnswerGeneration()
    primary = GeminiAnswerAdapter(settings)
    if settings.answer_generation_fallback == "groq":
        return FallbackAnswerGeneration(
            primary,
            PratikAnswerGenerationAdapter(settings),
            primary_name=f"gemini:{settings.vertex_model_id}",
            fallback_name=f"groq:{settings.groq_model_id}",
        )
    return primary


def create_app(
    retrieval: RetrievalPort | None = None,
    source_records: SourceRecordPort | None = None,
    answer_generation: AnswerGenerationPort | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    """Build the API with owner-supplied adapters, or explicit unconfigured defaults."""
    resolved_settings = settings or Settings.from_environment()
    configure_logging(resolved_settings.log_level, resolved_settings.environment)
    is_production = resolved_settings.environment.lower() in {"prod", "production"}

    redis_client = (
        Redis.from_url(
            resolved_settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
        if resolved_settings.redis_url
        else None
    )
    database_engine: AsyncEngine | None = (
        create_async_engine(
            resolved_settings.database_url,
            pool_pre_ping=True,
            pool_size=3,
            max_overflow=1,
            pool_timeout=5,
            pool_recycle=1800,
            connect_args={"timeout": 5, "command_timeout": 10},
        )
        if resolved_settings.database_url
        else None
    )
    vertex_adapter = (
        VertexAIAdapter(resolved_settings)
        if all(
            (
                resolved_settings.google_cloud_project,
                resolved_settings.google_cloud_location,
                resolved_settings.vertex_model_id,
                resolved_settings.vertex_embedding_model_id,
            )
        )
        else None
    )
    postgres_adapter = (
        PostgresQueryAdapter(
            database_engine,
            vertex_adapter,
        )
        if database_engine and vertex_adapter and resolved_settings.vertex_embedding_model_id
        else None
    )
    retrieval_settings = RetrievalSettings.from_environment()
    default_retrieval_adapter = (
        retrieval if retrieval is not None else postgres_adapter or UnconfiguredRetrieval()
    )
    retrieval_slot = _RetrievalSlot(default_retrieval_adapter)
    source_records_adapter = (
        source_records
        if source_records is not None
        else (
            SqliteSourceRecordAdapter(retrieval_settings.sqlite_path)
            if retrieval_settings.vector_store == "sqlite" and retrieval_settings.sqlite_path
            else postgres_adapter or UnconfiguredSourceRecords()
        )
    )
    answer_generation_adapter = (
        answer_generation
        if answer_generation is not None
        else (
            LocalTestAnswerGeneration()
            if (
                os.getenv("LOCAL_TEST_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
                and resolved_settings.environment.lower() == "development"
            )
            else _configured_answer_generation(resolved_settings)
        )
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        active_retrieval: RetrievalPort | None = retrieval
        retention_task: asyncio.Task[None] | None = None
        if retrieval is not None:
            warmup = getattr(retrieval, "warmup", None)
            if warmup is not None:
                await asyncio.to_thread(warmup)
        if isinstance(
            answer_generation_adapter, (PratikAnswerGenerationAdapter, FallbackAnswerGeneration)
        ):
            await asyncio.to_thread(answer_generation_adapter.warmup)
        if retrieval is None and os.getenv(
            "RETRIEVAL_PORT_ADAPTER_ENABLED", "false"
        ).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            candidate = None
            try:
                if retrieval_settings.vector_store == "sqlite":
                    candidate = create_backend_retrieval_adapter(
                        RetrievedPassage, resolved_settings.database_url
                    )
                    await asyncio.to_thread(candidate.warmup)
                    retrieval_slot.replace(candidate)
                    active_retrieval = candidate
                    logger.info("SQLite RetrievalPort adapter initialized and warmed up")
                elif database_engine is None:
                    logger.warning(
                        "Pratik retrieval adapter is enabled but DATABASE_URL is not configured"
                    )
                else:
                    async with database_engine.connect() as connection:
                        model_rows = (
                            await connection.execute(
                                text(
                                    "SELECT provider, model_id FROM embedding_models "
                                    "WHERE is_active = true"
                                )
                            )
                        ).all()
                        vector_count = await connection.scalar(
                            text("SELECT count(*) FROM chunks WHERE embedding IS NOT NULL")
                        )
                    if (
                        len(model_rows) != 1
                        or not vector_count
                        or not retrieval_settings.embedding_model
                        or (model_rows[0][1] or "").strip() != retrieval_settings.embedding_model
                    ):
                        logger.warning(
                            "Pratik retrieval adapter is enabled but requires one active "
                            "embedding model matching RETRIEVAL_EMBEDDING_MODEL and at least "
                            "one stored vector; keeping the configured backend adapter"
                        )
                    else:
                        candidate = create_backend_retrieval_adapter(
                            RetrievedPassage, resolved_settings.database_url
                        )
                        await asyncio.to_thread(candidate.warmup)
                        retrieval_slot.replace(candidate)
                        active_retrieval = candidate
                        logger.info("Pratik RetrievalPort adapter initialized and warmed up")
            except Exception:
                if candidate is not None:
                    try:
                        await asyncio.to_thread(candidate.close)
                    except Exception:
                        logger.exception("Failed to close incomplete retrieval adapter")
                logger.exception(
                    "Pratik retrieval adapter could not start; keeping the configured "
                    "backend adapter"
                )
        if database_engine is not None and is_production:

            async def purge_expired_chat_history() -> None:
                while True:
                    try:
                        async with database_engine.begin() as connection:
                            result = await connection.execute(
                                text(
                                    """DELETE FROM query_runs
                                       WHERE request_type = 'CHAT'
                                         AND created_at < now() - make_interval(days => :days)"""
                                ),
                                {"days": resolved_settings.chat_retention_days},
                            )
                        logger.info(
                            "privacy.chat_history_retention_cleanup",
                            extra={"deleted_records": result.rowcount or 0},
                        )
                    except Exception:
                        logger.exception("privacy.chat_history_retention_cleanup_failed")
                    await asyncio.sleep(24 * 60 * 60)

            retention_task = asyncio.create_task(purge_expired_chat_history())
        try:
            yield
        finally:
            if retention_task is not None:
                retention_task.cancel()
                try:
                    await retention_task
                except asyncio.CancelledError:
                    pass
            if active_retrieval is not None:
                close = getattr(active_retrieval, "close", None)
                if close is not None:
                    try:
                        if inspect.iscoroutinefunction(close):
                            await close()
                        else:
                            result = await asyncio.to_thread(close)
                            if inspect.isawaitable(result):
                                await result
                    except Exception:
                        logger.exception("Retrieval adapter failed to close cleanly")
            if redis_client is not None:
                await redis_client.aclose()
            if database_engine is not None:
                await database_engine.dispose()
            if vertex_adapter is not None:
                await vertex_adapter.close()
            if isinstance(
                answer_generation_adapter,
                (PratikAnswerGenerationAdapter, GeminiAnswerAdapter, FallbackAnswerGeneration),
            ):
                await answer_generation_adapter.close()

    application = FastAPI(
        title="Supreme Court Judgment Query API",
        version="1.0.0",
        description=(
            "Evidence-grounded query API; storage, retrieval, and model adapters "
            "are injected by their owners."
        ),
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
        lifespan=lifespan,
    )
    if resolved_settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=list(resolved_settings.cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-CSRF-Token",
                "X-Request-ID",
            ],
            expose_headers=["X-Request-ID"],
        )
    application.state.query_orchestrator = QueryOrchestrator(
        retrieval=retrieval_slot,
        source_records=source_records_adapter,
        answer_generation=answer_generation_adapter,
        max_context_chars=resolved_settings.max_context_chars,
    )
    application.state.environment = resolved_settings.environment.lower()
    application.state.settings = resolved_settings
    application.state.retrieval_adapter = retrieval_slot
    application.state.database_engine = database_engine
    application.state.redis_client = redis_client
    application.state.auth_service = (
        AuthService(
            database_engine, redis_client, resolved_settings, SMTPEmailSender(resolved_settings)
        )
        if database_engine is not None and redis_client is not None
        else None
    )
    application.state.request_timeout_seconds = resolved_settings.request_timeout_seconds
    application.state.oidc_verifier = (
        OIDCVerifier(resolved_settings)
        if all(
            (
                resolved_settings.oidc_issuer,
                resolved_settings.oidc_audience,
                resolved_settings.oidc_jwks_url,
            )
        )
        else None
    )
    application.state.rate_limiter = (
        RedisRateLimiter(redis_client, resolved_settings) if redis_client else None
    )
    application.state.auth_identity_rate_limiter = (
        RedisRateLimiter(
            redis_client,
            resolved_settings,
            limit=resolved_settings.auth_identity_rate_limit_requests,
            window_seconds=resolved_settings.auth_identity_rate_limit_window_seconds,
            key_prefix="api:auth-identity-rate-limit",
        )
        if redis_client
        else None
    )

    async def database_is_ready() -> bool:
        if postgres_adapter is None:
            return resolved_settings.environment.lower() == "development"
        try:
            return await asyncio.wait_for(postgres_adapter.healthcheck(), timeout=3)
        except Exception:
            return False

    async def adapter_is_ready(adapter: object, configured: bool) -> bool:
        if not configured:
            return False
        probe = getattr(adapter, "healthcheck", None)
        if probe is None:
            return resolved_settings.environment.lower() == "development"
        try:
            return bool(await asyncio.wait_for(probe(), timeout=3))
        except Exception:
            return False

    async def limiter_is_ready() -> bool:
        if application.state.rate_limiter is None:
            return resolved_settings.environment.lower() == "development"
        try:
            return await asyncio.wait_for(application.state.rate_limiter.healthcheck(), timeout=3)
        except Exception:
            return False

    async def authentication_is_ready() -> bool:
        auth_service = application.state.auth_service
        if auth_service is not None:
            try:
                return await asyncio.wait_for(auth_service.healthcheck(), timeout=3)
            except Exception:
                return False
        verifier = application.state.oidc_verifier
        if verifier is None:
            return resolved_settings.environment.lower() == "development"
        try:
            return await asyncio.wait_for(verifier.healthcheck(), timeout=6)
        except Exception:
            return False

    application.state.dependency_checks = {
        "retrieval": lambda: adapter_is_ready(retrieval_slot, retrieval_slot.configured),
        "source_records": lambda: adapter_is_ready(
            source_records_adapter,
            not isinstance(source_records_adapter, UnconfiguredSourceRecords),
        ),
        "answer_generation": lambda: adapter_is_ready(
            answer_generation_adapter,
            not isinstance(answer_generation_adapter, UnconfiguredAnswerGeneration),
        ),
        "rate_limit_store": limiter_is_ready,
        "database": database_is_ready,
        "authentication": authentication_is_ready,
    }
    application.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=resolved_settings.max_request_body_bytes,
    )
    application.add_middleware(
        RequestLoggingMiddleware,
        strict_transport_security=is_production,
    )
    application.include_router(router)
    application.include_router(compat_router)
    register_error_handlers(application)
    return application


app = create_app()
