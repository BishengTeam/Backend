from datetime import date as calendar_date
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, or_, literal, union_all, case

from app.adapter.database import get_db_ctx
from app.port.config import settings
from app.port.exceptions import BusinessException, NotFoundException, ThirdPartyException
from app.integrations.wechat_pay import WechatPayClient
from app.domain.h3c.src.index import H3cRegistration
from app.domain.nisp.src.index import NispRegistration
from app.domain.order.src.index import (
    Order,
    apply_order_status_transition,
    refund_inventory_sale,
)
from app.schemas.common import PaginatedData
from app.schemas.order import OrderDetailResponse, OrderFilter, OrderResponse
from app.services.order_fulfillment import OrderFulfillmentService


class AdminOrderService:

    def __init__(self) -> None:
        self.fulfillment = OrderFulfillmentService()

    @staticmethod
    async def _reject_dedicated_certification_order(db, order: Order) -> None:
        """Keep H3C/NISP state transitions inside their dedicated services."""
        if order.application_id is not None:
            raise BusinessException("人社订单必须使用专用报名审核和退款流程")
        if order.product_type.startswith("NISP-"):
            raise BusinessException("NISP 订单必须使用专用报名审核和退款流程")
        linked_h3c = await db.scalar(
            select(H3cRegistration.id)
            .where(H3cRegistration.order_id == order.id)
            .limit(1)
        )
        if linked_h3c is not None:
            raise BusinessException("H3C 订单必须使用专用报名审核和退款流程")
        linked_nisp = await db.scalar(
            select(NispRegistration.id)
            .where(NispRegistration.order_id == order.id)
            .limit(1)
        )
        if linked_nisp is not None:
            raise BusinessException("NISP 订单必须使用专用报名审核和退款流程")

    @staticmethod
    async def _submit_wechat_v3_refund(
        order: Order, *, reason: str | None = None
    ) -> bool:
        """Submit/retry a full V3 refund; return true only when final SUCCESS.

        A successful HTTP response commonly means ``PROCESSING``.  Persisting
        that as a final local refund would revoke access and release inventory
        before the provider has actually returned the money.
        """

        if getattr(order, "payment_channel", "unknown") == "bank_transfer":
            raise BusinessException("银行转账订单须登记线下退款，不得调用微信退款")
        if not order.transaction_id or not order.out_trade_no:
            raise BusinessException("订单缺少微信支付交易信息，无法退款")
        out_refund_no = f"RF{order.id}"
        existing_metadata = (order.extra_data or {}).get("_wechat_refund_v3")
        wechat_pay = WechatPayClient()
        if (
            isinstance(existing_metadata, dict)
            and existing_metadata.get("out_refund_no") == out_refund_no
            and existing_metadata.get("status") == "PROCESSING"
        ):
            response = await wechat_pay.query_refund(out_refund_no=out_refund_no)
        else:
            response = await wechat_pay.refund(
                out_trade_no=order.out_trade_no,
                out_refund_no=out_refund_no,
                amount_total=order.price,
                refund_amount=order.price,
                reason=reason,
            )
        status = response.get("status")
        if status not in {"SUCCESS", "PROCESSING"}:
            raise ThirdPartyException("微信支付 V3 返回异常退款状态")
        response_refund_no = response.get("out_refund_no")
        if response_refund_no is not None and response_refund_no != out_refund_no:
            raise ThirdPartyException("微信支付 V3 退款单号不一致")
        amount = response.get("amount")
        if isinstance(amount, dict):
            if amount.get("total") != order.price or amount.get("refund") != order.price:
                raise ThirdPartyException("微信支付 V3 退款金额不一致")

        extra = dict(order.extra_data or {})
        refund_metadata = {
            "out_refund_no": out_refund_no,
            "status": status,
        }
        refund_id = response.get("refund_id")
        if isinstance(refund_id, str) and refund_id:
            refund_metadata["refund_id"] = refund_id
        extra["_wechat_refund_v3"] = refund_metadata
        order.extra_data = extra
        return status == "SUCCESS"

    async def list_orders(
        self,
        filters: OrderFilter | None,
        start_time: datetime | None,
        end_time: datetime | None,
        page: int,
        page_size: int,
    ) -> PaginatedData[OrderResponse]:
        async with get_db_ctx() as db:
            base = select(Order)
            if filters:
                from app.models.offline_payment import OfflineBankEntry
                if filters.payment_channel:
                    base = base.where(Order.payment_channel == filters.payment_channel)
                if filters.offline_status:
                    base = base.where(Order.offline_status == filters.offline_status)
                if filters.plan_id:
                    base = base.where(Order.plan_id == filters.plan_id)
                if filters.keyword:
                    pattern = f"%{filters.keyword.strip()}%"
                    base = base.where(or_(Order.out_trade_no.ilike(pattern), Order.candidate_name.ilike(pattern),
                        Order.candidate_phone.ilike(pattern), Order.id.in_(select(OfflineBankEntry.order_id).where(OfflineBankEntry.bank_reference.ilike(pattern)))))
                if filters.status:
                    base = base.where(Order.status == filters.status)
                if filters.product_type:
                    base = base.where(Order.product_type == filters.product_type)
                if filters.phone:
                    base = base.where(Order.candidate_phone == filters.phone)
            if start_time:
                base = base.where(Order.created_at >= start_time)
            if end_time:
                base = base.where(Order.created_at <= end_time)
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = base.order_by(Order.id.desc()).offset((page - 1) * page_size).limit(page_size)
            result = await db.execute(stmt)
            orders = result.scalars().all()
            return PaginatedData[OrderResponse](
                items=[OrderResponse.model_validate(o) for o in orders],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def get_order(self, order_id: int) -> OrderDetailResponse:
        async with get_db_ctx() as db:
            order = await db.get(Order, order_id)
            if order is None:
                raise NotFoundException("订单")
            return OrderDetailResponse.model_validate(order)

    async def review_order(self, order_id: int, data: "AdminOrderReview") -> OrderDetailResponse:
        from app.schemas.admin import AdminOrderReview

        if data.action == "reject_registration" and not data.comment:
            raise BusinessException("驳回报名信息时需填写理由")
        if data.action == "reject_and_refund" and not data.comment:
            raise BusinessException("驳回并退款时需填写理由")

        async with get_db_ctx() as db:
            order = (
                await db.execute(
                    select(Order).where(Order.id == order_id).with_for_update()
                )
            ).scalar_one_or_none()
            if order is None:
                raise NotFoundException("订单")
            await self._reject_dedicated_certification_order(db, order)
            if order.status != "paid":
                raise BusinessException("仅已支付订单可审核")

            if data.action == "approve":
                apply_order_status_transition(order, "completed")
            elif data.action == "reject_registration":
                # 保持 paid 状态，驳回理由写入 extra_data
                extra = dict(order.extra_data) if order.extra_data else {}
                extra["_reject_comment"] = data.comment
                order.extra_data = extra
            elif data.action == "reject_and_refund":
                refund_succeeded = await self._submit_wechat_v3_refund(
                    order,
                    reason=data.comment,
                )
                if refund_succeeded:
                    apply_order_status_transition(order, "refunded")
                    await refund_inventory_sale(
                        db,
                        order,
                        reason="review_reject_refund",
                    )
                    await self.fulfillment.on_refunded(db, order)

            await db.commit()
            await db.refresh(order)
            return OrderDetailResponse.model_validate(order)

    async def refund_order(self, order_id: int) -> OrderDetailResponse:
        async with get_db_ctx() as db:
            order = (
                await db.execute(
                    select(Order).where(Order.id == order_id).with_for_update()
                )
            ).scalar_one_or_none()
            if order is None:
                raise NotFoundException("订单")
            await self._reject_dedicated_certification_order(db, order)
            if order.status == "refunded":
                inventory_changed = await refund_inventory_sale(
                    db,
                    order,
                    reason="admin_refund_repair",
                )
                access_revoked = await self.fulfillment.on_refunded(db, order)
                if inventory_changed or access_revoked:
                    await db.commit()
                    await db.refresh(order)
                return OrderDetailResponse.model_validate(order)

            # 检查是否已支付
            if not order.transaction_id:
                raise BusinessException("订单未支付，无法退款")

            refund_succeeded = await self._submit_wechat_v3_refund(order)
            if not refund_succeeded:
                await db.commit()
                await db.refresh(order)
                return OrderDetailResponse.model_validate(order)

            apply_order_status_transition(order, "refunded")
            await refund_inventory_sale(db, order, reason="admin_refund")
            await self.fulfillment.on_refunded(db, order)
            await db.commit()
            await db.refresh(order)
            return OrderDetailResponse.model_validate(order)

    async def reconciliation(self, date: str) -> dict:
        target_date = calendar_date.fromisoformat(date)
        local_zone = ZoneInfo(settings.APP_TIMEZONE)
        date_start = datetime.combine(
            target_date,
            time.min,
            tzinfo=local_zone,
        ).astimezone(timezone.utc)
        date_end = datetime.combine(
            target_date + timedelta(days=1),
            time.min,
            tzinfo=local_zone,
        ).astimezone(timezone.utc)
        async with get_db_ctx() as db:
            from app.models.offline_payment import OfflineBankEntry
            from app.domain.h3c.src.model.h3c import H3cRefundRequest
            from app.domain.nisp.src.model.nisp import NispRefundRequest
            from app.domain.renshe.src.model.renshe import RensheRefundRequest
            from app.domain.order.src.model.payment_refund import PaymentRefundTask

            # Aggregate immutable money events, never the order's present state.
            events = [select(
                OfflineBankEntry.order_id.label("order_id"),
                literal("bank_transfer").label("channel"),
                OfflineBankEntry.kind.label("kind"),
                OfflineBankEntry.amount_cents.label("amount"),
            ).where(OfflineBankEntry.bank_time >= date_start,
                    OfflineBankEntry.bank_time < date_end)]
            events.append(select(Order.id, literal("wechat"), literal("receipt"), Order.price)
                .where(Order.payment_channel == "wechat", Order.paid_at >= date_start,
                       Order.paid_at < date_end))
            for refund in (H3cRefundRequest, NispRefundRequest, RensheRefundRequest, PaymentRefundTask):
                events.append(select(refund.order_id, literal("wechat"), literal("refund"), refund.amount_cents)
                    .join(Order, Order.id == refund.order_id)
                    .where(Order.payment_channel == "wechat", refund.status == "succeeded",
                           refund.succeeded_at >= date_start, refund.succeeded_at < date_end))
            ledger = union_all(*events).subquery()
            signed = case((ledger.c.kind == "receipt", ledger.c.amount), else_=-ledger.c.amount)
            row = (await db.execute(select(
                func.coalesce(func.sum(case((ledger.c.kind == "receipt", ledger.c.amount), else_=0)), 0),
                func.coalesce(func.sum(case((ledger.c.kind == "refund", ledger.c.amount), else_=0)), 0),
                func.coalesce(func.sum(case((ledger.c.channel == "bank_transfer", signed), else_=0)), 0),
                func.coalesce(func.sum(case((ledger.c.channel == "wechat", signed), else_=0)), 0),
                func.count(func.distinct(ledger.c.order_id)),
            ))).one()
            order_total, refund_total, bank_total, wechat_total, order_count = row
            return {
                "bank_transfer_total": bank_total,
                "wechat_total": wechat_total,
                "timezone": settings.APP_TIMEZONE,
                "date": target_date.isoformat(),
                "order_total": order_total,
                "refund_total": refund_total,
                "net_income": order_total - refund_total,
                "order_count": order_count,
            }

    async def export_orders(
        self,
        filters: OrderFilter | None,
        start_time: datetime | None,
        end_time: datetime | None,
    ) -> str:
        import csv
        import io

        async with get_db_ctx() as db:
            base = select(Order)
            if filters:
                from app.models.offline_payment import OfflineBankEntry
                if filters.payment_channel:
                    base = base.where(Order.payment_channel == filters.payment_channel)
                if filters.offline_status:
                    base = base.where(Order.offline_status == filters.offline_status)
                if filters.plan_id:
                    base = base.where(Order.plan_id == filters.plan_id)
                if filters.keyword:
                    pattern = f"%{filters.keyword.strip()}%"
                    base = base.where(or_(Order.out_trade_no.ilike(pattern), Order.candidate_name.ilike(pattern),
                        Order.candidate_phone.ilike(pattern), Order.id.in_(select(OfflineBankEntry.order_id).where(OfflineBankEntry.bank_reference.ilike(pattern)))))
                if filters.status:
                    base = base.where(Order.status == filters.status)
                if filters.product_type:
                    base = base.where(Order.product_type == filters.product_type)
                if filters.phone:
                    base = base.where(Order.candidate_phone == filters.phone)
            if start_time:
                base = base.where(Order.created_at >= start_time)
            if end_time:
                base = base.where(Order.created_at <= end_time)
            stmt = base.order_by(Order.id.desc())
            result = await db.execute(stmt)
            orders = result.scalars().all()
            output = io.StringIO()
            writer = csv.writer(output)

            all_keys: set[str] = set()
            for o in orders:
                if o.extra_data:
                    all_keys.update(o.extra_data.keys())
            extra_headers = sorted(all_keys)

            writer.writerow(
                ["ID", "UserID", "ProductType", "CandidateName", "CandidatePhone",
                 "Price", "Status", "CreatedAt"]
                + extra_headers
            )
            for o in orders:
                row = [o.id, o.user_id, o.product_type, o.candidate_name,
                       o.candidate_phone, o.price, o.status, str(o.created_at)]
                for k in extra_headers:
                    row.append(str(o.extra_data.get(k, "")) if o.extra_data else "")
                writer.writerow(row)
            return output.getvalue()
