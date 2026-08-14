from sqlalchemy import and_, func, select
from sqlalchemy.orm import joinedload

from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException
from app.domain.certification.src.index import (
    Course,
    CourseAsset,
    CourseChapter,
    CourseEnrollment,
    UserChapterProgress,
)
from app.domain.community.src.index import QuizCourseLibraryBinding, QuizLibrary
from app.schemas.common import PaginatedData
from app.schemas.course import (
    ChapterProgressResponse,
    ChapterProgressUpsert,
    ChapterResponse,
    CourseDetailResponse,
    CourseChaptersResponse,
    CourseAssetResponse,
    CourseContentResponse,
    CourseEnrollRequest,
    CourseEnrollmentResponse,
    CourseFilter,
    CourseListResponse,
    CourseQuizLibrarySummary,
)
from app.services.course_purchase import CoursePurchaseService


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

    async def get_course(self, course_id: int, user_id: int | None = None) -> CourseDetailResponse:
        async with get_db_ctx() as db:
            stmt = (
                select(Course)
                .options(joinedload(Course.chapters))
                .where(Course.id == course_id)
            )
            result = await db.execute(stmt)
            course = result.unique().scalar_one_or_none()
            if course is None:
                raise NotFoundException("课程")

            enrollment = None
            if user_id is not None:
                enrollment = await self._get_learning_enrollment(db, user_id, course_id)
            if not course.is_active and enrollment is None:
                raise NotFoundException("课程")

            response = CourseDetailResponse.model_validate(course)

            bound_libraries = list(
                (
                    await db.execute(
                        select(QuizLibrary)
                        .join(
                            QuizCourseLibraryBinding,
                            QuizCourseLibraryBinding.library_id == QuizLibrary.id,
                        )
                        .where(
                            QuizCourseLibraryBinding.course_id == course_id,
                            QuizCourseLibraryBinding.status == "active",
                            QuizLibrary.access_mode == "course_entitlement",
                            QuizLibrary.status.in_(("draft", "published", "suspended")),
                        )
                        .order_by(QuizLibrary.sort_order.asc(), QuizLibrary.id.asc())
                    )
                ).scalars()
            )
            response.included_quiz_libraries = [
                CourseQuizLibrarySummary(
                    id=int(library.id),
                    library_code=library.library_code,
                    name=library.name,
                    description=library.description,
                    cover_url=library.cover_url,
                    status=library.status,
                    available=(
                        library.status == "published" and bool(library.v2_enabled)
                    ),
                )
                for library in bound_libraries
            ]

            # 过滤活跃章节并按 sort_order 排序
            active_chapters = [ch for ch in course.chapters if ch.is_active]
            active_chapters.sort(key=lambda ch: ch.sort_order)
            response.chapters = [ChapterResponse.model_validate(ch) for ch in active_chapters]

            if enrollment is not None:
                response.has_access = True
                response.enrollment_id = enrollment.id

            # 试看时长：已购买为 null，否则返回课程设置的试看时长
            if not response.has_access:
                response.free_preview_seconds = course.free_preview_seconds
            else:
                response.free_preview_seconds = None

            return response

    async def get_content(self, user_id: int, course_id: int) -> CourseContentResponse:
        async with get_db_ctx() as db:
            course = await db.get(Course, course_id)
            if course is None:
                raise NotFoundException("课程")
            has_access = await self._has_learning_access(db, user_id, course_id)
            if not course.is_active and not has_access:
                raise NotFoundException("课程")

            asset_filter = CourseAsset.course_id == course_id
            if not has_access:
                asset_filter = and_(asset_filter, CourseAsset.is_preview.is_(True))
            result = await db.execute(
                select(CourseAsset)
                .where(asset_filter)
                .order_by(CourseAsset.sort_order, CourseAsset.id)
            )
            assets = result.scalars().all()
            return CourseContentResponse(
                course_id=course.id,
                title=course.title,
                learning_access=has_access,
                assets=[
                    CourseAssetResponse(
                        id=asset.id,
                        course_id=asset.course_id,
                        title=asset.title,
                        asset_type=asset.asset_type,
                        sort_order=asset.sort_order,
                        is_preview=asset.is_preview,
                        content_url=f"/api/course-assets/{asset.id}/content",
                    )
                    for asset in assets
                ],
            )

    async def list_categories(self) -> list[str]:
        async with get_db_ctx() as db:
            result = await db.execute(
                select(Course.category)
                .where(Course.is_active == True)
                .distinct()
                .order_by(Course.category)
            )
            return [row[0] for row in result.all()]

    async def enroll(self, user_id: int, data: CourseEnrollRequest) -> CourseEnrollmentResponse:
        purchase = await CoursePurchaseService().purchase(
            user_id,
            data.course_id,
            batch=data.batch,
            allow_paid=False,
        )
        async with get_db_ctx() as db:
            stmt = (
                select(CourseEnrollment)
                .options(joinedload(CourseEnrollment.course))
                .where(CourseEnrollment.id == purchase.enrollment_id)
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

    # ==================== 章节与学习进度 ====================

    @staticmethod
    async def check_has_access(user_id: int, course_id: int) -> bool:
        """判断用户是否有课程学习权限。"""
        async with get_db_ctx() as db:
            return await CourseService._has_learning_access(db, user_id, course_id)

    async def get_chapters(
        self, course_id: int, user_id: int | None = None
    ) -> CourseChaptersResponse:
        """返回 { free_preview_seconds, chapters, progress }。"""
        async with get_db_ctx() as db:
            stmt = (
                select(Course)
                .options(joinedload(Course.chapters))
                .where(Course.id == course_id)
            )
            result = await db.execute(stmt)
            course = result.unique().scalar_one_or_none()
            if course is None:
                raise NotFoundException("课程")

            has_access = (
                await self._has_learning_access(db, user_id, course_id)
                if user_id is not None
                else False
            )
            if not course.is_active and not has_access:
                raise NotFoundException("课程")

            active_chapters = [ch for ch in course.chapters if ch.is_active]
            active_chapters.sort(key=lambda ch: ch.sort_order)

            free_seconds = None if has_access else course.free_preview_seconds

            progress = None
            if user_id is not None:
                progress = await self._build_progress(user_id, course_id)

            return CourseChaptersResponse(
                free_preview_seconds=free_seconds,
                chapters=[ChapterResponse.model_validate(ch) for ch in active_chapters],
                progress=progress,
            )

    async def get_progress(
        self, user_id: int, course_id: int
    ) -> ChapterProgressResponse:
        return await self._build_progress(user_id, course_id)

    async def upsert_progress(
        self, user_id: int, course_id: int, data: ChapterProgressUpsert
    ) -> ChapterProgressResponse:
        async with get_db_ctx() as db:
            # 获取章节 duration
            chapter = await db.get(CourseChapter, data.chapter_id)
            if (
                chapter is None
                or chapter.course_id != course_id
                or not chapter.is_active
            ):
                raise NotFoundException("章节")

            # 5 秒容差自动判完成
            is_completed = data.is_completed or (
                chapter.duration is not None
                and data.last_position_seconds >= chapter.duration - 5
            )

            stmt = select(UserChapterProgress).where(
                UserChapterProgress.user_id == user_id,
                UserChapterProgress.course_id == course_id,
                UserChapterProgress.chapter_id == data.chapter_id,
            )
            result = await db.execute(stmt)
            progress = result.scalar_one_or_none()

            if progress is None:
                progress = UserChapterProgress(
                    user_id=user_id,
                    course_id=course_id,
                    chapter_id=data.chapter_id,
                    last_position_seconds=data.last_position_seconds,
                    is_completed=is_completed,
                )
                db.add(progress)
            else:
                progress.last_position_seconds = max(
                    progress.last_position_seconds, data.last_position_seconds
                )
                progress.is_completed = progress.is_completed or is_completed

            await db.commit()
            await db.refresh(progress)

            return await self._build_progress(user_id, course_id)

    @staticmethod
    async def _build_progress(user_id: int, course_id: int) -> ChapterProgressResponse:
        """构造学习进度响应。"""
        async with get_db_ctx() as db:
            rows = (
                await db.execute(
                    select(UserChapterProgress).where(
                        UserChapterProgress.user_id == user_id,
                        UserChapterProgress.course_id == course_id,
                    ).order_by(UserChapterProgress.updated_at.desc())
                )
            ).scalars().all()

            if not rows:
                return ChapterProgressResponse()

            completed_ids = [r.chapter_id for r in rows if r.is_completed]
            latest = max(rows, key=lambda r: r.updated_at)
            return ChapterProgressResponse(
                last_chapter_id=latest.chapter_id,
                last_position_seconds=latest.last_position_seconds,
                completed_chapter_ids=completed_ids,
            )

    @staticmethod
    async def _get_learning_enrollment(db, user_id: int, course_id: int):
        return (
            await db.execute(
                select(CourseEnrollment).where(
                    CourseEnrollment.user_id == user_id,
                    CourseEnrollment.course_id == course_id,
                    CourseEnrollment.learning_access.is_(True),
                    CourseEnrollment.status.in_(("enrolled", "completed")),
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def _has_learning_access(db, user_id: int, course_id: int) -> bool:
        enrollment = await CourseService._get_learning_enrollment(db, user_id, course_id)
        return enrollment is not None
