"""RAG retrieval: cosine-similarity search over StatuteChunk via pgvector.

Falls back to an empty list when RAG_ENABLED is not set or the database
is unavailable, so the service degrades gracefully to its built-in city
context.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("emergency_ai.rag.search")


async def retrieve_context(
    session: Optional[AsyncSession],
    query: str,
    jurisdiction: str,
    top_k: int = 5,
) -> list[str]:
    """Return the top-*k* statute chunks most similar to *query*.

    Uses pgvector cosine distance on the 384-dim ``all-MiniLM-L6-v2``
    embeddings stored in ``statute_chunks``.

    Args:
        session:      AsyncSession; if None, returns ``[]``.
        query:        The user's emergency situation string.
        jurisdiction: Jurisdiction slug (e.g. ``"new-york"``).
        top_k:        Maximum number of chunks to return.

    Returns:
        List of chunk body strings (may be empty).
    """
    if os.environ.get("RAG_ENABLED") != "1":
        return []
    if session is None:
        return []
    try:
        from sqlalchemy import select, text as sa_text  # noqa: PLC0415

        from .embed import embed_texts  # noqa: PLC0415
        from ..db.models import StatuteChunk  # noqa: PLC0415

        query_vec = embed_texts([query])[0]
        # pgvector cosine distance operator: <=>
        stmt = (
            select(StatuteChunk.body)
            .where(StatuteChunk.jurisdiction == jurisdiction)
            .order_by(StatuteChunk.embedding.op("<=>")(query_vec))
            .limit(top_k)
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()
        return list(rows)
    except Exception as exc:  # noqa: BLE001
        log.warning("RAG retrieval failed (%s) — returning empty context", exc)
        return []
