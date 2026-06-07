from datetime import datetime, timezone

from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.domain.content.src.index import Activity, Training, Zone
from app.domain.certification.src.index import Certification, CompetitionReg, Course, Job
from app.domain.content.src.model.banner import Banner
from app.schemas.activity import ActivityResponse
from app.schemas.admin_training import AdminTrainingListItem
from app.schemas.certification import CertificationResponse
from app.schemas.competition import CompetitionRegResponse
from app.schemas.course import CourseListResponse
from app.schemas.job import JobResponse
from app.schemas.zone import (
    BannerBrief,
    HomeAggregationResponse,
    ZONE_ENTITY_MAP,
    ZoneBrief,
    ZoneSectionData,
)

# Maximum items per zone_type in home aggregation
HOME_ZONE_LIMIT = 10

ALL_ZONE_TYPES = ("cert", "study", "competition", "activity", "employment", "training")

# Entity query config: (model_class, response_schema, has_is_active_filter)
_ENTITY_QUERIES: dict[str, tuple] = {
    "courses":         (Course,         CourseListResponse,        True),
    "activities":      (Activity,       ActivityResponse,          True),
    "certifications":  (Certification,  CertificationResponse,     True),
    "trainings":       (Training,       AdminTrainingListItem,     True),
    "competitions":    (CompetitionReg, CompetitionRegResponse,    False),
    "jobs":            (Job,            JobResponse,               True),
}


class ZoneService:

    # ── B-P0.1 首页聚合 ───────────────────────────────────────────

    async def get_home_aggregation(self) -> HomeAggregationResponse:
        async with get_db_ctx() as db:
            now = datetime.now(timezone.utc)

            # ── Banners ──────────────────────────────────────────
            banner_stmt = (
                select(Banner)
                .where(
                    Banner.is_active == True,
                    (Banner.start_time == None) | (Banner.start_time <= now),
                    (Banner.end_time == None) | (Banner.end_time >= now),
                )
                .order_by(Banner.sort, Banner.id.desc())
            )
            banner_result = await db.execute(banner_stmt)
            banners = [
                BannerBrief.model_validate(b) for b in banner_result.scalars().all()
            ]

            # ── Zone cards ───────────────────────────────────────
            zones: dict[str, list[ZoneBrief]] = {}
            for ztype in ALL_ZONE_TYPES:
                zone_stmt = (
                    select(Zone)
                    .where(Zone.zone_type == ztype, Zone.is_active == True)
                    .order_by(Zone.sort_order, Zone.id.desc())
                    .limit(HOME_ZONE_LIMIT)
                )
                zone_result = await db.execute(zone_stmt)
                zone_list = [
                    ZoneBrief.model_validate(z)
                    for z in zone_result.scalars().all()
                ]
                if zone_list:
                    zones[ztype] = zone_list

            # ── Entity data ──────────────────────────────────────
            entity_data: dict[str, list] = {}
            for field_name, (model_cls, schema_cls, active_filter) in _ENTITY_QUERIES.items():
                stmt = select(model_cls)
                if active_filter:
                    stmt = stmt.where(model_cls.is_active == True)
                stmt = stmt.order_by(model_cls.id.desc()).limit(HOME_ZONE_LIMIT)
                result = await db.execute(stmt)
                entity_data[field_name] = [
                    schema_cls.model_validate(obj)
                    for obj in result.scalars().all()
                ]

            # ── Build zone sections ──────────────────────────────
            zone_sections: dict[str, ZoneSectionData] = {}
            for ztype in ALL_ZONE_TYPES:
                section_kwargs: dict = {"items": zones.get(ztype, [])}
                field_name = ZONE_ENTITY_MAP.get(ztype)
                if field_name and entity_data.get(field_name):
                    section_kwargs[field_name] = entity_data[field_name]
                if any(section_kwargs.values()):
                    zone_sections[ztype] = ZoneSectionData(**section_kwargs)

            return HomeAggregationResponse(banners=banners, zones=zone_sections)
