"""Safety and manifest tests for the QF-53 PostgreSQL backup utility."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import postgres_backup


def _write_verified_backup(directory: Path, *, age_hours: float = 0) -> tuple[Path, Path]:
    archive = directory / "wemini-postgres-daily-20260812T000000Z-test.dump"
    archive.write_bytes(b"verified-backup")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = archive.with_name(archive.name + postgres_backup.MANIFEST_SUFFIX)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "verified",
                "archive": archive.name,
                "sha256": checksum,
                "alembic_revision": "plan003",
                "database_fingerprint": "1" * 64,
                "created_at": (
                    datetime.now(timezone.utc) - timedelta(hours=age_hours)
                ).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    return archive, manifest


def test_database_target_parses_encoded_credentials_without_rendering_url(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://other:secret@other/db")
    target = postgres_backup.DatabaseTarget.from_url(
        "postgresql+asyncpg://quiz:p%40ss@127.0.0.1:3306/wemini_app_test?sslmode=require"
    )
    assert target.host == "127.0.0.1"
    assert target.port == 3306
    assert target.user == "quiz"
    assert target.password == "p@ss"
    assert target.database == "wemini_app_test"
    environment = target.environment()
    assert environment["PGPASSWORD"] == "p@ss"
    assert "DATABASE_URL" not in environment


def test_safe_directory_rejects_broad_targets(tmp_path) -> None:
    for path in ("/", str(Path.home()), str(postgres_backup.REPO_ROOT)):
        with pytest.raises(postgres_backup.BackupError, match="broad"):
            postgres_backup._safe_directory(path)
    assert postgres_backup._safe_directory(str(tmp_path / "backups")).is_dir()


def test_rpo_check_accepts_fresh_verified_checksum(tmp_path) -> None:
    archive, manifest = _write_verified_backup(tmp_path, age_hours=1)
    result = postgres_backup.check_rpo(tmp_path, max_age_hours=24)
    assert result["status"] == "ok"
    assert result["archive"] == str(archive)
    assert result["manifest"] == str(manifest)
    assert 0 <= result["age_hours"] < 2


def test_rpo_check_rejects_stale_or_tampered_backup(tmp_path) -> None:
    archive, _manifest = _write_verified_backup(tmp_path, age_hours=25)
    with pytest.raises(postgres_backup.BackupError, match="RPO"):
        postgres_backup.check_rpo(tmp_path, max_age_hours=24)

    archive.write_bytes(b"tampered")
    with pytest.raises(postgres_backup.BackupError, match="checksum"):
        postgres_backup.check_rpo(tmp_path, max_age_hours=24)


def test_restore_drill_refuses_non_isolated_or_mismatched_target(tmp_path) -> None:
    archive, _manifest = _write_verified_backup(tmp_path)
    production = postgres_backup.DatabaseTarget(
        host="db",
        port=5432,
        user="app",
        password=None,
        database="wemini_app",
    )
    with pytest.raises(postgres_backup.BackupError, match="_restore_drill"):
        postgres_backup.restore_drill(
            production,
            archive,
            tmp_path / "reports",
            confirmed_database="wemini_app",
        )

    isolated = postgres_backup.DatabaseTarget(
        host="db",
        port=5432,
        user="app",
        password=None,
        database="wemini_app_restore_drill",
    )
    with pytest.raises(postgres_backup.BackupError, match="confirmation"):
        postgres_backup.restore_drill(
            isolated,
            archive,
            tmp_path / "reports",
            confirmed_database="different_restore_drill",
        )


def test_backup_command_uses_custom_format_and_writes_verified_manifest(
    tmp_path,
    monkeypatch,
) -> None:
    commands: list[list[str]] = []

    def fake_scalar(_target, query: str) -> str:
        if "alembic_version" in query:
            return "plan003"
        assert "pg_control_system" in query
        return "wemini_app|test-system-id"

    def fake_run(command, *, target=None, capture_output=False):
        del target, capture_output
        commands.append(command)
        if command[0] == "pg_dump":
            output = Path(command[command.index("--file") + 1])
            output.write_bytes(b"custom-format-backup")
        return object()

    monkeypatch.setattr(postgres_backup, "_psql_scalar", fake_scalar)
    monkeypatch.setattr(postgres_backup, "_run", fake_run)
    target = postgres_backup.DatabaseTarget(
        host="db",
        port=5432,
        user="app",
        password="secret",
        database="wemini_app",
    )

    result = postgres_backup.create_backup(target, tmp_path, kind="pre-migration")
    archive = Path(result["archive"])
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert archive.is_file()
    assert manifest["status"] == "verified"
    assert manifest["kind"] == "pre-migration"
    assert manifest["alembic_revision"] == "plan003"
    assert manifest["database_fingerprint"] == hashlib.sha256(
        b"test-system-id/wemini_app"
    ).hexdigest()
    assert result["backup_reference"].startswith(archive.name + "#sha256=")
    assert commands[0][0] == "pg_dump"
    assert "--format=custom" in commands[0]
    assert commands[1][:2] == ["pg_restore", "--list"]
    assert "secret" not in " ".join(part for command in commands for part in command)
