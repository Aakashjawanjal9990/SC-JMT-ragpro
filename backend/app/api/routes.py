"""Versioned HTTP routes for the query service and operational checks."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.models import HealthResponse, QueryRequest, QueryResponse, ReadinessResponse
from app.orchestrator import QueryOrchestrator
from app.ports import AdapterNotConfigured, DependencyUnavailable, UnsupportedQueryFilter
from app.rate_limit import enforce_rate_limit
from app.retrieval.errors import FilterError, RetrievalFailure, RetrievalTimeoutError
from app.security import AuthenticatedUser, require_authenticated_user

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["operations"])
async def health() -> HealthResponse:
    """Report that the process is alive; this does not check dependencies."""
    return HealthResponse()


@router.get("/ready", response_model=ReadinessResponse, tags=["operations"])
async def readiness(request: Request) -> ReadinessResponse | JSONResponse:
    """Report configuration and bounded health checks for runtime dependencies."""
    checks = request.app.state.dependency_checks
    outcomes = await asyncio.gather(*(check() for check in checks.values()))
    dependencies: dict[str, bool] = dict(zip(checks, outcomes, strict=True))
    ready = all(dependencies.values())
    # Do not expose dependency-level details to unauthenticated callers.
    response = ReadinessResponse(status="ready" if ready else "not_ready", dependencies={})
    if not ready:
        return JSONResponse(status_code=503, content=response.model_dump(mode="json"))
    return response


@router.post(
    "/v1/query",
    response_model=QueryResponse,
    tags=["query"],
)
async def query(
    request: QueryRequest,
    http_request: Request,
    authenticated_user: AuthenticatedUser = Depends(require_authenticated_user),
) -> QueryResponse:
    """Run retrieval and evidence-grounded answer generation."""
    await enforce_rate_limit(http_request, authenticated_user)
    orchestrator: QueryOrchestrator = http_request.app.state.query_orchestrator
    try:
        async with asyncio.timeout(http_request.app.state.request_timeout_seconds):
            return await orchestrator.answer(request, request_id=http_request.state.request_id)
    except FilterError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    except RetrievalTimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail="Retrieval did not respond in time.",
            headers={"Retry-After": "5"},
        ) from exc
    except RetrievalFailure as exc:
        raise HTTPException(
            status_code=503,
            detail="Retrieval is temporarily unavailable.",
            headers={"Retry-After": "5"},
        ) from exc
    except TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="The query dependencies did not respond in time.",
            headers={"Retry-After": "5"},
        ) from exc
    except UnsupportedQueryFilter as exc:
        raise HTTPException(
            status_code=422,
            detail="The requested filter is not supported by the current database schema.",
        ) from exc
    except AdapterNotConfigured as exc:
        raise HTTPException(
            status_code=503,
            detail="Query dependencies are not configured yet.",
            headers={"Retry-After": "30"},
        ) from exc
    except DependencyUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="A query dependency is temporarily unavailable.",
            headers={"Retry-After": "5"},
        ) from exc
