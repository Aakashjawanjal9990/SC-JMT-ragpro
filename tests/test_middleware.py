"""Unit tests for request body-limit and request logging ASGI middleware.

Expected application module: backend/app/middleware.py
If the middleware lives in another module, update the import below.
"""

import json
import logging
from unittest.mock import patch

import pytest

from app import middleware as middleware_module


def http_scope(headers=None, method="POST", path="/query"):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }


def message_body(messages):
    return b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")


def header_dict(start_message):
    return {k.lower(): v for k, v in start_message.get("headers", [])}


def request_messages(*parts):
    messages = []
    for index, part in enumerate(parts):
        messages.append(
            {
                "type": "http.request",
                "body": part,
                "more_body": index < len(parts) - 1,
            }
        )
    return messages


async def invoke(middleware, scope, incoming):
    sent = []
    queue = list(incoming)

    async def receive():
        if queue:
            return queue.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


class EchoApp:
    def __init__(self, status=200, body=b"ok", headers=None, raise_error=None):
        self.status = status
        self.body = body
        self.headers = headers or []
        self.raise_error = raise_error
        self.scope = None
        self.received_body = None
        self.receive_messages = []

    async def __call__(self, scope, receive, send):
        self.scope = scope
        if self.raise_error:
            raise self.raise_error
        chunks = []
        for _ in range(3):
            message = await receive()
            self.receive_messages.append(message)
            if message["type"] == "http.disconnect":
                break
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        self.received_body = b"".join(chunks)
        await send(
            {
                "type": "http.response.start",
                "status": self.status,
                "headers": self.headers,
            }
        )
        await send({"type": "http.response.body", "body": self.body})


# RequestBodyLimitMiddleware tests


@pytest.mark.asyncio
async def test_body_limit_passes_non_http_scope_through_unchanged():
    captured = []

    async def app(scope, receive, send):
        captured.append(scope)
        await send({"type": "custom.message"})

    scope = {"type": "websocket", "path": "/ws"}
    sent = await invoke(
        middleware_module.RequestBodyLimitMiddleware(app, max_body_bytes=4),
        scope,
        [],
    )
    assert captured == [scope]
    assert sent == [{"type": "custom.message"}]


@pytest.mark.asyncio
async def test_body_limit_rejects_invalid_content_length_with_400():
    app = EchoApp()
    middleware = middleware_module.RequestBodyLimitMiddleware(app, 10)
    sent = await invoke(middleware, http_scope([(b"content-length", b"abc")]), [])
    assert sent[0]["status"] == 400
    assert json.loads(message_body(sent)) == {"detail": "Invalid Content-Length."}
    assert header_dict(sent[0])[b"cache-control"] == b"no-store"
    assert app.scope is None


@pytest.mark.asyncio
@pytest.mark.parametrize("declared_length", [b"-1", b"11", b"1000"])
async def test_body_limit_rejects_negative_or_oversized_content_length(declared_length):
    app = EchoApp()
    sent = await invoke(
        middleware_module.RequestBodyLimitMiddleware(app, 10),
        http_scope([(b"content-length", declared_length)]),
        [],
    )
    assert sent[0]["status"] == 413
    assert json.loads(message_body(sent)) == {"detail": "Request body is too large."}
    assert app.scope is None


@pytest.mark.asyncio
async def test_body_limit_accepts_content_length_equal_to_limit():
    app = EchoApp()
    sent = await invoke(
        middleware_module.RequestBodyLimitMiddleware(app, 4),
        http_scope([(b"content-length", b"4")]),
        request_messages(b"data"),
    )
    assert sent[0]["status"] == 200
    assert app.received_body == b"data"


@pytest.mark.asyncio
async def test_body_limit_rejects_body_that_exceeds_limit_without_content_length():
    app = EchoApp()
    sent = await invoke(
        middleware_module.RequestBodyLimitMiddleware(app, 4),
        http_scope(),
        request_messages(b"abc", b"de"),
    )
    assert sent[0]["status"] == 413
    assert app.scope is None


@pytest.mark.asyncio
async def test_body_limit_buffers_multiple_chunks_and_replays_combined_body():
    app = EchoApp()
    sent = await invoke(
        middleware_module.RequestBodyLimitMiddleware(app, 10),
        http_scope(),
        request_messages(b"ab", b"cd", b"ef"),
    )
    assert sent[0]["status"] == 200
    assert app.received_body == b"abcdef"
    assert app.receive_messages[0]["more_body"] is False


