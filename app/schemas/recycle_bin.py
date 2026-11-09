"""统一回收站 API 契约。"""

from datetime import datetime

from pydantic import BaseModel, Field


class RecycleBinItem(BaseModel):
    module: str
    id: int = Field(ge=1)
    title: str
    # deleted=已删除（30 自然日倒计时）；archived=已归档（不倒计时）
    status: str
    deleted_at: datetime | None = None
    restore_until: datetime | None = None
    expired: bool = False
    restorable: bool = True


class RecycleBinSummaryItem(BaseModel):
    module: str
    label: str
    deleted_count: int = Field(ge=0)
    archived_count: int = Field(ge=0, default=0)


class RecycleBinRestoreRequest(BaseModel):
    module: str
    ids: list[int] = Field(min_length=1)


class RecycleBinPurgeRequest(BaseModel):
    module: str
    ids: list[int] = Field(min_length=1)
