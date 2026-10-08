"""NISP material metadata and full-package exports

Revision ID: nisp004
Revises: doc002
Create Date: 2026-10-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "nisp004"
down_revision: Union[str, Sequence[str], None] = "doc002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "nisp_material_file",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "registration_id",
            sa.BigInteger(),
            sa.ForeignKey("nisp_registration.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("material_type", sa.String(length=32), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=256), nullable=True),
        sa.Column("version_no", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "is_current",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name="uq_nisp_material_file_storage_key"),
        sa.CheckConstraint(
            "material_type IN ('id_card_both_sides', 'portrait_photo', "
            "'xuexin_report', 'application_form')",
            name="ck_nisp_material_file_type",
        ),
        sa.CheckConstraint(
            "length(storage_key) > 0", name="ck_nisp_material_key_nonempty"
        ),
        sa.CheckConstraint(
            "registration_id IS NULL OR is_current = false OR bound_at IS NOT NULL",
            name="ck_nisp_material_bound_current",
        ),
    )
    op.create_index(
        "ix_nisp_material_file_registration_id",
        "nisp_material_file",
        ["registration_id"],
    )
    op.create_index("ix_nisp_material_file_user_id", "nisp_material_file", ["user_id"])
    op.create_index(
        "ix_nisp_material_file_material_type",
        "nisp_material_file",
        ["material_type"],
    )
    op.create_index(
        "uq_nisp_material_current_registration_type",
        "nisp_material_file",
        ["registration_id", "material_type"],
        unique=True,
        postgresql_where=sa.text("is_current AND registration_id IS NOT NULL"),
        sqlite_where=sa.text("is_current AND registration_id IS NOT NULL"),
    )

    op.execute(
        """
        INSERT INTO nisp_material_file (
            registration_id, user_id, material_type, storage_key,
            original_filename, version_no, content_type, size_bytes, sha256,
            is_current, bound_at, created_at, updated_at
        )
        SELECT
            r.id,
            r.user_id,
            entry.key,
            entry.value,
            NULL,
            NULL,
            CASE WHEN entry.key = 'portrait_photo' THEN 'image/jpeg' ELSE 'application/pdf' END,
            NULL,
            NULL,
            true,
            r.created_at,
            now(),
            now()
        FROM nisp_registration AS r
        CROSS JOIN LATERAL jsonb_each_text(COALESCE(r.material_keys, '{}'::jsonb)) AS entry
        WHERE entry.value <> ''
        ON CONFLICT (storage_key) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE nisp_material_file AS material
        SET version_no = numbered.version_no
        FROM (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY registration_id, material_type
                    ORDER BY created_at, id
                )::integer AS version_no
            FROM nisp_material_file
            WHERE registration_id IS NOT NULL
        ) AS numbered
        WHERE material.id = numbered.id
          AND material.version_no IS NULL
        """
    )

    op.add_column(
        "nisp_export_job",
        sa.Column(
            "artifact_type",
            sa.String(length=24),
            server_default="excel",
            nullable=False,
        ),
    )
    op.execute("UPDATE nisp_export_job SET artifact_type = 'excel'")
    op.alter_column(
        "nisp_export_job",
        "artifact_type",
        server_default="full_package",
    )
    op.add_column(
        "nisp_export_job",
        sa.Column("result_summary", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("nisp_export_job", "result_summary")
    op.drop_column("nisp_export_job", "artifact_type")
    op.drop_index(
        "uq_nisp_material_current_registration_type",
        table_name="nisp_material_file",
    )
    op.drop_index("ix_nisp_material_file_material_type", table_name="nisp_material_file")
    op.drop_index("ix_nisp_material_file_user_id", table_name="nisp_material_file")
    op.drop_index(
        "ix_nisp_material_file_registration_id", table_name="nisp_material_file"
    )
    op.drop_table("nisp_material_file")
