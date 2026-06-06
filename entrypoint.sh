#!/bin/sh
set -e

echo "==> Running database migrations..."
# 首次运行：若 DB 有表但无迁移记录，先 stamp 再升级
python3 /app/scripts/_stamp_db.py || true

# 尝试迁移；若 DB 卷持久化导致重复 DDL 错误，stamp 后重试
alembic upgrade head || {
  echo "Migration failed — stamping head and retrying..."
  alembic stamp head 2>/dev/null || true
  alembic upgrade head
}

echo "==> Seeding default data..."
cd /app && PYTHONPATH=/app python scripts/seed_admin.py || true

echo "==> Starting application..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
