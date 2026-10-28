"""Unit tests for the Redis sliding-window rate limiter.

Run from the repository root (with backend on PYTHONPATH):
    $env:PYTHONPATH = "$PWD\\backend"
    python -m pytest tests\\test_rate_limiter.py --cov=app.rate_limiter --cov-report=term-missing -v

Adjust the import below if your production module has a different filename.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request
from redis.exceptions import RedisError

from app.rate_limit import RedisRateLimiter, enforce_rate_limit


@pytest.fixture
def settings():
    return SimpleNamespace(
        rate_limit_requests=3,
        rate_limit_window_seconds=60,
    )


@pytest.fixture
def redis():
    client = MagicMock()
    client.eval = AsyncMock(return_value=[1, 0])
    client.ping = AsyncMock(return_value=True)
    return client


@pytest.fixture
def limiter(redis, settings):
    return RedisRateLimiter(redis, settings)


@pytest.mark.asyncio
async def test_check_allows_request_and_calls_redis_eval(limiter, redis):
    await limiter.check("user-123")

    redis.eval.assert_awaited_once()
    args = redis.eval.await_args.args
    assert args[0]  # Lua sliding-window script
    assert args[1] == 1
    assert args[2].startswith("api:rate-limit:")
    assert args[3] == 60
    assert args[4] == 3
    assert isinstance(args[5], str)


@pytest.mark.asyncio
async def test_check_hashes_subject_in_redis_key(limiter, redis):
    await limiter.check("sensitive-subject")

    redis_key = redis.eval.await_args.args[2]
    assert "sensitive-subject" not in redis_key
    assert len(redis_key.rsplit(":", 1)[1]) == 64


@pytest.mark.asyncio
async def test_check_uses_custom_limit_window_and_prefix(redis, settings):
    limiter = RedisRateLimiter(
        redis,
        settings,
        limit=7,
        window_seconds=120,
        key_prefix="custom-prefix",
    )

    await limiter.check("alice")

    args = redis.eval.await_args.args
    assert args[2].startswith("custom-prefix:")
    assert args[3] == 120
    assert args[4] == 7


@pytest.mark.asyncio
async def test_check_raises_429_when_limit_exceeded(redis, settings):
    redis.eval.return_value = [0, 17]
    limiter = RedisRateLimiter(redis, settings)

    with pytest.raises(HTTPException) as exc_info:
        await limiter.check("user-123")

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "Rate limit exceeded."
    assert exc_info.value.headers == {"Retry-After": "17"}


@pytest.mark.asyncio
async def test_check_maps_redis_error_to_503(redis, settings):
    redis.eval.side_effect = RedisError("redis down")
    limiter = RedisRateLimiter(redis, settings)

    with pytest.raises(HTTPException) as exc_info:
        await limiter.check("user-123")

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Request protection is temporarily unavailable."
    assert exc_info.value.headers == {"Retry-After": "5"}
    assert isinstance(exc_info.value.__cause__, RedisError)


@pytest.mark.asyncio
async def test_healthcheck_returns_true_when_ping_is_truthy(limiter, redis):
    redis.ping.return_value = True
    assert await limiter.healthcheck() is True
    redis.ping.assert_awaited_once()


@pytest.mark.asyncio
async def test_healthcheck_returns_false_when_ping_is_false(limiter, redis):
    redis.ping.return_value = False
    assert await limiter.healthcheck() is False


@pytest.mark.asyncio
async def test_enforce_rate_limit_uses_configured_limiter():
    limiter = SimpleNamespace(check=AsyncMock())
    request = MagicMock(spec=Request)
    request.app.state = SimpleNamespace(
        rate_limiter=limiter,
        environment="production",
    )
    user = SimpleNamespace(subject="user-456")

    await enforce_rate_limit(request, user)

    limiter.check.assert_awaited_once_with("user-456")


@pytest.mark.asyncio
async def test_enforce_rate_limit_skips_missing_limiter_in_development():
    request = MagicMock(spec=Request)
    request.app.state = SimpleNamespace(environment="development")
    user = SimpleNamespace(subject="user-456")

    await enforce_rate_limit(request, user)


@pytest.mark.asyncio
async def test_enforce_rate_limit_raises_503_when_missing_outside_development():
    request = MagicMock(spec=Request)
    request.app.state = SimpleNamespace(environment="production")
    user = SimpleNamespace(subject="user-456")

    with pytest.raises(HTTPException) as exc_info:
        await enforce_rate_limit(request, user)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Rate limiting is not configured."


@pytest.mark.asyncio
async def test_enforce_rate_limit_propagates_limiter_exception():
    limiter = SimpleNamespace(
        check=AsyncMock(side_effect=HTTPException(status_code=429, detail="Rate limit exceeded."))
    )
    request = MagicMock(spec=Request)
    request.app.state = SimpleNamespace(
        rate_limiter=limiter,
        environment="production",
    )
    user = SimpleNamespace(subject="user-456")

    with pytest.raises(HTTPException) as exc_info:
        await enforce_rate_limit(request, user)

    assert exc_info.value.status_code == 429
    limiter.check.assert_awaited_once_with("user-456")


def test_constructor_uses_settings_when_overrides_are_none(redis, settings):
    limiter = RedisRateLimiter(
        redis,
        settings,
        limit=None,
        window_seconds=None,
    )

    assert limiter._limit == 3
    assert limiter._window == 60
    assert limiter._key_prefix == "api:rate-limit"


def test_constructor_uses_truthy_overrides(redis, settings):
    limiter = RedisRateLimiter(
        redis,
        settings,
        limit=10,
        window_seconds=30,
        key_prefix="test",
    )

    assert limiter._limit == 10
    assert limiter._window == 30
    assert limiter._key_prefix == "test"
