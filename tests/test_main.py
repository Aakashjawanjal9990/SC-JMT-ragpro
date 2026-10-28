"""Unit tests for backend.app.main (FastAPI composition root).

Run from the repository root:
    $env:PYTHONPATH = "$PWD\backend"
    python -m pytest tests\test_main_composition.py -v
    python -m pytest tests\test_main_composition.py --cov=app.main --cov-report=term-missing -v
"""

import asyncio
import inspect
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI

from app import main as main_module


def make_settings(**overrides):
    values = {
        "log_level": "INFO",
        "environment": "development",
        "redis_url": None,
        "database_url": None,
        "google_cloud_project": None,
        "google_cloud_location": None,
        "vertex_model_id": None,
        "vertex_embedding_model_id": None,
        "cors_origins": (),
        "max_context_chars": 8000,
        "request_timeout_seconds": 30,
        "oidc_issuer": None,
        "oidc_audience": None,
        "oidc_jwks_url": None,
        "auth_identity_rate_limit_requests": 5,
        "auth_identity_rate_limit_window_seconds": 60,
        "max_request_body_bytes": 1024 * 1024,
        "chat_retention_days": 30,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_retrieval_settings(**overrides):
    values = {
        "vector_store": "postgres",
        "sqlite_path": None,
        "embedding_model": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def setup_create_app(monkeypatch):
    monkeypatch.setattr(main_module, "configure_logging", Mock())
    monkeypatch.setattr(
        main_module.RetrievalSettings,
        "from_environment",
        staticmethod(lambda: make_retrieval_settings()),
    )
    return monkeypatch


class FakeRetrieval:
    def __init__(self, result=True, *, error=None, warmup_error=None, close_error=None):
        self.result = result
        self.error = error
        self.warmup_error = warmup_error
        self.close_error = close_error
        self.warmup_calls = 0
        self.close_calls = 0

    async def retrieve(self, question, filters, limit):
        return []

    async def healthcheck(self):
        if self.error:
            raise self.error
        return self.result

    def warmup(self):
        self.warmup_calls += 1
        if self.warmup_error:
            raise self.warmup_error

    def close(self):
        self.close_calls += 1
        if self.close_error:
            raise self.close_error


class FakeEngine:
    def __init__(self):
        self.dispose = AsyncMock()
        self.begin = Mock(return_value=FakeAsyncContext())
        self.connect = Mock(return_value=FakeAsyncContext())


class FakeAsyncContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, *args, **kwargs):
        return SimpleNamespace(rowcount=0, all=lambda: [])

    async def scalar(self, *args, **kwargs):
        return 0


class FakeCandidate:
    def __init__(self, *, warmup_error=None, close_error=None):
        self.warmup_error = warmup_error
        self.close_error = close_error
        self.warmup_calls = 0
        self.close_calls = 0

    def warmup(self):
        self.warmup_calls += 1
        if self.warmup_error:
            raise self.warmup_error

    def close(self):
        self.close_calls += 1
        if self.close_error:
            raise self.close_error

    async def retrieve(self, question, filters, limit):
        return []

    async def healthcheck(self):
        return True


def test_retrieval_slot_unconfigured_status_and_replace():
    slot = main_module._RetrievalSlot(main_module.UnconfiguredRetrieval())

    assert slot.configured is False

    adapter = FakeRetrieval()
    slot.replace(adapter)

    assert slot.configured is True


@pytest.mark.asyncio
async def test_retrieval_slot_delegates_retrieve():
    adapter = Mock()
    adapter.retrieve = AsyncMock(return_value=["passage"])
    slot = main_module._RetrievalSlot(adapter)

    result = await slot.retrieve("question", object(), 4)

    assert result == ["passage"]
    adapter.retrieve.assert_awaited_once()


@pytest.mark.asyncio
async def test_retrieval_slot_healthcheck_returns_false_when_unconfigured():
    slot = main_module._RetrievalSlot(main_module.UnconfiguredRetrieval())

    assert await slot.healthcheck() is False


@pytest.mark.asyncio
async def test_retrieval_slot_healthcheck_returns_false_without_probe():
    slot = main_module._RetrievalSlot(SimpleNamespace(retrieve=AsyncMock()))

    assert await slot.healthcheck() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [True, 1, "healthy", False, 0, None])
