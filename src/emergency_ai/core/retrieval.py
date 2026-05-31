"""Jurisdiction-aware TF-IDF retrieval for RAG prompt injection.

Builds a pure-Python TF-IDF index over per-city law/notes paragraphs
(collections + math only — no sklearn, no numpy).  At query time, returns
the top-k most relevant paragraphs as Snippet objects that can be injected
directly into the system prompt as cached context blocks.

Privacy invariant: this module operates only on pre-authored city body text,
never on raw situation strings.  Situation text passes through transiently
during .search() but is never stored.
"""

from __future__ import annotations

import math
import re
import string
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cities import CityContext

# ---------------------------------------------------------------------------
# Minimal stop-word set (keeps index lean, improves IDF signal)
# ---------------------------------------------------------------------------
_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will", "would",
        "could", "should", "may", "might", "must", "can", "that", "this",
        "these", "those", "it", "its", "as", "if", "not", "no", "nor",
        "so", "yet", "both", "either", "neither", "such", "than", "too",
        "very", "just", "also", "only", "any", "all", "each", "every",
        "other", "same", "more", "most", "much", "many", "some",
        "your", "their", "they", "them", "you", "we", "he", "she", "who",
        "which", "what", "when", "where", "how", "there", "here",
    }
)

_PUNCT_TRANS = str.maketrans("", "", string.punctuation)


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Snippet:
    """A retrieved paragraph with its TF-IDF relevance score."""

    text: str
    score: float
    city_slug: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _tokenise(text: str) -> list[str]:
    """Lower-case, strip punctuation, remove stop words and very short tokens."""
    tokens = text.lower().translate(_PUNCT_TRANS).split()
    return [t for t in tokens if t not in _STOP_WORDS and len(t) > 1]


def _split_paragraphs(body: str) -> list[str]:
    """Split a city body into non-empty paragraphs.

    Paragraphs are separated by one or more blank lines.  Markdown headings
    (##, ###, …) are kept attached to the paragraph that follows them so that
    the context label stays with the content.
    """
    # Collapse CRLF, then split on blank lines
    raw = re.split(r"\n{2,}", body.replace("\r\n", "\n").strip())
    paragraphs: list[str] = []
    pending_heading = ""
    for chunk in raw:
        chunk = chunk.strip()
        if not chunk:
            continue
        # If chunk is purely a markdown heading, buffer it
        if re.match(r"^#{1,6}\s+", chunk) and "\n" not in chunk:
            pending_heading = chunk + "\n"
            continue
        if pending_heading:
            chunk = pending_heading + chunk
            pending_heading = ""
        paragraphs.append(chunk)
    # Flush any trailing heading (edge case)
    if pending_heading:
        paragraphs.append(pending_heading.rstrip())
    return paragraphs


# ---------------------------------------------------------------------------
# JurisdictionIndex
# ---------------------------------------------------------------------------


