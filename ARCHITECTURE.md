# Architecture — emergency-ai

emergency-ai is built around one design axiom: **it must work when the network does not**.
Every architectural decision flows from that constraint.

---

## 1. Dual-path design

The system has two execution paths for the exact same user action (submit a situation
description). The UX — streaming token reveal, urgency theming, latency HUD — is identical
on both paths. The path taken is transparent to the user.

```
User submits situation text
        │
        ▼
window.EMERGENCY_API_BASE set?
  AND /health reachable within 1.5 s?
        │
   YES  │  NO (offline, airplane mode, slow conn, API down)
        │                    │
        ▼                    ▼
  ┌─────────────┐    ┌───────────────────────────┐
  │  LIVE PATH  │    │      OFFLINE PATH         │
  │  FastAPI    │    │  engine.js deterministic  │
  │  + Claude   │    │  weighted-keyword triage  │
  │  via SSE    │    │  + scenario corpus        │
  └─────────────┘    └───────────────────────────┘
        │                    │
        └─────────┬──────────┘
                  ▼
          onField / onToken callbacks
          (same streaming API, same UX)
```

### Why not just use the API?

In a mass-casualty event the cellular network degrades first. The entire value proposition
collapses if the app requires connectivity. The offline path is not a stub — it runs a
real weighted-keyword triage classifier (`engine.js`) over the same 20-scenario corpus used
in production, produces jurisdictionally-grounded answers, and emits realistic inter-token
delays so the UI behaves identically.

### Why not just run offline?

The deterministic engine is good for known scenarios and clear signal. Free-text edge cases
("my father is acting strange and keeps grabbing his chest but he says he's fine") benefit
from a language model's ability to reason about context, ambiguity, and local jurisdiction.
The live path also sets `cache_control` on the static city block (see §4) — a latency
optimization that has no equivalent in the offline path.

---

## 2. Component diagram

```
╔══════════════════════════════════════════════════════════════════════╗
║  BROWSER (GitHub Pages — docs/)                                      ║
║                                                                      ║
║  ┌─────────────┐  registers  ┌─────────────────────────────────┐   ║
║  │  index.html │ ──────────► │  sw.js (Service Worker)         │   ║
║  │  manifest   │             │  • Install: precaches app shell  │   ║
║  └──────┬──────┘             │    + all data/*.json             │   ║
║         │ imports            │  • Fetch: cache-first for shell  │   ║
║         ▼                    │    network-first for /emergency  │   ║
║  ┌─────────────┐             └─────────────────────────────────┘   ║
║  │   app.js    │  imports                                            ║
║  │ (controller)│ ──────────► engine.js                              ║
║  │             │             │  classify()  nearestCity()           ║
║  │             │ ──────────► effects.js                             ║
║  │             │             │  token reveal, latency HUD           ║
║  └──────┬──────┘             │  cache heatmap, ambient theme        ║
║         │                    └──────────────────────────────────    ║
║  ┌──────▼──────────────────────────────────────────────┐           ║
║  │  engine.js  respond()                                │           ║
║  │                                                      │           ║
║  │  ┌─ offline path ──────────────────────────────┐    │           ║
║  │  │  classify(text) → weighted keyword triage   │    │           ║
║  │  │  _compose(text, city, cls) → Response       │    │           ║
║  │  │  emit field-by-field with sleep delays      │    │           ║
║  │  └────────────────────────────────────────────-┘    │           ║
║  │                                                      │           ║
║  │  ┌─ live path ──────────────────────────────────┐   │           ║
║  │  │  POST /emergency  Accept: text/event-stream  │   │           ║
║  │  │  parse SSE frames → forward onField events   │   │           ║
║  │  └──────────────────────────────────────────────┘   │           ║
║  └──────────────────────────────────────────────────────┘          ║
║                                                                      ║
║  localStorage: medical-ID card, incident log  (never transmitted)   ║
╚═════════════════════════════╤════════════════════════════════════════╝
                              │  HTTPS / SSE (online only)
╔═════════════════════════════▼════════════════════════════════════════╗
║  FastAPI service  (src/emergency_ai/)                                ║
║                                                                      ║
║  api/server.py                                                       ║
║   POST /emergency ──► cache.get() ──► hit? return cached JSON       ║
║                   │                                                  ║
║                   └──► triage.classify()  (urgency pre-check)       ║
║                   └──► retrieval.search() (top-3 jurisdiction para) ║
║                   └──► prompts.build_system_blocks()                 ║
║                              │  cached city block (cache_control)    ║
║                              │  + retrieved snippets block           ║
║                              ▼                                       ║
║                        AnthropicProvider.stream_text()               ║
║                              │  claude-haiku-4-5                     ║
║                              ▼                                       ║
║                        EmergencyClient._parse_stream()               ║
║                              │  incremental JSON key extraction      ║
║                              ▼                                       ║
║                        SSE frames → browser                          ║
║                              │                                       ║
║   (fire-and-forget after response committed)                         ║
║   ├──► cache.set(sha256_key, response_dict)                          ║
║   ├──► store.record({request_id, city, urgency, ttft_ms, …})        ║
║   └──► metrics.inc_request() / observe_ttft()                        ║
║                                                                      ║
║  core/                                                               ║
║  ├── triage.py    pure-Python weighted-keyword classifier            ║
║  ├── retrieval.py pure-Python TF-IDF index (JurisdictionIndex)      ║
║  ├── cache.py     Redis (if REDIS_URL) + LRU fallback               ║
║  ├── store.py     Postgres (DATABASE_URL) / SQLite / in-memory      ║
║  ├── metrics.py   Prometheus text-format, no heavy dep              ║
║  ├── geo.py       haversine nearest-city                            ║
║  ├── prompts.py   system-block builder (cached + uncached blocks)   ║
║  └── client.py    AnthropicProvider / MockProvider / stream parser  ║
╚══════════════════════════════════════════════════════════════════════╝
```

