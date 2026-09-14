#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# ContextSynapse — Quick Setup
# ═══════════════════════════════════════════════════════════════
# Usage: ./scripts/setup.sh
#
# Starts PostgreSQL + Redis, runs migrations, seeds demo data,
# and launches the API server.
#
# Requirements: Docker, Python 3.11+
# ═══════════════════════════════════════════════════════════════

set -e
cd "$(dirname "$0")/.."

echo ""
echo "  ContextSynapse — Quick Setup"
echo "  ============================"
echo ""

# 1. Start infrastructure
echo "[1/4] Starting PostgreSQL + Redis..."
docker compose up -d postgres redis 2>/dev/null || docker-compose up -d postgres redis 2>/dev/null
sleep 5

for i in $(seq 1 15); do
  docker exec contextsynapse-postgres-1 pg_isready -U contextsynapse > /dev/null 2>&1 && break
  echo "  Waiting for PostgreSQL ($i)..."
  sleep 2
done

export DATABASE_URL="postgresql://contextsynapse:contextsynapse@localhost:5432/contextsynapse"

# 2. Install dependencies
echo "[2/4] Installing dependencies..."
pip install -e "." --quiet 2>/dev/null

# 3. Seed demo data
echo "[3/4] Seeding demo data..."
python scripts/seed_demo.py

# 4. Start server
echo "[4/4] Starting server..."
nohup python -m uvicorn contextsynapse.api.api:app --host 0.0.0.0 --port 8000 > /tmp/contextsynapse.log 2>&1 &

echo ""
echo "  Setup complete!"
echo "  API:    http://localhost:8000/health"
echo "  Docs:   http://localhost:8000/docs"
echo "  Demo:   python scripts/hero_demo.py"
echo ""
