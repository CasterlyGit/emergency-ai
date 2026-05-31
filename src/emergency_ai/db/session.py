"""Async SQLAlchemy engine, session factory, and FastAPI dependency.

Falls back gracefully when DATABASE_URL is unset or the database is
unreachable — in that case the service continues without persistence.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Base

log = logging.getLogger("emergency_ai.db")

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _build_engine():
    """Initialise the engine from DATABASE_URL; return None if unavailable."""
    global _engine, _session_factory
    url = os.environ.get("DATABASE_URL")
    if not url:
        log.warning("DATABASE_URL not set — persistence disabled")
        return None
    try:
        _engine = create_async_engine(url, pool_pre_ping=True, echo=False)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
        return _engine
    except Exception as exc:
        log.warning("DB engine init failed (%s) — persistence disabled", exc)
        return None


async def create_all() -> None:
    """Create all tables (idempotent).  Call from app startup."""
    engine = _engine or _build_engine()
    if engine is None:
        return
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("DB tables ready")
    except Exception as exc:
        log.warning("create_all failed (%s) — continuing without persistence", exc)


async def get_session() -> AsyncGenerator[AsyncSession | None, None]:
    """FastAPI dependency: yields an AsyncSession, or None if DB unavailable."""
    factory = _session_factory or (_build_engine() and _session_factory)
    if factory is None:
        yield None
        return
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
