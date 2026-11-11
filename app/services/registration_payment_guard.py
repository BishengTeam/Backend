"""Shared payment prerequisite; callers lock Order before registration/refund rows."""
from sqlalchemy import select, union_all

from app.models.offline_payment import OfflineBankEntry, OfflinePayment, OfflinePaymentSubmission
from app.port.exceptions import ConflictException
from app.schemas.registration_payment import RegistrationPaymentSummary


def payment_filter(state):
    """SQL list filter; payment eligibility is still rechecked inside writes."""
    from app.domain.order.src.index import Order
    from sqlalchemy import and_, or_
    if state == "closed":
        return Order.status.in_(("closed", "refunded"))
    if state == "confirmed":
        receipt = select(OfflineBankEntry.id).join(OfflinePayment, OfflinePayment.order_id == OfflineBankEntry.order_id).where(
            OfflineBankEntry.order_id == Order.id, OfflineBankEntry.kind == "receipt",
            OfflineBankEntry.amount_cents == Order.price,
            OfflineBankEntry.account_fingerprint == OfflinePayment.account_fingerprint,
            OfflineBankEntry.submission_version == OfflinePayment.current_version,
            OfflinePayment.confirmed_at.is_not(None),
        ).exists()
        return and_(Order.status.in_(("paid", "completed")), or_(
            and_(Order.payment_channel == "bank_transfer", Order.offline_status == "confirmed", Order.paid_at.is_not(None), receipt),
            and_(Order.payment_channel == "wechat", Order.transaction_id.is_not(None), Order.transaction_id != "", Order.price > 0, Order.paid_at.is_not(None), Order.offline_status.is_(None)),
            and_(Order.payment_channel == "free", Order.price == 0, Order.paid_at.is_not(None), Order.offline_status.is_(None)),
        ), or_(Order.offline_status.is_(None), Order.offline_status != "exception"))
    if state not in {"awaiting_proof", "under_review", "returned", "exception"}:
        raise ConflictException("不支持的核账状态筛选")
    return and_(Order.payment_channel == "bank_transfer", Order.offline_status == state)


async def payment_summary(db, order, registration=None) -> RegistrationPaymentSummary:
    result = RegistrationPaymentSummary(
        order_id=order.id if order else None,
        payment_channel=getattr(order, "payment_channel", "unknown") if order else "unknown",
        order_status=order.status if order else None,
        offline_status=getattr(order, "offline_status", None) if order else None,
        review_block_reason="关联订单缺失" if order is None else "付款尚未确认",
    )
    if order is None:
        return result
    if registration is not None:
        linked = getattr(registration, "order_id", None)
        consistent = (
            getattr(registration, "user_id", None) == getattr(order, "user_id", None)
            and getattr(registration, "plan_id", None) == getattr(order, "plan_id", None)
            and (linked == order.id if linked is not None else order.application_id == registration.id)
        )
        if not consistent:
            result.review_block_reason = "报名与订单的用户、批次或关联记录不一致"
            return result
    if registration is not None and (getattr(registration, "frozen_at", None) is not None or registration.status in {"pending_refund_confirmation", "refund_processing", "refunded_closed", "cancelled", "closed"}):
        result.review_block_reason = "报名已冻结、退款处理中或关闭"
        return result
    if order.status not in {"paid", "completed"}:
        result.review_block_reason = "订单已退款或关闭" if order.status in {"refunded", "closed"} else "付款尚未确认"
        return result
    if result.offline_status == "exception":
        result.review_block_reason = "付款存在异常，请先处理资金问题"
        return result
    if result.payment_channel not in {"bank_transfer", "wechat", "free"}:
        result.review_block_reason = "付款渠道未核实，请先处理订单"
        return result
    from app.domain.h3c.src.model.h3c import H3cRefundRequest
    from app.domain.nisp.src.model.nisp import NispRefundRequest
    from app.domain.renshe.src.model.renshe import RensheRefundRequest
    from app.domain.order.src.model.payment_refund import PaymentRefundTask
    refunds = union_all(*[
        select(model.order_id).where(model.order_id == order.id,
            model.status.in_(("requested", "approved", "processing", "failed", "queued", "manual_review", "succeeded")))
        for model in (H3cRefundRequest, NispRefundRequest, RensheRefundRequest, PaymentRefundTask)
    ]).subquery()
    if await db.scalar(select(refunds.c.order_id).limit(1)):
        result.review_block_reason = "存在退款申请或退款记录，不能继续审核"
        return result
    valid = False
    if result.payment_channel == "bank_transfer":
        if result.offline_status == "confirmed":
            row = (await db.execute(select(OfflineBankEntry, OfflinePayment)
                .join(OfflinePayment, OfflinePayment.order_id == OfflineBankEntry.order_id)
                .join(OfflinePaymentSubmission, (OfflinePaymentSubmission.order_id == OfflinePayment.order_id)
                      & (OfflinePaymentSubmission.version == OfflinePayment.current_version))
                .where(OfflineBankEntry.order_id == order.id, OfflineBankEntry.kind == "receipt",
                       OfflinePaymentSubmission.status == "confirmed"))).first()
            if row:
                receipt, payment = row
                valid = (receipt.amount_cents == order.price and receipt.account_fingerprint == payment.account_fingerprint
                         and receipt.submission_version == payment.current_version and payment.confirmed_at is not None
                         and order.paid_at is not None)
        result.review_block_reason = "银行核账尚未确认或收款记录不一致"
    elif result.payment_channel == "wechat":
        valid = bool(order.transaction_id and order.paid_at and order.price > 0 and result.offline_status is None)
        result.review_block_reason = "微信付款记录不完整"
    elif result.payment_channel == "free":
        valid = order.price == 0 and order.paid_at is not None and result.offline_status is None
        result.review_block_reason = "零元订单尚未由系统确认"
    else:
        result.review_block_reason = "付款渠道未核实，请先处理订单"
    result.review_payment_allowed = valid
    if valid:
        result.review_block_reason = None
    return result


async def require_review_payment(db, order, registration=None):
    summary = await payment_summary(db, order, registration)
    if not summary.review_payment_allowed:
        raise ConflictException(summary.review_block_reason)
    return summary
