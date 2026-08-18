#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DETECTED_BACKEND_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="${BACKEND_DIR:-$DETECTED_BACKEND_DIR}"
BACKEND_DIR="$(cd -- "$BACKEND_DIR" && pwd)"
PROJECT_ROOT="$(cd -- "$BACKEND_DIR/.." && pwd)"
ADMIN_DIR="${ADMIN_DIR:-$PROJECT_ROOT/Admin}"
BOOTSTRAP_HOST_DEPLOY_ROOT="${BOOTSTRAP_HOST_DEPLOY_ROOT:-/srv/wemini-bootstrap}"
DEPLOY_SOURCE_MODE="${DEPLOY_SOURCE_MODE:-git}"
RELEASE_SOURCE_FILE="${RELEASE_SOURCE_FILE:-$BACKEND_DIR/release-source.env}"
PROJECT_RENSHE_TEMPLATE_DIR="$PROJECT_ROOT/docs/renshe"
if [[ -z "${RENSHE_TEMPLATE_HOST_DIR:-}" ]]; then
  if [[ -f "$PROJECT_RENSHE_TEMPLATE_DIR/报名信息.xlsx" \
    && -f "$PROJECT_RENSHE_TEMPLATE_DIR/工作经历.xlsx" ]]; then
    RENSHE_TEMPLATE_HOST_DIR="$PROJECT_RENSHE_TEMPLATE_DIR"
  else
    RENSHE_TEMPLATE_HOST_DIR="$BOOTSTRAP_HOST_DEPLOY_ROOT/assets/renshe"
  fi
fi
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-wemini}"
BOOTSTRAP_PORT="${BOOTSTRAP_PORT:-18080}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
ADMIN_PORT="${ADMIN_PORT:-8080}"
ADMIN_GIT_REMOTE="${ADMIN_GIT_REMOTE:-https://github.com/BishengTeam/Admin.git}"
if [[ -z "${RELEASE_IMAGE_MODE:-}" ]]; then
  if [[ "$DEPLOY_SOURCE_MODE" = "release" ]]; then
    RELEASE_IMAGE_MODE="preloaded"
  else
    RELEASE_IMAGE_MODE="build"
  fi
fi
BACKEND_IMAGE_REPOSITORY="${BACKEND_IMAGE_REPOSITORY:-ghcr.io/bishengteam/backend}"
ADMIN_IMAGE_REPOSITORY="${ADMIN_IMAGE_REPOSITORY:-ghcr.io/bishengteam/admin}"
BACKEND_IMAGE_OVERRIDE="${BACKEND_IMAGE:-}"
ADMIN_IMAGE_OVERRIDE="${ADMIN_IMAGE:-}"
DOCKER_USE_SUDO="${DOCKER_USE_SUDO:-0}"

CONTROL_DIR="$BOOTSTRAP_HOST_DEPLOY_ROOT/control"
TOKEN_FILE="$CONTROL_DIR/bootstrap_token"
SOURCE_PINS_FILE="$CONTROL_DIR/source-pins.env"
RUNTIME_ENV="$BOOTSTRAP_HOST_DEPLOY_ROOT/installation/runtime.env"
RELEASE_ENV="$CONTROL_DIR/release.env"
BOOTSTRAP_COMPOSE_FILE="$BACKEND_DIR/docker-compose.bootstrap.yml"
RUNTIME_COMPOSE_FILE="$BACKEND_DIR/docker-compose.deploy.yml"
if [[ "$DEPLOY_SOURCE_MODE" = "release" ]]; then
  BOOTSTRAP_COMPOSE_FILE="$BACKEND_DIR/docker-compose.bootstrap.release.yml"
fi

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