@pytest.mark.asyncio
async def test_body_limit_empty_body_is_forwarded():
    app = EchoApp()
    sent = await invoke(
        middleware_module.RequestBodyLimitMiddleware(app, 0),
        http_scope(),
        request_messages(b""),
    )
    assert sent[0]["status"] == 200
    assert app.received_body == b""


@pytest.mark.asyncio
async def test_body_limit_returns_without_calling_app_on_disconnect():
    app = EchoApp()
    sent = await invoke(
        middleware_module.RequestBodyLimitMiddleware(app, 10),
        http_scope(),
        [{"type": "http.disconnect"}],
    )
    assert sent == []
    assert app.scope is None


@pytest.mark.asyncio
async def test_body_limit_skips_unexpected_receive_message_types():
    app = EchoApp()
    sent = await invoke(
        middleware_module.RequestBodyLimitMiddleware(app, 10),
        http_scope(),
        [{"type": "other.message"}, *request_messages(b"hello")],
    )
    assert sent[0]["status"] == 200
    assert app.received_body == b"hello"


@pytest.mark.asyncio
async def test_body_limit_reject_response_has_json_content_type_and_length():
    sent = await invoke(
        middleware_module.RequestBodyLimitMiddleware(EchoApp(), 1),
        http_scope([(b"content-length", b"2")]),
        [],
    )
    headers = header_dict(sent[0])
    assert headers[b"content-type"] == b"application/json"
    assert int(headers[b"content-length"]) == len(message_body(sent))


# RequestLoggingMiddleware tests


@pytest.mark.asyncio
async def test_request_logging_passes_non_http_scope_through():
    captured = []

    async def app(scope, receive, send):
        captured.append(scope)
        await send({"type": "custom.message"})

    scope = {"type": "lifespan"}
    sent = await invoke(middleware_module.RequestLoggingMiddleware(app), scope, [])
    assert captured == [scope]
    assert sent == [{"type": "custom.message"}]


@pytest.mark.asyncio
async def test_request_logging_preserves_valid_supplied_request_id(caplog):
    app = EchoApp()
    middleware = middleware_module.RequestLoggingMiddleware(app)
    scope = http_scope([(b"x-request-id", b"request_123-abc.X")])
    with caplog.at_level(logging.INFO, logger="supreme_court_rag.http"):
        sent = await invoke(middleware, scope, request_messages(b"secret request body"))
    assert scope["state"]["request_id"] == "request_123-abc.X"
    assert header_dict(sent[0])[b"x-request-id"] == b"request_123-abc.X"
    assert any(r.message == "http.request.completed" for r in caplog.records)
    assert "secret request body" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "supplied_id",
    [b"bad id", b"has/slash", b"", b"x" * 65, b"\xff"],
)
async def test_request_logging_replaces_invalid_supplied_request_id(supplied_id):
    app = EchoApp()
    sent = await invoke(
        middleware_module.RequestLoggingMiddleware(app),
        http_scope([(b"x-request-id", supplied_id)]),
        request_messages(b""),
    )
    request_id = header_dict(sent[0])[b"x-request-id"].decode("ascii")
    assert request_id
    assert len(request_id) == 36  # UUID string
    assert app.scope["state"]["request_id"] == request_id


@pytest.mark.asyncio
async def test_request_logging_replaces_duplicate_request_id_headers():
    app = EchoApp()
    sent = await invoke(
        middleware_module.RequestLoggingMiddleware(app),
        http_scope([(b"x-request-id", b"first"), (b"x-request-id", b"second")]),
        request_messages(b""),
    )
    assert header_dict(sent[0])[b"x-request-id"] not in (b"first", b"second")


