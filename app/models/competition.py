from sqlalchemy import String, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class CompetitionReg(Base, TimestampMixin):
    __tablename__ = "competition_reg"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    competition_name: Mapped[str] = mapped_column(String(128), nullable=False)
    school: Mapped[str] = mapped_column(String(128), nullable=False)
    track: Mapped[str | None] = mapped_column(String(64))
