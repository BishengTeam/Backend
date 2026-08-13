#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd -- "$BACKEND_ROOT/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$BACKEND_ROOT/.venv/bin/python}"
NPM_BIN="${NPM_BIN:-npm}"
MODE="${1:-check}"

case "$MODE" in
  check|full) ;;
  *)
    printf 'usage: %s [check|full]\n' "$0" >&2
    exit 2
    ;;
esac

[[ -x "$PYTHON_BIN" ]] || { printf 'joint release gate: Python is unavailable\n' >&2; exit 1; }

manifest_args=(
  --check "$BACKEND_ROOT/app/contracts/quiz_contract_manifest.json"
  --check "$REPO_ROOT/Admin/src/contracts/quiz-contract.json"
  --check "$REPO_ROOT/Platform/src/contracts/quiz-contract.json"
  --scan-client "$REPO_ROOT/Admin/src"
  --scan-client "$REPO_ROOT/Platform/src"
)

printf '\n== Joint quiz contract ==\n'
"$PYTHON_BIN" "$BACKEND_ROOT/scripts/quiz_contract_manifest.py" "${manifest_args[@]}"

printf '\n== Project quality command inventory ==\n'
"$PYTHON_BIN" - "$REPO_ROOT" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
requirements = {
    "Admin": ("test", "build", "test:e2e"),
    "Platform": ("typecheck", "test", "build:weapp"),
}
missing = []
for project, commands in requirements.items():
    package = json.loads((root / project / "package.json").read_text(encoding="utf-8"))
    scripts = package.get("scripts") or {}
    for command in commands:
        if not scripts.get(command):
            missing.append(f"{project}:npm run {command}")
if missing:
    raise SystemExit("missing mandatory quality commands: " + ", ".join(missing))
print("quality_command_inventory=ok")
PY

if [[ "$MODE" == "full" ]]; then
  printf '\n== Backend quality gate ==\n'
  "$BACKEND_ROOT/scripts/quality_gate.sh" backend

  printf '\n== Admin test/build ==\n'
  (cd "$REPO_ROOT/Admin" && "$NPM_BIN" test -- --no-cache && "$NPM_BIN" run build)

  printf '\n== Platform typecheck/test/build ==\n'
  (cd "$REPO_ROOT/Platform" && "$NPM_BIN" run typecheck && "$NPM_BIN" test && "$NPM_BIN" run build:weapp)
fi

printf '\njoint_release_gate=passed mode=%s\n' "$MODE"
