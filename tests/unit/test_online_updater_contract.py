from __future__ import annotations

import subprocess
import shutil
from pathlib import Path
from types import SimpleNamespace
import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="bash 语法检查需要 bash（部署目标为 Linux）",
)
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
    assert "value = json.loads(sys.argv[1])" in source
    assert '"$new_backend_image" python scripts/oss_backup.py' not in source
    assert source.count('--installation-dir "$DEPLOYMENT_ROOT/installation" \\') == 2
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES" in source
    assert "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES" in source


def test_oss_backup_uploads_an_open_file(tmp_path, monkeypatch):
    import importlib.util
    import sys
    from types import ModuleType

    script = ROOT / "scripts/oss_backup.py"
    oss2 = ModuleType("oss2")
    oss2.Bucket = object
    monkeypatch.setitem(sys.modules, "oss2", oss2)
    spec = importlib.util.spec_from_file_location("oss_backup", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    backup = tmp_path / "backup.tar.gz"
    backup.write_bytes(b"wemini-backup")
    uploaded = {}

    class ACL:
        acl = "private"

    class Metadata:
        headers = {"x-oss-meta-sha256": module._sha256(backup)}

    class Bucket:
        def get_bucket_acl(self):
            return ACL()

        def put_object(self, key, body, headers=None):
            uploaded["key"] = key
            uploaded["body"] = body
            uploaded["contents"] = body.read()
            uploaded["headers"] = headers

        def head_object(self, key):
            return Metadata()

    monkeypatch.setattr(module, "_bucket", lambda installation_dir: Bucket())
    module.upload(
        SimpleNamespace(
            file=str(backup),
            installation_dir=str(tmp_path),
            object_key="wemini-backups/postgresql/installation/test.tar.gz",
            content_type="application/octet-stream",
        )
    )

    assert uploaded["key"] == "wemini-backups/postgresql/installation/test.tar.gz"
    assert hasattr(uploaded["body"], "read")
    assert uploaded["contents"] == b"wemini-backup"
    assert uploaded["headers"]["x-oss-meta-purpose"] == "wemini-postgresql-backup"


def test_release_bundle_contains_updater_and_maintenance_page():
    packager = (ROOT / "scripts/package_github_release.py").read_text(encoding="utf-8")
    assert 'ROOT / "scripts/upgrade_release.sh"' in packager
    assert 'ROOT / "deploy/nginx/maintenance.html"' in packager
    page = (ROOT / "deploy/nginx/maintenance.html").read_text(encoding="utf-8")
    assert "系统维护中" in page
    assert "503" in page
