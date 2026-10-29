"""Persist decision evidence with SQLite; these tests do not claim PostgreSQL lock coverage."""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adapter.database import Base
from app.domain.order.src.index import Order
from app.models.offline_payment import OfflineBankEntry, OfflinePayment, OfflinePaymentAudit, OfflinePaymentSubmission
from app.schemas.offline_payment import ConfirmInput, DecisionInput
from app.services.offline_payment import OfflinePaymentService, now


@pytest.fixture
async def finance(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda db: Base.metadata.create_all(db, tables=[
                Order.__table__, OfflinePayment.__table__, OfflinePaymentAudit.__table__,
                OfflinePaymentSubmission.__table__, OfflineBankEntry.__table__,
            ]))
        factory = async_sessionmaker(engine, expire_on_commit=False)

        @asynccontextmanager
        async def context():
            async with factory() as db:
                yield db

        monkeypatch.setattr("app.services.offline_payment.get_db_ctx", context)
        monkeypatch.setattr("app.services.offline_payment.confirm_inventory_sale", AsyncMock())
        monkeypatch.setattr("app.services.offline_payment.OrderFulfillmentService.on_paid", AsyncMock())
        async with factory() as db, db.begin():
            db.add(Order(id=1, user_id=1, order_kind="certification", product_type="AUDIT-TEST",
                         price=10000, status="pending", payment_channel="bank_transfer", offline_status="under_review"))
            db.add(OfflinePayment(order_id=1, current_version=1, account_snapshot={"correction_hours": 48},
                                  account_fingerprint="test-only"))
            db.add(OfflinePaymentSubmission(order_id=1, version=1, data={}, status="under_review"))
        yield OfflinePaymentService(), factory
    finally:
        await engine.dispose()


def confirm(amount):
    return ConfirmInput(expected_version=1, request_key=uuid4().hex, amount_cents=amount,
                        bank_time=now(), bank_reference=uuid4().hex, note="已核实银行流水")


async def test_each_mismatch_retains_its_own_reason_and_amount_after_success(finance):
    service, factory = finance
    for amount in [9000, 11000, 10000]:
        await service.decide(1, 101, confirm(amount), "confirm")
    async with factory() as db:
        audits = list(await db.scalars(select(OfflinePaymentAudit).order_by(OfflinePaymentAudit.id)))
        assert len(audits) == 3
        for audit in audits[:2]:
            assert audit.summary["reason_code"] == "amount_mismatch"
            assert audit.summary["reason"] == "实际到账金额与应付金额不一致"
            assert audit.summary["expected_amount_cents"] == 10000
            assert audit.summary["result"] == "exception"
            assert audit.summary["note"] == "已核实银行流水"
        assert [a.summary["amount_cents"] for a in audits] == [9000, 11000, 10000]
        assert audits[2].summary["result"] == "confirmed"
        assert audits[2].summary["reason"] is None
        assert audits[2].summary["reason_code"] is None


@pytest.mark.parametrize("action,code,result", [
    ("return", "returned_for_correction", "returned"), ("exception", "manual_exception", "exception"),
])
async def test_manual_decision_reason_is_stored_in_the_operation(finance, action, code, result):
    service, factory = finance
    data = DecisionInput(expected_version=1, request_key=uuid4().hex, note="流水无法辨认，请提供完整银行回单")
    await service.decide(1, 101, data, action)
    async with factory() as db:
        audit = await db.scalar(select(OfflinePaymentAudit))
        assert audit.summary["reason"] == data.note
        assert audit.summary["reason_code"] == code
        assert audit.summary["result"] == result
        if action == "return":
            assert audit.summary["deadline"]


async def test_exception_on_a_paid_order_also_keeps_its_reason(finance):
    service, factory = finance
    async with factory() as db, db.begin():
        order = await db.get(Order, 1)
        order.status = "paid"
        order.offline_status = "confirmed"
    await service.decide(1, 101, DecisionInput(expected_version=1, request_key=uuid4().hex,
                                            note="银行通知资金待进一步核实"), "exception")
    async with factory() as db:
        audit = await db.scalar(select(OfflinePaymentAudit))
        assert audit.summary["reason"] == "银行通知资金待进一步核实"
        assert audit.summary["reason_code"] == "manual_exception"
        assert (await db.get(Order, 1)).status == "paid"


async def test_retry_does_not_append_or_rewrite_the_original_audit(finance):
    service, factory = finance
    data = confirm(9000)
    await service.decide(1, 101, data, "confirm")
    assert (await service.decide(1, 101, data, "confirm"))["replayed"]
    async with factory() as db:
        audits = list(await db.scalars(select(OfflinePaymentAudit)))
        assert len(audits) == 1
        assert audits[0].summary["reason_code"] == "amount_mismatch"
        assert audits[0].summary["expected_amount_cents"] == 10000
