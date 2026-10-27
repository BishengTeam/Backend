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
from sqlalchemy import delete, func, select, update
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

    for name in ["offline_payment", "order", "order_timeout", "nisp_admin", "nisp_refund", "renshe_refund", "h3c_refund", "h3c_registration", "nisp_registration", "renshe_review", "certification_final_review"]:
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
            await db.execute(update(Order).where(Order.id.in_(created_order_ids)).values(paid_at=None))
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
        order.paid_at = datetime(2094, 10, 22, tzinfo=timezone.utc)
        db.add(PaymentRefundTask(order_id=order_id, user_id=uid, amount_cents=10000,
            out_trade_no=order.out_trade_no, transaction_id=uuid4().hex,
            out_refund_no=uuid4().hex, status="succeeded", next_attempt_at=now(),
            succeeded_at=datetime(2094, 10, 23, tzinfo=timezone.utc)))
    service = admin_order.AdminOrderService()
    receipt = await service.reconciliation("2094-10-22")
    refund = await service.reconciliation("2094-10-23")
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
    await context.service.save_settings(101, SettingsInput(**{**settings, "account_number": "6222111111111111", "submit_hours": 72, "correction_hours": 96}))
    assert (await context.service.detail(id, uid))["account"]["account_number"] == "6222000000000000"
    assert (await context.service.detail(id, uid))["account"]["submit_hours"] == 24
    new_order, new_user = await context.create()
    new_snapshot = (await context.service.detail(new_order, new_user))["account"]
    assert (new_snapshot["account_number"], new_snapshot["submit_hours"], new_snapshot["correction_hours"]) == ("6222111111111111", 72, 96)
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


@pytest.mark.parametrize("channel", ["bank_transfer", "free", "wechat"])
async def test_review_payment_summary_accepts_only_real_confirmed_payment(context, channel):
    from app.services.registration_payment_guard import payment_summary
    id, uid = await context.create()
    async with context.db() as db:
        assert not (await payment_summary(db, await db.get(Order, id))).review_payment_allowed
    if channel == "bank_transfer":
        await context.submit(id, uid)
        await context.service.decide(id, 101, confirmation(), "confirm")
    else:
        async with context.db() as db, db.begin():
            order = await db.get(Order, id)
            order.payment_channel = channel
            order.offline_status = None
            order.status = "paid"
            order.price = 0 if channel == "free" else 10000
            order.paid_at = now()
            order.transaction_id = uuid4().hex if channel == "wechat" else None
    async with context.db() as db:
        summary = await payment_summary(db, await db.get(Order, id))
        assert summary.review_payment_allowed and summary.review_block_reason is None
        assert "materials" not in summary.model_dump() and "bank_reference" not in summary.model_dump()


