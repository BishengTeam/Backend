import csv
import io

from sqlalchemy import select, func, distinct

from app.adapter.database import get_db_ctx
from app.models.competition import CompetitionReg
from app.schemas.competition import CompetitionSignupRequest


class CompetitionService:

    async def get_school_stats(self) -> list[dict]:
        async with get_db_ctx() as db:
            stmt = (
                select(
                    CompetitionReg.school,
                    func.count().label("count"),
                )
                .group_by(CompetitionReg.school)
                .order_by(func.count().desc())
            )
            result = await db.execute(stmt)
            rows = result.all()
            return [{"school": row.school, "count": row.count} for row in rows]

    async def get_tracks(self) -> list[str]:
        async with get_db_ctx() as db:
            stmt = (
                select(distinct(CompetitionReg.track))
                .where(CompetitionReg.track.isnot(None))
                .where(CompetitionReg.track != "")
                .order_by(CompetitionReg.track)
            )
            result = await db.execute(stmt)
            rows = result.all()
            return [row[0] for row in rows]

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

    async def signup(self, user_id: int, data: CompetitionSignupRequest) -> CompetitionReg:
        async with get_db_ctx() as db:
            reg = CompetitionReg(
                user_id=user_id,
                competition_name=data.competition_name,
                school=data.school,
                track=data.track,
            )
            db.add(reg)
            await db.commit()
            await db.refresh(reg)
            return reg
