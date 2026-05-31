# emergency-ai

**[Live PWA](https://casterlygit.github.io/emergency-ai/)** — installable, works with zero signal.

> Long-press SOS → jurisdiction-aware action steps in under two seconds — online or completely offline. The same UX runs whether you are on a plane, in a tunnel, or on a live server.

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square)](https://fastapi.tiangolo.com)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square)](https://python.org)
[![Fly.io ready](https://img.shields.io/badge/Fly.io-deploy--ready-8B5CF6?style=flat-square)](infra/fly.toml)
[![MIT License](https://img.shields.io/badge/license-MIT-22C55E?style=flat-square)](LICENSE)

---

## The offline-first story

The PWA ships a full offline inference engine (`docs/js/engine.js`). When network is absent
(or `window.EMERGENCY_API_BASE` is not set), it runs entirely in the browser:

- Classifies free-text situations with a deterministic weighted-keyword triage engine
- Selects from a corpus of 20 structured scenarios (cardiac arrest, choking, stroke, ...)
- Streams fields to the UI with realistic inter-token delays (simulated TTFT 90-260 ms,
  full response under 1.6 s) so the UX is **identical online or off**
- Caches all data JSON via a Service Worker so the app installs and opens without any
  network after first load

When `EMERGENCY_API_BASE` is set and reachable, the engine transparently proxies the same
call to the live FastAPI service over SSE. The response shape is identical — the UI never
knows the difference.

This dual path is the core architectural story. The offline engine is not a stub; it follows
the same triage logic as `src/emergency_ai/core/triage.py`.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Browser / Installed PWA  (docs/)                   │
│                                                     │
│  ┌──────────────┐   ┌───────────────────────────┐  │
│  │  app.js       │   │  engine.js (offline path) │  │
│  │  (controller) │──▶│  classify → scenario →    │  │
│  │               │   │  simulated SSE stream      │  │
│  └──────┬────────┘   └───────────────────────────┘  │
│         │ if EMERGENCY_API_BASE set & reachable      │
│         │ POST /emergency  (SSE)                     │
└─────────┼───────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────┐
│  FastAPI service  (src/emergency_ai/)               │
│                                                     │
│  /emergency ──▶ triage.py (offline classify)        │
│             ──▶ retrieval.py (TF-IDF RAG, law ctx)  │
│             ──▶ cache.py (Redis / in-memory LRU)    │
│             ──▶ Claude Haiku 4.5 (streaming SSE)    │
│             ──▶ store.py (audit log, no raw text)   │
│             ──▶ metrics.py (Prometheus counters)    │
│                                                     │
│  /metrics  ──▶ Prometheus text exposition           │
│  /triage   ──▶ pure-Python classifier, no key       │
│  /cities   ──▶ city index + geo resolve             │
│  /scenarios──▶ scenario catalog                     │
└──────────────────┬──────────────────────────────────┘
                   │
       ┌───────────┴───────────────────┐
       │                               │
  ┌────▼─────┐                  ┌──────▼──────┐
  │  Redis   │                  │  Postgres   │
  │  cache   │                  │  (SQLite or │
  │  (or in- │                  │  in-memory  │
  │  memory) │                  │  fallback)  │
  └──────────┘                  └─────────────┘
```

---

## Stack

| Technology | Where used | In-memory fallback? |
|---|---|---|
| **FastAPI + Uvicorn** | HTTP server, SSE streaming, CORS | — (always required) |
| **Claude Haiku 4.5** | LLM inference, streaming structured output | Offline engine (no key) |
| **Prompt caching** | City law context block; cache_control: ephemeral | — |
| **TF-IDF retrieval** (`core/retrieval.py`) | RAG: top-k statute paragraphs injected per query | Pure-Python, zero deps |
| **Redis** (`core/cache.py`) | Response cache keyed on (city, normalized situation) | Yes — LRU dict |
| **Postgres / asyncpg** (`core/store.py`) | Append-only audit log (request_id, city, urgency, latency) | Yes — SQLite, then in-memory |
| **Prometheus** (`core/metrics.py`) | `/metrics` text exposition; counters + histograms | Yes — pure-Python renderer |
| **Service Worker** (`docs/sw.js`) | Cache-first app shell + data JSON; network-first for API | — (browser) |
| **Pydantic v2** | Request/response schema validation | — |
| **Haversine geo** (`core/geo.py`) | `/geo/resolve` lat/lon → nearest city | — |
| **Python triage engine** (`core/triage.py`) | Weighted-keyword urgency classify; mirrors `engine.js` | — (pure-Python) |
| **Fly.io** (`infra/fly.toml`) | Deploy target; internal :8080, HTTPS, health check | Docker Compose locally |

`pip install -e .` has zero infra dependencies. Redis and Postgres extras are opt-in:

```bash
pip install -e ".[redis,postgres]"
```

---

## Quickstart

### Option A — local Python (zero infra)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

export ANTHROPIC_API_KEY=sk-ant-...

# CLI demo
emergency "person collapsed on the platform, not breathing" --city "New York"

# HTTP server (in-memory cache + SQLite audit log, no Redis/Postgres needed)
emergency-server
# listens on :8080
```

The CLI prints a live latency banner: `TTFT: 312 ms · total: 1.4 s · cache_hit: yes`

### Option B — full stack with Docker Compose (app + Redis + Postgres)

```bash
# copy and fill your key
cp .env.example .env
# edit .env: ANTHROPIC_API_KEY=sk-ant-...

docker compose up
```

Services started: `app` on :8080, `redis:7-alpine`, `postgres:16-alpine`. Health check
at `/health`. Stop with `Ctrl-C`; data persists in named volumes.

### Option C — pure offline (no API key)

Open `https://casterlygit.github.io/emergency-ai/` — or open `docs/index.html` directly
in any modern browser. No server, no key, no network required after first load.

---

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/emergency` | Main inference endpoint — streams SSE fields |
| `POST` | `/triage` | Offline classifier only — no API key needed |
| `POST` | `/geo/resolve` | `{lat, lon}` → nearest city |
| `GET` | `/cities` | Full city index |
| `GET` | `/cities/{slug}` | Single city detail |
| `GET` | `/scenarios` | Scenario catalog |
| `GET` | `/metrics` | Prometheus text exposition |
| `GET` | `/health` | Liveness check |
| `GET` | `/version` | Build version |

CORS is enabled for all origins so the GitHub Pages PWA can call a deployed instance.

---

## Response schema

```json
{
  "urgency": "critical",
  "time_to_act_seconds": 30,
  "immediate_actions": [
    "Tilt head back, check for breathing — 5 seconds",
    "Begin chest compressions: 30 pushes at 100-120 bpm",
    "Have someone call 911 and fetch an AED if nearby"
  ],
  "reasoning": [
    "Airway must be open before compressions can circulate blood",
    "AHA guideline: 100-120 bpm restores ~30% of normal cardiac output",
    "AED within 3-5 minutes triples survival odds"
  ],
  "who_to_call": { "primary": "911", "poison_control": "1-800-222-1222" },
  "avoid": ["Do not stop compressions to check pulse more than every 2 minutes"],
  "jurisdictional_notes": "New York Good Samaritan Law (PHL §3000-a) protects bystanders giving good-faith aid from civil liability.",
  "confidence": 0.94,
  "disclaimer": "Decision support only — not a substitute for professional medical or legal advice.",
  "_meta": { "ttft_ms": 312, "total_ms": 1390, "cache_hit": true, "source": "live", "city_slug": "new-york" }
}
```

The `reasoning` array is the same length as `immediate_actions` — one sentence explaining
the clinical rationale for each step. The UI's "Explain why" toggle reveals it inline.

---

## 35 features

See [FEATURES.md](FEATURES.md) for the full catalog with one-line rationale per feature.

Headline features: offline PWA · voice SOS + TTS · CPR metronome (100-120 bpm) ·
auto-geo jurisdiction · tap-to-dial · adaptive triage questions · instant translation (8 languages) ·
precise location share · strobe SOS beacon · contact auto-alert · siren · medical-ID card ·
guided full-screen mode · scenario quick-grid · honesty layer (confidence + disclaimer).

Depth features: FAST stroke test · tourniquet/bleeding guide · offline urgency classifier ·
jurisdiction law explorer · incident timeline + export · poison lookup · EpiPen/anaphylaxis ·
drowning + recovery position · disaster protocols · explain-why reasoning.

---

## Latency budget

See [bench/results.md](bench/results.md) for full methodology and numbers.

| Phase | Budget | Notes |
|---|---|---|
| Network (browser → server) | < 150 ms | edge deploy |
| Cache lookup + TTFT (cold) | < 650 ms | Haiku 4.5, first city request |
| Cache lookup + TTFT (warm) | < 160 ms | Redis or in-memory LRU hit |
| First action visible | < 800 ms | streaming; `urgency` + action 1 |
| Full structured response | < 2 s | end of SSE stream |
| Offline (no network) | < 300 ms | engine.js, no server hop |

"Budget" = design target. "Mock-measured" = numbers from `bench/bench.py` against a local
mock provider. Live Haiku latency depends on Anthropic's API and your deploy region.

---

## Privacy posture

**The raw situation text is never persisted anywhere.**

- `core/store.py` records only: `{request_id, ts, city, urgency, ttft_ms, total_ms, source, cache_hit}`
- `core/metrics.py` labels only: `{city, urgency, source, cache_hit}`
- Server logs contain no situation text at any log level
- The PWA's medical-ID card and incident log live in `localStorage` only — never transmitted
- Cache keys are derived from a normalized hash of the situation; the plaintext is not stored

This is enforced as a hard rule across every layer. See [ARCHITECTURE.md](ARCHITECTURE.md) §7.

---

## Deploy to Fly.io

```bash
fly auth login
fly apps create emergency-ai          # one-time
fly secrets set ANTHROPIC_API_KEY=sk-ant-...  --config infra/fly.toml
# optional: wire Redis and Postgres Fly addons
fly secrets set REDIS_URL=redis://...         --config infra/fly.toml
fly secrets set DATABASE_URL=postgres://...   --config infra/fly.toml
fly deploy --config infra/fly.toml
```

Config: 512 MB shared VM, internal :8080, HTTPS enforced, health check on `/health`,
auto-stop when idle (zero cost at rest). See [`infra/fly.toml`](infra/fly.toml).

Without Redis/Postgres secrets the service starts cleanly with in-memory fallbacks —
safe for a free-tier demo deploy.

---

## Project layout

```
src/emergency_ai/
├── core/
│   ├── schema.py       # Pydantic models — EmergencyRequest, EmergencyResponse
│   ├── triage.py       # Pure-Python weighted-keyword classifier (mirrors engine.js)
│   ├── retrieval.py    # TF-IDF jurisdiction RAG — JurisdictionIndex.search()
│   ├── cache.py        # ResponseCache — Redis / in-memory LRU
│   ├── store.py        # IncidentStore — Postgres / SQLite / in-memory audit log
│   ├── metrics.py      # Prometheus counters + histograms, pure-Python renderer
│   ├── geo.py          # nearest_city(lat, lon) — haversine
│   ├── scenarios.py    # Scenario loader (docs/data/scenarios.json)
│   ├── report.py       # Markdown incident report renderer
│   ├── cities.py       # City context loader + prompt-cache block builder
│   ├── client.py       # Anthropic client: streaming, caching, parsing
│   └── prompts.py      # System prompt template
├── api/
│   └── server.py       # FastAPI app — all endpoints
├── cli/
│   └── main.py         # `emergency "..." --city "..."` CLI
└── cities/             # Per-city law context (Markdown, hot-reloaded)
    ├── new-york.md, san-francisco.md, london.md, tokyo.md,
    ├── mumbai.md, bangalore.md, delhi.md, los-angeles.md,
    ├── chicago.md, paris.md, berlin.md, sydney.md,
    └── singapore.md, toronto.md   (14 cities total)

docs/                   # GitHub Pages PWA
├── index.html          # App shell (pinned DOM IDs)
├── js/
│   ├── engine.js       # Offline inference engine — EmergencyEngine class
│   ├── app.js          # Controller, state machine, all feature modules
│   └── effects.js      # Visual helpers — token reveal, latency gauge, heat-map
├── data/               # JSON corpus shared with backend
│   ├── scenarios.json  # 20 scenarios (all categories)
│   ├── cities.json     # 14 cities with law + hospital data
│   ├── i18n.json       # 8 languages (en es hi ja fr de zh ar)
│   ├── poison.json     # Substance first-aid reference
│   ├── disasters.json  # 8 disaster protocols
│   └── medical_ref.json# CPR, FAST, tourniquet, EpiPen, Heimlich
├── sw.js               # Service worker — cache-first shell, network-first API
└── manifest.webmanifest# PWA install metadata

infra/
└── fly.toml            # Fly.io deploy config

bench/
├── bench.py            # asyncio load test against mock provider
└── results.md          # Methodology + numbers (mock-measured + live-budget)

tests/                  # pytest — schema, triage, retrieval, cache, store, e2e
.flow/SHOWCASE_SPEC.md  # Build contract (canonical source of truth)
```

---

## Adding a city

Add `src/emergency_ai/cities/<slug>.md` following the existing structure (frontmatter:
`display_name`, `country`, `emergency_numbers`). Then add a corresponding entry to
`docs/data/cities.json`. The service hot-reloads on next request; the PWA picks up the
JSON on next cache invalidation.

---

## Content accuracy note

First-aid guidance follows standard public references (AHA 2020 CPR guidelines: 100-120 bpm,
30:2 ratio; Red Cross Heimlich; FAST stroke mnemonic; recovery position; EpiPen technique).
All responses include a `disclaimer` field rendered prominently. This is decision support,
not a replacement for calling emergency services or trained medical personnel.
