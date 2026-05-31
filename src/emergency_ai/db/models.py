"""SQLAlchemy 2.x async ORM models for emergency-ai.

Tables:
    APIKey        — tenant auth credentials
    RequestLog    — per-request telemetry
    StatuteChunk  — pgvector-backed RAG chunks
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared declarative base for all models."""


class APIKey(Base):
    """Tenant API key record.

    The raw key is never stored — only a SHA-256 hex digest.
    """

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    owner_email: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    requests_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    logs: Mapped[list[RequestLog]] = relationship("RequestLog", back_populates="api_key")

    @staticmethod
    def hash_key(raw_key: str) -> str:
        """Return the SHA-256 hex digest of *raw_key*."""
        return hashlib.sha256(raw_key.encode()).hexdigest()


class RequestLog(Base):
    """Per-request telemetry row."""

    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    api_key_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("api_keys.id"), nullable=True, index=True
    )
    city_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    urgency: Mapped[str] = mapped_column(String(32), nullable=False)
    ttft_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    api_key: Mapped[APIKey | None] = relationship("APIKey", back_populates="logs")


class StatuteChunk(Base):
    """A jurisdiction-specific statute chunk with a pgvector embedding."""

    __tablename__ = "statute_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    jurisdiction: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
