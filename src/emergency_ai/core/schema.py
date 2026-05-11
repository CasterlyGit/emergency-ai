"""Schema contracts shared between the API, CLI, and tests."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Urgency = Literal["critical", "high", "medium", "low"]

DISCLAIMER = (
    "Decision support only. If life or safety is at risk, call your local emergency "
    "number immediately. This guidance is not a substitute for trained responders."
)


class EmergencyRequest(BaseModel):
    """Input to the inference service."""

    situation: str = Field(min_length=3, max_length=4000)
    city: str = Field(min_length=1, max_length=120)

    @field_validator("situation")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class EmergencyResponse(BaseModel):
    """Strict response contract. The mobile UI renders fields in display order."""

    urgency: Urgency
    time_to_act_seconds: int = Field(ge=0, le=86400)
    immediate_actions: list[str] = Field(min_length=1, max_length=8)
    who_to_call: dict[str, str] = Field(min_length=1)
    avoid: list[str] = Field(default_factory=list, max_length=8)
    jurisdictional_notes: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    disclaimer: str = DISCLAIMER

    @field_validator("immediate_actions", "avoid")
    @classmethod
    def _trim_each(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if s and s.strip()]


def fallback_response(primary_number: str = "911", note: str = "") -> EmergencyResponse:
    """Minimal safe response when the model fails. Always errs on the side of urgency."""
    return EmergencyResponse(
        urgency="high",
        time_to_act_seconds=60,
        immediate_actions=[
            f"Call {primary_number} immediately and describe the situation.",
            "Stay with the person if it is safe to do so.",
            "Do not move them unless they are in immediate danger.",
        ],
        who_to_call={"primary": primary_number},
        avoid=["Don't hang up until the operator says you can."],
        jurisdictional_notes=(
            note or "AI guidance was unavailable. Use the primary emergency number."
        ),
        confidence=0.0,
    )
