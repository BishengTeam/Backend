#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd -- "$BACKEND_ROOT/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$BACKEND_ROOT/.venv/bin/python}"
NPM_BIN="${NPM_BIN:-npm}"

if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python3}"
fi

fail() {
    printf 'quality gate failed: %s\n' "$1" >&2
    exit 1
}

run_backend() {
    printf '\n== Backend ==\n'
    "$PYTHON_BIN" -m compileall -q "$BACKEND_ROOT/app" "$BACKEND_ROOT/alembic" "$BACKEND_ROOT/tests"
    (cd "$BACKEND_ROOT" && "$PYTHON_BIN" -m pytest tests/unit -q)
    (cd "$BACKEND_ROOT" && "$PYTHON_BIN" scripts/check_renshe_contract.py)
    (cd "$BACKEND_ROOT" && "$PYTHON_BIN" scripts/check_migrations.py --offline-sql)
}

run_admin() {
    local admin_root="$REPO_ROOT/Admin"
    [[ -x "$admin_root/node_modules/.bin/vitest" ]] || fail "Admin dependencies are not installed"
    printf '\n== Admin ==\n'
    (cd "$admin_root" && "$NPM_BIN" test && "$NPM_BIN" run build)
}

run_platform() {
    local platform_root="$REPO_ROOT/Platform"
    [[ -x "$platform_root/node_modules/.bin/taro" ]] || fail "Platform dependencies are not installed"
    printf '\n== Platform ==\n'
    (cd "$platform_root" && "$NPM_BIN" run build:weapp)
}

case "${1:-backend}" in
    backend)
        run_backend
        ;;
    admin)
        run_admin
        ;;
    platform)
        run_platform
        ;;
    all)
        run_backend
        run_admin
        run_platform
        ;;
    *)
        printf 'usage: %s [backend|admin|platform|all]\n' "$0" >&2
        exit 2
        ;;
esac

printf '\nquality gate passed: %s\n' "${1:-backend}"
