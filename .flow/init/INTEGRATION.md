# INTEGRATION — emergency-ai v0.1

## Acceptance criteria verification

| # | AC | Status | Evidence |
|---|---|---|---|
| 1 | Structured response contract | ✅ | `test_schema.py` validates all field shapes; `test_emergency_json` verifies the live wire format |
| 2 | City context grounding | ✅ | `test_stream_includes_cached_system_block` asserts the city body is in the system block; city files contain Good Samaritan, drug law, mental health text |
| 3 | Unknown city handling | ✅ | `test_resolve_unknown_returns_sentinel`, `test_emergency_unknown_city` |
| 4 | Streaming (SSE) | ✅ | `test_emergency_sse` parses SSE events and asserts field-event + final-event delivery |
| 5 | Prompt caching | ✅ | `test_second_block_has_ephemeral_cache_control` |
| 6 | No PII in logs | ✅ | Inspected `server.py` log format — only `request_id, city, urgency, ttft_ms, total_ms, mock`. Situation NOT logged |
| 7 | CLI demo | ✅ | `emergency "..." --city "New York" --mock` runs and prints a rich-formatted panel with TTFT + elapsed |
| 8 | Tests pass without network | ✅ | 39/39 pytests green using `MockProvider` — no API key required |
| 9 | Operability | ✅ | `/health` returns ok + cities_loaded=6; `emergency-server` console script registered |

**Total: 9/9 ACs verified.**

## Latency status

- Mocked path: < 5 ms end-to-end (sanity number, not representative).
- Live path with Anthropic: not measured in this run — the Anthropic API key was not exposed to the build environment. Users running locally with `ANTHROPIC_API_KEY` set will see the real TTFT in the CLI banner.
- The architecture (Haiku 4.5 + prompt caching + streaming + prefill-free JSON) is the latency bet. Real measurements should land in v0.1.1.

## What landed

- Inference service (FastAPI) with JSON + SSE endpoints
- Anthropic + Mock providers
- 6 cities with real, sourced local context (Good Samaritan laws, drug amnesty, mental health, hospital lists)
- Streaming incremental JSON parser
- CLI demo with live rendering
- Full test suite — 39 tests, 0 failures, no network required

## What's deferred

- Mobile shell (long-press SOS trigger surface) — v0.2
- Voice input via Whisper — v0.2 (overlap with the `laptop-dictation` companion project)
- Reverse geocoding `{lat, lon} → city` — v0.2
- More cities — content task, ongoing
- Edge deployment + measured latency dashboard — v0.2

## Known issues

None blocking. The empty-string `jurisdictional_notes` in mock output is intentional — the mock is deliberately bland so tests pin on schema, not content.
