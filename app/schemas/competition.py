from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CompetitionStatsItem(BaseModel):
    school: str
    count: int


class CompetitionRegResponse(BaseModel):
    id: int
    competition_name: str
    school: str
    track: str | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class TrackListResponse(BaseModel):
    tracks: list[str]


class CompetitionSignupRequest(BaseModel):
    competition_name: str = Field(..., min_length=1, max_length=128, description="竞赛名称")
    school: str = Field(..., min_length=1, max_length=128, description="学校")
    track: str | None = Field(None, max_length=64, description="赛道")