docker_cli() {
  if [[ "$DOCKER_USE_SUDO" = "1" ]]; then
    sudo env \
      BOOTSTRAP_UID="$BOOTSTRAP_UID" \
      BOOTSTRAP_GID="$BOOTSTRAP_GID" \
      docker "$@"
  else
    env \
      BOOTSTRAP_UID="$BOOTSTRAP_UID" \
      BOOTSTRAP_GID="$BOOTSTRAP_GID" \
      docker "$@"
  fi
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

ensure_admin_repository() {
  if git -C "$ADMIN_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    return
  fi
  [[ ! -e "$ADMIN_DIR" ]] \
    || fail "Admin path exists but is not a Git worktree: $ADMIN_DIR"
  [[ "$ADMIN_GIT_REMOTE" != *://*@* ]] \
    || fail "ADMIN_GIT_REMOTE must not contain URL credentials"
  log "Admin repository is missing; cloning origin/main"
  git clone --branch main --single-branch "$ADMIN_GIT_REMOTE" "$ADMIN_DIR" \
    || fail "unable to clone Admin repository"
}

validate_image_ref() {
  local image_ref="$1"
  local name="$2"
  [[ -n "$image_ref" && "$image_ref" != -* ]] \
    || fail "$name is invalid"
  [[ "$image_ref" != *$'\n'* && "$image_ref" != *$'\r'* \
    && "$image_ref" != *' '* && "$image_ref" != *$'\t'* ]] \
    || fail "$name contains invalid whitespace"
}

release_value() {
  local key="$1"
  local count value
  count="$(grep -c "^${key}=" "$RELEASE_SOURCE_FILE" || true)"
  [[ "$count" = "1" ]] || fail "release source must contain exactly one $key"
  value="$(sed -n "s/^${key}=//p" "$RELEASE_SOURCE_FILE")"
  [[ -n "$value" && "$value" != *$'\n'* && "$value" != *$'\r'* ]] \
    || fail "release source value is invalid: $key"
  printf '%s' "$value"
}

load_release_source() {
  [[ -f "$RELEASE_SOURCE_FILE" && ! -L "$RELEASE_SOURCE_FILE" ]] \
    || fail "release source manifest is unavailable: $RELEASE_SOURCE_FILE"
  [[ "$(stat -c '%s' "$RELEASE_SOURCE_FILE")" -le 16384 ]] \
    || fail "release source manifest is too large"
  [[ "$(release_value RELEASE_BUNDLE_VERSION)" = "1" ]] \
    || fail "unsupported release bundle version"
  RELEASE_TOOLING_COMMIT="$(release_value TOOLING_COMMIT)"
  RELEASE_BACKEND_COMMIT="$(release_value BACKEND_COMMIT)"
  RELEASE_ADMIN_COMMIT="$(release_value ADMIN_COMMIT)"
  RELEASE_TAG="$(release_value RELEASE_TAG)"
  RELEASE_BACKEND_REMOTE="$(release_value BACKEND_REMOTE)"
  RELEASE_ADMIN_REMOTE="$(release_value ADMIN_REMOTE)"
  RELEASE_BACKEND_IMAGE="$(release_value BACKEND_IMAGE)"
  RELEASE_ADMIN_IMAGE="$(release_value ADMIN_IMAGE)"
  [[ "$RELEASE_TOOLING_COMMIT" =~ ^[0-9a-f]{40,64}$ ]] \
    || fail "release tooling commit is invalid"
  [[ "$RELEASE_BACKEND_COMMIT" =~ ^[0-9a-f]{40,64}$ ]] \
    || fail "release Backend commit is invalid"
  [[ "$RELEASE_ADMIN_COMMIT" =~ ^[0-9a-f]{40,64}$ ]] \
    || fail "release Admin commit is invalid"
  [[ "$RELEASE_TAG" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] \
    || fail "release source tag is invalid"
  [[ "$RELEASE_BACKEND_REMOTE" != *://*@* \
    && "$RELEASE_ADMIN_REMOTE" != *://*@* ]] \
    || fail "release repository remote contains URL credentials"
  validate_image_ref "$RELEASE_BACKEND_IMAGE" BACKEND_IMAGE
  validate_image_ref "$RELEASE_ADMIN_IMAGE" ADMIN_IMAGE
  [[ "$RELEASE_BACKEND_IMAGE" = *":$RELEASE_BACKEND_COMMIT" ]] \
    || fail "release Backend image tag must equal its full commit"
  [[ "$RELEASE_ADMIN_IMAGE" = *":$RELEASE_ADMIN_COMMIT" ]] \
    || fail "release Admin image tag must equal its full commit"
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
  for command in docker curl openssl sha256sum awk sed grep seq stat install; do
    require_command "$command"
  done
  (( EUID != 0 )) \
    || fail "do not run this script with sudo; grant the deployment user Docker access"
  case "$DOCKER_USE_SUDO" in
    0)
      ;;
    1)
      require_command sudo
      sudo -v || fail "sudo authentication failed"
      ;;
    *)
      fail "DOCKER_USE_SUDO must be 0 or 1"
      ;;
  esac
  docker_cli info >/dev/null 2>&1 \
    || fail "Docker daemon is unavailable; use Docker group access or DOCKER_USE_SUDO=1"
  docker_cli compose version >/dev/null 2>&1 || fail "Docker Compose v2 is unavailable"
  assert_safe_absolute_path "$BACKEND_DIR" BACKEND_DIR
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
  case "$DEPLOY_SOURCE_MODE" in
    git|release)
      ;;
    *)
      fail "DEPLOY_SOURCE_MODE must be git or release"
      ;;
  esac
  case "$RELEASE_IMAGE_MODE" in
    pull|preloaded|build)
      ;;
    *)
      fail "RELEASE_IMAGE_MODE must be pull, preloaded, or build"
      ;;
  esac
  if [[ "$DEPLOY_SOURCE_MODE" = "release" ]]; then
    [[ "$RELEASE_IMAGE_MODE" = "preloaded" ]] \
      || fail "release bundles require RELEASE_IMAGE_MODE=preloaded"
    load_release_source
  else
    require_command git
    assert_safe_absolute_path "$ADMIN_DIR" ADMIN_DIR
    ensure_admin_repository
    check_repository "$BACKEND_DIR" Backend
    check_repository "$ADMIN_DIR" Admin
  fi

  install -d -m 0700 "$BOOTSTRAP_HOST_DEPLOY_ROOT" "$CONTROL_DIR"
  chmod 0700 "$BOOTSTRAP_HOST_DEPLOY_ROOT" "$CONTROL_DIR"
  if [[ ! -e "$RENSHE_TEMPLATE_HOST_DIR" ]]; then
    install -d -m 0755 "$RENSHE_TEMPLATE_HOST_DIR"
  fi
  [[ -d "$RENSHE_TEMPLATE_HOST_DIR" && ! -L "$RENSHE_TEMPLATE_HOST_DIR" ]] \
    || fail "RENSHE_TEMPLATE_HOST_DIR must be a real directory"
  if [[ ! -f "$RENSHE_TEMPLATE_HOST_DIR/报名信息.xlsx" \
    || ! -f "$RENSHE_TEMPLATE_HOST_DIR/工作经历.xlsx" ]]; then
    log "WARNING: 人社 Excel 模板尚未补齐；平台可以部署，但人社批次导出暂不可用"
    log "template directory: $RENSHE_TEMPLATE_HOST_DIR"
  fi
  if command -v timedatectl >/dev/null 2>&1; then
    local synchronized
    synchronized="$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)"
    [[ "$synchronized" != "no" ]] || fail "system clock is not synchronized"
  fi
  warn_resources
}

