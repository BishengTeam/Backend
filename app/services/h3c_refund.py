"""H3C full-refund workflow backed by WeChat Pay API V3."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select

from app.adapter.database import get_db_ctx
from app.domain.h3c.src.index import H3cRefundRequest, H3cRegistration
from app.domain.plan.src.index import Plan
from app.domain.order.src.index import (
    Order,
    apply_order_status_transition,
    refund_inventory_sale,
)
from app.models.cert_product import CertProduct
from app.integrations.wechat_pay import (
    WECHAT_PAY_CURRENCY,
    WechatPayAPIError,
    WechatPayClient,
    WechatPayRefund,
    WechatPayResultUnknownError,
)
from app.port.config import settings
from app.port.exceptions import ConflictException, NotFoundException
from app.schemas.common import PaginatedData, PaginatedDataWithCounts
from app.schemas.h3c_registration import H3cRefundResponse
from app.services.refund_recovery import (
    format_refund_error,
    provider_refund_is_missing,
    refund_retry_delay,
)


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

    @staticmethod
    async def _lock_refund_chain(
        db,
        *,
        refund_id: int | None = None,
        out_refund_no: str | None = None,
    ) -> tuple[H3cRefundRequest, Order, H3cRegistration]:
        """Lock Order -> Registration -> Refund in the certification-wide order."""
        filters = []
        if refund_id is not None:
            filters.append(H3cRefundRequest.id == refund_id)
        if out_refund_no is not None:
            filters.append(H3cRefundRequest.out_refund_no == out_refund_no)
        unresolved = await db.scalar(select(H3cRefundRequest).where(*filters))
        if unresolved is None:
            raise NotFoundException("H3C 退款任务")
        order = await db.scalar(
            select(Order).where(Order.id == unresolved.order_id).with_for_update()
        )
        registration = await db.scalar(
            select(H3cRegistration)
            .where(H3cRegistration.id == unresolved.registration_id)
            .with_for_update()
        )
        refund = await db.scalar(
            select(H3cRefundRequest)
            .where(H3cRefundRequest.id == unresolved.id)
            .with_for_update()
        )
        if order is None or registration is None or refund is None:
            raise NotFoundException("H3C 退款关联记录")
        return refund, order, registration

    async def list_refunds(
        self,
        *,
        registration_id: int | None = None,
        batch_id: int | None = None,
        keyword: str | None = None,
        reason_code: str | None = None,
        request_kind: str | None = None,
        requested_at_start: datetime | None = None,
        requested_at_end: datetime | None = None,
        group: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedDataWithCounts[H3cRefundResponse]:
        async with get_db_ctx() as db:
            stmt = (
                select(H3cRefundRequest, H3cRegistration, Plan, Order, CertProduct)
                .join(H3cRegistration, H3cRegistration.id == H3cRefundRequest.registration_id)
                .join(Plan, Plan.id == H3cRegistration.plan_id)
                .join(Order, Order.id == H3cRefundRequest.order_id)
                .outerjoin(CertProduct, CertProduct.code == Plan.product_type)
            )
            if registration_id is not None:
                stmt = stmt.where(H3cRegistration.id == registration_id)
            if batch_id is not None:
                stmt = stmt.where(H3cRegistration.batch_id == batch_id)
            if reason_code:
                stmt = stmt.where(H3cRefundRequest.reason_code.ilike(f"%{reason_code.strip()}%"))
            if request_kind:
                stmt = stmt.where(H3cRefundRequest.request_kind == request_kind)
            if requested_at_start is not None:
                stmt = stmt.where(H3cRefundRequest.requested_at >= requested_at_start)
            if requested_at_end is not None:
                stmt = stmt.where(H3cRefundRequest.requested_at <= requested_at_end)
            if keyword and keyword.strip():
                pattern = f"%{keyword.strip()}%"
                stmt = stmt.where(or_(
                    H3cRegistration.candidate_snapshot["candidate_name"]
                        .as_string()
                        .ilike(pattern),
                    H3cRegistration.registration_no.ilike(pattern),
                    Order.out_trade_no.ilike(pattern),
                    H3cRefundRequest.out_refund_no.ilike(pattern),
                ))
            # Counts describe every status group under the same non-status
            # filters, so tab badges do not disappear when a group is selected.
            stats_subquery = (
                stmt.with_only_columns(
                    H3cRefundRequest.id.label("refund_id"),
                    H3cRefundRequest.status.label("refund_status"),
                )
                .subquery()
            )
            count_rows = (
                await db.execute(
                    select(stats_subquery.c.refund_status, func.count())
                    .group_by(stats_subquery.c.refund_status)
                )
            ).all()

            if status:
                stmt = stmt.where(H3cRefundRequest.status == status)
            elif group == "pending":
                stmt = stmt.where(H3cRefundRequest.status == "requested")
            elif group == "processing":
                stmt = stmt.where(H3cRefundRequest.status.in_(("approved", "processing")))
            elif group == "abnormal":
                stmt = stmt.where(H3cRefundRequest.status == "failed")

            total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
            rows = (
                await db.execute(
                    stmt.order_by(
                        H3cRefundRequest.requested_at.asc(), H3cRefundRequest.id.asc()
                    )
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
            items: list[H3cRefundResponse] = []
            for refund, registration, plan, order, product in rows:
                response = self._response(refund, hide_error=False)
                response.payment_channel = order.payment_channel
                response.out_trade_no = order.out_trade_no
                response.candidate_name = (
                    registration.candidate_snapshot.get("candidate_name")
                    if registration.candidate_snapshot
                    else None
                )
                response.registration_no = registration.registration_no
                response.batch_id = registration.batch_id
                response.batch_name = plan.name
                response.product_label = product.chinese_name or plan.product_type
                items.append(response)
            return PaginatedDataWithCounts(
                items=items,
                total=int(total),
                page=page,
                page_size=page_size,
                status_counts={status_value: int(count) for status_value, count in count_rows},
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
            refund, order, registration = await self._lock_refund_chain(
                db, refund_id=refund_id
            )
            if refund.status in {"processing", "succeeded"}:
                return self._response(refund)
            if refund.status not in {"requested", "failed"}:
                raise ConflictException("当前 H3C 退款状态不能确认")
            if order is None or registration is None:
                raise NotFoundException("H3C 退款关联记录")
            if registration.order_id != order.id or order.user_id != refund.user_id:
                raise ConflictException("H3C 退款、订单和报名关联不一致")
            if refund.amount_cents != order.price:
                raise ConflictException("退款金额与订单价格不一致")
            if getattr(order, "payment_channel", "unknown") == "bank_transfer":
                if order.status not in {"paid", "completed"}:
                    raise ConflictException("订单不是可退款状态")
                refund.status = "approved"
                refund.approved_by_admin_id = admin_id
                refund.approved_at = datetime.now(timezone.utc)
                refund.last_error = None
                await db.commit()
                return self._response(refund)
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
            refund, order, registration = await self._lock_refund_chain(
                db, refund_id=refund_id
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
        return await self._apply_provider_result(provider_refund)

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
        return await self._apply_provider_result(provider_refund)

    async def reconcile(self, refund_id: int) -> H3cRefundResponse:
        async with get_db_ctx() as db:
            refund = await db.get(H3cRefundRequest, refund_id)
            if refund is None:
                raise NotFoundException("H3C 退款任务")
            channel_order = await db.get(Order, refund.order_id)
            if channel_order is not None and getattr(channel_order, "payment_channel", "unknown") == "bank_transfer":
                return self._response(refund)
            if refund.status == "failed" and not refund.out_refund_no:
                raise ConflictException("H3C 退款缺少商户退款号")
            out_refund_no = refund.out_refund_no
        if out_refund_no is None:
            raise ConflictException("H3C 退款缺少商户退款号")
        payload = await self.wechat_pay.query_refund(out_refund_no=out_refund_no)
        provider_refund = WechatPayRefund.from_payload(payload, require_mchid=True)
        return await self._apply_provider_result(provider_refund)

    async def recover(self, refund_id: int) -> H3cRefundResponse:
        """Query an uncertain refund and resubmit a confirmed-missing request."""
        async with get_db_ctx() as db:
            refund = await db.get(H3cRefundRequest, refund_id)
            if refund is None:
                raise NotFoundException("H3C 退款任务")
            channel_order = await db.get(Order, refund.order_id)
            if channel_order is not None and getattr(channel_order, "payment_channel", "unknown") == "bank_transfer":
                return self._response(refund)
            if refund.status == "succeeded":
                return self._response(refund)
            if refund.status not in {"approved", "processing", "failed"}:
                return self._response(refund)
            if not refund.out_refund_no:
                raise ConflictException("H3C 退款缺少商户退款号")
            order = await db.get(Order, refund.order_id)
            if order is None:
                raise NotFoundException("H3C 退款订单")
            prepared = _PreparedRefund(
                refund_id=refund.id,
                out_refund_no=refund.out_refund_no,
                out_trade_no=order.out_trade_no or "",
                transaction_id=order.transaction_id or "",
                amount_cents=refund.amount_cents,
            )

        if prepared.amount_cents == 0:
            return await self._complete_zero_refund(refund_id)
        try:
            payload = await self.wechat_pay.query_refund(
                out_refund_no=prepared.out_refund_no
            )
        except WechatPayAPIError as exc:
            provider_missing = provider_refund_is_missing(exc)
            if provider_missing and prepared.refund_id == refund_id:
                current = await self._refund_status(refund_id)
                if current in {"approved", "failed"}:
                    return await self._submit(prepared)
            await self._record_failure(
                refund_id,
                exc,
                result_unknown=exc.status_code >= 500 or not provider_missing,
            )
            raise
        provider_refund = WechatPayRefund.from_payload(payload, require_mchid=True)
        return await self._apply_provider_result(provider_refund)

    async def _refund_status(self, refund_id: int) -> str | None:
        async with get_db_ctx() as db:
            refund = await db.get(H3cRefundRequest, refund_id)
            return refund.status if refund is not None else None

    async def _apply_provider_result(
        self,
        provider_refund: WechatPayRefund,
    ) -> H3cRefundResponse:
        async with get_db_ctx() as db:
            if not provider_refund.out_refund_no.startswith("H3RF"):
                raise NotFoundException("非 H3C 退款通知")
            refund, order, registration = await self._lock_refund_chain(
                db, out_refund_no=provider_refund.out_refund_no
            )
            if order is None or registration is None:
                raise NotFoundException("H3C 退款关联记录")
            self._validate_provider_result(refund, order, provider_refund)
            if refund.status == "succeeded":
                # A later signed callback/response is idempotent and can never
                # downgrade a successful refund.
                return self._response(refund)
            now = self._now()
            if provider_refund.status == "SUCCESS":
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
            elif provider_refund.status == "PROCESSING":
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
        if getattr(order, "payment_channel", "unknown") == "bank_transfer":
            raise ConflictException("银行转账订单不接受微信退款结果")
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
            refund, _order, _registration = await self._lock_refund_chain(
                db, refund_id=refund_id
            )
            if refund is None:
                return
            if refund.status == "succeeded":
                return
            refund.status = "processing" if result_unknown else "failed"
            refund.last_error = format_refund_error(exc)[:2000]
            refund.retry_count += 1
            if not result_unknown:
                _registration.status = "pending_refund_confirmation"
                _registration.close_reason = "refund_retry_scheduled"
            await db.commit()


async def h3c_refund_reconciliation_worker_loop(
    service: H3cRefundService | None = None,
) -> None:
    active_service = service or H3cRefundService()
    last_refund_id = 0
    while True:
        try:
            async with get_db_ctx() as db:
                candidates = (
                    await db.execute(
                        select(H3cRefundRequest)
                        .where(
                            H3cRefundRequest.status.in_(
                                ("approved", "processing", "failed")
                            ),
                            H3cRefundRequest.out_refund_no.is_not(None),
                            H3cRefundRequest.id > last_refund_id,
                        )
                        .order_by(H3cRefundRequest.id)
                        .limit(settings.WECHAT_PAY_REFUND_RECONCILE_BATCH_SIZE)
                    )
                ).scalars().all()
                now = active_service._now()
                refund_ids = [
                    row.id
                    for row in candidates
                    if row.updated_at is None
                    or row.updated_at
                    <= now
                    - timedelta(seconds=refund_retry_delay(row.retry_count))
                ]
                last_refund_id = candidates[-1].id if candidates else 0
            for refund_id in refund_ids:
                try:
                    await active_service.recover(refund_id)
                except Exception:
                    # A poisoned task must not prevent later tasks from being
                    # scanned; its retry delay is persisted through retry_count.
                    continue
        except Exception:
            last_refund_id = 0

        await asyncio.sleep(settings.WECHAT_PAY_REFUND_RECONCILE_POLL_SECONDS)
