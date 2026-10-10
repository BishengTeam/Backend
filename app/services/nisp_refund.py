"""NISP refund service — modeled after H3cRefundService."""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from app.adapter.database import get_db_ctx
from app.domain.nisp.src import NispRefundRequest, NispRegistration
from app.domain.order.src.index import Order, apply_order_status_transition
from app.integrations.wechat_pay import WechatPayClient, WechatPayRefund, WechatPayAPIError
from app.port.exceptions import ConflictException, NotFoundException
from app.schemas.nisp import NispRefundResponse
from app.port.config import settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _out_refund_no(refund_id: int) -> str:
    return f"NISP-RF-{refund_id:08d}"


@dataclass(frozen=True, slots=True)
class _PreparedRefund:
    refund_id: int
    order_id: int
    out_trade_no: str
    out_refund_no: str
    amount_total: int
    amount_cents: int


class NispRefundService:

    def __init__(self, wechat_pay: WechatPayClient | None = None) -> None:
        self.wechat_pay = wechat_pay or WechatPayClient()

    @staticmethod
    async def _lock_refund_chain(db, refund_id: int):
        """Lock Order -> Registration -> Refund without holding locks over HTTP."""
        unresolved = await db.get(NispRefundRequest, refund_id)
        if unresolved is None:
            raise NotFoundException("NISP 退款任务")
        order = await db.scalar(
            select(Order).where(Order.id == unresolved.order_id).with_for_update()
        )
        registration = await db.scalar(
            select(NispRegistration)
            .where(NispRegistration.id == unresolved.registration_id)
            .with_for_update()
        )
        refund = await db.scalar(
            select(NispRefundRequest)
            .where(NispRefundRequest.id == refund_id)
            .with_for_update()
        )
        if order is None or registration is None or refund is None:
            raise NotFoundException("NISP 退款关联记录")
        return refund, order, registration

    async def list_refunds(
        self,
        *,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[NispRefundResponse], int]:
        from sqlalchemy import func
        async with get_db_ctx() as db:
            base = select(NispRefundRequest)
            if status:
                base = base.where(NispRefundRequest.status == status)
            total = await db.scalar(
                select(func.count()).select_from(base.subquery())
            ) or 0
            rows = (
                await db.execute(
                    base.order_by(NispRefundRequest.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return [self._response(r) for r in rows], total

    async def confirm(
        self,
        *,
        admin_id: int,
        refund_id: int,
    ) -> NispRefundResponse:
        prepared = await self._prepare(admin_id=admin_id, refund_id=refund_id)
        if isinstance(prepared, NispRefundResponse):
            return prepared
        if prepared.amount_cents == 0:
            return await self._complete_zero_refund(refund_id)
        return await self._submit(prepared)

    async def _prepare(
        self,
        *,
        admin_id: int,
        refund_id: int,
    ) -> _PreparedRefund | NispRefundResponse:
        async with get_db_ctx() as db:
            refund, order, registration = await self._lock_refund_chain(db, refund_id)
            if refund.status in {"processing", "succeeded"}:
                return self._response(refund)
            if refund.status not in {"requested", "approved", "failed"}:
                raise ConflictException("当前 NISP 退款状态不能确认")
            if order is None or registration is None:
                raise NotFoundException("NISP 退款关联记录")
            if registration.order_id != order.id or order.user_id != refund.user_id:
                raise ConflictException("NISP 退款、订单和报名关联不一致")
            if refund.amount_cents != order.price:
                raise ConflictException("退款金额与订单价格不一致")

            if refund.amount_cents > 0:
                if order.status not in {"paid", "completed"}:
                    raise ConflictException("NISP 订单不是可退款状态")
                if not order.out_trade_no or not order.transaction_id or not order.paid_at:
                    raise ConflictException("NISP 订单缺少微信支付凭证")

            if not refund.out_refund_no:
                refund.out_refund_no = _out_refund_no(refund.id)
            refund.status = "approved"
            refund.approved_by_admin_id = admin_id
            refund.approved_at = _now()
            refund.last_error = None
            if refund.requested_by_admin_id is None:
                refund.requested_by_admin_id = admin_id
            await db.commit()

            return _PreparedRefund(
                refund_id=refund.id,
                order_id=order.id,
                out_trade_no=order.out_trade_no or "",
                out_refund_no=refund.out_refund_no,
                amount_total=order.price,
                amount_cents=refund.amount_cents,
            )

    async def _complete_zero_refund(self, refund_id: int) -> NispRefundResponse:
        async with get_db_ctx() as db:
            refund, order, registration = await self._lock_refund_chain(db, refund_id)
            now = _now()
            if order is not None and order.status in {"paid", "completed"}:
                apply_order_status_transition(order, "refunded")
            if registration is not None:
                registration.status = "refunded_closed"
                registration.closed_at = now
                registration.close_reason = "refund_completed"
            refund.status = "succeeded"
            refund.succeeded_at = now
            await db.commit()
            await db.refresh(refund)
            return self._response(refund)

    async def _submit(self, prepared: _PreparedRefund) -> NispRefundResponse:
        async with get_db_ctx() as db:
            refund, _order, registration = await self._lock_refund_chain(
                db, prepared.refund_id
            )
            if refund.status == "succeeded":
                return self._response(refund)
            refund.status = "processing"
            refund.processing_at = _now()
            if registration is not None:
                registration.status = "refund_processing"
            await db.commit()

        try:
            raw = await self.wechat_pay.refund(
                out_trade_no=prepared.out_trade_no,
                out_refund_no=prepared.out_refund_no,
                amount_total=prepared.amount_total,
                refund_amount=prepared.amount_cents,
                notify_url=settings.WECHAT_PAY_REFUND_NOTIFY_URL,
            )
        except Exception as exc:
            async with get_db_ctx() as db:
                refund, _order, _registration = await self._lock_refund_chain(
                    db, prepared.refund_id
                )
                if refund is not None and refund.status != "succeeded":
                    # A transport failure does not prove WeChat rejected the
                    # refund. Keep the merchant number and reconcile it.
                    refund.status = "failed" if isinstance(exc, WechatPayAPIError) else "processing"
                    refund.last_error = type(exc).__name__
                    refund.retry_count += 1
                    await db.commit()
            raise ConflictException("微信退款结果暂未确认，系统将自动对账") from exc

        result = WechatPayRefund.from_payload(raw)
        if (
            result.out_trade_no != prepared.out_trade_no
            or result.out_refund_no != prepared.out_refund_no
            or result.amount_total != prepared.amount_total
            or result.amount_refund != prepared.amount_cents
        ):
            raise ConflictException("微信退款提交结果与 NISP 退款单不一致")
        return await self._apply_provider_result(
            refund_id=prepared.refund_id,
            status=result.status,
            refund_id_wechat=result.refund_id,
        )

    async def reconcile(self, refund_id: int) -> NispRefundResponse:
        async with get_db_ctx() as db:
            refund = await db.scalar(
                select(NispRefundRequest)
                .where(NispRefundRequest.id == refund_id)
            )
            if refund is None:
                raise NotFoundException("NISP 退款任务")
            if refund.status not in {"approved", "processing", "failed"} or not refund.out_refund_no:
                return self._response(refund)
            order = await db.get(Order, refund.order_id)
            expected_out_trade_no = order.out_trade_no if order else None
            expected_amount_total = order.price if order else None
            expected_amount_refund = refund.amount_cents
            approved_by = refund.approved_by_admin_id
            if approved_by is None:
                raise ConflictException("退款尚未经管理员确认")
            prepared = _PreparedRefund(refund.id, refund.order_id,
                                       expected_out_trade_no or "", refund.out_refund_no,
                                       expected_amount_total or 0, expected_amount_refund)

        if expected_amount_refund == 0:
            return await self._complete_zero_refund(refund_id)
        try:
            raw = await self.wechat_pay.query_refund(out_refund_no=refund.out_refund_no)
        except WechatPayAPIError as exc:
            if exc.api_code not in {"RESOURCE_NOT_EXISTS", "REFUND_NOT_EXIST"}:
                raise
            # Confirmed locally but never accepted remotely (including a crash
            # before submission): retry the same idempotent merchant number.
            return await self._submit(prepared)
        result = WechatPayRefund.from_payload(raw)
        if (
            result.out_trade_no != expected_out_trade_no
            or result.out_refund_no != refund.out_refund_no
            or result.amount_total != expected_amount_total
            or result.amount_refund != expected_amount_refund
        ):
            raise ConflictException("微信退款查询结果与 NISP 退款单不一致")
        return await self._apply_provider_result(
            refund_id=refund.id,
            status=result.status,
            refund_id_wechat=result.refund_id,
        )

    async def handle_callback_raw(
        self,
        *,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> NispRefundResponse:
        provider_refund = self.wechat_pay.parse_refund_notification(
            headers=headers,
            raw_body=raw_body,
        )
        async with get_db_ctx() as db:
            refund = await db.scalar(
                select(NispRefundRequest)
                .where(NispRefundRequest.out_refund_no == provider_refund.out_refund_no)
                .limit(1)
            )
            if refund is None:
                raise NotFoundException("NISP 退款任务")
            order = await db.get(Order, refund.order_id)
            if (order is None or provider_refund.out_trade_no != order.out_trade_no
                    or provider_refund.amount_total != order.price
                    or provider_refund.amount_refund != refund.amount_cents):
                raise ConflictException("微信退款通知与 NISP 订单不一致")
            refund_id = refund.id

        return await self._apply_provider_result(
            refund_id=refund_id,
            status=provider_refund.status,
            refund_id_wechat=provider_refund.refund_id,
        )

    async def _apply_provider_result(
        self,
        *,
        refund_id: int,
        status: str,
        refund_id_wechat: str,
    ) -> NispRefundResponse:
        async with get_db_ctx() as db:
            refund, order, registration = await self._lock_refund_chain(
                db, refund_id
            )

            if refund.status == "succeeded":
                return self._response(refund)

            now = _now()
            refund.wechat_refund_id = refund_id_wechat

            if status == "SUCCESS":
                refund.status = "succeeded"
                refund.succeeded_at = now
                refund.last_error = None
                if order is not None and order.status in {"paid", "completed"}:
                    apply_order_status_transition(order, "refunded")
                if registration is not None:
                    registration.status = "refunded_closed"
                    registration.closed_at = now
                    registration.close_reason = "refund_completed"
            elif status in {"ABNORMAL", "CLOSED"}:
                refund.status = "failed"
                refund.last_error = f"provider status: {status}"
            elif status == "PROCESSING":
                refund.status = "processing"
                if registration is not None:
                    registration.status = "refund_processing"

            await db.commit()
            await db.refresh(refund)
            return self._response(refund)

    @staticmethod
    def _response(refund: NispRefundRequest) -> NispRefundResponse:
        return NispRefundResponse(
            id=refund.id,
            registration_id=refund.registration_id,
            order_id=refund.order_id,
            request_kind=refund.request_kind,
            reason_code=refund.reason_code,
            reason_detail=refund.reason_detail,
            amount_cents=refund.amount_cents,
            status=refund.status,
            requested_by_admin_id=refund.requested_by_admin_id,
            requested_at=refund.requested_at,
            approved_by_admin_id=refund.approved_by_admin_id,
            approved_at=refund.approved_at,
            out_refund_no=refund.out_refund_no,
            processing_at=refund.processing_at,
            succeeded_at=refund.succeeded_at,
            last_error=refund.last_error,
            retry_count=refund.retry_count,
        )