@pytest.mark.parametrize("vendor", ["h3c", "nisp", "renshe"])
async def test_initial_and_final_review_cannot_bypass_payment(context, vendor):
    from app.services.h3c_registration import H3cRegistrationService
    from app.services.nisp_registration import NispRegistrationService
    from app.services.renshe_review import RensheReviewService
    from app.services.certification_final_review import H3cFinalReviewService, NispFinalReviewService
    from app.schemas.h3c_registration import H3cReviewDecision, H3cFinalReviewRequest
    from app.schemas.nisp import NispReviewDecisionRequest, NispFinalReviewRequest
    from app.schemas.renshe import RensheReviewCreate
    from app.domain.h3c.src.index import H3cRegistration
    from app.domain.nisp.src.index import NispRegistration
    from app.domain.renshe.src.index import RensheApplication, RensheApplicationVersion
    id, uid, admin, rid = await seed_registration(context, vendor)
    model = {"h3c": H3cRegistration, "nisp": NispRegistration, "renshe": RensheApplication}[vendor]
    async with context.db() as db, db.begin():
        reg = await db.get(model, rid)
        reg.status = "pending_initial_review" if vendor == "renshe" else "pending_review"
        if vendor == "renshe":
            from app.domain.renshe.src.index import RENSHE_DEFAULT_PROGRAM
            from app.domain.plan.src.index import Plan
            plan = await db.get(Plan, reg.plan_id)
            plan.product_type = "RS-ZY"
            (await db.get(Order, id)).product_type = "RS-ZY"
            for key in ("occupation_name", "occupation_code", "skill_level", "exam_type", "application_type"):
                setattr(plan, key, getattr(RENSHE_DEFAULT_PROGRAM, key))
            version = RensheApplicationVersion(application_id=rid, version_no=1, submitted_by_user_id=uid,
                realname_snapshot={}, student_snapshot={}, form_data={}, submitted_at=now())
            db.add(version); await db.flush(); reg.current_version_id = version.id
            from app.domain.renshe.src.index import RensheMaterial
            from app.services.renshe_project import default_configuration
            for requirement in default_configuration()["materials"]:
                extension = ".pdf" if ".pdf" in requirement["extensions"] else ".jpg"
                db.add(RensheMaterial(application_id=rid, version_id=version.id, kind=requirement["kind"],
                    source_storage_key="fixture/source", storage_key=f"fixture/{rid}/{requirement['kind']}", original_filename="proof" + extension,
                    extension=extension, content_type="application/pdf" if extension == ".pdf" else "image/jpeg", size_bytes=100, sha256="a" * 64))
    async def initial():
        if vendor == "h3c":
            return await H3cRegistrationService().review(admin_id=admin, registration_id=rid, decision_data=H3cReviewDecision(decision="approved"))
        if vendor == "nisp":
            return await NispRegistrationService().review(admin_id=admin, registration_id=rid, data=NispReviewDecisionRequest(decision="approved"))
        detail = await RensheReviewService().get_application(rid)
        current = next(version for version in detail.versions if version.id == detail.current_version_id)
        return await RensheReviewService().review(reviewer_id=admin, application_id=rid, stage="initial", data=RensheReviewCreate(
            decision="approved", expected_version_id=current.id,
            checklist={check["key"]: True for check in current.review_requirements["checks"]}))
    with pytest.raises(ConflictException, match="付款"):
        await initial()
    await context.submit(id, uid)
    await context.service.decide(id, admin, confirmation(), "confirm")
    await initial()
    if vendor == "renshe":
        from app.schemas.renshe import RensheBatchExternalReviewRequest
        stale = await RensheReviewService().batch_external_review(reviewer_id=admin,
            data=RensheBatchExternalReviewRequest(decision="approved", targets=[{"application_id": rid, "version_id": 2147483647}]))
        assert stale[0].status == "failed" and "版本" in stale[0].reason
    # A paid order marked as a financial exception must stop all further review writes.
    await context.service.decide(id, admin, DecisionInput(expected_version=1, request_key=uuid4().hex, note="到账资金待进一步核实"), "exception")
    if vendor == "renshe":
        with pytest.raises(ConflictException, match="异常"):
            await RensheReviewService().return_for_correction(reviewer_id=admin, application_id=rid,
                data=RensheReviewCreate(decision="rejected", reason="需补正", required_changes=["contact_phone"]))
        with pytest.raises(ConflictException, match="异常"):
            await RensheReviewService().review(reviewer_id=admin, application_id=rid, stage="external", data=RensheReviewCreate(decision="approved"))
    else:
        final = H3cFinalReviewService() if vendor == "h3c" else NispFinalReviewService()
        data = H3cFinalReviewRequest(export_item_id=1) if vendor == "h3c" else NispFinalReviewRequest(export_item_id=1)
        with pytest.raises(ConflictException, match="异常"):
            await final.review(admin_id=admin, registration_id=rid, data=data)
        with pytest.raises(ConflictException, match="异常"):
            await initial()
    if vendor == "renshe":
        from app.schemas.renshe import RensheBatchExternalReviewRequest
        result = await RensheReviewService().batch_external_review(reviewer_id=admin,
            data=RensheBatchExternalReviewRequest(decision="approved", targets=[{"application_id": rid}]))
        item = result[0]
    else:
        from app.schemas.h3c_registration import H3cBatchFinalReviewRequest
        from app.schemas.nisp import NispBatchFinalReviewRequest
        request = H3cBatchFinalReviewRequest if vendor == "h3c" else NispBatchFinalReviewRequest
        result = await final.review_batch(admin_id=admin, data=request(targets=[{"registration_id": rid, "export_item_id": 1}]))
        item = result.items[0]
    assert item.status == "failed" and not item.success and "异常" in item.reason