@pytest.mark.asyncio
async def test_request_logging_adds_security_headers_and_removes_existing_protected_headers():
    app = EchoApp(
        headers=[
            (b"x-frame-options", b"SAMEORIGIN"),
            (b"cache-control", b"public"),
            (b"x-custom", b"keep"),
            (b"content-security-policy", b"unsafe-inline"),
        ]
    )
    sent = await invoke(
        middleware_module.RequestLoggingMiddleware(app),
        http_scope(),
        request_messages(b""),
    )
    headers = header_dict(sent[0])
    assert headers[b"x-content-type-options"] == b"nosniff"
    assert headers[b"referrer-policy"] == b"no-referrer"
    assert headers[b"cache-control"] == b"no-store"
    assert headers[b"x-frame-options"] == b"DENY"
    assert headers[b"content-security-policy"] == b"default-src 'none'; frame-ancestors 'none'"
    assert headers[b"permissions-policy"] == b"camera=(), microphone=(), geolocation=()"
    assert headers[b"x-custom"] == b"keep"


@pytest.mark.asyncio
async def test_request_logging_adds_hsts_only_when_enabled():
    app_without = EchoApp()
    sent_without = await invoke(
        middleware_module.RequestLoggingMiddleware(app_without, strict_transport_security=False),
        http_scope(),
        request_messages(b""),
    )
    assert b"strict-transport-security" not in header_dict(sent_without[0])

    app_with = EchoApp()
    sent_with = await invoke(
        middleware_module.RequestLoggingMiddleware(app_with, strict_transport_security=True),
        http_scope(),
        request_messages(b""),
    )
    assert header_dict(sent_with[0])[b"strict-transport-security"] == (
        b"max-age=31536000; includeSubDomains"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 201, 400, 404, 500])
async def test_request_logging_records_response_status(status, caplog):
    app = EchoApp(status=status)
    with caplog.at_level(logging.INFO, logger="supreme_court_rag.http"):
        sent = await invoke(
            middleware_module.RequestLoggingMiddleware(app),
            http_scope(method="GET", path="/health"),
            request_messages(b""),
        )
    assert sent[0]["status"] == status
    record = next(r for r in caplog.records if r.message == "http.request.completed")
    assert record.method == "GET"
    assert record.path == "/health"
    assert record.status_code == status
    assert record.duration_ms >= 0


@pytest.mark.asyncio
async def test_request_logging_logs_exception_and_reraises(caplog):
    app = EchoApp(raise_error=ValueError("sensitive detail"))
    middleware = middleware_module.RequestLoggingMiddleware(app)
    with caplog.at_level(logging.ERROR, logger="supreme_court_rag.http"):
        with pytest.raises(ValueError, match="sensitive detail"):
            await invoke(middleware, http_scope(), request_messages(b""))
    record = next(r for r in caplog.records if r.message == "http.request.failed")
    assert record.exception_type == "ValueError"
    assert record.status_code == 500
    assert "sensitive detail" not in caplog.text


@pytest.mark.asyncio
async def test_request_logging_resets_request_id_context_after_success():
    before = middleware_module.request_id_context.get()
    await invoke(
        middleware_module.RequestLoggingMiddleware(EchoApp()),
        http_scope([(b"x-request-id", b"context-test")]),
        request_messages(b""),
    )
    assert middleware_module.request_id_context.get() == before


@pytest.mark.asyncio
async def test_request_logging_resets_request_id_context_after_exception():
    before = middleware_module.request_id_context.get()
    app = EchoApp(raise_error=RuntimeError("failure"))
    with pytest.raises(RuntimeError):
        await invoke(
            middleware_module.RequestLoggingMiddleware(app),
            http_scope([(b"x-request-id", b"context-error")]),
            request_messages(b""),
        )
    assert middleware_module.request_id_context.get() == before


@pytest.mark.asyncio
async def test_request_logging_defaults_status_to_500_if_app_sends_no_response_start(caplog):
    async def app(scope, receive, send):
        return None

    with caplog.at_level(logging.INFO, logger="supreme_court_rag.http"):
        await invoke(
            middleware_module.RequestLoggingMiddleware(app),
            http_scope(),
            request_messages(b""),
        )
    record = next(r for r in caplog.records if r.message == "http.request.completed")
    assert record.status_code == 500


@pytest.mark.asyncio
async def test_request_logging_does_not_log_request_body(caplog):
    body = b"private legal query and personal data"

    with caplog.at_level(logging.INFO, logger="supreme_court_rag.http"):
        await invoke(
            middleware_module.RequestLoggingMiddleware(EchoApp()),
            http_scope(),
            request_messages(body),
        )

    assert body.decode() not in caplog.text
    record = next(r for r in caplog.records if r.message == "http.request.completed")
    assert record.path == "/query"
    assert record.method == "POST"
