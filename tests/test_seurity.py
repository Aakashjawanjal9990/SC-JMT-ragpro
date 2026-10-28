import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.security import (
    AuthenticatedUser,
    OIDCVerifier,
    require_admin_user,
    require_authenticated_user,
    require_trusted_origin,
)
from app.settings import Settings


def make_request(
    path="/api/query",
    method="GET",
    headers=None,
    cookies=None,
    environment="test",
    auth_service=None,
    oidc_verifier=None,
):
    request = MagicMock()
    request.url.path = path
    request.method = method
    request.headers = headers or {}
    request.cookies = cookies or {}
    request.app.state.settings = Settings(
        environment=environment,
        cors_origins=("https://app.example.com",),
    )
    request.app.state.environment = environment
    request.app.state.auth_service = auth_service
    request.app.state.oidc_verifier = oidc_verifier
    request.state = SimpleNamespace()
    return request


def oidc_settings(**overrides):
    values = {
        "oidc_issuer": "https://issuer.example.com/",
        "oidc_audience": "scj-rag-api",
        "oidc_jwks_url": (
            "https://issuer.example.com/.well-known/jwks.json"
        ),
    }
    values.update(overrides)
    return Settings(**values)


def assert_http_exception(exc_info, status_code, detail=None):
    assert exc_info.value.status_code == status_code

    if detail is not None:
        assert exc_info.value.detail == detail


# ==================================================
# AuthenticatedUser tests
# ==================================================

def test_authenticated_user_defaults():
    user = AuthenticatedUser(subject="user-123")

    assert user.subject == "user-123"
    assert user.user_id is None
    assert user.email is None
    assert user.name is None
    assert user.role == "user"
    assert user.session_id is None
    assert user.token_hash is None


def test_authenticated_user_is_immutable():
    user = AuthenticatedUser(subject="user-123")

    with pytest.raises((AttributeError, TypeError)):
        user.role = "admin"


def test_authenticated_user_custom_fields():
    user = AuthenticatedUser(
        subject="sub-1",
        user_id="user-1",
        email="user@example.com",
        name="Test User",
        role="admin",
        session_id="session-1",
        token_hash="hash-1",
    )

    assert user.user_id == "user-1"
    assert user.email == "user@example.com"
    assert user.name == "Test User"
    assert user.role == "admin"
    assert user.session_id == "session-1"
    assert user.token_hash == "hash-1"


# ==================================================
# OIDC initialization tests
# ==================================================

@pytest.mark.parametrize(
    "settings",
    [
        Settings(),
        Settings(oidc_issuer="https://issuer.example.com"),
        Settings(
            oidc_issuer="https://issuer.example.com",
            oidc_audience="api",
        ),
        Settings(
            oidc_audience="api",
            oidc_jwks_url="https://issuer.example.com/jwks",
        ),
    ],
)
def test_oidc_requires_all_configuration(settings):
    with pytest.raises(ValueError, match="OIDC issuer"):
        OIDCVerifier(settings)


@patch("app.security.PyJWKClient")
def test_oidc_initializes_jwks_client(mock_client):
    verifier = OIDCVerifier(oidc_settings())

    mock_client.assert_called_once_with(
        "https://issuer.example.com/.well-known/jwks.json",
        cache_keys=True,
        timeout=5,
    )

    assert verifier._issuer == "https://issuer.example.com/"
    assert verifier._audience == "scj-rag-api"


# ==================================================
# OIDC health-check tests
# ==================================================

@pytest.mark.asyncio
async def test_healthcheck_returns_true_when_keys_exist():
    verifier = OIDCVerifier(oidc_settings())

    with patch(
        "app.security.asyncio.to_thread",
        new=AsyncMock(return_value=[object()]),
    ):
        assert await verifier.healthcheck() is True


