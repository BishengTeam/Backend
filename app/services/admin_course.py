from datetime import datetime, timezone

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import BusinessException, NotFoundException
from app.domain.certification.src.index import (
    Course,
    CourseAsset,
    CourseChapter,
    CourseEnrollment,
)
from app.domain.order.src.index import Order
from app.schemas.admin_course import (
    AdminCourseAssetResponse,
    AdminCourseCreate,
    AdminCourseEnrollmentListItem,
    AdminCourseListItem,
    AdminCourseUpdate,
)
from app.schemas.common import PaginatedData
from app.schemas.course import ChapterCreate, ChapterResponse, ChapterSortItem, ChapterUpdate
from app.services.course_asset import CourseAssetStorage


class AdminCourseService:

    _list_columns = (
        Course.id,
        Course.title,
        Course.category,
        Course.description,
        Course.cover_url,
        Course.video_url,
        Course.price,
        Course.batches,
        Course.teacher_name,
        Course.teacher_contact,
        Course.is_active,
        Course.free_preview_seconds,
        Course.created_at,
    )

    async def list_courses(
        self, keyword: str | None, category: str | None, page: int, page_size: int
    ) -> PaginatedData[AdminCourseListItem]:
        async with get_db_ctx() as db:
            base = select(*self._list_columns)
            if keyword:
                base = base.where(Course.title.ilike(f"%{keyword}%"))
            if category:
                base = base.where(Course.category == category)
            count_stmt = select(func.count()).select_from(base.subquery())
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

    # ==================== 章节管理 ====================

    async def list_chapters(
        self, course_id: int, is_active: bool | None, page: int, page_size: int
    ) -> PaginatedData[ChapterResponse]:
        async with get_db_ctx() as db:
            base = select(CourseChapter).where(CourseChapter.course_id == course_id)
            if is_active is not None:
                base = base.where(CourseChapter.is_active == is_active)
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = (
                base.order_by(CourseChapter.sort_order.asc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            rows = (await db.execute(stmt)).scalars().all()
            return PaginatedData[ChapterResponse](
                items=[ChapterResponse.model_validate(r) for r in rows],
                total=total,
                page=page,
                page_size=page_size,
            )

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

            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar_one()
            rows = (
                await db.execute(
                    base.order_by(CourseEnrollment.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
            items = []
            for enrollment, course_title, order_status, order_price in rows:
                items.append(
                    AdminCourseEnrollmentListItem(
                        id=enrollment.id,
                        user_id=enrollment.user_id,
                        course_id=enrollment.course_id,
                        course_title=course_title,
                        order_id=enrollment.order_id,
                        order_status=order_status,
                        order_price=order_price,
                        batch_selected=enrollment.batch_selected,
                        status=enrollment.status,
                        learning_access=enrollment.learning_access,
                        access_granted_at=enrollment.access_granted_at,
                        access_revoked_at=enrollment.access_revoked_at,
                        created_at=enrollment.created_at,
                    )
                )
            return PaginatedData[AdminCourseEnrollmentListItem](
                items=items,
                total=total,
                page=page,
                page_size=page_size,
            )

    async def create_chapter(
        self, course_id: int, data: ChapterCreate
    ) -> ChapterResponse:
        async with get_db_ctx() as db:
            course = await db.get(Course, course_id)
            if course is None:
                raise NotFoundException("课程")
            # sort_order 默认追加到末尾
            if data.sort_order is None:
                max_sort = (
                    await db.execute(
                        select(func.coalesce(func.max(CourseChapter.sort_order), 0))
                        .where(CourseChapter.course_id == course_id)
                    )
                ).scalar() or 0
                sort_order = max_sort + 1
            else:
                sort_order = data.sort_order
            chapter = CourseChapter(
                course_id=course_id,
                title=data.title,
                video_url=data.video_url,
                duration=data.duration,
                sort_order=sort_order,
            )
            db.add(chapter)
            await db.commit()
            await db.refresh(chapter)
            return ChapterResponse.model_validate(chapter)

    async def update_chapter(
        self, chapter_id: int, data: ChapterUpdate
    ) -> ChapterResponse:
        async with get_db_ctx() as db:
            chapter = await db.get(CourseChapter, chapter_id)
            if chapter is None:
                raise NotFoundException("章节")
            update_data = data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(chapter, key, value)
            await db.commit()
            await db.refresh(chapter)
            return ChapterResponse.model_validate(chapter)

    async def delete_chapter(self, chapter_id: int) -> None:
        """软删除章节。"""
        async with get_db_ctx() as db:
            chapter = await db.get(CourseChapter, chapter_id)
            if chapter is None:
                raise NotFoundException("章节")
            chapter.is_active = False
            await db.commit()

    async def sort_chapters(
        self, course_id: int, items: list[ChapterSortItem]
    ) -> int:
        """批量更新 sort_order，返回更新条数。"""
        async with get_db_ctx() as db:
            count = 0
            for item in items:
                chapter = await db.get(CourseChapter, item.id)
                if chapter is None or chapter.course_id != course_id:
                    continue
                chapter.sort_order = item.sort_order
                count += 1
            await db.commit()
            return count

    async def revoke_enrollment(
        self,
        enrollment_id: int,
    ) -> AdminCourseEnrollmentListItem:
        async with get_db_ctx() as db:
            enrollment = (
                await db.execute(
                    select(CourseEnrollment)
                    .where(CourseEnrollment.id == enrollment_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if enrollment is None:
                raise NotFoundException("课程报名记录")
            if enrollment.status == "pending_payment":
                raise BusinessException("待支付报名尚未开通学习权限")

            revocable = enrollment.status in {"enrolled", "completed"}
            if revocable:
                enrollment.status = "cancelled"
            if enrollment.learning_access or (
                revocable and enrollment.access_granted_at is not None
            ):
                enrollment.learning_access = False
                enrollment.access_revoked_at = datetime.now(timezone.utc)
            await db.commit()

            course = await db.get(Course, enrollment.course_id)
            order = await db.get(Order, enrollment.order_id) if enrollment.order_id else None
            return AdminCourseEnrollmentListItem(
                id=enrollment.id,
                user_id=enrollment.user_id,
                course_id=enrollment.course_id,
                course_title=course.title if course else "",
                order_id=enrollment.order_id,
                order_status=order.status if order else None,
                order_price=order.price if order else None,
                batch_selected=enrollment.batch_selected,
                status=enrollment.status,
                learning_access=enrollment.learning_access,
                access_granted_at=enrollment.access_granted_at,
                access_revoked_at=enrollment.access_revoked_at,
                created_at=enrollment.created_at,
            )

    async def list_assets(self, course_id: int) -> list[AdminCourseAssetResponse]:
        async with get_db_ctx() as db:
            if await db.get(Course, course_id) is None:
                raise NotFoundException("课程")
            assets = (
                await db.execute(
                    select(CourseAsset)
                    .where(CourseAsset.course_id == course_id)
                    .order_by(CourseAsset.sort_order, CourseAsset.id)
                )
            ).scalars().all()
            return [AdminCourseAssetResponse.model_validate(asset) for asset in assets]

    async def create_asset(
        self,
        *,
        course_id: int,
        filename: str,
        content: bytes,
        title: str,
        asset_type: str,
        sort_order: int,
        is_preview: bool,
    ) -> AdminCourseAssetResponse:
        async with get_db_ctx() as db:
            if await db.get(Course, course_id) is None:
                raise NotFoundException("课程")
            storage_key, _ = CourseAssetStorage.save(course_id, filename, content)
            try:
                asset = CourseAsset(
                    course_id=course_id,
                    title=title,
                    storage_key=storage_key,
                    asset_type=asset_type,
                    sort_order=sort_order,
                    is_preview=is_preview,
                )
                db.add(asset)
                await db.commit()
            except Exception:
                CourseAssetStorage.delete(storage_key)
                raise
            await db.refresh(asset)
            return AdminCourseAssetResponse.model_validate(asset)

    async def delete_asset(self, course_id: int, asset_id: int) -> None:
        async with get_db_ctx() as db:
            asset = (
                await db.execute(
                    select(CourseAsset).where(
                        CourseAsset.id == asset_id,
                        CourseAsset.course_id == course_id,
                    )
                )
            ).scalar_one_or_none()
            if asset is None:
                raise NotFoundException("课程资源")
            storage_key = asset.storage_key
            await db.delete(asset)
            await db.commit()
        CourseAssetStorage.delete(storage_key)
