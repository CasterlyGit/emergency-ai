"""Statute ingestion pipeline: reads city .md files, embeds, and upserts.

CLI usage::

    python -m emergency_ai.rag.ingest

Requires:
    RAG_ENABLED=1
    DATABASE_URL=postgresql+asyncpg://...
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

log = logging.getLogger("emergency_ai.rag.ingest")


def _chunk_text(text: str, chunk_size: int = 512, overlap: int = 64) -> list[str]:
    """Split *text* into overlapping chunks of ~*chunk_size* characters.

    Args:
        text:       Source text.
        chunk_size: Target character count per chunk.
        overlap:    Characters of overlap between consecutive chunks.

    Returns:
        List of non-empty chunk strings.
    """
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks


async def ingest_city_statutes(
    session,
    jurisdiction: str,
    chunks: list[str],
    title: str = "statute",
) -> int:
    """Embed *chunks* and upsert them into the StatuteChunk table.

    Args:
        session:      AsyncSession (may be None — skipped gracefully).
        jurisdiction: Jurisdiction code, e.g. ``"us-ny"``.
        chunks:       List of text chunks to embed and store.
        title:        Human-readable title for the batch.

    Returns:
        Number of rows upserted (0 if session is None or on error).
    """
    if session is None or not chunks:
        return 0
    from ..db.models import StatuteChunk
    from .embed import embed_texts

    try:
        embeddings = embed_texts(chunks)
        count = 0
        for body, emb in zip(chunks, embeddings, strict=True):
            row = StatuteChunk(jurisdiction=jurisdiction, title=title, body=body, embedding=emb)
            session.add(row)
            count += 1
        await session.commit()
        log.info("Upserted %d chunks for jurisdiction=%s", count, jurisdiction)
        return count
    except Exception as exc:
        log.warning("ingest_city_statutes failed (%s) — skipping", exc)
        await session.rollback()
        return 0


async def ingest_all_from_cities_dir(session, cities_dir: str | Path | None = None) -> None:
    """Ingest all .md files from *cities_dir* into the StatuteChunk table.

    Args:
        session:    AsyncSession (may be None).
        cities_dir: Path to the directory containing city ``*.md`` files.
                    Defaults to the bundled ``cities/`` directory.
    """
    if cities_dir is None:
        cities_dir = Path(__file__).parent.parent / "cities"
    cities_dir = Path(cities_dir)
    md_files = list(cities_dir.glob("*.md"))
    if not md_files:
        log.warning("No .md files found in %s", cities_dir)
        return
    for md_file in md_files:
        jurisdiction = md_file.stem  # e.g. "new-york" → slug as jurisdiction
        text = md_file.read_text(encoding="utf-8")
        chunks = _chunk_text(text)
        await ingest_city_statutes(session, jurisdiction, chunks, title=md_file.stem)


async def _main() -> None:
    """Entry point when run as ``python -m emergency_ai.rag.ingest``."""
    from ..db.session import create_all, get_session

    logging.basicConfig(level=logging.INFO)
    await create_all()
    async for session in get_session():
        await ingest_all_from_cities_dir(session)
    log.info("Ingestion complete")


if __name__ == "__main__":
    asyncio.run(_main())
