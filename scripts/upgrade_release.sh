#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-BishengTeam/Backend}"
UPDATER_ROOT="${WEMINI_UPDATER_ROOT:-/srv/wemini-updater}"
DOCKER_USE_SUDO="${DOCKER_USE_SUDO:-0}"
RELEASE=""
DEPLOYMENT_ROOT=""
COMPOSE_PROJECT=""
DRY_RUN=0
FORCE=0

fail() {
  printf '[wemini-updater] ERROR: %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[wemini-updater] %s\n' "$*"
}

usage() {
  cat <<'EOF'
Usage:
  upgrade_release.sh --release TAG --deployment-root PATH --compose-project NAME [--dry-run] [--force]

The script downloads a stable GitHub Release, verifies all assets, backs up
PostgreSQL, runs migrations, and replaces the existing in-place deployment.
Run as the deployment user. Use DOCKER_USE_SUDO=1 when Docker requires sudo.
EOF
}

while (($#)); do
  case "$1" in
    --release)
      (($# >= 2)) || fail "--release requires a value"
      RELEASE="$2"
      shift 2
      ;;
    --deployment-root)
      (($# >= 2)) || fail "--deployment-root requires a value"
      DEPLOYMENT_ROOT="$2"
      shift 2
      ;;
    --compose-project)
      (($# >= 2)) || fail "--compose-project requires a value"
      COMPOSE_PROJECT="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --force)
      FORCE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

[[ "$RELEASE" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] \
  || fail "release tag is invalid"
[[ -n "$DEPLOYMENT_ROOT" && "$DEPLOYMENT_ROOT" = /* && "$DEPLOYMENT_ROOT" != "/" ]] \
  || fail "deployment root must be a safe absolute path"
[[ -n "$COMPOSE_PROJECT" && "$COMPOSE_PROJECT" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] \
  || fail "compose project is invalid"
[[ "$GITHUB_REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
  || fail "GitHub repository is invalid"

for command in docker curl python3 sha256sum tar zstd openssl stat find sort awk flock; do
  command -v "$command" >/dev/null 2>&1 || fail "required command is unavailable: $command"
done
(( EUID != 0 )) || fail "do not run the updater with sudo; use DOCKER_USE_SUDO=1"
case "$DOCKER_USE_SUDO" in
  0) ;;
  1)
    command -v sudo >/dev/null 2>&1 || fail "sudo is unavailable"
    sudo -v || fail "sudo authentication failed"
    ;;
  *) fail "DOCKER_USE_SUDO must be 0 or 1" ;;
esac

docker_cli() {
  if [[ "$DOCKER_USE_SUDO" = "1" ]]; then
    sudo docker "$@"
  else
    docker "$@"
  fi
}

runtime_env="$DEPLOYMENT_ROOT/installation/runtime.env"
control_dir="$DEPLOYMENT_ROOT/control"
old_release_env="$control_dir/release.env"
[[ -f "$runtime_env" && ! -L "$runtime_env" ]] || fail "runtime.env is unavailable"
[[ -f "$old_release_env" && ! -L "$old_release_env" ]] || fail "release.env is unavailable"
[[ -d "$control_dir" && ! -L "$control_dir" ]] || fail "control directory is unavailable"

read_env_value() {
  local file="$1" key="$2" count value
  count="$(grep -c "^${key}=" "$file" || true)"
  [[ "$count" = "1" ]] || fail "$file must contain exactly one $key"
  value="$(sed -n "s/^${key}=//p" "$file")"
  [[ -n "$value" && "$value" != *$'\n'* && "$value" != *$'\r'* ]] \
    || fail "$file contains an invalid $key"
  printf '%s' "$value"
}

deployment_mode="$(read_env_value "$runtime_env" DEPLOYMENT_MODE)"
[[ "$deployment_mode" = "internal" ]] \
  || fail "the first online updater supports the built-in PostgreSQL deployment only"

runtime_db_host="$(read_env_value "$runtime_env" DB_HOST)"
runtime_db_name="$(read_env_value "$runtime_env" DB_NAME)"
runtime_db_user="$(read_env_value "$runtime_env" DB_USER)"
[[ "$runtime_db_host" = "db" ]] || fail "online updates require the compose db service"
[[ "$runtime_db_name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] \
  || fail "database name is unsafe for restore"
[[ "$runtime_db_user" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] \
  || fail "database user is unsafe for restore"

old_backend_image="$(read_env_value "$old_release_env" BACKEND_IMAGE)"
old_admin_image="$(read_env_value "$old_release_env" ADMIN_IMAGE)"
backend_port="$(read_env_value "$old_release_env" BACKEND_PORT)"
admin_port="$(read_env_value "$old_release_env" ADMIN_PORT)"
[[ "$backend_port" =~ ^[0-9]+$ && "$admin_port" =~ ^[0-9]+$ ]] \
  || fail "runtime ports are invalid"

app_container_id="$(
  docker_cli compose --project-name "$COMPOSE_PROJECT" ps -q app 2>/dev/null || true
)"
[[ -n "$app_container_id" ]] || fail "running app container was not found for compose project"
old_compose="$(
  docker_cli inspect \
    --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' \
    "$app_container_id"
)"
[[ -f "$old_compose" && ! -L "$old_compose" ]] || fail "running compose file is unavailable: $old_compose"
old_bundle="$(cd -- "$(dirname -- "$old_compose")" && pwd)"
old_source_file="$old_bundle/release-source.env"
[[ -f "$old_source_file" ]] || fail "current release-source.env is unavailable"

old_release_tag="$(read_env_value "$old_source_file" RELEASE_TAG)"
old_backend_commit="$(read_env_value "$old_source_file" BACKEND_COMMIT)"
old_admin_commit="$(read_env_value "$old_source_file" ADMIN_COMMIT)"
running_backend_revision="$(
  docker_cli inspect \
    --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' \
    "$old_backend_image" 2>/dev/null || true
)"
[[ "$running_backend_revision" = "$old_backend_commit" ]] \
  || fail "current Backend image revision does not match its source pin"

compose_for() {
  local compose_file="$1" release_file="$2"
  shift 2
  if [[ "$DOCKER_USE_SUDO" = "1" ]]; then
    sudo env \
      BOOTSTRAP_UID="$(id -u)" \
      BOOTSTRAP_GID="$(id -g)" \
      docker compose \
      --project-name "$COMPOSE_PROJECT" \
      --env-file "$runtime_env" \
      --env-file "$release_file" \
      --file "$compose_file" \
      "$@"
  else
    env \
      BOOTSTRAP_UID="$(id -u)" \
      BOOTSTRAP_GID="$(id -g)" \
      docker compose \
      --project-name "$COMPOSE_PROJECT" \
      --env-file "$runtime_env" \
      --env-file "$release_file" \
      --file "$compose_file" \
      "$@"
  fi
}

readiness_value() {
  local json="$1" path="$2"
  python3 - "$json" "$path" <<'PY'
import json
import sys
value = json.loads(sys.argv[1])
for part in sys.argv[2].split("."):
    if not isinstance(value, dict) or part not in value:
        raise SystemExit(f"missing readiness field: {part}")
    value = value[part]
print(value)
PY
}

check_readiness() {
  local timeout="${1:-180}" attempt response
  for attempt in $(seq 1 $((timeout / 5))); do
    response="$(curl -fsS --max-time 5 "http://127.0.0.1:${backend_port}/ready" 2>/dev/null || true)"
    if [[ -n "$response" ]] \
      && [[ "$(readiness_value "$response" status 2>/dev/null || true)" = "ready" ]]; then
      printf '%s' "$response"
      return 0
    fi
    sleep 5
  done
  return 1
}

check_admin() {
  local timeout="${1:-120}" attempt
  for attempt in $(seq 1 $((timeout / 3))); do
    if curl -fsS --max-time 5 -o /dev/null "http://127.0.0.1:${admin_port}/"; then
      return 0
    fi
    sleep 3
  done
  return 1
}

sql_scalar() {
  local sql="$1"
  compose_for "$old_compose" "$old_release_env" exec -T db \
    sh -c "exec psql -U \"\$POSTGRES_USER\" -d \"$runtime_db_name\" -Atq -v ON_ERROR_STOP=1" \
    <<<"$sql" \
    | tr -d '\r\n'
}

check_activity() {
  local ready queue_depth active_exams active_practice recent_pending
  ready="$(check_readiness 30)" || fail "current Backend is not ready"
  queue_depth="$(readiness_value "$ready" details.quiz_tasks.signals.total_queue_depth)"
  active_exams="$(sql_scalar "SELECT count(*) FROM quiz_exam WHERE status = 'in_progress'")"
  active_practice="$(sql_scalar "SELECT count(*) FROM quiz_practice_session WHERE status IN ('in_progress', 'paused')")"
  recent_pending="$(sql_scalar "SELECT count(*) FROM \"order\" WHERE status = 'pending' AND updated_at >= now() - interval '2 hours'")"
  log "preflight: quiz_queue=$queue_depth active_exams=$active_exams active_practice=$active_practice recent_pending_orders=$recent_pending"
  if (( queue_depth + active_exams + active_practice + recent_pending > 0 )); then
    if [[ "$FORCE" != "1" ]]; then
      fail "critical user activity is in progress; retry later or use --force explicitly"
    fi
    log "WARNING: --force skips the critical activity preflight"
  fi
}

mkdir -p "$UPDATER_ROOT/releases" "$UPDATER_ROOT/logs" "$UPDATER_ROOT/cache"
chmod 0700 "$UPDATER_ROOT" "$UPDATER_ROOT/releases" "$UPDATER_ROOT/logs" "$UPDATER_ROOT/cache"
exec 9>"$UPDATER_ROOT/upgrade.lock"
flock -n 9 || fail "another upgrade is already running"

log_file="$UPDATER_ROOT/logs/${RELEASE}-$(date -u +%Y%m%dT%H%M%SZ).log"
install -m 0600 /dev/null "$log_file"
exec > >(tee -a "$log_file") 2>&1

asset_dir="$UPDATER_ROOT/cache/$RELEASE"
api_file="$UPDATER_ROOT/cache/$RELEASE.json"
mkdir -p "$asset_dir"
chmod 0700 "$asset_dir"

if [[ ! -f "$api_file" ]]; then
  curl -fsSL --retry 3 --connect-timeout 15 \
    -H 'Accept: application/vnd.github+json' \
    -H 'X-GitHub-Api-Version: 2022-11-28' \
    "https://api.github.com/repos/${GITHUB_REPOSITORY}/releases/tags/$RELEASE" \
    >"$api_file.tmp"
  mv "$api_file.tmp" "$api_file"
fi

python3 - "$api_file" "$asset_dir" <<'PY'
import json
import pathlib
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
if payload.get("draft") is not False or payload.get("prerelease") is not False:
    raise SystemExit("release must be stable")
if payload.get("tag_name") != pathlib.Path(sys.argv[1]).stem:
    raise SystemExit("release tag mismatch")
expected = {"SHA256SUMS"}
for asset in payload.get("assets", []):
    name = asset.get("name", "")
    expected.add(name)
    print(f"{name}\t{asset.get('browser_download_url', '')}\t{asset.get('size', 0)}")
required_prefixes = ("wemini-backend-", "wemini-admin-", "wemini-deploy-")
if not any(name.startswith(required_prefixes[0]) and name.endswith(".tar.zst") for name in expected) \
        or not any(name.startswith(required_prefixes[1]) and name.endswith(".tar.zst") for name in expected) \
        or not any(name.startswith(required_prefixes[2]) and name.endswith(".tar.gz") for name in expected):
    raise SystemExit("release assets are incomplete")
PY

while IFS=$'\t' read -r name url size; do
  destination="$asset_dir/$name"
  if [[ -f "$destination" ]]; then
    actual_size="$(stat -c '%s' "$destination")"
    [[ "$actual_size" = "$size" ]] || fail "cached release asset has the wrong size: $name"
  else
    log "downloading $name"
    curl -fL --retry 3 --connect-timeout 15 --max-time 1800 \
      "$url" >"$destination.part"
    mv "$destination.part" "$destination"
  fi
done < <(python3 - "$api_file" "$asset_dir" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
for asset in payload.get("assets", []):
    print(f"{asset['name']}\t{asset['browser_download_url']}\t{asset['size']}")
PY
)

(cd "$asset_dir" && sha256sum -c SHA256SUMS) || fail "release checksum verification failed"
bundle_archive="$asset_dir/wemini-deploy-$RELEASE.tar.gz"
[[ -f "$bundle_archive" ]] || fail "deployment bundle is missing"
bundle_dir="$UPDATER_ROOT/releases/$RELEASE"
if [[ ! -d "$bundle_dir" ]]; then
  mkdir "$bundle_dir"
  tar -xzf "$bundle_archive" -C "$bundle_dir" --strip-components=1
fi
new_compose="$bundle_dir/docker-compose.deploy.yml"
new_source_file="$bundle_dir/release-source.env"
[[ -f "$new_compose" && -f "$new_source_file" ]] || fail "new deployment bundle is incomplete"

new_release_tag="$(read_env_value "$new_source_file" RELEASE_TAG)"
new_backend_commit="$(read_env_value "$new_source_file" BACKEND_COMMIT)"
new_admin_commit="$(read_env_value "$new_source_file" ADMIN_COMMIT)"
new_backend_image="$(read_env_value "$new_source_file" BACKEND_IMAGE)"
new_admin_image="$(read_env_value "$new_source_file" ADMIN_IMAGE)"
new_backend_archive="$asset_dir/$(read_env_value "$new_source_file" BACKEND_IMAGE_ARCHIVE)"
new_admin_archive="$asset_dir/$(read_env_value "$new_source_file" ADMIN_IMAGE_ARCHIVE)"
[[ "$new_release_tag" = "$RELEASE" ]] || fail "new release tag differs from requested tag"
for image_name in "$new_backend_image" "$new_admin_image"; do
  [[ "$image_name" =~ ^wemini-(backend|admin):[0-9a-f]{40,64}$ ]] \
    || fail "new image reference is invalid"
done
for archive_name in \
  "$(read_env_value "$new_source_file" BACKEND_IMAGE_ARCHIVE)" \
  "$(read_env_value "$new_source_file" ADMIN_IMAGE_ARCHIVE)"; do
  [[ "$archive_name" =~ ^wemini-(backend|admin)-[0-9a-f]{40,64}\.tar\.zst$ ]] \
    || fail "new image archive name is invalid"
done
[[ -f "$new_backend_archive" && -f "$new_admin_archive" ]] || fail "image archives are missing"
zstd -t "$new_backend_archive" "$new_admin_archive" || fail "image archive compression check failed"

check_activity

db_size_bytes="$(sql_scalar "SELECT pg_database_size(current_database())")"
free_bytes="$(df -P "$DEPLOYMENT_ROOT" | awk 'NR == 2 {print $4 * 1024}')"
required_bytes=$((db_size_bytes * 3 + 2 * 1024 * 1024 * 1024))
log "disk: database=${db_size_bytes}B free=${free_bytes}B required=${required_bytes}B"
(( free_bytes >= required_bytes )) || fail "not enough disk space for backup and rollback"

log "current release: $old_release_tag ($old_backend_commit / $old_admin_commit)"
log "target release:  $RELEASE ($new_backend_commit / $new_admin_commit)"

if [[ "$DRY_RUN" = "1" ]]; then
  cat <<EOF

[wemini-updater] DRY RUN OK
deployment root: $DEPLOYMENT_ROOT
compose project:  $COMPOSE_PROJECT
current release:  $old_release_tag
target release:   $RELEASE
backend image:    $new_backend_image
admin image:      $new_admin_image
database size:    $db_size_bytes bytes

No service was stopped, no image was loaded, and no database was changed.
EOF
  exit 0
fi

log "loading Backend image"
zstd -dc "$new_backend_archive" | docker_cli load >/dev/null
log "loading Admin image"
zstd -dc "$new_admin_archive" | docker_cli load >/dev/null
for item in "Backend|$new_backend_image|$new_backend_commit" "Admin|$new_admin_image|$new_admin_commit"; do
  IFS='|' read -r name image commit <<<"$item"
  revision="$(docker_cli image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image")"
  [[ "$revision" = "$commit" ]] || fail "new $name image revision mismatch"
done

log "configuring course OSS browser-upload CORS"
docker_cli run --rm \
  --entrypoint python \
  --user "$(id -u):$(id -g)" \
  -v "$DEPLOYMENT_ROOT/installation:$DEPLOYMENT_ROOT/installation:ro" \
  "$new_backend_image" scripts/configure_course_oss.py \
  --installation-dir "$DEPLOYMENT_ROOT/installation" >/dev/null

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="$DEPLOYMENT_ROOT/backups/postgresql"
backup_file="$backup_dir/${RELEASE}-${timestamp}.dump"
encrypted_file="$backup_dir/${RELEASE}-${timestamp}.dump.enc"
encrypted_key="$backup_dir/${RELEASE}-${timestamp}.key.enc"
oss_bundle="$backup_dir/${RELEASE}-${timestamp}.oss.tar.gz"
manifest_file="$backup_dir/${RELEASE}-${timestamp}.json"
install -d -m 0700 "$backup_dir"
old_release_env_backup="$UPDATER_ROOT/cache/${RELEASE}-${timestamp}.release.env"
old_source_pins_backup="$UPDATER_ROOT/cache/${RELEASE}-${timestamp}.source-pins.env"
cp "$old_release_env" "$old_release_env_backup"
cp "$control_dir/source-pins.env" "$old_source_pins_backup"

new_release_work="$UPDATER_ROOT/cache/$RELEASE.release.env"
python3 - "$old_release_env" "$new_source_file" "$new_release_work" <<'PY'
import os
import sys
from pathlib import Path


def read_env(path):
    result = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, separator, value = line.partition("=")
            result[key] = value
    return result


old = read_env(sys.argv[1])
source = read_env(sys.argv[2])
preserve = {
    "ADMIN_PORT", "BACKEND_PORT", "RENSHE_TEMPLATE_HOST_DIR",
    "WEMINI_COMPOSE_PROJECT", "WEMINI_DEPLOYMENT_ROOT",
}
result = {key: value for key, value in old.items() if key in preserve}
result.update({
    "ADMIN_IMAGE": source["ADMIN_IMAGE"],
    "BACKEND_IMAGE": source["BACKEND_IMAGE"],
    "WEMINI_ADMIN_COMMIT": source["ADMIN_COMMIT"],
    "WEMINI_BACKEND_COMMIT": source["BACKEND_COMMIT"],
    "WEMINI_COMPOSE_PROJECT": result.get("WEMINI_COMPOSE_PROJECT", os.environ.get("WEMINI_COMPOSE_PROJECT", "")),
    "WEMINI_DEPLOYMENT_ROOT": result.get("WEMINI_DEPLOYMENT_ROOT", os.environ.get("WEMINI_DEPLOYMENT_ROOT", "")),
    "WEMINI_RELEASE_TAG": source["RELEASE_TAG"],
})
Path(sys.argv[3]).write_text(
    "".join(f"{key}={result[key]}\n" for key in sorted(result)),
    encoding="utf-8",
)
PY

# The updater receives these values as arguments, not from the old release.
sed -i \
  -e "s#^WEMINI_COMPOSE_PROJECT=.*#WEMINI_COMPOSE_PROJECT=$COMPOSE_PROJECT#" \
  -e "s#^WEMINI_DEPLOYMENT_ROOT=.*#WEMINI_DEPLOYMENT_ROOT=$DEPLOYMENT_ROOT#" \
  "$new_release_work"
chmod 0600 "$new_release_work"

SERVICE_STOPPED=0
DATABASE_TOUCHED=0
NEW_STARTED=0
ROLLBACK_DONE=0

restore_database() {
  compose_for "$old_compose" "$old_release_env" exec -T db \
    sh -c "exec psql -U \"\$POSTGRES_USER\" -d postgres -v ON_ERROR_STOP=1" \
    <<<"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$runtime_db_name' AND pid <> pg_backend_pid();" >/dev/null
  compose_for "$old_compose" "$old_release_env" exec -T db \
    sh -c "exec psql -U \"\$POSTGRES_USER\" -d postgres -v ON_ERROR_STOP=1" \
    <<<"DROP DATABASE WITH (FORCE) \"$runtime_db_name\";" >/dev/null
  compose_for "$old_compose" "$old_release_env" exec -T db \
    sh -c "exec psql -U \"\$POSTGRES_USER\" -d postgres -v ON_ERROR_STOP=1" \
    <<<"CREATE DATABASE \"$runtime_db_name\" OWNER \"$runtime_db_user\";" >/dev/null
  compose_for "$old_compose" "$old_release_env" exec -T db \
    sh -c "exec pg_restore -U \"\$POSTGRES_USER\" -d \"$runtime_db_name\" --no-owner --no-privileges" \
    <"$backup_file" >/dev/null
}

rollback() {
  [[ "$ROLLBACK_DONE" = "0" ]] || return 0
  ROLLBACK_DONE=1
  log "upgrade failed; rolling back release $old_release_tag"
  if [[ "$NEW_STARTED" = "1" ]]; then
    compose_for "$new_compose" "$new_release_work" stop app quiz-worker admin >/dev/null 2>&1 || true
  fi
  if [[ "$DATABASE_TOUCHED" = "1" ]]; then
    log "restoring PostgreSQL backup"
    restore_database || fail "database rollback failed; keep $backup_file and recover manually"
  fi
  install -m 0600 "$old_release_env_backup" "$old_release_env"
  install -m 0600 "$old_source_pins_backup" "$control_dir/source-pins.env"
  compose_for "$old_compose" "$old_release_env" up -d uploads-init app quiz-worker admin >/dev/null
  check_readiness 180 || fail "old Backend did not become healthy after rollback"
  check_admin || log "WARNING: old Admin health check failed after rollback"
  log "rollback to $old_release_tag completed"
}

on_error() {
  local exit_code=$?
  printf '[wemini-updater] ERROR: upgrade failed at line %s, exit=%s\n' "$1" "$exit_code" >&2
  rollback
  exit "$exit_code"
}
trap 'on_error $LINENO' ERR

log "stopping Backend, quiz-worker and Admin for the maintenance window"
compose_for "$old_compose" "$old_release_env" stop app quiz-worker admin >/dev/null
SERVICE_STOPPED=1

log "creating PostgreSQL custom backup"
compose_for "$old_compose" "$old_release_env" exec -T db \
  sh -c "exec pg_dump -U \"\$POSTGRES_USER\" --format=custom \"$runtime_db_name\"" \
  >"$backup_file"
(( $(stat -c '%s' "$backup_file") > 0 )) || fail "database backup is empty"
compose_for "$old_compose" "$old_release_env" exec -T db \
  sh -c "exec pg_restore --list >/dev/null" <"$backup_file" >/dev/null \
  || fail "database backup archive validation failed"
backup_sha256="$(sha256sum "$backup_file" | awk '{print $1}')"
log "backup sha256: $backup_sha256"

backup_key="$backup_dir/.key.$$.raw"
cleanup_key() {
  rm -f "$backup_key"
}
trap cleanup_key EXIT
openssl rand -out "$backup_key" 32
openssl enc -aes-256-cbc -salt -pbkdf2 \
  -in "$backup_file" -out "$encrypted_file" -pass file:"$backup_key"
openssl pkeyutl -encrypt -pubin \
  -inkey "$DEPLOYMENT_ROOT/installation/recovery_public_key.pem" \
  -pkeyopt rsa_padding_mode:oaep -pkeyopt digest:sha256 \
  -in "$backup_key" -out "$encrypted_key"
rm -f "$backup_key"
encrypted_sha256="$(sha256sum "$encrypted_file" | awk '{print $1}')"
python3 - "$backup_file" "$encrypted_file" "$encrypted_key" "$manifest_file" "$RELEASE" "$backup_sha256" "$encrypted_sha256" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

backup, encrypted, key_file, manifest, release, backup_sha, encrypted_sha = sys.argv[1:]
payload = {
    "format": "wemini-postgresql-backup-v1",
    "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "release_tag": release,
    "original_name": Path(backup).name,
    "original_sha256": backup_sha,
    "encrypted_sha256": encrypted_sha,
    "encryption": {
        "data": "AES-256-CBC-PBKDF2",
        "key": "RSA-OAEP-SHA256",
        "key_file": Path(key_file).name,
    },
}
Path(manifest).write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
PY
tar -czf "$oss_bundle" -C "$backup_dir" \
  "$(basename "$encrypted_file")" "$(basename "$encrypted_key")" "$(basename "$manifest_file")"

installation_id="$(python3 - "$control_dir/state.json" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["payload"]["installation_id"])
PY
)"
oss_prefix="wemini-backups/postgresql/$installation_id"
log "uploading encrypted backup to OSS prefix: $oss_prefix"
docker_cli run --rm \
  --entrypoint python \
  --user "$(id -u):$(id -g)" \
  -v "$DEPLOYMENT_ROOT:$DEPLOYMENT_ROOT:ro" \
  -v "$backup_dir:$backup_dir:ro" \
  "$new_backend_image" scripts/oss_backup.py upload \
  --installation-dir "$DEPLOYMENT_ROOT/installation" \
  --file "$oss_bundle" \
  --object-key "$oss_prefix/$RELEASE-$timestamp.oss.tar.gz" \
  --content-type application/octet-stream >/dev/null
docker_cli run --rm \
  --entrypoint python \
  --user "$(id -u):$(id -g)" \
  -v "$DEPLOYMENT_ROOT:$DEPLOYMENT_ROOT:ro" \
  "$new_backend_image" scripts/oss_backup.py prune \
  --installation-dir "$DEPLOYMENT_ROOT/installation" \
  --prefix "$oss_prefix/" \
  --retain-days 7 --min-objects 7 >/dev/null
rm -f "$encrypted_file" "$encrypted_key" "$manifest_file" "$oss_bundle"

log "running new Alembic migration"
DATABASE_TOUCHED=1
compose_for "$new_compose" "$new_release_work" run --rm --no-deps migration >/dev/null

log "starting new Backend, quiz-worker and Admin"
compose_for "$new_compose" "$new_release_work" up -d uploads-init app quiz-worker admin >/dev/null
NEW_STARTED=1
check_readiness 240 || fail "new Backend did not become healthy"
check_admin || fail "new Admin did not become healthy"
new_ready="$(check_readiness 10)" || true
if [[ -n "$new_ready" ]]; then
  log "new readiness dependencies: $(readiness_value "$new_ready" checks)"
fi

local_count="$(find "$backup_dir" -maxdepth 1 -type f -name '*.dump' | wc -l)"
if (( local_count > 7 )); then
  find "$backup_dir" -maxdepth 1 -type f -name '*.dump' -printf '%T@ %p\n' \
    | sort -rn | tail -n +"$((local_count - 6))" \
    | while read -r _ path; do rm -f "$path"; done
fi
find "$backup_dir" -maxdepth 1 -type f -name '*.dump' -mtime +7 -printf '%p\n' \
  | while read -r path; do
      local_count="$(find "$backup_dir" -maxdepth 1 -type f -name '*.dump' | wc -l)"
      (( local_count > 7 )) && rm -f "$path"
    done

if [[ -f "$bundle_dir/upgrade_release.sh" ]]; then
  install -m 0755 "$bundle_dir/upgrade_release.sh" "$UPDATER_ROOT/upgrade_release.sh.new"
  mv "$UPDATER_ROOT/upgrade_release.sh.new" "$UPDATER_ROOT/upgrade_release.sh"
fi
ln -sfn "$bundle_dir" "$UPDATER_ROOT/current"

install -m 0600 "$new_release_work" "$control_dir/release.env.new"
mv "$control_dir/release.env.new" "$old_release_env"
{
  printf 'RELEASE_TAG=%s\n' "$RELEASE"
  printf 'BACKEND_COMMIT=%s\n' "$new_backend_commit"
  printf 'ADMIN_COMMIT=%s\n' "$new_admin_commit"
} >"$control_dir/source-pins.env.new"
chmod 0600 "$control_dir/source-pins.env.new"
mv "$control_dir/source-pins.env.new" "$control_dir/source-pins.env"

trap - ERR
log "upgrade to $RELEASE completed successfully"
log "local backup: $backup_file"
log "maintenance page should automatically disappear when traffic reaches the new service"
