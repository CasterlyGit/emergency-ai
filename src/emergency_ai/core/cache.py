"""
ResponseCache — Redis-backed (redis.asyncio) with in-memory LRU fallback.

Privacy invariant: raw situation text is NEVER stored. Keys are sha256 hashes only.
Stored values must never contain the raw situation string.

Key = sha256(city_slug + '|' + normalize(situation))
normalize = lowercase + collapse whitespace
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

_LRU_MAX = 512
_DEFAULT_TTL = 300  # seconds


def _normalize(situation: str) -> str:
    """Lowercase and collapse all whitespace runs to a single space."""
    return re.sub(r"\s+", " ", situation.strip().lower())


def _make_key(city_slug: str, situation: str) -> str:
    """Return a hex sha256 hash of 'city_slug|normalized_situation'.

    The raw situation text is never recoverable from the key.
    """
    normalized = _normalize(situation)
    raw = f"{city_slug}|{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# In-memory LRU backend
# ---------------------------------------------------------------------------

class _LRUStore:
    """Thread-and-async-safe in-memory LRU with per-entry TTL."""

    def __init__(self, max_size: int = _LRU_MAX) -> None:
        self._max = max_size
        # OrderedDict: key -> (value_json, expires_at)
        self._data: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> dict | None:
        async with self._lock:
            if key not in self._data:
                return None
            value_json, expires_at = self._data[key]
            if time.monotonic() > expires_at:
                del self._data[key]
                return None
            # Move to end (most recently used)
            self._data.move_to_end(key)
            return json.loads(value_json)

    async def set(self, key: str, value: dict, ttl: int = _DEFAULT_TTL) -> None:
        async with self._lock:
            expires_at = time.monotonic() + ttl
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = (json.dumps(value), expires_at)
            # Evict oldest entries until within capacity
            while len(self._data) > self._max:
                self._data.popitem(last=False)


# ---------------------------------------------------------------------------
# Redis backend (lazily imported)
# ---------------------------------------------------------------------------

class _RedisStore:
    """Async Redis store. Constructed with a live redis.asyncio client."""

    _PREFIX = "eai:resp:"

    def __init__(self, client: Any) -> None:
        self._client = client

    async def get(self, key: str) -> dict | None:
        full_key = self._PREFIX + key
        try:
            raw = await self._client.get(full_key)
        except Exception as exc:
            logger.warning("Redis GET failed: %s", exc)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def set(self, key: str, value: dict, ttl: int = _DEFAULT_TTL) -> None:
        full_key = self._PREFIX + key
        try:
            await self._client.set(full_key, json.dumps(value), ex=ttl)
        except Exception as exc:
            logger.warning("Redis SET failed: %s", exc)


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------

class ResponseCache:
    """Two-level cache: Redis (if REDIS_URL set) + in-memory LRU fallback.

    Usage::

        cache = ResponseCache()
        await cache.initialize()          # call once at startup
        hit = await cache.get(city_slug, situation)
        if hit is None:
            result = await compute(...)
            await cache.set(city_slug, situation, result)
    """

    def __init__(self) -> None:
        self._lru: _LRUStore = _LRUStore(max_size=_LRU_MAX)
        self._redis: _RedisStore | None = None
        self._initialized = False

    async def initialize(self) -> None:
        """Attempt to connect to Redis; fall back silently to LRU only."""
        if self._initialized:
            return
        self._initialized = True

        redis_url = os.environ.get("REDIS_URL", "").strip()
        if not redis_url:
            logger.info("REDIS_URL not set — using in-memory LRU cache only")
            return

        try:
            import redis.asyncio as aioredis  # type: ignore[import]

            client = aioredis.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            # Ping to verify the connection is live
            await client.ping()
            self._redis = _RedisStore(client)
            logger.info("ResponseCache connected to Redis at %s", redis_url)
        except ImportError:
            logger.warning(
                "redis package not installed (pip install emergency-ai[redis]); "
                "falling back to in-memory LRU"
            )
        except Exception as exc:
            logger.warning(
                "Redis connection failed (%s); falling back to in-memory LRU", exc
            )

    async def get(self, city_slug: str, situation: str) -> dict | None:
        """Return cached response dict or None.

        Checks Redis first (if available), then LRU. Promotes Redis hits into LRU
        so subsequent misses are served without a network round-trip.
        """
        if not self._initialized:
            await self.initialize()

        key = _make_key(city_slug, situation)

        # 1. Try LRU (hot path — zero network)
        hit = await self._lru.get(key)
        if hit is not None:
            return hit

        # 2. Try Redis
        if self._redis is not None:
            hit = await self._redis.get(key)
            if hit is not None:
                # Warm LRU so next lookup is local
                await self._lru.set(key, hit, ttl=_DEFAULT_TTL)
                return hit

        return None

    async def set(
        self,
        city_slug: str,
        situation: str,
        value: dict,
        ttl: int = _DEFAULT_TTL,
    ) -> None:
        """Store a response dict under the hashed key.

        Writes to both Redis (if available) and LRU simultaneously.
        The raw situation text is never written anywhere — only its hash is used.
        """
        if not self._initialized:
            await self.initialize()

        key = _make_key(city_slug, situation)

        # Fire both writes; gather so Redis failure doesn't block LRU
        tasks: list[asyncio.Task] = []
        loop = asyncio.get_event_loop()

        if self._redis is not None:
            tasks.append(loop.create_task(self._redis.set(key, value, ttl=ttl)))
        tasks.append(loop.create_task(self._lru.set(key, value, ttl=ttl)))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
