"""FastAPI service. Supports JSON and Server-Sent Events responses.

v1.0:
    - v0.2 production layer: API key auth (X-Api-Key), Redis sliding-window rate
      limiting, Prometheus /metrics, async RequestLog persistence — all with graceful
      degradation (auth/rate-limit/DB failures never break inference; set
      EMERGENCY_AI_NO_AUTH=1 or leave infra unconfigured).
    - v1.0 read-only endpoints powering the offline-first PWA: /cities, /cities/{slug},
      /scenarios, /triage (offline classifier, no key needed), /geo/resolve, /version.
    - CORS enabled so the GitHub Pages PWA can call a deployed instance.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..core.cities import load_cities
from ..core.client import AnthropicProvider, EmergencyClient, MockProvider
from ..core.schema import EmergencyRequest, EmergencyResponse, fallback_response
from ..db.session import create_all, get_session
from .metrics import (
    emergency_requests_total,
    emergency_total_seconds,
    emergency_ttft_seconds,
)
from .metrics import (
    router as metrics_router,
)
from .middleware import check_rate_limit, get_limiter, require_api_key

log = logging.getLogger("emergency_ai")
log.setLevel(logging.INFO)
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_handler)

# Strong refs to fire-and-forget audit tasks so they aren't GC'd mid-flight (RUF006).
_bg_tasks: set = set()


def _spawn(coro):
    task = asyncio.ensure_future(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


# ---------------------------------------------------------------------------
# Pydantic models for the v1.0 read-only endpoints
# ---------------------------------------------------------------------------

class TriageRequest(BaseModel):
    situation: str = Field(..., min_length=1)
    city: str = Field(default="")


class TriageResponse(BaseModel):
    urgency: str
    score: float
    signals: list[str]
    matched: str | None


class GeoResolveRequest(BaseModel):
    lat: float
    lon: float


class GeoResolveResponse(BaseModel):
    slug: str
    display_name: str


async def _log_request(
    session,
    api_key: str | None,
    city_slug: str,
    urgency: str,
    ttft_ms: int | None,
    total_ms: int | None,
    cache_hit: bool,
) -> None:
    """Persist a RequestLog row; silently swallows all errors. No situation text."""
    if session is None:
        return
    try:
        from sqlalchemy import select

        from ..db.models import APIKey, RequestLog

        api_key_id: int | None = None
        if api_key:
            import hashlib

            key_hash = hashlib.sha256(api_key.encode()).hexdigest()
            result = await session.execute(
                select(APIKey.id).where(APIKey.key_hash == key_hash)
            )
            row = result.scalar_one_or_none()
            if row:
                api_key_id = row

        log_row = RequestLog(
            api_key_id=api_key_id,
            city_slug=city_slug,
            urgency=urgency,
            ttft_ms=ttft_ms,
            total_ms=total_ms,
            cache_hit=cache_hit,
        )
        session.add(log_row)
        await session.commit()
    except Exception as exc:
        log.warning("RequestLog persistence failed (%s)", exc)


def create_app(*, use_mock: bool | None = None) -> FastAPI:
    """Create and return the FastAPI application."""
    if use_mock is None:
        use_mock = os.environ.get("EMERGENCY_AI_MOCK") == "1"

    cities = load_cities()

    if use_mock:
        provider = MockProvider()
    else:
        try:
            provider = AnthropicProvider()
        except RuntimeError as e:
            log.warning("anthropic provider unavailable: %s", e)
            provider = None  # type: ignore[assignment]

    client = EmergencyClient(provider, cities) if provider else None  # type: ignore[arg-type]

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        await create_all()
        yield

    app = FastAPI(title="emergency-ai", version="1.0.0", lifespan=lifespan)
    app.include_router(metrics_router)

    # CORS — allow all origins so the GitHub Pages PWA can call a deployed instance.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Core inference endpoints
    # ------------------------------------------------------------------

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "cities_loaded": len(cities),
            "model_ready": client is not None,
            "mock_mode": use_mock,
        }

    @app.post("/emergency")
    async def emergency(
        req: EmergencyRequest,
        request: Request,
        accept: Annotated[str | None, Header()] = None,
        api_key: str | None = Depends(require_api_key),
        session=Depends(get_session),
    ):
        # Rate limiting (skip when no key in no-auth mode)
        limiter = get_limiter()
        await check_rate_limit(api_key, limiter)

        if client is None:
            raise HTTPException(503, "Inference unavailable: ANTHROPIC_API_KEY not configured")

        request_id = uuid.uuid4().hex[:12]
        city_ctx = client.resolve(req.city)
        wants_stream = accept and "text/event-stream" in accept.lower()
        t0 = time.monotonic()

        async def event_source() -> AsyncIterator[bytes]:
            final: EmergencyResponse | None = None
            ttft_ms: int | None = None
            async for ev in client.stream(req):
                if ttft_ms is None and not ev.field.startswith("__"):
                    ttft_ms = int((time.monotonic() - t0) * 1000)
                if ev.field == "__final__":
                    final = ev.value
                    payload = {"event": "final", "data": final.model_dump()}
                    yield f"data: {json.dumps(payload)}\n\n".encode()
                elif ev.field == "__error__":
                    yield f"data: {json.dumps({'event': 'error', 'data': ev.value})}\n\n".encode()
                elif ev.field == "__latency_ms__":
                    continue
                else:
                    payload = {"event": "field", "field": ev.field, "data": ev.value}
                    yield f"data: {json.dumps(payload)}\n\n".encode()
            total_ms = int((time.monotonic() - t0) * 1000)
            urgency = final.urgency if final else "unknown"
            log.info(
                "request_id=%s city=%s urgency=%s ttft_ms=%s total_ms=%s mock=%s",
                request_id, city_ctx.slug, urgency, ttft_ms, total_ms, use_mock,
            )
            emergency_requests_total.labels(city=city_ctx.slug, urgency=urgency, status="ok").inc()
            if ttft_ms is not None:
                emergency_ttft_seconds.observe(ttft_ms / 1000)
            emergency_total_seconds.observe(total_ms / 1000)
            _spawn(
                _log_request(session, api_key, city_ctx.slug, urgency, ttft_ms, total_ms, False)
            )

        if wants_stream:
            return StreamingResponse(event_source(), media_type="text/event-stream")

        # Non-streaming: drain stream, return final JSON
        final: EmergencyResponse | None = None
        ttft_ms: int | None = None
        async for ev in client.stream(req):
            if ttft_ms is None and not ev.field.startswith("__"):
                ttft_ms = int((time.monotonic() - t0) * 1000)
            if ev.field == "__final__":
                final = ev.value
        total_ms = int((time.monotonic() - t0) * 1000)
        urgency = final.urgency if final else "unknown"
        log.info(
            "request_id=%s city=%s urgency=%s ttft_ms=%s total_ms=%s mock=%s",
            request_id, city_ctx.slug, urgency, ttft_ms, total_ms, use_mock,
        )
        if final is None:
            final = fallback_response(city_ctx.primary_emergency_number)

        emergency_requests_total.labels(city=city_ctx.slug, urgency=urgency, status="ok").inc()
        if ttft_ms is not None:
            emergency_ttft_seconds.observe(ttft_ms / 1000)
        emergency_total_seconds.observe(total_ms / 1000)
        _spawn(
            _log_request(session, api_key, city_ctx.slug, urgency, ttft_ms, total_ms, False)
        )

        return JSONResponse(
            content={
                **final.model_dump(),
                "_meta": {
                    "request_id": request_id,
                    "ttft_ms": ttft_ms,
                    "total_ms": total_ms,
                    "city_slug": city_ctx.slug,
                },
            }
        )

    # ------------------------------------------------------------------
    # v1.0 read-only endpoints (power the offline PWA; no API key required)
    # ------------------------------------------------------------------

    @app.get("/cities")
    async def list_cities_endpoint():
        """List all loaded cities with slug, display_name, country, and primary number."""
        result = [
            {
                "slug": ctx.slug,
                "display_name": ctx.display_name,
                "country": ctx.country,
                "primary": ctx.primary_emergency_number,
            }
            for slug, ctx in sorted(cities.items())
            if slug != "_unknown"
        ]
        return JSONResponse(content={"cities": result})

    @app.get("/cities/{slug}")
    async def get_city_endpoint(slug: str):
        """Return full city context for the given slug, or 404."""
        ctx = cities.get(slug)
        if ctx is None or slug == "_unknown":
            raise HTTPException(status_code=404, detail=f"City not found: {slug!r}")
        return JSONResponse(content={
            "slug": ctx.slug,
            "display_name": ctx.display_name,
            "country": ctx.country,
            "primary": ctx.primary_emergency_number,
            "aliases": list(ctx.aliases),
            "body": ctx.body,
        })

    @app.get("/scenarios")
    async def list_scenarios_endpoint():
        """Return all scenarios from the offline corpus."""
        try:
            from ..core import scenarios as _scenarios
            return JSONResponse(content={"scenarios": _scenarios.list_scenarios()})
        except Exception as exc:
            log.warning("scenarios load failed: %s", exc)
            return JSONResponse(content={"scenarios": []})

    @app.post("/triage", response_model=TriageResponse)
    async def triage_endpoint(req: TriageRequest):
        """Offline urgency classifier — no API key needed, always works.

        Privacy: situation text is processed in-memory only; never logged or stored.
        """
        from ..core import triage as _triage
        from ..core.cities import resolve_city

        city_ctx = (
            resolve_city(req.city, cities) if req.city
            else cities.get("new-york") or next(iter(cities.values()))
        )
        result = _triage.classify(req.situation, city_ctx)
        return TriageResponse(
            urgency=result.urgency,
            score=result.score,
            signals=result.signals,
            matched=result.matched,
        )

    @app.post("/geo/resolve", response_model=GeoResolveResponse)
    async def geo_resolve_endpoint(req: GeoResolveRequest):
        """Resolve (lat, lon) to the nearest city slug and display_name."""
        try:
            from ..core.geo import nearest_city as _nearest_city
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Geo module unavailable: {exc}") from exc
        ctx = _nearest_city(req.lat, req.lon, cities)
        return GeoResolveResponse(slug=ctx.slug, display_name=ctx.display_name)

    @app.get("/version")
    async def version_endpoint():
        """Return service name, version, and build metadata."""
        try:
            ver = importlib.metadata.version("emergency-ai")
        except importlib.metadata.PackageNotFoundError:
            ver = "1.0.0"
        return JSONResponse(content={"name": "emergency-ai", "version": ver, "build": "source"})

    return app


app = create_app()


def run() -> None:
    """Console-script entry point: ``emergency-server``."""
    import uvicorn

    uvicorn.run(
        "emergency_ai.api.server:app",
        host="0.0.0.0",
        port=int(os.environ.get("EMERGENCY_AI_PORT", os.environ.get("PORT", "8080"))),
        log_level="info",
    )
