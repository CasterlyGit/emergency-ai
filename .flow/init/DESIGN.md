# DESIGN — emergency-ai v0.1

## Stack

- **Python 3.11+** — `pydantic` v2 for schema, `fastapi` for HTTP, `anthropic` SDK for the model, `click` + `rich` for the CLI.
- **Claude Haiku 4.5** (`claude-haiku-4-5-20251001`) — fastest model in the Claude 4 family, well-suited for low-latency structured output.
- **No database.** Stateless. City context is filesystem-resident markdown loaded at startup.

## Key design decisions

### 1. Prompt caching is the latency story

Each city has 2–4 KB of curated context (emergency numbers, local laws, cultural notes). On every request we send:

```
system = [
  { "type": "text", "text": GENERIC_INSTRUCTIONS },                        # not cached
  { "type": "text", "text": CITY_CONTEXT[city], "cache_control": "..." },  # cached
]
```

Cache key is the exact text of the city block. TTL is 5 minutes (Anthropic ephemeral cache). For a hotline serving steady traffic in one metro, the second-and-later requests within a 5-minute window pay only the input-tokens-uncached cost on the *non-cached* prefix and the system instructions — typically a 3-5x TTFT improvement.

### 2. Structured output via prefill, not tools

We prefill the assistant turn with `{` and instruct the model to emit only valid JSON matching the schema. Tools would require a second round-trip per call; prefill is a single round-trip with the same structural guarantee.

The schema is enforced after generation via `pydantic.TypeAdapter`. Validation failures retry once with a corrective system note.

### 3. Streaming + incremental parsing

The HTTP service exposes both `application/json` (wait for full response) and `text/event-stream` (SSE) endpoints. The SSE endpoint streams **parsed JSON fragments** — not raw tokens — so the client receives `{"urgency": "critical"}` as one event, then `{"immediate_actions": ["..."]}` as another. This requires a small incremental JSON parser that emits keys as their values complete. We use a `partial-json` parsing approach: try `json.loads` on the accumulated buffer after each token; if it parses, diff against the previous parse and emit new top-level keys.

### 4. City context loader

`cities.py` walks `src/emergency_ai/cities/*.md` at startup, parses YAML frontmatter, and stores `{slug: CityContext}`. Lookups are case-insensitive on `slug` and `display_name`. Unknown city → returns a sentinel `UNKNOWN_CITY_CONTEXT` block with generic-only guidance.

### 5. No PII in logs

Logger is a thin wrapper. Only `{request_id, city_slug, urgency, latency_ms, ttft_ms, cache_hit, status_code}` is logged. The `situation` field is *deliberately* not in the log format. Code review must catch any addition.

### 6. Mocked client for tests

`core/client.py` exposes `EmergencyClient` with an `AnthropicProvider` and a `MockProvider`. Tests inject the mock; the mock emits a canned streaming response keyed off the input. CI runs with `MockProvider` — no API key required.

## Component contracts

```
core/schema.py:
  class EmergencyRequest(BaseModel): situation: str, city: str
  class EmergencyResponse(BaseModel): urgency, time_to_act_seconds, immediate_actions, who_to_call, avoid, jurisdictional_notes, confidence, disclaimer

core/cities.py:
  load_cities(dir: Path) -> dict[str, CityContext]
  resolve_city(name: str, registry) -> CityContext | UNKNOWN_CITY_CONTEXT

core/prompts.py:
  SYSTEM_INSTRUCTIONS: str
  build_system_blocks(city: CityContext) -> list[SystemBlock]   # 2nd block has cache_control

core/client.py:
  class Provider(Protocol): async def stream(messages, system) -> AsyncIterator[str]
  class AnthropicProvider(Provider): ...
  class MockProvider(Provider): ...
  class EmergencyClient: __init__(provider), async def respond(req) -> EmergencyResponse, async def stream(req) -> AsyncIterator[dict]

api/server.py:
  POST /emergency  (json | sse based on Accept header)
  GET /health
```

## Failure modes & handling

| Failure | Behavior |
|---|---|
| Model returns malformed JSON | One retry with corrective system note; on second failure, return synthetic minimal response with `urgency=high, immediate_actions=["Call <primary emergency number>"]` + `confidence=0.0` |
| Model timeout (> 5 s) | Cancel stream, return synthetic fallback (same as above) |
| Unknown city | Use `UNKNOWN_CITY_CONTEXT`, set `jurisdictional_notes` to explanatory text |
| API key missing | Service still starts but `/emergency` returns 503 with a clear message |

## Why this doesn't over-engineer

We could add a vector store for arbitrary-city RAG, a tool-calling layer for live shelter lookups, an auth system, a database for audit logs. None of those move the v0.1 needle (latency, jurisdiction, schema discipline). They are explicit v0.2+ candidates.
