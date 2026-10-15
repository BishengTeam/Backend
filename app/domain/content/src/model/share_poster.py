from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.adapter.database import Base, TimestampMixin


class SharePoster(Base, TimestampMixin):
    """分享海报：管理端上传的单张推广海报，小程序「分享好友」页展示/保存用"""

    __tablename__ = "share_poster"

    image_url: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
