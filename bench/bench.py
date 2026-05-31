"""Benchmark: measure parser + serialization overhead using MockProvider.

No network, no model — isolates the service-layer cost:
  * system-block assembly (build_system_blocks)
  * MockProvider chunk iteration (asyncio.sleep(0) yields)
  * streaming JSON parser (_extract_complete_keys)
  * pydantic schema validation (EmergencyResponse.model_validate)

Run:
    python bench/bench.py [--n 200] [--concurrency 10] [--chunk-size 16]

Output: p50 / p95 / p99 for TTFT and total latency, plus per-city breakdown.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

# Make sure the installed package is importable when running from repo root.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from emergency_ai.core.cities import load_cities
from emergency_ai.core.client import EmergencyClient, MockProvider
from emergency_ai.core.schema import EmergencyRequest

# ---------------------------------------------------------------------------
# Workload definition — one representative situation per city.
# Cities are picked from the bundled set (fails gracefully to unknown city).
# ---------------------------------------------------------------------------
WORKLOAD: list[tuple[str, str]] = [
    ("new-york", "Person collapsed and not breathing, bystanders nearby"),
    ("san-francisco", "Severe bleeding from a knife wound, can't stop it"),
    ("london", "Child choking, face turning blue"),
    ("tokyo", "Person having a seizure on the train platform"),
    ("mumbai", "Someone took too many pills and is unconscious"),
    ("bangalore", "Suspected stroke — face drooping, slurred speech"),
]

# Latency budget from the README (ms) — used in the printed comparison table.
BUDGET_TTFT_COLD_MS = 800   # < 800 ms to first action visible
BUDGET_TOTAL_MS = 2_000     # < 2 s full structured response


def _percentile(data: list[float], p: float) -> float:
    """Return the p-th percentile (0-100) of a sorted list."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = (p / 100) * (len(sorted_data) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_data) - 1)
    frac = idx - lo
    return sorted_data[lo] * (1 - frac) + sorted_data[hi] * frac


async def _single_run(
    client: EmergencyClient,
    city: str,
    situation: str,
) -> tuple[float, float]:
    """Run one request and return (ttft_ms, total_ms).

    TTFT = time until the first StreamEvent with a real field arrives.
    Total = full time from request start to __final__ event.
    """
    req = EmergencyRequest(situation=situation, city=city)
    ttft_ms: float | None = None
    start = time.monotonic()

    async for ev in client.stream(req):
        elapsed = (time.monotonic() - start) * 1000
        if ttft_ms is None and ev.field not in ("__final__", "__error__", "__latency_ms__"):
            ttft_ms = elapsed
        if ev.field == "__final__":
            total_ms = elapsed
            break
    else:
        total_ms = (time.monotonic() - start) * 1000

    return (ttft_ms or total_ms, total_ms)


async def _worker(
    queue: asyncio.Queue[tuple[str, str] | None],
    client: EmergencyClient,
    results: dict[str, list[tuple[float, float]]],
    errors: list[str],
) -> None:
    while True:
        item = await queue.get()
        if item is None:
            queue.task_done()
            break
        city, situation = item
        try:
            ttft, total = await _single_run(client, city, situation)
            results.setdefault(city, []).append((ttft, total))
        except Exception as exc:
            errors.append(f"{city}: {exc}")
        finally:
            queue.task_done()


