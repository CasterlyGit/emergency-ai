"""FastAPI auth and rate-limiting dependencies.

Set ``EMERGENCY_AI_NO_AUTH=1`` to bypass API key validation (local dev /
test mode).  This must never be set in production.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..cache.redis_client import RateLimiter
from ..db.models import APIKey
from ..db.session import get_session

log = logging.getLogger("emergency_ai.middleware")

_NO_AUTH = os.environ.get("EMERGENCY_AI_NO_AUTH") == "1"

# Module-level rate limiter singleton (initialised lazily)
_limiter: Optional[RateLimiter] = None


def get_limiter() -> RateLimiter:
    """Return (or lazily create) the module-level RateLimiter."""
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter


async def require_api_key(
    x_api_key: Annotated[Optional[str], Header()] = None,
    session: Optional[AsyncSession] = Depends(get_session),
) -> Optional[str]:
    """FastAPI dependency that validates the ``X-Api-Key`` header.

    Returns the raw API key string on success so downstream handlers can
    use it as the rate-limit key.

    Skips validation entirely when ``EMERGENCY_AI_NO_AUTH=1``.
    """
    if os.environ.get("EMERGENCY_AI_NO_AUTH") == "1":
        return x_api_key  # may be None — that's fine in no-auth mode

    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-Api-Key header")

    if session is None:
        # DB unavailable — log and allow through (graceful degradation)
        log.warning("DB unavailable — skipping API key validation")
        return x_api_key

    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    from sqlalchemy import select  # noqa: PLC0415

    result = await session.execute(
        select(APIKey).where(APIKey.key_hash == key_hash, APIKey.is_active.is_(True))
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    return x_api_key


async def check_rate_limit(
    api_key: Optional[str],
    limiter: RateLimiter,
) -> None:
    """Raise HTTP 429 with a ``Retry-After`` header if the key is over the limit.

    Args:
        api_key: The raw API key string (or None in no-auth mode).
        limiter:  The RateLimiter instance to check against.
    """
    if api_key is None:
        return  # no-auth mode, no key to limit
    limited = await limiter.is_rate_limited(api_key)
    if limited:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": "60"},
        )