@pytest.mark.asyncio
async def test_healthcheck_returns_false_when_keys_empty():
    verifier = OIDCVerifier(oidc_settings())

    with patch(
        "app.security.asyncio.to_thread",
        new=AsyncMock(return_value=[]),
    ):
        assert await verifier.healthcheck() is False


@pytest.mark.asyncio
async def test_healthcheck_returns_false_on_exception():
    verifier = OIDCVerifier(oidc_settings())

    with patch(
        "app.security.asyncio.to_thread",
        new=AsyncMock(
            side_effect=RuntimeError("JWKS unavailable")
        ),
    ):
        assert await verifier.healthcheck() is False


# ==================================================
# OIDC token verification tests
# ==================================================

@pytest.mark.asyncio
async def test_verify_valid_token_returns_user():
    verifier = OIDCVerifier(oidc_settings())
    signing_key = SimpleNamespace(key="public-key")

    with (
        patch(
            "app.security.asyncio.to_thread",
            new=AsyncMock(return_value=signing_key),
        ),
        patch(
            "app.security.jwt.decode",
            return_value={
                "sub": "user-123",
                "iss": "https://issuer.example.com/",
                "aud": "scj-rag-api",
                "exp": 2_000_000_000,
                "iat": 1_900_000_000,
            },
        ) as mock_decode,
    ):
        user = await verifier.verify("valid-token")

    assert user == AuthenticatedUser(subject="user-123")

    mock_decode.assert_called_once()
    assert mock_decode.call_args.kwargs["algorithms"] == ["RS256"]
    assert mock_decode.call_args.kwargs["audience"] == "scj-rag-api"
    assert (
        mock_decode.call_args.kwargs["issuer"]
        == "https://issuer.example.com/"
    )
    assert mock_decode.call_args.kwargs["leeway"] == 30


