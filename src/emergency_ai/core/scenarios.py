"""Scenario corpus loader.

Loads ``docs/data/scenarios.json`` (resolved relative to the repo root when
running from a checkout) with a packaged-data fallback so the module works
correctly whether the package is installed via ``pip install -e .`` or as a
wheel with bundled data.

Public API
----------
list_scenarios() -> list[dict]
    Return all scenarios (shallow copies).

get(scenario_id: str) -> dict | None
    Return the scenario with the given ``id``, or ``None`` if not found.

search(query: str) -> list[dict]
    Case-insensitive keyword match against ``id``, ``title``, ``short``,
    ``keywords``, ``category``, and ``tags``.  Returns all matching scenarios
    ordered by relevance (number of matching terms, descending).

The parsed JSON is cached in module state after the first call to any of the
public functions; subsequent calls are O(1) for ``get`` and O(n) for
``list_scenarios``/``search``.
"""

from __future__ import annotations

import json
import re
from importlib.resources import files as _pkg_files
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    """Return the repository root by walking up from this file."""
    here = Path(__file__).resolve()
    # Walk up until we find pyproject.toml or .git as a sentinel, stopping at
    # filesystem root to avoid infinite loops.
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() or (parent / ".git").exists():
            return parent
    # Fallback: three levels up from src/emergency_ai/core/
    return here.parent.parent.parent.parent


def _locate_json() -> Path | None:
    """Return the path to scenarios.json, or None if not found on disk."""
    candidate = _repo_root() / "docs" / "data" / "scenarios.json"
    if candidate.is_file():
        return candidate
    return None


def _load_raw() -> dict[str, Any]:
    """Load and return the raw parsed JSON, preferring repo-root then package."""
    disk_path = _locate_json()
    if disk_path is not None:
        return json.loads(disk_path.read_text(encoding="utf-8"))

    # Packaged fallback: the file must be included under emergency_ai/data/
    try:
        pkg_data = _pkg_files("emergency_ai") / "data" / "scenarios.json"
        return json.loads(pkg_data.read_text(encoding="utf-8"))
    except (FileNotFoundError, ModuleNotFoundError, TypeError):
        pass

    # Last resort: return an empty corpus so callers never crash.
    return {"version": 1, "scenarios": []}


# ---------------------------------------------------------------------------
# Module-level cache
# ---------------------------------------------------------------------------

_cache: list[dict[str, Any]] | None = None
_index: dict[str, dict[str, Any]] | None = None  # id -> scenario


def _ensure_loaded() -> None:
    global _cache, _index
    if _cache is not None:
        return
    raw = _load_raw()
    _cache = raw.get("scenarios") or []
    _index = {s["id"]: s for s in _cache if "id" in s}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_scenarios() -> list[dict[str, Any]]:
    """Return all scenarios as a list of dicts (shallow copies)."""
    _ensure_loaded()
    assert _cache is not None
    return [dict(s) for s in _cache]


def get(scenario_id: str) -> dict[str, Any] | None:
    """Return the scenario with *scenario_id*, or ``None`` if not found."""
    _ensure_loaded()
    assert _index is not None
    scenario = _index.get(scenario_id)
    return dict(scenario) if scenario is not None else None


def search(query: str) -> list[dict[str, Any]]:
    """Keyword search across scenario fields.

    Splits *query* on whitespace/punctuation into tokens, then matches each
    token case-insensitively against ``id``, ``title``, ``short``,
    ``category``, ``keywords``, and ``tags``.  Results are ranked by the
    number of distinct tokens matched (descending); ties preserve JSON order.

    Returns a list of shallow-copied scenario dicts.
    """
    _ensure_loaded()
    assert _cache is not None

    if not query or not query.strip():
        return list_scenarios()

    # Tokenise: lower-case words only, ignore punctuation.
    tokens = [t for t in re.split(r"[\s,;/\-]+", query.lower()) if t]
    if not tokens:
        return list_scenarios()

    results: list[tuple[int, dict[str, Any]]] = []
    for scenario in _cache:
        # Build a flat searchable string for this scenario.
        searchable_parts: list[str] = [
            scenario.get("id") or "",
            scenario.get("title") or "",
            scenario.get("short") or "",
            scenario.get("category") or "",
            " ".join(scenario.get("keywords") or []),
            " ".join(scenario.get("tags") or []),
        ]
        haystack = " ".join(searchable_parts).lower()

        score = sum(1 for token in tokens if token in haystack)
        if score > 0:
            results.append((score, scenario))

    # Stable sort: highest score first.
    results.sort(key=lambda t: t[0], reverse=True)
    return [dict(s) for _, s in results]
