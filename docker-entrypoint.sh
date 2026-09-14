#!/bin/sh
set -e

echo "=== ContextSynapse Starting ==="
echo "Environment: ${AICONTEXTDB_ENV:-production}"
echo "Workers: ${WORKERS:-4}"

# Run database migrations
echo "Running database migrations..."
python -c "
from contextsynapse.db.postgres import run_migrations
run_migrations()
print('Migrations complete')
"

# Load stock universe if table is empty
echo "Checking stock master data..."
python -c "
from contextsynapse.db.stock_master import StockMaster
sm = StockMaster()
count = sm.count()
if count == 0:
    print(f'Stock master empty — loading NSE universe...')
    loaded = sm.load_nse_listing()
    print(f'Loaded {loaded} stocks')
else:
    print(f'Stock master: {count} stocks already loaded')
"

echo "Starting uvicorn..."
exec uvicorn contextsynapse.api.api:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers "${WORKERS:-4}" \
    --log-level "${AICONTEXTDB_LOG_LEVEL:-info}"
