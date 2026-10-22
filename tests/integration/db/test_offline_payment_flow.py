"""Real PostgreSQL locking/uniqueness, using explicitly selected test URLs."""
import asyncio
import importlib
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.order.src.index import Order
from app.domain.user.src.index import User
from app.models.offline_payment import OfflineBankEntry, OfflinePayment, OfflinePaymentSubmission, OfflinePaymentSettings, OfflinePaymentAudit
from app.port.exceptions import ConflictException, NotFoundException
from app.schemas.offline_payment import ConfirmInput, DecisionInput, SettingsInput, SubmissionInput
from app.services.offline_payment import OfflinePaymentService, now

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def context(monkeypatch, tmp_path):
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def ctx():
        async with factory() as session:
            yield session

    for name in ["offline_payment", "order", "order_timeout", "nisp_admin", "nisp_refund", "renshe_refund", "h3c_refund"]:
        monkeypatch.setattr(importlib.import_module(f"app.services.{name}"), "get_db_ctx", ctx)
    from app.port.config import settings
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "RENSHE_STORAGE_TYPE", "local")
    service = OfflinePaymentService()
    created_order_ids = []
    config = await service.get_settings()
    await service.save_settings(101, SettingsInput(account_name="测试收款账户", bank_name="测试银行",
        account_number="6222000000000000", contact_name="测试管理员", contact_phone="13800000000",
        version=config["version"] if config else 0))

    async def create(status="awaiting_proof", price=10000, plan=None):
        async with factory() as db, db.begin():
            user = User(openid=f"offline_test_{uuid4().hex}")
            db.add(user); await db.flush()
            order = Order(user_id=user.id, order_kind="certification", product_type="OFFLINE-TEST",
                plan_id=plan, price=price, status="pending", out_trade_no=f"OF-{uuid4().hex}")
            db.add(order); await db.flush()
            await service.initialize(db, order)
            order.offline_status = status
            created_order_ids.append(order.id)
            return order.id, user.id

    async def submit(order_id, user_id, version=0):
        file = await service.upload(order_id, UploadFile(filename="转账凭证.pdf", file=BytesIO(b"%PDF-1.4 test")), user_id=user_id)
        data = SubmissionInput(payer="测试付款人", transfer_time=now(), amount_cents=10000,
            material_ids=[file["id"]], expected_version=version, request_key=uuid4().hex)
        await service.submit(order_id, user_id, data)
        return data

    try:
        yield SimpleNamespace(db=factory, service=service, create=create, submit=submit)
    finally:
        # Money-day assertions must not inherit financial events from a prior run.
        from app.domain.order.src.model.payment_refund import PaymentRefundTask
        async with factory() as db, db.begin():
            await db.execute(delete(OfflineBankEntry).where(OfflineBankEntry.order_id.in_(created_order_ids)))
            await db.execute(delete(PaymentRefundTask).where(PaymentRefundTask.order_id.in_(created_order_ids)))
        await engine.dispose()


def confirmation(version=1, reference=None, **kwargs):
    return ConfirmInput(expected_version=version, request_key=uuid4().hex, note="已核对银行实际入账",
        amount_cents=10000, bank_time=now(), bank_reference=reference or uuid4().hex, **kwargs)


async def test_two_admins_same_order_and_replay(context):
    id, uid = await context.create()
    await context.submit(id, uid)
    command = confirmation()
    results = await asyncio.gather(*(context.service.decide(id, 101, command, "confirm") for _ in range(2)))
    assert all(r["status"] == "confirmed" for r in results)
    assert sum(bool(r.get("replayed")) for r in results) == 1
    async with context.db() as db:
        assert await db.scalar(select(func.count()).select_from(OfflineBankEntry).where(OfflineBankEntry.order_id == id)) == 1
        assert (await db.get(Order, id)).transaction_id is None


