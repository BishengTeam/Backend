#!/usr/bin/env python3
"""Create, verify and restore-drill PostgreSQL backups safely.

Credentials are passed to PostgreSQL tools through ``PG*`` environment
variables, never command arguments or output.  The restore drill intentionally
refuses to create/drop databases: an operator must provision an empty,
isolated database whose name ends in ``_restore_drill`` and confirm that exact
name on the command line.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKUP_PREFIX = "wemini-postgres-"
MANIFEST_SUFFIX = ".manifest.json"
MAX_RPO_HOURS = 24.0
MAX_RTO_SECONDS = 4 * 60 * 60
REQUIRED_QUIZ_TABLES = (
    "quiz_category",
    "quiz_question",
    "quiz_practice_session",
    "quiz_practice_session_question",
    "quiz_practice_attempt",
    "quiz_wrong_item",
    "quiz_collection",
    "quiz_checkin",
    "quiz_exam",
    "quiz_exam_question",
    "quiz_exam_answer",
    "quiz_user_stats",
    "quiz_question_stats",
    "quiz_import_job",
    "quiz_import_error",
    "quiz_admin_audit_log",
)


class BackupError(RuntimeError):
    """Operational failure safe to print without credential material."""


@dataclass(frozen=True, slots=True)
class DatabaseTarget:
    host: str
    port: int
    user: str
    password: str | None
    database: str
    sslmode: str | None = None

    @classmethod
    def from_url(cls, raw_url: str) -> "DatabaseTarget":
        parsed = urlsplit(raw_url.strip())
        scheme = parsed.scheme.lower()
        if scheme not in {
            "postgres",
            "postgresql",
            "postgresql+psycopg2",
            "postgresql+asyncpg",
        }:
            raise BackupError("database URL must use a PostgreSQL scheme")
        database = unquote(parsed.path.lstrip("/"))
        if not parsed.hostname or not parsed.username or not database:
            raise BackupError("database URL must include host, user and database")
        query = parse_qs(parsed.query)
        return cls(
            host=parsed.hostname,
            port=parsed.port or 5432,
            user=unquote(parsed.username),
            password=unquote(parsed.password) if parsed.password is not None else None,
            database=database,
            sslmode=(query.get("sslmode") or [None])[0],
        )

    def environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        # Do not pass application URLs to PostgreSQL subprocesses; they may
        # contain unrelated credentials and the PG* values below are the only
        # connection source these tools need.
        for name in (
            "DATABASE_URL",
            "DATABASE_URL_SYNC",
            "TEST_DATABASE_URL",
            "TEST_DATABASE_URL_SYNC",
            "BACKUP_DATABASE_URL",
            "BACKUP_DATABASE_URL_FILE",
        ):
            environment.pop(name, None)
        environment.update(
            {
                "PGHOST": self.host,
                "PGPORT": str(self.port),
                "PGUSER": self.user,
                "PGDATABASE": self.database,
            }
        )
        if self.password is not None:
            environment["PGPASSWORD"] = self.password
        if self.sslmode:
            environment["PGSSLMODE"] = self.sslmode
        return environment


def _read_database_url(value: str | None, file_path: str | None) -> str:
    if value and file_path:
        raise BackupError("database URL and database URL file are mutually exclusive")
    if file_path:
        path = Path(file_path)
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise BackupError("cannot read database URL file") from exc
    if not value:
        raise BackupError("database URL or database URL file is required")
    return value


def _safe_directory(raw_path: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    forbidden = {Path("/").resolve(), Path.home().resolve(), REPO_ROOT.resolve()}
    if path in forbidden:
        raise BackupError("refusing broad backup/report directory")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir() or path.is_symlink():
        raise BackupError("backup/report directory must be a real directory")
    return path


def _run(
    command: list[str],
    *,
    target: DatabaseTarget | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            env=target.environment() if target else os.environ.copy(),
            text=True,
            capture_output=capture_output,
            check=True,
        )
    except FileNotFoundError as exc:
        raise BackupError(f"required PostgreSQL tool is missing: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        # PostgreSQL stderr can contain host/user/database information.  The
        # operator can inspect protected service logs; CLI output stays safe.
        raise BackupError(f"{command[0]} failed with exit code {exc.returncode}") from exc


def _psql_scalar(target: DatabaseTarget, query: str) -> str:
    completed = _run(
        [
            "psql",
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--set",
            "ON_ERROR_STOP=1",
            "--command",
            query,
        ],
        target=target,
        capture_output=True,
    )
    return completed.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    partial = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
    try:
        partial.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        partial.chmod(0o600)
        partial.replace(path)
    finally:
        if partial.exists():
            partial.unlink()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def create_backup(
    target: DatabaseTarget,
    output_directory: Path,
    *,
    kind: str,
) -> dict[str, Any]:
    if kind not in {"daily", "pre-migration", "manual"}:
        raise BackupError("backup kind must be daily, pre-migration or manual")
    started_at = _utc_now()
    stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    token = uuid.uuid4().hex[:12]
    archive = output_directory / f"{BACKUP_PREFIX}{kind}-{stamp}-{token}.dump"
    partial = output_directory / f".{archive.name}.partial"
    manifest = archive.with_name(archive.name + MANIFEST_SUFFIX)
    try:
        revision = _psql_scalar(
            target,
            "SELECT version_num FROM alembic_version ORDER BY version_num LIMIT 1",
        )
        if not revision:
            raise BackupError("database has no Alembic revision")
        database_identity = _psql_scalar(
            target,
            "SELECT current_database() || '|' || "
            "(SELECT system_identifier::text FROM pg_control_system())",
        )
        identity_parts = database_identity.split("|")
        if len(identity_parts) != 2 or identity_parts[0] != target.database:
            raise BackupError("database returned an invalid backup identity")
        _run(
            [
                "pg_dump",
                "--format=custom",
                "--compress=9",
                "--no-owner",
                "--no-acl",
                "--lock-wait-timeout=60s",
                "--file",
                str(partial),
            ],
            target=target,
        )
        partial.chmod(0o600)
        _run(["pg_restore", "--list", str(partial)], capture_output=True)
        checksum = _sha256(partial)
        partial.replace(archive)
        finished_at = _utc_now()
        payload = {
            "schema_version": 1,
            "status": "verified",
            "kind": kind,
            "archive": archive.name,
            "sha256": checksum,
            "size_bytes": archive.stat().st_size,
            "alembic_revision": revision,
            "created_at": started_at.isoformat(),
            "verified_at": finished_at.isoformat(),
            "database_fingerprint": hashlib.sha256(
                f"{identity_parts[1]}/{identity_parts[0]}".encode("utf-8")
            ).hexdigest(),
        }
        _write_json_atomic(manifest, payload)
        return {
            "archive": str(archive),
            "manifest": str(manifest),
            "backup_reference": f"{archive.name}#sha256={checksum}",
            "alembic_revision": revision,
        }
    finally:
        if partial.exists():
            partial.unlink()


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("cannot read backup manifest") from exc
    if not isinstance(payload, dict) or payload.get("status") != "verified":
        raise BackupError("backup manifest is not verified")
    return payload


def check_rpo(output_directory: Path, *, max_age_hours: float) -> dict[str, Any]:
    if not 0 < max_age_hours <= MAX_RPO_HOURS:
        raise BackupError("max RPO age must be greater than zero and at most 24 hours")
    candidates: list[tuple[datetime, Path, dict[str, Any]]] = []
    for path in output_directory.glob(f"{BACKUP_PREFIX}*{MANIFEST_SUFFIX}"):
        payload = _load_manifest(path)
        try:
            created_at = datetime.fromisoformat(str(payload["created_at"]))
        except (KeyError, ValueError) as exc:
            raise BackupError("backup manifest has an invalid created_at") from exc
        if created_at.tzinfo is None:
            raise BackupError("backup manifest created_at must be timezone-aware")
        candidates.append((created_at.astimezone(timezone.utc), path, payload))
    if not candidates:
        raise BackupError("no verified PostgreSQL backup manifest found")
    created_at, manifest, payload = max(candidates, key=lambda item: item[0])
    archive = output_directory / str(payload.get("archive") or "")
    if archive.parent != output_directory or not archive.is_file():
        raise BackupError("latest backup archive is missing")
    if _sha256(archive) != payload.get("sha256"):
        raise BackupError("latest backup checksum does not match")
    age_hours = max(0.0, (_utc_now() - created_at).total_seconds() / 3600)
    if age_hours > max_age_hours:
        raise BackupError("latest verified backup exceeds the 24-hour RPO")
    return {
        "status": "ok",
        "manifest": str(manifest),
        "archive": str(archive),
        "age_hours": round(age_hours, 3),
        "max_age_hours": max_age_hours,
    }


def restore_drill(
    target: DatabaseTarget,
    archive: Path,
    report_directory: Path,
    *,
    confirmed_database: str,
) -> dict[str, Any]:
    if target.database != confirmed_database:
        raise BackupError("restore target confirmation does not match database name")
    if not target.database.endswith("_restore_drill"):
        raise BackupError("restore drill database must end with _restore_drill")
    if not archive.is_file() or archive.is_symlink():
        raise BackupError("backup archive must be an existing regular file")

    manifest_path = archive.with_name(archive.name + MANIFEST_SUFFIX)
    manifest = _load_manifest(manifest_path)
    if manifest.get("archive") != archive.name:
        raise BackupError("backup manifest archive name does not match")
    checksum = _sha256(archive)
    if checksum != manifest.get("sha256"):
        raise BackupError("backup archive checksum does not match")
    _run(["pg_restore", "--list", str(archive)], capture_output=True)

    actual_database = _psql_scalar(target, "SELECT current_database()")
    if actual_database != target.database:
        raise BackupError("connected restore database does not match requested target")
    existing_tables = _psql_scalar(
        target,
        "SELECT count(*) FROM pg_catalog.pg_tables "
        "WHERE schemaname NOT IN ('pg_catalog', 'information_schema')",
    )
    if existing_tables != "0":
        raise BackupError("restore drill target database must be empty")

    started_at = _utc_now()
    started = time.monotonic()
    _run(
        [
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "--no-acl",
            "--dbname",
            target.database,
            str(archive),
        ],
        target=target,
    )
    revision = _psql_scalar(
        target,
        "SELECT version_num FROM alembic_version ORDER BY version_num LIMIT 1",
    )
    expected_revision = str(manifest.get("alembic_revision") or "")
    if not revision or revision != expected_revision:
        raise BackupError("restored Alembic revision does not match backup manifest")
    names_sql = ",".join(f"'{name}'" for name in REQUIRED_QUIZ_TABLES)
    quiz_table_count = _psql_scalar(
        target,
        "SELECT count(*) FROM information_schema.tables "
        f"WHERE table_schema = 'public' AND table_name IN ({names_sql})",
    )
    if quiz_table_count != str(len(REQUIRED_QUIZ_TABLES)):
        raise BackupError("restored database is missing required quiz tables")
    duration_seconds = time.monotonic() - started
    if duration_seconds > MAX_RTO_SECONDS:
        raise BackupError("restore drill exceeded the four-hour RTO")
    finished_at = _utc_now()
    report = {
        "schema_version": 1,
        "status": "passed",
        "archive": archive.name,
        "sha256": checksum,
        "target_database": target.database,
        "alembic_revision": revision,
        "required_quiz_tables": len(REQUIRED_QUIZ_TABLES),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round(duration_seconds, 3),
        "rto_limit_seconds": MAX_RTO_SECONDS,
    }
    report_path = report_directory / (
        "postgres-restore-drill-" + finished_at.strftime("%Y%m%dT%H%M%SZ") + ".json"
    )
    _write_json_atomic(report_path, report)
    return {**report, "report": str(report_path)}


def _print_result(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def database_arguments(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument(
            "--database-url",
            default=os.getenv("BACKUP_DATABASE_URL"),
        )
        command_parser.add_argument(
            "--database-url-file",
            default=os.getenv("BACKUP_DATABASE_URL_FILE"),
        )

    backup_parser = subparsers.add_parser("backup", help="create and verify a backup")
    database_arguments(backup_parser)
    backup_parser.add_argument("--output-dir", required=True)
    backup_parser.add_argument(
        "--kind", choices=("daily", "pre-migration", "manual"), default="manual"
    )

    check_parser = subparsers.add_parser("check", help="check the frozen 24h RPO")
    check_parser.add_argument("--output-dir", required=True)
    check_parser.add_argument("--max-age-hours", type=float, default=MAX_RPO_HOURS)

    drill_parser = subparsers.add_parser(
        "restore-drill", help="restore into an explicit empty isolated database"
    )
    database_arguments(drill_parser)
    drill_parser.add_argument("--archive", required=True)
    drill_parser.add_argument("--report-dir", required=True)
    drill_parser.add_argument("--confirm-target", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            target = DatabaseTarget.from_url(
                _read_database_url(args.database_url, args.database_url_file)
            )
            result = create_backup(
                target,
                _safe_directory(args.output_dir),
                kind=args.kind,
            )
        elif args.command == "check":
            result = check_rpo(
                _safe_directory(args.output_dir),
                max_age_hours=args.max_age_hours,
            )
        else:
            target = DatabaseTarget.from_url(
                _read_database_url(args.database_url, args.database_url_file)
            )
            result = restore_drill(
                target,
                Path(args.archive).expanduser().resolve(),
                _safe_directory(args.report_dir),
                confirmed_database=args.confirm_target,
            )
        _print_result(result)
        return 0
    except BackupError as exc:
        print(f"postgres backup operation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
