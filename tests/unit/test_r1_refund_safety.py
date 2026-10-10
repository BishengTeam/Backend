from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from app.domain.order.src.index import Order
from app.integrations.wechat_pay import WechatPayRefund
from app.services.h3c_refund import H3cRefundService
from app.domain.order.src.index import Order, PaymentRefundTask
from app.services.payment_refund import PaymentRefundService


def provider(status="SUCCESS"):
    return WechatPayRefund(
        out_trade_no="order-1", transaction_id="transaction-1",
        out_refund_no="H3RF" + "1".zfill(28), refund_id="refund-1",
        status=status, amount_total=100, amount_refund=100,
        currency="CNY", mchid="1900000001",
    )


def closed_order():
    return Order(
        id=11, user_id=7, order_kind="certification", product_type="NISP-1",
        price=200, status="closed", out_trade_no="order-11",
        transaction_id="transaction-11",
    )


@pytest.mark.asyncio
async def test_late_payment_creates_one_stable_refund_task():
    order = closed_order()
    added = []
    db = NS(
        execute=AsyncMock(return_value=NS(scalar_one_or_none=lambda: None)),
        add=added.append,
    )

    async def flush():
        added[0].id = 99

    db.flush = flush
    task = await PaymentRefundService.schedule_late_payment_refund(db, order)
    assert task is added[0]
    assert task.order_id == order.id
    assert task.amount_cents == order.price
    assert task.out_refund_no == "LPRF" + str(order.id).zfill(24)
    assert task.status == "queued"


@pytest.mark.asyncio
async def test_late_payment_task_is_idempotent():
    order = closed_order()
    existing = PaymentRefundTask(
        order_id=order.id, user_id=order.user_id, amount_cents=order.price,
        out_trade_no=order.out_trade_no, transaction_id=order.transaction_id,
        out_refund_no="LPRF" + "1".zfill(24), status="processing",
        next_attempt_at=None,
    )
    db = NS(
        execute=AsyncMock(return_value=NS(scalar_one_or_none=lambda: existing)),
        add=Mock(),
    )
    assert await PaymentRefundService.schedule_late_payment_refund(db, order) is existing
    db.add.assert_not_called()


def test_late_payment_worker_retries_uncertain_tasks_without_blocking():
    from pathlib import Path

    source = Path("app/services/payment_refund.py").read_text(encoding="utf-8")
    assert "PaymentRefundTask.status.in_(EXECUTABLE_STATUSES)" in source
    assert "PaymentRefundTask.next_attempt_at <=" in source
    assert "PaymentRefundTask.id > last_task_id" in source


def test_r1_bypass_guards_are_present():
    from pathlib import Path

    payment = Path("app/services/payment.py").read_text(encoding="utf-8")
    admin_order = Path("app/services/admin_order.py").read_text(encoding="utf-8")
    plan = Path("app/services/plan.py").read_text(encoding="utf-8")

    assert "has_nisp_refund" in payment
    assert "not has_nisp_refund" in payment
    assert "_reject_dedicated_certification_order" in admin_order
    assert "_reject_dedicated_certification_batch" in plan
    payment_api = Path("app/api/payment.py").read_text(encoding="utf-8")
    assert "PaymentRefundService().handle_callback_raw" in payment_api


@pytest.mark.asyncio
async def test_h3c_refund_success_is_never_downgraded(monkeypatch):
    refund = NS(
        id=1, registration_id=1, order_id=1, user_id=1,
        request_kind="review_failed", reason_code="review", amount_cents=100,
        status="succeeded", requested_at=None, reason_detail=None, approved_by_admin_id=None, approved_at=None, processing_at=None, succeeded_at=None, created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc), out_refund_no="H3RF" + "1".zfill(28),
        wechat_refund_id="refund-1",
    )
    order = Order(id=1, user_id=1, order_kind="certification", product_type="H3C-NE",
                  price=100, status="refunded", out_trade_no="order-1",
                  transaction_id="transaction-1")
    registration = NS(id=1, batch_id=1, plan_id=1, order_id=1,
                                   registration_no="H3C-1", candidate_idcard="x"*18,
                                   candidate_snapshot={}, status="refunded_closed")
    unresolved = NS(id=refund.id, order_id=1, registration_id=1)
    db = NS(
        scalar=AsyncMock(side_effect=[unresolved, order, registration, refund]),
        commit=AsyncMock(), refresh=AsyncMock(),
    )

    @asynccontextmanager
    async def ctx():
        yield db

    monkeypatch.setattr("app.services.h3c_refund.get_db_ctx", ctx)
    service = H3cRefundService(wechat_pay=NS(mch_id="1900000001"))
    result = await service._apply_provider_result(provider("PROCESSING"))
    assert result.status == "succeeded"
    assert refund.status == "succeeded"
    assert order.status == "refunded"
    assert registration.status == "refunded_closed"
    db.commit.assert_not_awaited()