async def test_daily_money_uses_bank_day_not_creation_or_current_status(context, monkeypatch):
    from app.services import admin_order

    @asynccontextmanager
    async def ctx():
        async with context.db() as db:
            yield db

    monkeypatch.setattr(admin_order, "get_db_ctx", ctx)
    order_id, _ = await context.create()
    second_id, _ = await context.create()
    async with context.db() as db, db.begin():
        order = await db.get(Order, order_id)
        order.created_at = datetime(2098, 10, 20, tzinfo=timezone.utc)
        order.status = "refunded"
        # Shanghai 22nd 00:00 and 23rd 00:00; registration happens today.
        for oid, kind, amount, stamp in [
            (order_id, "receipt", 10000, datetime(2098, 10, 21, 16, tzinfo=timezone.utc)),
            (order_id, "refund", 10000, datetime(2098, 10, 22, 16, tzinfo=timezone.utc)),
            (second_id, "receipt", 3000, datetime(2098, 10, 22, 15, 59, 59, tzinfo=timezone.utc)),
        ]:
            db.add(OfflineBankEntry(order_id=oid, kind=kind, amount_cents=amount,
                bank_time=stamp, account_fingerprint="audit-day-test", bank_reference=uuid4().hex,
                admin_id=101, submission_version=1, material_ids=[]))
    service = admin_order.AdminOrderService()
    before = await service.reconciliation("2098-10-21")
    receipt = await service.reconciliation("2098-10-22")
    refund = await service.reconciliation("2098-10-23")
    assert before["order_total"] == before["refund_total"] == 0
    assert (receipt["order_total"], receipt["refund_total"], receipt["bank_transfer_total"], receipt["order_count"]) == (13000, 0, 13000, 2)
    assert (refund["order_total"], refund["refund_total"], refund["bank_transfer_total"], refund["order_count"]) == (0, 10000, -10000, 1)
    async with context.db() as db, db.begin():
        (await db.get(Order, order_id)).status = "closed"
    assert await service.reconciliation("2098-10-22") == receipt


async def test_daily_wechat_receipt_and_successful_refund_use_their_own_dates(context, monkeypatch):
    from app.services import admin_order
    from app.domain.order.src.model.payment_refund import PaymentRefundTask

    @asynccontextmanager
    async def ctx():
        async with context.db() as db:
            yield db

    monkeypatch.setattr(admin_order, "get_db_ctx", ctx)
    order_id, uid = await context.create()
    async with context.db() as db, db.begin():
        order = await db.get(Order, order_id)
        order.payment_channel = "wechat"
        order.status = "refunded"
        order.paid_at = datetime(2096, 10, 22, tzinfo=timezone.utc)
        db.add(PaymentRefundTask(order_id=order_id, user_id=uid, amount_cents=10000,
            out_trade_no=order.out_trade_no, transaction_id=uuid4().hex,
            out_refund_no=uuid4().hex, status="succeeded", next_attempt_at=now(),
            succeeded_at=datetime(2096, 10, 23, tzinfo=timezone.utc)))
    service = admin_order.AdminOrderService()
    receipt = await service.reconciliation("2096-10-22")
    refund = await service.reconciliation("2096-10-23")
    assert (receipt["order_total"], receipt["refund_total"], receipt["wechat_total"]) == (10000, 0, 10000)
    assert (refund["order_total"], refund["refund_total"], refund["wechat_total"]) == (0, 10000, -10000)


async def test_one_bank_entry_cannot_settle_two_orders(context):
    first, uid1 = await context.create(); second, uid2 = await context.create()
    await context.submit(first, uid1); await context.submit(second, uid2)
    reference = uuid4().hex
    result = await asyncio.gather(context.service.decide(first, 101, confirmation(reference=reference), "confirm"),
        context.service.decide(second, 102, confirmation(reference=reference[:8] + " " + reference[8:]), "confirm"), return_exceptions=True)
    assert sum(isinstance(r, ConflictException) for r in result) == 1
    async with context.db() as db:
        statuses = list(await db.scalars(select(Order.status).where(Order.id.in_([first, second]))))
        assert sorted(statuses) == ["paid", "pending"]


async def test_correction_rejects_stale_confirmation_and_freezes_coupon(context):
    from app.services.order import OrderService
    id, uid = await context.create(); await context.submit(id, uid)
    await context.service.decide(id, 101, DecisionInput(expected_version=1, request_key=uuid4().hex, note="请补充完整银行回单"), "return")
    await context.submit(id, uid, version=1)
    with pytest.raises(ConflictException):
        await context.service.decide(id, 101, confirmation(version=1), "confirm")
    with pytest.raises(ConflictException):
        await OrderService().apply_coupon_to_order(user_id=uid, order_id=id, coupon_code="")
    with pytest.raises(ConflictException):
        await OrderService().cancel_pending_order(uid, id)
    async with context.db() as db:
        rows = list(await db.scalars(select(OfflinePaymentSubmission).where(OfflinePaymentSubmission.order_id == id)))
        assert len(rows) == 2 and {s.status for s in rows} == {"returned", "under_review"}


