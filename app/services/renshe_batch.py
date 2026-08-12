from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.order.src.index import Order, apply_order_status_transition
from app.domain.plan.src.index import Plan
from app.domain.renshe.src.index import (
    RensheApplication,
    RensheAuditLog,
    RensheCleanupRun,
    RensheRefundRequest,
    add_business_days,
)
from app.port.exceptions import ConflictException


ACTIVE_REFUND_STATUSES = ("requested", "approved", "processing", "failed")
PENDING_FINALIZATION_STATUSES = (
    "pending_payment",
    "pending_initial_review",
    "pending_external_review",
)


class RensheBatchService:
    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    async def cancel(
        self, db: AsyncSession, *, plan: Plan, admin_id: int | None
    ) -> None:
        if plan.status not in {"draft", "published", "registration_closed"}:
            raise ConflictException("当前批次状态不可取消")
        now = self._now()
        orders_by_application = await self._lock_latest_orders(db, plan.id)
        applications = (
            await db.execute(
                select(RensheApplication)
                .where(
                    RensheApplication.plan_id == plan.id,
                    RensheApplication.status != "closed",
                )
                .order_by(RensheApplication.id)
                .with_for_update()
            )
        ).scalars().all()
        for application in applications:
            order = orders_by_application.get(application.id)
            if order is None or order.status == "closed":
                application.status = "closed"
                application.closed_at = now
                application.close_reason = "batch_cancelled"
                continue
            if order.status == "pending":
                apply_order_status_transition(order, "closed")
                order.closed_at = now
                order.close_reason = "batch_cancelled"
                application.status = "closed"
                application.closed_at = now
                application.close_reason = "batch_cancelled"
                continue
            if order.status in {"paid", "completed"}:
                await self._ensure_system_refund(
                    db,
                    application=application,
                    order=order,
                    request_kind="batch_cancel",
                    reason_code="batch_cancelled",
                    now=now,
                )

        plan.status = "cancelled"
        plan.cancelled_at = now
        plan.cleanup_due_at = now + timedelta(days=7)
        await self._schedule_cleanup(db, plan=plan, due_at=plan.cleanup_due_at)
        db.add(
            self._audit(
                admin_id=admin_id,
                action="batch.cancel",
                plan=plan,
                summary={"applications": len(applications)},
            )
        )

    async def finalize(
        self, db: AsyncSession, *, plan: Plan, admin_id: int
    ) -> None:
        if plan.status not in {"published", "registration_closed"}:
            raise ConflictException("仅已发布或已关闭报名的批次可终结")
        pending_count = await db.scalar(
            select(func.count())
            .select_from(RensheApplication)
            .where(
                RensheApplication.plan_id == plan.id,
                RensheApplication.status.in_(PENDING_FINALIZATION_STATUSES),
            )
        )
        if pending_count:
            raise ConflictException("批次仍有待支付或待审核报名，不能终结")

        now = self._now()
        orders_by_application = await self._lock_latest_orders(
            db, plan.id, statuses=("paid", "completed")
        )
        rejected = (
            await db.execute(
                select(RensheApplication)
                .where(
                    RensheApplication.plan_id == plan.id,
                    RensheApplication.status.in_(("initial_rejected", "external_rejected")),
                )
                .order_by(RensheApplication.id)
                .with_for_update()
            )
        ).scalars().all()
        refund_count = 0
        for application in rejected:
            order = orders_by_application.get(application.id)
            if order is not None:
                created = await self._ensure_system_refund(
                    db,
                    application=application,
                    order=order,
                    request_kind="batch_finalize",
                    reason_code="batch_finalized_rejected",
                    now=now,
                )
                refund_count += int(created)

        plan.status = "finalized"
        plan.finalized_at = now
        plan.cleanup_due_at = now + timedelta(days=7)
        await self._schedule_cleanup(db, plan=plan, due_at=plan.cleanup_due_at)

        db.add(
            self._audit(
                admin_id=admin_id,
                action="batch.finalize",
                plan=plan,
                summary={"automatic_refunds": refund_count},
            )
        )

    @staticmethod
    async def _lock_latest_orders(
        db: AsyncSession,
        plan_id: int,
        *,
        statuses: tuple[str, ...] | None = None,
    ) -> dict[int, Order]:
        stmt = (
            select(Order)
            .where(
                Order.plan_id == plan_id,
                Order.application_id.is_not(None),
            )
            .order_by(Order.application_id, Order.id)
            .with_for_update()
        )
        if statuses is not None:
            stmt = stmt.where(Order.status.in_(statuses))
        orders = (await db.execute(stmt)).scalars().all()
        return {
            order.application_id: order
            for order in orders
            if order.application_id is not None
        }

    async def _schedule_cleanup(
        self, db: AsyncSession, *, plan: Plan, due_at: datetime
    ) -> None:
        run_no = (
            await db.scalar(
                select(func.max(RensheCleanupRun.run_no)).where(
                    RensheCleanupRun.plan_id == plan.id
                )
            )
            or 0
        ) + 1
        active_refund_count = await db.scalar(
            select(func.count())
            .select_from(RensheRefundRequest)
            .join(
                RensheApplication,
                RensheApplication.id == RensheRefundRequest.application_id,
            )
            .where(
                RensheApplication.plan_id == plan.id,
                RensheRefundRequest.status.in_(ACTIVE_REFUND_STATUSES),
            )
        )
        cleanup_status = "paused" if active_refund_count else "scheduled"
        db.add(
            RensheCleanupRun(
                plan_id=plan.id,
                run_no=run_no,
                status=cleanup_status,
                due_at=due_at,
                paused_reason=("active_refunds" if active_refund_count else None),
            )
        )

    async def _ensure_system_refund(
        self,
        db: AsyncSession,
        *,
        application: RensheApplication,
        order: Order,
        request_kind: str,
        reason_code: str,
        now: datetime,
    ) -> bool:
        existing = await db.scalar(
            select(RensheRefundRequest.id)
            .where(
                RensheRefundRequest.order_id == order.id,
                RensheRefundRequest.status.in_(
                    (*ACTIVE_REFUND_STATUSES, "succeeded")
                ),
            )
            .limit(1)
        )
        if existing is not None:
            return False
        refund = RensheRefundRequest(
            application_id=application.id,
            order_id=order.id,
            user_id=application.user_id,
            request_kind=request_kind,
            reason_code=reason_code,
            amount_cents=order.price,
            status="requested",
            previous_application_status=application.status,
            requested_at=now,
            due_at=add_business_days(now, 3),
        )
        db.add(refund)
        await db.flush()
        application.frozen_at = now
        application.freeze_reason = f"refund:{refund.id}"
        return True

    @staticmethod
    def _audit(
        *, admin_id: int | None, action: str, plan: Plan, summary: dict
    ) -> RensheAuditLog:
        return RensheAuditLog(
            actor_type="admin" if admin_id is not None else "system",
            actor_id=admin_id,
            action=action,
            object_type="plan",
            object_id=plan.id,
            result="succeeded",
            summary=summary,
        )