async def run_bench(n: int, concurrency: int, chunk_size: int) -> None:
    cities = load_cities()
    provider = MockProvider(chunk_size=chunk_size)
    client = EmergencyClient(provider=provider, cities=cities)

    # Build the queue: distribute N requests round-robin across workload cities.
    queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()
    for i in range(n):
        city, situation = WORKLOAD[i % len(WORKLOAD)]
        await queue.put((city, situation))
    # Sentinel per worker
    for _ in range(concurrency):
        await queue.put(None)

    results: dict[str, list[tuple[float, float]]] = {}
    errors: list[str] = []

    wall_start = time.monotonic()
    workers = [
        asyncio.create_task(_worker(queue, client, results, errors))
        for _ in range(concurrency)
    ]
    await queue.join()
    await asyncio.gather(*workers)
    wall_elapsed = (time.monotonic() - wall_start) * 1000

    # Aggregate
    all_ttft: list[float] = []
    all_total: list[float] = []
    for pairs in results.values():
        for ttft, total in pairs:
            all_ttft.append(ttft)
            all_total.append(total)

    total_requests = len(all_ttft)
    if total_requests == 0:
        print("No successful requests. Errors:", errors)
        return

    print()
    print("=" * 68)
    print("  emergency-ai  bench — MockProvider (parser + serialization only)")
    print("  NO model latency included — see results.md for interpretation")
    print("=" * 68)
    print(f"  Requests  : {total_requests}   Concurrency: {concurrency}   Chunk: {chunk_size}B")
    print(f"  Wall time : {wall_elapsed:.0f} ms   Throughput: {total_requests / (wall_elapsed/1000):.1f} req/s")
    print()

    _print_latency_table(
        "TTFT (first streamed field)",
        all_ttft,
        budget_ms=BUDGET_TTFT_COLD_MS,
    )
    print()
    _print_latency_table(
        "Total (full EmergencyResponse)",
        all_total,
        budget_ms=BUDGET_TOTAL_MS,
    )

    # Per-city breakdown
    print()
    print("  Per-city breakdown (total latency ms)")
    print(f"  {'City':<20}  {'N':>4}  {'p50':>8}  {'p95':>8}  {'p99':>8}")
    print("  " + "-" * 56)
    for city in sorted(results):
        pairs = results[city]
        totals = [t for _, t in pairs]
        p50 = _percentile(totals, 50)
        p95 = _percentile(totals, 95)
        p99 = _percentile(totals, 99)
        print(f"  {city:<20}  {len(pairs):>4}  {p50:>7.2f}  {p95:>7.2f}  {p99:>7.2f}")

    if errors:
        print()
        print(f"  Errors ({len(errors)}):")
        for e in errors[:10]:
            print(f"    {e}")

    print()
    print("  NOTE: These numbers measure ONLY Python parsing/serialization overhead.")
    print("  Add the live-budget numbers from results.md to get end-to-end latency.")
    print("=" * 68)
    print()


def _print_latency_table(label: str, data: list[float], budget_ms: float) -> None:
    p50 = _percentile(data, 50)
    p95 = _percentile(data, 95)
    p99 = _percentile(data, 99)
    mean = statistics.mean(data)
    mn = min(data)
    mx = max(data)

    # Budget comparison markers
    def marker(v: float) -> str:
        return "OK" if v < budget_ms else "OVER"

    print(f"  {label}")
    print(f"  {'Metric':<12}  {'ms':>8}  {'vs budget':>12}")
    print("  " + "-" * 38)
    print(f"  {'p50':<12}  {p50:>8.2f}  {marker(p50):>12}  (budget {budget_ms:.0f} ms)")
    print(f"  {'p95':<12}  {p95:>8.2f}  {marker(p95):>12}")
    print(f"  {'p99':<12}  {p99:>8.2f}  {marker(p99):>12}")
    print(f"  {'mean':<12}  {mean:>8.2f}")
    print(f"  {'min':<12}  {mn:>8.2f}")
    print(f"  {'max':<12}  {mx:>8.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark emergency-ai service overhead (MockProvider, no model)."
    )
    parser.add_argument(
        "--n",
        type=int,
        default=200,
        help="Total number of requests to run (default: 200)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Number of concurrent async workers (default: 10)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=16,
        dest="chunk_size",
        help="MockProvider chunk size in bytes (default: 16, mimics token granularity)",
    )
    args = parser.parse_args()

    asyncio.run(run_bench(n=args.n, concurrency=args.concurrency, chunk_size=args.chunk_size))


if __name__ == "__main__":
    main()