@pytest.mark.asyncio
async def test_verify_jwks_failure_returns_503():
    from jwt.exceptions import PyJWKClientError

    verifier = OIDCVerifier(oidc_settings())

    with patch(
        "app.security.asyncio.to_thread",
        new=AsyncMock(
            side_effect=PyJWKClientError("JWKS unavailable")
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await verifier.verify("token")

    assert_http_exception(
        exc,
        503,
        "Authentication keys are temporarily unavailable.",
    )
    assert exc.value.headers["Retry-After"] == "5"


@pytest.mark.asyncio
async def test_verify_invalid_jwt_returns_401():
    from jwt.exceptions import InvalidTokenError

    verifier = OIDCVerifier(oidc_settings())

    with (
        patch(
            "app.security.asyncio.to_thread",
            new=AsyncMock(
                return_value=SimpleNamespace(key="public-key")
            ),
        ),
        patch(
            "app.security.jwt.decode",
            side_effect=InvalidTokenError("invalid token"),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await verifier.verify("invalid-token")

    assert_http_exception(
        exc,
        401,
        "Invalid or expired bearer token.",
    )
    assert exc.value.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [
        {},
        {"sub": ""},
        {"sub": None},
        {"sub": 123},
    ],
)
async def test_verify_rejects_invalid_subject(claims):
    verifier = OIDCVerifier(oidc_settings())

    with (
        patch(
            "app.security.asyncio.to_thread",
            new=AsyncMock(
                return_value=SimpleNamespace(key="public-key")
            ),
        ),
        patch(
            "app.security.jwt.decode",
            return_value=claims,
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await verifier.verify("token")

    assert_http_exception(
        exc,
        401,
        "Invalid or expired bearer token.",
    )


# ==================================================
# Cookie authentication tests
# ==================================================

@pytest.mark.asyncio
async def test_regular_cookie_authentication_succeeds():
    user = AuthenticatedUser(subject="cookie-user")

    service = SimpleNamespace(
        authenticate=AsyncMock(return_value=user),
        verify_csrf_token=MagicMock(),
    )

    request = make_request(
        cookies={"nm_session": "cookie-token"},
        auth_service=service,
    )

    result = await require_authenticated_user(request)

    assert result == user
    service.authenticate.assert_awaited_once_with("cookie-token")
    assert request.state.session_token == "cookie-token"
    assert request.state.user == user


@pytest.mark.asyncio
async def test_admin_path_uses_admin_cookie():
    user = AuthenticatedUser(subject="admin-user", role="admin")

    service = SimpleNamespace(
        authenticate=AsyncMock(return_value=user),
        verify_csrf_token=MagicMock(),
    )

    request = make_request(
        path="/api/admin/users",
        cookies={
            "nm_admin_session": "admin-token",
            "nm_session": "regular-token",
        },
        auth_service=service,
    )

    result = await require_authenticated_user(request)

    assert result == user
    service.authenticate.assert_awaited_once_with("admin-token")


@pytest.mark.asyncio
async def test_cookie_without_auth_service_returns_401():
    request = make_request(cookies={"nm_session": "token"})

    with pytest.raises(HTTPException) as exc:
        await require_authenticated_user(request)

    assert_http_exception(exc, 401, "Invalid or expired session.")


@pytest.mark.asyncio
async def test_invalid_cookie_session_returns_401():
    service = SimpleNamespace(
        authenticate=AsyncMock(return_value=None),
        verify_csrf_token=MagicMock(),
    )

    request = make_request(
        cookies={"nm_session": "invalid-token"},
        auth_service=service,
    )

    with pytest.raises(HTTPException) as exc:
        await require_authenticated_user(request)

    assert_http_exception(exc, 401, "Invalid or expired session.")


@pytest.mark.asyncio
async def test_cookie_auth_service_failure_returns_503():
    service = SimpleNamespace(
        authenticate=AsyncMock(
            side_effect=RuntimeError("database unavailable")
        ),
        verify_csrf_token=MagicMock(),
    )

    request = make_request(
        cookies={"nm_session": "token"},
        auth_service=service,
    )

    with pytest.raises(HTTPException) as exc:
        await require_authenticated_user(request)

    assert_http_exception(
        exc,
        503,
        "Authentication is temporarily unavailable.",
    )


@pytest.mark.asyncio
async def test_post_with_invalid_csrf_returns_403():
    service = SimpleNamespace(
        authenticate=AsyncMock(
            return_value=AuthenticatedUser(subject="user")
        ),
        verify_csrf_token=MagicMock(return_value=False),
    )

    request = make_request(
        method="POST",
        cookies={"nm_session": "token"},
        auth_service=service,
    )
    request.headers = {"x-csrf-token": "bad-token"}

    with pytest.raises(HTTPException) as exc:
        await require_authenticated_user(request)

    assert_http_exception(
        exc,
        403,
        "A valid CSRF token is required.",
    )


@pytest.mark.asyncio
async def test_post_with_valid_csrf_succeeds():
    user = AuthenticatedUser(subject="user")

    service = SimpleNamespace(
        authenticate=AsyncMock(return_value=user),
        verify_csrf_token=MagicMock(return_value=True),
    )

    request = make_request(
        method="POST",
        cookies={"nm_session": "token"},
        auth_service=service,
    )
    request.headers = {"x-csrf-token": "valid-token"}

    assert await require_authenticated_user(request) == user


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
async def test_safe_cookie_methods_skip_csrf(method):
    user = AuthenticatedUser(subject="user")

    service = SimpleNamespace(
        authenticate=AsyncMock(return_value=user),
        verify_csrf_token=MagicMock(),
    )

    request = make_request(
        method=method,
        cookies={"nm_session": "token"},
        auth_service=service,
    )

    assert await require_authenticated_user(request) == user
    service.verify_csrf_token.assert_not_called()


# ==================================================
# Bearer authentication tests
# ==================================================

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authorization",
    ["", "Basic abc", "Bearer", "Bearer   ", "BearerToken"],
)
async def test_missing_or_malformed_authorization_returns_401(
    authorization,
):
    request = make_request(headers={"authorization": authorization})

    with pytest.raises(HTTPException) as exc:
        await require_authenticated_user(request)

    assert_http_exception(
        exc,
        401,
        "An authenticated session is required.",
    )
    assert exc.value.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_bearer_application_session_is_rejected():
    service = SimpleNamespace(
        authenticate=AsyncMock(
            return_value=AuthenticatedUser(subject="app-session")
        ),
    )

    request = make_request(
        headers={"authorization": "Bearer app-token"},
        auth_service=service,
    )

    with pytest.raises(HTTPException) as exc:
        await require_authenticated_user(request)

    assert_http_exception(
        exc,
        401,
        "Application sessions must be sent as secure cookies.",
    )


@pytest.mark.asyncio
async def test_bearer_auth_service_exception_returns_503():
    service = SimpleNamespace(
        authenticate=AsyncMock(
            side_effect=RuntimeError("service down")
        ),
    )

    request = make_request(
        headers={"authorization": "Bearer oidc-token"},
        auth_service=service,
    )

    with pytest.raises(HTTPException) as exc:
        await require_authenticated_user(request)

    assert_http_exception(
        exc,
        503,
        "Authentication is temporarily unavailable.",
    )


@pytest.mark.asyncio
async def test_bearer_without_oidc_verifier_returns_401():
    request = make_request(headers={"authorization": "Bearer token"})

    with pytest.raises(HTTPException) as exc:
        await require_authenticated_user(request)

    assert_http_exception(
        exc,
        401,
        "Invalid or expired bearer token.",
    )


@pytest.mark.asyncio
async def test_bearer_oidc_success_sets_request_user():
    user = AuthenticatedUser(subject="oidc-user")

    verifier = SimpleNamespace(
        verify=AsyncMock(return_value=user),
    )

    request = make_request(
        headers={"authorization": "Bearer   oidc-token   "},
        oidc_verifier=verifier,
    )

    result = await require_authenticated_user(request)

    assert result == user
    verifier.verify.assert_awaited_once_with("oidc-token")
    assert request.state.user == user


# ==================================================
# Trusted-origin tests
# ==================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
async def test_safe_methods_bypass_origin_check(method):
    request = make_request(
        method=method,
        headers={"origin": "https://evil.example"},
    )

    assert await require_trusted_origin(request) is None


@pytest.mark.asyncio
async def test_allowed_origin_passes():
    request = make_request(
        method="POST",
        headers={"origin": "https://app.example.com"},
    )

    assert await require_trusted_origin(request) is None


@pytest.mark.asyncio
async def test_missing_origin_allowed_in_development():
    request = make_request(
        method="POST",
        environment="development",
    )

    assert await require_trusted_origin(request) is None


@pytest.mark.asyncio
async def test_missing_origin_rejected_in_production():
    request = make_request(
        method="POST",
        environment="production",
    )

    with pytest.raises(HTTPException) as exc:
        await require_trusted_origin(request)

    assert_http_exception(
        exc,
        403,
        "Request origin is not allowed.",
    )


@pytest.mark.asyncio
async def test_untrusted_origin_rejected():
    request = make_request(
        method="DELETE",
        headers={"origin": "https://evil.example"},
    )

    with pytest.raises(HTTPException) as exc:
        await require_trusted_origin(request)

    assert_http_exception(
        exc,
        403,
        "Request origin is not allowed.",
    )


# ==================================================
# Administrator authorization tests
# ==================================================

@pytest.mark.asyncio
async def test_admin_user_is_allowed():
    user = AuthenticatedUser(subject="admin", role="admin")
    request = make_request()

    assert await require_admin_user(request, user) == user


@pytest.mark.asyncio
async def test_regular_user_is_rejected_from_admin_access():
    user = AuthenticatedUser(subject="regular", role="user")
    request = make_request()

    with pytest.raises(HTTPException) as exc:
        await require_admin_user(request, user)

    assert_http_exception(
        exc,
        403,
        "Administrator access is required.",
    )