from datetime import datetime

from pydantic import BaseModel, Field, model_serializer

from app.schemas.activity import ActivityResponse
from app.schemas.admin_training import AdminTrainingListItem
from app.schemas.certification import CertificationResponse
from app.schemas.competition import CompetitionRegResponse
from app.schemas.course import CourseListResponse
from app.schemas.job import JobResponse


# ── zone_type → entity field mapping ──────────────────────────────
# Drives entity assignment in ZoneService.get_home_aggregation.
# Each zone_type maps to the ZoneSectionData field that carries its
# associated entity list.

ZONE_ENTITY_MAP: dict[str, str] = {
    "cert":        "certifications",
    "study":       "courses",
    "competition": "competitions",
    "activity":    "activities",
    "employment":  "jobs",
    "training":    "trainings",
}


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
    """专区聚合数据：Zone 入口卡片 + 该专区的业务数据

    实体字段按 zone_type 就近归属（例如 study → courses）。
    未赋值的实体字段在序列化时自动剔除，不会出现在响应 JSON 中。
    """
    items: list[ZoneBrief] = Field(default_factory=list)
    courses: list[CourseListResponse] | None = None
    activities: list[ActivityResponse] | None = None
    certifications: list[CertificationResponse] | None = None
    trainings: list[AdminTrainingListItem] | None = None
    competitions: list[CompetitionRegResponse] | None = None
    jobs: list[JobResponse] | None = None

    @model_serializer(mode='wrap')
    def _compact(self, handler, info):
        """剔除值为 None 的字段，只保留有数据的部分。"""
        result = handler(self)
        return {k: v for k, v in result.items() if v is not None}


class HomeAggregationResponse(BaseModel):
    banners: list[BannerBrief] = Field(default_factory=list)
    zones: dict[str, ZoneSectionData] = Field(default_factory=dict)
