"""JSON request logging with correlation IDs and no request-body capture."""

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    def __init__(self, environment: str) -> None:
        super().__init__()
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", request_id_context.get()),
            "environment": self.environment,
        }
        for field in (
            "method",
            "path",
            "status_code",
            "duration_ms",
            "exception_type",
            "security_event",
            "outcome",
            "subject_hash",
            "client_ip",
            "deleted_records",
        ):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_context.get()
        return True


def configure_logging(level: str, environment: str) -> None:
    app_logger = logging.getLogger("supreme_court_rag")
    app_logger.setLevel(level)
    app_logger.propagate = False
    existing_handler = next(
        (
            handler
            for handler in app_logger.handlers
            if getattr(handler, "_rag_json_handler", False)
        ),
        None,
    )
    if existing_handler is not None:
        existing_handler.setFormatter(JsonFormatter(environment))
        return
    handler = logging.StreamHandler()
    handler._rag_json_handler = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter(environment))
    handler.addFilter(RequestIdFilter())
    app_logger.addHandler(handler)
