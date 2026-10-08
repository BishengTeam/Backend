"""Admin-managed operational documents (currently PDF-only)."""

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class DocumentResource(Base, TimestampMixin):
    """A semantic document slot whose current file is served from private OSS.

    ``document_key`` is the stable integration identifier. Replacing a file
    keeps the key unchanged, increments ``version_no``, and immediately makes
    the new object active.
    """

    __tablename__ = "document_resource"
    __table_args__ = (
        CheckConstraint(
            "version_no > 0", name="ck_document_resource_version_positive"
        ),
        CheckConstraint(
            "length(trim(title)) > 0", name="ck_document_resource_title_non_empty"
        ),
    )

    document_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    scene: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    entry_text: Mapped[str | None] = mapped_column(String(64))
    entry_mode: Mapped[str | None] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(String(512))
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(256), nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(64), nullable=False, default="application/pdf"
    )
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    version_no: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("admin_user.id"), nullable=True
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("admin_user.id"), nullable=True
    )
