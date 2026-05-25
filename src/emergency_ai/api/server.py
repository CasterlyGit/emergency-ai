"""FastAPI service. Supports JSON and Server-Sent Events responses.

v0.2 additions:
    - API key auth (X-Api-Key header) via require_api_key dependency
    - Sliding-window rate limiting via Redis
    - Prometheus metrics (/metrics endpoint)
    - Async RequestLog persistence (fire-and-forget)
    - Graceful degradation: auth/rate-limit/DB failures never break the
      core inference path when EMERGENCY_AI_NO_AUTH=1 or infra is down
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..core.cities import load_cities
from ..core.client import AnthropicProvider, EmergencyClient, MockProvider
from ..core.schema import EmergencyRequest, EmergencyResponse, fallback_response
from ..db.session import create_all, get_session
from .metrics import (
    cache_hits_total,
    emergency_requests_total,
    emergency_total_seconds,
    emergency_ttft_seconds,
    router as metrics_router,
)
from .middleware import check_rate_limit, get_limiter, require_api_key

log = logging.getLogger("emergency_ai")
log.setLevel(logging.INFO)
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_handler)


async def _log_request(
    session,
    api_key: Optional[str],
    city_slug: str,
    urgency: str,
    ttft_ms: Optional[int],
    total_ms: Optional[int],
    cache_hit: bool,
) -> None:
    """Persist a RequestLog row; silently swallows all errors."""
    if session is None:
        return
    try:
        from sqlalchemy import select  # noqa: PLC0415

        from ..db.models import APIKey, RequestLog  # noqa: PLC0415

        api_key_id: Optional[int] = None
        if api_key:
            import hashlib  # noqa: PLC0415

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
    except Exception as exc:  # noqa: BLE001
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

    app = FastAPI(title="emergency-ai", version="0.2.0", lifespan=lifespan)
    app.include_router(metrics_router)

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
        api_key: Optional[str] = Depends(require_api_key),
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
                request_id,
                city_ctx.slug,
                urgency,
                ttft_ms,
                total_ms,
                use_mock,
            )
            # Metrics
            emergency_requests_total.labels(
                city=city_ctx.slug, urgency=urgency, status="ok"
            ).inc()
            if ttft_ms is not None:
                emergency_ttft_seconds.observe(ttft_ms / 1000)
            emergency_total_seconds.observe(total_ms / 1000)
            # Async DB log (fire-and-forget)
            asyncio.ensure_future(
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
            request_id,
            city_ctx.slug,
            urgency,
            ttft_ms,
            total_ms,
            use_mock,
        )
        if final is None:
            final = fallback_response(city_ctx.primary_emergency_number)

        # Metrics
        emergency_requests_total.labels(city=city_ctx.slug, urgency=urgency, status="ok").inc()
        if ttft_ms is not None:
            emergency_ttft_seconds.observe(ttft_ms / 1000)
        emergency_total_seconds.observe(total_ms / 1000)

        # Fire-and-forget DB log
        asyncio.ensure_future(
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
