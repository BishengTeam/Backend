from datetime import datetime, timezone

from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.domain.content.src.index import Activity, Zone
from app.domain.certification.src.index import CompetitionReg, Course
from app.schemas.zone import (
    ActivityBrief,
    BannerBrief,
    CompetitionBrief,
    CompetitionZoneResponse,
    CourseBrief,
    HomeAggregationResponse,
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

            # Active banners: zones with is_banner=True and within valid time range
            banner_stmt = (
                select(Zone)
                .where(
                    Zone.is_active == True,
                    Zone.is_banner == True,
                    (Zone.start_time == None) | (Zone.start_time <= now),
                    (Zone.end_time == None) | (Zone.end_time >= now),
                )
                .order_by(Zone.sort_order, Zone.id.desc())
            )
            banner_result = await db.execute(banner_stmt)
            banners: list[BannerBrief] = []
            for z in banner_result.scalars().all():
                banners.append(
                    BannerBrief(
                        id=z.id,
                        image_url=z.cover_url or "",
                        jump_link=z.link_url,
                        sort=z.sort_order,
                    )
                )

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

            # Top active courses
            course_stmt = (
                select(Course)
                .where(Course.is_active == True)
                .order_by(Course.id.desc())
                .limit(HOME_ZONE_LIMIT)
            )
            course_result = await db.execute(course_stmt)
            courses = [CourseBrief.model_validate(c) for c in course_result.scalars().all()]

            # Top active activities
            activity_stmt = (
                select(Activity)
                .where(Activity.is_active == True)
                .order_by(Activity.id.desc())
                .limit(HOME_ZONE_LIMIT)
            )
            activity_result = await db.execute(activity_stmt)
            activities = [ActivityBrief.model_validate(a) for a in activity_result.scalars().all()]

            return HomeAggregationResponse(
                banners=banners, zones=zones, courses=courses, activities=activities
            )

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


