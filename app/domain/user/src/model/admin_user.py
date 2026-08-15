from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.adapter.database import Base, TimestampMixin
from app.domain.user.src.rule.admin_roles import ADMIN_ROLES, QUIZ_ADMIN_ROLE


def _display_name_from_username(context) -> str:
    """Keep direct ORM callers safe while API validation requires a name."""

    username = context.get_current_parameters().get("username")
    return str(username or "").strip().lower()


class AdminUser(Base, TimestampMixin):
    __tablename__ = "admin_user"
    __table_args__ = (
        CheckConstraint(
            f"role = ANY (ARRAY[{', '.join(repr(r) for r in ADMIN_ROLES)}])",
            name="ck_admin_user_role",
        ),
        CheckConstraint(
            "username = lower(trim(username))",
            name="ck_admin_user_username_normalized",
        ),
        CheckConstraint(
            "length(trim(display_name)) > 0",
            name="ck_admin_user_display_name_non_empty",
        ),
        CheckConstraint(
            "auth_version >= 1", name="ck_admin_user_auth_version_positive"
        ),
        CheckConstraint(
            "failed_login_attempts >= 0",
            name="ck_admin_user_failed_login_attempts_non_negative",
        ),
        Index(
            "uq_admin_user_username_normalized",
            text("lower(username)"),
            unique=True,
        ),
        Index(
            "uq_admin_user_single_super_admin",
            "role",
            unique=True,
            postgresql_where=text("role = 'super_admin'"),
            sqlite_where=text("role = 'super_admin'"),
        ),
    )

    username: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(
        String(64), nullable=False, default=_display_name_from_username
    )
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=QUIZ_ADMIN_ROLE,
        server_default=QUIZ_ADMIN_ROLE,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    auth_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_ip: Mapped[str | None] = mapped_column(String(45))
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @validates("username")
    def _normalize_username(self, _key: str, value: str) -> str:
        return value.strip().lower()
