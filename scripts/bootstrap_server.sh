#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd -- "$BACKEND_DIR/.." && pwd)"
ADMIN_DIR="${ADMIN_DIR:-$PROJECT_ROOT/Admin}"
RENSHE_TEMPLATE_HOST_DIR="${RENSHE_TEMPLATE_HOST_DIR:-$PROJECT_ROOT/docs/renshe}"
BOOTSTRAP_HOST_DEPLOY_ROOT="${BOOTSTRAP_HOST_DEPLOY_ROOT:-/srv/wemini-bootstrap}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-wemini}"
BOOTSTRAP_PORT="${BOOTSTRAP_PORT:-18080}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
ADMIN_PORT="${ADMIN_PORT:-8080}"

CONTROL_DIR="$BOOTSTRAP_HOST_DEPLOY_ROOT/control"
TOKEN_FILE="$CONTROL_DIR/bootstrap_token"
SOURCE_PINS_FILE="$CONTROL_DIR/source-pins.env"
RUNTIME_ENV="$BOOTSTRAP_HOST_DEPLOY_ROOT/installation/runtime.env"
RELEASE_ENV="$CONTROL_DIR/release.env"
BOOTSTRAP_COMPOSE_FILE="$BACKEND_DIR/docker-compose.bootstrap.yml"
RUNTIME_COMPOSE_FILE="$BACKEND_DIR/docker-compose.deploy.yml"

BOOTSTRAP_UID="$(id -u)"
BOOTSTRAP_GID="$(id -g)"

log() {
  printf '[bootstrap] %s\n' "$*"
}

fail() {
  printf '[bootstrap] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
}

