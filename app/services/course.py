from decimal import Decimal
from time import time

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import (
    Course,
    CourseCategory,
    CourseChapter,
    CourseEnrollment,
    UserChapterProgress,
)
from app.domain.community.src.index import QuizCourseLibraryBinding, QuizLibrary
from app.domain.user.src.index import User
from app.port.exceptions import ForbiddenException, NotFoundException, ThirdPartyException
from app.schemas.common import PaginatedData
from app.schemas.course import (
    ChapterPlaybackResponse,
    ChapterProgressResponse,
    ChapterProgressUpsert,
    ChapterResponse,
    CourseChaptersResponse,
    CourseDetailResponse,
    CourseEnrollRequest,
    CourseEnrollmentResponse,
    CourseFilter,
    CourseListResponse,
    CourseQuizLibrarySummary,
    CourseQuizModuleSummary,
)
from app.services.course_entitlement import CourseEntitlementService
from app.services.course_purchase import CoursePurchaseService
from app.services.course_storage import CourseStorage


class CourseService:
    def __init__(self, storage: CourseStorage | None = None) -> None:
        self.storage = storage or CourseStorage()

    @staticmethod
    def _price_yuan(price: int) -> Decimal:
        return (Decimal(price) / Decimal(100)).quantize(Decimal("0.01"))

    async def course_list_response(self, course: Course) -> CourseListResponse:
        return CourseListResponse(
            id=course.id,
            title=course.title,
            category=course.category,
            description=course.description,
            cover_url=await self.storage.signed_url(course.cover_storage_key),
            price=course.price,
            price_yuan=self._price_yuan(course.price),
            teacher_name=course.teacher_name,
        )

    @staticmethod
    def _chapter_response(
        chapter: CourseChapter, *, can_play_course: bool, has_access: bool, preview_count: int
    ) -> ChapterResponse:
        is_preview = can_play_course or chapter.sort_order <= preview_count
        return ChapterResponse(
            id=chapter.id,
            title=chapter.title,
            duration=chapter.duration,
            sort_order=chapter.sort_order,
            is_preview=is_preview and not has_access,
            can_play=is_preview,
        )

    async def list_courses(
        self, filters: CourseFilter | None = None, page: int = 1, page_size: int = 20
    ) -> PaginatedData[CourseListResponse]:
        async with get_db_ctx() as db:
            base = select(Course).where(Course.status == "published")
            if filters and filters.category:
                base = base.where(Course.category == filters.category)
            total = int(
                await db.scalar(select(func.count()).select_from(base.subquery())) or 0
            )
            rows = (
                await db.execute(
                    base.order_by(Course.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return PaginatedData(
                items=[await self.course_list_response(row) for row in rows],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def list_categories(self) -> list[str]:
        async with get_db_ctx() as db:
            return list(
                await db.scalars(
                    select(CourseCategory.name)
                    .where(CourseCategory.is_active.is_(True))
                    .order_by(CourseCategory.sort_order, CourseCategory.id)
                )
            )

    async def get_course(
        self, course_id: int, user_id: int | None = None
    ) -> CourseDetailResponse:
        async with get_db_ctx() as db:
            course = await db.get(Course, course_id)
            if course is None:
                raise NotFoundException("课程")
            enrollment = (
                await self._get_learning_enrollment(db, user_id, course_id)
                if user_id is not None
                else None
            )
            if not course.is_active and enrollment is None:
                raise NotFoundException("课程")
            chapters = list(
                await db.scalars(
                    select(CourseChapter)
                    .where(CourseChapter.course_id == course_id)
                    .order_by(CourseChapter.sort_order, CourseChapter.id)
                )
            )
            has_access = enrollment is not None
            can_play_course = has_access or course.price == 0
            bound_libraries = list(
                await db.scalars(
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
                    .order_by(QuizLibrary.sort_order, QuizLibrary.id)
                )
            )
            modules_by_library = await CourseEntitlementService.list_library_modules(
                db, [int(library.id) for library in bound_libraries]
            )
            return CourseDetailResponse(
                id=course.id,
                title=course.title,
                category=course.category,
                description=course.description,
                cover_url=await self.storage.signed_url(course.cover_storage_key),
                price=course.price,
                price_yuan=self._price_yuan(course.price),
                teacher_name=course.teacher_name,
                status=course.status,
                has_access=has_access,
                enrollment_id=enrollment.id if enrollment else None,
                preview_chapter_count=(
                    0 if course.price == 0 else course.preview_chapter_count
                ),
                chapter_count=len(chapters),
                chapters=[
                    self._chapter_response(
                        chapter,
                        can_play_course=can_play_course,
                        has_access=has_access,
                        preview_count=course.preview_chapter_count,
                    )
                    for chapter in chapters
                ],
                included_quiz_libraries=[
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
                        modules=[
                            CourseQuizModuleSummary(
                                id=int(module.id),
                                name=module.name,
                                description=module.description,
                                status=module.status,
                            )
                            for module in modules_by_library.get(int(library.id), [])
                        ],
                    )
                    for library in bound_libraries
                ],
            )

    async def enroll(self, user_id: int, data: CourseEnrollRequest):
        purchase = await CoursePurchaseService().purchase(
            user_id, data.course_id, allow_paid=False
        )
        async with get_db_ctx() as db:
            enrollment = (
                await db.execute(
                    select(CourseEnrollment)
                    .options(joinedload(CourseEnrollment.course))
                    .where(CourseEnrollment.id == purchase.enrollment_id)
                )
            ).unique().scalar_one()
            return await self._enrollment_response(enrollment)

    async def my_courses(
        self, user_id: int, page: int = 1, page_size: int = 20
    ) -> PaginatedData[CourseEnrollmentResponse]:
        async with get_db_ctx() as db:
            base = (
                select(CourseEnrollment)
                .options(joinedload(CourseEnrollment.course))
                .where(CourseEnrollment.user_id == user_id)
            )
            total = int(
                await db.scalar(select(func.count()).select_from(base.subquery())) or 0
            )
            rows = (
                await db.execute(
                    base.order_by(CourseEnrollment.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).unique().scalars().all()
            return PaginatedData(
                items=[await self._enrollment_response(row) for row in rows],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def _enrollment_response(
        self, enrollment: CourseEnrollment
    ) -> CourseEnrollmentResponse:
        return CourseEnrollmentResponse(
            id=enrollment.id,
            course=await self.course_list_response(enrollment.course),
            order_id=enrollment.order_id,
            status=enrollment.status,
            learning_access=enrollment.learning_access,
            access_granted_at=enrollment.access_granted_at,
            access_revoked_at=enrollment.access_revoked_at,
            created_at=enrollment.created_at,
        )

    async def get_chapters(
        self, course_id: int, user_id: int | None
    ) -> CourseChaptersResponse:
        async with get_db_ctx() as db:
            course = await db.get(Course, course_id)
            if course is None:
                raise NotFoundException("课程")
            has_access = (
                await self._has_learning_access(db, user_id, course_id)
                if user_id is not None
                else False
            )
            if not course.is_active and not has_access:
                raise NotFoundException("课程")
            rows = list(
                await db.scalars(
                    select(CourseChapter)
                    .where(CourseChapter.course_id == course_id)
                    .order_by(CourseChapter.sort_order, CourseChapter.id)
                )
            )
            can_play_course = has_access or course.price == 0
            progress = await self._build_progress(db, user_id, course_id) if user_id else None
            return CourseChaptersResponse(
                preview_chapter_count=(
                    0 if course.price == 0 else course.preview_chapter_count
                ),
                chapters=[
                    self._chapter_response(
                        row,
                        can_play_course=can_play_course,
                        has_access=has_access,
                        preview_count=course.preview_chapter_count,
                    )
                    for row in rows
                ],
                progress=progress,
            )

    async def issue_playback(
        self, user_id: int, course_id: int, chapter_id: int
    ) -> ChapterPlaybackResponse:
        async with get_db_ctx() as db:
            user = await db.get(User, user_id)
            if user is None or not user.is_active:
                raise ForbiddenException("用户账号不可用")
            course = await db.get(Course, course_id)
            chapter = await db.get(CourseChapter, chapter_id)
            if course is None or chapter is None or chapter.course_id != course_id:
                raise NotFoundException("课程章节")
            has_access = await self._has_learning_access(db, user_id, course_id)
            can_play = (
                has_access
                or course.price == 0
                or chapter.sort_order <= course.preview_chapter_count
            )
            if not can_play:
                raise ForbiddenException("无课程学习权限")
            if not await self.storage.object_exists(chapter.video_storage_key):
                raise ThirdPartyException("课程视频文件不存在，请重新上传视频")
            from app.domain.certification.src.index import CourseAuditLog

            db.add(
                CourseAuditLog(
                    actor_type="user",
                    actor_id=user_id,
                    action="course.chapter.playback_issued",
                    object_type="course_chapter",
                    object_id=chapter.id,
                    result="succeeded",
                    summary={"course_id": course_id, "preview": not has_access},
                )
            )
            await db.commit()
        expires_at = int(time()) + 300
        return ChapterPlaybackResponse(
            chapter_id=chapter_id,
            url=await self.storage.signed_url(
                chapter.video_storage_key,
                download_filename=chapter.original_filename,
            ),
            expires_at=expires_at,
        )

    async def get_progress(
        self, user_id: int, course_id: int
    ) -> ChapterProgressResponse:
        async with get_db_ctx() as db:
            return await self._build_progress(db, user_id, course_id)

    async def upsert_progress(
        self, user_id: int, course_id: int, data: ChapterProgressUpsert
    ) -> ChapterProgressResponse:
        async with get_db_ctx() as db:
            chapter = await db.get(CourseChapter, data.chapter_id)
            if chapter is None or chapter.course_id != course_id:
                raise NotFoundException("课程章节")
            row = (
                await db.execute(
                    select(UserChapterProgress).where(
                        UserChapterProgress.user_id == user_id,
                        UserChapterProgress.course_id == course_id,
                        UserChapterProgress.chapter_id == data.chapter_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = UserChapterProgress(
                    user_id=user_id,
                    course_id=course_id,
                    chapter_id=data.chapter_id,
                    last_position_seconds=data.last_position_seconds,
                    is_completed=data.is_completed,
                )
                db.add(row)
            else:
                row.last_position_seconds = max(
                    row.last_position_seconds, data.last_position_seconds
                )
                row.is_completed = row.is_completed or data.is_completed
            await db.commit()
            return await self._build_progress(db, user_id, course_id)

    async def _build_progress(
        self, db, user_id: int, course_id: int
    ) -> ChapterProgressResponse:
        rows = (
            await db.execute(
                select(UserChapterProgress).where(
                    UserChapterProgress.user_id == user_id,
                    UserChapterProgress.course_id == course_id,
                )
            )
        ).scalars().all()
        last = max(rows, key=lambda item: item.updated_at, default=None)
        return ChapterProgressResponse(
            last_chapter_id=last.chapter_id if last else None,
            last_position_seconds=last.last_position_seconds if last else 0,
            completed_chapter_ids=[row.chapter_id for row in rows if row.is_completed],
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

    @classmethod
    async def _has_learning_access(cls, db, user_id: int, course_id: int) -> bool:
        return (
            await cls._get_learning_enrollment(db, user_id, course_id) is not None
        )
