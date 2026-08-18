from __future__ import annotations

import json
import os
import sys

from bootstrap_app import release_cli
from bootstrap_app.state import BootstrapPhase, BootstrapStateStore


def test_release_manifest_is_private_hashed_and_advances_state(tmp_path, monkeypatch):
    os.chmod(tmp_path, 0o700)
    control = tmp_path / "control"
    control.mkdir(mode=0o700)
    token_file = control / "bootstrap_token"
    token_file.write_text("t" * 64, encoding="utf-8")
    token_file.chmod(0o600)
    store = BootstrapStateStore(control, b"t" * 64)
    state = store.initialize()
    for target in (
        BootstrapPhase.CONFIGURED,
        BootstrapPhase.QUALITY_RUNNING,
    ):
        state = store.transition(state.phase, target)

    monkeypatch.setenv("BOOTSTRAP_DEPLOY_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOTSTRAP_HOST_DEPLOY_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOTSTRAP_TOKEN_FILE", str(token_file))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_cli",
            "--release-tag",
            "2026.08.19.1",
            "--backend-commit",
            "a" * 40,
            "--admin-commit",
            "b" * 40,
            "--backend-remote",
            "git@github.com:BishengTeam/Backend.git",
            "--admin-remote",
            "git@github.com:BishengTeam/Admin.git",
            "--backend-image",
            "wemini-backend:aaaaaaaaaaaa",
            "--admin-image",
            "wemini-admin:bbbbbbbbbbbb",
            "--backend-image-id",
            "sha256:" + "c" * 64,
            "--admin-image-id",
            "sha256:" + "d" * 64,
            "--quality-source",
            "ci_prebuilt",
            "--template-dir",
            "/srv/wemini/docs/renshe",
            "--compose-project",
            "wemini-test",
        ],
    )
    release_cli.main()

    manifest_path = control / "release-manifest.json"
    release_env = control / "release.env"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source"]["backend"]["commit"] == "a" * 40
    assert manifest["quality"]["source"] == "ci_prebuilt"
    assert manifest["quality"]["admin_test_build"] == "passed"
    assert (os.stat(manifest_path).st_mode & 0o777) == 0o600
    assert (os.stat(release_env).st_mode & 0o777) == 0o600
    assert "BACKEND_IMAGE=wemini-backend:aaaaaaaaaaaa" in release_env.read_text()
    assert "WEMINI_RELEASE_TAG=2026.08.19.1" in release_env.read_text()
    assert manifest["release"]["tag"] == "2026.08.19.1"

    saved = store.load()
    assert saved.phase == BootstrapPhase.QUALITY_PASSED
    assert saved.backend_commit == "a" * 40
    assert len(saved.release_manifest_sha256 or "") == 64


def test_release_manifest_rejects_remote_with_embedded_credentials():
    try:
        release_cli._safe_remote("https://user:token@example.com/repo.git")
    except ValueError as exc:
        assert "credential" in str(exc)
    else:
        raise AssertionError("credential-bearing remote should be rejected")