async def test_submitted_proof_survives_timeout_and_return_deadline(context):
    from app.services.order_timeout import OrderTimeoutCloseService
    id, uid = await context.create(); await context.submit(id, uid)
    await OrderTimeoutCloseService().close_expired_pending_orders(now=now() + timedelta(days=3))
    async with context.db() as db:
        assert (await db.get(Order, id)).status == "pending"
    await context.service.decide(id, 101, DecisionInput(expected_version=1, request_key=uuid4().hex, note="补交"), "return")
    await OrderTimeoutCloseService().close_expired_pending_orders(now=now() + timedelta(days=3))
    async with context.db() as db:
        order = await db.get(Order, id)
        assert order.status == "pending" and order.offline_status == "exception"


async def test_mismatch_stays_unpaid_and_no_claim(context):
    id, uid = await context.create(); await context.submit(id, uid)
    command = confirmation().model_copy(update={"amount_cents": 9000})
    result = await context.service.decide(id, 101, command, "confirm")
    assert result["status"] == "exception"
    async with context.db() as db:
        order = await db.get(Order, id)
        assert order.status == "pending" and order.paid_at is None
        assert await db.scalar(select(func.count()).select_from(OfflineBankEntry).where(OfflineBankEntry.order_id == id)) == 0


async def test_ownership_private_content_and_account_snapshot(context):
    id, uid = await context.create(); other, other_uid = await context.create()
    data = await context.submit(id, uid)
    with pytest.raises(NotFoundException):
        await context.service.content(id, data.material_ids[0], other_uid)
    with pytest.raises(ConflictException):
        await context.service.submit(other, other_uid, data)
    content, type, filename = await context.service.content(id, data.material_ids[0], uid)
    assert content.startswith(b"%PDF") and type == "application/pdf" and filename.endswith(".pdf")
    settings = await context.service.get_settings()
    await context.service.save_settings(101, SettingsInput(**{**settings, "account_number": "6222111111111111"}))
    assert (await context.service.detail(id, uid))["account"]["account_number"] == "6222000000000000"
    async with context.db() as db:
        change = await db.scalar(select(OfflinePaymentAudit).where(OfflinePaymentAudit.action == "settings.update").order_by(OfflinePaymentAudit.id.desc()).limit(1))
        assert change.summary["previous_account"]["account_number"] == "6222000000000000"
        assert change.summary["account"]["account_number"] == "6222111111111111"


async def test_closed_transfer_report_never_restores_order(context):
    from app.services.order import OrderService
    id, uid = await context.create()
    await OrderService().cancel_pending_order(uid, id)
    file = await context.service.upload(id, UploadFile(filename="回单.png", file=BytesIO(b"\x89PNG\r\n\x1a\nbody")), user_id=uid)
    body = SubmissionInput(payer="已转账用户", transfer_time=now(), amount_cents=10000,
        material_ids=[file["id"]], expected_version=0, request_key=uuid4().hex)
    await context.service.submit(id, uid, body, exception=True)
    await context.service.decide(id, 101, DecisionInput(expected_version=1, request_key=uuid4().hex, note="已联系银行核实，订单保持关闭"), "exception")
    async with context.db() as db:
        order = await db.get(Order, id)
        assert order.status == "closed" and order.offline_status == "exception"
    with pytest.raises(ConflictException):
        await context.service.decide(id, 101, confirmation(), "confirm")


async def seed_registration(context, vendor):
    from app.domain.plan.src.index import Plan
    from app.domain.h3c.src.index import H3cExamBatch, H3cRegistration
    from app.domain.nisp.src.index import NispExamBatch, NispRegistration
    from app.domain.renshe.src.index import RensheApplication
    from app.domain.user.src.index import AdminUser
    async with context.db() as db, db.begin():
        admin = AdminUser(username=f"offline_test_{uuid4().hex}", password_hash="test-only", role="cert_admin")
        plan = Plan(product_type="OFFLINE-TEST", name=uuid4().hex, status="published", capacity=100,
            apply_start=now() - timedelta(days=1), apply_end=now() + timedelta(days=1))
        db.add_all([plan, admin]); await db.flush()
        plan_id, admin_id = plan.id, admin.id
    id, uid = await context.create(plan=plan_id)
    async with context.db() as db, db.begin():
        order = await db.get(Order, id)
        if vendor == "renshe":
            registration = RensheApplication(plan_id=plan_id, user_id=uid, status="pending_payment")
            db.add(registration); await db.flush(); order.application_id = registration.id
        else:
            batch = NispExamBatch(plan_id=plan_id) if vendor == "nisp" else H3cExamBatch(plan_id=plan_id,
                exam_code="TEST", identity_tag="TEST", coupon_price_cents=10000, student_price_cents=10000, full_price_cents=10000)
            db.add(batch); await db.flush()
            values = dict(batch_id=batch.id, plan_id=plan_id, user_id=uid, order_id=id, registration_no=uuid4().hex,
                candidate_idcard=f"{uid:018d}", candidate_snapshot={"name": "测试考生"}, status="pending_payment")
            registration = NispRegistration(**values, level="1") if vendor == "nisp" else H3cRegistration(**values, registration_type="full")
            db.add(registration); await db.flush()
        return id, uid, admin_id, registration.id


