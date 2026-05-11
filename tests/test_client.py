"""Client tests with MockProvider — no real Anthropic call."""

from __future__ import annotations

import pytest

from emergency_ai.core.cities import load_cities
from emergency_ai.core.client import EmergencyClient, MockProvider
from emergency_ai.core.schema import EmergencyRequest, EmergencyResponse


@pytest.fixture
def cities():
    return load_cities()


@pytest.fixture
def client(cities):
    return EmergencyClient(MockProvider(), cities)


async def test_respond_returns_valid_response(client):
    req = EmergencyRequest(situation="person collapsed, not breathing", city="New York")
    resp = await client.respond(req)
    assert isinstance(resp, EmergencyResponse)
    assert resp.urgency == "critical"
    assert resp.immediate_actions
    assert resp.who_to_call["primary"] == "911"


async def test_stream_emits_field_events(client):
    req = EmergencyRequest(situation="fire in kitchen", city="London")
    events = []
    async for ev in client.stream(req):
        events.append((ev.field, ev.value))
    fields = [e[0] for e in events]
    # Should have at least one field event before __final__
    assert "urgency" in fields
    assert "__final__" in fields
    # __final__ must be the last event
    assert fields[-1] == "__final__"


async def test_stream_includes_cached_system_block(client):
    req = EmergencyRequest(situation="smoke alarm going off", city="Tokyo")
    # consume the stream
    async for _ in client.stream(req):
        pass
    # Inspect what the provider received
    provider = client._provider
    assert hasattr(provider, "last_system")
    assert len(provider.last_system) == 2
    assert provider.last_system[1]["cache_control"] == {"type": "ephemeral"}
    assert "Tokyo" in provider.last_system[1]["text"]


async def test_stream_unknown_city_uses_sentinel(client):
    req = EmergencyRequest(situation="something is wrong", city="Atlantis")
    resp = await client.respond(req)
    assert isinstance(resp, EmergencyResponse)
    # provider still streams the mock; resolver picked _unknown internally
    provider = client._provider
    assert "No city-specific" in provider.last_system[1]["text"]


async def test_mock_with_custom_payload(cities):
    custom = {
        "urgency": "medium",
        "time_to_act_seconds": 600,
        "immediate_actions": ["Move to a safe location."],
        "who_to_call": {"primary": "112"},
        "avoid": [],
        "jurisdictional_notes": "Test note.",
        "confidence": 0.5,
    }
    client = EmergencyClient(MockProvider(canned_json=custom), cities)
    resp = await client.respond(EmergencyRequest(situation="testing", city="London"))
    assert resp.urgency == "medium"
    assert resp.jurisdictional_notes == "Test note."


async def test_malformed_json_falls_back(cities):
    """If the model emits malformed JSON, we get a fallback response, not a crash."""

    class BrokenProvider:
        async def stream_text(self, *, system, messages, max_tokens):
            self.last_system = system
            self.last_messages = messages
            yield "this is not json at all"

    client = EmergencyClient(BrokenProvider(), cities)
    resp = await client.respond(EmergencyRequest(situation="testing fallback", city="London"))
    assert isinstance(resp, EmergencyResponse)
    assert resp.confidence == 0.0  # fallback marker
    assert resp.urgency == "high"
