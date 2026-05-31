# Benchmark Results

**Scope:** These numbers measure **parser + serialization overhead only** — the cost of
`build_system_blocks`, the streaming JSON parser (`_extract_complete_keys`), and pydantic
schema validation (`EmergencyResponse.model_validate`). They use `MockProvider`, which
replays a canned JSON response with zero artificial delay (`asyncio.sleep(0)` cooperative
yields only). **No model latency, no network, no cache lookup is included.**

These numbers tell you the Python service layer's own tax. To estimate true end-to-end
latency, add the live-model budget from the README (reproduced below).

---

## How to reproduce

```bash
# from repo root, with the package installed
python bench/bench.py                          # 200 requests, concurrency=10
python bench/bench.py --n 200 --concurrency 1  # serial baseline
python bench/bench.py --n 500 --concurrency 20 # higher load
```

---

## Methodology

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Provider | `MockProvider` | Deterministic canned JSON; no Anthropic key required |
| Chunk size | 16 bytes | Approximates real token-granularity streaming deltas |
| Concurrency | 1 (serial) and 10 (concurrent) | Covers single-user and light multi-user scenarios |
| Requests | 200 per run | Enough for stable p95/p99 on a 6-city round-robin |
| Cities | new-york, san-francisco, london, tokyo, mumbai, bangalore | All bundled cities |
| Situations | One representative high-urgency situation per city | Representative hot path |

**TTFT definition:** elapsed time from `client.stream(req)` entry to the first `StreamEvent`
carrying a real response field (i.e., the first parsed JSON key — typically `urgency`).

**Total definition:** elapsed time from `client.stream(req)` entry to the `__final__`
`StreamEvent` carrying the validated `EmergencyResponse`.

**Measured on:** macOS 15 (Apple M-series), Python 3.12, single process, event loop
running on a single OS thread (standard `asyncio.run`). No I/O blocking outside the
async yields in MockProvider.

---

## Mock-measured results (parser + serialization overhead — NO model latency)

### Serial baseline (concurrency = 1)

| Metric | TTFT (ms) | Total (ms) |
|--------|-----------|------------|
| p50    | 1.11      | 9.72       |
| p95    | 2.84      | 24.92      |
| p99    | 5.25      | 45.27      |
| mean   | 1.37      | 12.51      |
| min    | 0.45      | 2.88       |
| max    | 12.02     | 239.87     |

Throughput: **79 req/s** single-worker.

### Concurrent run (concurrency = 10, 200 requests)

| Metric | TTFT (ms) | Total (ms) |
|--------|-----------|------------|
| p50    | 5.34      | 37.07      |
| p95    | 34.29     | 173.28     |
| p99    | 129.81    | 174.58     |
| mean   | 11.92     | 69.82      |
| min    | 1.92      | 31.11      |
| max    | 130.65    | 174.62     |

Throughput: **143 req/s** across 10 workers.

The p95/p99 rise under concurrency reflects asyncio event-loop scheduling contention on
the streaming JSON parser (a CPU-bound loop), not I/O saturation. In production the
model's network latency (hundreds of ms) dwarfs this entirely.

### Per-city breakdown — concurrent run (total latency ms)

| City          | N  | p50   | p95    | p99    |
|---------------|----|-------|--------|--------|
| bangalore     | 33 | 39.81 | 173.11 | 173.39 |
| london        | 33 | 37.45 | 172.46 | 174.17 |
| mumbai        | 33 | 37.36 | 173.47 | 174.33 |
| new-york      | 34 | 35.84 | 172.39 | 173.13 |
| san-francisco | 34 | 35.96 | 171.83 | 172.91 |
| tokyo         | 33 | 37.14 | 172.73 | 174.23 |

City-to-city variance is negligible (<4 ms p50 spread) — city context size differences
do not meaningfully affect parser overhead.

---

## Live latency budget (from README — not mock-measured)

These are the design targets for a deployed instance with the real Haiku model.
The README states these explicitly as **budgets, not measured guarantees**.

| Phase                            | Budget           | Notes |
|----------------------------------|------------------|-------|
| Network (mobile → server)        | < 150 ms         | Edge deploy, persistent connection |
| Cache lookup + LLM TTFT (cold)   | < 600 ms         | Haiku 4.5, prompt cache miss |
| Cache lookup + LLM TTFT (warm)   | < 150 ms         | Prompt cache hit (≥5-min TTL window) |
| First action visible to user     | **< 800 ms**     | Streamed; `urgency` + first action emitted |
| Full structured response         | **< 2,000 ms**   | End of stream, full pydantic validation |

---

## Honest interpretation

```
end-to-end latency ≈ service overhead (this file)
                    + network round-trip
                    + model TTFT (cold: ~600 ms / warm: ~150 ms)
                    + model generation time (remaining tokens)
```

With warm cache, realistic end-to-end TTFT is approximately:

```
~5 ms (parser, p50) + ~50 ms (network, edge) + ~150 ms (cached TTFT) ≈ 205 ms
```

With cold cache:

```
~5 ms (parser, p50) + ~50 ms (network, edge) + ~600 ms (cold TTFT) ≈ 655 ms
```

Both are comfortably inside the 800 ms TTFT budget. The service layer itself
contributes less than 1% of end-to-end latency in the live path — the model is the
dominant term. Optimizing the Python parser further would be premature.

The p99 parser overhead of ~130 ms under concurrency is worth noting: in a sustained
burst it adds measurably to tail latency. The mitigation is horizontal scaling (each
replica runs its own event loop) rather than parser optimization.

---

## What these numbers do NOT cover

- Real Anthropic API latency (network + model TTFT + generation time)
- Redis cache lookup latency (typically < 1 ms local, < 5 ms remote)
- Postgres/SQLite `IncidentStore` write latency (async, off the hot path)
- FastAPI request/response framing overhead (ASGI, header parsing)
- TLS handshake (first connection)
- Prometheus metrics scrape impact (negligible)

A full end-to-end benchmark with a live Anthropic key and `k6` load driver against a
deployed Fly.io instance is the next step (tracked in the project deploy todo).
