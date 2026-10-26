import asyncio
import importlib
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from openpyxl import load_workbook
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.datastructures import UploadFile

from app.domain.order.src.index import Order
from app.domain.plan.src.index import Plan
from app.domain.user.src.index import User, UserRealname
from app.models.cert_product import CertProduct
from app.models.offline_payment import OfflineBankEntry, OfflinePayment, OfflinePaymentAudit, OfflinePaymentMaterial, OfflinePaymentSubmission
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.schemas.offline_payment import ConfirmInput, DecisionInput, RefundInput, SettingsInput, SubmissionInput
from app.schemas.sangfor import SangforCreate, SangforDecision, SangforResubmit
from app.services.sangfor import SangforService
from app.services.offline_payment import OfflinePaymentService

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


def values(**changes):
    return dict(phone="13800000000", organization="测试学院", first_name="Xiaofu", last_name="Shen", exam_voucher_code="VENDOR-001") | changes


@pytest.fixture
async def context(monkeypatch, tmp_path):
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    @asynccontextmanager
    async def ctx():
        async with factory() as db:
            yield db
    for name in ("sangfor", "offline_payment", "order", "admin_order", "plan", "admin_cert_product"):
        monkeypatch.setattr(importlib.import_module(f"app.services.{name}"), "get_db_ctx", ctx)
    from app.port.config import settings
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "RENSHE_STORAGE_TYPE", "local")
    monkeypatch.setattr(settings, "BANK_TRANSFER_NEW_ORDERS_ENABLED", True)
    agreement_calls = []
    async def accepted(db, user_id, kind, **kwargs):
        agreement_calls.append((user_id, kind))
    monkeypatch.setattr(importlib.import_module("app.services.sangfor"), "ensure_accepted", accepted)
    service, bank = SangforService(), OfflinePaymentService()
    config = await bank.get_settings()
    await bank.save_settings(101, SettingsInput(account_name="深信服测试收款账户", bank_name="测试银行", account_number="6222000000000000", contact_name="测试管理员", contact_phone="13800000000", version=config["version"] if config else 0))
    prefix = "sf_" + uuid4().hex[:12]
    async with factory() as db, db.begin():
        product = await db.scalar(select(CertProduct).where(CertProduct.code == "SCSA-T"))
        created_product = product is None
        if product is None:
            product = CertProduct(type="sangfor", code="SCSA-T", name="scsa_t", chinese_name="安全技术认证工程师", is_active=True)
            db.add(product)
        user = User(openid=prefix)
        other = User(openid=prefix + "_other")
        db.add_all([user, other]); await db.flush()
        identity = UserRealname(user_id=user.id, user_type="enterprise", status="verified", real_name="实名考生", id_card_number="11010519491231002X")
        plan = Plan(product_type="SCSA-T", name=prefix, status="published", apply_start=datetime.now(timezone.utc) - timedelta(days=1),
            apply_end=datetime.now(timezone.utc) + timedelta(days=1), exam_date=datetime.now(timezone.utc) + timedelta(days=2), capacity=2, price_cents=12345)
        db.add_all([identity, plan]); await db.flush()
        uid, other_id, plan_id = user.id, other.id, plan.id
    async def create():
        return await service.create(uid, SangforCreate(batch_id=plan_id, **values()))
    async def pay(order):
        evidence = await bank.upload(order["id"], UploadFile(filename="转账凭证.pdf", file=BytesIO(b"%PDF-1.4 test")), user_id=uid)
        await bank.submit(order["id"], uid, SubmissionInput(payer="测试付款人", transfer_time=datetime.now(timezone.utc), amount_cents=12345,
            material_ids=[evidence["id"]], expected_version=0, request_key=uuid4().hex))
        await bank.decide(order["id"], 101, ConfirmInput(expected_version=1, request_key=uuid4().hex, note="已核实实际到账", amount_cents=12345,
            bank_time=datetime.now(timezone.utc), bank_reference=uuid4().hex), "confirm")
        return await service.detail(order["id"], uid)
    yield SimpleNamespace(db=factory, service=service, bank=bank, create=create, pay=pay, uid=uid, other_id=other_id, plan_id=plan_id, agreement_calls=agreement_calls)
    async with factory() as db, db.begin():
        order_ids = select(Order.id).where(Order.plan_id == plan_id)
        for model in (OfflineBankEntry, OfflinePaymentSubmission, OfflinePaymentMaterial, OfflinePaymentAudit, OfflinePayment):
            await db.execute(delete(model).where(model.order_id.in_(order_ids)))
        await db.execute(delete(Order).where(Order.plan_id == plan_id))
        await db.execute(delete(Plan).where(Plan.id == plan_id))
        await db.execute(delete(UserRealname).where(UserRealname.user_id.in_([uid, other_id])))
        await db.execute(delete(User).where(User.id.in_([uid, other_id])))
        if created_product:
            await db.execute(delete(CertProduct).where(CertProduct.code == "SCSA-T"))
    await engine.dispose()


