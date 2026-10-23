"""FastAPI entry point for the query API."""

from fastapi.exceptions import RequestValidationError
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

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
    retrieval_adapter = retrieval if retrieval is not None else UnconfiguredRetrieval()
    source_records_adapter = source_records if source_records is not None else UnconfiguredSourceRecords()
    answer_generation_adapter = (
        answer_generation if answer_generation is not None else UnconfiguredAnswerGeneration()
    )
    application = FastAPI(
        title="Supreme Court Judgment Query API",
        version="1.0.0",
        description="Evidence-grounded query API; storage, retrieval, and model adapters are injected by their owners.",
    )
    application.state.query_orchestrator = QueryOrchestrator(
        retrieval=retrieval_adapter,
        source_records=source_records_adapter,
        answer_generation=answer_generation_adapter,
    )
    application.state.adapter_readiness = {
        "retrieval": not isinstance(retrieval_adapter, UnconfiguredRetrieval),
        "source_records": not isinstance(source_records_adapter, UnconfiguredSourceRecords),
        "answer_generation": not isinstance(answer_generation_adapter, UnconfiguredAnswerGeneration),
    }
    application.add_middleware(RequestLoggingMiddleware)

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        http_request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Do not echo submitted values (questions, identifiers, or other user data).
        safe_errors = [
            {
                "field": ".".join(str(part) for part in error.get("loc", ()) if part != "body"),
                "code": error.get("type", "invalid_value"),
                "message": error.get("msg", "Invalid request value."),
            }
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": safe_errors})

    @application.get("/health", response_model=HealthResponse, tags=["operations"])
    async def health() -> HealthResponse:
        return HealthResponse()

    @application.get("/ready", response_model=ReadinessResponse, tags=["operations"])
    async def readiness(http_request: Request) -> ReadinessResponse | JSONResponse:
        dependencies: dict[str, bool] = http_request.app.state.adapter_readiness
        ready = all(dependencies.values())
        response = ReadinessResponse(
            status="ready" if ready else "not_ready",
            dependencies=dependencies,
        )
        if not ready:
            return JSONResponse(status_code=503, content=response.model_dump(mode="json"))
        return response

    @application.post("/v1/query", response_model=QueryResponse, tags=["query"])
    async def query(request: QueryRequest, http_request: Request) -> QueryResponse:
        orchestrator: QueryOrchestrator = http_request.app.state.query_orchestrator
        try:
            return await orchestrator.answer(request, request_id=http_request.state.request_id)
        except AdapterNotConfigured as exc:
            raise HTTPException(
                status_code=503,
                detail="Query dependencies are not configured yet.",
                headers={"Retry-After": "30"},
            ) from exc

    return application


app = create_app()
