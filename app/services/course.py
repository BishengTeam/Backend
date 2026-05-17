from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.core.database import get_db_ctx
from app.core.exceptions import BusinessException, NotFoundException
from app.models.course import Course, CourseEnrollment
from app.schemas.common import PaginatedData
from app.schemas.course import (
    CourseDetailResponse,
    CourseEnrollRequest,
    CourseEnrollmentResponse,
    CourseFilter,
    CourseListResponse,
)


class CourseService:

    async def list_courses(
        self, filters: CourseFilter | None = None, page: int = 1, page_size: int = 20
    ) -> PaginatedData[CourseListResponse]:
        async with get_db_ctx() as db:
            base = select(Course).where(Course.is_active == True)
            if filters and filters.category:
                base = base.where(Course.category == filters.category)
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = base.order_by(Course.id).offset((page - 1) * page_size).limit(page_size)
            result = await db.execute(stmt)
            courses = result.scalars().all()
            return PaginatedData[CourseListResponse](
                items=[CourseListResponse.model_validate(c) for c in courses],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def get_course(self, course_id: int) -> CourseDetailResponse:
        async with get_db_ctx() as db:
            course = await db.get(Course, course_id)
            if course is None or not course.is_active:
                raise NotFoundException("课程")
            return CourseDetailResponse.model_validate(course)

    async def enroll(self, user_id: int, data: CourseEnrollRequest) -> CourseEnrollmentResponse:
        async with get_db_ctx() as db:
            course = await db.get(Course, data.course_id)
            if course is None or not course.is_active:
                raise NotFoundException("课程")
            existing = (
                await db.execute(
                    select(CourseEnrollment).where(
                        CourseEnrollment.user_id == user_id,
                        CourseEnrollment.course_id == data.course_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise BusinessException("已报名该课程")
            enrollment = CourseEnrollment(
                user_id=user_id,
                course_id=data.course_id,
                batch_selected=data.batch,
            )
            db.add(enrollment)
            await db.commit()
            await db.refresh(enrollment)
            stmt = (
                select(CourseEnrollment)
                .options(joinedload(CourseEnrollment.course))
                .where(CourseEnrollment.id == enrollment.id)
            )
            result = await db.execute(stmt)
            enrollment = result.scalar_one()
            return CourseEnrollmentResponse.model_validate(enrollment)

    async def my_courses(
        self, user_id: int, page: int = 1, page_size: int = 20
    ) -> PaginatedData[CourseEnrollmentResponse]:
        async with get_db_ctx() as db:
            base = (
                select(CourseEnrollment)
                .options(joinedload(CourseEnrollment.course))
                .where(CourseEnrollment.user_id == user_id)
            )
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = base.order_by(CourseEnrollment.id.desc()).offset(
                (page - 1) * page_size
            ).limit(page_size)
            result = await db.execute(stmt)
            enrollments = result.scalars().all()
            return PaginatedData[CourseEnrollmentResponse](
                items=[CourseEnrollmentResponse.model_validate(e) for e in enrollments],
                total=total,
                page=page,
                page_size=page_size,
            )
