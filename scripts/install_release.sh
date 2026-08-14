#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ASSET_DIR="${RELEASE_ASSET_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
RELEASE_SOURCE_FILE="$SCRIPT_DIR/release-source.env"
BOOTSTRAP_HOST_DEPLOY_ROOT="${BOOTSTRAP_HOST_DEPLOY_ROOT:-/srv/wemini-bootstrap}"
DOCKER_USE_SUDO="${DOCKER_USE_SUDO:-0}"

fail() {
  printf '[release-installer] ERROR: %s\n' "$*" >&2
  exit 1
}

docker_cli() {
  if [[ "$DOCKER_USE_SUDO" = "1" ]]; then
    sudo docker "$@"
  else
    docker "$@"
  fi
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

verify_archive() {
  local filename="$1"
  local expected_sha256="$2"
  [[ "$filename" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
    || fail "release archive filename is unsafe"
  [[ "$expected_sha256" =~ ^[0-9a-f]{64}$ ]] \
    || fail "release archive checksum is invalid"
  local path="$ASSET_DIR/$filename"
  [[ -f "$path" && ! -L "$path" ]] || fail "release archive is missing: $filename"
  local actual_sha256
  actual_sha256="$(sha256sum "$path" | awk '{print $1}')"
  [[ "$actual_sha256" = "$expected_sha256" ]] \
    || fail "release archive checksum mismatch: $filename"
}

for command in docker zstd sha256sum awk sed grep stat; do
  command -v "$command" >/dev/null 2>&1 \
    || fail "required command is unavailable: $command"
done
(( EUID != 0 )) \
  || fail "do not run the whole release installer with sudo"
case "$DOCKER_USE_SUDO" in
  0)
    ;;
  1)
    command -v sudo >/dev/null 2>&1 || fail "sudo is unavailable"
    sudo -v || fail "sudo authentication failed"
    ;;
  *)
    fail "DOCKER_USE_SUDO must be 0 or 1"
    ;;
esac
docker_cli info >/dev/null 2>&1 \
  || fail "Docker daemon is unavailable; use Docker group access or DOCKER_USE_SUDO=1"
[[ "$ASSET_DIR" = /* && "$ASSET_DIR" != "/" \
  && "$ASSET_DIR" != *$'\n'* && "$ASSET_DIR" != *$'\r'* ]] \
  || fail "RELEASE_ASSET_DIR must be a safe absolute path"
[[ "$BOOTSTRAP_HOST_DEPLOY_ROOT" = /* && "$BOOTSTRAP_HOST_DEPLOY_ROOT" != "/" ]] \
  || fail "BOOTSTRAP_HOST_DEPLOY_ROOT must be a safe absolute path"
[[ -f "$RELEASE_SOURCE_FILE" && ! -L "$RELEASE_SOURCE_FILE" ]] \
  || fail "release-source.env is unavailable"
[[ "$(release_value RELEASE_BUNDLE_VERSION)" = "1" ]] \
  || fail "unsupported release bundle version"

TOOLING_COMMIT="$(release_value TOOLING_COMMIT)"
BACKEND_COMMIT="$(release_value BACKEND_COMMIT)"
ADMIN_COMMIT="$(release_value ADMIN_COMMIT)"
BACKEND_IMAGE="$(release_value BACKEND_IMAGE)"
ADMIN_IMAGE="$(release_value ADMIN_IMAGE)"
BACKEND_ARCHIVE="$(release_value BACKEND_IMAGE_ARCHIVE)"
ADMIN_ARCHIVE="$(release_value ADMIN_IMAGE_ARCHIVE)"
BACKEND_ARCHIVE_SHA256="$(release_value BACKEND_IMAGE_ARCHIVE_SHA256)"
ADMIN_ARCHIVE_SHA256="$(release_value ADMIN_IMAGE_ARCHIVE_SHA256)"

[[ "$TOOLING_COMMIT" =~ ^[0-9a-f]{40,64}$ \
  && "$BACKEND_COMMIT" =~ ^[0-9a-f]{40,64}$ \
  && "$ADMIN_COMMIT" =~ ^[0-9a-f]{40,64}$ ]] \
  || fail "release source commit is invalid"
[[ "$BACKEND_IMAGE" = *":$BACKEND_COMMIT" \
  && "$ADMIN_IMAGE" = *":$ADMIN_COMMIT" ]] \
  || fail "release image tag does not match its commit"

verify_archive "$BACKEND_ARCHIVE" "$BACKEND_ARCHIVE_SHA256"
verify_archive "$ADMIN_ARCHIVE" "$ADMIN_ARCHIVE_SHA256"

printf '[release-installer] loading Backend image\n'
zstd -dc -- "$ASSET_DIR/$BACKEND_ARCHIVE" | docker_cli load
printf '[release-installer] loading Admin image\n'
zstd -dc -- "$ASSET_DIR/$ADMIN_ARCHIVE" | docker_cli load

for item in "Backend|$BACKEND_IMAGE|$BACKEND_COMMIT" "Admin|$ADMIN_IMAGE|$ADMIN_COMMIT"; do
  IFS='|' read -r name image expected_commit <<<"$item"
  revision="$(
    docker_cli image inspect \
      --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' \
      "$image" 2>/dev/null
  )"
  [[ "$revision" = "$expected_commit" ]] \
    || fail "$name image revision mismatch after docker load"
done

exec env \
  BACKEND_DIR="$SCRIPT_DIR" \
  BOOTSTRAP_HOST_DEPLOY_ROOT="$BOOTSTRAP_HOST_DEPLOY_ROOT" \
  DEPLOY_SOURCE_MODE=release \
  DOCKER_USE_SUDO="$DOCKER_USE_SUDO" \
  RELEASE_IMAGE_MODE=preloaded \
  RELEASE_SOURCE_FILE="$RELEASE_SOURCE_FILE" \
  "$SCRIPT_DIR/bootstrap_server.sh"
