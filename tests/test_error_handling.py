
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import create_app


def test_validation_error_uses_shared_envelope() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/query",
        json={},
        headers={"X-Request-ID": "validation-test-123"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["request_id"] == "validation-test-123"
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["message"] == "The request is invalid."
    assert body["error"]["retryable"] is False
    assert isinstance(body["error"]["details"], list)


def test_unknown_route_uses_shared_envelope() -> None:
    client = TestClient(create_app())
    response = client.get("/route-that-does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["retryable"] is False
    assert body["request_id"]


def test_unexpected_error_does_not_expose_exception_details() -> None:
    application = create_app()

    @application.get("/test-internal-error")
    async def raise_internal_error() -> None:
        raise RuntimeError("secret database details")

    client = TestClient(application, raise_server_exceptions=False)
    response = client.get("/test-internal-error")

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["retryable"] is False
    assert "secret database details" not in response.text


@pytest.mark.parametrize(
    ("status_code", "expected_code", "expected_retryable"),
    [
        (401, "UNAUTHORIZED", False),
        (403, "FORBIDDEN", False),
        (503, "SERVICE_UNAVAILABLE", True),
    ],
)
def test_http_errors_use_shared_envelope(
    status_code: int,
    expected_code: str,
    expected_retryable: bool,
) -> None:
    application = create_app()

    @application.get("/test-http-error/{status_code}")
    async def raise_http_error(status_code: int) -> None:
        raise HTTPException(
            status_code=status_code,
            detail="Internal details must not be exposed",
        )

    client = TestClient(application)
    response = client.get(f"/test-http-error/{status_code}")

    assert response.status_code == status_code
    body = response.json()
    assert body["error"]["code"] == expected_code
    assert body["error"]["retryable"] is expected_retryable
    assert body["request_id"]
    assert "Internal details must not be exposed" not in response.text


def test_validation_error_does_not_expose_submitted_values() -> None:
    client = TestClient(create_app())
    secret_value = "CONFIDENTIAL-TEST-VALUE-12345"

    response = client.post(
        "/v1/query",
        json={"unexpected_field": secret_value},
    )

    assert response.status_code == 422
    assert secret_value not in response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
