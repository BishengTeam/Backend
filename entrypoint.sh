#!/bin/sh
set -e

echo "==> Running database migrations..."
alembic upgrade head

echo "==> Seeding default data..."
cd /app && PYTHONPATH=/app python scripts/seed_admin.py || true

echo "==> Starting application..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
