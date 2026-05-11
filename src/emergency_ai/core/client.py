"""Inference client wrapper: provider abstraction + structured-output streaming.

The provider abstraction lets tests run without an Anthropic key. Production uses
`AnthropicProvider`. Tests use `MockProvider`.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from .cities import CityContext, resolve_city
from .prompts import build_system_blocks
from .schema import EmergencyRequest, EmergencyResponse, fallback_response

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 600
TIMEOUT_S = 5.0


@dataclass
class StreamEvent:
    """One incremental fragment of the response as it materializes."""

    field: str
    value: Any


class Provider(Protocol):
    async def stream_text(
        self, *, system: list[dict], messages: list[dict], max_tokens: int
    ) -> AsyncIterator[str]:
        """Yield raw text deltas from the model."""
        ...


class AnthropicProvider:
    """Real Anthropic SDK provider."""

    def __init__(self, api_key: str | None = None, model: str = MODEL) -> None:
        from anthropic import AsyncAnthropic

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        self._client = AsyncAnthropic(api_key=key)
        self._model = model

    async def stream_text(
        self, *, system: list[dict], messages: list[dict], max_tokens: int
    ) -> AsyncIterator[str]:
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text


class MockProvider:
    """Replays a canned response, byte-stream style. Used in tests and demos."""

    def __init__(self, canned_json: dict[str, Any] | None = None, chunk_size: int = 16) -> None:
        self._canned = canned_json or _DEFAULT_MOCK
        self._chunk_size = chunk_size

    async def stream_text(
        self, *, system: list[dict], messages: list[dict], max_tokens: int
    ) -> AsyncIterator[str]:
        # Capture system for inspection in tests
        self.last_system = system
        self.last_messages = messages
        text = json.dumps(self._canned)
        for i in range(0, len(text), self._chunk_size):
            await asyncio.sleep(0)  # cooperative yield
            yield text[i : i + self._chunk_size]


_DEFAULT_MOCK = {
    "urgency": "critical",
    "time_to_act_seconds": 30,
    "immediate_actions": [
        "Call the primary emergency number now.",
        "Stay on the line and follow operator instructions.",
        "Keep the person still unless they are in immediate danger.",
    ],
    "who_to_call": {"primary": "911"},
    "avoid": ["Don't move them.", "Don't give food or water."],
    "jurisdictional_notes": "",
    "confidence": 0.85,
}


class EmergencyClient:
    """High-level facade. Streams parsed JSON fragments OR returns the full response."""

    def __init__(self, provider: Provider, cities: dict[str, CityContext]) -> None:
        self._provider = provider
        self._cities = cities

    def resolve(self, city_name: str) -> CityContext:
        return resolve_city(city_name, self._cities)

    async def stream(self, req: EmergencyRequest) -> AsyncIterator[StreamEvent]:
        """Yield StreamEvent(field, value) as each top-level JSON key materializes.

        Also yields a final ('__final__', EmergencyResponse) event when the full parse succeeds,
        or ('__error__', message) if it cannot be validated.
        """
        city = self.resolve(req.city)
        system = build_system_blocks(city)
        messages = [
            {
                "role": "user",
                "content": (
                    f"Situation: {req.situation}\n\n"
                    "Respond with a JSON object matching the schema. No prose, no code fences."
                ),
            },
        ]

        buffer = ""
        emitted_keys: set[str] = set()
        start = time.monotonic()
        try:
            async with asyncio.timeout(TIMEOUT_S):
                async for delta in self._provider.stream_text(
                    system=system, messages=messages, max_tokens=MAX_TOKENS
                ):
                    buffer += delta
                    for field, value in _extract_complete_keys(buffer, emitted_keys):
                        emitted_keys.add(field)
                        yield StreamEvent(field=field, value=value)
        except asyncio.TimeoutError:
            yield StreamEvent(field="__error__", value=f"model timed out after {TIMEOUT_S}s")
            yield StreamEvent(field="__final__", value=fallback_response(city.primary_emergency_number))
            return

        # End of stream — try to validate
        final = _finalize(buffer)
        if final is None:
            yield StreamEvent(field="__error__", value="model returned malformed JSON")
            yield StreamEvent(field="__final__", value=fallback_response(city.primary_emergency_number))
            return
        try:
            resp = EmergencyResponse.model_validate(final)
        except Exception as e:  # pydantic ValidationError
            yield StreamEvent(field="__error__", value=f"schema validation failed: {e}")
            yield StreamEvent(field="__final__", value=fallback_response(city.primary_emergency_number))
            return
        elapsed_ms = int((time.monotonic() - start) * 1000)
        yield StreamEvent(field="__latency_ms__", value=elapsed_ms)
        yield StreamEvent(field="__final__", value=resp)

    async def respond(self, req: EmergencyRequest) -> EmergencyResponse:
        """Block until the full response is parsed."""
        final: EmergencyResponse | None = None
        async for ev in self.stream(req):
            if ev.field == "__final__":
                final = ev.value  # type: ignore[assignment]
        if final is None:
            return fallback_response(self.resolve(req.city).primary_emergency_number)
        return final


def _extract_complete_keys(
    buffer: str, already_emitted: set[str]
) -> list[tuple[str, Any]]:
    """Try parsing `buffer` (which may be incomplete JSON). For top-level keys whose
    values are fully parsed, yield (key, value) pairs not yet emitted.

    We use a forgiving approach: progressively close the JSON with `}` and missing
    string/array terminators and attempt `json.loads`. If parse succeeds, diff against
    `already_emitted` and return new keys whose values are stable (i.e., their span
    in the buffer has been *closed*).
    """
    closed = _try_close_json(buffer)
    if closed is None:
        return []
    try:
        parsed = json.loads(closed)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []

    # Only emit keys whose value-span in the raw buffer is *closed* — otherwise
    # the value is still streaming and the parse used our synthetic closer.
    out = []
    for key, value in parsed.items():
        if key in already_emitted:
            continue
        if _key_value_is_closed(buffer, key):
            out.append((key, value))
    return out


def _try_close_json(buffer: str) -> str | None:
    """Add the minimal suffix needed to make `buffer` parse as JSON.

    Handles: unterminated strings, unterminated arrays, missing trailing `}`.
    Returns None if structure is too damaged.
    """
    s = buffer
    # Walk tracking brackets and strings
    in_string = False
    escape = False
    stack = []  # of "{" or "["
    for ch in s:
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if not stack:
                return None
            opener = stack.pop()
            if (opener == "{" and ch != "}") or (opener == "[" and ch != "]"):
                return None

    closed = s
    if in_string:
        closed += '"'
    # Strip a trailing partial-key/value if present: if last meaningful char is `:` or `,`,
    # we cannot safely close. Remove dangling fragments.
    closed = closed.rstrip()
    while closed and closed[-1] in ":,":
        closed = closed[:-1].rstrip()
    # Now close any open arrays/objects
    while stack:
        opener = stack.pop()
        closed += "]" if opener == "[" else "}"
    return closed


def _key_value_is_closed(buffer: str, key: str) -> bool:
    """Heuristic: does `buffer` contain a comma or closing brace after the value for `key`?

    Looks for the `"key"` token, then walks forward through one balanced value,
    and checks whether the next non-whitespace char is `,` or `}`.
    """
    needle = f'"{key}"'
    idx = buffer.find(needle)
    if idx < 0:
        return False
    # find the `:` after the key
    i = idx + len(needle)
    while i < len(buffer) and buffer[i] != ":":
        i += 1
    if i >= len(buffer):
        return False
    i += 1  # skip ':'
    # skip whitespace
    while i < len(buffer) and buffer[i].isspace():
        i += 1
    if i >= len(buffer):
        return False
    end = _scan_value_end(buffer, i)
    if end is None:
        return False
    # any non-whitespace after `end` that is `,` or `}` means value is closed
    j = end
    while j < len(buffer) and buffer[j].isspace():
        j += 1
    if j >= len(buffer):
        return False
    return buffer[j] in ",}"


def _scan_value_end(buffer: str, start: int) -> int | None:
    """Return index one past the last character of the JSON value starting at `start`."""
    if start >= len(buffer):
        return None
    ch = buffer[start]
    if ch == '"':
        i = start + 1
        escape = False
        while i < len(buffer):
            c = buffer[i]
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                return i + 1
            i += 1
        return None
    if ch in "{[":
        depth = 0
        in_string = False
        escape = False
        i = start
        while i < len(buffer):
            c = buffer[i]
            if escape:
                escape = False
            elif in_string:
                if c == "\\":
                    escape = True
                elif c == '"':
                    in_string = False
            elif c == '"':
                in_string = True
            elif c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return None
    # number, true, false, null
    i = start
    while i < len(buffer) and buffer[i] not in ",}] \t\n\r":
        i += 1
    return i


def _finalize(buffer: str) -> dict | None:
    """Final parse attempt at end of stream. Tolerates missing closing brace."""
    closed = _try_close_json(buffer)
    if closed is None:
        return None
    try:
        parsed = json.loads(closed)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
