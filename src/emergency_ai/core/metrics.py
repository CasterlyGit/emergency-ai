"""
Prometheus metrics exposition — pure Python, no prometheus_client dependency.

Registry:
  emergency_requests_total{city,urgency,source}  counter
  emergency_cache_hits_total                      counter
  emergency_errors_total                          counter
  emergency_ttft_ms                               histogram (buckets below)

Public API:
  inc_request(city, urgency, source)
  inc_cache_hit()
  inc_error()
  observe_ttft(ms)
  render() -> str   # valid Prometheus text-format exposition

All operations are thread-safe via a single RLock.
"""

from __future__ import annotations

import math
import threading

# ---------------------------------------------------------------------------
# Internal primitives
# ---------------------------------------------------------------------------

_LOCK = threading.RLock()

# Counter: value is a float stored in a dict keyed by a sorted label tuple.
# E.g. _requests[(city="new-york", urgency="critical", source="live")] = 42.0
_CounterStore = dict[tuple[str, ...], float]

_requests: _CounterStore = {}       # labeled: (city, urgency, source)
_cache_hits: float = 0.0            # unlabeled
_errors: float = 0.0                # unlabeled

# Histogram: tracks per-bucket counts and sum/count.
_TTFT_BUCKETS: list[float] = [50.0, 100.0, 150.0, 250.0, 500.0, 1000.0, 2000.0, 5000.0]

# bucket_counts[i] = observations <= _TTFT_BUCKETS[i]
_ttft_bucket_counts: list[float] = [0.0] * len(_TTFT_BUCKETS)
_ttft_inf_count: float = 0.0       # +Inf bucket (all observations)
_ttft_sum: float = 0.0
_ttft_count: float = 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def inc_request(city: str, urgency: str, source: str) -> None:
    """Increment emergency_requests_total for the given label set."""
    key = (city, urgency, source)
    with _LOCK:
        _requests[key] = _requests.get(key, 0.0) + 1.0


def inc_cache_hit() -> None:
    """Increment emergency_cache_hits_total."""
    global _cache_hits
    with _LOCK:
        _cache_hits += 1.0


def inc_error() -> None:
    """Increment emergency_errors_total."""
    global _errors
    with _LOCK:
        _errors += 1.0


def observe_ttft(ms: float) -> None:
    """Record one observation for the emergency_ttft_ms histogram.

    Each bucket counter is cumulative: bucket[i] counts all observations
    whose value is <= _TTFT_BUCKETS[i].  This matches the Prometheus
    histogram data model — render() outputs the bucket counters directly.
    """
    global _ttft_inf_count, _ttft_sum, _ttft_count
    with _LOCK:
        for i, bound in enumerate(_TTFT_BUCKETS):
            if ms <= bound:
                _ttft_bucket_counts[i] += 1.0
            # buckets with bound < ms are NOT incremented, so lower-bound
            # buckets are strict subsets — cumulative naturally.
        _ttft_inf_count += 1.0
        _ttft_sum += ms
        _ttft_count += 1.0


# ---------------------------------------------------------------------------
# Prometheus text-format renderer
# ---------------------------------------------------------------------------

def render() -> str:
    """
    Return a valid Prometheus text-format exposition string.

    Format spec: https://prometheus.io/docs/instrumenting/exposition_formats/
    Each metric family has exactly one HELP line and one TYPE line followed by
    sample lines.  Label values are double-quoted; special chars are escaped.
    Numbers use repr so they round-trip exactly.
    """
    with _LOCK:
        # Snapshot under lock so the response is internally consistent.
        req_snapshot = dict(_requests)
        cache_hits_snap = _cache_hits
        errors_snap = _errors
        bucket_snap = list(_ttft_bucket_counts)
        inf_snap = _ttft_inf_count
        sum_snap = _ttft_sum
        count_snap = _ttft_count

    lines: list[str] = []

    # ------------------------------------------------------------------
    # emergency_requests_total
    # ------------------------------------------------------------------
    lines.append("# HELP emergency_requests_total Total emergency requests handled.")
    lines.append("# TYPE emergency_requests_total counter")
    for (city, urgency, source), value in sorted(req_snapshot.items()):
        label_str = (
            f'city="{_escape(city)}",'
            f'urgency="{_escape(urgency)}",'
            f'source="{_escape(source)}"'
        )
        lines.append(f"emergency_requests_total{{{label_str}}} {_fmt(value)}")

    # ------------------------------------------------------------------
    # emergency_cache_hits_total
    # ------------------------------------------------------------------
    lines.append("# HELP emergency_cache_hits_total Total cache hits on emergency responses.")
    lines.append("# TYPE emergency_cache_hits_total counter")
    lines.append(f"emergency_cache_hits_total {_fmt(cache_hits_snap)}")

    # ------------------------------------------------------------------
    # emergency_errors_total
    # ------------------------------------------------------------------
    lines.append("# HELP emergency_errors_total Total errors during emergency request processing.")
    lines.append("# TYPE emergency_errors_total counter")
    lines.append(f"emergency_errors_total {_fmt(errors_snap)}")

    # ------------------------------------------------------------------
    # emergency_ttft_ms (histogram)
    # ------------------------------------------------------------------
    lines.append(
        "# HELP emergency_ttft_ms Time to first token of emergency response in milliseconds."
    )
    lines.append("# TYPE emergency_ttft_ms histogram")
    # Histogram buckets are cumulative (le = "less-than-or-equal").
    # _ttft_bucket_counts[i] already holds the cumulative count (observations
    # where value <= _TTFT_BUCKETS[i]) — output directly, no re-summing.
    for i, bound in enumerate(_TTFT_BUCKETS):
        bound_str = _fmt_bound(bound)
        lines.append(
            f'emergency_ttft_ms_bucket{{le="{bound_str}"}} {_fmt(bucket_snap[i])}'
        )
    # +Inf bucket = total count
    lines.append(f'emergency_ttft_ms_bucket{{le="+Inf"}} {_fmt(inf_snap)}')
    lines.append(f"emergency_ttft_ms_sum {_fmt(sum_snap)}")
    lines.append(f"emergency_ttft_ms_count {_fmt(count_snap)}")

    # Prometheus text format ends with a trailing newline.
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reset helper (for tests)
# ---------------------------------------------------------------------------

def _reset() -> None:
    """Reset all metrics to zero.  Intended for unit tests only."""
    global _cache_hits, _errors, _ttft_inf_count, _ttft_sum, _ttft_count
    with _LOCK:
        _requests.clear()
        _cache_hits = 0.0
        _errors = 0.0
        for i in range(len(_ttft_bucket_counts)):
            _ttft_bucket_counts[i] = 0.0
        _ttft_inf_count = 0.0
        _ttft_sum = 0.0
        _ttft_count = 0.0


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _escape(s: str) -> str:
    """Escape label values per the Prometheus text-format spec."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _fmt(value: float) -> str:
    """Format a float for Prometheus: integer-looking floats use int repr."""
    if math.isfinite(value) and value == math.floor(value):
        return str(int(value))
    return repr(value)


def _fmt_bound(value: float) -> str:
    """Format a histogram bucket bound: drop '.0' suffix for whole numbers."""
    if value == math.floor(value):
        return str(int(value))
    return repr(value)
