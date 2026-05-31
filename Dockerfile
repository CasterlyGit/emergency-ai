# ── Stage 1: builder ────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# System deps needed only for building (e.g. asyncpg needs gcc + libpq-dev)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy only what pip needs to resolve the package
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install into a prefix we can copy cleanly into the runtime image
RUN pip install --upgrade pip && \
    pip install --prefix=/install "."

# ── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Minimal runtime system libs for asyncpg (libpq) and curl (healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --no-create-home --shell /usr/sbin/nologin appuser

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY --chown=appuser:appgroup src/ /app/src/

WORKDIR /app

USER appuser

EXPOSE 8080

# Healthcheck: prefer curl; fall back to Python if curl unavailable in a stripped image
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -fsSL http://localhost:8080/health || \
        python -c "import urllib.request, sys; r=urllib.request.urlopen('http://localhost:8080/health', timeout=8); sys.exit(0 if r.status==200 else 1)"

# emergency-server is registered in pyproject.toml [project.scripts]
CMD ["emergency-server"]
