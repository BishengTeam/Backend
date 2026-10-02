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
from app.schemas.competition_form import validate_field_values
from app.schemas.competition import (
    CompetitionMyRegistrationItem,
    CompetitionListItem,
    CompetitionSignupRequest,
    CompetitionRegistrationUpdateRequest,
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


def _as_utc(value: datetime | None) -> datetime | None:
    """归一化为带时区的 UTC 时间，避免 naive/aware 比较异常。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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
                    custom_fields=c.custom_fields,
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
            end_time = _as_utc(competition.end_time)
            deadline = _as_utc(competition.registration_deadline)
            if end_time is not None and end_time <= now:
                raise BusinessException("赛事已结束，无法报名")
            if deadline is not None and deadline <= now:
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

                        # Validate custom field values
            validated_custom = validate_field_values(
                competition.custom_fields,
                data.custom_field_values if hasattr(data, "custom_field_values") else None,
            )

            reg = CompetitionReg(
                custom_field_values=validated_custom,
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

    async def my_registrations(self, user_id: int) -> list[CompetitionMyRegistrationItem]:
        """当前用户的全部竞赛报名（含赛事截止配置，供前端判断可否修改）"""
        async with get_db_ctx() as db:
            rows = (
                await db.execute(
                    select(CompetitionReg, CompetitionTrack, Competition)
                    .outerjoin(
                        CompetitionTrack,
                        CompetitionReg.track_id == CompetitionTrack.id,
                    )
                    .outerjoin(
                        Competition, Competition.id == CompetitionTrack.competition_id
                    )
                    .where(CompetitionReg.user_id == user_id)
                    .order_by(CompetitionReg.id.desc())
                )
            ).all()
            now = datetime.now(timezone.utc)
            items: list[CompetitionMyRegistrationItem] = []
            for reg, _track, competition in rows:
                deadline = _as_utc(competition.registration_deadline) if competition else None
                end_time = _as_utc(competition.end_time) if competition else None
                editable = not (
                    (deadline is not None and deadline <= now)
                    or (end_time is not None and end_time <= now)
                )
                items.append(
                    CompetitionMyRegistrationItem(
                        id=reg.id,
                        competition_id=competition.id if competition else None,
                        competition_name=reg.competition_name,
                        track_id=reg.track_id,
                        track=reg.track,
                        school=reg.school,
                        real_name=reg.real_name,
                        phone=reg.phone,
                        custom_field_values=reg.custom_field_values,
                        registration_deadline=competition.registration_deadline if competition else None,
                        end_time=competition.end_time if competition else None,
                        custom_fields=competition.custom_fields if competition else None,
                        editable=editable,
                        created_at=reg.created_at,
                    )
                )
            return items

    async def update_registration(
        self,
        user_id: int,
        registration_id: int,
        data: CompetitionRegistrationUpdateRequest,
    ) -> CompetitionReg:
        """用户修改自己的报名信息（仅报名截止前；赛道不可更换）"""
        async with get_db_ctx() as db:
            row = (
                await db.execute(
                    select(CompetitionReg, CompetitionTrack, Competition)
                    .outerjoin(
                        CompetitionTrack,
                        CompetitionReg.track_id == CompetitionTrack.id,
                    )
                    .outerjoin(
                        Competition, Competition.id == CompetitionTrack.competition_id
                    )
                    .where(CompetitionReg.id == registration_id)
                )
            ).first()
            # 不区分“不存在”与“非本人”，避免探测他人报名 ID
            if row is None or row[0].user_id != user_id:
                raise NotFoundException("报名记录")
            reg, _track, competition = row
            if competition is None:
                raise BusinessException("赛事信息缺失，无法修改")

            now = datetime.now(timezone.utc)
            end_time = _as_utc(competition.end_time)
            deadline = _as_utc(competition.registration_deadline)
            if end_time is not None and end_time <= now:
                raise BusinessException("赛事已结束，无法修改")
            if deadline is not None and deadline <= now:
                raise BusinessException("报名已截止，无法修改")

            reg.school = data.school
            reg.real_name = data.real_name
            reg.phone = data.phone
            reg.custom_field_values = validate_field_values(
                competition.custom_fields, data.custom_field_values
            )
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
