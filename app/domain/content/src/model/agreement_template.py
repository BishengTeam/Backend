"""P0 e-agreement: versioned templates and immutable user acceptances."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


AGREEMENT_TEMPLATE_TYPES: tuple[str, ...] = (
    "user_terms",
    "privacy",
    "identity_auth",
    "cert_registration",
)


class AgreementTemplate(Base, TimestampMixin):
    """Versioned agreement template.

    Content edits create a NEW row with version+1 and archive the previous
    row, so every version stays addressable for acceptances. Exactly one
    active row per type (partial unique index in migration; service layer
    also enforces it).
    """

    __tablename__ = "agreement_template"
    __table_args__ = (
        CheckConstraint(
            "type IN ('user_terms', 'privacy', 'identity_auth', 'cert_registration')",
            name="ck_agreement_template_type",
        ),
        CheckConstraint("version > 0", name="ck_agreement_template_version"),
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_agreement_template_status",
        ),
        CheckConstraint("length(title) > 0", name="ck_agreement_template_title"),
        CheckConstraint("length(content) > 0", name="ck_agreement_template_content"),
    )

    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )


class AgreementAcceptance(Base):
    """Immutable legal evidence: who accepted which version, with full
    content snapshot at the acceptance moment.

    One row per (user, template version row); re-submitting an already
    accepted version is idempotent.
    """

    __tablename__ = "agreement_acceptance"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "template_id", name="uq_agreement_acceptance_user_template"
        ),
        CheckConstraint("version > 0", name="ck_agreement_acceptance_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=False, index=True
    )
    template_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agreement_template.id"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
