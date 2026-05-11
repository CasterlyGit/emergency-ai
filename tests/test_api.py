"""FastAPI app tests using the mocked provider."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from emergency_ai.api.server import create_app


@pytest.fixture
def client():
    app = create_app(use_mock=True)
    return TestClient(app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["cities_loaded"] >= 6
    assert body["mock_mode"] is True


def test_emergency_json(client):
    r = client.post(
        "/emergency",
        json={"situation": "person not breathing on the platform", "city": "New York"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["urgency"] == "critical"
    assert body["who_to_call"]["primary"] == "911"
    assert body["_meta"]["city_slug"] == "new-york"
    assert body["_meta"]["ttft_ms"] is not None


def test_emergency_unknown_city(client):
    r = client.post(
        "/emergency",
        json={"situation": "lost and confused", "city": "Atlantis"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["_meta"]["city_slug"] == "_unknown"


def test_emergency_sse(client):
    r = client.post(
        "/emergency",
        json={"situation": "fire in kitchen", "city": "London"},
        headers={"Accept": "text/event-stream"},
    )
    assert r.status_code == 200
    # Stream body
    text = r.text
    # Parse SSE events
    events = []
    for chunk in text.split("\n\n"):
        chunk = chunk.strip()
        if chunk.startswith("data: "):
            events.append(json.loads(chunk[6:]))
    assert any(e["event"] == "field" for e in events)
    finals = [e for e in events if e["event"] == "final"]
    assert len(finals) == 1
    assert finals[0]["data"]["urgency"] == "critical"


def test_emergency_validation_error(client):
    r = client.post("/emergency", json={"situation": "", "city": "London"})
    assert r.status_code == 422
