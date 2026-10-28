"""OIDC bearer-token verification for the query endpoint."""

import asyncio
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError, PyJWTError

from app.settings import Settings


@dataclass(frozen=True)
class AuthenticatedUser:
    subject: str
    user_id: str | None = None
    email: str | None = None
    name: str | None = None
    role: str = "user"
    session_id: str | None = None
    token_hash: str | None = None


class OIDCVerifier:
    """Verify RS256 access tokens against a configured OIDC issuer and JWKS."""

    def __init__(self, settings: Settings) -> None:
        if not all((settings.oidc_issuer, settings.oidc_audience, settings.oidc_jwks_url)):
            raise ValueError("OIDC issuer, audience, and JWKS URL are required.")
        self._issuer = settings.oidc_issuer
        self._audience = settings.oidc_audience
        self._jwks = PyJWKClient(settings.oidc_jwks_url, cache_keys=True, timeout=5)

    async def healthcheck(self) -> bool:
        try:
            keys = await asyncio.to_thread(self._jwks.get_signing_keys)
        except Exception:
            return False
        return bool(keys)

    async def verify(self, token: str) -> AuthenticatedUser:
        try:
            signing_key = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
                leeway=30,
            )
        except PyJWKClientError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication keys are temporarily unavailable.",
                headers={"Retry-After": "5"},
            ) from exc
        except (PyJWTError, ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired bearer token.",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired bearer token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return AuthenticatedUser(subject=subject)


async def require_authenticated_user(request: Request) -> AuthenticatedUser:
    """Validate an HttpOnly app-session cookie or configured OIDC bearer token."""
    settings = request.app.state.settings
    cookie_name = (
        settings.admin_session_cookie_name
        if request.url.path.startswith("/api/admin/")
        else settings.session_cookie_name
    )
    cookie_token = request.cookies.get(cookie_name)

    if cookie_token:
        auth_service = getattr(request.app.state, "auth_service", None)
        if auth_service is None:
            raise HTTPException(status_code=401, detail="Invalid or expired session.")
        try:
            user = await auth_service.authenticate(cookie_token)
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="Authentication is temporarily unavailable."
            ) from exc
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired session.")
        if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            csrf_token = request.headers.get("x-csrf-token")
            if not auth_service.verify_csrf_token(cookie_token, csrf_token):
                raise HTTPException(status_code=403, detail="A valid CSRF token is required.")
        request.state.session_token = cookie_token
        request.state.user = user
        return user

    authorization = request.headers.get("authorization", "")
    scheme, separator, bearer_token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not bearer_token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="An authenticated session is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    auth_service = getattr(request.app.state, "auth_service", None)
    if auth_service is not None:
        try:
            user = await auth_service.authenticate(bearer_token.strip())
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="Authentication is temporarily unavailable."
            ) from exc
        if user is not None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Application sessions must be sent as secure cookies.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    verifier: OIDCVerifier | None = getattr(request.app.state, "oidc_verifier", None)
    if verifier is None:
        raise HTTPException(status_code=401, detail="Invalid or expired bearer token.")
    user = await verifier.verify(bearer_token.strip())
    request.state.user = user
    return user


async def require_trusted_origin(request: Request) -> None:
    """Reject cross-origin browser writes to the compatibility API."""
    if request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return

    origin = request.headers.get("origin")
    settings = request.app.state.settings
    if origin in settings.cors_origins:
        return
    if origin is None and request.app.state.environment == "development":
        return
    raise HTTPException(status_code=403, detail="Request origin is not allowed.")


async def require_admin_user(
    request: Request,
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> AuthenticatedUser:
    """Require an authenticated application administrator."""
    del request
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator access is required.")
    return user
