"""Add the certification-registration agreement scene

Revision ID: agr003_cert_registration
Revises: quiz013_quiz_purchase
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "agr003_cert_registration"
down_revision: str | Sequence[str] = "quiz013_quiz_purchase"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_agreement_template_type",
        "agreement_template",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agreement_template_type",
        "agreement_template",
        "type IN ('user_terms', 'privacy', 'identity_auth', 'cert_registration')",
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM agreement_acceptance
            WHERE template_id IN (
                SELECT id FROM agreement_template WHERE type = 'cert_registration'
            )
            """
        )
    )
    op.execute(sa.text("DELETE FROM agreement_template WHERE type = 'cert_registration'"))
    op.drop_constraint(
        "ck_agreement_template_type",
        "agreement_template",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agreement_template_type",
        "agreement_template",
        "type IN ('user_terms', 'privacy', 'identity_auth')",
    )