async def test_retrieval_slot_healthcheck_awaits_async_probe_and_coerces_result(result):
    adapter = SimpleNamespace(healthcheck=AsyncMock(return_value=result), retrieve=AsyncMock())
    slot = main_module._RetrievalSlot(adapter)

    assert await slot.healthcheck() is bool(result)


@pytest.mark.asyncio
async def test_retrieval_slot_healthcheck_runs_sync_probe_in_thread(monkeypatch):
    adapter = SimpleNamespace(healthcheck=lambda: True, retrieve=AsyncMock())
    slot = main_module._RetrievalSlot(adapter)
    thread_calls = []
    async def fake_to_thread(func, *args, **kwargs):
        thread_calls.append(func)
        return func(*args, **kwargs)
    monkeypatch.setattr(main_module.asyncio, "to_thread", fake_to_thread)

    assert await slot.healthcheck() is True
    assert thread_calls == [adapter.healthcheck]


@pytest.mark.asyncio
async def test_retrieval_slot_healthcheck_awaits_awaitable_returned_by_sync_probe(monkeypatch):
    async def delayed_result():
        return True
    adapter = SimpleNamespace(healthcheck=lambda: delayed_result(), retrieve=AsyncMock())
    slot = main_module._RetrievalSlot(adapter)

    assert await slot.healthcheck() is True


@pytest.mark.asyncio
async def test_retrieval_slot_healthcheck_returns_false_on_exception():
    adapter = SimpleNamespace(
        healthcheck=AsyncMock(side_effect=RuntimeError("probe failed")),
        retrieve=AsyncMock(),
    )
    slot = main_module._RetrievalSlot(adapter)

    assert await slot.healthcheck() is False


def test_create_app_uses_injected_adapters_and_sets_state(setup_create_app):
    retrieval = FakeRetrieval()
    source = object()
    answer = object()
    settings = make_settings()

    app = main_module.create_app(
        retrieval=retrieval,
        source_records=source,
        answer_generation=answer,
        settings=settings,
    )

    assert isinstance(app, FastAPI)
    assert app.title == "Supreme Court Judgment Query API"
    assert app.version == "1.0.0"
    assert app.state.environment == "development"
    assert app.state.settings is settings
    assert app.state.retrieval_adapter.configured is True
    assert app.state.query_orchestrator is not None
    assert app.state.database_engine is None
    assert app.state.redis_client is None
    assert app.state.auth_service is None
    assert app.state.oidc_verifier is None
    assert app.state.rate_limiter is None
    assert app.state.auth_identity_rate_limiter is None


def test_create_app_defaults_to_unconfigured_adapters(setup_create_app):
    app = main_module.create_app(settings=make_settings())

    assert app.state.retrieval_adapter.configured is False
    assert app.state.dependency_checks.keys() == {
        "retrieval",
        "source_records",
        "answer_generation",
        "rate_limit_store",
        "database",
        "authentication",
    }


def test_create_app_hides_api_docs_in_production(setup_create_app):
    app = main_module.create_app(settings=make_settings(environment="production"))

    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None
    assert app.state.environment == "production"


def test_create_app_enables_cors_when_origins_configured(setup_create_app):
    app = main_module.create_app(
        settings=make_settings(cors_origins=("https://example.test",))
    )

    assert any(middleware.cls.__name__ == "CORSMiddleware" for middleware in app.user_middleware)


def test_create_app_does_not_add_cors_when_origins_empty(setup_create_app):
    app = main_module.create_app(settings=make_settings(cors_origins=()))

    assert not any(middleware.cls.__name__ == "CORSMiddleware" for middleware in app.user_middleware)


@pytest.mark.asyncio
async def test_create_app_lifespan_warms_and_closes_injected_retrieval(setup_create_app):
    retrieval = FakeRetrieval()
    app = main_module.create_app(retrieval=retrieval, settings=make_settings())

    async with app.router.lifespan_context(app):
        assert retrieval.warmup_calls == 1
        assert retrieval.close_calls == 0

    assert retrieval.close_calls == 1


