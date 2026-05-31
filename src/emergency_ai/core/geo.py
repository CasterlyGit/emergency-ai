"""Geospatial utilities for emergency-ai.

Reads city centroids from docs/data/cities.json at import time, then exposes:

    haversine_km(a, b) -> float
        Great-circle distance between two (lat, lon) points in kilometres.

    nearest_city(lat, lon, cities) -> CityContext
        Return the CityContext whose centroid is closest to (lat, lon).
        Falls back to UNKNOWN_CITY_CONTEXT when *cities* is empty.

Privacy note: raw (lat, lon) coordinates are never persisted or logged by
this module.  Only the resolved slug propagates to metrics/store layers.
"""

from __future__ import annotations

import json
import math
from functools import cache
from pathlib import Path
from typing import NamedTuple

from emergency_ai.core.cities import UNKNOWN_CITY_CONTEXT, CityContext

# ---------------------------------------------------------------------------
# City centroid table — loaded once at import from docs/data/cities.json
# ---------------------------------------------------------------------------

class _Centroid(NamedTuple):
    slug: str
    lat: float
    lon: float


def _load_centroids() -> dict[str, _Centroid]:
    """Resolve docs/data/cities.json relative to the package root.

    Walks up from this file's directory until it finds a ``docs/data/cities.json``
    sibling, which works whether the package is installed editable or not.
    Gracefully returns an empty dict if the file cannot be found so that a
    bare ``pip install -e .`` with no data directory still imports cleanly.
    """
    here = Path(__file__).resolve()
    # Walk up: core/ -> emergency_ai/ -> src/ -> repo root
    for ancestor in here.parents:
        candidate = ancestor / "docs" / "data" / "cities.json"
        if candidate.exists():
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
                result: dict[str, _Centroid] = {}
                for city in raw.get("cities", []):
                    slug = str(city["slug"])
                    result[slug] = _Centroid(
                        slug=slug,
                        lat=float(city["lat"]),
                        lon=float(city["lon"]),
                    )
                return result
            except Exception:
                # Malformed JSON — degrade gracefully
                return {}
    return {}


# Module-level centroid table (slug -> _Centroid)
_CENTROIDS: dict[str, _Centroid] = _load_centroids()

# ---------------------------------------------------------------------------
# Core geometry
# ---------------------------------------------------------------------------

_EARTH_RADIUS_KM = 6_371.0


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Return the great-circle distance in kilometres between two points.

    Args:
        a: (lat, lon) in decimal degrees for the first point.
        b: (lat, lon) in decimal degrees for the second point.

    Returns:
        Distance in kilometres (non-negative float).

    Example::

        >>> round(haversine_km((40.7128, -74.006), (51.5074, -0.1278)), 0)
        5570.0
    """
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    sin_dlat = math.sin(dlat / 2)
    sin_dlon = math.sin(dlon / 2)

    h = sin_dlat * sin_dlat + math.cos(lat1) * math.cos(lat2) * sin_dlon * sin_dlon
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(h))


# ---------------------------------------------------------------------------
# Nearest-city resolution
# ---------------------------------------------------------------------------

def nearest_city(
    lat: float,
    lon: float,
    cities: dict[str, CityContext],
) -> CityContext:
    """Return the CityContext whose centroid is closest to *(lat, lon)*.

    The function uses the module-level centroid table loaded from
    ``docs/data/cities.json``.  Only cities whose slug appears in *both* the
    centroid table **and** the supplied *cities* registry are considered, so
    callers can pass a restricted registry (e.g. a subset loaded from markdown
    files) without risking a KeyError.

    Args:
        lat: Latitude in decimal degrees (-90 to +90).
        lon: Longitude in decimal degrees (-180 to +180).
        cities: Slug-keyed registry of loaded CityContext objects, e.g. as
            returned by ``load_cities()``.

    Returns:
        The nearest ``CityContext`` from *cities*, or
        ``UNKNOWN_CITY_CONTEXT`` if *cities* is empty or no slug in *cities*
        has a corresponding centroid entry.
    """
    if not cities:
        return UNKNOWN_CITY_CONTEXT

    point = (lat, lon)
    best_ctx: CityContext | None = None
    best_dist = math.inf

    for slug, ctx in cities.items():
        centroid = _CENTROIDS.get(slug)
        if centroid is None:
            # City exists in registry but has no centroid — skip rather than crash
            continue
        dist = haversine_km(point, (centroid.lat, centroid.lon))
        if dist < best_dist:
            best_dist = dist
            best_ctx = ctx

    return best_ctx if best_ctx is not None else UNKNOWN_CITY_CONTEXT


# ---------------------------------------------------------------------------
# Convenience: centroid lookup
# ---------------------------------------------------------------------------

@cache
def city_centroid(slug: str) -> tuple[float, float] | None:
    """Return (lat, lon) for a city slug, or None if unknown.

    Cached indefinitely — centroids are static at process start.
    """
    c = _CENTROIDS.get(slug)
    return (c.lat, c.lon) if c is not None else None