pin_sources_once() {
  if [[ "$DEPLOY_SOURCE_MODE" = "release" ]]; then
    load_release_source
    BACKEND_COMMIT="$RELEASE_BACKEND_COMMIT"
    ADMIN_COMMIT="$RELEASE_ADMIN_COMMIT"
    BACKEND_IMAGE="$RELEASE_BACKEND_IMAGE"
    ADMIN_IMAGE="$RELEASE_ADMIN_IMAGE"
    if [[ -f "$SOURCE_PINS_FILE" ]]; then
      grep -Eq "^RELEASE_TAG=${RELEASE_TAG}$" "$SOURCE_PINS_FILE" \
        || fail "saved release tag differs from the release bundle"
      grep -Eq "^BACKEND_COMMIT=${BACKEND_COMMIT}$" "$SOURCE_PINS_FILE" \
        || fail "saved Backend source pin differs from the release bundle"
      grep -Eq "^ADMIN_COMMIT=${ADMIN_COMMIT}$" "$SOURCE_PINS_FILE" \
        || fail "saved Admin source pin differs from the release bundle"
    else
      {
        printf 'RELEASE_TAG=%s\n' "$RELEASE_TAG"
        printf 'BACKEND_COMMIT=%s\n' "$BACKEND_COMMIT"
        printf 'ADMIN_COMMIT=%s\n' "$ADMIN_COMMIT"
      } >"$SOURCE_PINS_FILE"
      chmod 0600 "$SOURCE_PINS_FILE"
    fi
    validate_image_ref "$BACKEND_IMAGE" BACKEND_IMAGE
    validate_image_ref "$ADMIN_IMAGE" ADMIN_IMAGE
    log "release source mode: GitHub Release bundle"
    log "Backend image: $BACKEND_IMAGE"
    log "Admin image:   $ADMIN_IMAGE"
    export BACKEND_COMMIT ADMIN_COMMIT BACKEND_IMAGE ADMIN_IMAGE RELEASE_TAG
    return
  fi
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
  case "$RELEASE_IMAGE_MODE" in
    pull|preloaded)
      BACKEND_IMAGE="${BACKEND_IMAGE_OVERRIDE:-${BACKEND_IMAGE_REPOSITORY}:${BACKEND_COMMIT}}"
      ADMIN_IMAGE="${ADMIN_IMAGE_OVERRIDE:-${ADMIN_IMAGE_REPOSITORY}:${ADMIN_COMMIT}}"
      ;;
    build)
      BACKEND_IMAGE="${BACKEND_IMAGE_OVERRIDE:-wemini-backend:${BACKEND_COMMIT:0:12}}"
      ADMIN_IMAGE="${ADMIN_IMAGE_OVERRIDE:-wemini-admin:${ADMIN_COMMIT:0:12}}"
      ;;
  esac
  validate_image_ref "$BACKEND_IMAGE" BACKEND_IMAGE
  validate_image_ref "$ADMIN_IMAGE" ADMIN_IMAGE
  log "release image mode: $RELEASE_IMAGE_MODE"
  log "Backend image: $BACKEND_IMAGE"
  log "Admin image:   $ADMIN_IMAGE"
  export BACKEND_COMMIT ADMIN_COMMIT BACKEND_IMAGE ADMIN_IMAGE
}

