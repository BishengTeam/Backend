from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.certification import CertificationResponse


# ── Shared briefs ──────────────────────────────────────────────────

class BannerBrief(BaseModel):
    id: int
    image_url: str
    jump_link: str | None = None
    sort: int

    model_config = {"from_attributes": True}


class ZoneBrief(BaseModel):
    id: int
    zone_type: str
    title: str
    cover_url: str | None = None
    description: str | None = None
    link_url: str | None = None
    sort_order: int

    model_config = {"from_attributes": True}


class ZoneSectionItem(BaseModel):
    """Compact zone item for home aggregation sections."""
    id: int
    title: str
    cover_url: str | None = None
    description: str | None = None

    model_config = {"from_attributes": True}


class CourseBrief(BaseModel):
    id: int
    title: str
    category: str
    description: str | None = None
    cover_url: str | None = None
    price: int
    teacher_name: str | None = None

    model_config = {"from_attributes": True}


class CompetitionBrief(BaseModel):
    id: int
    competition_name: str
    school: str
    track: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ActivityBrief(BaseModel):
    id: int
    title: str
    description: str | None = None
    cover_url: str | None = None
    location: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    max_participants: int

    model_config = {"from_attributes": True}


class JobBrief(BaseModel):
    id: int
    title: str
    company: str
    location: str | None = None
    salary_range: str | None = None
    description: str | None = None
    requirements: str | None = None
    contact_info: str | None = None

    model_config = {"from_attributes": True}


# ── Response models ───────────────────────────────────────────────

class HomeAggregationResponse(BaseModel):
    banners: list[BannerBrief] = Field(default_factory=list)
    zones: dict[str, list[ZoneSectionItem]] = Field(default_factory=dict)


class CertZoneResponse(BaseModel):
    zones: list[ZoneBrief] = Field(default_factory=list)
    certifications: list[CertificationResponse] = Field(default_factory=list)


class StudyZoneResponse(BaseModel):
    zones: list[ZoneBrief] = Field(default_factory=list)
    courses: list[CourseBrief] = Field(default_factory=list)


class CompetitionZoneResponse(BaseModel):
    zones: list[ZoneBrief] = Field(default_factory=list)
    competitions: list[CompetitionBrief] = Field(default_factory=list)


class ActivityZoneResponse(BaseModel):
    zones: list[ZoneBrief] = Field(default_factory=list)
    activities: list[ActivityBrief] = Field(default_factory=list)


class EmploymentZoneResponse(BaseModel):
    zones: list[ZoneBrief] = Field(default_factory=list)
    jobs: list[JobBrief] = Field(default_factory=list)
