"""Schema contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from emergency_ai.core.schema import (
    DISCLAIMER,
    EmergencyRequest,
    EmergencyResponse,
    fallback_response,
)


def test_request_strips_situation():
    req = EmergencyRequest(situation="  fire in kitchen  ", city="London")
    assert req.situation == "fire in kitchen"
    assert req.city == "London"


def test_request_min_length():
    with pytest.raises(ValidationError):
        EmergencyRequest(situation="a", city="London")


def test_response_valid():
    r = EmergencyResponse(
        urgency="high",
        time_to_act_seconds=60,
        immediate_actions=["Call 911", "Stay calm"],
        who_to_call={"primary": "911"},
        avoid=["Don't move them"],
        jurisdictional_notes="NY Good Samaritan applies",
        confidence=0.9,
    )
    assert r.disclaimer == DISCLAIMER
    assert r.urgency == "high"


def test_response_rejects_empty_actions():
    with pytest.raises(ValidationError):
        EmergencyResponse(
            urgency="high",
            time_to_act_seconds=60,
            immediate_actions=[],
            who_to_call={"primary": "911"},
            confidence=0.9,
        )


def test_response_rejects_empty_who_to_call():
    with pytest.raises(ValidationError):
        EmergencyResponse(
            urgency="high",
            time_to_act_seconds=60,
            immediate_actions=["Call help"],
            who_to_call={},
            confidence=0.9,
        )


def test_response_confidence_bounds():
    with pytest.raises(ValidationError):
        EmergencyResponse(
            urgency="high",
            time_to_act_seconds=60,
            immediate_actions=["x"],
            who_to_call={"primary": "911"},
            confidence=1.5,
        )


def test_fallback_response_shape():
    r = fallback_response("100")
    assert r.urgency == "high"
    assert "100" in r.immediate_actions[0]
    assert r.who_to_call == {"primary": "100"}
    assert r.confidence == 0.0
