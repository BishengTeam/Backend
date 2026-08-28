from sqlalchemy import select, func

from app.adapter.database import get_db_ctx
from app.port.exceptions import ConflictException, NotFoundException
from app.domain.certification.src.index import Job, JobApplication
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
        """已弃用：客户端投递功能已下线，保留仅为兼容存量数据读取。

        2026-08 运营模块重构决定：招聘只做岗位录入，求职者通过岗位
        联系方式自行联系企业。本方法不再有任何调用方，后续版本随
        job_application 表一并清理。
        """
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
