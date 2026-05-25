# ── Stage 1: builder ───────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --upgrade pip && \
    pip install --no-cache-dir -e "." && \
    pip install --no-cache-dir \
        sqlalchemy[asyncio]>=2.0 \
        asyncpg>=0.29 \
        alembic>=1.13 \
        "redis[asyncio]>=5.0" \
        pgvector>=0.3 \
        "prometheus-client>=0.20" \
        "python-dotenv>=1.0"

# ── Stage 2: runtime ───────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Non-root user
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Copy installed packages and source from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /build/src ./src
COPY pyproject.toml ./

USER appuser

EXPOSE 8080

ENTRYPOINT ["emergency-server"]
