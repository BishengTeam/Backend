"""Isolated PostgreSQL evidence for deploy001 upgrade/downgrade/immutability."""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.deployment_acceptance import (
    DEPLOYMENT_EVIDENCE_TYPES,
)
from app.port.exceptions import ConflictException
from app.schemas.deployment_acceptance import DeploymentAcceptanceSignRequest
from app.services.deployment_acceptance import DeploymentAcceptanceService
from app.domain.user.src.index import AdminUser
from bootstrap_app.acceptance import (
    RuntimeAcceptanceEvidence,
    register_installed_acceptance,
)
from bootstrap_app.state import BootstrapPhase, BootstrapState


pytestmark = pytest.mark.integration_db
REPO_ROOT = Path(__file__).resolve().parents[3]


def _maintenance_url(configured_url: str) -> URL:
    url = make_url(configured_url)
    if not url.drivername.startswith("postgresql"):
        pytest.skip("deployment migration test requires PostgreSQL")
    return url.set(drivername="postgresql+psycopg2", database="postgres")


@pytest.fixture
def deployment_migration_database(monkeypatch):
    configured_url = os.getenv("TEST_DATABASE_URL_SYNC")
    if not configured_url:
        pytest.skip("TEST_DATABASE_URL_SYNC is required for real migration tests")

    maintenance_url = _maintenance_url(configured_url)
    database_name = f"deployment_acceptance_test_{uuid.uuid4().hex[:12]}"
    maintenance_engine = create_engine(
        maintenance_url,
        isolation_level="AUTOCOMMIT",
    )
    try:
        with maintenance_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    except DBAPIError as exc:
        maintenance_engine.dispose()
        pytest.skip(f"PostgreSQL role cannot create an isolated database: {exc}")

    test_url = maintenance_url.set(database=database_name)
    monkeypatch.setenv(
        "TEST_DATABASE_URL_SYNC",
        test_url.render_as_string(hide_password=False),
    )
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        test_url.set(drivername="postgresql+asyncpg").render_as_string(
            hide_password=False
        ),
    )
    monkeypatch.setenv(
        "QUIZ_DESTRUCTIVE_MIGRATION_BACKUP_REF",
        "isolated-deployment-migration-test",
    )
    monkeypatch.setenv(
        "ADMIN_SECURITY_MIGRATION_BACKUP_REF",
        "isolated-deployment-migration-test",
    )
    try:
        yield test_url
    finally:
        with maintenance_engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        maintenance_engine.dispose()


def _alembic_config() -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return config


def test_deploy001_upgrade_downgrade_upgrade_and_append_only_guards(
    deployment_migration_database,
) -> None:
    config = _alembic_config()
    command.upgrade(config, "head")
    engine = create_engine(deployment_migration_database)
    inspector = inspect(engine)
    assert {
        "deployment_acceptance",
        "deployment_acceptance_event",
    } <= set(inspector.get_table_names())
    assert {
        "ck_deployment_acceptance_status",
        "ck_deployment_acceptance_completion",
    } <= {
        item["name"]
        for item in inspector.get_check_constraints("deployment_acceptance")
    }

    with engine.begin() as connection:
        admin_id = connection.execute(
            text(
                "INSERT INTO admin_user "
                "(username, password_hash, role, is_active) "
                "VALUES ('deployment-migration-admin', 'test-only', "
                "'super_admin', true) RETURNING id"
            )
        ).scalar_one()
        acceptance_id = connection.execute(
            text(
                "INSERT INTO deployment_acceptance ("
                "installation_id, status, backend_commit, admin_commit, "
                "release_manifest_sha256, recovery_object_key, recovery_sha256, "
                "database_fingerprint_sha256) VALUES ("
                ":installation_id, 'installed_pending_uat', :backend_commit, "
                ":admin_commit, :manifest, :object_key, :recovery, :database) "
                "RETURNING id"
            ),
            {
                "installation_id": "1" * 32,
                "backend_commit": "a" * 40,
                "admin_commit": "b" * 40,
                "manifest": "c" * 64,
                "object_key": "recovery/test-object",
                "recovery": "d" * 64,
                "database": "e" * 64,
            },
        ).scalar_one()
        event_id = connection.execute(
            text(
                "INSERT INTO deployment_acceptance_event ("
                "acceptance_id, event_type, evidence_type, result, source, "
                "actor_admin_id, evidence_sha256, summary) VALUES ("
                ":acceptance_id, 'evidence_recorded', 'runtime_health', "
                "'passed', 'system', NULL, :digest, CAST(:summary AS jsonb)) "
                "RETURNING id"
            ),
            {
                "acceptance_id": acceptance_id,
                "digest": "f" * 64,
                "summary": '{"status":"ok"}',
            },
        ).scalar_one()
        assert admin_id > 0

    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE deployment_acceptance_event "
                    "SET result = 'failed' WHERE id = :event_id"
                ),
                {"event_id": event_id},
            )

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE deployment_acceptance SET "
                "status = 'production_accepted', accepted_by_admin_id = :admin_id, "
                "accepted_at = now(), evidence_summary_sha256 = :summary "
                "WHERE id = :acceptance_id"
            ),
            {
                "admin_id": admin_id,
                "summary": "9" * 64,
                "acceptance_id": acceptance_id,
            },
        )

    with pytest.raises(DBAPIError, match="invalid deployment acceptance transition"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE deployment_acceptance "
                    "SET status = 'installed_pending_uat', "
                    "accepted_by_admin_id = NULL, accepted_at = NULL, "
                    "evidence_summary_sha256 = NULL WHERE id = :acceptance_id"
                ),
                {"acceptance_id": acceptance_id},
            )

    command.downgrade(config, "quiz007")
    assert "deployment_acceptance" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    assert "deployment_acceptance" in inspect(engine).get_table_names()
    engine.dispose()