@pytest.mark.parametrize("vendor", ["h3c", "nisp", "renshe"])
async def test_dedicated_payment_and_offline_refund_never_calls_wechat(context, vendor):
    from app.domain.h3c.src.index import H3cRefundRequest, H3cRegistration
    from app.domain.nisp.src.index import NispRefundRequest, NispRegistration
    from app.domain.renshe.src.index import RensheRefundRequest, RensheApplication
    from app.schemas.offline_payment import RefundInput
    from app.schemas.renshe import RensheRefundDecision
    id, uid, admin, reg_id = await seed_registration(context, vendor)
    await context.submit(id, uid)
    await context.service.decide(id, admin, confirmation(), "confirm")
    registration_model = {"h3c": H3cRegistration, "nisp": NispRegistration, "renshe": RensheApplication}[vendor]
    refund_model = {"h3c": H3cRefundRequest, "nisp": NispRefundRequest, "renshe": RensheRefundRequest}[vendor]
    async with context.db() as db, db.begin():
        reg = await db.get(registration_model, reg_id)
        assert reg.status == ("pending_initial_review" if vendor == "renshe" else "pending_review")
        values = dict(order_id=id, user_id=uid, amount_cents=10000, status="requested", reason_code="test", requested_at=now())
        if vendor == "renshe":
            values.update(application_id=reg_id, request_kind="normal", previous_application_status=reg.status, due_at=now() + timedelta(days=1))
            reg.frozen_at = now(); reg.freeze_reason = "refund"
        else:
            values.update(registration_id=reg_id, request_kind="review_failed")
            reg.status = "pending_refund_confirmation"
        refund = refund_model(**values); db.add(refund); await db.flush(); refund_id = refund.id
    module = importlib.import_module(f"app.services.{vendor}_refund")
    service = getattr(module, {"h3c": "H3cRefundService", "nisp": "NispRefundService", "renshe": "RensheRefundService"}[vendor])()
    if vendor == "renshe":
        result = await service.decide(super_admin_id=admin, refund_id=refund_id, data=RensheRefundDecision(decision="approved"))
    else:
        result = await service.confirm(admin_id=admin, refund_id=refund_id)
    assert result.status == "approved"  # No WeChat config or network is available.
    file = await context.service.upload(id, UploadFile(filename="实际退款.pdf", file=BytesIO(b"%PDF-1.4 proof")), admin_id=admin)
    body = RefundInput(**confirmation().model_dump(), refund_kind=vendor, refund_id=refund_id, material_ids=[file["id"]])
    results = await asyncio.gather(*(context.service.refund(id, admin, body) for _ in range(2)))
    assert all(r["status"] == "refunded" for r in results)
    async with context.db() as db:
        assert (await db.get(registration_model, reg_id)).status == ("closed" if vendor == "renshe" else "refunded_closed")
        assert (await db.get(refund_model, refund_id)).status == "succeeded"


async def test_nisp_batch_cancellation_and_confirmation_are_consistent(context):
    from app.domain.nisp.src.index import NispRegistration
    from app.domain.plan.src.index import Plan
    from app.services.nisp_admin import NispAdminService
    id, uid, admin, reg_id = await seed_registration(context, "nisp")
    await context.submit(id, uid)
    async with context.db() as db:
        registration = await db.get(NispRegistration, reg_id)
        batch_id, plan_id = registration.batch_id, registration.plan_id
    results = await asyncio.gather(context.service.decide(id, admin, confirmation(), "confirm"),
        NispAdminService().cancel_batch(batch_id), return_exceptions=True)
    async with context.db() as db:
        order = await db.get(Order, id)
        reg = await db.get(NispRegistration, reg_id)
        assert (await db.get(Plan, plan_id)).status == "cancelled"
        assert (order.status == "pending" and order.offline_status == "exception") or (
            order.status == "paid" and reg.status == "pending_refund_confirmation")
        assert not any(isinstance(result, Exception) and not isinstance(result, ConflictException) for result in results)


