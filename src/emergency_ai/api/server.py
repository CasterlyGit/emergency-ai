"""FastAPI service. Supports JSON and Server-Sent Events responses."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..core.cities import load_cities
from ..core.client import AnthropicProvider, EmergencyClient, MockProvider
from ..core.schema import EmergencyRequest, EmergencyResponse, fallback_response

log = logging.getLogger("emergency_ai")
log.setLevel(logging.INFO)
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_handler)


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

    app = FastAPI(title="emergency-ai", version="0.1.0")

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
            log.info(
                "request_id=%s city=%s urgency=%s ttft_ms=%s total_ms=%s mock=%s",
                request_id,
                city_ctx.slug,
                final.urgency if final else "unknown",
                ttft_ms,
                total_ms,
                use_mock,
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
        log.info(
            "request_id=%s city=%s urgency=%s ttft_ms=%s total_ms=%s mock=%s",
            request_id,
            city_ctx.slug,
            final.urgency if final else "unknown",
            ttft_ms,
            total_ms,
            use_mock,
        )
        if final is None:
            final = fallback_response(city_ctx.primary_emergency_number)
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
    """Console-script entry point: `emergency-server`."""
    import uvicorn

    uvicorn.run(
        "emergency_ai.api.server:app",
        host="0.0.0.0",
        port=int(os.environ.get("EMERGENCY_AI_PORT", "8080")),
        log_level="info",
    )
