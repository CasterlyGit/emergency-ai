"""
core/store.py — append-only incident audit store.

Stores ONLY: {request_id, ts, city, urgency, ttft_ms, total_ms, source, cache_hit}.
The raw situation text is NEVER stored here — privacy invariant (§7).

Fallback chain (all in one file, zero hard deps):
  1. Postgres via asyncpg  (DATABASE_URL env set)
  2. SQLite file           (SQLITE_PATH env set, default: no SQLite unless explicitly configured)
  3. In-memory deque       (always works, no infra)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)

# Allowed event keys — assert enforced in record()
_ALLOWED_KEYS: frozenset[str] = frozenset(
    {"request_id", "ts", "city", "urgency", "ttft_ms", "total_ms", "source", "cache_hit"}
)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS incidents (
    id          BIGSERIAL PRIMARY KEY,
    request_id  TEXT        NOT NULL,
    ts          TIMESTAMPTZ NOT NULL,
    city        TEXT        NOT NULL,
    urgency     TEXT        NOT NULL,
    ttft_ms     REAL,
    total_ms    REAL,
    source      TEXT,
    cache_hit   BOOLEAN
);
"""

_CREATE_TABLE_SQLITE = """
CREATE TABLE IF NOT EXISTS incidents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  TEXT    NOT NULL,
    ts          TEXT    NOT NULL,
    city        TEXT    NOT NULL,
    urgency     TEXT    NOT NULL,
    ttft_ms     REAL,
    total_ms    REAL,
    source      TEXT,
    cache_hit   INTEGER
);
"""

_INSERT_PG = """
INSERT INTO incidents (request_id, ts, city, urgency, ttft_ms, total_ms, source, cache_hit)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
"""

_INSERT_SQLITE = """
INSERT INTO incidents (request_id, ts, city, urgency, ttft_ms, total_ms, source, cache_hit)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_RECENT_PG = """
SELECT request_id, ts, city, urgency, ttft_ms, total_ms, source, cache_hit
FROM incidents
ORDER BY id DESC
LIMIT $1
"""

_SELECT_RECENT_SQLITE = """
SELECT request_id, ts, city, urgency, ttft_ms, total_ms, source, cache_hit
FROM incidents
ORDER BY id DESC
LIMIT ?
"""


def _validate_event(event: dict) -> None:
    """Assert no 'situation' key and only allowed keys are present."""
    if "situation" in event:
        raise ValueError(
            "Privacy violation: 'situation' key must never be stored in IncidentStore."
        )
    unknown = set(event.keys()) - _ALLOWED_KEYS
    if unknown:
        raise ValueError(f"Unknown event keys (not allowed): {unknown!r}")


def _ensure_ts(event: dict) -> dict:
    """Return a copy of event with 'ts' set to now if missing."""
    if "ts" not in event or not event["ts"]:
        event = dict(event)
        event["ts"] = datetime.now(UTC).isoformat()
    return event


def _row_to_dict(row: Any, use_asyncpg: bool = False) -> dict:
    """Convert a DB row to a plain dict."""
    if use_asyncpg:
        # asyncpg Record supports mapping access
        d = dict(row)
        # asyncpg returns datetime objects for TIMESTAMPTZ
        if "ts" in d and hasattr(d["ts"], "isoformat"):
            d["ts"] = d["ts"].isoformat()
        return d
    else:
        # sqlite3 Row
        return {
            "request_id": row[0],
            "ts": row[1],
            "city": row[2],
            "urgency": row[3],
            "ttft_ms": row[4],
            "total_ms": row[5],
            "source": row[6],
            "cache_hit": bool(row[7]) if row[7] is not None else None,
        }


# ---------------------------------------------------------------------------
# Backend implementations
# ---------------------------------------------------------------------------


class _MemoryBackend:
    """Thread-safe in-memory deque backend. No infra required."""

    def __init__(self, maxlen: int = 10_000) -> None:
        self._store: deque[dict] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    async def record(self, event: dict) -> None:
        with self._lock:
            self._store.append(event)

    async def recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            items = list(self._store)
        # Most-recent first
        return list(reversed(items[-limit:])) if len(items) >= limit else list(reversed(items))


class _SQLiteBackend:
    """SQLite file-backed backend. Uses asyncio executor so it doesn't block the loop."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_sync(self) -> None:
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE_SQLITE)
            conn.commit()

    async def _ensure_init(self) -> None:
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._init_sync)
            self._initialized = True

    def _record_sync(self, event: dict) -> None:
        ts = event.get("ts", datetime.now(UTC).isoformat())
        if hasattr(ts, "isoformat"):
            ts = ts.isoformat()
        cache_hit = event.get("cache_hit")
        if isinstance(cache_hit, bool):
            cache_hit = 1 if cache_hit else 0
        with self._connect() as conn:
            conn.execute(
                _INSERT_SQLITE,
                (
                    event.get("request_id"),
                    ts,
                    event.get("city"),
                    event.get("urgency"),
                    event.get("ttft_ms"),
                    event.get("total_ms"),
                    event.get("source"),
                    cache_hit,
                ),
            )
            conn.commit()

    def _recent_sync(self, limit: int) -> list[dict]:
        with self._connect() as conn:
            cursor = conn.execute(_SELECT_RECENT_SQLITE, (limit,))
            rows = cursor.fetchall()
        return [_row_to_dict(r, use_asyncpg=False) for r in rows]

    async def record(self, event: dict) -> None:
        await self._ensure_init()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._record_sync, event)

    async def recent(self, limit: int = 50) -> list[dict]:
        await self._ensure_init()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._recent_sync, limit)


