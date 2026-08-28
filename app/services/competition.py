import csv
import io
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import (
    Competition,
    CompetitionReg,
    CompetitionTrack,
)
from app.port.exceptions import BusinessException, NotFoundException
from app.schemas.competition import (
    CompetitionListItem,
    CompetitionSignupRequest,
    CompetitionTrackBrief,
)


async def _track_briefs(db, competition_id: int) -> list[CompetitionTrackBrief]:
    rows = (
        await db.execute(
            select(CompetitionTrack, func.count(CompetitionReg.id))
            .outerjoin(
                CompetitionReg,
                CompetitionReg.track_id == CompetitionTrack.id,
            )
            .where(CompetitionTrack.competition_id == competition_id)
            .group_by(CompetitionTrack.id)
            .order_by(CompetitionTrack.sort_order, CompetitionTrack.id)
        )
    ).all()
    return [
        CompetitionTrackBrief(
            id=track.id,
            name=track.name,
            max_participants=track.max_participants,
            enrolled=int(enrolled or 0),
            remaining=(
                None
                if track.max_participants == 0
                else max(track.max_participants - int(enrolled or 0), 0)
            ),
            sort_order=track.sort_order,
        )
        for track, enrolled in rows
    ]


class CompetitionService:
    """用户端赛事服务：列表 + 报名"""

    async def list_events(
        self, *, active_only: bool = True
    ) -> list[CompetitionListItem]:
        async with get_db_ctx() as db:
            stmt = select(Competition).order_by(Competition.id.desc())
            if active_only:
                stmt = stmt.where(Competition.is_active == True)  # noqa: E712
            competitions = (await db.execute(stmt)).scalars().all()
            return [
                CompetitionListItem(
                    id=c.id,
                    name=c.name,
                    description=c.description,
                    cover_url=c.cover_url,
                    start_time=c.start_time,
                    end_time=c.end_time,
                    registration_deadline=c.registration_deadline,
                    is_active=c.is_active,
                    tracks=await _track_briefs(db, c.id),
                    created_at=c.created_at,
                )
                for c in competitions
            ]

    async def signup(self, user_id: int, data: CompetitionSignupRequest) -> CompetitionReg:
        async with get_db_ctx() as db:
            track = (
                await db.execute(
                    select(CompetitionTrack, Competition)
                    .join(Competition, Competition.id == CompetitionTrack.competition_id)
                    .where(CompetitionTrack.id == data.track_id)
                )
            ).first()
            if track is None:
                raise NotFoundException("赛道")
            track_obj, competition = track

            if not competition.is_active:
                raise BusinessException("赛事未发布，无法报名")

            now = datetime.now(timezone.utc)
            if competition.end_time is not None and competition.end_time <= now:
                raise BusinessException("赛事已结束，无法报名")
            if (
                competition.registration_deadline is not None
                and competition.registration_deadline <= now
            ):
                raise BusinessException("报名已截止")

            if track_obj.max_participants > 0:
                enrolled = (
                    await db.execute(
                        select(func.count()).where(
                            CompetitionReg.track_id == track_obj.id
                        )
                    )
                ).scalar() or 0
                if enrolled >= track_obj.max_participants:
                    raise BusinessException("该赛道报名人数已满")

            existing = (
                await db.execute(
                    select(CompetitionReg).where(
                        CompetitionReg.user_id == user_id,
                        CompetitionReg.track_id == track_obj.id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise BusinessException("已报名过该赛道")

            reg = CompetitionReg(
                user_id=user_id,
                competition_name=competition.name,
                school=data.school,
                track=track_obj.name,
                track_id=track_obj.id,
                real_name=data.real_name,
                phone=data.phone,
            )
            db.add(reg)
            await db.commit()
            await db.refresh(reg)
            return reg

    async def export_my_registrations(self, user_id: int) -> str:
        async with get_db_ctx() as db:
            result = await db.execute(
                select(CompetitionReg)
                .where(CompetitionReg.user_id == user_id)
                .order_by(CompetitionReg.id)
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
