"""Unit tests for JSON request logging and correlation IDs."""

import importlib
import json
import logging
import os
from datetime import UTC, datetime

import pytest


# Override this if the logging script has a different module path:
# PowerShell: $env:LOGGING_MODULE = "app.your_logging_module"
MODULE_NAME = os.getenv("LOGGING_MODULE", "app.logging_config")
logging_module = importlib.import_module(MODULE_NAME)

JsonFormatter = logging_module.JsonFormatter
RequestIdFilter = logging_module.RequestIdFilter
request_id_context = logging_module.request_id_context
configure_logging = logging_module.configure_logging


@pytest.fixture
def app_logger():
    """Provide a clean logger and restore its original configuration afterward."""
    logger = logging.getLogger("supreme_court_rag")
    old_level = logger.level
    old_propagate = logger.propagate
    old_handlers = list(logger.handlers)

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    yield logger

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    for handler in old_handlers:
        logger.addHandler(handler)
    logger.setLevel(old_level)
    logger.propagate = old_propagate


@pytest.fixture(autouse=True)
def reset_request_id():
    token = request_id_context.set("-")
    yield
    request_id_context.reset(token)


def make_record(message="test message", **extra):
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_outputs_required_fields_and_valid_timestamp():
    formatter = JsonFormatter("test")
    record = make_record()
    record.created = 1_700_000_000.0

    result = json.loads(formatter.format(record))

    assert result["timestamp"] == datetime.fromtimestamp(
        record.created, UTC
    ).isoformat()
    assert result["level"] == "INFO"
    assert result["logger"] == "test.logger"
    assert result["message"] == "test message"
    assert result["request_id"] == "-"
    assert result["environment"] == "test"


def test_formatter_uses_request_id_from_record_when_present():
    record = make_record(request_id="record-id")
    request_id_context.set("context-id")

    result = json.loads(JsonFormatter("dev").format(record))

    assert result["request_id"] == "record-id"


def test_formatter_falls_back_to_context_request_id():
    request_id_context.set("context-id")

    result = json.loads(JsonFormatter("dev").format(make_record()))

    assert result["request_id"] == "context-id"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("method", "POST"),
        ("path", "/api/query"),
        ("status_code", 200),
        ("duration_ms", 12.5),
        ("exception_type", "ValueError"),
        ("security_event", "login"),
        ("outcome", "success"),
        ("subject_hash", "hash123"),
        ("client_ip", "192.0.2.1"),
        ("deleted_records", 3),
    ],
)
def test_formatter_includes_optional_fields_when_present(field, value):
    result = json.loads(JsonFormatter("test").format(make_record(**{field: value})))

    assert result[field] == value


def test_formatter_omits_optional_fields_when_none():
    result = json.loads(JsonFormatter("test").format(make_record()))

    optional_fields = {
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
    }
    assert optional_fields.isdisjoint(result)


def test_formatter_preserves_unicode_and_uses_compact_json():
    result = JsonFormatter("test").format(make_record("न्याय मित्र"))

    assert "न्याय मित्र" in result
    assert ": " not in result
    assert ", " not in result
    assert json.loads(result)["message"] == "न्याय मित्र"


def test_request_id_filter_adds_context_id_when_missing():
    request_id_context.set("filter-id")
    record = make_record()

    assert RequestIdFilter().filter(record) is True
    assert record.request_id == "filter-id"


def test_request_id_filter_does_not_overwrite_existing_id():
    request_id_context.set("context-id")
    record = make_record(request_id="existing-id")

    assert RequestIdFilter().filter(record) is True
    assert record.request_id == "existing-id"


def test_configure_logging_creates_json_handler_and_sets_logger_options(app_logger):
    configure_logging("DEBUG", "test-env")

    assert app_logger.level == logging.DEBUG
    assert app_logger.propagate is False
    tagged_handlers = [
        handler
        for handler in app_logger.handlers
        if getattr(handler, "_rag_json_handler", False)
    ]
    assert len(tagged_handlers) == 1
    handler = tagged_handlers[0]
    assert isinstance(handler.formatter, JsonFormatter)
    assert handler.formatter.environment == "test-env"
    assert any(isinstance(item, RequestIdFilter) for item in handler.filters)


def test_configure_logging_updates_existing_tagged_handler_without_adding_another(
    app_logger,
):
    configure_logging("INFO", "first-env")
    tagged_before = [
        handler
        for handler in app_logger.handlers
        if getattr(handler, "_rag_json_handler", False)
    ]
    assert len(tagged_before) == 1
    original_handler = tagged_before[0]

    configure_logging("WARNING", "second-env")

    tagged_after = [
        handler
        for handler in app_logger.handlers
        if getattr(handler, "_rag_json_handler", False)
    ]
    assert app_logger.level == logging.WARNING
    assert len(tagged_after) == 1
    assert tagged_after[0] is original_handler
    assert isinstance(original_handler.formatter, JsonFormatter)
    assert original_handler.formatter.environment == "second-env"


def test_configure_logging_can_emit_json_with_request_id(app_logger, caplog):
    configure_logging("INFO", "test-env")
    request_id_context.set("request-123")

    # Capture the actual configured stream handler output.
    handler = next(
        item
        for item in app_logger.handlers
        if getattr(item, "_rag_json_handler", False)
    )
    import io

    stream = io.StringIO()
    handler.setStream(stream)
    app_logger.info("query received", extra={"method": "POST", "path": "/api/query"})

    output = json.loads(stream.getvalue())
    assert output["message"] == "query received"
    assert output["request_id"] == "request-123"
    assert output["method"] == "POST"
    assert output["path"] == "/api/query"


def test_formatter_does_not_capture_request_body_unless_explicitly_added_as_message():
    # The formatter emits a defined allowlist of structured fields, not arbitrary
    # attributes. This checks that an unapproved request_body attribute is omitted.
    record = make_record(request_body={"secret": "do-not-log"}, method="POST")

    result = json.loads(JsonFormatter("test").format(record))

    assert "request_body" not in result
    assert "secret" not in result
    assert result["method"] == "POST"