@pytest.mark.asyncio
async def test_lifespan_continues_if_injected_retrieval_warmup_fails(setup_create_app):
    retrieval = FakeRetrieval(warmup_error=RuntimeError("warmup failed"))
    app = main_module.create_app(retrieval=retrieval, settings=make_settings())

    with pytest.raises(RuntimeError, match="warmup failed"):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.asyncio
async def test_lifespan_does_not_fail_when_retrieval_close_raises(setup_create_app, caplog):
    retrieval = FakeRetrieval(close_error=RuntimeError("close failed"))
    app = main_module.create_app(retrieval=retrieval, settings=make_settings())

    with caplog.at_level(logging.ERROR):
        async with app.router.lifespan_context(app):
            pass

    assert "Retrieval adapter failed to close cleanly" in caplog.text


@pytest.mark.asyncio
async def test_lifespan_initializes_sqlite_retrieval_adapter_when_enabled(
    setup_create_app, monkeypatch
):
    candidate = FakeCandidate()
    monkeypatch.setenv("RETRIEVAL_PORT_ADAPTER_ENABLED", "true")
    monkeypatch.setattr(
        main_module.RetrievalSettings,
        "from_environment",
        staticmethod(lambda: make_retrieval_settings(vector_store="sqlite", sqlite_path="db.sqlite")),
    )
    monkeypatch.setattr(
        main_module,
        "create_backend_retrieval_adapter",
        Mock(return_value=candidate),
    )
    app = main_module.create_app(settings=make_settings())

    async with app.router.lifespan_context(app):
        assert app.state.retrieval_adapter._adapter is candidate
        assert candidate.warmup_calls == 1

    assert candidate.close_calls == 1


@pytest.mark.asyncio
async def test_lifespan_logs_warning_when_adapter_enabled_without_database(
    setup_create_app, monkeypatch, caplog
):
    monkeypatch.setenv("RETRIEVAL_PORT_ADAPTER_ENABLED", "true")
    monkeypatch.setattr(
        main_module.RetrievalSettings,
        "from_environment",
        staticmethod(lambda: make_retrieval_settings(vector_store="postgres")),
    )
    app = main_module.create_app(settings=make_settings())

    with caplog.at_level(logging.WARNING):
        async with app.router.lifespan_context(app):
            pass

    assert "DATABASE_URL is not configured" in caplog.text


@pytest.mark.asyncio
async def test_lifespan_falls_back_if_sqlite_adapter_initialization_fails(
    setup_create_app, monkeypatch, caplog
):
    candidate = FakeCandidate(warmup_error=RuntimeError("bad sqlite"))
    monkeypatch.setenv("RETRIEVAL_PORT_ADAPTER_ENABLED", "true")
    monkeypatch.setattr(
        main_module.RetrievalSettings,
        "from_environment",
        staticmethod(lambda: make_retrieval_settings(vector_store="sqlite", sqlite_path="db.sqlite")),
    )
    monkeypatch.setattr(main_module, "create_backend_retrieval_adapter", Mock(return_value=candidate))
    app = main_module.create_app(settings=make_settings())

    with caplog.at_level(logging.ERROR):
        async with app.router.lifespan_context(app):
            assert app.state.retrieval_adapter.configured is False

    assert candidate.close_calls == 1
    assert "could not start" in caplog.text


@pytest.mark.asyncio
async def test_dependency_checks_return_development_defaults(setup_create_app):
    app = main_module.create_app(settings=make_settings(environment="development"))

    checks = app.state.dependency_checks
    assert await checks["database"]() is True
    assert await checks["rate_limit_store"]() is True
    assert await checks["authentication"]() is True
    assert await checks["retrieval"]() is False
    assert await checks["source_records"]() is False
    assert await checks["answer_generation"]() is False


@pytest.mark.asyncio
async def test_dependency_checks_return_false_for_unconfigured_production_dependencies(
    setup_create_app,
):
    app = main_module.create_app(settings=make_settings(environment="production"))

    checks = app.state.dependency_checks
    assert await checks["database"]() is False
    assert await checks["rate_limit_store"]() is False
    assert await checks["authentication"]() is False


