import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.certification.src.index import CourseEnrollment
from app.domain.certification.src.index import Course
from app.domain.order.src.index import Order
from app.domain.community.src.index import (
    QuizCourseLibraryBinding,
    QuizLibrary,
    QuizLibraryEntitlement,
)
from app.domain.nisp.src.index import NispRegistration
from app.domain.renshe.src.index import RensheApplication, RensheApplicationVersion
from app.domain.user.src.index import PointsHistory
from app.port.exceptions import ConflictException
from app.services.points_rules import PAYMENT_POINTS_CENTS_PER_STEP
from app.services.quiz_v2 import QuizV2Service

logger = logging.getLogger(__name__)


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
        application = (
            await db.execute(
                select(RensheApplication)
                .where(RensheApplication.id == order.application_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if application is not None:
            from app.domain.plan.src.index import Plan
            from app.domain.plan.src.index import PlanProduct
            from app.models.cert_product import CertProduct
            from sqlalchemy import select as _select
            from app.port.exceptions import ConflictException
            plan = await db.get(Plan, application.plan_id)
            # v2: 产品编码从 PlanProduct→CertProduct 解析
            _code = None
            if plan is not None:
                _code = await db.scalar(
                    _select(CertProduct.code)
                    .join(PlanProduct, PlanProduct.product_id == CertProduct.id)
                    .where(PlanProduct.plan_id == plan.id)
                    .limit(1)
                )
            if plan is None or application.user_id != order.user_id or application.plan_id != order.plan_id or _code != order.product_type:
                raise ConflictException("人社订单与报名项目、批次或用户归属不一致")
        return application

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

    async def _on_paid_impl(self, db: AsyncSession, order: Order) -> bool:
        from app.services.h3c_registration import H3cRegistrationService
        from app.services.nisp_registration import NispRegistrationService

        h3c_processed = await H3cRegistrationService().on_order_paid(db, order)
        nisp_processed = await NispRegistrationService().on_order_paid(db, order)
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

        # 题库单独购买：支付成功直接发放授权
        if order.order_kind == "quiz_order":
            from app.services.quiz_purchase import QuizPurchaseService

            await QuizPurchaseService.fulfill(db, order)
            return True

        enrollment = await self._lock_course_enrollment(db, order)
        if order.order_kind != "course":
            return h3c_processed or nisp_processed
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

    async def on_paid(self, db: AsyncSession, order: Order) -> bool:
        """Fulfil an order, then schedule a closed-video-site code issue."""
        from app.services.videoweb import VideoWebService

        processed = await self._on_paid_impl(db, order)
        if order.order_kind in {"course", "quiz_order"}:
            VideoWebService.schedule_issue_for_order(order.id)
        await self._grant_payment_points(db, order)
        return processed

    async def on_refunded(self, db: AsyncSession, order: Order) -> bool:
        await self._revoke_payment_points(db, order)
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

    async def _grant_payment_points(self, db: AsyncSession, order: Order) -> None:
        """缴费返积分：每实付 10 元 = 1 积分，不足 10 元不计。

        所有真实付款订单统一生效（当前唯一场景是认证报名缴费线下核账）。
        按订单幂等（核账重放不重复发），SAVEPOINT 隔离，失败只记日志不阻断履约。
        """
        from app.services.points import PointsService

        points = order.price // PAYMENT_POINTS_CENTS_PER_STEP
        if points <= 0:
            return
        try:
            async with db.begin_nested():
                await PointsService()._grant_points_in_session(
                    db,
                    user_id=order.user_id,
                    amount=points,
                    action_type="earn_order",
                    description="缴费返积分",
                    source_type="order_payment",
                    source_id=str(order.id),
                )
        except Exception:
            logger.warning(
                "payment points grant failed: order_id=%s user_id=%s",
                order.id,
                order.user_id,
                exc_info=True,
            )

    async def _revoke_payment_points(self, db: AsyncSession, order: Order) -> None:
        """退款追回该订单发放的缴费返积分。

        先于各业务分支执行（多数分支会提前 return）；批次按先进先出核销，
        余额不足扣至负数（欠分不过期，兑换被余额校验拦截）。失败只记日志不阻断退款。
        """
        from app.services.points import PointsService

        granted = (
            await db.execute(
                select(func.coalesce(func.sum(PointsHistory.amount), 0)).where(
                    PointsHistory.user_id == order.user_id,
                    PointsHistory.action_type == "earn_order",
                    PointsHistory.source_type == "order_payment",
                    PointsHistory.source_id == str(order.id),
                )
            )
        ).scalar_one()
        if not granted or int(granted) <= 0:
            return
        try:
            async with db.begin_nested():
                await PointsService()._debit_points_in_session(
                    db,
                    user_id=order.user_id,
                    amount=int(granted),
                    action_type="refund_revoke",
                    description="订单退款扣回积分",
                    source_type="order_payment",
                    source_id=f"refund:{order.id}",
                    allow_negative=True,
                )
        except Exception:
            logger.warning(
                "payment points revoke failed: order_id=%s user_id=%s",
                order.id,
                order.user_id,
                exc_info=True,
            )

    async def on_closed(self, db: AsyncSession, order: Order) -> bool:
        from app.services.h3c_registration import H3cRegistrationService

        h3c_processed = await H3cRegistrationService().on_order_closed(db, order)
        nisp_registration = (
            await db.execute(
                select(NispRegistration)
                .where(NispRegistration.order_id == order.id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if nisp_registration is not None and nisp_registration.status == "pending_payment":
            nisp_registration.status = "cancelled"
            nisp_registration.closed_at = order.closed_at or self._now()
            nisp_registration.close_reason = order.close_reason or "order_closed"
            return True
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
            return h3c_processed

        enrollment.status = "cancelled"
        enrollment.learning_access = False
        return True
