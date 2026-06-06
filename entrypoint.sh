#!/bin/sh
set -e

echo "==> Running database migrations..."
# 修复 DB 卷持久化导致的重复建表问题
python3 /app/scripts/_stamp_db.py || true

alembic upgrade head

echo "==> Seeding default data..."
cd /app && PYTHONPATH=/app python scripts/seed_admin.py || true

echo "==> Starting application..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
