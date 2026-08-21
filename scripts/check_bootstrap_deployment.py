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
    release_bootstrap_compose = (
        ROOT / "docker-compose.bootstrap.release.yml"
    ).read_text("utf-8")
    runtime_compose = (ROOT / "docker-compose.deploy.yml").read_text("utf-8")
    orchestrator = (ROOT / "scripts/bootstrap_server.sh").read_text("utf-8")
    installer = (ROOT / "scripts/install_server.sh").read_text("utf-8")
    release_installer = (ROOT / "scripts/install_release.sh").read_text("utf-8")
    release_packager = (ROOT / "scripts/package_github_release.py").read_text("utf-8")
    image_workflow = (
        ROOT / ".github/workflows/publish-github-release.yml"
    ).read_text("utf-8")
    production_seed = (ROOT / "scripts/seed_production.py").read_text("utf-8")

    require(
        bootstrap_compose,
        "127.0.0.1:${BOOTSTRAP_PORT:-18080}:18080",
        "loopback-only bootstrap port",
    )
    require(bootstrap_compose, "bootstrap_app.main:create_app", "bootstrap app factory")
    require(
        release_bootstrap_compose,
        "127.0.0.1:${BOOTSTRAP_PORT:-18080}:18080",
        "release loopback-only bootstrap port",
    )
    forbid(release_bootstrap_compose, "build:", "release image build")
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
    require(
        runtime_compose,
        "user: ${BOOTSTRAP_UID:?BOOTSTRAP_UID is required}:${BOOTSTRAP_GID:?BOOTSTRAP_GID is required}",
        "Backend runtime runs as the secret-owning bootstrap user",
    )
    require(
        runtime_compose,
        "chown -R ${BOOTSTRAP_UID:?BOOTSTRAP_UID is required}:${BOOTSTRAP_GID:?BOOTSTRAP_GID is required} /app/uploads",
        "uploads volume ownership matches the runtime user",
    )
    require(
        runtime_compose,
        "RENSHE_STORAGE_TYPE: ${RENSHE_STORAGE_TYPE:-disabled}",
        "optional human-resources OSS mode",
    )
    require(
        runtime_compose,
        "QUIZ_IMPORT_STORAGE_TYPE: ${QUIZ_IMPORT_STORAGE_TYPE:-disabled}",
        "optional quiz OSS mode",
    )
    forbid(
        runtime_compose,
        "${ALIYUN_OSS_ENDPOINT:?",
        "required human-resources OSS endpoint",
    )
    forbid(
        runtime_compose,
        "${QUIZ_OSS_ENDPOINT:?",
        "required quiz OSS endpoint",
    )
    require(orchestrator, "refs/heads/main:refs/remotes/origin/main", "single main fetch")
    require(orchestrator, "git clone --branch main", "automatic Admin clone")
    require(orchestrator, "status --porcelain", "dirty worktree refusal")
    require(orchestrator, 'DEPLOY_SOURCE_MODE="${DEPLOY_SOURCE_MODE:-git}"', "source mode")
    require(orchestrator, "release-source.env", "GitHub Release source manifest")
    require(orchestrator, 'docker_cli pull "$BACKEND_IMAGE"', "prebuilt Backend pull")
    require(orchestrator, 'docker_cli pull "$ADMIN_IMAGE"', "prebuilt Admin pull")
    require(
        orchestrator,
        "org.opencontainers.image.revision",
        "prebuilt image revision verification",
    )
    require(orchestrator, "--no-build bootstrap", "prebuilt bootstrap start")
    require(
        orchestrator,
        'BOOTSTRAP_UID="$BOOTSTRAP_UID"',
        "runtime Compose receives the secret-owning UID",
    )
    require(installer, "git clone --branch main", "automatic Backend clone")
    require(release_installer, "zstd -dc", "release image decompression")
    require(release_installer, "docker_cli load", "release image load")
    require(release_installer, "DEPLOY_SOURCE_MODE=release", "source-free release mode")
    require(release_installer, "DOCKER_USE_SUDO", "Docker-only sudo mode")
    require(release_packager, "MAX_RELEASE_ASSET_BYTES", "GitHub asset size guard")
    require(release_packager, "SHA256SUMS", "release checksum manifest")
    require(
        release_packager,
        'ROOT / "scripts/upgrade_release.sh"',
        "online updater in deployment bundle",
    )
    require(
        release_packager,
        'ROOT / "deploy/nginx/maintenance.html"',
        "maintenance fallback page in deployment bundle",
    )
    require(orchestrator, "--release-tag", "release metadata tag")
    require(runtime_compose, "WEMINI_RELEASE_TAG:", "runtime release tag metadata")
    require(image_workflow, "contents: write", "Release publish permission")
    require(
        image_workflow,
        "python -m alembic upgrade head",
        "CI PostgreSQL migration before integration tests",
    )
    require(image_workflow, "tests/integration/db", "CI PostgreSQL integration tests")
    if image_workflow.index("python -m alembic upgrade head") > image_workflow.index(
        "tests/integration/db"
    ):
        raise SystemExit(
            "CI PostgreSQL migration must run before the integration tests"
        )
    require(image_workflow, "docker save", "Docker image archive export")
    require(image_workflow, "gh release create", "GitHub Release creation")
    require(image_workflow, "SHA256SUMS", "GitHub Release checksums")
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
    require(
        production_seed,
        'PRODUCTION_SEED_VERSION = "2026.08.21.1"',
        "course-domain-aware production seed version",
    )
    require(
        production_seed,
        "COURSE_REQUIRED_TABLES",
        "course-domain migration gate",
    )
    require(
        production_seed,
        '"course_domain_ready": True',
        "course-domain seed evidence",
    )

    for source, label in (
        (bootstrap_compose, "bootstrap Compose"),
        (release_bootstrap_compose, "release bootstrap Compose"),
        (runtime_compose, "runtime Compose"),
        (orchestrator, "orchestrator"),
        (release_installer, "release installer"),
    ):
        forbid(source, "/var/run/docker.sock", f"Docker Socket in {label}")
        forbid(source, "docker.sock", f"Docker Socket alias in {label}")
    forbid(orchestrator, "git reset", "destructive Git reset")
    forbid(orchestrator, "git clean", "destructive Git clean")
    forbid(orchestrator, 'fail "missing official template', "hard template preflight")
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
