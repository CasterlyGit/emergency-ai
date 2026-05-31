# emergency-ai — Showcase Build Contract (v1.0)

Single source of truth for the v1.0 "real product" build. Every generated file MUST
conform to the schemas, symbol names, DOM IDs, and design tokens below so independently
authored modules compose without rework.

---

## 0. Product shape

`emergency-ai` is **(a)** an offline-first, installable emergency-response PWA in `docs/`
(the live GitHub Pages showcase, works with zero network) **and (b)** a real FastAPI
inference + retrieval + observability service in `src/emergency_ai/`.

The PWA degrades gracefully: if `window.EMERGENCY_API_BASE` is set and reachable it uses
the live model via SSE; otherwise it uses the bundled offline `engine.js` (deterministic
triage + scenario corpus) that *mimics* streaming inference so the UX is identical online
or off. **This dual path is the core architectural story.**

---

## 1. Design tokens (CSS custom properties) — matte-black neon "JARVIS HUD"

Authoritative `:root` tokens. Every stylesheet/component uses these, never hard-coded hex.

```
--bg:#04060a; --bg-1:#070b12; --surface:rgba(18,24,34,.62); --surface-2:rgba(28,36,50,.72);
--glass-brd:rgba(120,190,255,.14); --glass-hi:rgba(180,230,255,.06);
--neon:#2ee6ff; --neon-dim:#1aa6c4; --neon-soft:rgba(46,230,255,.14);
--ok:#30e87a; --u-low:#30e87a; --u-medium:#ffd60a; --u-high:#ff7a18; --u-critical:#ff2d55;
--text:#eaf6ff; --text-dim:#8fa6bd; --text-mut:#5b6f86;
--r-s:10px; --r-m:16px; --r-l:24px; --r-xl:34px;
--blur:18px; --glow:0 0 22px var(--neon-soft); --glow-strong:0 0 38px rgba(46,230,255,.32);
--mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
--sans:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;
--ease:cubic-bezier(.22,1,.36,1); --ease-in:cubic-bezier(.5,0,.75,0);
```

Urgency drives an **ambient theme** via a `data-urgency` attribute on `<html>`:
`critical|high|medium|low`. CSS reacts: accent color = `--u-*`, and a body radial-glow
"breathes" (slow pulse) faster + redder at higher urgency. Default (idle) accent = `--neon`.

Aesthetic: matte black, cyan neon, glassmorphism (backdrop-filter blur), thin luminous
borders, mono for all numbers/latency, generous radii, subtle grain. No drop-shadow soup.

---

## 2. Data file schemas (all under `docs/data/`, UTF-8 JSON)

### `scenarios.json` — `{ "version": 1, "scenarios": Scenario[] }`
```
Scenario = {
  id: string,                 // kebab, stable, e.g. "cardiac-arrest"
  title: string,              // "Cardiac arrest / not breathing"
  short: string,              // tile label, <= 22 chars, e.g. "Not breathing"
  icon: string,               // single emoji
  category: "medical"|"trauma"|"environmental"|"threat"|"poison",
  keywords: string[],         // for offline classifier + ⌘K + free-text match
  urgency: "critical"|"high"|"medium"|"low",
  time_to_act_seconds: int,
  immediate_actions: string[],        // 3-7 imperative steps, ordered
  reasoning: string[],                // SAME length as immediate_actions; the "why" per step
  avoid: string[],                    // 0-6
  who_to_call: { [label:string]: string },  // {"primary":"911","poison":"1-800-..."}
  triage: TriageQuestion[],           // 0-3 decisive branch questions (may be [])
  metronome_bpm: int|null,            // non-null only for CPR-type (e.g. 110)
  beacon: boolean,                    // true if a visual signal/strobe helps (e.g. lost, trapped)
  tags: string[]                      // e.g. ["cpr","aed","fast","tourniquet"]
}
TriageQuestion = {
  id: string,
  q: string,                          // "Is the person breathing?"
  options: { label: string, effect: { set_urgency?: Urgency, note?: string, jump?: string } }[]
}
```
Minimum 18 scenarios spanning all categories (see §8 list).

