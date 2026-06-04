from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DeletedOpenid(Base, TimestampMixin):
    __tablename__ = "deleted_openid"

    openid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
