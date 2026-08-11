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
from app.port.config import settings
from app.port.exceptions import ConflictException
from app.schemas.plan import (
    PlanImpactAction,
    PlanImpactBlocker,
    PlanImpactResponse,
)


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

    @staticmethod
    def cleanup_due_at(now: datetime) -> datetime:
        """Return the deployment-controlled cleanup deadline.

        Production configuration is validated to exactly 30 days at startup;
        isolated non-production deployments may shorten it for cleanup UAT.
        Keeping this calculation here makes previews and mutating operations
        use the same rule.
        """

        return now + timedelta(days=settings.RENSHE_CLEANUP_RETENTION_DAYS)

    async def preview_impact(
        self,
        db: AsyncSession,
        *,
        plan: Plan,
        action: PlanImpactAction,
    ) -> PlanImpactResponse:
        """Build a read-only impact snapshot for cancellation or finalization.

        This deliberately performs no locking and no mutation: it is advisory
        data for the confirmation dialog.  ``cancel`` and ``finalize`` still
        lock and re-check all state in their own transactions.
        """

        applications = (
            await db.execute(
                select(RensheApplication)
                .where(RensheApplication.plan_id == plan.id)
                .order_by(RensheApplication.id)
            )
        ).scalars().all()
        affected = [item for item in applications if item.status != "closed"]
        application_ids = [item.id for item in affected]

        orders: list[Order] = []
        refunds: list[RensheRefundRequest] = []
        if application_ids:
            orders = (
                await db.execute(
                    select(Order)
                    .where(
                        Order.plan_id == plan.id,
                        Order.application_id.in_(application_ids),
                    )
                    .order_by(Order.application_id, Order.id)
                )
            ).scalars().all()
            refunds = (
                await db.execute(
                    select(RensheRefundRequest)
                    .where(
                        RensheRefundRequest.application_id.in_(application_ids),
                        RensheRefundRequest.status.in_(
                            (*ACTIVE_REFUND_STATUSES, "succeeded")
                        ),
                    )
                    .order_by(
                        RensheRefundRequest.application_id,
                        RensheRefundRequest.id,
                    )
                )
            ).scalars().all()

        latest_orders: dict[int, Order] = {}
        latest_paid_orders: dict[int, Order] = {}
        for order in orders:
            if order.application_id is None:
                continue
            latest_orders[order.application_id] = order
            if order.status in {"paid", "completed"}:
                latest_paid_orders[order.application_id] = order
        already_refunded_order_ids = {refund.order_id for refund in refunds}

        if action == "cancel":
            pending_order_close_count = sum(
                1
                for application in affected
                if (order := latest_orders.get(application.id)) is not None
                and order.status == "pending"
            )
            refund_orders = [
                order
                for application in affected
                if (order := latest_orders.get(application.id)) is not None
                and order.status in {"paid", "completed"}
                and order.id not in already_refunded_order_ids
            ]
            allowed_statuses = {"draft", "published", "registration_closed"}
            invalid_status_message = "当前批次状态不可取消"
        else:
            pending_order_close_count = 0
            refund_orders = [
                order
                for application in affected
                if application.status in {"initial_rejected", "external_rejected"}
                and (order := latest_paid_orders.get(application.id)) is not None
                and order.id not in already_refunded_order_ids
            ]
            allowed_statuses = {"published", "registration_closed"}
            invalid_status_message = "仅已发布或已关闭报名的批次可终结"

        blocking_status_counts = {
            status: sum(1 for item in affected if item.status == status)
            for status in PENDING_FINALIZATION_STATUSES
            if any(item.status == status for item in affected)
        }
        blocking_application_count = (
            sum(blocking_status_counts.values()) if action == "finalize" else 0
        )
        blockers: list[PlanImpactBlocker] = []
        if plan.status not in allowed_statuses:
            blockers.append(
                PlanImpactBlocker(
                    code="plan_status",
                    message=invalid_status_message,
                )
            )
        if action == "finalize" and blocking_application_count:
            blockers.append(
                PlanImpactBlocker(
                    code="pending_applications",
                    message="批次仍有待支付或待审核报名，不能终结",
                    count=blocking_application_count,
                    status_counts=blocking_status_counts,
                )
            )

        now = self._now()
        return PlanImpactResponse(
            action=action,
            plan_id=plan.id,
            plan_name=plan.name,
            plan_status=plan.status,
            affected_application_count=len(affected),
            pending_order_close_count=pending_order_close_count,
            refund_candidate_count=len(refund_orders),
            refund_amount_cents=sum(order.price for order in refund_orders),
            blocking_application_count=blocking_application_count,
            blocking_status_counts=(
                blocking_status_counts if action == "finalize" else {}
            ),
            previewed_at=now,
            cleanup_due_at=self.cleanup_due_at(now),
            can_execute=not blockers,
            blockers=blockers,
        )

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
        plan.cleanup_due_at = self.cleanup_due_at(now)
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
        plan.cleanup_due_at = self.cleanup_due_at(now)
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
            retry_count=0,
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
