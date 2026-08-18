from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from bootstrap_app.config import BootstrapSettings
from bootstrap_app.state import (
    BootstrapPhase,
    BootstrapStateStore,
    _atomic_private_write,
)


COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*:[A-Za-z0-9][A-Za-z0-9._-]*$")
RELEASE_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _safe_remote(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or "\x00" in cleaned or "\r" in cleaned or "\n" in cleaned:
        raise ValueError("repository remote is invalid")
    parsed = urlsplit(cleaned)
    if parsed.scheme:
        if parsed.username or parsed.password:
            raise ValueError("credential-bearing repository remotes are forbidden")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    # SCP-style SSH remotes must not contain a password or URL query.
    if "?" in cleaned or "#" in cleaned or "://" in cleaned:
        raise ValueError("repository remote is invalid")
    return cleaned


def _single_line(value: str, name: str, *, allow_space: bool = False) -> str:
    cleaned = value.strip()
    if not cleaned or any(character in cleaned for character in "\x00\r\n"):
        raise ValueError(f"{name} is invalid")
    if not allow_space and any(character.isspace() for character in cleaned):
        raise ValueError(f"{name} must not contain whitespace")
    return cleaned


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write the immutable prebuilt or local-build release manifest."
    )
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--backend-commit", required=True)
    parser.add_argument("--admin-commit", required=True)
    parser.add_argument("--backend-remote", required=True)
    parser.add_argument("--admin-remote", required=True)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--admin-image", required=True)
    parser.add_argument("--backend-image-id", required=True)
    parser.add_argument("--admin-image-id", required=True)
    parser.add_argument(
        "--quality-source",
        choices=("ci_prebuilt", "github_release", "preloaded", "server_build"),
        default="server_build",
    )
    parser.add_argument("--template-dir", required=True)
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--admin-port", type=int, default=8080)
    parser.add_argument("--compose-project", required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if not RELEASE_TAG_RE.fullmatch(args.release_tag):
        raise SystemExit("release manifest refused: release_tag is invalid")
    for name, value in {
        "backend_commit": args.backend_commit,
        "admin_commit": args.admin_commit,
    }.items():
        if not COMMIT_RE.fullmatch(value):
            raise SystemExit(f"release manifest refused: {name} is invalid")
    for name, value in {
        "backend_image_id": args.backend_image_id,
        "admin_image_id": args.admin_image_id,
    }.items():
        if not IMAGE_ID_RE.fullmatch(value):
            raise SystemExit(f"release manifest refused: {name} is invalid")
    for name, value in {
        "backend_image": args.backend_image,
        "admin_image": args.admin_image,
    }.items():
        if not IMAGE_NAME_RE.fullmatch(value):
            raise SystemExit(f"release manifest refused: {name} is invalid")
    if not 1 <= args.backend_port <= 65535 or not 1 <= args.admin_port <= 65535:
        raise SystemExit("release manifest refused: port is invalid")

    settings = BootstrapSettings.from_env()
    store = BootstrapStateStore(settings.control_dir, settings.token)
    state = store.load(allow_completed=False)
    if state.phase != BootstrapPhase.QUALITY_RUNNING:
        raise SystemExit("release manifest refused: phase must be QUALITY_RUNNING")
    try:
        backend_remote = _safe_remote(args.backend_remote)
        admin_remote = _safe_remote(args.admin_remote)
        template_dir = _single_line(args.template_dir, "template_dir")
    except ValueError as exc:
        raise SystemExit(f"release manifest refused: {exc}") from exc

    payload = {
        "version": 1,
        "installation_id": state.installation_id,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "release": {"tag": args.release_tag},
        "source": {
            "backend": {"remote": backend_remote, "commit": args.backend_commit},
            "admin": {"remote": admin_remote, "commit": args.admin_commit},
        },
        "images": {
            "backend": {"name": args.backend_image, "id": args.backend_image_id},
            "admin": {"name": args.admin_image, "id": args.admin_image_id},
        },
        "quality": {
            "source": args.quality_source,
            "backend_unit_contract_migration": "passed",
            "backend_isolated_postgresql": "passed",
            "admin_test_build": "passed",
        },
    }
    manifest_bytes = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    _atomic_private_write(settings.control_dir / "release-manifest.json", manifest_bytes)

    release_env = {
        "ADMIN_IMAGE": args.admin_image,
        "ADMIN_PORT": str(args.admin_port),
        "BACKEND_IMAGE": args.backend_image,
        "BACKEND_PORT": str(args.backend_port),
        "WEMINI_ADMIN_COMMIT": args.admin_commit,
        "WEMINI_BACKEND_COMMIT": args.backend_commit,
        "WEMINI_COMPOSE_PROJECT": args.compose_project,
        "WEMINI_DEPLOYMENT_ROOT": str(settings.host_deploy_root),
        "WEMINI_RELEASE_TAG": args.release_tag,
        "RENSHE_TEMPLATE_HOST_DIR": template_dir,
    }
    env_bytes = ("\n".join(f"{key}={release_env[key]}" for key in sorted(release_env)) + "\n").encode()
    _atomic_private_write(settings.control_dir / "release.env", env_bytes)
    store.transition(
        BootstrapPhase.QUALITY_RUNNING,
        BootstrapPhase.QUALITY_PASSED,
        backend_commit=args.backend_commit,
        admin_commit=args.admin_commit,
        release_manifest_sha256=manifest_sha256,
    )
    print(
        json.dumps(
            {"status": "ok", "release_manifest_sha256": manifest_sha256},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
