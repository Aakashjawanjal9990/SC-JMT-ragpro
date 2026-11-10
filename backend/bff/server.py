"""IAM-authenticated, same-origin proxy for the private query API."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import google.auth.transport.requests
import google.oauth2.id_token
import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

MAX_BODY_BYTES = 1_048_576
PROXY_TIMEOUT_SECONDS = 65.0
METHODS = ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]
REQUEST_HEADERS = {
    "accept",
    "authorization",
    "content-type",
    "cookie",
    "origin",
    "referer",
    "user-agent",
    "x-csrf-token",
    "x-request-id",
}
RESPONSE_HEADERS = {
    "cache-control",
    "content-type",
    "location",
    "retry-after",
    "set-cookie",
    "www-authenticate",
    "x-request-id",
}


def _backend_url() -> str:
    value = os.environ.get("BACKEND_URL", "").strip().rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or not parsed.hostname
        or not parsed.hostname.endswith(".run.app")
        or "replace-with" in value.lower()
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("BACKEND_URL must be the private backend's HTTPS service origin.")
    return value


BACKEND_URL = _backend_url()
_token_lock = asyncio.Lock()
_cached_token: str | None = None
_cached_token_expiry = 0


def _fetch_identity_token() -> str:
    return google.oauth2.id_token.fetch_id_token(
        google.auth.transport.requests.Request(), BACKEND_URL
    )


def _token_expiry(token: str) -> int:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return int(claims["exp"])
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Google returned an invalid Cloud Run identity token.") from exc


async def _identity_token() -> str:
    global _cached_token, _cached_token_expiry
    if _cached_token and _cached_token_expiry > time.time() + 60:
        return _cached_token
    async with _token_lock:
        if _cached_token and _cached_token_expiry > time.time() + 60:
            return _cached_token
        token = await asyncio.to_thread(_fetch_identity_token)
        expiry = _token_expiry(token)
        if expiry <= time.time() + 60:
            raise RuntimeError("Google returned an expired Cloud Run identity token.")
        _cached_token = token
        _cached_token_expiry = expiry
        return token


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(
        timeout=httpx.Timeout(PROXY_TIMEOUT_SECONDS),
        follow_redirects=False,
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )
    try:
        yield
    finally:
        await app.state.http.aclose()


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


async def _request_body(request: Request) -> bytes:
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) > MAX_BODY_BYTES:
                raise HTTPException(status_code=413, detail="Request body is too large.")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length.") from exc

    parts: list[bytes] = []
    total = 0
    async for part in request.stream():
        total += len(part)
        if total > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Request body is too large.")
        parts.append(part)
    return b"".join(parts)


async def _proxy(request: Request, path: str = "") -> Response:
    try:
        token = await _identity_token()
    except Exception:
        # Do not expose metadata-server or credential details to browser callers.
        return JSONResponse(
            {"detail": "The API connection is temporarily unavailable."},
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )

    suffix = f"/{path}" if path else ""
    url = f"{BACKEND_URL}/api{suffix}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() in REQUEST_HEADERS
    }
    # Cloud Run checks this Google token; the app session remains in Cookie
    # (and any app bearer token remains in Authorization).
    headers["X-Serverless-Authorization"] = f"Bearer {token}"

    try:
        body = await _request_body(request)
        upstream = await request.app.state.http.request(
            request.method,
            url,
            headers=headers,
            content=body,
        )
    except HTTPException:
        raise
    except httpx.TimeoutException:
        return JSONResponse(
            {"detail": "The API request timed out. Please try again."},
            status_code=504,
            headers={"Cache-Control": "no-store"},
        )
    except httpx.HTTPError:
        return JSONResponse(
            {"detail": "The API connection is temporarily unavailable."},
            status_code=502,
            headers={"Cache-Control": "no-store"},
        )

    response = Response(content=upstream.content, status_code=upstream.status_code)
    for name, value in upstream.headers.multi_items():
        if name.lower() in RESPONSE_HEADERS:
            response.headers.append(name, value)
    if "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.api_route("/api", methods=METHODS, include_in_schema=False)
async def proxy_api_root(request: Request) -> Response:
    return await _proxy(request)


@app.api_route("/api/{path:path}", methods=METHODS, include_in_schema=False)
async def proxy_api(request: Request, path: str) -> Response:
    return await _proxy(request, path)
