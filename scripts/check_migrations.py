#!/usr/bin/env python3
"""Check the Alembic graph, or run a destructive cycle on a test database.

The default mode is static and never opens a database.  ``--full-cycle`` must
be given an explicit test URL and runs ``upgrade head -> downgrade base ->
upgrade head`` with ``check=True``.  A failed command exits immediately; this
script deliberately has no path that stamps a revision.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[1]


def _redact_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.username is None:
        return url
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    auth = parsed.username
    if parsed.password is not None:
        auth += ":***"
    return urlunsplit((parsed.scheme, f"{auth}@{host}", parsed.path, parsed.query, parsed.fragment))


def static_check() -> dict[str, object]:
    config = Config(str(ROOT / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    heads = scripts.get_heads()
    revisions = {revision.revision for revision in scripts.walk_revisions()}
    operational_files = [
        path
        for path in ROOT.joinpath("scripts").glob("*")
        if path.is_file() and path.name != Path(__file__).name
    ]
    stamp_pattern = re.compile(r"\balembic\s+stamp(?:\s+head)?\b", re.IGNORECASE)
    stamp_files = [
        str(path.relative_to(ROOT))
        for path in operational_files
        if stamp_pattern.search(path.read_text(encoding="utf-8"))
    ]
    if len(heads) != 1:
        raise RuntimeError(f"expected exactly one Alembic head, found: {heads}")
    if stamp_files:
        raise RuntimeError("deployment scripts must not stamp migrations: " + ", ".join(stamp_files))
    return {"heads": heads, "revision_count": len(revisions), "stamp_files": stamp_files}


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+asyncpg://"):
        return sync_url
    if sync_url.startswith("postgresql+psycopg2://"):
        return "postgresql+asyncpg://" + sync_url.removeprefix("postgresql+psycopg2://")
    if sync_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + sync_url.removeprefix("postgresql://")
    raise ValueError("migration URL must use a PostgreSQL scheme")


def run_full_cycle(sync_url: str) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "JWT_SECRET": "test-only-jwt-secret-that-is-at-least-32-characters",
            "TEST_DATABASE_URL_SYNC": sync_url,
            "TEST_DATABASE_URL": _async_url(sync_url),
        }
    )
    commands = (
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        [sys.executable, "-m", "alembic", "upgrade", "head"],
    )
    for command in commands:
        print("$", " ".join(command))
        subprocess.run(command, cwd=ROOT, env=environment, check=True)


def run_offline_sql_check() -> None:
    """Render the whole chain without a live DB and verify the final revision."""

    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "JWT_SECRET": "test-only-jwt-secret-that-is-at-least-32-characters",
            "DATABASE_URL_SYNC": "postgresql://test:test@127.0.0.1:5432/wemini_app_test",
        }
    )
    command = [
        sys.executable,
        "-m",
        "alembic",
        "-x",
        "quiz_backup_ref=offline-static-check",
        "upgrade",
        "base:head",
        "--sql",
    ]
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as sql_file:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            stdout=sql_file,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "Alembic offline SQL generation failed:\n" + completed.stderr
            )
        sql_file.seek(0)
        rendered_sql = sql_file.read()
    if "version_num='quiz002'" not in rendered_sql:
        raise RuntimeError("offline SQL did not reach the current Alembic head quiz002")
    print(f"alembic_offline_sql=ok statements_bytes={len(rendered_sql.encode('utf-8'))}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full-cycle",
        action="store_true",
        help="在明确指定的测试 PostgreSQL 上执行升级、降级、再升级",
    )
    parser.add_argument(
        "--offline-sql",
        action="store_true",
        help="生成 base -> head 的离线 SQL，不连接数据库",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("MIGRATION_CHECK_DATABASE_URL") or os.getenv("TEST_DATABASE_URL_SYNC"),
        help="同步 PostgreSQL URL；仅 --full-cycle 使用",
    )
    args = parser.parse_args()

    report = static_check()
    print(f"alembic_static heads={report['heads']} revisions={report['revision_count']}")
    if not args.full_cycle:
        if args.offline_sql:
            run_offline_sql_check()
        return 0
    if not args.database_url:
        parser.error("--full-cycle requires --database-url or MIGRATION_CHECK_DATABASE_URL")
    print("migration_target=", _redact_url(args.database_url))
    run_full_cycle(args.database_url)
    if args.offline_sql:
        run_offline_sql_check()
    print("alembic_full_cycle=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
