"""Embedding client using sentence-transformers (local, no API key).

Model: all-MiniLM-L6-v2 (384 dimensions).

The model is only loaded when RAG_ENABLED=1 to avoid the startup cost in
non-RAG deployments.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger("emergency_ai.rag.embed")

_model = None  # lazy singleton


def _get_model():
    """Lazy-load the sentence-transformers model."""
    global _model
    if _model is not None:
        return _model
    if os.environ.get("RAG_ENABLED") != "1":
        raise RuntimeError("RAG_ENABLED is not set — refusing to load embedding model")
    try:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        log.info("Loading embedding model all-MiniLM-L6-v2 …")
        _model = SentenceTransformer("all-MiniLM-L6-v2")
        log.info("Embedding model loaded")
        return _model
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is not installed. "
            "Install with: pip install 'emergency-ai[rag]'"
        ) from exc


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of text strings using all-MiniLM-L6-v2.

    Args:
        texts: Non-empty list of strings to embed.

    Returns:
        List of 384-dimensional float vectors, one per input string.
    """
    model = _get_model()
    embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    return [vec.tolist() for vec in embeddings]
