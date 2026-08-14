"""Isolated PostgreSQL evidence for initial admin and production seed."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError

from bootstrap_app.models import BootstrapAdminRequest
from bootstrap_app.runtime import create_initial_super_admin


pytestmark = pytest.mark.integration_db
REPO_ROOT = Path(__file__).resolve().parents[3]


def _maintenance_url(configured_url: str) -> URL:
    url = make_url(configured_url)
    if not url.drivername.startswith("postgresql"):
        pytest.skip("bootstrap runtime test requires PostgreSQL")
    return url.set(drivername="postgresql+psycopg2", database="postgres")


@pytest.fixture
def bootstrap_runtime_database(tmp_path, monkeypatch):
    configured_url = os.getenv("TEST_DATABASE_URL_SYNC")
    if not configured_url:
        pytest.skip("TEST_DATABASE_URL_SYNC is required for real runtime tests")
    maintenance_url = _maintenance_url(configured_url)
    suffix = uuid.uuid4().hex[:12]
    database_name = f"bootstrap_runtime_test_{suffix}"
    maintenance_engine = create_engine(
        maintenance_url,
        isolation_level="AUTOCOMMIT",
    )
    database_created = False
    try:
        with maintenance_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
            database_created = True
    except DBAPIError as exc:
        if database_created:
            with maintenance_engine.connect() as connection:
                connection.exec_driver_sql(
                    f'DROP DATABASE IF EXISTS "{database_name}"'
                )
        maintenance_engine.dispose()
        pytest.skip(f"PostgreSQL role cannot provision an isolated database: {exc}")

    test_base = maintenance_url.set(database=database_name)
    sync_url = test_base.set(drivername="postgresql")
    async_url = test_base.set(drivername="postgresql+asyncpg")
    monkeypatch.setenv(
        "TEST_DATABASE_URL_SYNC", sync_url.render_as_string(hide_password=False)
    )
    monkeypatch.setenv(
        "TEST_DATABASE_URL", async_url.render_as_string(hide_password=False)
    )
    monkeypatch.setenv(
        "QUIZ_DESTRUCTIVE_MIGRATION_BACKUP_REF",
        "isolated-bootstrap-runtime-test",
    )

    installation = tmp_path / "installation"
    installation.mkdir(mode=0o700)
    runtime = installation / "runtime.env"
    runtime.write_text(
        "\n".join(
            (
                f"DB_HOST={test_base.host or '/var/run/postgresql'}",
                f"DB_PORT={test_base.port or 5432}",
                f"DB_USER={test_base.username}",
                f"DB_NAME={database_name}",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    runtime.chmod(0o600)
    secret_dir = installation / "secrets"
    secret_dir.mkdir(mode=0o700)
    password_file = secret_dir / "postgres_password"
    password_file.write_text(
        test_base.password or "peer-auth-placeholder",
        encoding="utf-8",
    )
    password_file.chmod(0o600)

    try:
        yield installation, sync_url, async_url
    finally:
        with maintenance_engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.exec_driver_sql(
                f'DROP DATABASE IF EXISTS "{database_name}"'
            )
        maintenance_engine.dispose()


def _alembic_config() -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return config


def test_initial_admin_is_concurrent_idempotent_and_seed_repeats_cleanly(
    bootstrap_runtime_database,
) -> None:
    installation, sync_url, async_url = bootstrap_runtime_database
    command.upgrade(_alembic_config(), "head")
    request = BootstrapAdminRequest(
        username="initial-super-admin",
        password="bootstrap-test-password-123",
    )

    async def create_twice() -> tuple[int, int]:
        first, second = await asyncio.gather(
            create_initial_super_admin(installation, request),
            create_initial_super_admin(installation, request),
        )
        return first, second

    first_id, second_id = asyncio.run(create_twice())
    assert first_id == second_id

    env = os.environ | {
        "APP_ENV": "test",
        "APP_DEBUG": "false",
        "DATABASE_URL": async_url.render_as_string(hide_password=False),
        "DATABASE_URL_SYNC": sync_url.render_as_string(hide_password=False),
        "JWT_SECRET": "test-only-jwt-secret-that-is-at-least-32-characters",
    }
    outputs = []
    for _ in range(2):
        result = subprocess.run(
            [str(REPO_ROOT / ".venv/bin/python"), "scripts/seed_production.py"],
            cwd=REPO_ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        outputs.append(json.loads(result.stdout))

    assert outputs[0]["created_certifications"] == 4
    assert outputs[0]["created_prices"] == 8
    assert outputs[1]["created_certifications"] == 0
    assert outputs[1]["created_prices"] == 0

    engine = create_engine(sync_url)
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT count(*) FROM admin_user WHERE role = 'super_admin'")
        ) == 1
        assert connection.scalar(text("SELECT count(*) FROM certification")) == 4
        assert connection.scalar(text("SELECT count(*) FROM price_config")) == 8
    engine.dispose()