---

## 3. Retrieval-augmented generation (RAG)

### The problem RAG solves

A flat system prompt with all city knowledge for every city is wasteful: most of the text
is irrelevant to the current situation. Injecting only relevant paragraphs into the
**uncached** part of the prompt (a) keeps the cached portion stable across requests and (b)
grounds the model on the specific statutes that bear on the scenario.

### Implementation

`JurisdictionIndex` (core/retrieval.py) builds a two-pass TF-IDF index at startup over
per-city law and notes paragraphs:

1. Pass 1: tokenize all paragraphs, accumulate document frequencies.
2. Pass 2: compute log-normalized TF and smooth IDF; L2-normalize each paragraph vector.

At inference time, `search(situation, city_slug, k=3)` builds a query vector from the same
TF-IDF scheme and returns the top-k paragraphs by cosine similarity (dot product over
L2-normalized vectors). These are injected into the prompt as a separate non-cached block.

No numpy, no sklearn. Pure `math` + `collections`. The index is 100% offline-buildable and
adds zero infra dependencies.

---

## 4. Prompt-cache latency trick

The Anthropic API supports `cache_control: {"type": "ephemeral"}` on system content blocks.
A block marked this way is cached server-side after the first request and reused for
subsequent requests with the same block content, skipping re-tokenization.

`build_system_blocks()` (core/prompts.py) emits two blocks:

```
Block 1: static system instructions        (NOT cached — small, changes rarely)
Block 2: full city knowledge base text     (cache_control: ephemeral)
```

The retrieved jurisdiction snippets are appended as a **third block without cache_control**
because they vary per request.

Effect: on a warm cache hit, the city-block tokens are not re-processed by the model.
This translates to a ~30–60 % reduction in TTFT for repeated same-city requests, which is
the common case in any real deployment. The offline engine mirrors this warming behavior:
`engine._warm` tracks which city slugs have been classified before and uses a shorter
simulated TTFT (90 ms vs 220 ms cold).

---

## 5. Two-level response cache

```
incoming request
      │
      ▼
_make_key = sha256( city_slug + "|" + normalize(situation) )
      │
      ▼
 LRU store (in-memory, 512 entries, 5 min TTL)
      │  miss
      ▼
 Redis store (if REDIS_URL set, 5 min TTL, eai:resp: prefix)
      │  miss
      ▼
 model inference path
      │
      ▼
 write-through: Redis SET + LRU SET (async, non-blocking)
```

**Why two levels?** LRU is zero-latency (no network round-trip) and serves repeated
identical requests within the same process instantly. Redis provides cross-process sharing
across replicas and survives process restarts. If Redis is unavailable (import error or
connection timeout), the service degrades to LRU-only with a log warning — no crash, no
user impact.

**Privacy note:** the cache key is a SHA-256 hash. The raw situation text is never stored
anywhere in the cache layer. Only the hashed key and the structured response dict (which
contains urgency, actions, and calls — not the original text) are persisted.

---

## 6. Incident store and metrics

### IncidentStore (core/store.py)

Append-only audit log with a three-tier fallback:

