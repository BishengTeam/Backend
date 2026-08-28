from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class Competition(Base, TimestampMixin):
    """赛事（一场比赛，含多条赛道）"""

    __tablename__ = "competition"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(String(512))
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registration_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class CompetitionTrack(Base, TimestampMixin):
    """赛道（隶属赛事，独立限额）"""

    __tablename__ = "competition_track"

    competition_id: Mapped[int] = mapped_column(
        ForeignKey("competition.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    max_participants: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class CompetitionReg(Base, TimestampMixin):
    __tablename__ = "competition_reg"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    competition_name: Mapped[str] = mapped_column(String(128), nullable=False)
    school: Mapped[str] = mapped_column(String(128), nullable=False)
    track: Mapped[str | None] = mapped_column(String(64))
    # ── 赛事化报名扩展 ──
    track_id: Mapped[int | None] = mapped_column(
        ForeignKey("competition_track.id", ondelete="SET NULL")
    )
    real_name: Mapped[str | None] = mapped_column(String(64))
    phone: Mapped[str | None] = mapped_column(String(20))
