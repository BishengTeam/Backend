import csv
import io

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.services.recycle_bin import restore_row, soft_delete_row
from app.domain.certification.src.index import (
    Competition,
    CompetitionReg,
    CompetitionTrack,
)
from app.domain.community.src.index import QuizLibrary
from app.port.exceptions import BusinessException, NotFoundException
from app.schemas.admin_competition import (
    AdminCompetitionCreate,
    AdminCompetitionListItem,
    AdminCompetitionRegistrationItem,
    AdminCompetitionSchoolStatItem,
    AdminCompetitionUpdate,
)
from app.schemas.common import PaginatedData
from app.services.competition import _track_briefs
from app.schemas.competition_form import validate_custom_fields
from app.utils.excel import export_csv


# 列表/创建/更新响应共用的竞赛表字段（不含关联子表）
_ITEM_FIELDS = (
    "id", "name", "description", "cover_url", "poster_url", "start_time",
    "end_time", "registration_deadline", "is_active",
    "show_schedule", "show_signup", "custom_fields",
    "attachments", "created_at", "quiz_library_id",
)


async def _validate_quiz_library(db, library_id: int | None) -> None:
    """绑定的练习题库必须存在且未进归档/回收站。"""
    if library_id is None:
        return
    library = await db.get(QuizLibrary, library_id)
    if library is None or getattr(library, "status", None) in ("archived", "deleted"):
        raise BusinessException("题库不存在或不可绑定")


async def _quiz_library_name(db, library_id: int | None) -> str | None:
    if library_id is None:
        return None
    library = await db.get(QuizLibrary, library_id)
    return library.name if library is not None else None


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
            base = select(Competition).where(Competition.deleted_at.is_(None))
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
            # 批量取绑定题库名称，避免逐行查询
            library_ids = {
                c.quiz_library_id for c in rows if c.quiz_library_id is not None
            }
            library_names: dict[int, str] = {}
            if library_ids:
                libs = (
                    await db.execute(
                        select(QuizLibrary).where(QuizLibrary.id.in_(library_ids))
                    )
                ).scalars().all()
                library_names = {lib.id: lib.name for lib in libs}
            items: list[AdminCompetitionListItem] = []
            for c in rows:
                tracks = await _track_briefs(db, c.id)
                items.append(
                    AdminCompetitionListItem(
                        **{
                            k: getattr(c, k)
                            for k in _ITEM_FIELDS
                        },
                        tracks=tracks,
                        total_enrolled=sum(t.enrolled for t in tracks),
                        quiz_library_name=library_names.get(c.quiz_library_id),
                    )
                )
            return PaginatedData(
                items=items, total=total, page=page, page_size=page_size
            )

    async def create(self, data: AdminCompetitionCreate) -> AdminCompetitionListItem:
        async with get_db_ctx() as db:
            async with db.begin():
                await _validate_quiz_library(db, data.quiz_library_id)
                competition = Competition(
                    name=data.name,
                    description=data.description,
                    cover_url=data.cover_url,
                    poster_url=data.poster_url,
                    start_time=data.start_time,
                    end_time=data.end_time,
                    registration_deadline=data.registration_deadline,
                    is_active=data.is_active,
                    show_schedule=data.show_schedule,
                    show_signup=data.show_signup,
                    custom_fields=validate_custom_fields(
                        data.custom_fields if hasattr(data, "custom_fields") else None
                    ),
                    attachments=[
                        a.model_dump() for a in data.attachments
                    ] if data.attachments is not None else [],
                    quiz_library_id=data.quiz_library_id,
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
                    for k in _ITEM_FIELDS
                },
                tracks=tracks,
                total_enrolled=0,
                quiz_library_name=await _quiz_library_name(
                    db, competition.quiz_library_id
                ),
            )

    async def update(
        self, competition_id: int, data: AdminCompetitionUpdate
    ) -> AdminCompetitionListItem:
        async with get_db_ctx() as db:
            async with db.begin():
                competition = await db.get(Competition, competition_id)
                # getattr 兜底：旧单测用 SimpleNamespace 构造竞赛对象，无软删字段
                if competition is None or getattr(competition, "deleted_at", None) is not None:
                    raise NotFoundException("赛事")
                update_data = data.model_dump(exclude_unset=True)
                tracks_input = update_data.pop("tracks", None)
                custom_fields_input = update_data.pop("custom_fields", None)
                if custom_fields_input is not None:
                    competition.custom_fields = validate_custom_fields(custom_fields_input)
                if "quiz_library_id" in update_data:
                    await _validate_quiz_library(db, update_data["quiz_library_id"])
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
                    for k in _ITEM_FIELDS
                },
                tracks=tracks,
                total_enrolled=sum(t.enrolled for t in tracks),
                quiz_library_name=await _quiz_library_name(
                    db, competition.quiz_library_id
                ),
            )

    async def delete(self, competition_id: int) -> None:
        """软删除进回收站，30 自然日内可恢复。报名记录（学生数据）保留不动。"""
        async with get_db_ctx() as db:
            competition = await db.get(Competition, competition_id)
            if competition is None or competition.deleted_at is not None:
                raise NotFoundException("赛事")
            soft_delete_row(competition)
            await db.commit()

    async def restore(self, competition_id: int) -> None:
        """从回收站恢复。"""
        async with get_db_ctx() as db:
            competition = await db.get(Competition, competition_id)
            if competition is None or competition.deleted_at is None:
                raise NotFoundException("赛事")
            restore_row(competition)
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

    async def school_stats(
        self,
        competition_id: int,
        track_id: int | None,
    ) -> list[AdminCompetitionSchoolStatItem]:
        """按学校聚合赛事报名人数（可按赛道过滤），人数降序。"""
        async with get_db_ctx() as db:
            competition = await db.get(Competition, competition_id)
            if competition is None:
                raise NotFoundException("赛事")
            base = (
                select(
                    CompetitionReg.school,
                    func.count().label("registration_count"),
                )
                .join(
                    CompetitionTrack,
                    CompetitionReg.track_id == CompetitionTrack.id,
                )
                .where(CompetitionTrack.competition_id == competition_id)
            )
            if track_id is not None:
                base = base.where(CompetitionReg.track_id == track_id)
            rows = (
                await db.execute(
                    base.group_by(CompetitionReg.school)
                    .order_by(func.count().desc(), CompetitionReg.school.asc())
                )
            ).all()
            return [
                AdminCompetitionSchoolStatItem(
                    school=row.school or "", count=int(row.registration_count)
                )
                for row in rows
            ]
