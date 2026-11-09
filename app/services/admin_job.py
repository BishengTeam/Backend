from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException
from app.domain.certification.src.index import Job
from app.schemas.admin_job import AdminJobCreate, AdminJobListItem, AdminJobUpdate
from app.schemas.common import PaginatedData
from app.services.recycle_bin import restore_row, soft_delete_row


class AdminJobService:

    async def list_jobs(
        self, keyword: str | None, page: int, page_size: int
    ) -> PaginatedData[AdminJobListItem]:
        async with get_db_ctx() as db:
            base = select(Job).where(Job.deleted_at.is_(None))
            if keyword:
                base = base.where(Job.title.ilike(f"%{keyword}%") | Job.company.ilike(f"%{keyword}%"))
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
            if job is None or job.deleted_at is not None:
                raise NotFoundException("岗位")
            update_data = data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(job, key, value)
            await db.commit()
            await db.refresh(job)
            return AdminJobListItem.model_validate(job)

    async def delete(self, job_id: int) -> None:
        """软删除进回收站，30 自然日内可恢复。投递记录（学生数据）保留不动。"""
        async with get_db_ctx() as db:
            job = await db.get(Job, job_id)
            if job is None or job.deleted_at is not None:
                raise NotFoundException("岗位")
            soft_delete_row(job)
            await db.commit()

    async def restore(self, job_id: int) -> None:
        """从回收站恢复。"""
        async with get_db_ctx() as db:
            job = await db.get(Job, job_id)
            if job is None or job.deleted_at is None:
                raise NotFoundException("岗位")
            restore_row(job)
            await db.commit()
