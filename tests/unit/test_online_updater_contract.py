from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_online_updater_shell_contract():
    script = ROOT / "scripts/upgrade_release.sh"
    source = script.read_text(encoding="utf-8")
    subprocess.run(["bash", "-n", str(script)], check=True)
    assert "--dry-run" in source
    assert "--force" in source
    assert "pg_dump" in source
    assert "pg_restore --list" in source
    assert "upgrade_release.sh.new" in source
    assert 'install -m 0600 /dev/null "$log_file"' in source
    assert 'exec > >(tee -a "$log_file") 2>&1' in source
    assert source.index('install -m 0600 /dev/null "$log_file"') < source.index(
        'exec > >(tee -a "$log_file") 2>&1'
    )
    assert "/var/run/docker.sock" not in source
    assert "eval" not in source


def test_release_bundle_contains_updater_and_maintenance_page():
    packager = (ROOT / "scripts/package_github_release.py").read_text(encoding="utf-8")
    assert 'ROOT / "scripts/upgrade_release.sh"' in packager
    assert 'ROOT / "deploy/nginx/maintenance.html"' in packager
    page = (ROOT / "deploy/nginx/maintenance.html").read_text(encoding="utf-8")
    assert "系统维护中" in page
    assert "503" in page
