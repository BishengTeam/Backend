"""Build the small deployment bundle accompanying two Docker image archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
RELEASE_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ARCHIVE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.tar\.zst$")
MAX_RELEASE_ASSET_BYTES = 2 * 1024 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive(path: str) -> Path:
    candidate = Path(path).resolve()
    if not candidate.is_file() or candidate.is_symlink():
        raise ValueError(f"image archive is unavailable: {candidate}")
    if not ARCHIVE_RE.fullmatch(candidate.name):
        raise ValueError(f"image archive name is unsafe: {candidate.name}")
    if candidate.stat().st_size >= MAX_RELEASE_ASSET_BYTES:
        raise ValueError(f"image archive reaches the GitHub Release 2 GiB limit: {candidate.name}")
    return candidate


def _copy(source: Path, target: Path, *, executable: bool = False) -> None:
    if not source.is_file():
        raise ValueError(f"release input is unavailable: {source}")
    shutil.copy2(source, target)
    if executable:
        target.chmod(0o755)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--tooling-commit", required=True)
    parser.add_argument("--backend-commit", required=True)
    parser.add_argument("--admin-commit", required=True)
    parser.add_argument("--backend-archive", required=True)
    parser.add_argument("--admin-archive", required=True)
    parser.add_argument(
        "--runtime-compose",
        default=str(ROOT / "docker-compose.deploy.yml"),
    )
    parser.add_argument("--output-dir", required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if not RELEASE_TAG_RE.fullmatch(args.release_tag):
        raise SystemExit("release tag is invalid")
    if not COMMIT_RE.fullmatch(args.tooling_commit):
        raise SystemExit("release tooling commit is invalid")
    if not COMMIT_RE.fullmatch(args.backend_commit):
        raise SystemExit("Backend commit is invalid")
    if not COMMIT_RE.fullmatch(args.admin_commit):
        raise SystemExit("Admin commit is invalid")
    try:
        backend_archive = _archive(args.backend_archive)
        admin_archive = _archive(args.admin_archive)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    bundle_name = f"wemini-deploy-{args.release_tag}"
    bundle_dir = output_dir / bundle_name
    if bundle_dir.exists() or bundle_dir.is_symlink():
        raise SystemExit(f"release bundle path already exists: {bundle_dir}")
    bundle_dir.mkdir(mode=0o755)

    _copy(
        ROOT / "docker-compose.bootstrap.release.yml",
        bundle_dir / "docker-compose.bootstrap.release.yml",
    )
    _copy(
        Path(args.runtime_compose).resolve(),
        bundle_dir / "docker-compose.deploy.yml",
    )
    _copy(
        ROOT / "scripts/bootstrap_server.sh",
        bundle_dir / "bootstrap_server.sh",
        executable=True,
    )
    _copy(
        ROOT / "scripts/install_release.sh",
        bundle_dir / "install_release.sh",
        executable=True,
    )
    _copy(
        ROOT / "scripts/upgrade_release.sh",
        bundle_dir / "upgrade_release.sh",
        executable=True,
    )
    _copy(
        ROOT / "deploy/nginx/maintenance.html",
        bundle_dir / "maintenance.html",
    )

    backend_image = f"wemini-backend:{args.backend_commit}"
    admin_image = f"wemini-admin:{args.admin_commit}"
    source_values = {
        "RELEASE_BUNDLE_VERSION": "1",
        "RELEASE_TAG": args.release_tag,
        "TOOLING_COMMIT": args.tooling_commit,
        "BACKEND_COMMIT": args.backend_commit,
        "ADMIN_COMMIT": args.admin_commit,
        "BACKEND_REMOTE": "https://github.com/BishengTeam/Backend.git",
        "ADMIN_REMOTE": "https://github.com/BishengTeam/Admin.git",
        "BACKEND_IMAGE": backend_image,
        "ADMIN_IMAGE": admin_image,
        "BACKEND_IMAGE_ARCHIVE": backend_archive.name,
        "ADMIN_IMAGE_ARCHIVE": admin_archive.name,
        "BACKEND_IMAGE_ARCHIVE_SHA256": _sha256(backend_archive),
        "ADMIN_IMAGE_ARCHIVE_SHA256": _sha256(admin_archive),
    }
    source_file = bundle_dir / "release-source.env"
    source_file.write_text(
        "".join(f"{key}={value}\n" for key, value in source_values.items()),
        encoding="utf-8",
    )
    source_file.chmod(0o644)

    (bundle_dir / "README.txt").write_text(
        "1. Download this deployment archive, both .tar.zst image assets, and "
        "SHA256SUMS into one directory.\n"
        "2. Verify: sha256sum -c SHA256SUMS\n"
        f"3. Extract this archive and run: ./{bundle_name}/install_release.sh\n"
        "4. Run as the deployment user; if needed use DOCKER_USE_SUDO=1, "
        "never sudo the entire installer.\n",
        encoding="utf-8",
    )

    deployment_archive = output_dir / f"{bundle_name}.tar.gz"
    with tarfile.open(deployment_archive, mode="w:gz", compresslevel=6) as archive:
        archive.add(bundle_dir, arcname=bundle_name, recursive=True)

    checksum_assets = (backend_archive, admin_archive, deployment_archive)
    checksum_file = output_dir / "SHA256SUMS"
    checksum_file.write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in checksum_assets),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "release_tag": args.release_tag,
                "deployment_archive": deployment_archive.name,
                "backend_image": backend_image,
                "admin_image": admin_image,
                "checksums": checksum_file.name,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
