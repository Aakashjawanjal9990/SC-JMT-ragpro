
"""FastAPI entry point for the query API."""

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.error_handling import (
    http_exception_handler,
    unexpected_error_handler,
    validation_error_handler,
)
from app.logging_config import configure_logging
from app.middleware import RequestLoggingMiddleware
from app.models import HealthResponse, QueryRequest, QueryResponse, ReadinessResponse
from app.orchestrator import QueryOrchestrator
from app.ports import (
    AdapterNotConfigured,
    AnswerGenerationPort,
    RetrievalPort,
    SourceRecordPort,
    UnconfiguredAnswerGeneration,
    UnconfiguredRetrieval,
    UnconfiguredSourceRecords,
)
from app.settings import Settings


def create_app(
    retrieval: RetrievalPort | None = None,
    source_records: SourceRecordPort | None = None,
    answer_generation: AnswerGenerationPort | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    """Build the API with owner-supplied adapters, or explicit unconfigured defaults."""
    resolved_settings = settings or Settings.from_environment()
    configure_logging(resolved_settings.log_level, resolved_settings.environment)

    retrieval_adapter = (
        retrieval if retrieval is not None else UnconfiguredRetrieval()
    )
    source_records_adapter = (
        source_records if source_records is not None else UnconfiguredSourceRecords()
    )
    answer_generation_adapter = (
        answer_generation
        if answer_generation is not None
        else UnconfiguredAnswerGeneration()
    )

    application = FastAPI(
        title="Supreme Court Judgment Query API",
        version="1.0.0",
        description=(
            "Evidence-grounded query API; storage, retrieval, and model "
            "adapters are injected by their owners."
        ),
    )

    application.state.query_orchestrator = QueryOrchestrator(
        retrieval=retrieval_adapter,
        source_records=source_records_adapter,
        answer_generation=answer_generation_adapter,
    )

    application.state.adapter_readiness = {
        "retrieval": not isinstance(retrieval_adapter, UnconfiguredRetrieval),
        "source_records": not isinstance(
            source_records_adapter, UnconfiguredSourceRecords
        ),
        "answer_generation": not isinstance(
            answer_generation_adapter, UnconfiguredAnswerGeneration
        ),
    }

    application.add_middleware(RequestLoggingMiddleware)

    # Shared error handlers for consistent, client-safe API responses.
    application.add_exception_handler(
        RequestValidationError,
        validation_error_handler,
    )
    application.add_exception_handler(
        StarletteHTTPException,
        http_exception_handler,
    )
    application.add_exception_handler(
        Exception,
        unexpected_error_handler,
    )

    @application.get("/health", response_model=HealthResponse, tags=["operations"])
    async def health() -> HealthResponse:
        """Report whether the application process is alive."""
        return HealthResponse()

    @application.get(
        "/ready",
        response_model=ReadinessResponse,
        tags=["operations"],
    )
    async def readiness(
        http_request: Request,
    ) -> ReadinessResponse | JSONResponse:
        """Report whether required adapters are configured."""
        dependencies: dict[str, bool] = (
            http_request.app.state.adapter_readiness
        )
        ready = all(dependencies.values())

        response = ReadinessResponse(
            status="ready" if ready else "not_ready",
            dependencies=dependencies,
        )

        if not ready:
            return JSONResponse(
                status_code=503,
                content=response.model_dump(mode="json"),
            )

        return response

    @application.post(
        "/v1/query",
        response_model=QueryResponse,
        tags=["query"],
    )
    async def query(
        request: QueryRequest,
        http_request: Request,
    ) -> QueryResponse:
        """Execute a query through the existing orchestrator."""
        orchestrator: QueryOrchestrator = (
            http_request.app.state.query_orchestrator
        )

        try:
            return await orchestrator.answer(
                request,
                request_id=http_request.state.request_id,
            )
        except AdapterNotConfigured as exc:
            raise HTTPException(
                status_code=503,
                detail="Query dependencies are not configured yet.",
                headers={"Retry-After": "30"},
            ) from exc

    return application


app = create_app()
