"""H3C full-refund workflow backed by WeChat Pay API V3."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.domain.h3c.src.index import H3cRefundRequest, H3cRegistration
from app.domain.order.src.index import (
    Order,
    apply_order_status_transition,
    refund_inventory_sale,
)
from app.integrations.wechat_pay import (
    WECHAT_PAY_CURRENCY,
    WechatPayAPIError,
    WechatPayClient,
    WechatPayRefund,
    WechatPayResultUnknownError,
)
from app.port.exceptions import ConflictException, NotFoundException
from app.schemas.common import PaginatedData
from app.schemas.h3c_registration import H3cRefundResponse


@dataclass(frozen=True, slots=True)
class _PreparedRefund:
    refund_id: int
    out_refund_no: str
    out_trade_no: str
    transaction_id: str
    amount_cents: int


class H3cRefundService:
    def __init__(self, wechat_pay: WechatPayClient | None = None) -> None:
        self.wechat_pay = wechat_pay or WechatPayClient()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _out_refund_no(refund_id: int) -> str:
        return f"H3RF{refund_id:028d}"

    @staticmethod
    def _response(refund: H3cRefundRequest, *, hide_error: bool = True) -> H3cRefundResponse:
        result = H3cRefundResponse.model_validate(refund)
        if hide_error:
            result.last_error = None
        return result

    async def list_refunds(
        self,
        *,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedData[H3cRefundResponse]:
        async with get_db_ctx() as db:
            stmt = select(H3cRefundRequest)
            if status:
                stmt = stmt.where(H3cRefundRequest.status == status)
            total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
            rows = (
                await db.execute(
                    stmt.order_by(H3cRefundRequest.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()
            return PaginatedData(
                items=[self._response(row, hide_error=False) for row in rows],
                total=int(total),
                page=page,
                page_size=page_size,
            )

    async def confirm(
        self,
        *,
        admin_id: int,
        refund_id: int,
    ) -> H3cRefundResponse:
        prepared = await self._prepare(admin_id=admin_id, refund_id=refund_id)
        if isinstance(prepared, H3cRefundResponse):
            return prepared
        if prepared.amount_cents == 0:
            return await self._complete_zero_refund(refund_id)
        return await self._submit(prepared)

    async def _prepare(
        self,
        *,
        admin_id: int,
        refund_id: int,
    ) -> _PreparedRefund | H3cRefundResponse:
        async with get_db_ctx() as db:
            refund = await db.scalar(
                select(H3cRefundRequest)
                .where(H3cRefundRequest.id == refund_id)
                .with_for_update()
            )
            if refund is None:
                raise NotFoundException("H3C 退款任务")
            if refund.status in {"processing", "succeeded"}:
                return self._response(refund)
            if refund.status not in {"requested", "failed"}:
                raise ConflictException("当前 H3C 退款状态不能确认")
            order = await db.scalar(
                select(Order).where(Order.id == refund.order_id).with_for_update()
            )
            registration = await db.scalar(
                select(H3cRegistration)
                .where(H3cRegistration.id == refund.registration_id)
                .with_for_update()
            )
            if order is None or registration is None:
                raise NotFoundException("H3C 退款关联记录")
            if registration.order_id != order.id or order.user_id != refund.user_id:
                raise ConflictException("H3C 退款、订单和报名关联不一致")
            if refund.amount_cents != order.price:
                raise ConflictException("退款金额与订单价格不一致")
            if refund.amount_cents > 0:
                if order.status not in {"paid", "completed"}:
                    raise ConflictException("H3C 订单不是可退款状态")
                if not order.out_trade_no or not order.transaction_id or not order.paid_at:
                    raise ConflictException("H3C 订单缺少微信支付凭证")
            if not refund.out_refund_no:
                refund.out_refund_no = self._out_refund_no(refund.id)
            refund.status = "approved"
            refund.approved_by_admin_id = admin_id
            refund.approved_at = self._now()
            refund.last_error = None
            if refund.requested_by_admin_id is None:
                refund.requested_by_admin_id = admin_id
            await db.commit()
            return _PreparedRefund(
                refund_id=refund.id,
                out_refund_no=refund.out_refund_no,
                out_trade_no=order.out_trade_no or "",
                transaction_id=order.transaction_id or "",
                amount_cents=refund.amount_cents,
            )

    async def _complete_zero_refund(self, refund_id: int) -> H3cRefundResponse:
        async with get_db_ctx() as db:
            refund = await db.scalar(
                select(H3cRefundRequest)
                .where(H3cRefundRequest.id == refund_id)
                .with_for_update()
            )
            if refund is None:
                raise NotFoundException("H3C 退款任务")
            order = await db.scalar(
                select(Order).where(Order.id == refund.order_id).with_for_update()
            )
            registration = await db.scalar(
                select(H3cRegistration)
                .where(H3cRegistration.id == refund.registration_id)
                .with_for_update()
            )
            if order is None or registration is None:
                raise NotFoundException("H3C 退款关联记录")
            now = self._now()
            refund.status = "succeeded"
            refund.succeeded_at = now
            if order.status in {"paid", "completed"}:
                apply_order_status_transition(order, "refunded")
            await refund_inventory_sale(db, order, reason="h3c_zero_price_refund")
            registration.status = "refunded_closed"
            registration.closed_at = now
            registration.close_reason = "refund_succeeded"
            await db.commit()
            await db.refresh(refund)
            return self._response(refund)

    async def _submit(self, prepared: _PreparedRefund) -> H3cRefundResponse:
        try:
            payload = await self.wechat_pay.refund(
                out_trade_no=prepared.out_trade_no,
                out_refund_no=prepared.out_refund_no,
                amount_total=prepared.amount_cents,
                refund_amount=prepared.amount_cents,
                reason="H3C 报名全额退款",
                notify_url=self.wechat_pay.refund_notify_url,
            )
            provider_refund = WechatPayRefund.from_payload(payload)
        except WechatPayResultUnknownError as exc:
            await self._record_failure(prepared.refund_id, exc, result_unknown=True)
            raise
        except WechatPayAPIError as exc:
            await self._record_failure(prepared.refund_id, exc, result_unknown=False)
            raise
        except Exception as exc:
            await self._record_failure(prepared.refund_id, exc, result_unknown=True)
            raise
        return await self._apply_provider_result(
            provider_refund,
            allow_success=False,
        )

    async def handle_callback_raw(
        self,
        *,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> H3cRefundResponse:
        provider_refund = self.wechat_pay.parse_refund_notification(
            headers=headers,
            raw_body=raw_body,
        )
        return await self._apply_provider_result(provider_refund, allow_success=True)

    async def reconcile(self, refund_id: int) -> H3cRefundResponse:
        async with get_db_ctx() as db:
            refund = await db.get(H3cRefundRequest, refund_id)
            if refund is None:
                raise NotFoundException("H3C 退款任务")
            if refund.status == "failed" and not refund.out_refund_no:
                raise ConflictException("H3C 退款缺少商户退款号")
            out_refund_no = refund.out_refund_no
        if out_refund_no is None:
            raise ConflictException("H3C 退款缺少商户退款号")
        payload = await self.wechat_pay.query_refund(out_refund_no=out_refund_no)
        provider_refund = WechatPayRefund.from_payload(payload, require_mchid=True)
        return await self._apply_provider_result(provider_refund, allow_success=True)

    async def _apply_provider_result(
        self,
        provider_refund: WechatPayRefund,
        *,
        allow_success: bool,
    ) -> H3cRefundResponse:
        async with get_db_ctx() as db:
            refund = await db.scalar(
                select(H3cRefundRequest)
                .where(H3cRefundRequest.out_refund_no == provider_refund.out_refund_no)
                .with_for_update()
            )
            if refund is None:
                raise NotFoundException("H3C 退款任务")
            if not provider_refund.out_refund_no.startswith("H3RF"):
                raise NotFoundException("非 H3C 退款通知")
            order = await db.scalar(
                select(Order).where(Order.id == refund.order_id).with_for_update()
            )
            registration = await db.scalar(
                select(H3cRegistration)
                .where(H3cRegistration.id == refund.registration_id)
                .with_for_update()
            )
            if order is None or registration is None:
                raise NotFoundException("H3C 退款关联记录")
            self._validate_provider_result(refund, order, provider_refund)
            now = self._now()
            if provider_refund.status == "SUCCESS" and allow_success:
                refund.status = "succeeded"
                refund.wechat_refund_id = provider_refund.refund_id
                refund.succeeded_at = provider_refund.success_time or now
                refund.last_error = None
                if order.status in {"paid", "completed"}:
                    apply_order_status_transition(order, "refunded")
                await refund_inventory_sale(db, order, reason="h3c_refund_succeeded")
                registration.status = "refunded_closed"
                registration.closed_at = now
                registration.close_reason = "refund_succeeded"
            elif provider_refund.status in {"PROCESSING", "SUCCESS"}:
                refund.status = "processing"
                refund.wechat_refund_id = provider_refund.refund_id
                refund.processing_at = refund.processing_at or now
                registration.status = "refund_processing"
            else:
                refund.status = "failed"
                refund.wechat_refund_id = provider_refund.refund_id
                refund.last_error = f"WechatRefundStatus:{provider_refund.status}"
                registration.status = "pending_refund_confirmation"
            await db.commit()
            await db.refresh(refund)
            return self._response(refund, hide_error=False)

    def _validate_provider_result(
        self,
        refund: H3cRefundRequest,
        order: Order,
        provider_refund: WechatPayRefund,
    ) -> None:
        if not order.out_trade_no or provider_refund.out_trade_no != order.out_trade_no:
            raise ConflictException("H3C 退款商户订单号不一致")
        if not order.transaction_id or provider_refund.transaction_id != order.transaction_id:
            raise ConflictException("H3C 退款微信交易号不一致")
        if provider_refund.currency != WECHAT_PAY_CURRENCY:
            raise ConflictException("H3C 退款币种不一致")
        if (
            refund.amount_cents != order.price
            or provider_refund.amount_total != order.price
            or provider_refund.amount_refund != order.price
        ):
            raise ConflictException("H3C 退款金额不一致")
        if provider_refund.mchid and provider_refund.mchid != self.wechat_pay.mch_id:
            raise ConflictException("H3C 退款商户号不一致")

    async def _record_failure(
        self,
        refund_id: int,
        exc: Exception,
        *,
        result_unknown: bool,
    ) -> None:
        async with get_db_ctx() as db:
            refund = await db.get(H3cRefundRequest, refund_id)
            if refund is None:
                return
            refund.status = "processing" if result_unknown else "failed"
            refund.last_error = f"{type(exc).__name__}: {exc}"[:2000]
            refund.retry_count += 1
            await db.commit()


async def h3c_refund_reconciliation_worker_loop(
    service: H3cRefundService | None = None,
) -> None:
    active_service = service or H3cRefundService()
    while True:
        async with get_db_ctx() as db:
            refund_ids = (
                await db.execute(
                    select(H3cRefundRequest.id)
                    .where(
                        H3cRefundRequest.status.in_(("approved", "processing", "failed")),
                        H3cRefundRequest.out_refund_no.is_not(None),
                    )
                    .order_by(H3cRefundRequest.id)
                    .limit(50)
                )
            ).scalars().all()
        for refund_id in refund_ids:
            try:
                await active_service.reconcile(refund_id)
            except Exception:
                continue
        import asyncio

        await asyncio.sleep(30)
