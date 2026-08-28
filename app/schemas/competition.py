from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ── 用户端（小程序） ──────────────────────────────────────────

class CompetitionTrackBrief(BaseModel):
    """赛道（含报名余量）"""

    id: int
    name: str
    max_participants: int
    enrolled: int
    remaining: int | None = None  # None = 不限
    sort_order: int


class CompetitionListItem(BaseModel):
    """赛事列表项（小程序 + 管理端共用）"""

    id: int
    name: str
    description: str | None = None
    cover_url: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    registration_deadline: datetime | None = None
    is_active: bool = True
    tracks: list[CompetitionTrackBrief] = Field(default_factory=list)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CompetitionSignupRequest(BaseModel):
    track_id: int = Field(..., ge=1, description="赛道 ID")
    school: str = Field(..., min_length=1, max_length=128, description="学校")
    real_name: str = Field(..., min_length=1, max_length=64, description="真实姓名")
    phone: str = Field(..., min_length=1, max_length=20, description="联系电话")


class CompetitionRegResponse(BaseModel):
    id: int
    competition_name: str
    school: str
    track: str | None = None
    real_name: str | None = None
    phone: str | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