class JurisdictionIndex:
    """TF-IDF index over jurisdiction (city) body text paragraphs.

    Build once at startup, then call .search() at inference time.

    >>> from emergency_ai.core.cities import load_cities
    >>> cities = load_cities()
    >>> idx = JurisdictionIndex(cities)
    >>> snippets = idx.search("good samaritan law immunity", "new-york", k=3)
    >>> for s in snippets:
    ...     print(s.score, s.text[:60])
    """

    def __init__(self, cities: dict[str, CityContext]) -> None:
        # paragraphs[city_slug] = [paragraph_text, ...]
        self._paragraphs: dict[str, list[str]] = {}

        # tf[city_slug][para_idx] = Counter of term -> raw term freq
        self._tf: dict[str, list[Counter[str]]] = {}

        # df[term] = number of (city, paragraph) documents containing the term
        self._df: Counter[str] = Counter()

        # Total number of paragraph documents across all cities
        self._N: int = 0

        # Precomputed TF-IDF vectors for fast dot-product scoring
        # _tfidf[city_slug][para_idx] = {term: tfidf_weight}
        self._tfidf: dict[str, list[dict[str, float]]] = {}

        self._build(cities)

    # ------------------------------------------------------------------
    # Index construction
    # ------------------------------------------------------------------

    def _build(self, cities: dict[str, CityContext]) -> None:
        """Two-pass index construction: DF pass then TF-IDF weight pass."""
        # --- Pass 1: tokenise paragraphs and accumulate document frequencies ---
        raw_tf: dict[str, list[Counter[str]]] = {}

        for slug, ctx in cities.items():
            paras = _split_paragraphs(ctx.body)
            if not paras:
                continue
            self._paragraphs[slug] = paras
            para_tfs: list[Counter[str]] = []
            for para in paras:
                tokens = _tokenise(para)
                tf = Counter(tokens)
                para_tfs.append(tf)
                for term in tf:
                    self._df[term] += 1
                self._N += 1
            raw_tf[slug] = para_tfs

        # --- Pass 2: compute TF-IDF weights per paragraph ---
        # TF: log-normalised  tf(t,d) = 1 + log(count) if count > 0
        # IDF: smooth IDF     idf(t)  = log((1 + N) / (1 + df(t))) + 1
        for slug, para_tfs in raw_tf.items():
            self._tf[slug] = para_tfs
            tfidf_vecs: list[dict[str, float]] = []
            for tf in para_tfs:
                vec: dict[str, float] = {}
                for term, count in tf.items():
                    tf_val = 1.0 + math.log(count) if count > 0 else 0.0
                    idf_val = math.log((1.0 + self._N) / (1.0 + self._df[term])) + 1.0
                    vec[term] = tf_val * idf_val
                # L2-normalise so cosine similarity reduces to a simple dot product
                norm = math.sqrt(sum(w * w for w in vec.values())) or 1.0
                tfidf_vecs.append({t: w / norm for t, w in vec.items()})
            self._tfidf[slug] = tfidf_vecs

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        city_slug: str,
        k: int = 3,
    ) -> list[Snippet]:
        """Return the top-k most relevant paragraphs for *query* in *city_slug*.

        If the city has no indexed paragraphs (e.g. unknown city), returns [].
        Scores are cosine-similarity via L2-normalised TF-IDF dot product.
        Results are sorted descending by score; ties broken by original order.

        The query string is used only transiently here and is never stored.
        """
        city_slug = city_slug.lower().strip()
        paras = self._paragraphs.get(city_slug)
        tfidf_vecs = self._tfidf.get(city_slug)
        if not paras or tfidf_vecs is None:
            return []

        # Build query vector (same TF-IDF weighting, L2-normalised)
        q_tokens = _tokenise(query)
        if not q_tokens:
            return []

        q_tf = Counter(q_tokens)
        q_vec: dict[str, float] = {}
        for term, count in q_tf.items():
            if term not in self._df:
                # Unknown term: still contributes with max-IDF weight
                idf_val = math.log((1.0 + self._N) / 1.0) + 1.0
            else:
                idf_val = math.log((1.0 + self._N) / (1.0 + self._df[term])) + 1.0
            tf_val = 1.0 + math.log(count) if count > 0 else 0.0
            q_vec[term] = tf_val * idf_val

        q_norm = math.sqrt(sum(w * w for w in q_vec.values())) or 1.0
        q_vec = {t: w / q_norm for t, w in q_vec.items()}

        # Dot-product scores (cosine similarity via normalised vectors)
        scored: list[tuple[float, int]] = []
        for idx, para_vec in enumerate(tfidf_vecs):
            score = sum(q_vec.get(term, 0.0) * weight for term, weight in para_vec.items())
            scored.append((score, idx))

        # Sort descending by score; stable sort preserves original order on tie
        scored.sort(key=lambda x: -x[0])

        results: list[Snippet] = []
        for score, idx in scored[:k]:
            if score <= 0.0:
                break  # No further relevant paragraphs
            results.append(Snippet(text=paras[idx], score=round(score, 6), city_slug=city_slug))

        return results


# ---------------------------------------------------------------------------
# Prompt injection helper
# ---------------------------------------------------------------------------


def build_retrieved_blocks(
    city: CityContext,
    snippets: list[Snippet],
) -> list[dict]:
    """Construct Anthropic-style content blocks from retrieved snippets.

    Each block is a ``{"type": "text", "text": ...}`` dict ready to be
    appended to a system-block list.  The optional ``cache_control`` key is
    intentionally *not* added here because retrieved blocks vary per request
    and should not be cached at the Anthropic prompt-caching layer; only the
    static city block warrants caching (handled in ``prompts.build_system_blocks``).

    Returns an empty list when there are no snippets (caller can extend safely).

    Example usage in the inference path::

        from emergency_ai.core.retrieval import JurisdictionIndex, build_retrieved_blocks
        from emergency_ai.core.prompts import build_system_blocks

        idx = JurisdictionIndex(cities)
        snippets = idx.search(request.situation, city.slug, k=3)
        blocks = build_system_blocks(city) + build_retrieved_blocks(city, snippets)
    """
    if not snippets:
        return []

    lines = [
        f"# RETRIEVED JURISDICTION CONTEXT — {city.display_name}",
        (
            "The following paragraphs were selected from the city knowledge base as "
            "most relevant to this request. Ground your response in this content; "
            "do not invent laws or procedures not present here."
        ),
        "",
    ]
    for i, snippet in enumerate(snippets, start=1):
        lines.append(f"[Excerpt {i} — relevance {snippet.score:.4f}]")
        lines.append(snippet.text)
        lines.append("")

    return [{"type": "text", "text": "\n".join(lines).rstrip()}]
