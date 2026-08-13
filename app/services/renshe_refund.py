"""Human-resources full-refund workflow backed only by WeChat Pay API V3."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update

from app.adapter.database import get_db_ctx
from app.domain.order.src.index import Order, apply_order_status_transition
from app.domain.plan.src.index import Plan
from app.domain.renshe.src.index import (
    RensheApplication,
    RensheAuditLog,
    RensheCleanupRun,
    RensheRefundRequest,
    add_business_days,
)
from app.integrations.wechat_pay import (
    WECHAT_PAY_CURRENCY,
    WechatPayAPIError,
    WechatPayClient,
    WechatPayRefund,
    WechatPayResultUnknownError,
)
from app.port.config import settings
from app.port.exceptions import ConflictException, NotFoundException
from app.schemas.common import PaginatedData
from app.schemas.renshe import (
    RensheRefundCreate,
    RensheRefundDecision,
    RensheRefundResponse,
)
from app.utils.audit import redact_sensitive_text


ACTIVE_REFUND_STATUSES = ("requested", "approved", "processing", "failed")
SYSTEM_REFUND_KINDS = ("batch_cancel", "batch_finalize")
SUBMITTABLE_REFUND_STATUSES = ("requested", "approved", "failed")


@dataclass(frozen=True, slots=True)
class PreparedRensheRefund:
    refund_id: int
    out_refund_no: str
    out_trade_no: str
    transaction_id: str
    amount_cents: int


@dataclass(frozen=True, slots=True)
class RensheRefundApplyResult:
    refund: RensheRefundResponse
    processed: bool

    @property
    def refund_id(self) -> int:
        return self.refund.id

    @property
    def status(self) -> str:
        return self.refund.status


class RensheRefundService:
    def __init__(self, wechat_pay: WechatPayClient | None = None) -> None:
        self.wechat_pay = wechat_pay or WechatPayClient()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _out_refund_no(refund_id: int) -> str:
        """Build a stable V3 merchant refund number no longer than 32 chars."""

        return f"RSRF{refund_id:028d}"

    @staticmethod
    def _user_response(refund: RensheRefundRequest) -> RensheRefundResponse:
        """Hide provider diagnostics from the user-facing refund contract."""

        payload = RensheRefundResponse.model_validate(refund)
        payload.last_error = None
        return payload

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
                return self._user_response(existing)

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
                retry_count=0,
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
                self._audit(
                    actor_type="user",
                    actor_id=user_id,
                    action="refund.request",
                    refund=refund,
                    application=application,
                    result="succeeded",
                    summary={"kind": data.request_kind, "amount_cents": order.price},
                )
            )
            await db.commit()
            await db.refresh(refund)
            return self._user_response(refund)

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
            return self._user_response(refund)

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
        if data.decision == "rejected":
            return await self._reject(
                super_admin_id=super_admin_id,
                refund_id=refund_id,
                reason=data.reason or "",
            )

        self.wechat_pay.ensure_refund_configured()
        prepared = await self._prepare_submission(
            refund_id=refund_id,
            actor_type="admin",
            actor_id=super_admin_id,
            system_only=False,
        )
        if isinstance(prepared, RensheRefundResponse):
            return prepared
        return await self._submit_prepared(prepared)

    async def submit_system_refund(self, refund_id: int) -> RensheRefundResponse:
        """Submit an automatic cancel/finalize refund without human approval."""

        self.wechat_pay.ensure_refund_configured()
        prepared = await self._prepare_submission(
            refund_id=refund_id,
            actor_type="system",
            actor_id=None,
            system_only=True,
        )
        if isinstance(prepared, RensheRefundResponse):
            return prepared
        return await self._submit_prepared(prepared)

    async def reconcile_refund(self, refund_id: int) -> RensheRefundResponse:
        """Restart-safe entry point used by the refund reconciliation worker."""

        async with get_db_ctx() as db:
            refund = await db.get(RensheRefundRequest, refund_id)
            if refund is None:
                raise NotFoundException("退款申请")
            status = refund.status
            request_kind = refund.request_kind
            response = RensheRefundResponse.model_validate(refund)
        if status == "requested" and request_kind in SYSTEM_REFUND_KINDS:
            return await self.submit_system_refund(refund_id)
        if status == "approved":
            self.wechat_pay.ensure_refund_configured()
            prepared = await self._prepare_submission(
                refund_id=refund_id,
                actor_type="system",
                actor_id=None,
                system_only=False,
            )
            if isinstance(prepared, RensheRefundResponse):
                return prepared
            return await self._submit_prepared(prepared)
        if status == "processing":
            return await self.sync_refund(refund_id)
        return response

    async def sync_refund(self, refund_id: int) -> RensheRefundResponse:
        """Query one stable refund number and apply the signed provider result."""

        self.wechat_pay.ensure_refund_configured()
        async with get_db_ctx() as db:
            refund = await db.get(RensheRefundRequest, refund_id)
            if refund is None:
                raise NotFoundException("退款申请")
            if refund.status == "succeeded":
                return RensheRefundResponse.model_validate(refund)
            if refund.status != "processing" or not refund.out_refund_no:
                raise ConflictException("当前退款状态不能主动查询")
            out_refund_no = refund.out_refund_no
        try:
            payload = await self.wechat_pay.query_refund(
                out_refund_no=out_refund_no
            )
            provider_refund = WechatPayRefund.from_payload(payload)
        except Exception as exc:
            await self._record_query_failure(refund_id, exc)
            raise
        applied = await self._apply_provider_result(
            provider_refund,
            source="query",
            expected_refund_id=refund_id,
            allow_success=True,
        )
        return applied.refund

    async def handle_callback_raw(
        self,
        *,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> RensheRefundApplyResult:
        provider_refund = self.wechat_pay.parse_refund_notification(
            headers=headers,
            raw_body=raw_body,
        )
        return await self._apply_provider_result(
            provider_refund,
            source="notification",
            expected_refund_id=None,
            allow_success=True,
        )

    async def _prepare_submission(
        self,
        *,
        refund_id: int,
        actor_type: str,
        actor_id: int | None,
        system_only: bool,
    ) -> PreparedRensheRefund | RensheRefundResponse:
        """Persist approval and the immutable merchant number before I/O."""

        async with get_db_ctx() as db:
            refund = await db.scalar(
                select(RensheRefundRequest)
                .where(RensheRefundRequest.id == refund_id)
                .with_for_update()
            )
            if refund is None:
                raise NotFoundException("退款申请")
            if system_only and refund.request_kind not in SYSTEM_REFUND_KINDS:
                raise ConflictException("用户退款必须由超级管理员批准")
            if refund.status in {"processing", "succeeded"}:
                return RensheRefundResponse.model_validate(refund)
            if refund.status not in SUBMITTABLE_REFUND_STATUSES:
                raise ConflictException("当前退款状态不能批准或重试")

            order = await db.scalar(
                select(Order)
                .where(Order.id == refund.order_id)
                .with_for_update()
            )
            application = await db.scalar(
                select(RensheApplication)
                .where(RensheApplication.id == refund.application_id)
                .with_for_update()
            )
            if order is None:
                raise NotFoundException("退款关联订单")
            if application is None:
                raise NotFoundException("人社报名")
            if (
                order.application_id != refund.application_id
                or order.user_id != refund.user_id
                or application.user_id != refund.user_id
            ):
                raise ConflictException("退款、订单和报名关联不一致")
            if order.status not in {"paid", "completed"}:
                raise ConflictException("订单不是可退款的已支付状态")
            if not order.out_trade_no or not order.transaction_id or not order.paid_at:
                raise ConflictException("订单缺少微信支付成功凭证")
            if order.price <= 0 or refund.amount_cents != order.price:
                raise ConflictException("退款金额与订单实付价格快照不一致")

            previous_status = refund.status
            had_number = bool(refund.out_refund_no)
            if not refund.out_refund_no:
                refund.out_refund_no = self._out_refund_no(refund.id)
            now = self._now()
            refund.status = "approved"
            if refund.decided_at is None:
                refund.decided_at = now
            if actor_type == "admin":
                refund.approved_by_admin_id = actor_id
            if had_number or previous_status in {"approved", "failed"}:
                refund.retry_count += 1
            refund.last_error = None
            db.add(
                self._audit(
                    actor_type=actor_type,
                    actor_id=actor_id,
                    action=("refund.retry" if had_number else "refund.approved"),
                    refund=refund,
                    application=application,
                    result="succeeded",
                    summary={
                        "amount_cents": refund.amount_cents,
                        "request_kind": refund.request_kind,
                        "retry_count": refund.retry_count,
                    },
                )
            )
            prepared = PreparedRensheRefund(
                refund_id=refund.id,
                out_refund_no=refund.out_refund_no,
                out_trade_no=order.out_trade_no,
                transaction_id=order.transaction_id,
                amount_cents=refund.amount_cents,
            )
            await db.commit()
            return prepared

    async def _submit_prepared(
        self, prepared: PreparedRensheRefund
    ) -> RensheRefundResponse:
        """Perform network I/O after approval has committed, then reconcile."""

        try:
            payload = await self.wechat_pay.refund(
                out_trade_no=prepared.out_trade_no,
                out_refund_no=prepared.out_refund_no,
                amount_total=prepared.amount_cents,
                refund_amount=prepared.amount_cents,
                reason="人社报名全额退款",
                notify_url=self.wechat_pay.refund_notify_url,
            )
            provider_refund = WechatPayRefund.from_payload(payload)
        except WechatPayResultUnknownError as exc:
            return await self._record_submission_failure(
                prepared.refund_id,
                exc,
                result_unknown=True,
            )
        except WechatPayAPIError as exc:
            await self._record_submission_failure(
                prepared.refund_id,
                exc,
                result_unknown=False,
            )
            raise
        except Exception as exc:
            # A signed but malformed response is not proof that the refund was
            # rejected.  Keep it queryable under the same merchant number.
            await self._record_submission_failure(
                prepared.refund_id,
                exc,
                result_unknown=True,
            )
            raise

        applied = await self._apply_provider_result(
            provider_refund,
            source="submit",
            expected_refund_id=prepared.refund_id,
            # The submit response only proves provider acceptance.  Even if it
            # already says SUCCESS, callback/query performs the final transition.
            allow_success=False,
        )
        return applied.refund

    async def _reject(
        self,
        *,
        super_admin_id: int,
        refund_id: int,
        reason: str,
    ) -> RensheRefundResponse:
        async with get_db_ctx() as db:
            refund = await db.scalar(
                select(RensheRefundRequest)
                .where(RensheRefundRequest.id == refund_id)
                .with_for_update()
            )
            if refund is None:
                raise NotFoundException("退款申请")
            if refund.request_kind in SYSTEM_REFUND_KINDS:
                raise ConflictException("批次取消或终结产生的系统退款不能驳回")
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
            refund.rejection_reason = reason.strip()
            application.frozen_at = None
            application.freeze_reason = None
            await db.flush()
            await self._rebase_cleanup_if_refunds_resolved(
                db,
                plan_id=application.plan_id,
                resolved_at=now,
            )
            db.add(
                self._audit(
                    actor_type="admin",
                    actor_id=super_admin_id,
                    action="refund.rejected",
                    refund=refund,
                    application=application,
                    result="succeeded",
                    summary={"decision": "rejected"},
                )
            )
            await db.commit()
            await db.refresh(refund)
            return RensheRefundResponse.model_validate(refund)

    async def _apply_provider_result(
        self,
        provider_refund: WechatPayRefund,
        *,
        source: str,
        expected_refund_id: int | None,
        allow_success: bool,
    ) -> RensheRefundApplyResult:
        """The single row-locked transaction shared by callback and query."""

        async with get_db_ctx() as db:
            filters = [
                RensheRefundRequest.out_refund_no == provider_refund.out_refund_no
            ]
            if expected_refund_id is not None:
                filters.append(RensheRefundRequest.id == expected_refund_id)
            refund = await db.scalar(
                select(RensheRefundRequest).where(*filters).with_for_update()
            )
            if refund is None:
                raise NotFoundException("退款申请")
            order = await db.scalar(
                select(Order).where(Order.id == refund.order_id).with_for_update()
            )
            application = await db.scalar(
                select(RensheApplication)
                .where(RensheApplication.id == refund.application_id)
                .with_for_update()
            )
            if order is None:
                raise NotFoundException("退款关联订单")
            if application is None:
                raise NotFoundException("人社报名")
            self._validate_provider_result(refund, order, provider_refund)
            duplicate_provider_refund = await db.scalar(
                select(RensheRefundRequest.id)
                .where(
                    RensheRefundRequest.wechat_refund_id
                    == provider_refund.refund_id,
                    RensheRefundRequest.id != refund.id,
                )
                .with_for_update()
                .limit(1)
            )
            if duplicate_provider_refund is not None:
                raise ConflictException("微信退款单号已绑定其他退款申请")
            if (
                refund.wechat_refund_id
                and refund.wechat_refund_id != provider_refund.refund_id
            ):
                raise ConflictException("微信退款单号与已有记录不一致")

            now = self._now()
            before_status = refund.status
            processed = False
            metadata_changed = False
            if provider_refund.status == "SUCCESS" and allow_success:
                if refund.status == "rejected":
                    raise ConflictException("已驳回退款不能接收微信成功结果")
                if refund.status != "succeeded":
                    if order.status not in {"paid", "completed", "refunded"}:
                        raise ConflictException("订单状态不允许确认退款成功")
                    refund.status = "succeeded"
                    refund.wechat_refund_id = provider_refund.refund_id
                    refund.succeeded_at = provider_refund.success_time or now
                    refund.last_error = None
                    if order.status in {"paid", "completed"}:
                        apply_order_status_transition(order, "refunded")
                    if application.status != "closed":
                        application.status = "closed"
                        application.closed_at = now
                        application.close_reason = "refund_succeeded"
                    application.frozen_at = None
                    application.freeze_reason = None
                    await db.flush()
                    await self._rebase_cleanup_if_refunds_resolved(
                        db,
                        plan_id=application.plan_id,
                        resolved_at=now,
                    )
                    processed = True
            elif provider_refund.status in {"PROCESSING", "SUCCESS"}:
                if refund.status == "rejected":
                    raise ConflictException("已驳回退款不能进入处理中")
                if refund.status != "succeeded":
                    refund.status = "processing"
                    if refund.wechat_refund_id != provider_refund.refund_id:
                        refund.wechat_refund_id = provider_refund.refund_id
                        metadata_changed = True
                    if refund.processing_at is None:
                        refund.processing_at = now
                        metadata_changed = True
                    if refund.last_error is not None:
                        refund.last_error = None
                        metadata_changed = True
                    processed = before_status != "processing"
            else:
                # A delayed failure must never roll a confirmed success back.
                if refund.status != "succeeded":
                    refund.status = "failed"
                    if refund.wechat_refund_id != provider_refund.refund_id:
                        refund.wechat_refund_id = provider_refund.refund_id
                        metadata_changed = True
                    provider_error = f"WechatRefundStatus:{provider_refund.status}"
                    if refund.last_error != provider_error:
                        refund.last_error = provider_error
                        metadata_changed = True
                    processed = before_status != "failed"

            if processed or metadata_changed:
                result = (
                    "succeeded"
                    if refund.status in {"processing", "succeeded"}
                    else "failed"
                )
                db.add(
                    self._audit(
                        actor_type="system",
                        actor_id=None,
                        action=f"refund.{source}",
                        refund=refund,
                        application=application,
                        result=result,
                        summary={
                            "provider_status": provider_refund.status,
                            "status": refund.status,
                            "amount_cents": refund.amount_cents,
                        },
                    )
                )
                await db.commit()
                await db.refresh(refund)
            response = RensheRefundResponse.model_validate(refund)
            return RensheRefundApplyResult(refund=response, processed=processed)

    def _validate_provider_result(
        self,
        refund: RensheRefundRequest,
        order: Order,
        provider_refund: WechatPayRefund,
    ) -> None:
        if refund.order_id != order.id or refund.application_id != order.application_id:
            raise ConflictException("退款、订单和报名关联不一致")
        if not order.out_trade_no or provider_refund.out_trade_no != order.out_trade_no:
            raise ConflictException("微信退款结果的商户订单号不一致")
        if not order.transaction_id or provider_refund.transaction_id != order.transaction_id:
            raise ConflictException("微信退款结果的交易号不一致")
        if provider_refund.mchid and provider_refund.mchid != self.wechat_pay.mch_id:
            raise ConflictException("微信退款结果的商户号不一致")
        if provider_refund.currency != WECHAT_PAY_CURRENCY:
            raise ConflictException("微信退款结果的币种不一致")
        if (
            order.price <= 0
            or refund.amount_cents != order.price
            or provider_refund.amount_total != order.price
            or provider_refund.amount_refund != order.price
        ):
            raise ConflictException("微信退款结果的全额退款金额不一致")

    async def _record_submission_failure(
        self,
        refund_id: int,
        exc: Exception,
        *,
        result_unknown: bool,
    ) -> RensheRefundResponse:
        safe_error = self._safe_error(exc)
        async with get_db_ctx() as db:
            refund = await db.scalar(
                select(RensheRefundRequest)
                .where(RensheRefundRequest.id == refund_id)
                .with_for_update()
            )
            if refund is None:
                raise NotFoundException("退款申请")
            if refund.status == "succeeded":
                return RensheRefundResponse.model_validate(refund)
            refund.status = "processing" if result_unknown else "failed"
            refund.processing_at = refund.processing_at or (
                self._now() if result_unknown else None
            )
            refund.last_error = safe_error
            application = await db.get(RensheApplication, refund.application_id)
            db.add(
                self._audit(
                    actor_type="system",
                    actor_id=None,
                    action="refund.submit",
                    refund=refund,
                    application=application,
                    result="failed",
                    summary={
                        "error_type": type(exc).__name__,
                        "result_unknown": result_unknown,
                    },
                )
            )
            await db.commit()
            await db.refresh(refund)
            return RensheRefundResponse.model_validate(refund)

    async def _record_query_failure(self, refund_id: int, exc: Exception) -> None:
        async with get_db_ctx() as db:
            refund = await db.scalar(
                select(RensheRefundRequest)
                .where(RensheRefundRequest.id == refund_id)
                .with_for_update()
            )
            if refund is None or refund.status == "succeeded":
                return
            refund.last_error = self._safe_error(exc)
            application = await db.get(RensheApplication, refund.application_id)
            db.add(
                self._audit(
                    actor_type="system",
                    actor_id=None,
                    action="refund.query",
                    refund=refund,
                    application=application,
                    result="failed",
                    summary={"error_type": type(exc).__name__},
                )
            )
            await db.commit()

    async def _rebase_cleanup_if_refunds_resolved(
        self,
        db,
        *,
        plan_id: int,
        resolved_at: datetime,
    ) -> int:
        active_count = await db.scalar(
            select(func.count())
            .select_from(RensheRefundRequest)
            .join(
                RensheApplication,
                RensheApplication.id == RensheRefundRequest.application_id,
            )
            .where(
                RensheApplication.plan_id == plan_id,
                RensheRefundRequest.status.in_(ACTIVE_REFUND_STATUSES),
            )
        )
        if active_count:
            return 0
        runs = (
            await db.execute(
                select(RensheCleanupRun)
                .where(
                    RensheCleanupRun.plan_id == plan_id,
                    RensheCleanupRun.status == "paused",
                    RensheCleanupRun.paused_reason == "active_refunds",
                )
                .order_by(RensheCleanupRun.id)
                .with_for_update()
            )
        ).scalars().all()
        if not runs:
            return 0
        due_at = resolved_at + timedelta(
            days=settings.RENSHE_CLEANUP_RETENTION_DAYS
        )
        plan = await db.scalar(select(Plan).where(Plan.id == plan_id).with_for_update())
        if plan is not None:
            plan.cleanup_due_at = due_at
        for run in runs:
            run.status = "scheduled"
            run.paused_reason = None
            run.due_at = due_at
            run.rebase_count += 1
            db.add(
                RensheAuditLog(
                    actor_type="system",
                    actor_id=None,
                    action="cleanup.rebase",
                    object_type="cleanup_run",
                    object_id=run.id,
                    result="succeeded",
                    summary={
                        "plan_id": plan_id,
                        "rebase_count": run.rebase_count,
                        "reason": "refunds_resolved",
                    },
                )
            )
        return len(runs)

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        return redact_sensitive_text(
            f"{type(exc).__name__}: {str(exc)[:1000]}"
        ) or type(exc).__name__

    @staticmethod
    def _audit(
        *,
        actor_type: str,
        actor_id: int | None,
        action: str,
        refund: RensheRefundRequest,
        application: RensheApplication | None,
        result: str,
        summary: dict[str, Any],
    ) -> RensheAuditLog:
        return RensheAuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            object_type="refund",
            object_id=refund.id,
            application_id=refund.application_id,
            version_id=(application.current_version_id if application else None),
            result=result,
            summary=summary,
        )


__all__ = [
    "ACTIVE_REFUND_STATUSES",
    "PreparedRensheRefund",
    "RensheRefundApplyResult",
    "RensheRefundService",
    "SYSTEM_REFUND_KINDS",
]