@pytest.mark.asyncio
async def test_acceptance_service_cannot_sign_missing_evidence_and_is_terminal(
    deployment_migration_database,
    tmp_path,
) -> None:
    command.upgrade(_alembic_config(), "head")
    async_url = deployment_migration_database.set(
        drivername="postgresql+asyncpg"
    ).render_as_string(hide_password=False)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def db_context():
        async with factory() as session:
            yield session

    async with factory() as db:
        admin = AdminUser(
            username="deployment-service-admin",
            password_hash="test-only",
            role="super_admin",
            is_active=True,
        )
        db.add(admin)
        await db.commit()
        admin_id = admin.id

    installation = tmp_path / "installation"
    installation.mkdir(mode=0o700)
    runtime_path = installation / "runtime.env"
    runtime_path.write_text(
        "\n".join(
            (
                f"DB_HOST={deployment_migration_database.host or '/var/run/postgresql'}",
                f"DB_PORT={deployment_migration_database.port or 5432}",
                f"DB_USER={deployment_migration_database.username}",
                f"DB_NAME={deployment_migration_database.database}",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    runtime_path.chmod(0o600)
    secret_dir = installation / "secrets"
    secret_dir.mkdir(mode=0o700)
    password_path = secret_dir / "postgres_password"
    password_path.write_text(
        deployment_migration_database.password or "peer-auth-placeholder",
        encoding="utf-8",
    )
    password_path.chmod(0o600)
    control = tmp_path / "control"
    control.mkdir(mode=0o700)
    manifest = {
        "installation_id": "2" * 32,
        "source": {
            "backend": {"commit": "a" * 40},
            "admin": {"commit": "b" * 40},
        },
    }
    manifest_bytes = (
        json.dumps(manifest, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()
    manifest_path = control / "release-manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    manifest_path.chmod(0o600)
    state = BootstrapState(
        version=1,
        installation_id="2" * 32,
        phase=BootstrapPhase.RECOVERY_VERIFIED,
        created_at="2026-08-14T00:00:00Z",
        updated_at="2026-08-14T00:00:00Z",
        backend_commit="a" * 40,
        admin_commit="b" * 40,
        release_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        recovery_object_key="recovery/service-test",
        recovery_sha256="d" * 64,
    )
    initial_evidence = RuntimeAcceptanceEvidence(
        database_fingerprint_sha256="e" * 64,
        summaries={
            "runtime_health": {"status": "passed"},
            "runtime_readiness": {"status": "passed"},
            "worker_heartbeat": {"status": "passed"},
        },
    )
    first_registration = await register_installed_acceptance(
        installation_dir=installation,
        control_dir=control,
        state=state,
        runtime_evidence=initial_evidence,
    )
    repeated_registration = await register_installed_acceptance(
        installation_dir=installation,
        control_dir=control,
        state=state,
        runtime_evidence=initial_evidence,
    )
    assert first_registration == repeated_registration

    service = DeploymentAcceptanceService(db_context)
    request = DeploymentAcceptanceSignRequest(
        confirmation="PRODUCTION_ACCEPTED",
        release_manifest_sha256=state.release_manifest_sha256,
    )
    pending = await service.get_status()
    bootstrap_evidence = {
        "runtime_health",
        "runtime_readiness",
        "worker_heartbeat",
        "recovery_bundle",
    }
    assert set(pending.missing_evidence) == (
        set(DEPLOYMENT_EVIDENCE_TYPES) - bootstrap_evidence
    )
    assert pending.can_accept is False
    with pytest.raises(ConflictException, match="验收项未通过"):
        await service.accept(admin_id=admin_id, request=request)

    for evidence_type in pending.missing_evidence:
        await service.record_evidence(
            installation_id="2" * 32,
            evidence_type=evidence_type,
            result="passed",
            source="uat_reconciler",
            summary={"test": evidence_type, "status": "passed"},
        )

    ready = await service.get_status()
    assert ready.missing_evidence == []
    assert ready.can_accept is True
    accepted = await service.accept(admin_id=admin_id, request=request)
    assert accepted.status == "production_accepted"
    assert accepted.can_accept is False
    assert accepted.accepted_by_admin_id == admin_id
    assert len(accepted.evidence_summary_sha256 or "") == 64

    repeated = await service.accept(admin_id=admin_id, request=request)
    assert repeated.evidence_summary_sha256 == accepted.evidence_summary_sha256
    with pytest.raises(ConflictException, match="不能追加证据"):
        await service.record_evidence(
            installation_id="2" * 32,
            evidence_type="runtime_health",
            result="passed",
            source="system",
            summary={"status": "passed"},
        )

    async with factory() as db:
        evidence_count = await db.scalar(
            text(
                "SELECT count(*) FROM deployment_acceptance_event "
                "WHERE event_type = 'evidence_recorded'"
            )
        )
        signature_count = await db.scalar(
            text(
                "SELECT count(*) FROM deployment_acceptance_event "
                "WHERE event_type = 'acceptance_signed'"
            )
        )
        assert evidence_count == len(DEPLOYMENT_EVIDENCE_TYPES)
        assert signature_count == 1
        signature = await db.scalar(
            text(
                "SELECT evidence_sha256 FROM deployment_acceptance_event "
                "WHERE event_type = 'acceptance_signed'"
            )
        )
        assert signature == accepted.evidence_summary_sha256

    await engine.dispose()
