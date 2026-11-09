"""Regression coverage for NISP signup/correction and its fixed material policy."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.domain.nisp.src.constants import NISP_MAX_MATERIAL_BYTES
from app.domain.nisp.src.model.nisp import NISP_REGISTRATION_STATUSES
from app.integrations.nisp_storage import NispObjectStorage
from app.port.exceptions import BusinessException, ValidationException
from app.schemas.nisp import NispExamBatchCreate, NispOrderCreate, NispResubmitRequest
from app.services.nisp_registration import NispRegistrationService


ORDER = dict(
    batch_id=1, level="2", name="测试考生", pinyin="CE SHI", major="信息安全",
    school="测试学校", id_card="110101199001011234", phone="13800138000",
    email="test@example.com", province="四川", gender="男", age=20,
    education="本科", address="测试地址", zip_code="010000",
    id_card_both_sides_key="id.pdf", portrait_photo_key="photo.jpg",
    xuexin_report_key="report.pdf", application_form_key="form.pdf",
)


@pytest.mark.parametrize("model", [NispOrderCreate, NispResubmitRequest])
@pytest.mark.parametrize("field,value", [
    ("phone", "12345678901"), ("phone", "12800138000"),
    ("phone", "1380013800a"), ("phone", "1380013800"),
    ("phone", "１3800138000"),
    ("zip_code", "12345"), ("zip_code", "1234567"),
    ("zip_code", "abcdef"), ("zip_code", "１２３４５６"),
])
def test_signup_and_correction_reject_invalid_contact_fields(model, field, value):
    data = ORDER | {field: value} if model is NispOrderCreate else {field: value}
    with pytest.raises(ValidationError) as error:
        model.model_validate(data)
    assert error.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize("phone", ["13800138000", "16600138000", "19900138000"])
def test_valid_contacts_and_optional_level_one_postcode(phone):
    assert NispOrderCreate(**(ORDER | {"phone": phone})).zip_code == "010000"
    assert NispResubmitRequest(phone=phone, zip_code="010000").phone == phone
    assert NispOrderCreate(**(ORDER | {"level": "1", "zip_code": None})).zip_code is None
    assert NispResubmitRequest(email="test@example.com").zip_code is None


def test_legacy_batch_input_only_accepts_the_actual_upload_limit():
    data = dict(plan_product_id=1, level="1", level1_price_cents=100, level2_price_cents=100)
    assert NispExamBatchCreate(**data).max_material_bytes == NISP_MAX_MATERIAL_BYTES
    for limit in [5 * 1024 * 1024, 20 * 1024 * 1024]:
        with pytest.raises(ValidationError):
            NispExamBatchCreate(**data, max_material_bytes=limit)
    assert "final_approved" in NISP_REGISTRATION_STATUSES


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,filename,content_type", [
    ("portrait_photo", "photo.jpg", "image/jpeg"),
    ("id_card_both_sides", "id.pdf", "application/pdf"),
    ("xuexin_report", "report.pdf", "application/pdf"),
    ("application_form", "form.pdf", "application/pdf"),
])
async def test_all_material_types_enforce_ten_mb_before_storage(kind, filename, content_type):
    storage = NispObjectStorage()
    storage._put = AsyncMock()
    args = dict(user_id=1, material_type=kind, filename=filename, content_type=content_type)
    with pytest.raises(ValidationException) as error:
        await storage.save_source(**args, data=b"x" * (NISP_MAX_MATERIAL_BYTES + 1))
    assert "10MB" in error.value.message
    storage._put.assert_not_awaited()
    _, size, _ = await storage.save_source(**args, data=b"x" * NISP_MAX_MATERIAL_BYTES)
    assert size == NISP_MAX_MATERIAL_BYTES


@pytest.mark.asyncio
async def test_oversized_receipt_cannot_replace_current_material():
    material = SimpleNamespace(user_id=1, material_type="portrait_photo", registration_id=None,
                               size_bytes=NISP_MAX_MATERIAL_BYTES + 1)
    db = SimpleNamespace(scalar=AsyncMock(return_value=material), execute=AsyncMock())
    with pytest.raises(BusinessException) as error:
        await NispRegistrationService()._bind_material(
            db, user_id=1, registration_id=1, material_type="portrait_photo",
            storage_key="nisp/materials/1/photo.jpg", replace_current=True,
        )
    assert "10MB" in error.value.message
    assert material.registration_id is None
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_material_without_size_metadata_can_still_be_bound():
    material = SimpleNamespace(
        user_id=1,
        material_type="portrait_photo",
        registration_id=None,
        size_bytes=None,
        version_no=None,
        is_current=False,
        bound_at=None,
    )
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[material, 0]))

    bound = await NispRegistrationService()._bind_material(
        db, user_id=1, registration_id=1, material_type="portrait_photo",
        storage_key="nisp/materials/1/legacy.jpg",
    )

    assert bound is material
    assert material.registration_id == 1
    assert material.version_no == 1
    assert material.is_current is True
    assert material.bound_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_limit", [5 * 1024 * 1024, 20 * 1024 * 1024])
async def test_batch_responses_advertise_actual_limit_for_legacy_rows(monkeypatch, stored_limit):
    import app.adapter.database as database
    import app.api.nisp as api
    import app.services.nisp_admin as admin

    now = datetime.now(timezone.utc)
    batch = SimpleNamespace(
        id=1, plan_product_id=1, level="1", training_org=None, training_teacher=None,
        training_address=None, training_start=None, training_end=None,
        level1_price_cents=100, level2_price_cents=200, payment_timeout_minutes=30,
        resubmission_window_hours=72, max_resubmissions=2, max_material_bytes=stored_limit,
        created_at=now, updated_at=now,
    )
    plan = SimpleNamespace(id=1, name="测试批次", status="published", capacity=10,
                           apply_start=now, apply_end=now, exam_date=now, exam_location=None)
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [(batch, plan)])),
                         get=AsyncMock(return_value=plan), scalar=AsyncMock(return_value=None))

    @asynccontextmanager
    async def context():
        yield db

    monkeypatch.setattr(database, "get_db_ctx", context)
    monkeypatch.setattr(api, "occupied_count", AsyncMock(return_value=0))
    monkeypatch.setattr(admin, "occupied_count", AsyncMock(return_value=0))
    public = await api.list_batches(level="1")
    assert public.data[0].max_material_bytes == NISP_MAX_MATERIAL_BYTES
    db.execute.return_value = SimpleNamespace(all=lambda: [])
    detail = await admin.NispAdminService()._response(db, batch)
    assert detail.max_material_bytes == NISP_MAX_MATERIAL_BYTES
    assert batch.max_material_bytes == stored_limit  # No implicit data migration.