class _PostgresBackend:
    """asyncpg-backed Postgres backend. Lazily imported."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Any = None
        self._init_lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        # Create lock lazily (must happen inside a running event loop)
        if self._init_lock is None:
            self._init_lock = asyncio.Lock()
        return self._init_lock

    async def _ensure_pool(self) -> Any:
        if self._pool is not None:
            return self._pool
        async with self._get_lock():
            if self._pool is not None:
                return self._pool
            try:
                asyncpg = __import__("asyncpg")  # lazy import
            except ImportError as exc:
                raise RuntimeError(
                    "asyncpg is not installed. Install with: pip install asyncpg"
                ) from exc
            pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
            async with pool.acquire() as conn:
                await conn.execute(_CREATE_TABLE_SQL)
            self._pool = pool
            return self._pool

    async def record(self, event: dict) -> None:
        pool = await self._ensure_pool()
        ts = event.get("ts", datetime.now(UTC).isoformat())
        # asyncpg accepts datetime objects or ISO strings with tz offset
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except ValueError:
                ts = datetime.now(UTC)
        async with pool.acquire() as conn:
            await conn.execute(
                _INSERT_PG,
                event.get("request_id"),
                ts,
                event.get("city"),
                event.get("urgency"),
                event.get("ttft_ms"),
                event.get("total_ms"),
                event.get("source"),
                event.get("cache_hit"),
            )

    async def recent(self, limit: int = 50) -> list[dict]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_RECENT_PG, limit)
        return [_row_to_dict(r, use_asyncpg=True) for r in rows]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class IncidentStore:
    """
    Append-only incident audit store.

    Privacy invariant: only {request_id, ts, city, urgency, ttft_ms, total_ms,
    source, cache_hit} are ever stored. The 'situation' key is explicitly rejected.

    Backend selection (tried in order):
      1. Postgres  — if DATABASE_URL env var is set
      2. SQLite    — if SQLITE_PATH env var is set
      3. In-memory — always available fallback
    """

    def __init__(
        self,
        *,
        database_url: str | None = None,
        sqlite_path: str | None = None,
    ) -> None:
        # Env vars override constructor args (12-factor)
        db_url = database_url or os.environ.get("DATABASE_URL", "")
        sq_path = sqlite_path or os.environ.get("SQLITE_PATH", "")

        if db_url:
            log.info("IncidentStore: using Postgres backend (%s)", db_url.split("@")[-1])
            self._backend: _MemoryBackend | _SQLiteBackend | _PostgresBackend = (
                _PostgresBackend(db_url)
            )
        elif sq_path:
            log.info("IncidentStore: using SQLite backend (%s)", sq_path)
            self._backend = _SQLiteBackend(sq_path)
        else:
            log.info("IncidentStore: using in-memory backend (no DATABASE_URL or SQLITE_PATH)")
            self._backend = _MemoryBackend()

    async def record(self, event: dict) -> None:
        """
        Persist one incident event.

        Raises ValueError if 'situation' key is present or unknown keys are found.
        Gracefully logs and swallows backend errors so the caller (request handler)
        is never blocked by audit failures.
        """
        _validate_event(event)
        event = _ensure_ts(event)
        try:
            await self._backend.record(event)
        except Exception:
            log.exception("IncidentStore.record failed — audit entry dropped (non-fatal)")

    async def recent(self, limit: int = 50) -> list[dict]:
        """
        Return up to `limit` most-recent events, newest first.

        Returns an empty list on backend errors (graceful degradation).
        """
        if limit <= 0:
            return []
        try:
            return await self._backend.recent(limit)
        except Exception:
            log.exception("IncidentStore.recent failed — returning empty list")
            return []