async def test_payment_guard_rejects_inconsistent_receipt_and_active_refund(context):
    from app.services.registration_payment_guard import payment_summary
    id, uid = await context.create(); await context.submit(id, uid)
    await context.service.decide(id, 101, confirmation(), "confirm")
    async with context.db() as db, db.begin():
        receipt = await db.scalar(select(OfflineBankEntry).where(OfflineBankEntry.order_id == id))
        receipt.amount_cents = 9999
    async with context.db() as db:
        assert not (await payment_summary(db, await db.get(Order, id))).review_payment_allowed
    # A real NISP refund request blocks the prerequisite even before registration status changes.
    from app.domain.nisp.src.index import NispRefundRequest
    id, uid, admin, rid = await seed_registration(context, "nisp")
    await context.submit(id, uid); await context.service.decide(id, admin, confirmation(), "confirm")
    async with context.db() as db, db.begin():
        db.add(NispRefundRequest(order_id=id, user_id=uid, registration_id=rid, amount_cents=10000,
            status="requested", request_kind="review_failed", reason_code="user_request", requested_at=now()))
    async with context.db() as db:
        summary = await payment_summary(db, await db.get(Order, id))
        assert not summary.review_payment_allowed and "退款" in summary.review_block_reason


@pytest.mark.parametrize("mutation", ["exception", "refund"])
async def test_review_serializes_with_financial_exception_and_refund(context, monkeypatch, mutation):
    import app.services.nisp_registration as registrations
    from app.services.registration_payment_guard import require_review_payment, payment_summary
    from app.schemas.nisp import NispReviewDecisionRequest, NispRejectRefundRequest
    id, uid, admin, rid = await seed_registration(context, "nisp")
    await context.submit(id, uid); await context.service.decide(id, admin, confirmation(), "confirm")
    entered, release = asyncio.Event(), asyncio.Event()
    async def pause_guard(db, order, registration=None):
        result = await require_review_payment(db, order, registration)
        entered.set()
        await release.wait()
        return result
    monkeypatch.setattr(registrations, "require_review_payment", pause_guard)
    review = asyncio.create_task(registrations.NispRegistrationService().review(admin_id=admin, registration_id=rid,
        data=NispReviewDecisionRequest(decision="approved")))
    await asyncio.wait_for(entered.wait(), 5)
    if mutation == "exception":
        financial = asyncio.create_task(context.service.decide(id, admin,
            DecisionInput(expected_version=1, request_key=uuid4().hex, note="资金待查"), "exception"))
    else:
        financial = asyncio.create_task(registrations.NispRegistrationService().reject_and_refund(admin_id=admin, registration_id=rid,
            data=NispRejectRefundRequest(reason_code="review_rejected", reason_detail="需要退费")))
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(financial), .2)
    finally:
        release.set()
    await asyncio.wait_for(asyncio.gather(review, financial), 10)
    async with context.db() as db:
        assert not (await payment_summary(db, await db.get(Order, id))).review_payment_allowed
    monkeypatch.setattr(registrations, "require_review_payment", require_review_payment)
    with pytest.raises(ConflictException):
        await registrations.NispRegistrationService().review(admin_id=admin, registration_id=rid, data=NispReviewDecisionRequest(decision="approved"))


@pytest.mark.parametrize("vendor", ["h3c", "nisp", "renshe"])
async def test_batch_list_finance_filters_and_public_summary(context, vendor):
    id, uid, admin, rid = await seed_registration(context, vendor)
    await context.submit(id, uid)
    async with context.db() as db:
        order = await db.get(Order, id)
        plan_id = order.plan_id
    async def query(state):
        if vendor == "h3c":
            from app.services.h3c_registration import H3cRegistrationService
            result = await H3cRegistrationService().list_admin_registrations(payment_state=state, keyword=None, registration_ids=[rid])
            return result.items
        if vendor == "nisp":
            from app.services.nisp_admin import NispAdminService
            from app.domain.nisp.src.index import NispRegistration
            async with context.db() as db:
                batch_id = (await db.get(NispRegistration, rid)).batch_id
            rows, _ = await NispAdminService().list_registrations(batch_id=batch_id, payment_state=state)
            return rows
        from app.services.renshe_review import RensheReviewService
        result = await RensheReviewService().list_applications(plan_id=plan_id, status=None, payment_state=state, keyword=str(rid), page=1, page_size=20)
        return result.items
    rows = await query("under_review")
    assert len(rows) == 1 and rows[0].payment_summary.offline_status == "under_review"
    assert not rows[0].payment_summary.review_payment_allowed
    await context.service.decide(id, admin, confirmation(), "confirm")
    rows = await query("confirmed")
    assert len(rows) == 1 and rows[0].payment_summary.review_payment_allowed
    assert not await query("under_review")
    await context.service.decide(id, admin, DecisionInput(expected_version=1, request_key=uuid4().hex, note="资金异常"), "exception")
    assert not await query("confirmed")
    rows = await query("exception")
    assert len(rows) == 1 and not rows[0].payment_summary.review_payment_allowed