| Condition | Backend |
|---|---|
| `DATABASE_URL` set + asyncpg importable | Postgres (connection pool, TIMESTAMPTZ) |
| `SQLITE_PATH` set | SQLite file (asyncio executor, non-blocking) |
| Neither | In-memory deque (maxlen=10 000, no infra) |

Every backend enforces the privacy invariant at the data layer: `_validate_event()` raises
`ValueError` if a `"situation"` key appears in the event dict. The allowed key set is an
explicit allowlist: `{request_id, ts, city, urgency, ttft_ms, total_ms, source, cache_hit}`.

Audit records are written with `asyncio.ensure_future()` — fire-and-forget so a slow DB
write never adds to response latency.

### Metrics (core/metrics.py)

Prometheus text-format exposition with no `prometheus_client` dependency. Module-level
globals protected by a single `threading.RLock` for thread safety. Exposed metrics:

| Metric | Type | Labels |
|---|---|---|
| `emergency_requests_total` | counter | `city`, `urgency`, `source` |
| `emergency_cache_hits_total` | counter | — |
| `emergency_errors_total` | counter | — |
| `emergency_ttft_ms` | histogram | — |

Histogram buckets: 50, 100, 150, 250, 500, 1000, 2000, 5000 ms. `render()` outputs valid
Prometheus text format, consumed by `GET /metrics` and scraped by Grafana or any
Prometheus-compatible backend.

---

## 7. Privacy invariant

**The raw situation text is never persisted or logged at any layer.**

This is enforced by design, not convention:

| Layer | Mechanism |
|---|---|
| `cache.py` | Key = `sha256(city_slug + normalized_situation)`. Only the hash is stored. |
| `store.py` | `_validate_event()` raises `ValueError` if `"situation"` key present. Explicit allowlist for all stored keys. |
| `triage.py` | Docstring: "never stored; work on local ref only". Text processed in-memory, not logged. |
| `retrieval.py` | Query string used transiently in `search()`, never persisted. |
| `server.py` | Log statements emit only `request_id, city, urgency, ttft_ms, total_ms`. |
| PWA (`app.js`) | Medical-ID card and incident log stored in `localStorage` only. Never transmitted to any server. |
| `sw.js` | Service worker never caches POST bodies. No API response containing situation context is persisted. |

Stored and logged fields are strictly: `{request_id, city, urgency, latency, source, cache_hit}`.

---

## 8. Latency budget vs measured

### Budget (from README targets)

| Metric | Target |
|---|---|
| TTFT, cold (live model) | < 800 ms |
| Total (live model) | < 2 000 ms |
| TTFT, cache hit | < 50 ms |
| Offline TTFT, cold | 220–260 ms (simulated) |
| Offline TTFT, warm | 90–110 ms (simulated) |

### Measured (mock provider — parser + serialization overhead only)

Benchmarked with `bench/bench.py` (50 requests, concurrency 5, 16-byte chunks):

| Metric | Measured | vs Budget |
|---|---|---|
| TTFT p50 | 3 ms | well under 800 ms |
| TTFT p95 | 5 ms | well under 800 ms |
| Total p50 | 21 ms | well under 2 000 ms |
| Total p95 | 27 ms | well under 2 000 ms |
| Throughput | ~236 req/s | — |

**Interpretation.** These numbers measure only the Python parsing and serialization
overhead in the service layer (no model, no network). The dominant latency in production is
model TTFT from the Anthropic API (~300–600 ms for claude-haiku-4-5 on a warm cache) plus
network RTT. The service layer adds well under 30 ms — it is not the bottleneck.

### Budget breakdown for a live request

```
t=0      Request received by FastAPI
t=1 ms   Cache lookup (LRU hit: 0 ms, Redis hit: ~1 ms)
t=~5 ms  TF-IDF retrieval (JurisdictionIndex.search, k=3)
t=~8 ms  build_system_blocks() → Anthropic API call begins
t=~350ms First token from model (warm prompt-cache) → SSE frame emitted → browser TTFT
t=~1200ms Full EmergencyResponse JSON → final SSE frame
t=~1201ms fire-and-forget: cache.set, store.record, metrics.inc_*
```

On a **cache hit** (same city + normalized situation seen before): the LRU returns the
stored dict in under 1 ms; the full JSON response returns in under 5 ms total.

---

## 9. PWA service worker — true offline

The service worker (`docs/sw.js`) enables the PWA to function with zero network after
first visit.

### Install phase

On first load the service worker precaches the entire app shell and data corpus:

