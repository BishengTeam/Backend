from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tarfile

import pytest

from scripts import package_github_release


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_package_github_release_builds_source_free_checked_bundle(
    tmp_path, monkeypatch, capsys
):
    backend_commit = "a" * 40
    admin_commit = "b" * 40
    backend_archive = tmp_path / f"wemini-backend-{backend_commit}.tar.zst"
    admin_archive = tmp_path / f"wemini-admin-{admin_commit}.tar.zst"
    backend_archive.write_bytes(b"backend-image-archive")
    admin_archive.write_bytes(b"admin-image-archive")
    output = tmp_path / "dist"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "package_github_release.py",
            "--release-tag",
            "2026.08.14.1",
            "--tooling-commit",
            "c" * 40,
            "--backend-commit",
            backend_commit,
            "--admin-commit",
            admin_commit,
            "--backend-archive",
            str(backend_archive),
            "--admin-archive",
            str(admin_archive),
            "--output-dir",
            str(output),
        ],
    )

    package_github_release.main()

    bundle_name = "wemini-deploy-2026.08.14.1"
    bundle = output / bundle_name
    source = (bundle / "release-source.env").read_text(encoding="utf-8")
    assert f"TOOLING_COMMIT={'c' * 40}\n" in source
    assert f"BACKEND_COMMIT={backend_commit}\n" in source
    assert f"ADMIN_COMMIT={admin_commit}\n" in source
    assert f"BACKEND_IMAGE=wemini-backend:{backend_commit}\n" in source
    assert f"ADMIN_IMAGE=wemini-admin:{admin_commit}\n" in source
    assert f"BACKEND_IMAGE_ARCHIVE_SHA256={_sha256(backend_archive)}\n" in source
    assert f"ADMIN_IMAGE_ARCHIVE_SHA256={_sha256(admin_archive)}\n" in source
    assert (bundle / "docker-compose.bootstrap.release.yml").is_file()
    assert (bundle / "docker-compose.deploy.yml").is_file()
    assert (bundle / "bootstrap_server.sh").stat().st_mode & 0o111
    assert (bundle / "install_release.sh").stat().st_mode & 0o111

    deployment_archive = output / f"{bundle_name}.tar.gz"
    with tarfile.open(deployment_archive, "r:gz") as archive:
        names = set(archive.getnames())
    assert f"{bundle_name}/release-source.env" in names
    assert not any(name.endswith(".tar.zst") for name in names)

    checksums = (output / "SHA256SUMS").read_text(encoding="utf-8")
    assert f"{_sha256(backend_archive)}  {backend_archive.name}\n" in checksums
    assert f"{_sha256(admin_archive)}  {admin_archive.name}\n" in checksums
    assert deployment_archive.name in checksums
    assert '"release_tag": "2026.08.14.1"' in capsys.readouterr().out

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        "case \"${1:-}\" in\n"
        "  info) exit 0 ;;\n"
        "  load) cat >/dev/null; exit 0 ;;\n"
        "  image)\n"
        "    image=\"${@: -1}\"\n"
        f"    [[ \"$image\" == wemini-backend:* ]] && printf '%s\\n' '{backend_commit}' && exit 0\n"
        f"    [[ \"$image\" == wemini-admin:* ]] && printf '%s\\n' '{admin_commit}' && exit 0\n"
        "    exit 1 ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    fake_zstd = fake_bin / "zstd"
    fake_zstd.write_text(
        "#!/usr/bin/env bash\nset -Eeuo pipefail\ncat -- \"${@: -1}\"\n",
        encoding="utf-8",
    )
    fake_zstd.chmod(0o755)
    capture = tmp_path / "installer-environment.txt"
    fake_bootstrap = bundle / "bootstrap_server.sh"
    fake_bootstrap.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        "printf '%s|%s|%s|%s\\n' \"$DEPLOY_SOURCE_MODE\" \"$RELEASE_IMAGE_MODE\" "
        "\"$BACKEND_DIR\" \"$DOCKER_USE_SUDO\" > \"$INSTALL_CAPTURE\"\n",
        encoding="utf-8",
    )
    fake_bootstrap.chmod(0o755)
    subprocess.run(
        [str(bundle / "install_release.sh")],
        cwd=bundle,
        env=os.environ
        | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "RELEASE_ASSET_DIR": str(tmp_path),
            "BOOTSTRAP_HOST_DEPLOY_ROOT": str(tmp_path / "deploy"),
            "INSTALL_CAPTURE": str(capture),
        },
        check=True,
        capture_output=True,
        text=True,
    )
    assert capture.read_text(encoding="utf-8") == (
        f"release|preloaded|{bundle}|0\n"
    )


