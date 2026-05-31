"""FastAPI service. Supports JSON and Server-Sent Events responses."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..core.cities import load_cities
from ..core.client import AnthropicProvider, EmergencyClient, MockProvider
from ..core.schema import EmergencyRequest, EmergencyResponse, fallback_response

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
# Pydantic models for new endpoints
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


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(*, use_mock: bool | None = None) -> FastAPI:
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

    # Lazy singletons — created inside the async context so event loops are safe.
    _cache: object = None
    _store: object = None

    def _get_cache():
        nonlocal _cache
        if _cache is None:
            try:
                from ..core.cache import ResponseCache
                _cache = ResponseCache()
            except Exception:
                _cache = _NoopCache()
        return _cache

    def _get_store():
        nonlocal _store
        if _store is None:
            try:
                from ..core.store import IncidentStore
                _store = IncidentStore()
            except Exception:
                _store = _NoopStore()
        return _store

    app = FastAPI(title="emergency-ai", version="0.1.0")

    # CORS — allow all origins so the GitHub Pages PWA can call a deployed instance.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Existing endpoints (contract unchanged)
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
    ):
        if client is None:
            raise HTTPException(503, "Inference unavailable: ANTHROPIC_API_KEY not configured")

        request_id = uuid.uuid4().hex[:12]
        city_ctx = client.resolve(req.city)
        wants_stream = accept and "text/event-stream" in accept.lower()
        t0 = time.monotonic()

        # Import metrics lazily — never hard-fails.
        try:
            from ..core import metrics as _metrics
            _has_metrics = True
        except Exception:
            _has_metrics = False

        cache = _get_cache()
        store = _get_store()

        # Check cache first (non-streaming path only; streaming skips cache for UX).
        if not wants_stream:
            cached_hit = await _safe_cache_get(cache, city_ctx.slug, req.situation)
            if cached_hit is not None:
                total_ms = int((time.monotonic() - t0) * 1000)
                if _has_metrics:
                    _metrics.inc_request(city_ctx.slug, cached_hit.get("urgency", "unknown"), "cache")
                    _metrics.inc_cache_hit()
                # Fire-and-forget audit record.
                _spawn(_safe_store_record(store, {
                    "request_id": request_id,
                    "city": city_ctx.slug,
                    "urgency": cached_hit.get("urgency", "unknown"),
                    "ttft_ms": 0.0,
                    "total_ms": float(total_ms),
                    "source": "cache",
                    "cache_hit": True,
                }))
                return JSONResponse(
                    content={
                        **cached_hit,
                        "_meta": {
                            "request_id": request_id,
                            "ttft_ms": 0,
                            "total_ms": total_ms,
                            "city_slug": city_ctx.slug,
                            "cache_hit": True,
                        },
                    }
                )

        async def event_source() -> AsyncIterator[bytes]:
            final: EmergencyResponse | None = None
            ttft_ms: int | None = None
            source = "mock" if use_mock else "live"
            try:
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
                    request_id,
                    city_ctx.slug,
                    urgency,
                    ttft_ms,
                    total_ms,
                    use_mock,
                )
                if _has_metrics:
                    _metrics.inc_request(city_ctx.slug, urgency, source)
                    if ttft_ms is not None:
                        _metrics.observe_ttft(float(ttft_ms))
                _spawn(_safe_store_record(store, {
                    "request_id": request_id,
                    "city": city_ctx.slug,
                    "urgency": urgency,
                    "ttft_ms": float(ttft_ms) if ttft_ms is not None else None,
                    "total_ms": float(total_ms),
                    "source": source,
                    "cache_hit": False,
                }))
            except Exception:
                if _has_metrics:
                    _metrics.inc_error()
                raise

        if wants_stream:
            return StreamingResponse(event_source(), media_type="text/event-stream")

        # Non-streaming: drain stream, return final JSON
        final: EmergencyResponse | None = None
        ttft_ms: int | None = None
        source = "mock" if use_mock else "live"
        try:
            async for ev in client.stream(req):
                if ttft_ms is None and not ev.field.startswith("__"):
                    ttft_ms = int((time.monotonic() - t0) * 1000)
                if ev.field == "__final__":
                    final = ev.value
        except Exception:
            if _has_metrics:
                _metrics.inc_error()
            raise
        total_ms = int((time.monotonic() - t0) * 1000)
        urgency = final.urgency if final else "unknown"
        log.info(
            "request_id=%s city=%s urgency=%s ttft_ms=%s total_ms=%s mock=%s",
            request_id,
            city_ctx.slug,
            urgency,
            ttft_ms,
            total_ms,
            use_mock,
        )
        if final is None:
            final = fallback_response(city_ctx.primary_emergency_number)

        if _has_metrics:
            _metrics.inc_request(city_ctx.slug, urgency, source)
            if ttft_ms is not None:
                _metrics.observe_ttft(float(ttft_ms))

        result_dict = final.model_dump()

        # Cache the result for future identical requests.
        _spawn(_safe_cache_set(cache, city_ctx.slug, req.situation, result_dict))

        _spawn(_safe_store_record(store, {
            "request_id": request_id,
            "city": city_ctx.slug,
            "urgency": urgency,
            "ttft_ms": float(ttft_ms) if ttft_ms is not None else None,
            "total_ms": float(total_ms),
            "source": source,
            "cache_hit": False,
        }))

        return JSONResponse(
            content={
                **result_dict,
                "_meta": {
                    "request_id": request_id,
                    "ttft_ms": ttft_ms,
                    "total_ms": total_ms,
                    "city_slug": city_ctx.slug,
                    "cache_hit": False,
                },
            }
        )

    # ------------------------------------------------------------------
    # New endpoints
    # ------------------------------------------------------------------

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics_endpoint():
        """Prometheus text-format exposition of service metrics."""
        try:
            from ..core import metrics as _metrics
            return PlainTextResponse(
                content=_metrics.render(),
                media_type="text/plain; version=0.0.4; charset=utf-8",
            )
        except Exception as exc:
            log.warning("metrics render failed: %s", exc)
            return PlainTextResponse(content="# metrics unavailable\n", status_code=200)

    @app.get("/cities")
    async def list_cities_endpoint():
        """List all loaded cities with slug, display_name, country, and primary number."""
        result = []
        for slug, ctx in sorted(cities.items()):
            if slug == "_unknown":
                continue
            result.append({
                "slug": ctx.slug,
                "display_name": ctx.display_name,
                "country": ctx.country,
                "primary": ctx.primary_emergency_number,
            })
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
        """Return all scenarios from the corpus."""
        try:
            from ..core import scenarios as _scenarios
            return JSONResponse(content={"scenarios": _scenarios.list_scenarios()})
        except Exception as exc:
            log.warning("scenarios load failed: %s", exc)
            return JSONResponse(content={"scenarios": []})

    @app.post("/triage", response_model=TriageResponse)
    async def triage_endpoint(req: TriageRequest):
        """Offline urgency classifier — no API key needed, always works.

        Privacy: situation text is processed in-memory only and never logged or stored.
        """
        from ..core import triage as _triage
        from ..core.cities import resolve_city

        city_ctx = resolve_city(req.city, cities) if req.city else \
            cities.get("new-york") or next(iter(cities.values()))

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
            ver = "0.1.0"
        return JSONResponse(content={
            "name": "emergency-ai",
            "version": ver,
            "build": "source",
        })

    return app


# ---------------------------------------------------------------------------
# Noop fallbacks for optional infra (cache + store)
# ---------------------------------------------------------------------------

class _NoopCache:
    async def initialize(self) -> None:
        pass

    async def get(self, city_slug: str, situation: str) -> dict | None:
        return None

    async def set(self, city_slug: str, situation: str, value: dict, ttl: int = 300) -> None:
        pass


class _NoopStore:
    async def record(self, event: dict) -> None:
        pass


# ---------------------------------------------------------------------------
# Helper coroutines — all failures are non-fatal (logged, swallowed)
# ---------------------------------------------------------------------------

async def _safe_cache_get(cache: object, city_slug: str, situation: str) -> dict | None:
    try:
        if hasattr(cache, "initialize"):
            await cache.initialize()  # type: ignore[union-attr]
        return await cache.get(city_slug, situation)  # type: ignore[union-attr]
    except Exception as exc:
        log.debug("cache.get failed (non-fatal): %s", exc)
        return None


async def _safe_cache_set(cache: object, city_slug: str, situation: str, value: dict) -> None:
    try:
        if hasattr(cache, "initialize"):
            await cache.initialize()  # type: ignore[union-attr]
        await cache.set(city_slug, situation, value)  # type: ignore[union-attr]
    except Exception as exc:
        log.debug("cache.set failed (non-fatal): %s", exc)


async def _safe_store_record(store: object, event: dict) -> None:
    try:
        await store.record(event)  # type: ignore[union-attr]
    except Exception as exc:
        log.debug("store.record failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Module-level app and entry point
# ---------------------------------------------------------------------------

app = create_app()


def run() -> None:
    """Console-script entry point: `emergency-server`."""
    import uvicorn

    uvicorn.run(
        "emergency_ai.api.server:app",
        host="0.0.0.0",
        port=int(os.environ.get("EMERGENCY_AI_PORT", "8080")),
        log_level="info",
    )
