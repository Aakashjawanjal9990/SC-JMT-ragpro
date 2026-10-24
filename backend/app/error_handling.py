
"""Shared, client-safe error responses for the query API."""

import re
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def _request_id(request: Request) -> str:
    """Reuse the request ID created by the request middleware."""
    return getattr(request.state, "request_id", "unknown")


def error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool,
    details: list[dict[str, str]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build the shared error envelope without exposing internal details."""
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if details:
        error["details"] = details

    return JSONResponse(
        status_code=status_code,
        content={
            "request_id": _request_id(request),
            "error": error,
        },
        headers=headers,
    )


def _safe_validation_message(field: str, error_type: str) -> str:
    """Return a fixed message; never use Pydantic's raw error message."""
    if error_type == "missing":
        if field == "question":
            return "A question is required."
        return "This field is required."
    if error_type in {"string_too_short", "too_short"}:
        return "This field is too short."
    if error_type in {"string_too_long", "too_long"}:
        return "This field is too long."
    if error_type == "string_pattern_mismatch":
        return "This field has an invalid format."
    if error_type == "extra_forbidden":
        return "This field is not allowed."
    return "This field has an invalid value."


async def validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Format request validation errors without echoing submitted values."""
    details: list[dict[str, str]] = []

    for item in exc.errors():
        location = [
            str(part)
            for part in item.get("loc", ())
            if part not in {"body", "query", "path", "header", "cookie"}
        ]
        field = ".".join(location) or "request"

        raw_type = str(item.get("type", "invalid_value"))
        error_type = (
            raw_type
            if re.fullmatch(r"[a-z0-9_]{1,64}", raw_type)
            else "invalid_value"
        )

        details.append(
            {
                "field": field,
                "code": error_type,
                "message": _safe_validation_message(field, error_type),
            }
        )

    return error_response(
        request,
        status_code=422,
        code="VALIDATION_ERROR",
        message="The request is invalid.",
        retryable=False,
        details=details,
    )


async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    """Map known HTTP failures to the shared error envelope."""
    known_errors = {
        401: ("UNAUTHORIZED", "Authentication is required or invalid.", False),
        403: ("FORBIDDEN", "You are not permitted to perform this action.", False),
        404: ("NOT_FOUND", "The requested resource was not found.", False),
        422: ("VALIDATION_ERROR", "The request is invalid.", False),
        503: (
            "SERVICE_UNAVAILABLE",
            "A required service is temporarily unavailable.",
            True,
        ),
    }

    code, message, retryable = known_errors.get(
        exc.status_code,
        ("INTERNAL_ERROR", "An unexpected server error occurred.", False),
    )

    # Preserve only the defined contract status codes.
    status_code = exc.status_code if exc.status_code in known_errors else 500

    return error_response(
        request,
        status_code=status_code,
        code=code,
        message=message,
        retryable=retryable,
        headers=exc.headers,
    )


async def unexpected_error_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Return a generic 500 response without exposing exception internals."""
    return error_response(
        request,
        status_code=500,
        code="INTERNAL_ERROR",
        message="An unexpected server error occurred.",
        retryable=False,
    )
