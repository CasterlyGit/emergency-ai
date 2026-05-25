"""Prometheus metrics definitions and /metrics endpoint.

Metrics exported:
    emergency_requests_total   — counter, labels: city, urgency, status
    emergency_ttft_seconds     — histogram of time-to-first-token
    emergency_total_seconds    — histogram of full response time
    cache_hits_total           — counter, labels: city
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)

_LATENCY_BUCKETS = (0.1, 0.25, 0.5, 0.8, 1.0, 1.5, 2.0, 5.0)

emergency_requests_total = Counter(
    "emergency_requests_total",
    "Total emergency requests processed",
    ["city", "urgency", "status"],
)

emergency_ttft_seconds = Histogram(
    "emergency_ttft_seconds",
    "Time to first token (seconds)",
    buckets=_LATENCY_BUCKETS,
)

emergency_total_seconds = Histogram(
    "emergency_total_seconds",
    "Total response time (seconds)",
    buckets=_LATENCY_BUCKETS,
)

cache_hits_total = Counter(
    "cache_hits_total",
    "Prompt-cache hits from Anthropic",
    ["city"],
)

router = APIRouter()


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics_endpoint() -> PlainTextResponse:
    """Return Prometheus text-format metrics."""
    data = generate_latest()
    return PlainTextResponse(content=data, media_type=CONTENT_TYPE_LATEST)
