from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.activity import ActivityResponse
from app.schemas.admin_training import AdminTrainingListItem
from app.schemas.certification import CertificationResponse
from app.schemas.competition import CompetitionRegResponse
from app.schemas.course import CourseListResponse
from app.schemas.job import JobResponse


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


# ── Response models ───────────────────────────────────────────────

class ZoneSectionData(BaseModel):
    """专区聚合数据：Zone 入口卡片 + 该专区的业务数据"""
    items: list[ZoneBrief] = Field(default_factory=list)
    courses: list[CourseListResponse] | None = None
    activities: list[ActivityResponse] | None = None
    certifications: list[CertificationResponse] | None = None
    trainings: list[AdminTrainingListItem] | None = None
    competitions: list[CompetitionRegResponse] | None = None
    jobs: list[JobResponse] | None = None


class HomeAggregationResponse(BaseModel):
    banners: list[BannerBrief] = Field(default_factory=list)
    zones: dict[str, ZoneSectionData] = Field(default_factory=dict)