async def test_public_batch_projection_and_owner_only_identity(context):
    batches = await context.service.batches(public=True)
    batch = next(row for row in batches if row["id"] == context.plan_id)
    assert not {"candidate_snapshot", "id_card", "exam_voucher_code", "contact_phone", "renshe_config"} & set(batch)
    order = await context.create()
    assert order["status"] == "pending_payment" and order["price_cents"] == 12345
    assert order["candidate_snapshot"]["name"] == "实名考生"
    assert context.agreement_calls == [(context.uid, "cert_registration")]
    with pytest.raises(NotFoundException):
        await context.service.detail(order["id"], context.other_id)
    with pytest.raises(BusinessException, match="实名认证"):
        await context.service.create(context.other_id, SangforCreate(batch_id=context.plan_id, **values()))


async def test_payment_correction_current_export_and_final_review(context):
    order = await context.create()
    with pytest.raises(ConflictException):
        await context.service.review(order["id"], 101, SangforDecision(expected_revision=1, action="approve"))
    order = await context.pay(order)
    assert order["payment_summary"]["review_payment_allowed"]
    returned = await context.service.review(order["id"], 101, SangforDecision(expected_revision=1, action="return", reason="请核对名字拼音", correction_due_at=datetime.now(timezone.utc) + timedelta(hours=2)))
    # Corrections survive the ordinary enrollment deadline and retain the paid order.
    async with context.db() as db, db.begin():
        plan = await db.get(Plan, context.plan_id)
        plan.apply_end = datetime.now(timezone.utc) - timedelta(hours=1)
    corrected = await context.service.resubmit(order["id"], context.uid, SangforResubmit(expected_revision=returned["revision"], **values(first_name="Xiao Fu")))
    assert corrected["order_id"] == order["order_id"] and corrected["current_version"] == 2
    assert corrected["versions"][0]["snapshot"]["first_name"] == "Xiaofu"
    assert corrected["candidate_snapshot"]["first_name"] == "Xiao Fu"
    with pytest.raises(ConflictException, match="刷新"):
        await context.service.review(order["id"], 101, SangforDecision(expected_revision=1, action="approve"))
    approved = await context.service.review(order["id"], 101, SangforDecision(expected_revision=corrected["revision"], action="approve"))
    with pytest.raises(ConflictException, match="成功导出"):
        await context.service.review(order["id"], 101, SangforDecision(expected_revision=approved["revision"], action="final_approve"))
    content = await context.service.export(context.plan_id, [order["id"]], 101)
    assert load_workbook(BytesIO(content))["Sheet1"]["H3"].value == "Xiao Fu"
    exported = await context.service.detail(order["id"])
    assert exported["exports"][-1]["version"] == 2
    result = await context.service.review(order["id"], 101, SangforDecision(expected_revision=exported["revision"], action="final_approve"))
    assert result["status"] == "final_approved" and result["order_status"] == "completed"
    page = await context.service.registrations(user_id=context.uid, state="final_approved", page_size=1)
    assert page["total"] == 1 and page["items"][0]["id"] == order["id"]


async def test_duplicate_concurrent_submission_has_one_order(context):
    results = await asyncio.gather(context.create(), context.create(), return_exceptions=True)
    assert sum(isinstance(row, dict) for row in results) == 1
    assert sum(isinstance(row, ConflictException) for row in results) == 1
    async with context.db() as db:
        assert await db.scalar(select(func.count()).select_from(Order).where(Order.plan_id == context.plan_id)) == 1


