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
    ZoneBrief,
    ZoneSectionData,
)

# Maximum items per zone_type in home aggregation
HOME_ZONE_LIMIT = 10

ALL_ZONE_TYPES = ("cert", "study", "competition", "activity", "employment", "training")


class ZoneService:

    # ── B-P0.1 首页聚合 ───────────────────────────────────────────

    async def get_home_aggregation(self) -> HomeAggregationResponse:
        async with get_db_ctx() as db:
            now = datetime.now(timezone.utc)

            # Active banners from Banner table, within valid time range
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
            banners: list[BannerBrief] = [
                BannerBrief.model_validate(b) for b in banner_result.scalars().all()
            ]

            # All zone types: top N active zones sorted by sort_order
            zones: dict[str, list[ZoneBrief]] = {}
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
                    ZoneBrief.model_validate(z)
                    for z in zone_result.scalars().all()
                ]
                if zone_list:
                    zones[ztype] = zone_list

            # Top active courses
            course_stmt = (
                select(Course)
                .where(Course.is_active == True)
                .order_by(Course.id.desc())
                .limit(HOME_ZONE_LIMIT)
            )
            course_result = await db.execute(course_stmt)
            courses = [CourseListResponse.model_validate(c) for c in course_result.scalars().all()]

            # Top active activities
            activity_stmt = (
                select(Activity)
                .where(Activity.is_active == True)
                .order_by(Activity.id.desc())
                .limit(HOME_ZONE_LIMIT)
            )
            activity_result = await db.execute(activity_stmt)
            activities = [ActivityResponse.model_validate(a) for a in activity_result.scalars().all()]

            # Top active certifications
            cert_stmt = (
                select(Certification)
                .where(Certification.is_active == True)
                .order_by(Certification.id.desc())
                .limit(HOME_ZONE_LIMIT)
            )
            cert_result = await db.execute(cert_stmt)
            certifications = [CertificationResponse.model_validate(c) for c in cert_result.scalars().all()]

            # Top active trainings
            training_stmt = (
                select(Training)
                .where(Training.is_active == True)
                .order_by(Training.id.desc())
                .limit(HOME_ZONE_LIMIT)
            )
            training_result = await db.execute(training_stmt)
            trainings = [AdminTrainingListItem.model_validate(t) for t in training_result.scalars().all()]

            # Top active competition registrations
            comp_stmt = (
                select(CompetitionReg)
                .order_by(CompetitionReg.id.desc())
                .limit(HOME_ZONE_LIMIT)
            )
            comp_result = await db.execute(comp_stmt)
            competitions = [CompetitionRegResponse.model_validate(c) for c in comp_result.scalars().all()]

            # Top active jobs
            job_stmt = (
                select(Job)
                .where(Job.is_active == True)
                .order_by(Job.id.desc())
                .limit(HOME_ZONE_LIMIT)
            )
            job_result = await db.execute(job_stmt)
            jobs = [JobResponse.model_validate(j) for j in job_result.scalars().all()]

            # 将各专区业务数据收敛到 zones 字典内
            zone_sections: dict[str, ZoneSectionData] = {}
            for ztype in ALL_ZONE_TYPES:
                section = ZoneSectionData(items=zones.get(ztype, []))
                if ztype == "cert":
                    section.certifications = certifications
                elif ztype == "study":
                    section.courses = courses
                elif ztype == "competition":
                    section.competitions = competitions
                elif ztype == "activity":
                    section.activities = activities
                elif ztype == "employment":
                    section.jobs = jobs
                elif ztype == "training":
                    section.trainings = trainings
                if (section.items or section.courses or section.activities
                        or section.certifications or section.trainings
                        or section.competitions or section.jobs):
                    zone_sections[ztype] = section

            return HomeAggregationResponse(banners=banners, zones=zone_sections)
