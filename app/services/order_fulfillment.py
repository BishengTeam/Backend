from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.certification.src.index import CourseEnrollment
from app.domain.certification.src.index import Course
from app.domain.order.src.index import Order
from app.domain.community.src.index import (
    QuizCourseLibraryBinding,
    QuizLibrary,
    QuizLibraryEntitlement,
)
from app.domain.renshe.src.index import RensheApplication, RensheApplicationVersion
from app.port.exceptions import ConflictException
from app.services.quiz_v2 import QuizV2Service


class OrderFulfillmentService:
    """Apply product fulfillment inside the caller's database transaction."""

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    async def _lock_course_enrollment(
        self,
        db: AsyncSession,
        order: Order,
    ) -> CourseEnrollment | None:
        if order.order_kind != "course":
            return None
        return (
            await db.execute(
                select(CourseEnrollment)
                .where(CourseEnrollment.order_id == order.id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def _lock_renshe_application(
        self, db: AsyncSession, order: Order
    ) -> RensheApplication | None:
        if order.application_id is None:
            return None
        return (
            await db.execute(
                select(RensheApplication)
                .where(RensheApplication.id == order.application_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def _grant_quiz_entitlements(
        self,
        db: AsyncSession,
        order: Order,
        enrollment: CourseEnrollment,
    ) -> bool:
        """Grant all active libraries bound to the settled course, idempotently."""

        rows = (
            await db.execute(
                select(QuizCourseLibraryBinding, QuizLibrary)
                .join(QuizLibrary, QuizLibrary.id == QuizCourseLibraryBinding.library_id)
                .where(
                    QuizCourseLibraryBinding.course_id == enrollment.course_id,
                    QuizCourseLibraryBinding.status == "active",
                    QuizLibrary.access_mode == "course_entitlement",
                    QuizLibrary.status.in_(("draft", "published", "suspended")),
                )
                .order_by(QuizCourseLibraryBinding.id.asc())
            )
        ).all()
        if not rows:
            return False
        starts_at = order.paid_at or self._now()
        changed = False
        changed_library_ids: set[int] = set()
        for _binding, library in rows:
            entitlement = (
                await db.execute(
                    select(QuizLibraryEntitlement)
                    .where(
                        QuizLibraryEntitlement.order_id == order.id,
                        QuizLibraryEntitlement.library_id == library.id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            snapshot = {
                "library_id": int(library.id),
                "library_code": library.library_code,
                "name": library.name,
                "cover_url": library.cover_url,
                "description": library.description,
            }
            if entitlement is None:
                db.add(
                    QuizLibraryEntitlement(
                        user_id=order.user_id,
                        library_id=library.id,
                        course_id=enrollment.course_id,
                        order_id=order.id,
                        source_type="course_order",
                        status="active",
                        starts_at=starts_at,
                        snapshot=snapshot,
                    )
                )
                changed = True
                changed_library_ids.add(int(library.id))
            elif entitlement.status != "active":
                entitlement.status = "active"
                entitlement.starts_at = starts_at
                entitlement.ends_at = None
                entitlement.revoked_at = None
                entitlement.snapshot = snapshot
                changed = True
                changed_library_ids.add(int(library.id))
        for library_id in changed_library_ids:
            await QuizV2Service.sync_sessions_for_library(
                db, library_id, user_id=order.user_id, now=self._now()
            )
        return changed

    async def _revoke_quiz_entitlements(
        self, db: AsyncSession, order: Order
    ) -> bool:
        entitlements = list(
            (
                await db.execute(
                    select(QuizLibraryEntitlement)
                    .where(
                        QuizLibraryEntitlement.order_id == order.id,
                        QuizLibraryEntitlement.status == "active",
                    )
                    .with_for_update()
                )
            ).scalars()
        )
        now = self._now()
        for entitlement in entitlements:
            entitlement.status = "revoked"
            entitlement.revoked_at = now
        for library_id in {int(item.library_id) for item in entitlements}:
            await QuizV2Service.sync_sessions_for_library(
                db, library_id, user_id=order.user_id, now=now
            )
        return bool(entitlements)

    async def on_paid(self, db: AsyncSession, order: Order) -> bool:
        application = await self._lock_renshe_application(db, order)
        if order.application_id is not None:
            if application is None:
                raise ConflictException("人社订单缺少报名记录")
            if application.status == "pending_initial_review":
                return False
            if application.status != "pending_payment":
                raise ConflictException("当前人社报名状态不允许确认支付")
            application.status = "pending_initial_review"
            return True

        enrollment = await self._lock_course_enrollment(db, order)
        if order.order_kind != "course":
            return False
        if enrollment is None:
            raise ConflictException("课程订单缺少报名记录，无法开通学习权限")
        course = await db.get(Course, enrollment.course_id)
        if course is None or course.status != "published":
            raise ConflictException("课程已下线，无法开通学习权限")
        if enrollment.status in {"enrolled", "completed"} and enrollment.learning_access:
            return await self._grant_quiz_entitlements(db, order, enrollment)
        if enrollment.status in {"refunded", "cancelled", "expired"}:
            return False
        if enrollment.status != "pending_payment":
            raise ConflictException("当前报名状态不允许开通学习权限")

        enrollment.status = "enrolled"
        enrollment.learning_access = True
        enrollment.access_granted_at = order.paid_at or self._now()
        enrollment.access_revoked_at = None
        await self._grant_quiz_entitlements(db, order, enrollment)
        return True

    async def on_refunded(self, db: AsyncSession, order: Order) -> bool:
        application = await self._lock_renshe_application(db, order)
        if order.application_id is not None:
            if application is None:
                raise ConflictException("人社订单缺少报名记录")
            if application.status == "closed":
                return False
            application.status = "closed"
            application.closed_at = self._now()
            application.close_reason = "refund_succeeded"
            application.frozen_at = None
            application.freeze_reason = None
            return True

        enrollment = await self._lock_course_enrollment(db, order)
        if order.order_kind == "course" and enrollment is None:
            raise ConflictException("课程订单缺少报名记录，无法撤销学习权限")
        if enrollment is None:
            return False
        changed = enrollment.status != "refunded" or enrollment.learning_access
        if not changed:
            return False

        enrollment.status = "refunded"
        enrollment.learning_access = False
        enrollment.access_revoked_at = self._now()
        await self._revoke_quiz_entitlements(db, order)
        return True

    async def on_closed(self, db: AsyncSession, order: Order) -> bool:
        application = await self._lock_renshe_application(db, order)
        if order.application_id is not None:
            if application is None:
                raise ConflictException("人社订单缺少报名记录")
            if application.status == "draft":
                return False
            if application.status != "pending_payment":
                raise ConflictException("当前人社报名状态不允许关闭待支付订单")
            application.status = "draft"
            if application.current_version_id is not None:
                version = await db.get(
                    RensheApplicationVersion, application.current_version_id
                )
                if version is not None:
                    application.draft_data = dict(version.form_data)
            return True

        enrollment = await self._lock_course_enrollment(db, order)
        if order.order_kind == "course" and enrollment is None:
            raise ConflictException("课程订单缺少报名记录，无法关闭报名")
        if enrollment is None or enrollment.status != "pending_payment":
            return False

        enrollment.status = "cancelled"
        enrollment.learning_access = False
        return True
