# ============================================================
# ContextSynapse — Multi-stage Docker build
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

# System deps for compiled wheels (numpy, scikit-learn, lxml, psycopg2)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev \
        libxslt1-dev \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd -r contextsynapse && useradd -r -g contextsynapse -m contextsynapse

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY pyproject.toml ./
COPY requirements/ ./requirements/
# Production dependencies only (no torch, ray, GNN etc.)
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements/requirements-prod.txt

# Copy backend source
COPY contextsynapse/ ./contextsynapse/
COPY plugins/ ./plugins/
COPY verticals/ ./verticals/
COPY skills/ ./skills/
COPY config/ ./config/
COPY scripts/ ./scripts/

# Copy built frontend assets
COPY --from=frontend-build /app/frontend/build ./frontend/build

# Startup script
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

# Data directory (mount as volume in production)
RUN mkdir -p /app/contextsynapse_data && chown -R contextsynapse:contextsynapse /app
VOLUME ["/app/contextsynapse_data"]

# Switch to non-root
USER contextsynapse

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

# Workers configurable via WORKERS env var (default: 4)
ENV WORKERS=4
ENTRYPOINT ["./docker-entrypoint.sh"]