assert_safe_absolute_path() {
  local candidate="$1"
  local name="$2"
  [[ "$candidate" = /* ]] || fail "$name must be an absolute path"
  [[ "$candidate" != "/" ]] || fail "$name must not be /"
  [[ "$candidate" != *$'\n'* && "$candidate" != *$'\r'* ]] \
    || fail "$name contains an invalid newline"
  [[ "$candidate" != *' '* && "$candidate" != *$'\t'* ]] \
    || fail "$name must not contain whitespace"
}

check_repository() {
  local directory="$1"
  local name="$2"
  git -C "$directory" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || fail "$name is not a Git worktree: $directory"
  if [[ -n "$(git -C "$directory" status --porcelain --untracked-files=normal)" ]]; then
    fail "$name worktree is not clean; deployment will not overwrite local changes"
  fi
  local remote
  remote="$(git -C "$directory" remote get-url origin)"
  [[ "$remote" != *://*@* ]] \
    || fail "$name origin contains URL credentials; use an SSH remote or credential helper"
}

warn_resources() {
  local cpu_count memory_kib disk_kib
  cpu_count="$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '0')"
  memory_kib="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || printf '0')"
  disk_kib="$(df -Pk "$BOOTSTRAP_HOST_DEPLOY_ROOT" | awk 'NR == 2 {print $4}')"
  if (( cpu_count < 4 )); then
    log "WARNING: only $cpu_count CPU cores detected; build and runtime may be slow"
  fi
  if (( memory_kib < 8 * 1024 * 1024 )); then
    log "WARNING: less than 8 GiB memory detected"
  fi
  if (( disk_kib < 100 * 1024 * 1024 )); then
    log "WARNING: less than 100 GiB free disk detected"
  fi
}

preflight() {
  for command in docker git curl openssl sha256sum awk sed grep seq stat install; do
    require_command "$command"
  done
  docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable"
  docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is unavailable"
  assert_safe_absolute_path "$BACKEND_DIR" BACKEND_DIR
  assert_safe_absolute_path "$ADMIN_DIR" ADMIN_DIR
  assert_safe_absolute_path "$BOOTSTRAP_HOST_DEPLOY_ROOT" BOOTSTRAP_HOST_DEPLOY_ROOT
  assert_safe_absolute_path "$RENSHE_TEMPLATE_HOST_DIR" RENSHE_TEMPLATE_HOST_DIR
  [[ "$BOOTSTRAP_PORT" =~ ^[0-9]+$ && "$BOOTSTRAP_PORT" -ge 1 && "$BOOTSTRAP_PORT" -le 65535 ]] \
    || fail "BOOTSTRAP_PORT is invalid"
  [[ "$BACKEND_PORT" =~ ^[0-9]+$ && "$BACKEND_PORT" -ge 1 && "$BACKEND_PORT" -le 65535 ]] \
    || fail "BACKEND_PORT is invalid"
  [[ "$ADMIN_PORT" =~ ^[0-9]+$ && "$ADMIN_PORT" -ge 1 && "$ADMIN_PORT" -le 65535 ]] \
    || fail "ADMIN_PORT is invalid"
  [[ "$BOOTSTRAP_PORT" != "$BACKEND_PORT" && "$BOOTSTRAP_PORT" != "$ADMIN_PORT" && "$BACKEND_PORT" != "$ADMIN_PORT" ]] \
    || fail "bootstrap, Backend and Admin ports must be different"
  [[ -f "$RENSHE_TEMPLATE_HOST_DIR/报名信息.xlsx" ]] \
    || fail "missing official template: 报名信息.xlsx"
  [[ -f "$RENSHE_TEMPLATE_HOST_DIR/工作经历.xlsx" ]] \
    || fail "missing official template: 工作经历.xlsx"
  check_repository "$BACKEND_DIR" Backend
  check_repository "$ADMIN_DIR" Admin

  install -d -m 0700 "$BOOTSTRAP_HOST_DEPLOY_ROOT" "$CONTROL_DIR"
  chmod 0700 "$BOOTSTRAP_HOST_DEPLOY_ROOT" "$CONTROL_DIR"
  if command -v timedatectl >/dev/null 2>&1; then
    local synchronized
    synchronized="$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)"
    [[ "$synchronized" != "no" ]] || fail "system clock is not synchronized"
  fi
  warn_resources
}

pin_sources_once() {
  if [[ -f "$SOURCE_PINS_FILE" ]]; then
    # This file contains only two lowercase hexadecimal commit IDs generated
    # below. Validate every line before sourcing it.
    grep -Eq '^BACKEND_COMMIT=[0-9a-f]{40,64}$' "$SOURCE_PINS_FILE" \
      || fail "saved Backend source pin is invalid"
    grep -Eq '^ADMIN_COMMIT=[0-9a-f]{40,64}$' "$SOURCE_PINS_FILE" \
      || fail "saved Admin source pin is invalid"
    # shellcheck disable=SC1090
    source "$SOURCE_PINS_FILE"
  else
    log "fetching Backend origin/main once"
    git -C "$BACKEND_DIR" fetch --prune origin \
      refs/heads/main:refs/remotes/origin/main
    BACKEND_COMMIT="$(git -C "$BACKEND_DIR" rev-parse refs/remotes/origin/main)"
    log "fetching Admin origin/main once"
    git -C "$ADMIN_DIR" fetch --prune origin \
      refs/heads/main:refs/remotes/origin/main
    ADMIN_COMMIT="$(git -C "$ADMIN_DIR" rev-parse refs/remotes/origin/main)"
    {
      printf 'BACKEND_COMMIT=%s\n' "$BACKEND_COMMIT"
      printf 'ADMIN_COMMIT=%s\n' "$ADMIN_COMMIT"
    } >"$SOURCE_PINS_FILE"
    chmod 0600 "$SOURCE_PINS_FILE"
  fi
  git -C "$BACKEND_DIR" cat-file -e "$BACKEND_COMMIT^{commit}" \
    || fail "pinned Backend commit is unavailable"
  git -C "$ADMIN_DIR" cat-file -e "$ADMIN_COMMIT^{commit}" \
    || fail "pinned Admin commit is unavailable"
  git -C "$BACKEND_DIR" checkout --detach "$BACKEND_COMMIT"
  git -C "$ADMIN_DIR" checkout --detach "$ADMIN_COMMIT"
  BACKEND_IMAGE="wemini-backend:${BACKEND_COMMIT:0:12}"
  ADMIN_IMAGE="wemini-admin:${ADMIN_COMMIT:0:12}"
  export BACKEND_COMMIT ADMIN_COMMIT BACKEND_IMAGE ADMIN_IMAGE
}

ensure_token() {
  if [[ ! -f "$TOKEN_FILE" ]]; then
    openssl rand -hex 32 >"$TOKEN_FILE"
    chmod 0600 "$TOKEN_FILE"
  fi
  [[ ! -L "$TOKEN_FILE" && -f "$TOKEN_FILE" ]] || fail "bootstrap token path is unsafe"
  [[ "$(stat -c '%a' "$TOKEN_FILE")" = "600" ]] \
    || fail "bootstrap token permissions must be 0600"
  BOOTSTRAP_TOKEN="$(<"$TOKEN_FILE")"
  [[ "$BOOTSTRAP_TOKEN" =~ ^[0-9a-f]{64}$ ]] || fail "bootstrap token is invalid"
  export BOOTSTRAP_TOKEN
}

bootstrap_compose() {
  env \
    BOOTSTRAP_UID="$BOOTSTRAP_UID" \
    BOOTSTRAP_GID="$BOOTSTRAP_GID" \
    BOOTSTRAP_HOST_DEPLOY_ROOT="$BOOTSTRAP_HOST_DEPLOY_ROOT" \
    BOOTSTRAP_PORT="$BOOTSTRAP_PORT" \
    BOOTSTRAP_IMAGE="$BACKEND_IMAGE" \
    docker compose \
      --project-name "$COMPOSE_PROJECT_NAME" \
      --file "$BOOTSTRAP_COMPOSE_FILE" \
      "$@"
}

runtime_compose() {
  [[ -f "$RUNTIME_ENV" && -f "$RELEASE_ENV" ]] \
    || fail "runtime environment is not ready"
  docker compose \
    --project-name "$COMPOSE_PROJECT_NAME" \
    --env-file "$RUNTIME_ENV" \
    --env-file "$RELEASE_ENV" \
    --file "$RUNTIME_COMPOSE_FILE" \
    "$@"
}

start_bootstrap() {
  log "building and starting loopback-only bootstrap service"
  bootstrap_compose up -d --build bootstrap
  local attempt
  for attempt in $(seq 1 60); do
    if curl --fail --silent --show-error \
      "http://127.0.0.1:${BOOTSTRAP_PORT}/healthz" >/dev/null; then
      return
    fi
    sleep 1
  done
  fail "bootstrap service did not become healthy"
}

bootstrap_cli() {
  bootstrap_compose exec -T bootstrap python -m bootstrap_app.cli "$@"
}

state_json() {
  bootstrap_cli status
}

state_phase() {
  state_json | sed -n 's/.*"phase": "\([A-Z_]*\)".*/\1/p'
}

state_installation_id() {
  state_json | sed -n 's/.*"installation_id": "\([0-9a-f]*\)".*/\1/p'
}

state_has_failure() {
  ! state_json | grep -q '"last_failure": null'
}

record_failure() {
  local code="$1"
  local stage="$2"
  bootstrap_cli failure --code "$code" --stage "$stage" >/dev/null \
    || log "WARNING: unable to persist failure state"
}

wait_for_retry() {
  log "fix the reported problem, then click ‘重试当前步骤’ in the setup page"
  while state_has_failure; do
    sleep 3
  done
}

run_with_web_retry() {
  local stage="$1"
  shift
  while true; do
    if "$@"; then
      return
    fi
    record_failure "${stage}_failed" "$stage"
    wait_for_retry
  done
}

wait_for_phase_change() {
  local current="$1"
  while [[ "$(state_phase)" = "$current" ]]; do
    sleep 3
  done
}

cleanup_quality_infrastructure() {
  local database_container="$1"
  local network="$2"
  docker rm -f "$database_container" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
}

run_backend_quality() {
  local installation_id quality_network quality_database quality_image test_password
  installation_id="$(state_installation_id)"
  quality_network="${COMPOSE_PROJECT_NAME}-quality-${installation_id:0:10}"
  quality_database="${COMPOSE_PROJECT_NAME}-quality-db-${installation_id:0:10}"
  quality_image="wemini-backend-quality:${BACKEND_COMMIT:0:12}"
  test_password="isolated-test-password"
  cleanup_quality_infrastructure "$quality_database" "$quality_network"
  docker network create "$quality_network" >/dev/null || return 1
  docker run -d --name "$quality_database" --network "$quality_network" \
    -e POSTGRES_USER=test \
    -e POSTGRES_PASSWORD="$test_password" \
    -e POSTGRES_DB=wemini_app_test \
    postgres:16-alpine >/dev/null || {
      cleanup_quality_infrastructure "$quality_database" "$quality_network"
      return 1
    }
  local attempt
  for attempt in $(seq 1 60); do
    if docker exec "$quality_database" pg_isready -U test -d wemini_app_test >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  if ! docker exec "$quality_database" pg_isready -U test -d wemini_app_test >/dev/null 2>&1; then
    cleanup_quality_infrastructure "$quality_database" "$quality_network"
    return 1
  fi

  docker build --target builder --tag "$quality_image" "$BACKEND_DIR" || {
    cleanup_quality_infrastructure "$quality_database" "$quality_network"
    return 1
  }
  local async_url sync_url
  async_url="postgresql+asyncpg://test:${test_password}@${quality_database}:5432/wemini_app_test"
  sync_url="postgresql://test:${test_password}@${quality_database}:5432/wemini_app_test"
  docker run --rm --network "$quality_network" \
    --volume "$BACKEND_DIR:/workspace:ro" \
    --workdir /workspace \
    -e APP_ENV=test \
    -e APP_DEBUG=false \
    -e JWT_SECRET=test-only-jwt-secret-that-is-at-least-32-characters \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -e PYTHONPYCACHEPREFIX=/tmp/pycache \
    -e "TEST_DATABASE_URL=$async_url" \
    -e "TEST_DATABASE_URL_SYNC=$sync_url" \
    -e "DATABASE_URL=$async_url" \
    -e "DATABASE_URL_SYNC=$sync_url" \
    -e PYTHON_BIN=/opt/venv/bin/python \
    "$quality_image" \
    /bin/bash -lc '
      scripts/quality_gate.sh backend &&
      /opt/venv/bin/python -m pytest -q -p no:cacheprovider tests/integration/db
    '
  local result=$?
  cleanup_quality_infrastructure "$quality_database" "$quality_network"
  return "$result"
}

run_admin_quality() {
  docker run --rm \
    --volume "$ADMIN_DIR:/source:ro" \
    node:20-alpine \
    /bin/sh -ec '
      mkdir -p /tmp/admin
      tar -C /source --exclude=.git --exclude=node_modules --exclude=dist -cf - . \
        | tar -C /tmp/admin -xf -
      cd /tmp/admin
      npm ci
      npm test -- --no-cache
      npm run build
    '
}

build_release_images() {
  docker build --tag "$BACKEND_IMAGE" "$BACKEND_DIR" &&
  docker build --tag "$ADMIN_IMAGE" "$ADMIN_DIR"
}

write_release_manifest() {
  local backend_image_id admin_image_id backend_remote admin_remote
  backend_image_id="$(docker image inspect --format '{{.Id}}' "$BACKEND_IMAGE")"
  admin_image_id="$(docker image inspect --format '{{.Id}}' "$ADMIN_IMAGE")"
  backend_remote="$(git -C "$BACKEND_DIR" remote get-url origin)"
  admin_remote="$(git -C "$ADMIN_DIR" remote get-url origin)"
  bootstrap_compose exec -T bootstrap python -m bootstrap_app.release_cli \
    --backend-commit "$BACKEND_COMMIT" \
    --admin-commit "$ADMIN_COMMIT" \
    --backend-remote "$backend_remote" \
    --admin-remote "$admin_remote" \
    --backend-image "$BACKEND_IMAGE" \
    --admin-image "$ADMIN_IMAGE" \
    --backend-image-id "$backend_image_id" \
    --admin-image-id "$admin_image_id" \
    --template-dir "$RENSHE_TEMPLATE_HOST_DIR" \
    --backend-port "$BACKEND_PORT" \
    --admin-port "$ADMIN_PORT"
}

quality_and_build() {
  run_backend_quality && run_admin_quality && build_release_images && write_release_manifest
}

start_infrastructure() {
  local mode
  mode="$(sed -n 's/^DEPLOYMENT_MODE=//p' "$RUNTIME_ENV")"
  case "$mode" in
    internal)
      runtime_compose --profile internal up -d db redis || return 1
      ;;
    external)
      log "using externally managed PostgreSQL and Redis"
      ;;
    *)
      return 1
      ;;
  esac
  bootstrap_compose exec -T bootstrap python -m bootstrap_app.infrastructure_cli
}