@pytest.mark.asyncio
async def test_adapter_is_ready_returns_false_when_not_configured(setup_create_app):
    app = main_module.create_app(settings=make_settings())

    assert await app.state.dependency_checks["retrieval"]() is False


@pytest.mark.asyncio
async def test_adapter_is_ready_handles_probe_without_healthcheck_in_development(setup_create_app):
    adapter = SimpleNamespace(retrieve=AsyncMock())
    app = main_module.create_app(
        source_records=adapter,
        settings=make_settings(environment="development"),
    )

    assert await app.state.dependency_checks["source_records"]() is True


@pytest.mark.asyncio
async def test_adapter_is_ready_returns_false_when_healthcheck_raises(setup_create_app):
    adapter = SimpleNamespace(
        retrieve=AsyncMock(),
        healthcheck=AsyncMock(side_effect=RuntimeError("unhealthy")),
    )
    app = main_module.create_app(
        source_records=adapter,
        settings=make_settings(environment="production"),
    )

    assert await app.state.dependency_checks["source_records"]() is False


@pytest.mark.asyncio
async def test_database_readiness_handles_postgres_healthcheck_failure(setup_create_app, monkeypatch):
    postgres = SimpleNamespace(healthcheck=AsyncMock(side_effect=RuntimeError("db down")))
    monkeypatch.setattr(main_module, "PostgresQueryAdapter", Mock(return_value=postgres))
    monkeypatch.setattr(main_module, "create_async_engine", Mock(return_value=object()))
    monkeypatch.setattr(
        main_module,
        "VertexAIAdapter",
        Mock(return_value=SimpleNamespace(close=AsyncMock())),
    )
    settings = make_settings(
        database_url="postgresql+asyncpg://example/db",
        google_cloud_project="project",
        google_cloud_location="location",
        vertex_model_id="model",
        vertex_embedding_model_id="embedding",
    )
    app = main_module.create_app(settings=settings)

    assert await app.state.dependency_checks["database"]() is False


@pytest.mark.asyncio
async def test_authentication_readiness_uses_oidc_verifier_when_auth_service_absent(
    setup_create_app, monkeypatch
):
    verifier = SimpleNamespace(healthcheck=AsyncMock(return_value=True))
    monkeypatch.setattr(main_module, "OIDCVerifier", Mock(return_value=verifier))
    app = main_module.create_app(
        settings=make_settings(
            oidc_issuer="issuer",
            oidc_audience="audience",
            oidc_jwks_url="https://example.test/jwks",
        )
    )

    assert await app.state.dependency_checks["authentication"]() is True
    verifier.healthcheck.assert_awaited_once()


@pytest.mark.asyncio
async def test_authentication_readiness_returns_false_when_oidc_probe_fails(
    setup_create_app, monkeypatch
):
    verifier = SimpleNamespace(healthcheck=AsyncMock(side_effect=RuntimeError("OIDC down")))
    monkeypatch.setattr(main_module, "OIDCVerifier", Mock(return_value=verifier))
    app = main_module.create_app(
        settings=make_settings(
            environment="production",
            oidc_issuer="issuer",
            oidc_audience="audience",
            oidc_jwks_url="https://example.test/jwks",
        )
    )

    assert await app.state.dependency_checks["authentication"]() is False


def test_configure_logging_receives_settings(setup_create_app):
    settings = make_settings(log_level="DEBUG", environment="test")
    main_module.create_app(settings=settings)

    main_module.configure_logging.assert_called_with("DEBUG", "test")


@pytest.mark.asyncio
async def test_lifespan_does_not_start_adapter_when_flag_disabled(setup_create_app, monkeypatch):
    monkeypatch.delenv("RETRIEVAL_PORT_ADAPTER_ENABLED", raising=False)
    factory = Mock()
    monkeypatch.setattr(main_module, "create_backend_retrieval_adapter", factory)
    app = main_module.create_app(settings=make_settings())

    async with app.router.lifespan_context(app):
        pass

    factory.assert_not_called()
