"""Isolated SQL/storage regressions; SQLite does not prove PostgreSQL lock behavior."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.adapter.database import Base
from app.domain.h3c.src.index import H3cExamBatch
from app.domain.order.src.index import Inventory, InventoryRecord, Order
from app.domain.plan.src.index import Plan
from app.integrations.h3c_storage import H3cObjectStorage, validate_h3c_jpeg
from app.port.exceptions import BusinessException, ConflictException, ValidationException
from app.schemas.h3c_registration import H3cExamBatchUpdate, H3cOrderCreate, H3cResubmissionCreate
from app.services.h3c_admin import H3cAdminBatchService


@pytest.fixture
async def batches(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda db: Base.metadata.create_all(db, tables=[
                Plan.__table__, H3cExamBatch.__table__, Order.__table__,
                Inventory.__table__, InventoryRecord.__table__,
            ]))
        factory = async_sessionmaker(engine, expire_on_commit=False)

        @asynccontextmanager
        async def context():
            async with factory() as db:
                yield db

        monkeypatch.setattr("app.services.h3c_admin.get_db_ctx", context)
        # Only response formatting and the separate security audit writer are stubbed.
        # All batch/inventory/order queries, flushes and transaction boundaries are real.
        monkeypatch.setattr("app.services.h3c_admin.AdminSecurityAuditService.append", Mock())
        now = datetime.now()
        monkeypatch.setattr("app.services.h3c_admin._now", lambda: now)  # SQLite drops tzinfo.
        async with factory() as db, db.begin():
            db.add(Plan(id=1, product_type="H3C-TEST", name="草稿批次", capacity=10, status="draft",
                        apply_start=now - timedelta(days=1), apply_end=now + timedelta(days=1),
                        exam_date=now + timedelta(days=2)))
            db.add(H3cExamBatch(id=1, plan_id=1, exam_code="H3C-TEST", identity_tag="TEST",
                                coupon_price_cents=100, student_price_cents=200, full_price_cents=300))
            db.add(Inventory(id=1, inventory_type="h3c_batch", ref_code="h3c-batch-1",
                             total_quota=10, available_quota=10, locked_quota=0, sold_quota=0))
        service = H3cAdminBatchService()
        service._response = AsyncMock(return_value=None)
        yield service, factory
    finally:
        await engine.dispose()


async def snapshot(factory):
    async with factory() as db:
        plan, stock = await db.get(Plan, 1), await db.get(Inventory, 1)
        records = list(await db.scalars(select(InventoryRecord).order_by(InventoryRecord.id)))
        return plan, stock, records


async def test_draft_capacity_increase_decrease_and_noop_keep_stock_and_history(batches):
    service, factory = batches
    for capacity in [20, 5, 5]:
        await service.update_batch(admin_id=1, batch_id=1, data=H3cExamBatchUpdate(capacity=capacity))
    plan, stock, records = await snapshot(factory)
    assert plan.capacity == stock.total_quota == stock.available_quota == 5
    assert stock.locked_quota == stock.sold_quota == 0
    assert len(records) == 2
    assert [r.quantity for r in records] == [10, -15]
    assert [(r.before_total_quota, r.before_available_quota, r.after_total_quota, r.after_available_quota)
            for r in records] == [(10, 10, 20, 20), (20, 20, 5, 5)]


@pytest.mark.parametrize("change", ["order", "locked", "sold", "mismatch", "missing", "published"])
async def test_unsafe_capacity_changes_are_rejected_without_partial_updates(batches, change):
    service, factory = batches
    async with factory() as db, db.begin():
        plan, stock = await db.get(Plan, 1), await db.get(Inventory, 1)
        if change == "order":
            db.add(Order(id=1, user_id=1, order_kind="certification", product_type="H3C-TEST",
                         plan_id=1, price=300, status="pending"))
        elif change == "locked": stock.locked_quota, stock.available_quota = 1, 9
        elif change == "sold": stock.sold_quota, stock.available_quota = 1, 9
        elif change == "mismatch": plan.capacity = 12
        elif change == "missing": await db.delete(stock)
        elif change == "published": plan.status = "published"
    with pytest.raises(ConflictException):
        await service.update_batch(admin_id=1, batch_id=1,
                                   data=H3cExamBatchUpdate(capacity=20, name="不应保存"))
    plan, stock, records = await snapshot(factory)
    assert plan.name == "草稿批次"
    assert plan.capacity == (12 if change == "mismatch" else 10)
    assert not records
    if stock:
        assert stock.total_quota == 10


async def test_capacity_and_ledger_rollback_when_later_operation_fails(batches, monkeypatch):
    service, factory = batches
    monkeypatch.setattr("app.services.h3c_admin.AdminSecurityAuditService.append",
                        Mock(side_effect=RuntimeError("audit unavailable")))
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await service.update_batch(admin_id=1, batch_id=1, data=H3cExamBatchUpdate(capacity=20))
    plan, stock, records = await snapshot(factory)
    assert plan.capacity == stock.total_quota == stock.available_quota == 10
    assert not records


async def test_publish_blocks_legacy_mismatch_and_accepts_synchronized_capacity(batches):
    service, factory = batches
    async with factory() as db, db.begin():
        (await db.get(Plan, 1)).capacity = 12
    with pytest.raises(ConflictException) as error:
        await service.publish_batch(admin_id=1, batch_id=1)
    assert "名额与库存不一致" in error.value.message
    assert (await snapshot(factory))[0].status == "draft"
    async with factory() as db, db.begin():
        (await db.get(Plan, 1)).capacity = 10
    await service.update_batch(admin_id=1, batch_id=1, data=H3cExamBatchUpdate(capacity=20))
    await service.publish_batch(admin_id=1, batch_id=1)
    plan, stock, _ = await snapshot(factory)
    assert plan.status == "published" and stock.total_quota == plan.capacity == 20


ORDER = dict(batch_id=1, registration_type="full", candidate_name="测试", gender="男",
             candidate_idcard="110101199001011234", school="学校", address="地址",
             phone="13800138000", email="test@example.com", education="本科",
             first_name_en="Shi", last_name_en="Ce")


@pytest.mark.parametrize("phone", ["12345678901", "12345", "abc", "12800138000", "１3800138000", "1380013800a"])
@pytest.mark.parametrize("model", [H3cOrderCreate, H3cResubmissionCreate])
def test_signup_and_correction_reject_invalid_phones(model, phone):
    with pytest.raises(ValidationError):
        model.model_validate(ORDER | {"phone": phone} if model is H3cOrderCreate else {"phone": phone})


@pytest.mark.parametrize("phone", ["13800138000", "16600138000", "19900138000"])
def test_valid_phones_and_correction_without_phone(phone):
    assert H3cOrderCreate(**(ORDER | {"phone": phone})).phone == phone
    assert H3cResubmissionCreate(phone=phone).phone == phone
    assert H3cResubmissionCreate(email="test@example.com").phone is None


def jpeg_bytes():
    output = BytesIO()
    Image.new("RGB", (32, 32), "white").save(output, format="JPEG")
    return output.getvalue()


@pytest.mark.parametrize("data", [b"\xff\xd8\xffnot-a-jpeg", jpeg_bytes()[:-30]])
async def test_fake_or_truncated_jpeg_is_rejected_before_storage(data):
    storage = H3cObjectStorage()
    storage._put = AsyncMock()
    with pytest.raises(ValidationException) as error:
        await storage.save_source(user_id=1, filename="proof.jpg", content_type="image/jpeg",
                                  data=data, max_bytes=10485760)
    assert "图片损坏或无法读取" in error.value.message
    storage._put.assert_not_awaited()


async def test_valid_jpeg_is_saved_and_size_limits_remain_effective(monkeypatch):
    storage = H3cObjectStorage()
    storage._put = AsyncMock()
    data = jpeg_bytes()
    key, size, digest = await storage.save_source(user_id=1, filename="proof.jpg", content_type="image/jpeg",
                                                 data=data, max_bytes=10485760)
    assert key.startswith("h3c/materials/1/") and size == len(data) and len(digest) == 64
    storage._put.assert_awaited_once()
    with pytest.raises(ValidationException) as error:
        await storage.save_source(user_id=1, filename="proof.jpg", content_type="image/jpeg",
                                  data=data, max_bytes=len(data) - 1)
    assert "超过大小限制" in error.value.message
    monkeypatch.setattr("app.integrations.h3c_storage.H3C_MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ValidationException) as error:
        validate_h3c_jpeg(data)
    assert "分辨率过高" in error.value.message


async def test_export_of_legacy_bad_image_identifies_registration_and_material(tmp_path):
    from app.services.h3c_export import H3cExportService

    async def download(key, target):
        target.write_bytes(b"\xff\xd8\xffnot-a-jpeg")

    service = H3cExportService(storage=SimpleNamespace(download_file=download))
    batch = SimpleNamespace(exam_code="TEST", country="CHN", language="CHS", identity_tag="TEST",
                             training_org=None, training_teacher=None, training_address=None,
                             training_start=None, training_end=None)
    registration = SimpleNamespace(id=1, registration_no="H3C-STUDENT-00000001",
                                     candidate_snapshot={"candidate_name": "测试", "verify_code": "CODE"})
    row = SimpleNamespace(registration=registration, materials=[
        SimpleNamespace(id=1, material_type="student_proof", storage_key="proof.jpg"),
    ])
    with pytest.raises(BusinessException) as error:
        await service._build_workbook(batch=batch, registration_type="student", rows=[row], destination=tmp_path)
    assert "H3C-STUDENT-00000001" in error.value.message
    assert "学生证明" in error.value.message and "打回补正" in error.value.message
