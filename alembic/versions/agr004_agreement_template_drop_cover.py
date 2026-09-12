"""drop per-version agreement cover URL

The client switched the admin bookshelf cover to a pure CSS title cover
(2026-09-12), so the server-side screenshot pipeline and its column are
removed entirely. Historical JPEG files under the media directory are left
in place as harmless orphans.

Revision ID: agr004
Revises: agr003_cert_registration
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "agr004"
down_revision: str | Sequence[str] | None = "agr003_cert_registration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("agreement_template", "cover_url")


def downgrade() -> None:
    op.add_column(
        "agreement_template",
        sa.Column("cover_url", sa.String(length=512), nullable=True),
    )
