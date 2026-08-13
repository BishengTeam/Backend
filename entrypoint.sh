#!/bin/sh
set -e

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "==> Running database migrations..."
  alembic upgrade head
fi

echo "==> Starting application..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
