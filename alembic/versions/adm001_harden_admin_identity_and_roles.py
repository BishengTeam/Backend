"""harden administrator identity, fixed roles, and security persistence

Revision ID: adm001
Revises: deploy001
Create Date: 2026-08-15 10:00:00.000000

An empty ``admin_user`` table is the sole clean-install exception to the
existing-install preflight.  The bootstrap workflow creates the first unique
active super administrator after schema migration; runtime readiness must not
be considered complete until that step succeeds.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "adm001"
down_revision: str | Sequence[str] | None = "deploy001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADMIN_IDEMPOTENCY_DIGEST_LENGTH = 64
ADMIN_CREDENTIAL_IDEMPOTENCY_ACTIONS = (
    "admin_account.create",
    "admin_account.enable",
    "admin_account.password_reset",
)
ADMIN_CREDENTIAL_IDEMPOTENCY_PREDICATE = (
    "result = 'succeeded' AND actor_admin_id IS NOT NULL "
    "AND idempotency_key_hash IS NOT NULL AND action IN "
    f"({', '.join(repr(action) for action in ADMIN_CREDENTIAL_IDEMPOTENCY_ACTIONS)})"
)


def _migration_backup_reference() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    return (
        x_args.get("admin_security_backup_ref")
        or os.getenv("ADMIN_SECURITY_MIGRATION_BACKUP_REF", "")
    ).strip()


def _downgrade_backup_reference() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    return (
        x_args.get("admin_security_downgrade_backup_ref")
        or os.getenv("ADMIN_SECURITY_DOWNGRADE_BACKUP_REF", "")
    ).strip()


def _preflight_existing_administrators() -> None:
    """Reject unsafe legacy state before changing any account row.

    Existing installations must provide a verified backup reference and have
    exactly one active super administrator.  An entirely empty table is
    allowed for the fresh-install bootstrap sequence described above.
    """

    if context.is_offline_mode():
        if not _migration_backup_reference():
            raise RuntimeError(
                "offline administrator migration requires "
                "-x admin_security_backup_ref=<verified-backup-reference>"
            )
        return

    bind = op.get_bind()
    rows = list(
        bind.execute(
            sa.text(
                "SELECT id, username, role, is_active "
                "FROM admin_user ORDER BY id"
            )
        ).mappings()
    )
    if not rows:
        return

    super_admins = [row for row in rows if row["role"] == "super_admin"]
    if len(super_admins) != 1:
        raise RuntimeError(
            "administrator migration preflight requires exactly one "
            f"super_admin when admin_user is non-empty; found {len(super_admins)}"
        )
    if not super_admins[0]["is_active"]:
        raise RuntimeError(
            "administrator migration preflight requires the sole "
            "super_admin to be active"
        )

    normalized: dict[str, list[int]] = {}
    for row in rows:
        value = str(row["username"]).strip().lower()
        normalized.setdefault(value, []).append(int(row["id"]))
    conflicts = {
        username: ids for username, ids in normalized.items() if len(ids) > 1
    }
    if conflicts:
        conflict_list = ", ".join(
            f"{username}={ids}" for username, ids in sorted(conflicts.items())
        )
        raise RuntimeError(
            "administrator migration preflight found normalized username "
            f"conflicts: {conflict_list}"
        )

    if not _migration_backup_reference():
        raise RuntimeError(
            "administrator accounts exist; verify a database backup and rerun "
            "with -x admin_security_backup_ref=<verified-backup-reference> "
            "or ADMIN_SECURITY_MIGRATION_BACKUP_REF"
        )


def _require_downgrade_backup() -> None:
    """Protect password history and permanent security audit during rollback."""

    if context.is_offline_mode():
        if not _downgrade_backup_reference():
            raise RuntimeError(
                "offline administrator downgrade requires "
                "-x admin_security_downgrade_backup_ref=<verified-backup-reference>"
            )
        return

    bind = op.get_bind()
    has_security_rows = bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM admin_password_history LIMIT 1) "
            "OR EXISTS (SELECT 1 FROM admin_security_audit LIMIT 1)"
        )
    ).scalar_one()
    if has_security_rows and not _downgrade_backup_reference():
        raise RuntimeError(
            "administrator password history or permanent security audit exists; "
            "verify a restorable backup and rerun with "
            "-x admin_security_downgrade_backup_ref=<verified-backup-reference> "
            "or ADMIN_SECURITY_DOWNGRADE_BACKUP_REF"
        )


def upgrade() -> None:
    _preflight_existing_administrators()

    # Expand first, backfill deterministically, then enforce the target shape.
    op.add_column(
        "admin_user", sa.Column("display_name", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "admin_user",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=True,
        ),
    )
    op.add_column(
        "admin_user",
        sa.Column("auth_version", sa.Integer(), server_default="1", nullable=True),
    )
    op.add_column(
        "admin_user",
        sa.Column(
            "failed_login_attempts",
            sa.Integer(),
            server_default="0",
            nullable=True,
        ),
    )
    op.add_column(
        "admin_user",
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "admin_user",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "admin_user", sa.Column("last_login_ip", sa.String(length=45), nullable=True)
    )
    op.add_column(
        "admin_user",
        sa.Column(
            "password_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )

    op.execute("UPDATE admin_user SET username = lower(btrim(username))")
    op.execute(
        "UPDATE admin_user SET display_name = username "
        "WHERE display_name IS NULL OR btrim(display_name) = ''"
    )
    op.drop_constraint("ck_admin_user_role", "admin_user", type_="check")
    op.execute(
        "UPDATE admin_user SET role = 'quiz_admin', is_active = false, "
        "must_change_password = true, auth_version = 2, "
        "failed_login_attempts = 0, locked_until = NULL "
        "WHERE role = 'admin'"
    )
    op.execute(
        "UPDATE admin_user SET must_change_password = false, auth_version = 1, "
        "failed_login_attempts = 0, locked_until = NULL "
        "WHERE role = 'super_admin'"
    )

    op.alter_column("admin_user", "display_name", nullable=False)
    op.alter_column("admin_user", "must_change_password", nullable=False)
    op.alter_column("admin_user", "auth_version", nullable=False)
    op.alter_column("admin_user", "failed_login_attempts", nullable=False)
    op.alter_column("admin_user", "password_changed_at", nullable=False)

    op.alter_column(
        "admin_user",
        "role",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default="quiz_admin",
    )
    op.create_check_constraint(
        "ck_admin_user_role",
        "admin_user",
        "role IN ('super_admin', 'quiz_admin')",
    )
    op.create_check_constraint(
        "ck_admin_user_username_normalized",
        "admin_user",
        "username = lower(btrim(username))",
    )
    op.create_check_constraint(
        "ck_admin_user_display_name_non_empty",
        "admin_user",
        "length(btrim(display_name)) > 0",
    )
    op.create_check_constraint(
        "ck_admin_user_auth_version_positive",
        "admin_user",
        "auth_version >= 1",
    )
    op.create_check_constraint(
        "ck_admin_user_failed_login_attempts_non_negative",
        "admin_user",
        "failed_login_attempts >= 0",
    )

    op.drop_index("ix_admin_user_username", table_name="admin_user")
    op.create_index(
        "uq_admin_user_username_normalized",
        "admin_user",
        [sa.text("lower(username)")],
        unique=True,
    )
    op.create_index(
        "uq_admin_user_single_super_admin",
        "admin_user",
        ["role"],
        unique=True,
        postgresql_where=sa.text("role = 'super_admin'"),
    )

    op.create_table(
        "admin_password_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("admin_id", sa.Integer(), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["admin_id"], ["admin_user.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_admin_password_history_recent",
        "admin_password_history",
        ["admin_id", "created_at", "id"],
    )
    op.execute(
        "INSERT INTO admin_password_history (admin_id, password_hash, created_at) "
        "SELECT id, password_hash, password_changed_at FROM admin_user"
    )

    op.create_table(
        "admin_security_audit",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("actor_admin_id", sa.Integer(), nullable=True),
        sa.Column("target_admin_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column(
            "idempotency_key_hash",
            sa.String(length=ADMIN_IDEMPOTENCY_DIGEST_LENGTH),
            nullable=True,
        ),
        sa.Column("source_ip", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "result IN ('succeeded', 'failed')",
            name="ck_admin_security_audit_result",
        ),
        sa.CheckConstraint(
            "idempotency_key_hash IS NULL "
            f"OR length(idempotency_key_hash) = {ADMIN_IDEMPOTENCY_DIGEST_LENGTH}",
            name="ck_admin_security_audit_idempotency_hash_length",
        ),
        sa.ForeignKeyConstraint(
            ["actor_admin_id"], ["admin_user.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_admin_id"], ["admin_user.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_admin_security_audit_actor",
        "admin_security_audit",
        ["actor_admin_id", "created_at", "id"],
    )
    op.create_index(
        "ix_admin_security_audit_target",
        "admin_security_audit",
        ["target_admin_id", "created_at", "id"],
    )
    op.create_index(
        "ix_admin_security_audit_action_result",
        "admin_security_audit",
        ["action", "result", "created_at", "id"],
    )
    op.create_index(
        "ix_admin_security_audit_username",
        "admin_security_audit",
        ["username", "created_at", "id"],
    )
    op.create_index(
        "ix_admin_security_audit_request_id",
        "admin_security_audit",
        ["request_id", "id"],
    )
    op.create_index(
        "uq_admin_security_audit_credential_idempotency",
        "admin_security_audit",
        ["actor_admin_id", "action", "idempotency_key_hash"],
        unique=True,
        postgresql_where=sa.text(ADMIN_CREDENTIAL_IDEMPOTENCY_PREDICATE),
    )
    op.create_index(
        "ix_admin_security_audit_created",
        "admin_security_audit",
        ["created_at", "id"],
    )

    # Compatibility for direct SQL/bootstrap callers and immutable usernames.
    op.execute(
        """
        CREATE FUNCTION enforce_admin_user_identity()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                NEW.username := lower(btrim(NEW.username));
                IF NEW.display_name IS NULL THEN
                    NEW.display_name := NEW.username;
                END IF;
            ELSIF NEW.username IS DISTINCT FROM OLD.username THEN
                RAISE EXCEPTION 'administrator username is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_admin_user_identity
        BEFORE INSERT OR UPDATE OF username ON admin_user
        FOR EACH ROW EXECUTE FUNCTION enforce_admin_user_identity()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_admin_password_history_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'administrator password history is immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_admin_password_history_immutable
        BEFORE UPDATE ON admin_password_history
        FOR EACH ROW EXECUTE FUNCTION reject_admin_password_history_update()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_admin_security_audit_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'administrator security audit is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_admin_security_audit_append_only
        BEFORE UPDATE OR DELETE ON admin_security_audit
        FOR EACH ROW EXECUTE FUNCTION reject_admin_security_audit_mutation()
        """
    )


def downgrade() -> None:
    _require_downgrade_backup()

    op.execute(
        "DROP TRIGGER IF EXISTS trg_admin_security_audit_append_only "
        "ON admin_security_audit"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_admin_security_audit_mutation()")
    op.drop_table("admin_security_audit")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_admin_password_history_immutable "
        "ON admin_password_history"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_admin_password_history_update()")
    op.drop_table("admin_password_history")

    op.execute("DROP TRIGGER IF EXISTS trg_admin_user_identity ON admin_user")
    op.execute("DROP FUNCTION IF EXISTS enforce_admin_user_identity()")

    op.drop_index("uq_admin_user_single_super_admin", table_name="admin_user")
    op.drop_index("uq_admin_user_username_normalized", table_name="admin_user")
    op.drop_constraint(
        "ck_admin_user_failed_login_attempts_non_negative",
        "admin_user",
        type_="check",
    )
    op.drop_constraint(
        "ck_admin_user_auth_version_positive", "admin_user", type_="check"
    )
    op.drop_constraint(
        "ck_admin_user_display_name_non_empty", "admin_user", type_="check"
    )
    op.drop_constraint(
        "ck_admin_user_username_normalized", "admin_user", type_="check"
    )
    op.drop_constraint("ck_admin_user_role", "admin_user", type_="check")
    op.execute("UPDATE admin_user SET role = 'admin' WHERE role = 'quiz_admin'")
    op.alter_column(
        "admin_user",
        "role",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default="admin",
    )
    op.create_check_constraint(
        "ck_admin_user_role",
        "admin_user",
        "role IN ('super_admin', 'admin')",
    )
    op.create_index(
        "ix_admin_user_username", "admin_user", ["username"], unique=True
    )

    op.drop_column("admin_user", "password_changed_at")
    op.drop_column("admin_user", "last_login_ip")
    op.drop_column("admin_user", "last_login_at")
    op.drop_column("admin_user", "locked_until")
    op.drop_column("admin_user", "failed_login_attempts")
    op.drop_column("admin_user", "auth_version")
    op.drop_column("admin_user", "must_change_password")
    op.drop_column("admin_user", "display_name")
