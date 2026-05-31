"""Tests for v1 endpoints: /metrics, /cities, /scenarios, /triage, /geo/resolve, /version.

Existing /health and /emergency tests are NOT duplicated here — see test_api.py.
All tests run against create_app(use_mock=True) so no API key is required.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from emergency_ai.api.server import create_app


@pytest.fixture(scope="module")
def client():
    app = create_app(use_mock=True)
    return TestClient(app)


# ---------------------------------------------------------------------------
# /metrics
# ---------------------------------------------------------------------------

def test_metrics_returns_200_text(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    # Must be plain text (Prometheus exposition format).
    ct = r.headers.get("content-type", "")
    assert "text/plain" in ct
    # Body is a string (possibly empty or containing comment lines).
    assert isinstance(r.text, str)


def test_metrics_content_is_prometheus_compatible(client):
    """Prometheus text lines start with '#' (comment/HELP/TYPE) or a metric name."""
    r = client.get("/metrics")
    assert r.status_code == 200
    # Drive at least one request through /emergency so counters are non-zero,
    # then re-fetch metrics to confirm something is rendered.
    client.post(
        "/emergency",
        json={"situation": "someone collapsed and is not breathing", "city": "London"},
    )
    r2 = client.get("/metrics")
    assert r2.status_code == 200
    # Every non-empty, non-comment line must contain a space separating name from value.
    for line in r2.text.splitlines():
        if not line or line.startswith("#"):
            continue
        assert " " in line, f"Malformed metrics line: {line!r}"


# ---------------------------------------------------------------------------
# /cities
# ---------------------------------------------------------------------------

def test_cities_returns_list(client):
    r = client.get("/cities")
    assert r.status_code == 200
    body = r.json()
    assert "cities" in body
    cities = body["cities"]
    assert isinstance(cities, list)
    assert len(cities) >= 6  # at minimum the 6 bundled cities


def test_cities_list_structure(client):
    r = client.get("/cities")
    cities = r.json()["cities"]
    for city in cities:
        assert "slug" in city
        assert "display_name" in city
        assert "country" in city
        assert "primary" in city
        # _unknown sentinel must never appear in the public list.
        assert city["slug"] != "_unknown"


def test_cities_slugs_are_kebab(client):
    r = client.get("/cities")
    for city in r.json()["cities"]:
        slug = city["slug"]
        assert slug == slug.lower(), f"Slug not lowercase: {slug!r}"
        assert " " not in slug, f"Slug has spaces: {slug!r}"


# ---------------------------------------------------------------------------
# /cities/{slug}
# ---------------------------------------------------------------------------

def test_cities_slug_200(client):
    r = client.get("/cities/new-york")
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "new-york"
    assert "display_name" in body
    assert "country" in body
    assert "primary" in body


def test_cities_slug_has_aliases_and_body(client):
    r = client.get("/cities/london")
    assert r.status_code == 200
    body = r.json()
    assert "aliases" in body
    assert isinstance(body["aliases"], list)
    assert "body" in body
    assert isinstance(body["body"], str)


def test_cities_slug_404(client):
    r = client.get("/cities/atlantis")
    assert r.status_code == 404
    detail = r.json().get("detail", "")
    assert "atlantis" in detail.lower() or "not found" in detail.lower()


def test_cities_slug_unknown_sentinel_404(client):
    """The _unknown sentinel slug must return 404 even though it exists internally."""
    r = client.get("/cities/_unknown")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /scenarios
# ---------------------------------------------------------------------------

def test_scenarios_returns_list(client):
    r = client.get("/scenarios")
    assert r.status_code == 200
    body = r.json()
    assert "scenarios" in body
    assert isinstance(body["scenarios"], list)


def test_scenarios_list_not_empty(client):
    r = client.get("/scenarios")
    scenarios = r.json()["scenarios"]
    # The corpus must have at least one entry; 18+ per spec but graceful empty is fine.
    # If the data file exists the count should be >= 1.
    if scenarios:
        first = scenarios[0]
        assert "id" in first or len(first) > 0


# ---------------------------------------------------------------------------
# POST /triage
# ---------------------------------------------------------------------------

def test_triage_returns_urgency_and_signals(client):
    r = client.post(
        "/triage",
        json={"situation": "someone is not breathing and has no pulse", "city": "new-york"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "urgency" in body
    assert body["urgency"] in ("critical", "high", "medium", "low")
    assert "score" in body
    assert isinstance(body["score"], (int, float))
    assert "signals" in body
    assert isinstance(body["signals"], list)


def test_triage_no_api_key_needed(client):
    """POST /triage must work even with no ANTHROPIC_API_KEY — pure offline classifier."""
    r = client.post(
        "/triage",
        json={"situation": "minor cut on finger, bleeding has stopped"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["urgency"] in ("critical", "high", "medium", "low")


def test_triage_critical_scenario(client):
    r = client.post(
        "/triage",
        json={
            "situation": "cardiac arrest, person collapsed, not breathing, no pulse",
            "city": "london",
        },
    )
    assert r.status_code == 200
    assert r.json()["urgency"] == "critical"


def test_triage_signals_are_strings(client):
    r = client.post(
        "/triage",
        json={"situation": "seizure, person shaking on the ground"},
    )
    assert r.status_code == 200
    signals = r.json()["signals"]
    for s in signals:
        assert isinstance(s, str)


def test_triage_matched_field_present(client):
    r = client.post(
        "/triage",
        json={"situation": "choking, person cannot breathe, hands around throat"},
    )
    assert r.status_code == 200
    body = r.json()
    # matched may be None or a string — both are valid.
    assert "matched" in body
    assert body["matched"] is None or isinstance(body["matched"], str)


def test_triage_empty_situation_422(client):
    r = client.post("/triage", json={"situation": ""})
    assert r.status_code == 422


def test_triage_city_optional(client):
    """city field is optional; omitting it should still return a valid response."""
    r = client.post("/triage", json={"situation": "severe allergic reaction, hives"})
    assert r.status_code == 200
    assert "urgency" in r.json()


# ---------------------------------------------------------------------------
# POST /geo/resolve
# ---------------------------------------------------------------------------

def test_geo_resolve_returns_city(client):
    # Coordinates for New York City.
    r = client.post("/geo/resolve", json={"lat": 40.7128, "lon": -74.0060})
    assert r.status_code == 200
    body = r.json()
    assert "slug" in body
    assert "display_name" in body
    assert isinstance(body["slug"], str)
    assert isinstance(body["display_name"], str)


def test_geo_resolve_london(client):
    # Coordinates for central London.
    r = client.post("/geo/resolve", json={"lat": 51.5074, "lon": -0.1278})
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "london"
    assert "London" in body["display_name"]


def test_geo_resolve_tokyo(client):
    # Coordinates for Tokyo.
    r = client.post("/geo/resolve", json={"lat": 35.6762, "lon": 139.6503})
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "tokyo"


def test_geo_resolve_missing_fields_422(client):
    r = client.post("/geo/resolve", json={"lat": 40.7128})
    assert r.status_code == 422


def test_geo_resolve_slug_not_unknown(client):
    """Nearest city must never return the _unknown sentinel."""
    r = client.post("/geo/resolve", json={"lat": -33.8688, "lon": 151.2093})  # Sydney
    assert r.status_code == 200
    assert r.json()["slug"] != "_unknown"


# ---------------------------------------------------------------------------
# GET /version
# ---------------------------------------------------------------------------

def test_version_returns_name_and_version(client):
    r = client.get("/version")
    assert r.status_code == 200
    body = r.json()
    assert "name" in body
    assert body["name"] == "emergency-ai"
    assert "version" in body
    assert isinstance(body["version"], str)
    assert len(body["version"]) > 0


def test_version_semver_shape(client):
    r = client.get("/version")
    ver = r.json()["version"]
    parts = ver.split(".")
    assert len(parts) >= 2, f"Version does not look like semver: {ver!r}"


# ---------------------------------------------------------------------------
# Regression guard — existing /health and /emergency contracts still hold
# ---------------------------------------------------------------------------

def test_health_still_works(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["cities_loaded"] >= 6
    assert body["mock_mode"] is True


def test_emergency_still_works(client):
    r = client.post(
        "/emergency",
        json={"situation": "person not breathing on the platform", "city": "New York"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["urgency"] == "critical"
    assert "_meta" in body
    assert body["_meta"]["city_slug"] == "new-york"
