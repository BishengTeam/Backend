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
from app.schemas.competition_form import validate_custom_fields
from app.utils.excel import export_csv


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

    async def export_registrations_csv(
        self, competition_id: int, track_id: int | None
    ) -> str:
        """按赛事（可按赛道）导出报名 CSV；自定义字段按赛事配置动态成列。"""
        async with get_db_ctx() as db:
            competition = await db.get(Competition, competition_id)
            if competition is None:
                raise NotFoundException("赛事")
            fields = sorted(
                competition.custom_fields or [],
                key=lambda f: f.get("sort_order", 0),
            )
            headers = (
                ["报名ID", "姓名", "学校", "手机号", "赛道"]
                + [f["label"] for f in fields]
                + ["报名时间"]
            )
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
            regs = (
                await db.execute(base.order_by(CompetitionReg.id))
            ).scalars().all()
            rows: list[list] = []
            for reg in regs:
                values = reg.custom_field_values or {}
                custom_cells = []
                for field in fields:
                    value = values.get(field["key"])
                    if isinstance(value, list):
                        custom_cells.append(",".join(str(v) for v in value))
                    elif value is None:
                        custom_cells.append("")
                    else:
                        custom_cells.append(str(value))
                rows.append(
                    [
                        reg.id,
                        reg.real_name or "",
                        reg.school,
                        reg.phone or "",
                        reg.track or "",
                        *custom_cells,
                        reg.created_at.strftime("%Y-%m-%d %H:%M:%S")
                        if reg.created_at
                        else "",
                    ]
                )
            return export_csv(headers, rows).getvalue()

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
            items: list[AdminCompetitionListItem] = []
            for c in rows:
                tracks = await _track_briefs(db, c.id)
                items.append(
                    AdminCompetitionListItem(
                        **{
                            k: getattr(c, k)
                            for k in (
                                "id", "name", "description", "cover_url", "start_time",
                                "end_time", "registration_deadline", "is_active",
                                "custom_fields", "created_at",
                            )
                        },
                        tracks=tracks,
                        total_enrolled=sum(t.enrolled for t in tracks),
                    )
                )
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
                    custom_fields=validate_custom_fields(
                        data.custom_fields if hasattr(data, "custom_fields") else None
                    ),
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
                        "end_time", "registration_deadline", "is_active", "custom_fields",
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
                custom_fields_input = update_data.pop("custom_fields", None)
                if custom_fields_input is not None:
                    competition.custom_fields = validate_custom_fields(custom_fields_input)
                for key, value in update_data.items():
                    setattr(competition, key, value)
                if tracks_input is not None:
                    # 赛道差量同步：按 id 更新原赛道（保留 competition_reg.track_id
                    # 关联与报名去重），仅删除真正被移除的赛道，新增无 id 的赛道。
                    existing = (
                        await db.execute(
                            select(CompetitionTrack).where(
                                CompetitionTrack.competition_id == competition_id
                            )
                        )
                    ).scalars().all()
                    existing_by_id = {t.id: t for t in existing}
                    kept_ids: set[int] = set()
                    for t in tracks_input:
                        track_id = t.get("id")
                        track_obj = existing_by_id.get(track_id) if track_id is not None else None
                        if track_obj is not None:
                            track_obj.name = t["name"]
                            track_obj.max_participants = t["max_participants"]
                            track_obj.sort_order = t["sort_order"]
                            kept_ids.add(track_obj.id)
                        else:
                            db.add(
                                CompetitionTrack(
                                    competition_id=competition_id,
                                    name=t["name"],
                                    max_participants=t["max_participants"],
                                    sort_order=t["sort_order"],
                                )
                            )
                    for track_obj in existing:
                        if track_obj.id not in kept_ids:
                            await db.delete(track_obj)
                    await db.flush()
                # Use flush instead of refresh — refresh re-reads from the DB
                # and can silently discard uncommitted in-memory changes in
                # certain async session configurations.
                await db.flush()
            tracks = await _track_briefs(db, competition.id)
            return AdminCompetitionListItem(
                **{
                    k: getattr(competition, k)
                    for k in (
                        "id", "name", "description", "cover_url", "start_time",
                        "end_time", "registration_deadline", "is_active", "custom_fields",
                        "created_at",
                    )
                },
                tracks=tracks,
                total_enrolled=sum(t.enrolled for t in tracks),
            )

    async def delete(self, competition_id: int) -> None:
        """Delete a competition, its tracks, and all associated registrations."""
        async with get_db_ctx() as db:
            async with db.begin():
                competition = await db.get(Competition, competition_id)
                if competition is None:
                    raise NotFoundException("赛事")

                # Get all track IDs for this competition
                track_ids = (
                    await db.execute(
                        select(CompetitionTrack.id).where(
                            CompetitionTrack.competition_id == competition_id
                        )
                    )
                ).scalars().all()

                # Delete registrations pointing to those tracks
                if track_ids:
                    regs = (
                        await db.execute(
                            select(CompetitionReg).where(
                                CompetitionReg.track_id.in_(track_ids)
                            )
                        )
                    ).scalars().all()
                    for reg in regs:
                        await db.delete(reg)

                # Delete the competition (tracks cascade via FK)
                await db.delete(competition)

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