async def test_generic_refund_requires_approval_and_real_material(context):
    from app.schemas.offline_payment import RefundInput
    id, uid = await context.create(); await context.submit(id, uid)
    await context.service.decide(id, 101, confirmation(), "confirm")
    file = await context.service.upload(id, UploadFile(filename="refund.pdf", file=BytesIO(b"%PDF-1.4 proof")), admin_id=101)
    body = RefundInput(**confirmation().model_dump(), refund_kind="order", refund_id=id, material_ids=[file["id"]])
    with pytest.raises(ConflictException):
        await context.service.refund(id, 101, body)
    await context.service.approve_generic_refund(id, 101, DecisionInput(expected_version=1, request_key=uuid4().hex, note="原业务退款审批已通过"))
    assert (await context.service.refund(id, 101, body))["status"] == "refunded"


async def test_resubmit_racing_stale_confirmation(context):
    id, uid = await context.create(); await context.submit(id, uid)
    await context.service.decide(id, 101, DecisionInput(expected_version=1, request_key=uuid4().hex, note="补交完整回单"), "return")
    results = await asyncio.gather(context.submit(id, uid, version=1),
        context.service.decide(id, 102, confirmation(), "confirm"), return_exceptions=True)
    assert isinstance(results[1], ConflictException)
    async with context.db() as db:
        order = await db.get(Order, id)
        payment = await db.scalar(select(OfflinePayment).where(OfflinePayment.order_id == id))
        assert order.status == "pending" and payment.current_version == 2


async def test_submission_racing_timeout(context):
    from app.services.order_timeout import OrderTimeoutCloseService
    id, uid = await context.create()
    file = await context.service.upload(id, UploadFile(filename="回单.pdf", file=BytesIO(b"%PDF-1.4 test")), user_id=uid)
    body = SubmissionInput(payer="测试转账人", transfer_time=now(), amount_cents=10000,
        material_ids=[file["id"]], expected_version=0, request_key=uuid4().hex)
    await asyncio.gather(context.service.submit(id, uid, body),
        OrderTimeoutCloseService().close_expired_pending_orders(now=now() + timedelta(days=3)), return_exceptions=True)
    async with context.db() as db:
        order = await db.get(Order, id)
        count = await db.scalar(select(func.count()).select_from(OfflinePaymentSubmission).where(OfflinePaymentSubmission.order_id == id))
        assert (order.status == "pending" and order.offline_status == "under_review" and count == 1) or (order.status == "closed" and count == 0)


async def test_exception_return_records_real_receipt_and_refund_without_reopening(context):
    from app.services.order import OrderService
    from app.schemas.offline_payment import ExceptionRefundInput
    id, uid = await context.create(); await OrderService().cancel_pending_order(uid, id)
    proof = await context.service.upload(id, UploadFile(filename="回单.pdf", file=BytesIO(b"%PDF-1.4 test")), user_id=uid)
    await context.service.submit(id, uid, SubmissionInput(payer="测试转账人", transfer_time=now(), amount_cents=10000,
        material_ids=[proof["id"]], expected_version=0, request_key=uuid4().hex), exception=True)
    returned = await context.service.upload(id, UploadFile(filename="退回.pdf", file=BytesIO(b"%PDF-1.4 test")), admin_id=101)
    body = ExceptionRefundInput(**confirmation().model_dump(), material_ids=[returned["id"]],
        refund_bank_time=now(), refund_bank_reference=uuid4().hex)
    assert (await context.service.resolve_exception(id, 101, body))["status"] == "closed"
    detail = await context.service.detail(id, uid)
    assert not detail["can_report_exception"] and detail["order_status"] == "closed"
    async with context.db() as db:
        assert (await db.get(Order, id)).paid_at is None
        assert await db.scalar(select(func.count()).select_from(OfflineBankEntry).where(OfflineBankEntry.order_id == id)) == 2


async def test_zero_coupon_settlement_still_requires_qualification_review(context):
    from app.domain.renshe.src.index import RensheApplication
    from app.services.renshe_refund import RensheRefundService
    from app.schemas.renshe import RensheRefundCreate
    id, uid, _admin, application_id = await seed_registration(context, "renshe")
    async with context.db() as db, db.begin():
        order = await db.get(Order, id)
        order.original_price = order.price
        order.discount_amount = order.price
        order.price = 0
    await context.service.settle_free(id, uid)
    async with context.db() as db:
        order = await db.get(Order, id)
        assert order.status == "paid" and order.payment_channel == "free" and order.offline_status is None
        assert (await db.get(RensheApplication, application_id)).status == "pending_initial_review"
        assert await db.scalar(select(func.count()).select_from(OfflineBankEntry).where(OfflineBankEntry.order_id == id)) == 0
    with pytest.raises(ConflictException, match="零元"):
        await RensheRefundService().request_refund(user_id=uid, application_id=application_id,
            data=RensheRefundCreate(request_kind="normal", reason_code="user_request", reason_detail="零元报名"))
