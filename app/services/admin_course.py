from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import delete, func, select

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import (
    Course,
    CourseAuditLog,
    CourseCategory,
    CourseChapter,
    CourseEnrollment,
    CourseEntitlementJob,
    CourseEntitlementJobItem,
    CourseUpload,
)
from app.domain.community.src.index import (
    QuizCourseLibraryBinding,
    QuizLibraryEntitlement,
    QuizLibrary,
)
from app.domain.order.src.index import Order
from app.domain.order.src.transition.order_transitions import apply_order_status_transition
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.schemas.admin_course import (
    AdminCourseAuditListItem,
    AdminChapterResponse,
    AdminCourseBindingResponse,
    AdminCourseCategoryCreate,
    AdminCourseCategoryResponse,
    AdminCourseCategoryUpdate,
    AdminCourseCreate,
    AdminCourseEnrollmentListItem,
    AdminCourseEntitlementJobResponse,
    AdminCourseListItem,
    AdminCourseUpdate,
    AdminChapterUpdate,
)
from app.schemas.common import PaginatedData
from app.schemas.course_upload import CourseUploadCreate
from app.services.course_entitlement import CourseEntitlementService
from app.services.course_storage import CourseStorage
from app.services.course_upload import CourseUploadService
from app.services.order_fulfillment import OrderFulfillmentService
from app.utils.audit import sanitize_audit_value


COURSE_LIFECYCLE_TARGETS = {
    "publish": {"draft"},
    "offline": {"published"},
    "archive": {"published", "offline"},
    "restore": {"offline", "archived"},
}


