# REQUIREMENTS — emergency-ai v0.1

## Problem

When a user triggers an emergency surface (long-press SOS, hardware button), they need:
- Action steps grounded in their **city's** laws and emergency response system, not a generic chatbot answer.
- The first useful piece of guidance visible in **under one second**.
- A structured response a mobile UI can render predictably under stress.

## In scope (v0.1)

The inference service only. Mobile shell is a separate milestone.

## Acceptance criteria

### AC-1 — Structured response contract
Given a valid `POST /emergency` with `{situation: string, city: string}`, the service returns a JSON object matching the `EmergencyResponse` schema:
- `urgency`: one of `critical | high | medium | low`
- `time_to_act_seconds`: positive integer
- `immediate_actions`: ordered list of ≥ 1 strings (imperative phrasing)
- `who_to_call`: object with at least one key
- `avoid`: list (may be empty)
- `jurisdictional_notes`: string (may be empty for cities without specific notes)
- `confidence`: float in [0, 1]
- `disclaimer`: string (non-empty, identical across responses)

### AC-2 — City context grounding
Given a situation involving local-law-relevant content (Good Samaritan, drug amnesty, mental-health response) and a city with seeded context, the response's `jurisdictional_notes` MUST reference the seeded local context (verified via mocked-client tests asserting context inclusion in the prompt).

### AC-3 — Unknown city handling
Given an unrecognized city, the service responds successfully with a `jurisdictional_notes` field stating the city was unrecognized and advising the user to follow generic guidance. No 404.

### AC-4 — Streaming
A request with `Accept: text/event-stream` returns a Server-Sent Events stream emitting parsed JSON fragments as the model generates them. First event arrives within the TTFT budget on a cache hit.

### AC-5 — Prompt caching
The Anthropic request includes a `cache_control: ephemeral` block on the city context portion of the system prompt. (Verified by inspecting the request payload in tests.)

### AC-6 — No PII in logs
The service log line for each request includes `{request_id, city, urgency, latency_ms, cache_hit}` and **excludes** the raw `situation` string and any client identifier beyond a hashed IP.

### AC-7 — CLI demo
`emergency "<situation>" --city "<city>"` prints a live, formatted response and a latency banner showing TTFT and total time.

### AC-8 — Tests
- Unit tests for schema validation, city loader, prompt assembly.
- Integration test against the FastAPI app using a mocked Anthropic client (no real API key needed).
- All tests pass with `pytest` in CI without network access.

### AC-9 — Operability
- `GET /health` returns `{status: "ok", cities_loaded: <n>}`.
- The server starts with `emergency-server` and serves on port 8080.

## Out of scope (v0.1)

- Mobile shell, voice input, reverse geocoding, full city catalog, user accounts, payment, persistent storage, telemetry, edge deployment.

## Non-functional targets

- **Cached TTFT:** < 200 ms (P50), < 400 ms (P95).
- **Total time:** < 2 s (P50) for typical 200-token responses.
- **No mandatory persistent storage.** The service is stateless.
