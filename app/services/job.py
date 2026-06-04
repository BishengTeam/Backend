from sqlalchemy import select, func

from app.adapter.database import get_db_ctx
from app.port.exceptions import ConflictException, NotFoundException
from app.models.job import Job, JobApplication
from app.schemas.common import PaginatedData
from app.schemas.job import JobResponse


class JobService:

    async def list_jobs(self, page: int, page_size: int) -> PaginatedData[JobResponse]:
        async with get_db_ctx() as db:
            count_stmt = select(func.count()).select_from(Job).where(Job.is_active == True)
            total = (await db.execute(count_stmt)).scalar() or 0

            stmt = (
                select(Job)
                .where(Job.is_active == True)
                .order_by(Job.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            result = await db.execute(stmt)
            jobs = result.scalars().all()

            items = [JobResponse.model_validate(j) for j in jobs]
            return PaginatedData(items=items, total=total, page=page, page_size=page_size)

    async def apply(self, user_id: int, job_id: int) -> JobApplication:
        async with get_db_ctx() as db:
            job = await db.get(Job, job_id)
            if job is None or not job.is_active:
                raise NotFoundException("岗位")

            existing = (
                await db.execute(
                    select(JobApplication).where(
                        JobApplication.user_id == user_id,
                        JobApplication.job_id == job_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise ConflictException("已申请过该岗位")

            application = JobApplication(user_id=user_id, job_id=job_id)
            db.add(application)
            await db.commit()
            await db.refresh(application)
            return application