```
app shell:  index.html, manifest.webmanifest, css/styles.css
JS modules: engine.js, app.js, effects.js
data:       scenarios.json, cities.json, i18n.json,
            poison.json, disasters.json, medical_ref.json
icons:      icon-192.svg, icon-512.svg, icon-maskable-512.svg
```

All 20 scenarios, 14 cities, 8 languages, poison database, and disaster protocols are
available offline from that point forward.

### Fetch routing

```
Request comes in
      │
      ├─ isApiRequest()? (/emergency, /triage, /cities, …)
      │        │ YES → networkFirst()
      │        │         try network; on failure → stale cache or 503 JSON
      │        │
      │        └─ NO (app shell, data, assets)
      │                 → cacheFirst()
      │                   serve from cache; miss → fetch + update cache
      │
      └─ POST methods other than SSE → pass through to network
```

**Network-first for API routes** means the live model response is attempted first when
connectivity exists. If the network call fails (offline, timeout), the service worker
serves the stale cached response for that route if one exists, or a 503 JSON body. The
browser-side `engine.js` then falls through to the offline deterministic path. This
layering gives three tiers of resilience:

1. Live model + cache (best case, online)
2. Stale API cache (network just dropped)
3. Offline deterministic engine (no network at all)

### Activate phase

On each SW update the activate handler deletes all caches whose name is not the current
`CACHE_NAME` (`eai-v1`). This ensures stale data bundles are evicted when the app ships
a new version.

---

## 10. Data flow summary

```
[User types situation]
        │
        ▼
app.js: engine.respond(text, city, opts)
        │
        ├── [LIVE] POST /emergency (SSE)
        │              │
        │    server.py: cache.get → miss
        │              │
        │    triage.classify(situation, city_ctx) → urgency pre-check
        │    retrieval.search(situation, city_slug, k=3) → Snippet[]
        │    prompts.build_system_blocks(city)
        │      ├── block 1: SYSTEM_INSTRUCTIONS (no cache)
        │      ├── block 2: city body (cache_control: ephemeral) ◄── prompt cache
        │      └── block 3: retrieved snippets (no cache)
        │              │
        │    AnthropicProvider.stream_text() → text deltas
        │    EmergencyClient._parse_stream() → StreamEvent[]
        │              │
        │    SSE: data: {"event":"field", "field":"urgency", "data":"critical"}
        │    SSE: … more fields …
        │    SSE: data: {"event":"final", "data":{...EmergencyResponse...}}
        │              │
        │    [async] cache.set / store.record / metrics.inc_*
        │              │
        │    browser: onField() → fx.revealField()
        │             onLatency() → fx.updateHUD()
        │             onFinal() → render complete response
        │
        └── [OFFLINE] classify(text) → _compose(text, city, cls)
                       emit fields with sleep delays (90-260ms TTFT)
                       onField() → same effects callbacks
                       onFinal() → same render path
```

---

## 11. Infrastructure topology

```
GitHub Pages (static CDN)
  docs/  ── PWA, no server required for offline use

Optional: Fly.io (infra/fly.toml)
  Port 8080 (internal), HTTP health check /health
  Multi-stage Dockerfile: build → slim runtime, non-root user

Local full-stack (docker-compose.yml):
  emergency-ai service   ← Dockerfile
  redis:7-alpine         ← REDIS_URL=redis://redis:6379
  postgres:16-alpine     ← DATABASE_URL=postgresql://...

All infra dependencies are optional. Zero-infra run:
  pip install -e .
  EMERGENCY_AI_MOCK=1 emergency-server
  # Works immediately. Redis and Postgres absent → LRU + in-memory fallbacks.
```

---

## 12. Module dependency graph

```
api/server.py
  └── core/client.py
        ├── core/cities.py
        ├── core/prompts.py
        │     └── core/cities.py
        ├── core/schema.py
        └── (anthropic SDK — lazy, optional)
  └── core/cache.py      (redis.asyncio — lazy, optional)
  └── core/store.py      (asyncpg — lazy, optional; sqlite3 — stdlib)
  └── core/metrics.py    (stdlib only)
  └── core/triage.py     (stdlib only)
  └── core/retrieval.py  (stdlib only)
  └── core/geo.py        (stdlib only)
  └── core/scenarios.py  (stdlib only)
  └── core/report.py     (stdlib only)

All optional deps (anthropic, redis.asyncio, asyncpg) are lazily imported.
ImportError → graceful fallback, logged warning, service continues.
pip install -e .  requires zero infra.
```
