"""Shared refund errors and request replay, with an isolated SQLite ledger."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.domain.order.src.index import Order
from app.models.offline_payment import OfflineBankEntry, OfflinePaymentAudit
from app.port.exceptions import ConflictException
from app.schemas.offline_payment import RefundInput
from app.services.offline_payment import now
from tests.unit.test_offline_payment_audit_reasons import finance  # Shared isolated ledger fixture.


def refund_data(amount=10000):
    return RefundInput(expected_version=1, request_key=uuid4().hex, amount_cents=amount,
                       bank_time=now(), bank_reference=uuid4().hex, note="已核实实际退款",
                       material_ids=[1], refund_kind="order", refund_id=1)


@pytest.mark.parametrize("status,amount,expected", [
    ("refunded", 10000, "该订单已完成退款，请勿重复登记"),
    ("closed", 10000, "当前订单状态不允许登记退款，请刷新核对"),
    ("pending", 10000, "当前订单状态不允许登记退款，请刷新核对"),
    ("paid", 9000, "实际退款金额与已付款订单不一致"),
    ("completed", 11000, "实际退款金额与已付款订单不一致"),
])
async def test_refund_errors_distinguish_status_and_amount_and_rollback(finance, status, amount, expected):
    service, factory = finance
    async with factory() as db, db.begin():
        (await db.get(Order, 1)).status = status
    with pytest.raises(ConflictException) as error:
        await service.refund(1, 101, refund_data(amount))
    assert error.value.message == expected
    async with factory() as db:
        assert (await db.get(Order, 1)).status == status
        assert await db.scalar(select(func.count()).select_from(OfflineBankEntry)) == 0
        assert await db.scalar(select(func.count()).select_from(OfflinePaymentAudit)) == 0


async def test_completed_refund_replays_original_request_without_a_second_entry(finance, monkeypatch):
    service, factory = finance
    # The regression is ledger idempotency, not material storage or certification routing.
    monkeypatch.setattr("app.services.admin_order.AdminOrderService._reject_dedicated_certification_order", AsyncMock())
    monkeypatch.setattr("app.domain.order.src.index.refund_inventory_sale", AsyncMock())
    monkeypatch.setattr("app.services.offline_payment.OrderFulfillmentService.on_refunded", AsyncMock())
    service.check_materials = AsyncMock()
    async with factory() as db, db.begin():
        (await db.get(Order, 1)).status = "paid"
        db.add(OfflinePaymentAudit(order_id=1, actor_id=101, action="admin.generic_refund_approved",
                                   request_key=uuid4().hex, payload_hash="test", summary={}))
    data = refund_data()
    assert await service.refund(1, 101, data) == {"status": "refunded"}
    assert await service.refund(1, 101, data) == {"status": "refunded", "replayed": True}
    with pytest.raises(ConflictException) as error:
        await service.refund(1, 101, refund_data())
    assert error.value.message == "该订单已完成退款，请勿重复登记"
    async with factory() as db:
        assert (await db.get(Order, 1)).status == "refunded"
        assert await db.scalar(select(func.count()).select_from(OfflineBankEntry)) == 1
        assert await db.scalar(select(func.count()).select_from(OfflinePaymentAudit).where(
            OfflinePaymentAudit.action == "admin.refund")) == 1
