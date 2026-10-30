"""Safe HTTP error formatting for malformed API requests."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def register_error_handlers(application: FastAPI) -> None:
    """Register handlers that do not echo user-submitted values."""

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del request  # The request body may contain sensitive case queries.
        safe_errors = []
        for error in exc.errors():
            message = error.get("msg", "Invalid request value.")
            safe_errors.append(
                {
                    "field": ".".join(str(part) for part in error.get("loc", ()) if part != "body"),
                    "code": error.get("type", "invalid_value"),
                    "message": message,
                    "msg": message,
                }
            )
        return JSONResponse(status_code=422, content={"detail": safe_errors})
