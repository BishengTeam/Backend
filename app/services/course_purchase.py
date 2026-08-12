import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.domain.certification.src.index import Course, CourseEnrollment
from app.domain.order.src.index import (
    ORDER_PAYMENT_EXPIRE_MINUTES,
    Order,
    close_expired_pending_order,
)
from app.domain.user.src.index import User
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.schemas.course import CoursePurchaseResponse
from app.services.order_fulfillment import OrderFulfillmentService


ACTIVE_ENROLLMENT_STATUSES = ("pending_payment", "enrolled", "completed")


class CoursePurchaseService:
    def __init__(self) -> None:
        self.fulfillment = OrderFulfillmentService()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _response(enrollment: CourseEnrollment) -> CoursePurchaseResponse:
        return CoursePurchaseResponse(
            course_id=enrollment.course_id,
            enrollment_id=enrollment.id,
            payment_required=enrollment.status == "pending_payment",
            order_id=enrollment.order_id,
            status=enrollment.status,
            learning_access=enrollment.learning_access,
        )

    async def purchase(
        self,
        user_id: int,
        course_id: int,
        *,
        batch: str | None = None,
        allow_paid: bool = True,
    ) -> CoursePurchaseResponse:
        async with get_db_ctx() as db:
            async with db.begin():
                # Serializes concurrent purchases for the same user, including
                # the no-existing-enrollment race before the partial index fires.
                user_exists = (
                    await db.execute(
                        select(User.id)
                        .where(User.id == user_id, User.is_active.is_(True))
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if user_exists is None:
                    raise NotFoundException("用户")

                course = (
                    await db.execute(
                        select(Course).where(Course.id == course_id).with_for_update()
                    )
                ).scalar_one_or_none()
                if course is None or not course.is_active:
                    raise NotFoundException("课程")
                if course.price > 0 and not allow_paid:
                    raise BusinessException("付费课程请使用课程购买接口")

                enrollment = (
                    await db.execute(
                        select(CourseEnrollment)
                        .where(
                            CourseEnrollment.user_id == user_id,
                            CourseEnrollment.course_id == course_id,
                            CourseEnrollment.status.in_(ACTIVE_ENROLLMENT_STATUSES),
                        )
                    )
                ).scalar_one_or_none()
                if enrollment is not None:
                    candidate_order_id = enrollment.order_id
                    order = None
                    if candidate_order_id is not None:
                        order = (
                            await db.execute(
                                select(Order)
                                .where(Order.id == candidate_order_id)
                                .with_for_update()
                            )
                        ).scalar_one_or_none()
                    enrollment = (
                        await db.execute(
                            select(CourseEnrollment)
                            .where(CourseEnrollment.id == enrollment.id)
                            .with_for_update()
                        )
                    ).scalar_one_or_none()
                    if enrollment is not None and enrollment.status in ACTIVE_ENROLLMENT_STATUSES:
                        if enrollment.order_id != candidate_order_id:
                            raise ConflictException("课程报名的订单关联已发生变化，请重试")
                        reusable = await self._reuse_existing(db, enrollment, order)
                        if reusable:
                            return self._response(enrollment)

                if course.price == 0:
                    enrollment = CourseEnrollment(
                        user_id=user_id,
                        course_id=course.id,
                        batch_selected=batch,
                        status="enrolled",
                        learning_access=True,
                        access_granted_at=self._now(),
                    )
                    db.add(enrollment)
                    await db.flush()
                    return self._response(enrollment)

                expires_at = self._now() + timedelta(
                    minutes=ORDER_PAYMENT_EXPIRE_MINUTES
                )
                order = Order(
                    user_id=user_id,
                    order_kind="course",
                    product_type=f"course:{course.id}",
                    price=course.price,
                    status="pending",
                    out_trade_no=(
                        f"course_{user_id}_{int(time.time() * 1000)}_"
                        f"{uuid.uuid4().hex[:8]}"
                    ),
                    expires_at=expires_at,
                    extra_data={"course_id": course.id, "course_title": course.title},
                )
                db.add(order)
                await db.flush()

                enrollment = CourseEnrollment(
                    user_id=user_id,
                    course_id=course.id,
                    order_id=order.id,
                    batch_selected=batch,
                    status="pending_payment",
                    learning_access=False,
                )
                db.add(enrollment)
                await db.flush()
                return self._response(enrollment)

    async def _reuse_existing(
        self,
        db,
        enrollment: CourseEnrollment,
        order: Order | None,
    ) -> bool:
        if enrollment.status in {"enrolled", "completed"}:
            return True
        if enrollment.status != "pending_payment":
            return False
        if enrollment.order_id is None:
            enrollment.status = "cancelled"
            enrollment.learning_access = False
            return False

        if order is None:
            enrollment.status = "cancelled"
            enrollment.learning_access = False
            enrollment.order_id = None
            return False
        if (
            order.user_id != enrollment.user_id
            or order.order_kind != "course"
            or order.product_type != f"course:{enrollment.course_id}"
        ):
            raise ConflictException("课程报名关联的订单信息不一致")
        if order.status == "pending":
            if close_expired_pending_order(order, now=self._now()):
                await self.fulfillment.on_closed(db, order)
                return False
            return True
        if order.status in {"paid", "completed"}:
            await self.fulfillment.on_paid(db, order)
            return True
        if order.status == "refunded":
            await self.fulfillment.on_refunded(db, order)
        else:
            await self.fulfillment.on_closed(db, order)
        return False
