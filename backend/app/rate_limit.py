"""Shared Redis sliding-window rate limiting."""

import hashlib
from uuid import uuid4

from fastapi import HTTPException, Request, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.security import AuthenticatedUser
from app.settings import Settings

_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local redis_time = redis.call('TIME')
local now = tonumber(redis_time[1]) + tonumber(redis_time[2]) / 1000000
local window = tonumber(ARGV[1])
local max_requests = tonumber(ARGV[2])
local member = ARGV[3]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count >= max_requests then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry_after = window
  if oldest[2] then retry_after = math.max(1, math.ceil(window - (now - tonumber(oldest[2])))) end
  return {0, retry_after}
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, window + 1)
return {1, 0}
"""


class RedisRateLimiter:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._limit = settings.rate_limit_requests
        self._window = settings.rate_limit_window_seconds

    async def check(self, subject: str) -> None:
        subject_key = hashlib.sha256(subject.encode("utf-8")).hexdigest()
        unique_request = str(uuid4())
        try:
            allowed, retry_after = await self._redis.eval(
                _SLIDING_WINDOW_SCRIPT,
                1,
                f"api:rate-limit:{subject_key}",
                self._window,
                self._limit,
                unique_request,
            )
        except RedisError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Request protection is temporarily unavailable.",
                headers={"Retry-After": "5"},
            ) from exc
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded.",
                headers={"Retry-After": str(retry_after)},
            )

    async def healthcheck(self) -> bool:
        return bool(await self._redis.ping())


async def enforce_rate_limit(request: Request, user: AuthenticatedUser) -> None:
    limiter: RedisRateLimiter | None = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        if request.app.state.environment == "development":
            return
        raise HTTPException(status_code=503, detail="Rate limiting is not configured.")
    await limiter.check(user.subject)
