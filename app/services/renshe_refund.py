from datetime import datetime, timezone

from sqlalchemy import func, select, update

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order
from app.domain.renshe.src.index import (
    RensheApplication,
    RensheAuditLog,
    RensheCleanupRun,
    RensheRefundRequest,
    add_business_days,
)
from app.port.exceptions import ConflictException, NotFoundException, ThirdPartyException
from app.schemas.common import PaginatedData
from app.schemas.renshe import (
    RensheRefundCreate,
    RensheRefundDecision,
    RensheRefundResponse,
)


ACTIVE_REFUND_STATUSES = ("requested", "approved", "processing", "failed")


class RensheRefundService:
    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    async def request_refund(
        self,
        *,
        user_id: int,
        application_id: int,
        data: RensheRefundCreate,
    ) -> RensheRefundResponse:
        async with get_db_ctx() as db:
            order = await db.scalar(
                select(Order)
                .where(
                    Order.application_id == application_id,
                    Order.user_id == user_id,
                    Order.status.in_(("paid", "completed")),
                )
                .order_by(Order.id.desc())
                .with_for_update()
                .limit(1)
            )
            application = await db.scalar(
                select(RensheApplication)
                .where(
                    RensheApplication.id == application_id,
                    RensheApplication.user_id == user_id,
                )
                .with_for_update()
            )
            if application is None:
                raise NotFoundException("人社报名")
            if application.status == "closed":
                raise ConflictException("报名已关闭，不能再次申请退款")
            if data.request_kind == "normal" and application.status == "external_approved":
                raise ConflictException("复审已通过，仅可按例外原因申请退款")
            if order is None:
                raise ConflictException("报名没有可退款的已支付订单")
            cleanup_run = await db.scalar(
                select(RensheCleanupRun)
                .where(
                    RensheCleanupRun.plan_id == application.plan_id,
                    RensheCleanupRun.status.in_(("scheduled", "paused", "running")),
                )
                .order_by(RensheCleanupRun.id.desc())
                .with_for_update()
                .limit(1)
            )
            if cleanup_run is not None and cleanup_run.status == "running":
                raise ConflictException("批次敏感数据正在清理，请稍后再申请退款")

            existing = await db.scalar(
                select(RensheRefundRequest)
                .where(
                    RensheRefundRequest.order_id == order.id,
                    RensheRefundRequest.status.in_(ACTIVE_REFUND_STATUSES),
                )
                .order_by(RensheRefundRequest.id.desc())
                .limit(1)
            )
            if existing is not None:
                return RensheRefundResponse.model_validate(existing)

            succeeded = await db.scalar(
                select(RensheRefundRequest.id)
                .where(
                    RensheRefundRequest.order_id == order.id,
                    RensheRefundRequest.status == "succeeded",
                )
                .limit(1)
            )
            if succeeded is not None:
                raise ConflictException("该订单已退款成功")

            now = self._now()
            refund = RensheRefundRequest(
                application_id=application.id,
                order_id=order.id,
                user_id=user_id,
                request_kind=data.request_kind,
                reason_code=data.reason_code,
                reason_detail=data.reason_detail,
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
            await db.execute(
                update(RensheCleanupRun)
                .where(
                    RensheCleanupRun.plan_id == application.plan_id,
                    RensheCleanupRun.status == "scheduled",
                )
                .values(status="paused", paused_reason="active_refunds")
            )
            db.add(
                RensheAuditLog(
                    actor_type="user",
                    actor_id=user_id,
                    action="refund.request",
                    object_type="refund",
                    object_id=refund.id,
                    application_id=application.id,
                    version_id=application.current_version_id,
                    result="succeeded",
                    summary={"kind": data.request_kind, "amount_cents": order.price},
                )
            )
            await db.commit()
            await db.refresh(refund)
            return RensheRefundResponse.model_validate(refund)

    async def get_refund(self, user_id: int, refund_id: int) -> RensheRefundResponse:
        async with get_db_ctx() as db:
            refund = await db.scalar(
                select(RensheRefundRequest).where(
                    RensheRefundRequest.id == refund_id,
                    RensheRefundRequest.user_id == user_id,
                )
            )
            if refund is None:
                raise NotFoundException("退款申请")
            return RensheRefundResponse.model_validate(refund)

    async def list_refunds(
        self,
        *,
        status: str | None,
        page: int,
        page_size: int,
    ) -> PaginatedData[RensheRefundResponse]:
        async with get_db_ctx() as db:
            base = select(RensheRefundRequest)
            if status:
                base = base.where(RensheRefundRequest.status == status)
            total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
            rows = (
                await db.execute(
                    base.order_by(RensheRefundRequest.due_at, RensheRefundRequest.id)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return PaginatedData[RensheRefundResponse](
                items=[RensheRefundResponse.model_validate(row) for row in rows],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def decide(
        self,
        *,
        super_admin_id: int,
        refund_id: int,
        data: RensheRefundDecision,
    ) -> RensheRefundResponse:
        if data.decision == "approved":
            # Validate and audit the attempted operation, but do not persist an
            # approved/processing state until a signed V3 request can be issued.
            async with get_db_ctx() as db:
                refund = await db.scalar(
                    select(RensheRefundRequest)
                    .where(RensheRefundRequest.id == refund_id)
                    .with_for_update()
                )
                if refund is None:
                    raise NotFoundException("退款申请")
                if refund.status not in {"requested", "failed"}:
                    raise ConflictException("当前退款状态不能批准或重试")
                db.add(
                    RensheAuditLog(
                        actor_type="admin",
                        actor_id=super_admin_id,
                        action="refund.approve_blocked",
                        object_type="refund",
                        object_id=refund.id,
                        application_id=refund.application_id,
                        result="failed",
                        summary={"error_type": "WechatPayV3NotConfigured"},
                    )
                )
                await db.commit()
            raise ThirdPartyException(
                "微信支付 API V3 商户私钥、证书序列号、API V3 Key 和平台证书尚未配置，不能批准退款"
            )

        async with get_db_ctx() as db:
            refund = await db.scalar(
                select(RensheRefundRequest)
                .where(RensheRefundRequest.id == refund_id)
                .with_for_update()
            )
            if refund is None:
                raise NotFoundException("退款申请")
            if refund.status != "requested":
                raise ConflictException("当前退款状态不能驳回")
            application = await db.scalar(
                select(RensheApplication)
                .where(RensheApplication.id == refund.application_id)
                .with_for_update()
            )
            if application is None:
                raise NotFoundException("人社报名")

            now = self._now()
            refund.status = "rejected"
            refund.approved_by_admin_id = super_admin_id
            refund.decided_at = now
            refund.rejection_reason = data.reason.strip() if data.reason else None
            application.frozen_at = None
            application.freeze_reason = None
            db.add(
                RensheAuditLog(
                    actor_type="admin",
                    actor_id=super_admin_id,
                    action="refund.rejected",
                    object_type="refund",
                    object_id=refund.id,
                    application_id=application.id,
                    version_id=application.current_version_id,
                    result="succeeded",
                    summary={"decision": "rejected"},
                )
            )
            await db.commit()
            await db.refresh(refund)
            return RensheRefundResponse.model_validate(refund)
