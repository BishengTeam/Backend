from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, RecycleBinMixin, TimestampMixin

# 来源：sync=同步 worker 维护；manual=Admin 手动维护（worker 不覆盖、不清理）
SOURCE_SYNC = "sync"
SOURCE_MANUAL = "manual"


class GzhArticle(Base, TimestampMixin, RecycleBinMixin):
    """公众号已发布推文（定时同步 freepublish/batchget，供首页资讯栏）"""

    __tablename__ = "gzh_articles"

    title: Mapped[str] = mapped_column(String(256), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    thumb_url: Mapped[str | None] = mapped_column(String(512))
    publish_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SOURCE_SYNC, server_default=SOURCE_SYNC
    )