async def test_external_return_invalidates_export_and_concurrent_reviews_keep_one_decision(context):
    order = await context.pay(await context.create())
    command = SangforDecision(expected_revision=1, action="approve")
    results = await asyncio.gather(context.service.review(order["id"], 101, command), context.service.review(order["id"], 102, command), return_exceptions=True)
    assert sum(isinstance(item, dict) for item in results) == 1
    assert sum(isinstance(item, ConflictException) for item in results) == 1
    await context.service.export(context.plan_id, [order["id"]], 101)
    record = await context.service.detail(order["id"])
    returned = await context.service.review(order["id"], 101, SangforDecision(expected_revision=record["revision"], action="final_return", reason="券码需补正", correction_due_at=datetime.now(timezone.utc) + timedelta(hours=1)))
    corrected = await context.service.resubmit(order["id"], context.uid, SangforResubmit(expected_revision=returned["revision"], **values(exam_voucher_code="NEW-VENDOR-CODE")))
    approved = await context.service.review(order["id"], 101, SangforDecision(expected_revision=corrected["revision"], action="approve"))
    assert len(approved["exports"]) == 1 and approved["exports"][0]["version"] == 1
    with pytest.raises(ConflictException, match="成功导出"):
        await context.service.review(order["id"], 101, SangforDecision(expected_revision=approved["revision"], action="final_approve"))


async def test_generic_creation_and_review_cannot_bypass_sangfor_validation(context):
    from app.schemas.order import OrderCreate
    from app.schemas.admin import AdminOrderReview
    from app.services.order import OrderService
    from app.services.admin_order import AdminOrderService
    with pytest.raises(BusinessException, match="深信服"):
        await OrderService().create_order(context.uid, OrderCreate(order_kind="certification", product_type="SCSA-T", candidate_name="他人"))
    order = await context.pay(await context.create())
    with pytest.raises(BusinessException, match="深信服"):
        await AdminOrderService().review_order(order["id"], AdminOrderReview(action="approve"))


async def test_refund_approval_freezes_reviews_and_uses_existing_bank_refund(context):
    order = await context.pay(await context.create())
    await context.bank.approve_generic_refund(order["id"], 101, DecisionInput(expected_version=1, request_key=uuid4().hex, note="同意全额线下退款"))
    frozen = await context.service.detail(order["id"])
    assert frozen["status"] == "refund_processing" and not frozen["payment_summary"]["review_payment_allowed"]
    with pytest.raises(ConflictException, match="退款已审批"):
        await context.service.review(order["id"], 101, SangforDecision(expected_revision=1, action="approve"))
    proof = await context.bank.upload(order["id"], UploadFile(filename="退款凭证.pdf", file=BytesIO(b"%PDF-1.4 test")), admin_id=101)
    await context.bank.refund(order["id"], 101, RefundInput(expected_version=1, request_key=uuid4().hex, note="已实际退回",
        amount_cents=12345, bank_time=datetime.now(timezone.utc), bank_reference=uuid4().hex,
        material_ids=[proof["id"]], refund_kind="order", refund_id=order["id"]))
    assert (await context.service.detail(order["id"]))["status"] == "refunded_closed"


async def test_inactive_product_and_expired_correction_block(context):
    order = await context.pay(await context.create())
    with pytest.raises(BusinessException, match="截止时间"):
        await context.service.review(order["id"], 101, SangforDecision(expected_revision=1, action="return", reason="补正"))
    returned = await context.service.review(order["id"], 101, SangforDecision(expected_revision=1, action="return", reason="核对券码", correction_due_at=datetime.now(timezone.utc) + timedelta(hours=1)))
    async with context.db() as db, db.begin():
        row = await db.get(Order, order["id"])
        extra = dict(row.extra_data); extra["correction_due_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(); row.extra_data = extra
        product = await db.scalar(select(CertProduct).where(CertProduct.code == "SCSA-T")); product.is_active = False
    assert not any(row["id"] == context.plan_id for row in await context.service.batches(public=True))
    with pytest.raises(ConflictException, match="期限"):
        await context.service.resubmit(order["id"], context.uid, SangforResubmit(expected_revision=returned["revision"], **values()))