run_migration() {
  runtime_compose --profile orchestration run --rm --no-deps migration \
    || return 1
  bootstrap_cli transition --from INFRA_READY --to MIGRATED >/dev/null
}

advance_to_admin_prompt() {
  bootstrap_cli transition --from MIGRATED --to AWAITING_ADMIN >/dev/null
}

run_production_seed() {
  runtime_compose --profile orchestration run --rm --no-deps production-seed \
    || return 1
  bootstrap_cli transition --from ADMIN_CREATED --to SEEDED >/dev/null
}

upload_recovery_bundle() {
  bootstrap_compose exec -T bootstrap python -m bootstrap_app.recovery_cli upload
}

start_runtime() {
  runtime_compose up -d uploads-init app quiz-worker admin
  local attempt
  for attempt in $(seq 1 120); do
    if curl --fail --silent --show-error \
      "http://127.0.0.1:${BACKEND_PORT}/ready" >/dev/null \
      && curl --fail --silent --show-error \
        "http://127.0.0.1:${ADMIN_PORT}/" >/dev/null; then
      bootstrap_compose exec -T bootstrap \
        python -m bootstrap_app.acceptance_cli register >/dev/null \
        || return 1
      bootstrap_cli transition \
        --from RECOVERY_VERIFIED \
        --to INSTALLED_PENDING_UAT >/dev/null \
        || return 1
      return
    fi
    sleep 2
  done
  return 1
}