### `cities.json` — `{ "version": 1, "cities": City[] }`
```
City = {
  slug, display_name, country, flag (emoji), region (continent),
  lat: number, lon: number,           // city centroid for offline nearest-city geo
  primary: string,                    // primary emergency number
  numbers: { [label]: string },       // poison, mental_health, non_emergency, etc.
  laws: { title: string, ref: string, text: string }[],   // Good Samaritan, amnesty, ...
  notes: string[],                    // practical local notes
  hospitals: string[]                 // named trauma centers
}
```
Min 12 cities: existing 6 (new-york, san-francisco, london, tokyo, mumbai, bangalore)
mirrored from `src/emergency_ai/cities/*.md`, plus delhi, los-angeles, chicago, paris,
berlin, sydney, singapore, toronto (closes issue #5).

### `i18n.json` — `{ "version": 1, "languages": {code:{name,flag,dir}}, "strings": {key:{code:translated}} }`
UI string keys (authoritative set): `app_title, tagline, ask_placeholder, locate_me,
listen, speak, actions, avoid, who_to_call, call_now, share_location, alert_contact,
siren, beacon, medical_id, guided_mode, explain_why, confidence, disclaimer, urgency,
act_within, offline_badge, cache_hit, language, scenarios, law_explorer, incident_log,
export_report, settings, fast_test, more`.
Languages: en, es, hi, ja, fr, de, zh, ar (ar is dir:"rtl"). en values are canonical English.

### `poison.json` — `{ "version":1, "substances": {name, category, induce_vomiting:boolean, first_aid:string[], antidote:string|null, call:string}[] }`
### `disasters.json` — `{ "version":1, "protocols": {id,title,icon,region:"global"|continent,steps:string[],avoid:string[]}[] }`
(earthquake, house-fire, flood, active-threat, wildfire, tornado, heatwave, gas-leak)
### `medical_ref.json` — `{ recovery_position:string[], cpr:{adult,child,infant: string[]}, fast:{F,A,S,T:string}, tourniquet:string[], epipen:string[], heimlich:{adult,infant:string[]} }`

---

## 3. `engine.js` — offline inference engine (ES module, `docs/js/engine.js`)

Default export class `EmergencyEngine`. Public API (STABLE — UI depends on it):
```
await engine.load()                  // fetch+cache all data/*.json; resolves when ready
engine.cities: City[]                // sorted by display_name
engine.scenarios: Scenario[]
engine.resolveCity(nameOrSlug): City|null
engine.nearestCity(lat, lon): City   // haversine over city centroids
engine.classify(text): { urgency, score, scenario: Scenario|null, signals: string[] }
                                     // transparent weighted-keyword triage; signals = matched terms
engine.respond(text, city, { onField, onToken, onLatency, onFinal, signal, lang }): Promise<Response>
   // If EMERGENCY_API_BASE set -> POST SSE to `${base}/emergency`, forward events.
   // Else -> offline: classify, pick scenario (or compose generic), then EMIT field-by-field
   //   with realistic inter-token delays (simulated TTFT 90-260ms, full < 1.6s) calling
   //   onToken(field, chunk) for streaming reveal, onField(field,value), onLatency({ttft_ms,total_ms,cache_hit}),
   //   onFinal(Response). Cache "warms" per city (first call cold ~240ms, repeat ~110ms).
engine.t(key, lang): string          // i18n lookup w/ en fallback
engine.cacheState(): { [slug]: "cold"|"warm" }   // for the heat-map viz
```
`Response` object shape == backend `EmergencyResponse` + `_meta`:
```
{ urgency, time_to_act_seconds, immediate_actions[], reasoning[], avoid[], who_to_call{},
  jurisdictional_notes, confidence, disclaimer, scenario_id|null,
  _meta:{ ttft_ms, total_ms, cache_hit, source:"offline"|"live", city_slug } }
```

`app.js` (`docs/js/app.js`, ES module) is the controller/state-machine and owns all DOM
wiring, feature modules (voice, metronome, beacon, siren, geo, palette, parallax, haptics,
medical-id, incident-log, guided-mode, translation). `effects.js` (`docs/js/effects.js`)
owns purely-visual helpers (token reveal, latency gauge, cache heat-map, ambient theme,
skeleton morph). These three are hand-authored by the orchestrator; data + backend + docs
are fanned out. Keep `app.js`/`effects.js` importing only from `engine.js`.

---

## 4. DOM contract (IDs pinned in `docs/index.html`)

App regions (ids): `#app, #sos-orb, #ask, #ask-input, #city-select, #locate-btn,
#response, #resp-urgency, #resp-timer, #resp-actions, #resp-avoid, #resp-calls,
#resp-notes, #resp-confidence, #resp-disclaimer, #latency-hud, #cache-heatmap,
#scenario-grid, #triage-modal, #guided-modal, #palette, #palette-input, #palette-results,
#law-explorer, #incident-log, #medical-id, #settings, #lang-select, #toast`.
Action bar buttons (ids): `#btn-listen, #btn-speak, #btn-call, #btn-share, #btn-alert,
#btn-siren, #btn-beacon, #btn-metronome, #btn-explain, #btn-guided, #btn-export`.
All interactive controls carry `data-i18n="<key>"` for live translation.

---

## 5. Backend modules (`src/emergency_ai/`) — make the README's stack TRUE

New modules (each authored against these signatures; in-memory fallbacks so nothing is a
hard dependency — `pip install -e .` still runs with zero infra):

- `core/triage.py` — `classify(situation:str, city:CityContext) -> TriageResult` (dataclass:
  urgency, score:float, matched:str|None, signals:list[str]). Pure-Python weighted keyword
  engine; the same logic mirrored in `engine.js`. Fully offline, deterministic, unit-tested.
- `core/retrieval.py` — `class JurisdictionIndex`: builds a TF-IDF (pure-python, no heavy
  deps) index over per-city law/notes paragraphs; `.search(query, city_slug, k) -> Snippet[]`.
  This is the honest "RAG": retrieve the most relevant statute paragraphs and inject only
  those into the prompt (smaller cache block, grounded answers). `build_system_blocks` gains
  an optional retrieved-snippets path.
- `core/metrics.py` — Prometheus exposition WITHOUT a heavy dep: counters/histograms
  (`emergency_requests_total{city,urgency,source}`, `emergency_ttft_ms` histogram,
  `emergency_cache_hits_total`, `emergency_errors_total`) + `render() -> str` in text format.
- `core/cache.py` — `class ResponseCache`: Redis-backed (via `redis.asyncio` if
  `REDIS_URL` set) with an in-memory LRU fallback. Keys on (city_slug, normalized_situation).
- `core/store.py` — `class IncidentStore`: append-only audit of `{request_id, ts, city,
  urgency, ttft_ms, total_ms, source, cache_hit}` (NEVER the situation text — privacy).
  Postgres via `asyncpg` if `DATABASE_URL` set, else SQLite file, else in-memory.
- `core/scenarios.py` — loader for `data/scenarios.json` (shared with PWA via a generator
  script); `list_scenarios()`, `get(id)`.
- `core/geo.py` — `nearest_city(lat, lon, cities) -> CityContext` (haversine). Closes #2/#7.
- `core/report.py` — `render_incident_report(events) -> str` (markdown) for export.

New endpoints in `api/server.py` (additive, existing `/health` + `/emergency` unchanged
in contract): `GET /metrics` (text/plain Prometheus), `GET /cities`, `GET /cities/{slug}`,
`GET /scenarios`, `POST /triage` (offline classifier, no model key needed — always works),
`POST /geo/resolve` ({lat,lon}->city), `GET /version`. `/emergency` records to metrics +
store + cache. CORS enabled so the Pages PWA can call a deployed instance.

`pyproject.toml`: add optional extras `[redis]`, `[postgres]`, keep core deps light.

---

## 6. Infra & docs (fan-out)
- `Dockerfile` (multi-stage, slim, non-root, healthcheck hitting `/health`).
- `infra/fly.toml` (Fly.io app config, internal 8080, http health check) — deploy-ready.
- `docker-compose.yml` (app + redis + postgres for the full-stack local run).
- `LICENSE` (MIT, author CasterlyGit, year 2026).
- `bench/results.md` — methodology + a runnable `bench/bench.py` (asyncio load over mock
  provider) and a results table (clearly labeled mock-measured vs live-budget).
- `README.md` — rewrite: lead with live PWA + offline story, real architecture diagram
  (PWA ⇄ service ⇄ retrieval/cache/store/metrics), honest stack table, quickstart
  (`docker compose up`), feature catalog link, benchmark link. Repo description + topics
  become TRUE (Postgres/Redis/RAG/Prometheus all really present, Fly-ready).
- `FEATURES.md` — the 35-feature catalog (§8), each with a one-line "why it matters".
- `ARCHITECTURE.md` — dual-path (online/offline) design, retrieval+cache+store rationale,
  privacy posture, latency budget vs measured.
- `docs/sw.js` (service worker: cache-first for app shell + data, network-first for API),
  `docs/manifest.webmanifest` (installable, icons, theme matte-black), `docs/assets/*` icons.

---

## 7. Privacy invariant (HARD RULE, every layer)
Never persist or log the raw `situation` text. Store/metrics/logs carry only
`{request_id, city, urgency, latency, source, cache_hit}`. The PWA keeps medical-ID and
incident log in `localStorage` only (never transmitted). State this in README + ARCHITECTURE.

---

## 8. Canonical 35-feature list (for FEATURES.md, ⌘K, and QA checklist)

A (15 headline): A1 offline PWA · A2 voice SOS+TTS · A3 CPR metronome · A4 auto-geo
jurisdiction · A5 tap-to-dial/sms · A6 adaptive triage Q · A7 instant translation ·
A8 precise location share · A9 strobe SOS beacon · A10 contact auto-alert · A11 siren ·
A12 medical-ID card · A13 guided full-screen mode · A14 scenario quick-grid · A15 honesty layer.

B (10 depth): B1 FAST stroke test · B2 tourniquet/bleeding guide · B3 offline urgency
classifier · B4 jurisdiction law explorer · B5 incident timeline+export · B6 poison lookup ·
B7 EpiPen/anaphylaxis · B8 drowning+recovery position · B9 disaster protocols ·
B10 explain-why reasoning.

C (10 UI): C1 triage-reactive ambient theme · C2 streaming token reveal · C3 live latency
HUD gauge · C4 matte-black neon glass system · C5 long-press SOS orb · C6 haptic+audio
feedback · C7 tilt parallax · C8 skeleton→content morph · C9 ⌘K command palette · C10 live
cache heat-map.

Scenarios (min 18): cardiac-arrest, choking-adult, choking-infant, severe-bleeding,
stroke, anaphylaxis, opioid-overdose, seizure, burns, drowning, heart-attack,
heat-stroke, hypothermia, broken-bone, head-injury, poisoning, house-fire, childbirth,
allergic-reaction, electric-shock.
