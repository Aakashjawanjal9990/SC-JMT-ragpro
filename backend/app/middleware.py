"""Request correlation and safe access logging middleware."""

import logging
import re
import time
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging_config import request_id_context


logger = logging.getLogger("supreme_court_rag.http")
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestLoggingMiddleware:
    """Adds a request ID and logs method/path/status/duration, never the body."""

    def __init__(self, app: ASGIApp, strict_transport_security: bool = False) -> None:
        self.app = app
        self.strict_transport_security = strict_transport_security

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied_ids = [
            value.decode("latin-1")
            for name, value in scope.get("headers", [])
            if name.lower() == b"x-request-id"
        ]
        supplied_id = supplied_ids[0] if len(supplied_ids) == 1 else ""
        request_id = supplied_id if _VALID_REQUEST_ID.fullmatch(supplied_id) else str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_context.set(request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                protected = {
                    b"x-request-id",
                    b"x-content-type-options",
                    b"referrer-policy",
                    b"cache-control",
                    b"x-frame-options",
                    b"content-security-policy",
                    b"permissions-policy",
                    b"strict-transport-security",
                }
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() not in protected
                ]
                headers.extend(
                    [
                        (b"x-request-id", request_id.encode("ascii")),
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"cache-control", b"no-store"),
                        (b"x-frame-options", b"DENY"),
                        (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
                        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                    ]
                )
                if self.strict_transport_security:
                    headers.append(
                        (b"strict-transport-security", b"max-age=31536000; includeSubDomains")
                    )
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as exc:
            logger.error(
                "http.request.failed",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status_code": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    "exception_type": type(exc).__name__,
                },
            )
            raise
        else:
            logger.info(
                "http.request.completed",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status_code": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
        finally:
            request_id_context.reset(token)