close_bootstrap() {
  bootstrap_compose stop bootstrap >/dev/null 2>&1 || true
  bootstrap_compose rm -f bootstrap >/dev/null 2>&1 || true
}

show_connection() {
  local host_hint
  host_hint="${BOOTSTRAP_SSH_HOST:-<server>}"
  printf '\nOpen an SSH tunnel from your workstation:\n\n'
  printf '  ssh -L %s:127.0.0.1:%s %s\n\n' "$BOOTSTRAP_PORT" "$BOOTSTRAP_PORT" "$host_hint"
  printf 'Then open this local URL once:\n\n'
  printf '  http://127.0.0.1:%s/setup#token=%s\n\n' "$BOOTSTRAP_PORT" "$BOOTSTRAP_TOKEN"
}

main() {
  preflight
  pin_sources_once
  ensure_token
  start_bootstrap
  case "$(state_phase)" in
    INSTALLED_PENDING_UAT|PRODUCTION_ACCEPTED)
      ;;
    *)
      show_connection
      ;;
  esac

  while true; do
    local phase
    phase="$(state_phase)"
    case "$phase" in
      NEW)
        log "waiting for deployment configuration in the setup page"
        wait_for_phase_change NEW
        ;;
      CONFIGURED)
        bootstrap_cli transition --from CONFIGURED --to QUALITY_RUNNING >/dev/null
        ;;
      QUALITY_RUNNING)
        run_with_web_retry quality quality_and_build
        ;;
      QUALITY_PASSED)
        run_with_web_retry infrastructure start_infrastructure
        ;;
      INFRA_READY)
        run_with_web_retry migration run_migration
        ;;
      MIGRATED)
        advance_to_admin_prompt
        ;;
      AWAITING_ADMIN)
        log "waiting for the initial super administrator in the setup page"
        wait_for_phase_change AWAITING_ADMIN
        ;;
      ADMIN_CREATED)
        run_with_web_retry production_seed run_production_seed
        ;;
      SEEDED)
        run_with_web_retry recovery_upload upload_recovery_bundle
        ;;
      RECOVERY_VERIFIED)
        run_with_web_retry runtime_start start_runtime
        ;;
      INSTALLED_PENDING_UAT)
        bootstrap_compose exec -T bootstrap \
          python -m bootstrap_app.acceptance_cli sync-state >/dev/null
        if [[ "$(state_phase)" = "PRODUCTION_ACCEPTED" ]]; then
          continue
        fi
        close_bootstrap
        log "installation completed: INSTALLED_PENDING_UAT"
        log "Backend: http://127.0.0.1:${BACKEND_PORT}"
        log "Admin:   http://127.0.0.1:${ADMIN_PORT}"
        log "complete real WeChat/payment/refund UAT before production traffic"
        log "after Admin acceptance, rerun this script once to seal PRODUCTION_ACCEPTED"
        return
        ;;
      PRODUCTION_ACCEPTED)
        close_bootstrap
        log "installation is already production-accepted"
        return
        ;;
      *)
        fail "unknown or unreadable bootstrap phase: $phase"
        ;;
    esac
  done
}

main "$@"
