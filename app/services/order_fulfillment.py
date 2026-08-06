from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.certification.src.index import CourseEnrollment
from app.domain.order.src.index import Order
from app.domain.renshe.src.index import RensheApplication, RensheApplicationVersion
from app.port.exceptions import ConflictException


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
        if enrollment.status in {"enrolled", "completed"} and enrollment.learning_access:
            return False
        if enrollment.status in {"refunded", "cancelled", "expired"}:
            return False
        if enrollment.status != "pending_payment":
            raise ConflictException("当前报名状态不允许开通学习权限")

        enrollment.status = "enrolled"
        enrollment.learning_access = True
        enrollment.access_granted_at = order.paid_at or self._now()
        enrollment.access_revoked_at = None
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
