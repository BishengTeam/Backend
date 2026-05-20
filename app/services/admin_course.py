from sqlalchemy import func, select

from app.core.database import get_db_ctx
from app.core.exceptions import NotFoundException
from app.models.course import Course
from app.schemas.admin_course import AdminCourseCreate, AdminCourseListItem, AdminCourseUpdate
from app.schemas.common import PaginatedData


class AdminCourseService:

    _list_columns = (
        Course.id,
        Course.title,
        Course.category,
        Course.description,
        Course.cover_url,
        Course.price,
        Course.teacher_name,
        Course.is_active,
        Course.created_at,
    )

    async def list_courses(
        self, page: int, page_size: int
    ) -> PaginatedData[AdminCourseListItem]:
        async with get_db_ctx() as db:
            base = select(*self._list_columns)
            count_stmt = select(func.count()).select_from(Course)
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = base.order_by(Course.id.desc()).offset((page - 1) * page_size).limit(page_size)
            result = await db.execute(stmt)
            courses = result.all()
            return PaginatedData[AdminCourseListItem](
                items=[AdminCourseListItem.model_validate(c) for c in courses],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def create_course(self, data: AdminCourseCreate) -> AdminCourseListItem:
        async with get_db_ctx() as db:
            course = Course(**data.model_dump())
            db.add(course)
            await db.commit()
            await db.refresh(course)
            return AdminCourseListItem.model_validate(course)

    async def update_course(self, course_id: int, data: AdminCourseUpdate) -> AdminCourseListItem:
        async with get_db_ctx() as db:
            course = await db.get(Course, course_id)
            if course is None:
                raise NotFoundException("课程")
            update_data = data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(course, key, value)
            await db.commit()
            await db.refresh(course)
            return AdminCourseListItem.model_validate(course)

    async def deactivate_course(self, course_id: int) -> None:
        async with get_db_ctx() as db:
            course = await db.get(Course, course_id)
            if course is None:
                raise NotFoundException("课程")
            course.is_active = False
            await db.commit()
