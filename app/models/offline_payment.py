"""Private payment evidence and append-only financial decisions."""
from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class OfflinePaymentSettings(Base, TimestampMixin):
    __tablename__ = "offline_payment_settings"
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class OfflinePayment(Base, TimestampMixin):
    __tablename__ = "offline_payment"
    order_id: Mapped[int] = mapped_column(ForeignKey("order.id"), unique=True, nullable=False)
    account_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    account_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[int | None] = mapped_column(Integer)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message: Mapped[str | None] = mapped_column(String(1000))


class OfflinePaymentMaterial(Base, TimestampMixin):
    __tablename__ = "offline_payment_material"
    order_id: Mapped[int] = mapped_column(ForeignKey("order.id"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    admin_id: Mapped[int | None] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class OfflinePaymentSubmission(Base, TimestampMixin):
    __tablename__ = "offline_payment_submission"
    __table_args__ = (UniqueConstraint("order_id", "version", name="uq_offline_submission_version"),)
    order_id: Mapped[int] = mapped_column(ForeignKey("order.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1000))


class OfflineBankEntry(Base, TimestampMixin):
    __tablename__ = "offline_bank_entry"
    __table_args__ = (
        UniqueConstraint("account_fingerprint", "bank_reference", "kind", name="uq_offline_bank_entry"),
        UniqueConstraint("order_id", "kind", name="uq_offline_order_entry"),
        CheckConstraint("amount_cents > 0", name="ck_offline_entry_amount"),
    )
    order_id: Mapped[int] = mapped_column(ForeignKey("order.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    account_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    bank_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    bank_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    admin_id: Mapped[int] = mapped_column(Integer, nullable=False)
    submission_version: Mapped[int] = mapped_column(Integer, nullable=False)
    material_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class OfflinePaymentAudit(Base, TimestampMixin):
    __tablename__ = "offline_payment_audit"
    __table_args__ = (UniqueConstraint("order_id", "action", "request_key", name="uq_offline_command"),)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("order.id"), index=True)
    actor_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[dict] = mapped_column(JSON, nullable=False)
