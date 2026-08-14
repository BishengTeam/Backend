"""add immutable deployment acceptance and UAT evidence

Revision ID: deploy001
Revises: quiz007
Create Date: 2026-08-14 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "deploy001"
down_revision: str | Sequence[str] | None = "quiz007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deployment_acceptance",
        sa.Column("installation_id", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="installed_pending_uat",
            nullable=False,
        ),
        sa.Column("backend_commit", sa.String(length=64), nullable=False),
        sa.Column("admin_commit", sa.String(length=64), nullable=False),
        sa.Column("release_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("recovery_object_key", sa.String(length=512), nullable=False),
        sa.Column("recovery_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "database_fingerprint_sha256", sa.String(length=64), nullable=False
        ),
        sa.Column("accepted_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_summary_sha256", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('installed_pending_uat', 'production_accepted')",
            name="ck_deployment_acceptance_status",
        ),
        sa.CheckConstraint(
            "((status = 'installed_pending_uat' "
            "AND accepted_by_admin_id IS NULL AND accepted_at IS NULL "
            "AND evidence_summary_sha256 IS NULL) OR "
            "(status = 'production_accepted' "
            "AND accepted_by_admin_id IS NOT NULL AND accepted_at IS NOT NULL "
            "AND evidence_summary_sha256 IS NOT NULL))",
            name="ck_deployment_acceptance_completion",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_admin_id"], ["admin_user.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_deployment_acceptance_installation_id",
        "deployment_acceptance",
        ["installation_id"],
        unique=True,
    )
    op.create_index(
        "ix_deployment_acceptance_status",
        "deployment_acceptance",
        ["status"],
    )

    op.create_table(
        "deployment_acceptance_event",
        sa.Column("acceptance_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("evidence_type", sa.String(length=32), nullable=True),
        sa.Column("result", sa.String(length=16), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("actor_admin_id", sa.Integer(), nullable=True),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('evidence_recorded', 'acceptance_signed')",
            name="ck_deployment_acceptance_event_type",
        ),
        sa.CheckConstraint(
            "result IS NULL OR result IN ('passed', 'failed')",
            name="ck_deployment_acceptance_event_result",
        ),
        sa.CheckConstraint(
            "evidence_type IS NULL OR evidence_type IN ("
            "'runtime_health', 'runtime_readiness', 'worker_heartbeat', "
            "'wechat_login', 'uat_scope', 'renshe_private_oss', "
            "'wechat_payment', 'wechat_refund', 'recovery_bundle', "
            "'backup_restore_config')",
            name="ck_deployment_acceptance_evidence_type",
        ),
        sa.CheckConstraint(
            "source IN ('bootstrap', 'system', 'uat_reconciler')",
            name="ck_deployment_acceptance_event_source",
        ),
        sa.CheckConstraint(
            "((event_type = 'evidence_recorded' "
            "AND evidence_type IS NOT NULL AND result IS NOT NULL) OR "
            "(event_type = 'acceptance_signed' "
            "AND evidence_type IS NULL AND result IS NULL "
            "AND actor_admin_id IS NOT NULL))",
            name="ck_deployment_acceptance_event_shape",
        ),
        sa.ForeignKeyConstraint(
            ["acceptance_id"], ["deployment_acceptance.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["actor_admin_id"], ["admin_user.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "acceptance_id",
            "event_type",
            "evidence_sha256",
            name="uq_deployment_acceptance_event_digest",
        ),
    )
    op.create_index(
        "ix_deployment_acceptance_event_lookup",
        "deployment_acceptance_event",
        ["acceptance_id", "evidence_type", "id"],
    )

    op.execute(
        """
        CREATE FUNCTION reject_deployment_acceptance_event_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'deployment acceptance events are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_deployment_acceptance_event_append_only
        BEFORE UPDATE OR DELETE ON deployment_acceptance_event
        FOR EACH ROW EXECUTE FUNCTION reject_deployment_acceptance_event_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_deployment_acceptance_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'deployment acceptance records cannot be deleted';
            END IF;
            IF NEW.installation_id IS DISTINCT FROM OLD.installation_id
               OR NEW.backend_commit IS DISTINCT FROM OLD.backend_commit
               OR NEW.admin_commit IS DISTINCT FROM OLD.admin_commit
               OR NEW.release_manifest_sha256 IS DISTINCT FROM OLD.release_manifest_sha256
               OR NEW.recovery_object_key IS DISTINCT FROM OLD.recovery_object_key
               OR NEW.recovery_sha256 IS DISTINCT FROM OLD.recovery_sha256
               OR NEW.database_fingerprint_sha256 IS DISTINCT FROM OLD.database_fingerprint_sha256 THEN
                RAISE EXCEPTION 'deployment identity is immutable';
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status
               AND NOT (
                   OLD.status = 'installed_pending_uat'
                   AND NEW.status = 'production_accepted'
               ) THEN
                RAISE EXCEPTION 'invalid deployment acceptance transition';
            END IF;
            IF OLD.status = 'production_accepted' AND NEW IS DISTINCT FROM OLD THEN
                RAISE EXCEPTION 'production acceptance is terminal';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_deployment_acceptance_guard
        BEFORE UPDATE OR DELETE ON deployment_acceptance
        FOR EACH ROW EXECUTE FUNCTION guard_deployment_acceptance_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_deployment_acceptance_guard "
        "ON deployment_acceptance"
    )
    op.execute("DROP FUNCTION IF EXISTS guard_deployment_acceptance_mutation()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_deployment_acceptance_event_append_only "
        "ON deployment_acceptance_event"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS reject_deployment_acceptance_event_mutation()"
    )
    op.drop_index(
        "ix_deployment_acceptance_event_lookup",
        table_name="deployment_acceptance_event",
    )
    op.drop_table("deployment_acceptance_event")
    op.drop_index(
        "ix_deployment_acceptance_status", table_name="deployment_acceptance"
    )
    op.drop_index(
        "ix_deployment_acceptance_installation_id",
        table_name="deployment_acceptance",
    )
    op.drop_table("deployment_acceptance")
