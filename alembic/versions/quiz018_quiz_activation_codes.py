"""quiz library activation codes: new access mode + per-library code batches

Revision ID: quiz018
Revises: quiz017
Create Date: 2026-11-03 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "quiz018"
down_revision: str | Sequence[str] | None = "quiz017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 冻结元数据清单（tests/unit/test_quiz_stage1.py）据此核对 quiz 表集合。
ACTIVATION_TABLES = ("quiz_activation_code",)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
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
    ]


def upgrade() -> None:
    # 激活码模式：库对所有人可见，练习需兑换甲方线下发放的激活码。
    op.create_table(
        "quiz_activation_code",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("library_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="issued", nullable=False),
        sa.Column("note", sa.String(length=512), nullable=True),
        sa.Column("redeemed_by", sa.Integer(), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('issued', 'redeemed', 'revoked')",
            name="ck_quiz_activation_code_status",
        ),
        sa.ForeignKeyConstraint(
            ["library_id"], ["quiz_library.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["redeemed_by"], ["user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["admin_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_quiz_activation_code_code"),
    )
    op.create_index(
        "ix_quiz_activation_code_library",
        "quiz_activation_code",
        ["library_id", "status", "id"],
    )

    # 权益行回链激活码（一码一行权益，partial unique 兜底并发）。
    op.add_column(
        "quiz_library_entitlement",
        sa.Column("activation_code_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_quiz_library_entitlement_activation_code",
        "quiz_library_entitlement",
        "quiz_activation_code",
        ["activation_code_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_quiz_library_entitlement_activation_code",
        "quiz_library_entitlement",
        ["activation_code_id"],
        unique=True,
        postgresql_where=sa.text("activation_code_id IS NOT NULL"),
        sqlite_where=sa.text("activation_code_id IS NOT NULL"),
    )

    op.drop_constraint(
        "ck_quiz_library_entitlement_source_type", "quiz_library_entitlement", type_="check"
    )
    op.create_check_constraint(
        "ck_quiz_library_entitlement_source_type",
        "quiz_library_entitlement",
        "source_type IN ('course_order', 'course_enrollment', 'quiz_order', 'points_order', 'activation_code')",
    )
    op.drop_constraint(
        "ck_quiz_library_entitlement_source_ref", "quiz_library_entitlement", type_="check"
    )
    op.create_check_constraint(
        "ck_quiz_library_entitlement_source_ref",
        "quiz_library_entitlement",
        "((source_type IN ('course_order', 'quiz_order') AND order_id IS NOT NULL) OR "
        "(source_type = 'course_enrollment' AND enrollment_id IS NOT NULL) OR "
        "(source_type = 'points_order' AND points_order_id IS NOT NULL) OR "
        "(source_type = 'activation_code' AND activation_code_id IS NOT NULL))",
    )
    op.drop_constraint("ck_quiz_library_access_mode", "quiz_library", type_="check")
    op.create_check_constraint(
        "ck_quiz_library_access_mode",
        "quiz_library",
        "access_mode IN ('access_mode_pending', 'free', 'course_entitlement', 'paid', 'activation_code')",
    )


def downgrade() -> None:
    # 回滚保护：若已存在 activation_code 模式的库或激活码权益，重建旧 CHECK 会直接失败。
    op.drop_constraint("ck_quiz_library_access_mode", "quiz_library", type_="check")
    op.create_check_constraint(
        "ck_quiz_library_access_mode",
        "quiz_library",
        "access_mode IN ('access_mode_pending', 'free', 'course_entitlement', 'paid')",
    )
    op.drop_constraint(
        "ck_quiz_library_entitlement_source_ref", "quiz_library_entitlement", type_="check"
    )
    op.create_check_constraint(
        "ck_quiz_library_entitlement_source_ref",
        "quiz_library_entitlement",
        "((source_type IN ('course_order', 'quiz_order') AND order_id IS NOT NULL) OR "
        "(source_type = 'course_enrollment' AND enrollment_id IS NOT NULL) OR "
        "(source_type = 'points_order' AND points_order_id IS NOT NULL))",
    )
    op.drop_constraint(
        "ck_quiz_library_entitlement_source_type", "quiz_library_entitlement", type_="check"
    )
    op.create_check_constraint(
        "ck_quiz_library_entitlement_source_type",
        "quiz_library_entitlement",
        "source_type IN ('course_order', 'course_enrollment', 'quiz_order', 'points_order')",
    )
    op.drop_index(
        "uq_quiz_library_entitlement_activation_code", table_name="quiz_library_entitlement"
    )
    op.drop_constraint(
        "fk_quiz_library_entitlement_activation_code",
        "quiz_library_entitlement",
        type_="foreignkey",
    )
    op.drop_column("quiz_library_entitlement", "activation_code_id")
    op.drop_index("ix_quiz_activation_code_library", table_name="quiz_activation_code")
    op.drop_table("quiz_activation_code")
