"""Async Redis client and sliding-window rate limiter.

Falls back gracefully when Redis is unavailable — requests are allowed
through without rate-limiting, and a warning is logged.
"""

from __future__ import annotations

import logging
import os
import time

log = logging.getLogger("emergency_ai.cache")

_RATE_LIMIT_RPM_DEFAULT = 100


class RateLimiter:
    """Sliding-window rate limiter backed by Redis sorted sets.

    Each API key gets a sorted set keyed ``rl:{api_key}``.  Member scores
    are Unix timestamps (seconds).  On each request we:
      1. Remove members older than 60 s.
      2. Count remaining members.
      3. If count >= limit → rate limited.
      4. Otherwise add the current timestamp and set TTL = 60 s.

    Falls back to allowing all requests when Redis is unreachable.
    """

    def __init__(self, redis_url: str | None = None, rpm: int | None = None) -> None:
        """Initialise the limiter.

        Args:
            redis_url: Redis connection URL.  Defaults to REDIS_URL env var.
            rpm: Requests per minute per key.  Defaults to RATE_LIMIT_RPM env
                 var, or 100 if unset.
        """
        self._url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379")
        self._rpm = rpm or int(os.environ.get("RATE_LIMIT_RPM", str(_RATE_LIMIT_RPM_DEFAULT)))
        self._client = None
        self._available = True  # flipped False on first connection error

    async def _get_client(self):
        """Lazy-initialise the async Redis client."""
        if self._client is not None:
            return self._client
        try:
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(self._url, decode_responses=True)
            # Ping to verify connectivity
            await self._client.ping()
            log.info("Redis connected: %s", self._url)
            return self._client
        except Exception as exc:
            log.warning("Redis unavailable (%s) — rate limiting disabled", exc)
            self._available = False
            return None

    async def is_rate_limited(self, api_key: str) -> bool:
        """Return True if *api_key* has exceeded the per-minute request limit.

        Always returns False when Redis is unavailable (fail-open).
        """
        if not self._available:
            return False
        client = await self._get_client()
        if client is None:
            return False
        try:
            key = f"rl:{api_key}"
            now = time.time()
            window_start = now - 60.0

            pipe = client.pipeline()
            pipe.zremrangebyscore(key, "-inf", window_start)
            pipe.zcard(key)
            pipe.zadd(key, {str(now): now})
            pipe.expire(key, 61)
            results = await pipe.execute()

            count_before_add = results[1]
            return int(count_before_add) >= self._rpm
        except Exception as exc:
            log.warning("Rate-limit check failed (%s) — allowing request", exc)
            return False

    async def close(self) -> None:
        """Close the Redis connection."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
