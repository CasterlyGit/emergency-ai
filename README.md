# emergency-ai

> Long-press SOS → sub-2-second, jurisdiction-aware action steps. Built around Claude Haiku with prompt-cached city law context and streaming structured output.

**Status:** v0.1 — core service, CLI demo, 6 seeded cities. Mobile shell is the next milestone.

---

## Why this exists

Most "AI for safety" demos are repackaged chatbots. Real emergencies need three things a chatbot fails at:

1. **Speed.** A frozen 4-second model call is useless when someone is choking. Target: first action visible in **< 800 ms TTFT**, full structured response **< 2 s**.
2. **Jurisdiction.** What you should *do* in an emergency depends on where you are. The Good Samaritan law in California differs from New York. Drug amnesty in some jurisdictions changes whether you say *"opioid"* on the phone. The model needs grounded local context, not vibes.
3. **Discipline.** A wall of text wastes critical seconds. The output is a strict schema: urgency, ordered actions, who to call, what to avoid, time-to-act.

This service is the inference layer. Trigger surface (long-press button, Action Button on iOS, Android SOS) is a thin client that posts to `/emergency`.

---

## Architecture

```
┌──────────────────────┐    POST /emergency      ┌─────────────────────┐
│ Mobile / CLI client  │ ──────────────────────▶ │ FastAPI service     │
│  (long-press SOS)    │   {situation, city}     │                     │
└──────────────────────┘                         │   1. Load city ctx  │
        ▲                                        │      (prompt cache) │
        │  streamed JSON                         │   2. Call Haiku 4.5 │
        │  (urgency → actions → calls → avoid)   │      with cache hit │
        └────────────────────────────────────────│   3. Stream parsed  │
                                                 │      schema fields  │
                                                 └─────────────────────┘
```

**Prompt-caching trick.** Each city's law/cultural context is a multi-KB markdown blob. Sent as a `cache_control: ephemeral` block in the system prompt. First request to a city: full read (~600 ms TTFT). Subsequent requests within the 5-minute TTL: cached (~120 ms TTFT). For an emergency hotline serving repeat traffic in a metro, ≥ 90% of queries hit the cache.

**Streaming structured output.** We don't wait for the full JSON. The CLI client renders fields as they arrive — `urgency` first, then the action list line-by-line. The user starts reacting before the model is done generating.

**No tool calls in the hot path.** Tools add round-trips. Everything the model needs is in the cached system prompt. Tools are reserved for a slower v2 "look up specific shelter location" path.

---

## Project layout

```
src/emergency_ai/
├── core/
│   ├── schema.py       # pydantic models — EmergencyRequest, EmergencyResponse
│   ├── cities.py       # city context loader (filesystem → cached prompt blocks)
│   ├── client.py       # Anthropic client wrapper: streaming + caching + parsing
│   └── prompts.py      # system prompt template
├── api/
│   └── server.py       # FastAPI app
├── cli/
│   └── main.py         # `emergency "..." --city "..."` demo client
└── cities/             # bundled city law context (one .md per city)
    ├── new-york.md
    ├── san-francisco.md
    ├── london.md
    ├── tokyo.md
    ├── mumbai.md
    └── bangalore.md

tests/                  # pytest — schema, cities loader, mocked client, e2e (mocked)
.flow/init/             # SDD pipeline artifacts (REQUIREMENTS, DESIGN, TEST_PLAN, INTEGRATION)
```

---

## Quickstart

```bash
# 1. Install (one-time)
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# 3. Try the CLI
emergency "person collapsed on the platform, not breathing" --city "New York"

# 4. Or run the HTTP server
emergency-server   # listens on :8080
curl -N -X POST http://localhost:8080/emergency \
  -H 'content-type: application/json' \
  -d '{"situation":"smoke from kitchen, kids in apartment","city":"London"}'
```

The CLI prints a live latency banner (`TTFT: 312 ms · total: 1.4 s · cache_hit: yes`) so you can see the cache effect.

---

## Response schema

```json
{
  "urgency": "critical",
  "time_to_act_seconds": 30,
  "immediate_actions": [
    "Tilt head back, check breathing for 5 seconds",
    "If not breathing: begin chest compressions, 30 fast pushes",
    "Have someone else call 911 and put phone on speaker"
  ],
  "who_to_call": {
    "primary": "911",
    "poison_control": "1-800-222-1222"
  },
  "avoid": [
    "Don't move them unless they're in immediate danger",
    "Don't give water — they can't swallow safely"
  ],
  "jurisdictional_notes": "New York Good Samaritan Law (PHL §3000-a) protects bystanders giving good-faith aid from civil liability. You will not be charged for low-level drug possession if calling for an overdose (PHL §3000-a).",
  "confidence": 0.92
}
```

---

## Latency budget

| Phase | Target | Notes |
|---|---|---|
| Network (mobile → server) | < 150 ms | edge deploy, persistent connection |
| Cache lookup + LLM TTFT | < 600 ms first req · < 150 ms cached | Haiku 4.5 + prompt caching |
| First action visible to user | < 800 ms | streamed; `urgency` + first action |
| Full structured response | < 2 s | end of stream |

These are budgets, not measured guarantees yet. The CLI's latency banner reports real numbers.

---

## Adding a city

Drop a markdown file into `src/emergency_ai/cities/<slug>.md` following the structure of an existing city. Frontmatter fields: `display_name`, `country`, `emergency_numbers`. The loader hot-reloads on next request.

---

## Security & privacy posture

- **No logs of situations.** The default config logs only `{city, urgency, latency, cache_hit}`. The raw situation string is never persisted server-side.
- **No PII in prompts.** Client should strip names/addresses before sending where reasonable. The model is instructed to ignore identifying details if present.
- **Rate limiting.** v0.1 has a per-IP limiter (in-memory). Production needs a real edge limiter.
- **Disclaimer in every response.** The schema includes a `disclaimer` field rendered prominently in the client. This is decision support, not medical/legal advice.

---

## What's NOT in v0.1

- Mobile shell (iOS/Android long-press trigger). The service is the dependency; the shell ships next.
- Voice input. Whisper integration is wired through the CLI but not the HTTP service.
- More than 6 cities. Coverage expansion is a content task, not an engineering one.
- Caller location lookup from coordinates. Right now you pass `city` as a string. v0.2 will accept `{lat, lon}` and reverse-geocode.

---

## Resume framing

What this project demonstrates:

- **Latency-aware AI engineering** — explicit budgets, prompt caching, streaming, no avoidable round-trips.
- **Production-shaped service** — pydantic schemas, FastAPI, async streaming, tests with a mocked LLM client (no API key required in CI).
- **Domain modeling under constraints** — designing a response schema that is *both* a UI contract for the mobile app *and* a discipline forcing function for the model.
- **Useful, not flashy** — picks a hard problem (sub-2-second jurisdiction-aware action steps) and ships a working slice of it rather than a generic chatbot wrapper.
