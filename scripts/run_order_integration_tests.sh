#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_ROOT="$(cd "${REPO_ROOT}/.." && pwd)"

if [[ "${1:-}" == "--help" ]]; then
    cat <<'EOF'
Usage: scripts/run_order_integration_tests.sh [pytest arguments]

Creates or reuses a dedicated PostgreSQL test database, applies Alembic
migrations, and runs the order/plan/inventory/course/human-resources integration tests.

Optional environment variables:
  ENV_FILE       Environment file to load. Defaults to ../.env, then ./.env.
  TEST_DB_NAME   Test database name. Defaults to wemini_app_test.

The test database name must contain "test". Development/production databases
are rejected.
EOF
    exit 0
fi

PYTHON="${REPO_ROOT}/.venv/bin/python"
ALEMBIC="${REPO_ROOT}/.venv/bin/alembic"

if [[ ! -x "${PYTHON}" || ! -x "${ALEMBIC}" ]]; then
    echo "Error: Backend/.venv is missing python or alembic." >&2
    exit 1
fi

if [[ -n "${ENV_FILE:-}" ]]; then
    ENV_PATH="${ENV_FILE}"
elif [[ -f "${WORKSPACE_ROOT}/.env" ]]; then
    ENV_PATH="${WORKSPACE_ROOT}/.env"
elif [[ -f "${REPO_ROOT}/.env" ]]; then
    ENV_PATH="${REPO_ROOT}/.env"
else
    echo "Error: no .env file found. Set ENV_FILE explicitly." >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "${ENV_PATH}"
set +a

SOURCE_ASYNC_URL="${DATABASE_URL:-}"
SOURCE_SYNC_URL="${DATABASE_URL_SYNC:-}"
TEST_DB_NAME="${TEST_DB_NAME:-wemini_app_test}"

if [[ -z "${SOURCE_ASYNC_URL}" || -z "${SOURCE_SYNC_URL}" ]]; then
    echo "Error: DATABASE_URL and DATABASE_URL_SYNC are required." >&2
    exit 1
fi

if [[ ! "${TEST_DB_NAME}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    echo "Error: TEST_DB_NAME contains unsupported characters." >&2
    exit 1
fi

if [[ "${TEST_DB_NAME,,}" != *test* ]]; then
    echo "Error: TEST_DB_NAME must contain 'test'. Refusing to continue." >&2
    exit 1
fi

eval "$(
    "${PYTHON}" - "${SOURCE_SYNC_URL}" "${TEST_DB_NAME}" <<'PY'
import shlex
import sys
from urllib.parse import unquote, urlsplit

source_url, test_db_name = sys.argv[1:]
parsed = urlsplit(source_url)

values = {
    "PGHOST": unquote(parsed.hostname) if parsed.hostname else "/var/run/postgresql",
    "PGPORT": str(parsed.port or 5432),
    "PGUSER": unquote(parsed.username) if parsed.username else "",
    "PGPASSWORD": unquote(parsed.password) if parsed.password else "",
    "PGDATABASE": test_db_name,
}

for key, value in values.items():
    print(f"export {key}={shlex.quote(value)}")
PY
)"

if ! command -v psql >/dev/null 2>&1 || ! command -v createdb >/dev/null 2>&1; then
    echo "Error: psql and createdb must be installed on the host." >&2
    exit 1
fi

echo "Using PostgreSQL test database: ${TEST_DB_NAME}"
echo "Connection target: ${PGHOST}:${PGPORT} user=${PGUSER}"

CURRENT_OS_USER="$(id -un)"
if [[ "${PGHOST}" == /* && -z "${PGPASSWORD}" && "${CURRENT_OS_USER}" != "${PGUSER}" ]]; then
    echo "Error: PostgreSQL Peer authentication requires the OS user to match PGUSER." >&2
    echo "Current OS user: ${CURRENT_OS_USER}; required user: ${PGUSER}." >&2
    echo "Run this script without sudo from the ${PGUSER} account." >&2
    exit 1
fi

DATABASE_EXISTS="$(
    psql \
        --dbname=postgres \
        --tuples-only \
        --no-align \
        --command="SELECT 1 FROM pg_database WHERE datname = '${TEST_DB_NAME}';"
)"

if [[ "${DATABASE_EXISTS}" != "1" ]]; then
    echo "Creating test database ${TEST_DB_NAME}..."
    createdb --maintenance-db=postgres "${TEST_DB_NAME}"
fi

TEST_DATABASE_URL="$(
    "${PYTHON}" - "${SOURCE_ASYNC_URL}" "${TEST_DB_NAME}" <<'PY'
import sys
from sqlalchemy.engine import make_url

source_url, test_db_name = sys.argv[1:]
print(make_url(source_url).set(database=test_db_name).render_as_string(hide_password=False))
PY
)"

TEST_DATABASE_URL_SYNC="$(
    "${PYTHON}" - "${SOURCE_SYNC_URL}" "${TEST_DB_NAME}" <<'PY'
import sys
from sqlalchemy.engine import make_url

source_url, test_db_name = sys.argv[1:]
print(make_url(source_url).set(database=test_db_name).render_as_string(hide_password=False))
PY
)"

export TEST_DATABASE_URL
export TEST_DATABASE_URL_SYNC
export DATABASE_URL="${TEST_DATABASE_URL}"
export DATABASE_URL_SYNC="${TEST_DATABASE_URL_SYNC}"
export JWT_SECRET="${JWT_SECRET:-integration-test-jwt-secret-minimum-32-characters}"
export APP_ENV="test"
export APP_DEBUG="false"

# Never let integration tests call a real payment provider.
export WECHAT_PAY_ENABLED="false"
export WECHAT_PAY_MCHID=""
export WECHAT_PAY_APPID=""
export WECHAT_PAY_NOTIFY_URL=""
export WECHAT_PAY_REFUND_NOTIFY_URL=""

cd "${REPO_ROOT}"

echo "Applying Alembic migrations..."
"${ALEMBIC}" upgrade head

echo "Running order, plan, inventory, course, and human-resources integration tests..."
"${PYTHON}" -m pytest \
    tests/integration/db/test_inventory_lifecycle_flow.py \
    tests/integration/db/test_h3c_plan_order_flow.py \
    tests/integration/db/test_plan_order_management_flow.py \
    tests/integration/db/test_plan_flow.py \
    tests/integration/db/test_course_purchase_flow.py \
    tests/integration/db/test_renshe_domain.py \
    -q \
    "$@"
