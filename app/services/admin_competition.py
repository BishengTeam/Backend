import csv
import io

from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import CompetitionReg


class AdminCompetitionService:

    async def export_csv(self) -> str:
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
