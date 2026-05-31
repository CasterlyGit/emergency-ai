"""City loader tests."""

from __future__ import annotations

from emergency_ai.core.cities import (
    UNKNOWN_CITY_CONTEXT,
    load_cities,
    resolve_city,
)


def test_bundled_cities_load():
    cities = load_cities()
    # 14 seeded: original 6 + 8 expanded (delhi, los-angeles, chicago, paris, berlin,
    # sydney, singapore, toronto)
    assert len(cities) == 14
    expected_slugs = {
        "new-york", "san-francisco", "london", "tokyo", "mumbai", "bangalore",
        "delhi", "los-angeles", "chicago", "paris", "berlin", "sydney", "singapore", "toronto",
    }
    assert expected_slugs.issubset(set(cities))


def test_city_has_required_fields():
    cities = load_cities()
    ny = cities["new-york"]
    assert ny.display_name == "New York"
    assert ny.country == "USA"
    assert ny.primary_emergency_number == "911"
    assert ny.body  # non-empty


def test_resolve_by_slug():
    cities = load_cities()
    assert resolve_city("new-york", cities).slug == "new-york"


def test_resolve_by_display_name():
    cities = load_cities()
    assert resolve_city("New York", cities).slug == "new-york"
    assert resolve_city("San Francisco", cities).slug == "san-francisco"


def test_resolve_by_alias():
    cities = load_cities()
    assert resolve_city("NYC", cities).slug == "new-york"
    assert resolve_city("Bombay", cities).slug == "mumbai"
    assert resolve_city("Bengaluru", cities).slug == "bangalore"


def test_resolve_case_and_punctuation_insensitive():
    cities = load_cities()
    assert resolve_city("new york city", cities).slug == "new-york"
    assert resolve_city("SAN-FRANCISCO", cities).slug == "san-francisco"


def test_resolve_unknown_returns_sentinel():
    cities = load_cities()
    ctx = resolve_city("Atlantis", cities)
    assert ctx.slug == "_unknown"
    assert ctx is UNKNOWN_CITY_CONTEXT
    assert not ctx.known
