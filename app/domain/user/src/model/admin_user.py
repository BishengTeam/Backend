from sqlalchemy import Boolean, CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

ADMIN_ROLES = ("super_admin", "content_editor", "customer_service", "finance", "auditor")


class AdminUser(Base, TimestampMixin):
    __tablename__ = "admin_user"
    __table_args__ = (
        CheckConstraint(
            f"role = ANY (ARRAY[{', '.join(repr(r) for r in ADMIN_ROLES)}])",
            name="ck_admin_user_role",
        ),
    )

    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="content_editor")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
