from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class GzhArticle(Base, TimestampMixin):
    """公众号已发布推文（定时同步 freepublish/batchget，供首页资讯栏）"""

    __tablename__ = "gzh_articles"

    title: Mapped[str] = mapped_column(String(256), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    thumb_url: Mapped[str | None] = mapped_column(String(512))
    publish_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