class AdminCourseService:
    def __init__(self, storage: CourseStorage | None = None) -> None:
        self.storage = storage or CourseStorage()
        self.uploads = CourseUploadService(storage=self.storage)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _audit_value(value: object) -> object:
        if isinstance(value, datetime):
            return value.isoformat()
        return sanitize_audit_value(value)

    @classmethod
    def _audit(
        cls,
        db,
        *,
        admin_id: int,
        action: str,
        object_type: str,
        object_id: int | None,
        before: dict[str, object] | None = None,
        after: dict[str, object] | None = None,
        summary: dict[str, object] | None = None,
    ) -> None:
        changed_fields = None
        if before is not None and after is not None:
            changed_fields = {
                key: {
                    "before": cls._audit_value(before.get(key)),
                    "after": cls._audit_value(after.get(key)),
                }
                for key in after
                if before.get(key) != after.get(key)
            }
        db.add(
            CourseAuditLog(
                actor_type="admin",
                actor_id=admin_id,
                action=action,
                object_type=object_type,
                object_id=object_id,
                result="succeeded",
                changed_fields=changed_fields,
                summary=summary,
            )
        )

    @classmethod
    def _course_fields(cls, course: Course) -> dict[str, object]:
        return {
            "title": course.title,
            "category": course.category,
            "description": course.description,
            "cover_storage_key": course.cover_storage_key,
            "price": course.price,
            "teacher_name": course.teacher_name,
            "teacher_contact": course.teacher_contact,
            "preview_chapter_count": course.preview_chapter_count,
            "status": course.status,
        }

    @staticmethod
    async def _locked_course(db, course_id: int) -> Course:
        course = (
            await db.execute(select(Course).where(Course.id == course_id).with_for_update())
        ).scalar_one_or_none()
        if course is None:
            raise NotFoundException("课程")
        return course

    @staticmethod
    async def _ensure_category(db, name: str) -> None:
        exists = (
            await db.execute(
                select(CourseCategory.id).where(
                    CourseCategory.name == name, CourseCategory.is_active.is_(True)
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            raise BusinessException("课程类目不存在")

    async def list_categories(self) -> list[AdminCourseCategoryResponse]:
        async with get_db_ctx() as db:
            rows = (
                await db.execute(
                    select(CourseCategory).order_by(
                        CourseCategory.sort_order, CourseCategory.id
                    )
                )
            ).scalars().all()
            return [AdminCourseCategoryResponse.model_validate(row) for row in rows]

    async def create_category(
        self, data: AdminCourseCategoryCreate, *, admin_id: int
    ) -> AdminCourseCategoryResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                existing = (
                    await db.execute(
                        select(CourseCategory).where(CourseCategory.name == data.name)
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    raise ConflictException("课程类目已存在")
                row = CourseCategory(**data.model_dump())
                db.add(row)
                await db.flush()
                self._audit(
                    db,
                    admin_id=admin_id,
                    action="course.category.created",
                    object_type="course_category",
                    object_id=row.id,
                    after={"name": row.name, "sort_order": row.sort_order},
                )
                await db.refresh(row)
                return AdminCourseCategoryResponse.model_validate(row)

    async def update_category(
        self, category_id: int, data: AdminCourseCategoryUpdate, *, admin_id: int
    ) -> AdminCourseCategoryResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                row = (
                    await db.execute(
                        select(CourseCategory)
                        .where(CourseCategory.id == category_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if row is None:
                    raise NotFoundException("课程类目")
                before = {"name": row.name, "sort_order": row.sort_order, "is_active": row.is_active}
                for key, value in data.model_dump(exclude_unset=True).items():
                    setattr(row, key, value)
                await db.flush()
                after = {"name": row.name, "sort_order": row.sort_order, "is_active": row.is_active}
                self._audit(
                    db,
                    admin_id=admin_id,
                    action="course.category.updated",
                    object_type="course_category",
                    object_id=row.id,
                    before=before,
                    after=after,
                )
                await db.refresh(row)
                return AdminCourseCategoryResponse.model_validate(row)

    async def list_courses(
        self,
        *,
        keyword: str | None = None,
        category: str | None = None,
        status: str | None = None,
        price_type: str | None = None,
        bound_quiz: bool | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[AdminCourseListItem]:
        async with get_db_ctx() as db:
            base = select(Course)
            if keyword:
                base = base.where(Course.title.ilike(f"%{keyword}%"))
            if category:
                base = base.where(Course.category == category)
            if status:
                base = base.where(Course.status == status)
            if price_type == "free":
                base = base.where(Course.price == 0)
            elif price_type == "paid":
                base = base.where(Course.price > 0)
            if created_from:
                base = base.where(Course.created_at >= created_from)
            if created_to:
                base = base.where(Course.created_at <= created_to)
            bound_courses = select(QuizCourseLibraryBinding.course_id).where(
                QuizCourseLibraryBinding.status == "active"
            )
            if bound_quiz is True:
                base = base.where(Course.id.in_(bound_courses))
            elif bound_quiz is False:
                base = base.where(~Course.id.in_(bound_courses))

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
            items: list[AdminCourseListItem] = []
            for course in rows:
                bound_count = int(
                    await db.scalar(
                        select(func.count())
                        .select_from(QuizCourseLibraryBinding)
                        .where(
                            QuizCourseLibraryBinding.course_id == course.id,
                            QuizCourseLibraryBinding.status == "active",
                        )
                    )
                    or 0
                )
                enrollment_count = int(
                    await db.scalar(
                        select(func.count())
                        .select_from(CourseEnrollment)
                        .where(
                            CourseEnrollment.course_id == course.id,
                            CourseEnrollment.status.in_(("enrolled", "completed")),
                        )
                    )
                    or 0
                )
                items.append(
                    AdminCourseListItem(
                        id=course.id,
                        title=course.title,
                        category=course.category,
                        description=course.description,
                        cover_url=await self.storage.signed_url(course.cover_storage_key),
                        price=course.price,
                        price_yuan=(Decimal(course.price) / Decimal(100)).quantize(Decimal("0.01")),
                        teacher_name=course.teacher_name,
                        teacher_contact=course.teacher_contact,
                        preview_chapter_count=course.preview_chapter_count,
                        status=course.status,
                        bound_quiz_library_count=bound_count,
                        enrollment_count=enrollment_count,
                        created_at=course.created_at,
                        updated_at=course.updated_at,
                    )
                )
            return PaginatedData(items=items, total=total, page=page, page_size=page_size)

    async def create_course(
        self, data: AdminCourseCreate, *, admin_id: int
    ) -> AdminCourseListItem:
        async with get_db_ctx() as db:
            async with db.begin():
                await self._ensure_category(db, data.category)
                upload = await db.get(CourseUpload, data.cover_upload_id)
                if upload is None or upload.kind != "cover" or upload.status != "completed":
                    raise BusinessException("课程封面尚未上传完成")
                payload = data.model_dump(exclude={"cover_upload_id", "price_yuan"})
                course = Course(
                    **payload,
                    cover_storage_key=upload.object_key,
                    price=self._price_to_cents(data.price_yuan),
                    status="draft",
                    is_active=False,
                )
                db.add(course)
                await db.flush()
                upload.course_id = course.id
                upload.status = "bound"
                self._audit(
                    db,
                    admin_id=admin_id,
                    action="course.created",
                    object_type="course",
                    object_id=course.id,
                    after=self._course_fields(course),
                )
                await db.refresh(course)
                return await self._list_item(db, course)

    async def get_course(self, course_id: int) -> AdminCourseListItem:
        async with get_db_ctx() as db:
            course = await db.get(Course, course_id)
            if course is None:
                raise NotFoundException("课程")
            return await self._list_item(db, course)

    async def update_course(
        self, course_id: int, data: AdminCourseUpdate, *, admin_id: int
    ) -> AdminCourseListItem:
        async with get_db_ctx() as db:
            async with db.begin():
                course = await self._locked_course(db, course_id)
                before = self._course_fields(course)
                changes = data.model_dump(exclude_unset=True)
                replacement_cover_key = None
                if "category" in changes:
                    await self._ensure_category(db, changes["category"])
                if "cover_upload_id" in changes:
                    upload = await db.get(CourseUpload, changes.pop("cover_upload_id"))
                    if upload is None or upload.kind != "cover" or upload.status != "completed":
                        raise BusinessException("新课程封面尚未上传完成")
                    replacement_cover_key = course.cover_storage_key
                    course.cover_storage_key = upload.object_key
                    upload.status = "bound"
                    upload.course_id = course.id
                for key, value in changes.items():
                    setattr(course, key, value)
                await db.flush()
                self._audit(
                    db,
                    admin_id=admin_id,
                    action="course.updated",
                    object_type="course",
                    object_id=course.id,
                    before=before,
                    after=self._course_fields(course),
                )
                await db.refresh(course)
                return await self._list_item(db, course)
        if replacement_cover_key:
            await self.storage.delete(replacement_cover_key)

    @staticmethod
    def _price_to_cents(value: Decimal) -> int:
        return int((value * Decimal(100)).to_integral_exact())

    async def update_price(
        self,
        course_id: int,
        price_yuan: Decimal,
        *,
        admin_id: int,
    ) -> AdminCourseListItem:
        async with get_db_ctx() as db:
            async with db.begin():
                course = await self._locked_course(db, course_id)
                before = {"price": course.price}
                course.price = self._price_to_cents(price_yuan)
                await db.flush()
                self._audit(
                    db,
                    admin_id=admin_id,
                    action="course.price_changed",
                    object_type="course",
                    object_id=course.id,
                    before=before,
                    after={"price": course.price},
                )
                await db.refresh(course)
                return await self._list_item(db, course)

    async def _list_item(self, db, course: Course) -> AdminCourseListItem:
        bound_count = int(
            await db.scalar(
                select(func.count())
                .select_from(QuizCourseLibraryBinding)
                .where(
                    QuizCourseLibraryBinding.course_id == course.id,
                    QuizCourseLibraryBinding.status == "active",
                )
            )
            or 0
        )
        enrollment_count = int(
            await db.scalar(
                select(func.count())
                .select_from(CourseEnrollment)
                .where(
                    CourseEnrollment.course_id == course.id,
                    CourseEnrollment.status.in_(("enrolled", "completed")),
                )
            )
            or 0
        )
        return AdminCourseListItem(
            id=course.id,
            title=course.title,
            category=course.category,
            description=course.description,
            cover_url=await self.storage.signed_url(course.cover_storage_key),
            price=course.price,
            price_yuan=(Decimal(course.price) / Decimal(100)).quantize(Decimal("0.01")),
            teacher_name=course.teacher_name,
            teacher_contact=course.teacher_contact,
            preview_chapter_count=course.preview_chapter_count,
            status=course.status,
            bound_quiz_library_count=bound_count,
            enrollment_count=enrollment_count,
            created_at=course.created_at,
            updated_at=course.updated_at,
        )

    async def change_lifecycle(
        self, course_id: int, action: str, *, admin_id: int
    ) -> AdminCourseListItem:
        if action not in COURSE_LIFECYCLE_TARGETS:
            raise BusinessException("课程状态操作无效")
        async with get_db_ctx() as db:
            async with db.begin():
                course = await self._locked_course(db, course_id)
                if course.status not in COURSE_LIFECYCLE_TARGETS[action]:
                    raise BusinessException("当前课程状态不允许该操作")
                if action == "publish":
                    chapter_count = int(
                        await db.scalar(
                            select(func.count()).select_from(CourseChapter).where(
                                CourseChapter.course_id == course_id
                            )
                        )
                        or 0
                    )
                    if not course.cover_storage_key or chapter_count == 0:
                        raise BusinessException("课程发布前必须上传封面和至少一个章节视频")
                    if course.price > 0 and course.preview_chapter_count > chapter_count:
                        raise BusinessException("试看集数不能超过章节数")
                before = {"status": course.status}
                if action == "publish":
                    course.status = "published"
                    course.is_active = True
                elif action == "offline":
                    course.status = "offline"
                    course.is_active = False
                    await self._close_pending_enrollments(db, course.id)
                elif action == "archive":
                    course.status = "archived"
                    course.is_active = False
                    await self._close_pending_enrollments(db, course.id)
                else:
                    course.status = "published"
                    course.is_active = True
                await db.flush()
                action_name = {
                    "publish": "published",
                    "offline": "offlined",
                    "archive": "archived",
                    "restore": "restored",
                }[action]
                self._audit(
                    db,
                    admin_id=admin_id,
                    action=f"course.{action_name}",
                    object_type="course",
                    object_id=course.id,
                    before=before,
                    after={"status": course.status},
                )
                await db.refresh(course)
                return await self._list_item(db, course)

    async def _close_pending_enrollments(self, db, course_id: int) -> int:
        rows = list(
            (
                await db.execute(
                    select(CourseEnrollment, Order)
                    .join(Order, Order.id == CourseEnrollment.order_id)
                    .where(
                        CourseEnrollment.course_id == course_id,
                        CourseEnrollment.status == "pending_payment",
                        Order.status == "pending",
                    )
                    .with_for_update(of=CourseEnrollment)
                )
            ).all()
        )
        closed = 0
        for enrollment, order in rows:
            locked_order = (
                await db.execute(select(Order).where(Order.id == order.id).with_for_update())
            ).scalar_one()
            if locked_order.status != "pending":
                continue
            if not apply_order_status_transition(locked_order, "closed"):
                continue
            locked_order.closed_at = self._now()
            locked_order.close_reason = "course_offline"
            await OrderFulfillmentService().on_closed(db, locked_order)
            closed += 1
        return closed

    async def delete_course(self, course_id: int, *, admin_id: int) -> None:
        async with get_db_ctx() as db:
            async with db.begin():
                course = await self._locked_course(db, course_id)
                if course.status != "draft":
                    raise BusinessException("只有草稿课程可以删除")
                associated = int(
                    await db.scalar(
                        select(func.count()).select_from(CourseEnrollment).where(
                            CourseEnrollment.course_id == course_id
                        )
                    )
                    or 0
                ) + int(
                    await db.scalar(
                        select(func.count()).select_from(CourseChapter).where(
                            CourseChapter.course_id == course_id
                        )
                    )
                    or 0
                ) + int(
                    await db.scalar(
                        select(func.count()).select_from(QuizCourseLibraryBinding).where(
                            QuizCourseLibraryBinding.course_id == course_id
                        )
                    )
                    or 0
                )
                if associated:
                    raise BusinessException("课程已存在业务关联，只能归档")
                self._audit(
                    db,
                    admin_id=admin_id,
                    action="course.deleted",
                    object_type="course",
                    object_id=course.id,
                    after=self._course_fields(course),
                )
                await db.delete(course)

    async def list_chapters(
        self, course_id: int, is_active: bool | None, page: int, page_size: int
    ) -> PaginatedData[AdminChapterResponse]:
        async with get_db_ctx() as db:
            base = select(CourseChapter).where(CourseChapter.course_id == course_id)
            total = int(
                await db.scalar(select(func.count()).select_from(base.subquery())) or 0
            )
            rows = (
                await db.execute(
                    base.order_by(CourseChapter.sort_order, CourseChapter.id)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return PaginatedData(
                items=[AdminChapterResponse.model_validate(row) for row in rows],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def sort_chapters(
        self, course_id: int, items: list[ChapterSortItem], *, admin_id: int
    ) -> int:
        async with get_db_ctx() as db:
            async with db.begin():
                count = 0
                for item in items:
                    row = await db.get(CourseChapter, item.id)
                    if row is None or row.course_id != course_id:
                        continue
                    row.sort_order = item.sort_order
                    count += 1
                self._audit(
                    db,
                    admin_id=admin_id,
                    action="course.chapter.sorted",
                    object_type="course",
                    object_id=course_id,
                    summary={"count": count},
                )
                return count

    async def batch_create_chapters(
        self, course_id: int, upload_ids: list[int], *, admin_id: int
    ) -> list[AdminChapterResponse]:
        rows = await self.uploads.create_chapters(
            course_id, upload_ids, admin_id=admin_id
        )
        return [AdminChapterResponse.model_validate(row) for row in rows]

    async def update_chapter_metadata(
        self, course_id: int, chapter_id: int, data: AdminChapterUpdate, *, admin_id: int
    ) -> AdminChapterResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                row = await db.get(CourseChapter, chapter_id)
                if row is None or row.course_id != course_id:
                    raise NotFoundException("章节")
                before = {
                    "title": row.title,
                    "sort_order": row.sort_order,
                }
                for key, value in data.model_dump(exclude_unset=True).items():
                    setattr(row, key, value)
                await db.flush()
                self._audit(
                    db,
                    admin_id=admin_id,
                    action="course.chapter.updated",
                    object_type="course_chapter",
                    object_id=row.id,
                    before=before,
                    after={
                        "title": row.title,
                        "sort_order": row.sort_order,
                    },
                )
                await db.refresh(row)
                return AdminChapterResponse.model_validate(row)

    async def replace_chapter_video(
        self, course_id: int, chapter_id: int, upload_id: int, *, admin_id: int
    ) -> AdminChapterResponse:
        row = await self.uploads.replace_video(
            course_id, chapter_id, upload_id, admin_id=admin_id
        )
        return AdminChapterResponse.model_validate(row)

    async def delete_chapter_completely(
        self, course_id: int, chapter_id: int, *, admin_id: int, force: bool = False
    ) -> None:
        from app.domain.certification.src.index import UserChapterProgress

        async with get_db_ctx() as db:
            async with db.begin():
                row = await db.get(CourseChapter, chapter_id)
                if row is None or row.course_id != course_id:
                    raise NotFoundException("章节")
                progress_count = int(
                    await db.scalar(
                        select(func.count())
                        .select_from(UserChapterProgress)
                        .where(UserChapterProgress.chapter_id == chapter_id)
                    )
                    or 0
                )
                if progress_count and not force:
                    raise BusinessException("已有学习进度的章节不能删除，确认删除请传 force=true")
                if progress_count:
                    await db.execute(
                        delete(UserChapterProgress).where(
                            UserChapterProgress.chapter_id == chapter_id
                        )
                    )
                object_key = row.video_storage_key
                self._audit(
                    db,
                    admin_id=admin_id,
                    action="course.chapter.deleted",
                    object_type="course_chapter",
                    object_id=row.id,
                    after={"title": row.title},
                )
                await db.delete(row)
        await self.storage.delete(object_key)

    async def list_enrollments(
        self,
        *,
        course_id: int | None,
        user_id: int | None,
        status: str | None,
        page: int,
        page_size: int,
    ) -> PaginatedData[AdminCourseEnrollmentListItem]:
        async with get_db_ctx() as db:
            base = (
                select(
                    CourseEnrollment,
                    Course.title.label("course_title"),
                    Order.status.label("order_status"),
                    Order.price.label("order_price"),
                )
                .join(Course, Course.id == CourseEnrollment.course_id)
                .outerjoin(Order, Order.id == CourseEnrollment.order_id)
            )
            if course_id is not None:
                base = base.where(CourseEnrollment.course_id == course_id)
            if user_id is not None:
                base = base.where(CourseEnrollment.user_id == user_id)
            if status is not None:
                base = base.where(CourseEnrollment.status == status)
            total = int(
                await db.scalar(select(func.count()).select_from(base.subquery())) or 0
            )
            rows = (
                await db.execute(
                    base.order_by(CourseEnrollment.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
            items: list[AdminCourseEnrollmentListItem] = []
            for enrollment, course_title, order_status, order_price in rows:
                entitlement_rows = (
                    await db.execute(
                        select(QuizLibraryEntitlement.source_type).where(
                            QuizLibraryEntitlement.course_id == enrollment.course_id,
                            QuizLibraryEntitlement.user_id == enrollment.user_id,
                            QuizLibraryEntitlement.status == "active",
                        )
                    )
                ).scalars().all()
                items.append(
                    AdminCourseEnrollmentListItem(
                        id=enrollment.id,
                        user_id=enrollment.user_id,
                        course_id=enrollment.course_id,
                        course_title=course_title,
                        order_id=enrollment.order_id,
                        order_status=order_status,
                        order_price=order_price,
                        active_entitlement_count=len(entitlement_rows),
                        entitlement_sources=sorted(set(entitlement_rows)),
                        status=enrollment.status,
                        learning_access=enrollment.learning_access,
                        access_granted_at=enrollment.access_granted_at,
                        access_revoked_at=enrollment.access_revoked_at,
                        created_at=enrollment.created_at,
                    )
                )
            return PaginatedData(items=items, total=total, page=page, page_size=page_size)

    async def list_bindings(self, course_id: int) -> list[AdminCourseBindingResponse]:
        async with get_db_ctx() as db:
            rows = (
                await db.execute(
                    select(QuizCourseLibraryBinding, QuizLibrary)
                    .join(QuizLibrary, QuizLibrary.id == QuizCourseLibraryBinding.library_id)
                    .where(QuizCourseLibraryBinding.course_id == course_id)
                    .order_by(QuizCourseLibraryBinding.id)
                )
            ).all()
            return [
                AdminCourseBindingResponse(
                    id=binding.id,
                    course_id=binding.course_id,
                    library_id=binding.library_id,
                    library_name=library.name,
                    library_code=library.library_code,
                    status=binding.status,
                    lock_version=binding.lock_version,
                    created_at=binding.created_at,
                    updated_at=binding.updated_at,
                )
                for binding, library in rows
            ]

    async def list_jobs(
        self,
        course_id: int,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[AdminCourseEntitlementJobResponse]:
        async with get_db_ctx() as db:
            base = select(CourseEntitlementJob).where(
                CourseEntitlementJob.course_id == course_id
            )
            total = int(
                await db.scalar(select(func.count()).select_from(base.subquery())) or 0
            )
            rows = (
                await db.execute(
                    base.order_by(CourseEntitlementJob.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            items = []
            for job in rows:
                failed = (
                    await db.execute(
                        select(CourseEntitlementJobItem)
                        .where(
                            CourseEntitlementJobItem.job_id == job.id,
                            CourseEntitlementJobItem.status == "failed",
                        )
                        .order_by(CourseEntitlementJobItem.id)
                        .limit(20)
                    )
                ).scalars().all()
                items.append(
                    AdminCourseEntitlementJobResponse(
                        **{
                            field: getattr(job, field)
                            for field in AdminCourseEntitlementJobResponse.model_fields
                            if field != "failed_items"
                        },
                        failed_items=[
                            {
                                "id": item.id,
                                "enrollment_id": item.enrollment_id,
                                "user_id": item.user_id,
                                "status": item.status,
                                "error_message": item.error_message,
                            }
                            for item in failed
                        ],
                    )
                )
            return PaginatedData(items=items, total=total, page=page, page_size=page_size)

    async def retry_job(self, job_id: int, *, admin_id: int) -> AdminCourseEntitlementJobResponse:
        await CourseEntitlementService.retry_job(job_id, admin_id=admin_id)
        async with get_db_ctx() as db:
            job = await db.get(CourseEntitlementJob, job_id)
            if job is None:
                raise NotFoundException("课程权益任务")
            return AdminCourseEntitlementJobResponse(
                **{
                    field: getattr(job, field)
                    for field in AdminCourseEntitlementJobResponse.model_fields
                    if field != "failed_items"
                }
            )

    async def list_audit_logs(
        self,
        *,
        course_id: int | None = None,
        action: str | None = None,
        result: str | None = None,
        page: int,
        page_size: int,
    ) -> PaginatedData[AdminCourseAuditListItem]:
        async with get_db_ctx() as db:
            base = select(CourseAuditLog)
            if course_id is not None:
                chapter_ids = select(CourseChapter.id).where(
                    CourseChapter.course_id == course_id
                )
                binding_ids = select(QuizCourseLibraryBinding.id).where(
                    QuizCourseLibraryBinding.course_id == course_id
                )
                job_ids = select(CourseEntitlementJob.id).where(
                    CourseEntitlementJob.course_id == course_id
                )
                from sqlalchemy import or_

                base = base.where(
                    or_(
                        (CourseAuditLog.object_type == "course")
                        & (CourseAuditLog.object_id == course_id),
                        (CourseAuditLog.object_type == "course_chapter")
                        & CourseAuditLog.object_id.in_(chapter_ids),
                        (CourseAuditLog.object_type == "course_library_binding")
                        & CourseAuditLog.object_id.in_(binding_ids),
                        (CourseAuditLog.object_type == "course_entitlement_job")
                        & CourseAuditLog.object_id.in_(job_ids),
                    )
                )
            if action:
                base = base.where(CourseAuditLog.action == action)
            if result:
                base = base.where(CourseAuditLog.result == result)
            total = int(
                await db.scalar(select(func.count()).select_from(base.subquery())) or 0
            )
            rows = (
                await db.execute(
                    base.order_by(CourseAuditLog.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return PaginatedData(
                items=[AdminCourseAuditListItem.model_validate(row) for row in rows],
                total=total,
                page=page,
                page_size=page_size,
            )