image_revision() {
  docker_cli image inspect \
    --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' \
    "$1" 2>/dev/null
}

verify_release_image() {
  local image_ref="$1"
  local expected_commit="$2"
  local name="$3"
  docker_cli image inspect "$image_ref" >/dev/null 2>&1 \
    || fail "$name image is unavailable: $image_ref"
  local revision
  revision="$(image_revision "$image_ref")"
  [[ "$revision" = "$expected_commit" ]] \
    || fail "$name image revision mismatch: expected $expected_commit, got ${revision:-missing}"
}

verify_release_images() {
  verify_release_image "$BACKEND_IMAGE" "$BACKEND_COMMIT" Backend
  verify_release_image "$ADMIN_IMAGE" "$ADMIN_COMMIT" Admin
}

prepare_release_images() {
  case "$RELEASE_IMAGE_MODE" in
    pull)
      log "pulling immutable prebuilt Backend image"
      docker_cli pull "$BACKEND_IMAGE" \
        || fail "unable to pull Backend image; publish it in CI and log in to its registry"
      log "pulling immutable prebuilt Admin image"
      docker_cli pull "$ADMIN_IMAGE" \
        || fail "unable to pull Admin image; publish it in CI and log in to its registry"
      verify_release_images
      ;;
    preloaded)
      log "using preloaded release images"
      verify_release_images
      ;;
    build)
      log "development build mode selected; images will be built on this host"
      ;;
  esac
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
  if [[ "$DOCKER_USE_SUDO" = "1" ]]; then
    sudo env \
      BOOTSTRAP_UID="$BOOTSTRAP_UID" \
      BOOTSTRAP_GID="$BOOTSTRAP_GID" \
      BOOTSTRAP_HOST_DEPLOY_ROOT="$BOOTSTRAP_HOST_DEPLOY_ROOT" \
      BOOTSTRAP_PORT="$BOOTSTRAP_PORT" \
      BOOTSTRAP_IMAGE="$BACKEND_IMAGE" \
      docker compose \
      --project-name "$COMPOSE_PROJECT_NAME" \
      --file "$BOOTSTRAP_COMPOSE_FILE" \
      "$@"
  else
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
  fi
}

runtime_compose() {
  [[ -f "$RUNTIME_ENV" && -f "$RELEASE_ENV" ]] \
    || fail "runtime environment is not ready"
  docker_cli compose \
    --project-name "$COMPOSE_PROJECT_NAME" \
    --env-file "$RUNTIME_ENV" \
    --env-file "$RELEASE_ENV" \
    --file "$RUNTIME_COMPOSE_FILE" \
    "$@"
}

start_bootstrap() {
  if [[ "$RELEASE_IMAGE_MODE" = "build" ]]; then
    log "building and starting loopback-only bootstrap service"
    bootstrap_compose up -d --build bootstrap
  else
    log "starting loopback-only bootstrap service from the prebuilt image"
    bootstrap_compose up -d --no-build bootstrap
  fi
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
  docker_cli rm -f "$database_container" >/dev/null 2>&1 || true
  docker_cli network rm "$network" >/dev/null 2>&1 || true
}