def test_package_rejects_asset_at_github_size_limit(tmp_path):
    archive = tmp_path / "oversized.tar.zst"
    archive.touch()
    with archive.open("r+b") as stream:
        stream.truncate(package_github_release.MAX_RELEASE_ASSET_BYTES)
    with pytest.raises(ValueError, match="2 GiB"):
        package_github_release._archive(str(archive))


def test_bootstrap_release_source_creates_and_enforces_immutable_pins(tmp_path):
    backend_commit = "a" * 40
    admin_commit = "b" * 40
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    source_file = bundle / "release-source.env"
    source_file.write_text(
        "RELEASE_BUNDLE_VERSION=1\n"
        "RELEASE_TAG=2026.08.19.1\n"
        f"TOOLING_COMMIT={'c' * 40}\n"
        f"BACKEND_COMMIT={backend_commit}\n"
        f"ADMIN_COMMIT={admin_commit}\n"
        "BACKEND_REMOTE=https://github.com/BishengTeam/Backend.git\n"
        "ADMIN_REMOTE=https://github.com/BishengTeam/Admin.git\n"
        f"BACKEND_IMAGE=wemini-backend:{backend_commit}\n"
        f"ADMIN_IMAGE=wemini-admin:{admin_commit}\n",
        encoding="utf-8",
    )
    deploy_root = tmp_path / "deploy"
    control = deploy_root / "control"
    control.mkdir(parents=True)
    script = package_github_release.ROOT / "scripts/bootstrap_server.sh"
    command = (
        f"source {script!s}; "
        "load_release_source; pin_sources_once; "
        "printf 'RESULT=%s|%s|%s|%s\\n' "
        '"$BACKEND_COMMIT" "$ADMIN_COMMIT" "$BACKEND_IMAGE" "$ADMIN_IMAGE"'
    )
    environment = os.environ | {
        "BACKEND_DIR": str(bundle),
        "BOOTSTRAP_HOST_DEPLOY_ROOT": str(deploy_root),
        "DEPLOY_SOURCE_MODE": "release",
        "RELEASE_IMAGE_MODE": "preloaded",
        "RELEASE_SOURCE_FILE": str(source_file),
    }
    result = subprocess.run(
        ["bash", "-c", command],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert (
        f"RESULT={backend_commit}|{admin_commit}|wemini-backend:{backend_commit}|"
        f"wemini-admin:{admin_commit}\n"
    ) in result.stdout
    assert (control / "source-pins.env").read_text(encoding="utf-8") == (
        "RELEASE_TAG=2026.08.19.1\n"
        f"BACKEND_COMMIT={backend_commit}\nADMIN_COMMIT={admin_commit}\n"
    )

    source_file.write_text(
        source_file.read_text(encoding="utf-8").replace(
            f"BACKEND_COMMIT={backend_commit}", f"BACKEND_COMMIT={'d' * 40}"
        ).replace(
            f"BACKEND_IMAGE=wemini-backend:{backend_commit}",
            f"BACKEND_IMAGE=wemini-backend:{'d' * 40}",
        ),
        encoding="utf-8",
    )
    rejected = subprocess.run(
        ["bash", "-c", command],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0
    assert "source pin differs" in rejected.stderr
