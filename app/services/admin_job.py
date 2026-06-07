from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException
from app.domain.certification.src.index import Job
from app.schemas.admin_job import AdminJobCreate, AdminJobListItem, AdminJobUpdate
from app.schemas.common import PaginatedData


class AdminJobService:

    async def list_jobs(
        self, keyword: str | None, page: int, page_size: int
    ) -> PaginatedData[AdminJobListItem]:
        async with get_db_ctx() as db:
            base = select(Job)
            if keyword:
                base = base.where(Job.title.ilike(f"%{keyword}%"))
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = base.order_by(Job.id.desc()).offset(
                (page - 1) * page_size
            ).limit(page_size)
            result = await db.execute(stmt)
            jobs = result.scalars().all()
            return PaginatedData[AdminJobListItem](
                items=[AdminJobListItem.model_validate(j) for j in jobs],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def create(self, data: AdminJobCreate) -> AdminJobListItem:
        async with get_db_ctx() as db:
            job = Job(**data.model_dump())
            db.add(job)
            await db.commit()
            await db.refresh(job)
            return AdminJobListItem.model_validate(job)

    async def update(self, job_id: int, data: AdminJobUpdate) -> AdminJobListItem:
        async with get_db_ctx() as db:
            job = await db.get(Job, job_id)
            if job is None:
                raise NotFoundException("岗位")
            update_data = data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(job, key, value)
            await db.commit()
            await db.refresh(job)
            return AdminJobListItem.model_validate(job)

    async def deactivate(self, job_id: int) -> None:
        async with get_db_ctx() as db:
            job = await db.get(Job, job_id)
            if job is None:
                raise NotFoundException("岗位")
            job.is_active = False
            await db.commit()
