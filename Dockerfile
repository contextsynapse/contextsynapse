# ============================================================
# AIContextDB — Multi-stage Docker build
# ============================================================
# Stage 1: Build React frontend
# Stage 2: Slim Python runtime with backend + static assets
# ============================================================

# ── Stage 1: Frontend build ──────────────────────────────────
FROM node:20-alpine AS frontend-build

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python runtime ─────────────────────────────────
FROM python:3.11-slim AS runtime

# System deps for compiled wheels (numpy, scikit-learn, lxml)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev \
        libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd -r contextsynapse && useradd -r -g contextsynapse -m contextsynapse

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY pyproject.toml ./
COPY requirements/ ./requirements/
RUN pip install --no-cache-dir -e ".[prod]"

# Copy backend source
COPY contextsynapse/ ./contextsynapse/
COPY scripts/ ./scripts/
COPY config/ ./config/
COPY pytest.ini ./

# Copy built frontend assets
COPY --from=frontend-build /app/frontend/build ./frontend/build

# Data directory (mount as volume in production)
RUN mkdir -p /app/contextsynapse_data && chown -R contextsynapse:contextsynapse /app/contextsynapse_data
VOLUME ["/app/contextsynapse_data"]

# Switch to non-root
USER contextsynapse

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import httpx; r = httpx.get('http://localhost:8000/health'); r.raise_for_status()"

EXPOSE 8000

# Workers configurable via WORKERS env var (default: 4)
ENV WORKERS=4
CMD ["sh", "-c", "uvicorn contextsynapse.api.api:app --host 0.0.0.0 --port 8000 --workers $WORKERS"]
