import csv
import io

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import (
    Competition,
    CompetitionReg,
    CompetitionTrack,
)
from app.port.exceptions import NotFoundException
from app.schemas.admin_competition import (
    AdminCompetitionCreate,
    AdminCompetitionListItem,
    AdminCompetitionRegistrationItem,
    AdminCompetitionUpdate,
)
from app.schemas.common import PaginatedData
from app.services.competition import _track_briefs


class AdminCompetitionService:

    async def export_csv(self) -> str:
        """导出全部竞赛报名为 CSV（兼容旧 /admin/competition/export）"""
        async with get_db_ctx() as db:
            result = await db.execute(
                select(CompetitionReg).order_by(CompetitionReg.id)
            )
            registrations = result.scalars().all()

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["ID", "用户ID", "竞赛名称", "学校", "赛道", "报名时间"])
            for reg in registrations:
                writer.writerow([
                    reg.id,
                    reg.user_id,
                    reg.competition_name,
                    reg.school,
                    reg.track or "",
                    reg.created_at.isoformat() if reg.created_at else "",
                ])
            return output.getvalue()

    async def list_competitions(
        self, keyword: str | None, page: int, page_size: int
    ) -> PaginatedData[AdminCompetitionListItem]:
        async with get_db_ctx() as db:
            base = select(Competition)
            if keyword:
                base = base.where(Competition.name.ilike(f"%{keyword}%"))
            total = (
                await db.execute(
                    select(func.count()).select_from(base.subquery())
                )
            ).scalar() or 0
            rows = (
                await db.execute(
                    base.order_by(Competition.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            items = [
                AdminCompetitionListItem(
                    id=c.id,
                    name=c.name,
                    description=c.description,
                    cover_url=c.cover_url,
                    start_time=c.start_time,
                    end_time=c.end_time,
                    registration_deadline=c.registration_deadline,
                    is_active=c.is_active,
                    tracks=await _track_briefs(db, c.id),
                    total_enrolled=sum(t.enrolled for t in await _track_briefs(db, c.id)),
                    created_at=c.created_at,
                )
                for c in rows
            ]
            return PaginatedData(
                items=items, total=total, page=page, page_size=page_size
            )

    async def create(self, data: AdminCompetitionCreate) -> AdminCompetitionListItem:
        async with get_db_ctx() as db:
            async with db.begin():
                competition = Competition(
                    name=data.name,
                    description=data.description,
                    cover_url=data.cover_url,
                    start_time=data.start_time,
                    end_time=data.end_time,
                    registration_deadline=data.registration_deadline,
                    is_active=data.is_active,
                )
                db.add(competition)
                await db.flush()
                for t in data.tracks:
                    db.add(
                        CompetitionTrack(
                            competition_id=competition.id,
                            name=t.name,
                            max_participants=t.max_participants,
                            sort_order=t.sort_order,
                        )
                    )
                await db.refresh(competition)
            tracks = await _track_briefs(db, competition.id)
            return AdminCompetitionListItem(
                **{
                    k: getattr(competition, k)
                    for k in (
                        "id", "name", "description", "cover_url", "start_time",
                        "end_time", "registration_deadline", "is_active",
                        "created_at",
                    )
                },
                tracks=tracks,
                total_enrolled=0,
            )

    async def update(
        self, competition_id: int, data: AdminCompetitionUpdate
    ) -> AdminCompetitionListItem:
        async with get_db_ctx() as db:
            async with db.begin():
                competition = await db.get(Competition, competition_id)
                if competition is None:
                    raise NotFoundException("赛事")
                update_data = data.model_dump(exclude_unset=True)
                tracks_input = update_data.pop("tracks", None)
                for key, value in update_data.items():
                    setattr(competition, key, value)
                if tracks_input is not None:
                    # 赛道全量替换
                    existing = (
                        await db.execute(
                            select(CompetitionTrack).where(
                                CompetitionTrack.competition_id == competition_id
                            )
                        )
                    ).scalars().all()
                    for t in existing:
                        await db.delete(t)
                    await db.flush()
                    for t in tracks_input:
                        db.add(
                            CompetitionTrack(
                                competition_id=competition_id,
                                name=t["name"],
                                max_participants=t["max_participants"],
                                sort_order=t["sort_order"],
                            )
                        )
                await db.refresh(competition)
            tracks = await _track_briefs(db, competition.id)
            return AdminCompetitionListItem(
                **{
                    k: getattr(competition, k)
                    for k in (
                        "id", "name", "description", "cover_url", "start_time",
                        "end_time", "registration_deadline", "is_active",
                        "created_at",
                    )
                },
                tracks=tracks,
                total_enrolled=sum(t.enrolled for t in tracks),
            )

    async def delete(self, competition_id: int) -> None:
        async with get_db_ctx() as db:
            competition = await db.get(Competition, competition_id)
            if competition is None:
                raise NotFoundException("赛事")
            await db.delete(competition)
            await db.commit()

    async def list_registrations(
        self,
        competition_id: int,
        track_id: int | None,
        page: int,
        page_size: int,
    ) -> PaginatedData[AdminCompetitionRegistrationItem]:
        async with get_db_ctx() as db:
            competition = await db.get(Competition, competition_id)
            if competition is None:
                raise NotFoundException("赛事")
            base = (
                select(CompetitionReg)
                .join(
                    CompetitionTrack,
                    CompetitionReg.track_id == CompetitionTrack.id,
                )
                .where(CompetitionTrack.competition_id == competition_id)
            )
            if track_id is not None:
                base = base.where(CompetitionReg.track_id == track_id)
            total = (
                await db.execute(
                    select(func.count()).select_from(base.subquery())
                )
            ).scalar() or 0
            rows = (
                await db.execute(
                    base.order_by(CompetitionReg.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return PaginatedData(
                items=[
                    AdminCompetitionRegistrationItem.model_validate(r) for r in rows
                ],
                total=total,
                page=page,
                page_size=page_size,
            )