run_backend_quality() {
  local installation_id quality_network quality_database quality_image test_password
  installation_id="$(state_installation_id)"
  quality_network="${COMPOSE_PROJECT_NAME}-quality-${installation_id:0:10}"
  quality_database="${COMPOSE_PROJECT_NAME}-quality-db-${installation_id:0:10}"
  quality_image="wemini-backend-quality:${BACKEND_COMMIT:0:12}"
  test_password="isolated-test-password"
  cleanup_quality_infrastructure "$quality_database" "$quality_network"
  docker_cli network create "$quality_network" >/dev/null || return 1
  docker_cli run -d --name "$quality_database" --network "$quality_network" \
    -e POSTGRES_USER=test \
    -e POSTGRES_PASSWORD="$test_password" \
    -e POSTGRES_DB=wemini_app_test \
    postgres:16-alpine >/dev/null || {
      cleanup_quality_infrastructure "$quality_database" "$quality_network"
      return 1
    }
  local attempt
  for attempt in $(seq 1 60); do
    if docker_cli exec "$quality_database" pg_isready -U test -d wemini_app_test >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  if ! docker_cli exec "$quality_database" pg_isready -U test -d wemini_app_test >/dev/null 2>&1; then
    cleanup_quality_infrastructure "$quality_database" "$quality_network"
    return 1
  fi

  docker_cli build --target builder --tag "$quality_image" "$BACKEND_DIR" || {
    cleanup_quality_infrastructure "$quality_database" "$quality_network"
    return 1
  }
  local async_url sync_url
  async_url="postgresql+asyncpg://test:${test_password}@${quality_database}:5432/wemini_app_test"
  sync_url="postgresql://test:${test_password}@${quality_database}:5432/wemini_app_test"
  docker_cli run --rm --network "$quality_network" \
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
  docker_cli run --rm \
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
  docker_cli build \
    --label "org.opencontainers.image.revision=$BACKEND_COMMIT" \
    --tag "$BACKEND_IMAGE" "$BACKEND_DIR" &&
  docker_cli build \
    --label "org.opencontainers.image.revision=$ADMIN_COMMIT" \
    --tag "$ADMIN_IMAGE" "$ADMIN_DIR"
}

write_release_manifest() {
  local quality_source="$1"
  local backend_image_id admin_image_id backend_remote admin_remote
  local quality_source_args=()
  backend_image_id="$(docker_cli image inspect --format '{{.Id}}' "$BACKEND_IMAGE")"
  admin_image_id="$(docker_cli image inspect --format '{{.Id}}' "$ADMIN_IMAGE")"
  if [[ "$DEPLOY_SOURCE_MODE" = "release" ]]; then
    backend_remote="$RELEASE_BACKEND_REMOTE"
    admin_remote="$RELEASE_ADMIN_REMOTE"
  else
    backend_remote="$(git -C "$BACKEND_DIR" remote get-url origin)"
    admin_remote="$(git -C "$ADMIN_DIR" remote get-url origin)"
  fi
  if bootstrap_compose exec -T bootstrap \
    python -m bootstrap_app.release_cli --help 2>/dev/null \
    | grep -q -- '--quality-source'; then
    quality_source_args=(--quality-source "$quality_source")
  else
    log "WARNING: pinned Backend predates quality-source metadata; writing a legacy manifest"
  fi
  bootstrap_compose exec -T bootstrap python -m bootstrap_app.release_cli \
    --release-tag "$RELEASE_TAG" \
    --backend-commit "$BACKEND_COMMIT" \
    --admin-commit "$ADMIN_COMMIT" \
    --backend-remote "$backend_remote" \
    --admin-remote "$admin_remote" \
    --backend-image "$BACKEND_IMAGE" \
    --admin-image "$ADMIN_IMAGE" \
    --backend-image-id "$backend_image_id" \
    --admin-image-id "$admin_image_id" \
    "${quality_source_args[@]}" \
    --template-dir "$RENSHE_TEMPLATE_HOST_DIR" \
    --backend-port "$BACKEND_PORT" \
    --admin-port "$ADMIN_PORT" \
    --compose-project "$COMPOSE_PROJECT_NAME"
}

quality_and_build() {
  case "$RELEASE_IMAGE_MODE" in
    build)
      run_backend_quality \
        && run_admin_quality \
        && build_release_images \
        && verify_release_images \
        && write_release_manifest server_build
      ;;
    pull)
      verify_release_images && write_release_manifest ci_prebuilt
      ;;
    preloaded)
      if [[ "$DEPLOY_SOURCE_MODE" = "release" ]]; then
        verify_release_images && write_release_manifest github_release
      else
        verify_release_images && write_release_manifest preloaded
      fi
      ;;
  esac
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
  prepare_release_images
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

if [[ "${BASH_SOURCE[0]}" = "$0" ]]; then
  main "$@"
fi
