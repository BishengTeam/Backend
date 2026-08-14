"""Static fail-closed checks for the one-time deployment bootstrap assets."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(source: str, fragment: str, label: str) -> None:
    if fragment not in source:
        raise SystemExit(f"bootstrap deployment check failed: missing {label}")


def forbid(source: str, fragment: str, label: str) -> None:
    if fragment in source:
        raise SystemExit(f"bootstrap deployment check failed: forbidden {label}")


def main() -> None:
    bootstrap_compose = (ROOT / "docker-compose.bootstrap.yml").read_text("utf-8")
    runtime_compose = (ROOT / "docker-compose.deploy.yml").read_text("utf-8")
    orchestrator = (ROOT / "scripts/bootstrap_server.sh").read_text("utf-8")
    production_seed = (ROOT / "scripts/seed_production.py").read_text("utf-8")

    require(
        bootstrap_compose,
        "127.0.0.1:${BOOTSTRAP_PORT:-18080}:18080",
        "loopback-only bootstrap port",
    )
    require(bootstrap_compose, "bootstrap_app.main:create_app", "bootstrap app factory")
    require(runtime_compose, 'RUN_MIGRATIONS: "false"', "runtime migration disable")
    require(runtime_compose, "migration:", "dedicated migration job")
    require(runtime_compose, "production-seed:", "production seed job")
    require(
        runtime_compose,
        "127.0.0.1:${BACKEND_PORT:-8000}:8000",
        "loopback-only Backend port",
    )
    require(
        runtime_compose,
        "127.0.0.1:${ADMIN_PORT:-8080}:80",
        "loopback-only Admin port",
    )
    require(runtime_compose, "REDIS_URL_FILE: /run/secrets/redis_url", "Redis secret")
    require(orchestrator, "refs/heads/main:refs/remotes/origin/main", "single main fetch")
    require(orchestrator, "status --porcelain", "dirty worktree refusal")
    require(orchestrator, "tests/integration/db", "isolated PostgreSQL tests")
    require(orchestrator, "INSTALLED_PENDING_UAT", "post-install UAT state")
    require(
        orchestrator,
        "bootstrap_app.acceptance_cli register",
        "database acceptance registration",
    )
    require(
        orchestrator,
        "bootstrap_app.acceptance_cli sync-state",
        "terminal acceptance reconciliation",
    )
    require(
        orchestrator,
        "--no-deps migration \\",
        "migration fail-closed guard",
    )
    require(
        orchestrator,
        "acceptance_cli register >/dev/null \\",
        "acceptance registration fail-closed guard",
    )
    if orchestrator.count("|| return 1") < 5:
        raise SystemExit(
            "bootstrap deployment check failed: explicit fail-closed guards missing"
        )
    require(production_seed, "PRODUCTION_SEED_VERSION", "versioned production seed")

    for source, label in (
        (bootstrap_compose, "bootstrap Compose"),
        (runtime_compose, "runtime Compose"),
        (orchestrator, "orchestrator"),
    ):
        forbid(source, "/var/run/docker.sock", f"Docker Socket in {label}")
        forbid(source, "docker.sock", f"Docker Socket alias in {label}")
    forbid(orchestrator, "git reset", "destructive Git reset")
    forbid(orchestrator, "git clean", "destructive Git clean")
    forbid(orchestrator, "Platform", "Platform deployment")
    forbid(production_seed, "seed_testdata", "test data import")
    forbid(production_seed, '"--force"', "force seed mode")

    if not re.search(r"profiles:\s*\[internal\]", runtime_compose):
        raise SystemExit(
            "bootstrap deployment check failed: internal infrastructure profile missing"
        )
    subprocess.run(
        ["bash", "-n", str(ROOT / "scripts/bootstrap_server.sh")],
        check=True,
    )
    print("bootstrap_deployment_contract=ok")


if __name__ == "__main__":
    main()
