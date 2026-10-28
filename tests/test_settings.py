"""Pytest unit tests for OIDC and request authentication helpers.

Run from the repository root:
    $env:PYTHONPATH = "$PWD\\backend"
    python -m pytest tests\\test_security.py -v
    python -m pytest tests\\test_security.py --cov=app.security --cov-report=term-missing -v

Assumes the production module is backend/app/security.py.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from jwt.exceptions import PyJWKClientError, PyJWTError

from app.security import (
    AuthenticatedUser,
    OIDCVerifier,
    require_admin_user,
    require_authenticated_user,
    require_trusted_origin,
)


@pytest.fixture
def settings():
    return SimpleNamespace(
        oidc_issuer="https://issuer.example.test/",
        oidc_audience="rag-api",
        oidc_jwks_url="https://issuer.example.test/.well-known/jwks.json",
        session_cookie_name="app_session",
        admin_session_cookie_name="admin_session",
        cors_origins=["https://frontend.example.test"],
    )


@pytest.fixture
def request_factory():
    def make_request(
        *,
        path="/api/query",
        method="GET",
        cookies=None,
        headers=None,
        environment="production",
        settings=None,
        auth_service=None,
        oidc_verifier=None,
    ):
        request = MagicMock()
        request.url.path = path
        request.method = method
        request.cookies = cookies or {}
        request.headers = headers or {}
        request.app.state = SimpleNamespace(
            settings=settings,
            environment=environment,
            auth_service=auth_service,
            oidc_verifier=oidc_verifier,
        )
        request.state = SimpleNamespace()
        return request

    return make_request


def test_authenticated_user_defaults_and_fields():
    user = AuthenticatedUser(subject="subject-1")

    assert user.subject == "subject-1"
    assert user.user_id is None
    assert user.email is None
    assert user.name is None
    assert user.role == "user"
    assert user.session_id is None
    assert user.token_hash is None


def test_authenticated_user_is_frozen():
    user = AuthenticatedUser(subject="subject-1")

    with pytest.raises((AttributeError, TypeError)):
        user.role = "admin"


def test_oidc_verifier_requires_issuer_audience_and_jwks_url():
    incomplete_settings = SimpleNamespace(
        oidc_issuer="",
        oidc_audience="audience",
        oidc_jwks_url="https://issuer.example.test/jwks",
    )

    with pytest.raises(ValueError, match="OIDC issuer, audience, and JWKS URL are required"):
        OIDCVerifier(incomplete_settings)


def test_oidc_verifier_initializes_jwks_client(settings):
    with patch("app.security.PyJWKClient") as client_class:
        verifier = OIDCVerifier(settings)

    client_class.assert_called_once_with(
        settings.oidc_jwks_url,
        cache_keys=True,
        timeout=5,
    )
    assert verifier._issuer == settings.oidc_issuer
    assert verifier._audience == settings.oidc_audience


@pytest.mark.asyncio
async def test_oidc_healthcheck_returns_true_when_signing_keys_exist(settings):
    with patch("app.security.PyJWKClient") as client_class:
        client_class.return_value.get_signing_keys.return_value = ["key"]
        verifier = OIDCVerifier(settings)

    assert await verifier.healthcheck() is True


@pytest.mark.asyncio
async def test_oidc_healthcheck_returns_false_when_no_keys(settings):
    with patch("app.security.PyJWKClient") as client_class:
        client_class.return_value.get_signing_keys.return_value = []
        verifier = OIDCVerifier(settings)

    assert await verifier.healthcheck() is False


@pytest.mark.asyncio
async def test_oidc_healthcheck_returns_false_when_jwks_raises(settings):
    with patch("app.security.PyJWKClient") as client_class:
        client_class.return_value.get_signing_keys.side_effect = RuntimeError("network")
        verifier = OIDCVerifier(settings)

    assert await verifier.healthcheck() is False


@pytest.mark.asyncio
async def test_oidc_verify_returns_authenticated_user(settings):
    signing_key = SimpleNamespace(key="public-key")
    with (
        patch("app.security.PyJWKClient") as client_class,
        patch("app.security.asyncio.to_thread", new_callable=AsyncMock) as to_thread,
        patch("app.security.jwt.decode") as decode,
    ):
        client_class.return_value.get_signing_key_from_jwt.return_value = signing_key
        to_thread.side_effect = [signing_key, None]
        decode.return_value = {
            "sub": "user-123",
            "iss": settings.oidc_issuer,
            "aud": settings.oidc_audience,
            "exp": 9999999999,
            "iat": 100,
        }
        verifier = OIDCVerifier(settings)

        user = await verifier.verify("token-value")

    assert user == AuthenticatedUser(subject="user-123")
    decode.assert_called_once()
    assert decode.call_args.kwargs["algorithms"] == ["RS256"]
    assert decode.call_args.kwargs["audience"] == settings.oidc_audience
    assert decode.call_args.kwargs["issuer"] == settings.oidc_issuer
    assert decode.call_args.kwargs["options"]["require"] == ["exp", "iat", "sub", "iss", "aud"]


@pytest.mark.asyncio
async def test_oidc_verify_maps_jwks_failure_to_503(settings):
    with (
        patch("app.security.PyJWKClient") as client_class,
        patch("app.security.asyncio.to_thread", new_callable=AsyncMock) as to_thread,
    ):
        client_class.return_value.get_signing_key_from_jwt.return_value = None
        to_thread.side_effect = PyJWKClientError("JWKS unavailable")
        verifier = OIDCVerifier(settings)

        with pytest.raises(HTTPException) as exc_info:
            await verifier.verify("token-value")

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Authentication keys are temporarily unavailable."
    assert exc_info.value.headers == {"Retry-After": "5"}


@pytest.mark.asyncio
async def test_oidc_verify_maps_invalid_token_to_401(settings):
    with (
        patch("app.security.PyJWKClient") as client_class,
        patch("app.security.asyncio.to_thread", new_callable=AsyncMock) as to_thread,
        patch("app.security.jwt.decode", side_effect=PyJWTError("bad token")),
    ):
        signing_key = SimpleNamespace(key="public-key")
        client_class.return_value.get_signing_key_from_jwt.return_value = signing_key
        to_thread.side_effect = [signing_key, None]
        verifier = OIDCVerifier(settings)

        with pytest.raises(HTTPException) as exc_info:
            await verifier.verify("token-value")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid or expired bearer token."
    assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}


@pytest.mark.asyncio
async def test_oidc_verify_rejects_empty_subject(settings):
    with (
        patch("app.security.PyJWKClient") as client_class,
        patch("app.security.asyncio.to_thread", new_callable=AsyncMock) as to_thread,
        patch("app.security.jwt.decode", return_value={"sub": ""}),
    ):
        signing_key = SimpleNamespace(key="public-key")
        client_class.return_value.get_signing_key_from_jwt.return_value = signing_key
        to_thread.side_effect = [signing_key, None]
        verifier = OIDCVerifier(settings)

        with pytest.raises(HTTPException) as exc_info:
            await verifier.verify("token-value")

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_oidc_verify_rejects_non_string_subject(settings):
    with (
        patch("app.security.PyJWKClient") as client_class,
        patch("app.security.asyncio.to_thread", new_callable=AsyncMock) as to_thread,
        patch("app.security.jwt.decode", return_value={"sub": 123}),
    ):
        signing_key = SimpleNamespace(key="public-key")
        client_class.return_value.get_signing_key_from_jwt.return_value = signing_key
        to_thread.side_effect = [signing_key, None]
        verifier = OIDCVerifier(settings)

        with pytest.raises(HTTPException) as exc_info:
            await verifier.verify("token-value")

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_cookie_auth_uses_admin_cookie_name_for_admin_path(settings, request_factory):
    user = AuthenticatedUser(subject="admin-subject", role="admin")
    auth_service = SimpleNamespace(
        authenticate=AsyncMock(return_value=user),
        verify_csrf_token=MagicMock(return_value=True),
    )
    request = request_factory(
        path="/api/admin/users",
        method="GET",
        cookies={"admin_session": "cookie-token"},
        settings=settings,
        auth_service=auth_service,
    )

    result = await require_authenticated_user(request)

    assert result is user
    auth_service.authenticate.assert_awaited_once_with("cookie-token")
    assert request.state.session_token == "cookie-token"
    assert request.state.user is user


@pytest.mark.asyncio
async def test_cookie_auth_uses_regular_cookie_name(settings, request_factory):
    user = AuthenticatedUser(subject="user-1")
    auth_service = SimpleNamespace(
        authenticate=AsyncMock(return_value=user),
        verify_csrf_token=MagicMock(return_value=True),
    )
    request = request_factory(
        cookies={"app_session": "cookie-token"},
        settings=settings,
        auth_service=auth_service,
    )

    assert await require_authenticated_user(request) is user


@pytest.mark.asyncio
async def test_cookie_auth_without_auth_service_returns_401(settings, request_factory):
    request = request_factory(
        cookies={"app_session": "cookie-token"},
        settings=settings,
    )

    with pytest.raises(HTTPException) as exc_info:
        await require_authenticated_user(request)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid or expired session."


@pytest.mark.asyncio
async def test_cookie_auth_invalid_session_returns_401(settings, request_factory):
    auth_service = SimpleNamespace(
        authenticate=AsyncMock(return_value=None),
        verify_csrf_token=MagicMock(return_value=True),
    )
    request = request_factory(
        cookies={"app_session": "cookie-token"},
        settings=settings,
        auth_service=auth_service,
    )

    with pytest.raises(HTTPException) as exc_info:
        await require_authenticated_user(request)

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_cookie_auth_service_failure_returns_503(settings, request_factory):
    auth_service = SimpleNamespace(
        authenticate=AsyncMock(side_effect=RuntimeError("database down")),
        verify_csrf_token=MagicMock(return_value=True),
    )
    request = request_factory(
        cookies={"app_session": "cookie-token"},
        settings=settings,
        auth_service=auth_service,
    )

    with pytest.raises(HTTPException) as exc_info:
        await require_authenticated_user(request)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Authentication is temporarily unavailable."


@pytest.mark.asyncio
async def test_cookie_write_without_valid_csrf_returns_403(settings, request_factory):
    user = AuthenticatedUser(subject="user-1")
    auth_service = SimpleNamespace(
        authenticate=AsyncMock(return_value=user),
        verify_csrf_token=MagicMock(return_value=False),
    )
    request = request_factory(
        method="POST",
        cookies={"app_session": "cookie-token"},
        settings=settings,
        auth_service=auth_service,
    )

    with pytest.raises(HTTPException) as exc_info:
        await require_authenticated_user(request)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "A valid CSRF token is required."
    auth_service.verify_csrf_token.assert_called_once_with("cookie-token", None)


@pytest.mark.asyncio
async def test_cookie_write_with_csrf_passes(settings, request_factory):
    user = AuthenticatedUser(subject="user-1")
    auth_service = SimpleNamespace(
        authenticate=AsyncMock(return_value=user),
        verify_csrf_token=MagicMock(return_value=True),
    )
    request = request_factory(
        method="POST",
        cookies={"app_session": "cookie-token"},
        headers={"x-csrf-token": "csrf-value"},
        settings=settings,
        auth_service=auth_service,
    )

    assert await require_authenticated_user(request) is user
    auth_service.verify_csrf_token.assert_called_once_with("cookie-token", "csrf-value")


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
async def test_safe_cookie_methods_do_not_require_csrf(method, settings, request_factory):
    user = AuthenticatedUser(subject="user-1")
    auth_service = SimpleNamespace(
        authenticate=AsyncMock(return_value=user),
        verify_csrf_token=MagicMock(return_value=False),
    )
    request = request_factory(
        method=method,
        cookies={"app_session": "cookie-token"},
        settings=settings,
        auth_service=auth_service,
    )

    assert await require_authenticated_user(request) is user
    auth_service.verify_csrf_token.assert_not_called()


@pytest.mark.asyncio
async def test_missing_or_malformed_authorization_returns_401(settings, request_factory):
    for header in ("", "Basic abc", "Bearer", "Bearer   "):
        request = request_factory(headers={"authorization": header}, settings=settings)
        with pytest.raises(HTTPException) as exc_info:
            await require_authenticated_user(request)
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_bearer_token_accepted_by_app_auth_service_is_rejected_as_cookie_only(
    settings, request_factory
):
    auth_service = SimpleNamespace(
        authenticate=AsyncMock(return_value=AuthenticatedUser(subject="session-user")),
    )
    request = request_factory(
        headers={"authorization": "Bearer session-token"},
        settings=settings,
        auth_service=auth_service,
    )

    with pytest.raises(HTTPException) as exc_info:
        await require_authenticated_user(request)

    assert exc_info.value.status_code == 401
    assert "secure cookies" in exc_info.value.detail


@pytest.mark.asyncio
async def test_bearer_auth_service_failure_returns_503(settings, request_factory):
    auth_service = SimpleNamespace(authenticate=AsyncMock(side_effect=RuntimeError("down")))
    request = request_factory(
        headers={"authorization": "Bearer oidc-token"},
        settings=settings,
        auth_service=auth_service,
    )

    with pytest.raises(HTTPException) as exc_info:
        await require_authenticated_user(request)

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_bearer_without_oidc_verifier_returns_401(settings, request_factory):
    request = request_factory(
        headers={"authorization": "Bearer oidc-token"},
        settings=settings,
    )

    with pytest.raises(HTTPException) as exc_info:
        await require_authenticated_user(request)

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_bearer_uses_oidc_verifier_and_sets_request_user(settings, request_factory):
    user = AuthenticatedUser(subject="oidc-subject")
    verifier = SimpleNamespace(verify=AsyncMock(return_value=user))
    request = request_factory(
        headers={"authorization": "bEaReR   oidc-token  "},
        settings=settings,
        oidc_verifier=verifier,
    )

    result = await require_authenticated_user(request)

    assert result is user
    verifier.verify.assert_awaited_once_with("oidc-token")
    assert request.state.user is user


@pytest.mark.asyncio
async def test_trusted_origin_allows_safe_methods(settings, request_factory):
    for method in ("GET", "HEAD", "OPTIONS"):
        request = request_factory(method=method, settings=settings)
        await require_trusted_origin(request)


@pytest.mark.asyncio
async def test_trusted_origin_allows_configured_origin(settings, request_factory):
    request = request_factory(
        method="POST",
        headers={"origin": settings.cors_origins[0]},
        settings=settings,
    )

    await require_trusted_origin(request)


@pytest.mark.asyncio
async def test_trusted_origin_allows_missing_origin_in_development(settings, request_factory):
    request = request_factory(
        method="POST",
        environment="development",
        settings=settings,
    )

    await require_trusted_origin(request)


@pytest.mark.asyncio
async def test_trusted_origin_rejects_untrusted_origin(settings, request_factory):
    request = request_factory(
        method="POST",
        headers={"origin": "https://evil.example.test"},
        settings=settings,
    )

    with pytest.raises(HTTPException) as exc_info:
        await require_trusted_origin(request)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Request origin is not allowed."


@pytest.mark.asyncio
async def test_trusted_origin_rejects_missing_origin_outside_development(
    settings, request_factory
):
    request = request_factory(method="POST", settings=settings)

    with pytest.raises(HTTPException) as exc_info:
        await require_trusted_origin(request)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_dependency_allows_admin():
    admin = AuthenticatedUser(subject="admin-1", role="admin")
    request = MagicMock()

    assert await require_admin_user(request, admin) is admin


@pytest.mark.asyncio
async def test_admin_dependency_rejects_non_admin():
    user = AuthenticatedUser(subject="user-1", role="user")
    request = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await require_admin_user(request, user)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Administrator access is required."
