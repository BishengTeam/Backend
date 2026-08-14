#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

DEPLOY_ROOT="${BOOTSTRAP_HOST_DEPLOY_ROOT:-/srv/wemini-bootstrap}"
BACKEND_DIR="${BACKEND_DIR:-$DEPLOY_ROOT/Backend}"
BACKEND_GIT_REMOTE="${BACKEND_GIT_REMOTE:-https://github.com/BishengTeam/Backend.git}"

fail() {
  printf '[installer] ERROR: %s\n' "$*" >&2
  exit 1
}

(( EUID != 0 )) \
  || fail "do not run this installer with sudo; prepare DEPLOY_ROOT for a deployment user"
command -v git >/dev/null 2>&1 || fail "Git is unavailable"
[[ "$DEPLOY_ROOT" = /* && "$DEPLOY_ROOT" != "/" ]] \
  || fail "BOOTSTRAP_HOST_DEPLOY_ROOT must be a safe absolute path"
[[ "$BACKEND_DIR" = /* && "$BACKEND_DIR" != "/" ]] \
  || fail "BACKEND_DIR must be a safe absolute path"
[[ "$DEPLOY_ROOT" != *$'\n'* && "$DEPLOY_ROOT" != *$'\r'* \
  && "$DEPLOY_ROOT" != *' '* && "$DEPLOY_ROOT" != *$'\t'* ]] \
  || fail "BOOTSTRAP_HOST_DEPLOY_ROOT contains invalid whitespace"
[[ "$BACKEND_GIT_REMOTE" != *://*@* ]] \
  || fail "BACKEND_GIT_REMOTE must not contain URL credentials"

mkdir -p "$DEPLOY_ROOT" \
  || fail "cannot create DEPLOY_ROOT; create it once with sudo install and retry"
chmod 0700 "$DEPLOY_ROOT"

if git -C "$BACKEND_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if [[ -n "$(git -C "$BACKEND_DIR" status --porcelain --untracked-files=normal)" ]]; then
    fail "Backend worktree is not clean; refusing to overwrite local changes"
  fi
else
  [[ ! -e "$BACKEND_DIR" ]] \
    || fail "Backend path exists but is not a Git worktree: $BACKEND_DIR"
  printf '[installer] cloning Backend origin/main\n'
  git clone --branch main --single-branch "$BACKEND_GIT_REMOTE" "$BACKEND_DIR" \
    || fail "unable to clone Backend repository"
fi

printf '[installer] refreshing Backend origin/main\n'
git -C "$BACKEND_DIR" fetch --prune origin \
  refs/heads/main:refs/remotes/origin/main
git -C "$BACKEND_DIR" checkout --detach refs/remotes/origin/main

exec env \
  BOOTSTRAP_HOST_DEPLOY_ROOT="$DEPLOY_ROOT" \
  "$BACKEND_DIR/scripts/bootstrap_server.sh"
