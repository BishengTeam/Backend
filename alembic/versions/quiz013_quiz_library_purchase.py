"""quiz library standalone purchase

Revision ID: quiz013_quiz_purchase
Revises: agr001
"""
from alembic import op
from collections.abc import Sequence

import sqlalchemy as sa


revision: str = 'quiz013_quiz_purchase'
down_revision: str | Sequence[str] = 'agr002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. quiz_library 加价格列
    op.add_column('quiz_library', sa.Column('price_cents', sa.Integer(), server_default='0', nullable=False))

    # 2. access_mode 加 'paid'
    op.drop_constraint('ck_quiz_library_access_mode', 'quiz_library', type_='check')
    op.create_check_constraint(
        'ck_quiz_library_access_mode', 'quiz_library',
        "access_mode IN ('access_mode_pending', 'free', 'course_entitlement', 'paid')"
    )

    # 3. entitlement source_type 加 'quiz_order'
    op.drop_constraint('ck_quiz_library_entitlement_source_type', 'quiz_library_entitlement', type_='check')
    op.create_check_constraint(
        'ck_quiz_library_entitlement_source_type', 'quiz_library_entitlement',
        "source_type IN ('course_order', 'course_enrollment', 'quiz_order')"
    )

    # 4. source_ref 约束：quiz_order 也需要 order_id
    op.drop_constraint('ck_quiz_library_entitlement_source_ref', 'quiz_library_entitlement', type_='check')
    op.create_check_constraint(
        'ck_quiz_library_entitlement_source_ref', 'quiz_library_entitlement',
        "((source_type IN ('course_order', 'quiz_order') AND order_id IS NOT NULL) OR "
        "(source_type = 'course_enrollment' AND enrollment_id IS NOT NULL))"
    )


def downgrade() -> None:
    op.drop_constraint('ck_quiz_library_entitlement_source_ref', 'quiz_library_entitlement', type_='check')
    op.create_check_constraint(
        'ck_quiz_library_entitlement_source_ref', 'quiz_library_entitlement',
        "((source_type = 'course_order' AND order_id IS NOT NULL) OR "
        "(source_type = 'course_enrollment' AND enrollment_id IS NOT NULL))"
    )
    op.drop_constraint('ck_quiz_library_entitlement_source_type', 'quiz_library_entitlement', type_='check')
    op.create_check_constraint(
        'ck_quiz_library_entitlement_source_type', 'quiz_library_entitlement',
        "source_type IN ('course_order', 'course_enrollment')"
    )
    op.drop_constraint('ck_quiz_library_access_mode', 'quiz_library', type_='check')
    op.create_check_constraint(
        'ck_quiz_library_access_mode', 'quiz_library',
        "access_mode IN ('access_mode_pending', 'free', 'course_entitlement')"
    )
    op.drop_column('quiz_library', 'price_cents')
