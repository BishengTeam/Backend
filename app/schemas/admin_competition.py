from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.competition import CompetitionTrackBrief


class AdminCompetitionTrackInput(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    max_participants: int = Field(0, ge=0)
    sort_order: int = Field(0, ge=0)


class AdminCompetitionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = None
    cover_url: str | None = Field(None, max_length=512)
    start_time: datetime | None = None
    end_time: datetime | None = None
    registration_deadline: datetime | None = None
    is_active: bool = True
    tracks: list[AdminCompetitionTrackInput] = Field(default_factory=list)


class AdminCompetitionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = None
    cover_url: str | None = Field(None, max_length=512)
    start_time: datetime | None = None
    end_time: datetime | None = None
    registration_deadline: datetime | None = None
    is_active: bool | None = None
    tracks: list[AdminCompetitionTrackInput] | None = None


class AdminCompetitionListItem(BaseModel):
    id: int
    name: str
    description: str | None = None
    cover_url: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    registration_deadline: datetime | None = None
    is_active: bool
    tracks: list[CompetitionTrackBrief] = Field(default_factory=list)
    total_enrolled: int = 0
    created_at: datetime


class AdminCompetitionRegistrationItem(BaseModel):
    id: int
    user_id: int
    competition_name: str
    track: str | None = None
    track_id: int | None = None
    school: str
    real_name: str | None = None
    phone: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}
