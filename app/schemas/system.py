from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PosterResponse(BaseModel):
    url: str | None = Field(None, description="登录页海报图片 URL")


class ServerTimeResponse(BaseModel):
    unix: int = Field(..., description="服务器 Unix 秒时间戳")
    unix_ms: int = Field(..., description="服务器 Unix 毫秒时间戳，用于亚秒级校准")
    iso: datetime = Field(..., description="服务器 ISO-8601 时间")
    timezone: Literal["UTC"] = "UTC"
