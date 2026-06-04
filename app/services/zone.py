from datetime import datetime, timezone

from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.models.activity import Activity
from app.models.banner import Banner
from app.models.certification import Certification
from app.models.competition import CompetitionReg
from app.models.course import Course
from app.models.job import Job
from app.models.zone import Zone
from app.schemas.certification import CertificationResponse
from app.schemas.zone import (
    ActivityBrief,
    ActivityZoneResponse,
    BannerBrief,
    CertZoneResponse,
    CompetitionBrief,
    CompetitionZoneResponse,
    CourseBrief,
    EmploymentZoneResponse,
    HomeAggregationResponse,
    JobBrief,
    StudyZoneResponse,
    ZoneBrief,
    ZoneSectionItem,
)

# Maximum items per zone_type in home aggregation
HOME_ZONE_LIMIT = 10

ALL_ZONE_TYPES = ("cert", "study", "competition", "activity", "employment")


class ZoneService:

    # ── B-P0.1 首页聚合 ───────────────────────────────────────────

    async def get_home_aggregation(self) -> HomeAggregationResponse:
        async with get_db_ctx() as db:
            now = datetime.now(timezone.utc)

            # Active banners within valid time range, sorted by sort
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
            banners = [BannerBrief.model_validate(b) for b in banner_result.scalars().all()]

            # All zone types: top N active zones sorted by sort_order
            zones: dict[str, list[ZoneSectionItem]] = {}
            for ztype in ALL_ZONE_TYPES:
                zone_stmt = (
                    select(Zone)
                    .where(
                        Zone.zone_type == ztype,
                        Zone.is_active == True,
                    )
                    .order_by(Zone.sort_order, Zone.id.desc())
                    .limit(HOME_ZONE_LIMIT)
                )
                zone_result = await db.execute(zone_stmt)
                zone_list = [
                    ZoneSectionItem.model_validate(z)
                    for z in zone_result.scalars().all()
                ]
                if zone_list:
                    zones[ztype] = zone_list

            return HomeAggregationResponse(banners=banners, zones=zones)

    # ── B-P0.2 认证专区 ───────────────────────────────────────────

    async def get_cert_zone(self) -> CertZoneResponse:
        async with get_db_ctx() as db:
            zone_stmt = (
                select(Zone)
                .where(
                    Zone.zone_type == "cert",
                    Zone.is_active == True,
                )
                .order_by(Zone.sort_order, Zone.id.desc())
            )
            zone_result = await db.execute(zone_stmt)
            zones = [ZoneBrief.model_validate(z) for z in zone_result.scalars().all()]

            cert_stmt = (
                select(Certification)
                .where(Certification.is_active == True)
                .order_by(Certification.id)
            )
            cert_result = await db.execute(cert_stmt)
            certifications = [
                CertificationResponse.model_validate(c)
                for c in cert_result.scalars().all()
            ]

            return CertZoneResponse(zones=zones, certifications=certifications)

    # ── B-P0.3 学习专区 ───────────────────────────────────────────

    async def get_study_zone(self) -> StudyZoneResponse:
        async with get_db_ctx() as db:
            zone_stmt = (
                select(Zone)
                .where(
                    Zone.zone_type == "study",
                    Zone.is_active == True,
                )
                .order_by(Zone.sort_order, Zone.id.desc())
            )
            zone_result = await db.execute(zone_stmt)
            zones = [ZoneBrief.model_validate(z) for z in zone_result.scalars().all()]

            course_stmt = (
                select(Course)
                .where(Course.is_active == True)
                .order_by(Course.id.desc())
            )
            course_result = await db.execute(course_stmt)
            courses = [CourseBrief.model_validate(c) for c in course_result.scalars().all()]

            return StudyZoneResponse(zones=zones, courses=courses)

    # ── B-P0.4 竞赛专区 ───────────────────────────────────────────

    async def get_competition_zone(
        self, user_id: int | None = None
    ) -> CompetitionZoneResponse:
        async with get_db_ctx() as db:
            zone_stmt = (
                select(Zone)
                .where(
                    Zone.zone_type == "competition",
                    Zone.is_active == True,
                )
                .order_by(Zone.sort_order, Zone.id.desc())
            )
            zone_result = await db.execute(zone_stmt)
            zones = [ZoneBrief.model_validate(z) for z in zone_result.scalars().all()]

            competitions: list[CompetitionBrief] = []
            if user_id is not None:
                comp_stmt = (
                    select(CompetitionReg)
                    .where(CompetitionReg.user_id == user_id)
                    .order_by(CompetitionReg.id.desc())
                )
            else:
                # All competition registrations (public listing)
                comp_stmt = (
                    select(CompetitionReg)
                    .order_by(CompetitionReg.id.desc())
                )
            comp_result = await db.execute(comp_stmt)
            competitions = [
                CompetitionBrief.model_validate(c)
                for c in comp_result.scalars().all()
            ]

            return CompetitionZoneResponse(zones=zones, competitions=competitions)

    # ── B-P0.5 活动专区 ───────────────────────────────────────────

    async def get_activity_zone(self) -> ActivityZoneResponse:
        async with get_db_ctx() as db:
            zone_stmt = (
                select(Zone)
                .where(
                    Zone.zone_type == "activity",
                    Zone.is_active == True,
                )
                .order_by(Zone.sort_order, Zone.id.desc())
            )
            zone_result = await db.execute(zone_stmt)
            zones = [ZoneBrief.model_validate(z) for z in zone_result.scalars().all()]

            activity_stmt = (
                select(Activity)
                .where(Activity.is_active == True)
                .order_by(Activity.id.desc())
            )
            activity_result = await db.execute(activity_stmt)
            activities = [
                ActivityBrief.model_validate(a)
                for a in activity_result.scalars().all()
            ]

            return ActivityZoneResponse(zones=zones, activities=activities)

    # ── B-P0.6 就业专区 ───────────────────────────────────────────

    async def get_employment_zone(self) -> EmploymentZoneResponse:
        async with get_db_ctx() as db:
            zone_stmt = (
                select(Zone)
                .where(
                    Zone.zone_type == "employment",
                    Zone.is_active == True,
                )
                .order_by(Zone.sort_order, Zone.id.desc())
            )
            zone_result = await db.execute(zone_stmt)
            zones = [ZoneBrief.model_validate(z) for z in zone_result.scalars().all()]

            job_stmt = (
                select(Job)
                .where(Job.is_active == True)
                .order_by(Job.id.desc())
            )
            job_result = await db.execute(job_stmt)
            jobs = [JobBrief.model_validate(j) for j in job_result.scalars().all()]

            return EmploymentZoneResponse(zones=zones, jobs=jobs)
