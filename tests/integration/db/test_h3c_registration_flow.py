import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def context(monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_async_engine(url, pool_size=4, max_overflow=4)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"h3cflow_{uuid4().hex[:12]}"

    @asynccontextmanager
    async def db_ctx():
        async with factory() as session:
            yield session

    import app.services.h3c_registration as registration_module

    monkeypatch.setattr(registration_module, "get_db_ctx", db_ctx)
    yield SimpleNamespace(factory=factory, prefix=prefix, db_ctx=db_ctx)

    from app.domain.h3c.src.index import (
        H3cExamBatch,
        H3cMaterial,
        H3cMaterialUpload,
        H3cRefundRequest,
        H3cRegistration,
        H3cReview,
    )
    from app.domain.order.src.index import Inventory, InventoryRecord, Order
    from app.domain.plan.src.index import Plan
    from app.domain.user.src.index import AdminUser, User, UserRealname
    from app.domain.certification.src.index import Certification

    async with factory() as db:
        batch_ids = select(H3cExamBatch.id).where(
            H3cExamBatch.exam_code.like(f"{prefix}%")
        )
        order_ids = select(Order.id).where(Order.product_type.like(f"{prefix}%"))
        user_ids = select(User.id).where(User.openid.like(f"{prefix}%"))
        registration_ids = select(H3cRegistration.id).where(
            H3cRegistration.user_id.in_(user_ids)
        )
        batches = (
            await db.scalars(
                select(H3cExamBatch).where(H3cExamBatch.exam_code.like(f"{prefix}%"))
            )
        ).all()
        inventory_refs = [f"h3c-batch-{batch.id}" for batch in batches]
        await db.execute(delete(H3cReview).where(H3cReview.registration_id.in_(registration_ids)))
        await db.execute(
            delete(H3cRefundRequest).where(
                H3cRefundRequest.registration_id.in_(registration_ids)
            )
        )
        await db.execute(
            delete(H3cMaterial).where(H3cMaterial.registration_id.in_(registration_ids))
        )
        await db.execute(
            delete(H3cMaterialUpload).where(
                H3cMaterialUpload.storage_key.like(f"h3c/materials/%{prefix}%")
            )
        )
        await db.execute(
            delete(H3cRegistration).where(H3cRegistration.id.in_(registration_ids))
        )
        inventory_ids = select(Inventory.id).where(
            Inventory.ref_code.in_(inventory_refs)
        )
        await db.execute(
            delete(InventoryRecord).where(InventoryRecord.inventory_id.in_(
                inventory_ids
            ))
        )
        await db.execute(delete(Order).where(Order.id.in_(order_ids)))
        await db.execute(
            delete(Inventory).where(Inventory.ref_code.in_(inventory_refs))
        )
        await db.execute(delete(H3cExamBatch).where(H3cExamBatch.id.in_(batch_ids)))
        await db.execute(delete(Plan).where(Plan.product_type.like(f"{prefix}%")))
        await db.execute(
            delete(Certification).where(Certification.code.like(f"{prefix}%"))
        )
        await db.execute(delete(UserRealname).where(UserRealname.user_id.in_(user_ids)))
        await db.execute(
            delete(AdminUser).where(AdminUser.username.like(f"{prefix}%"))
        )
        await db.execute(delete(User).where(User.id.in_(user_ids)))
        await db.commit()
    await engine.dispose()


async def _seed(context):
    from app.domain.certification.src.index import Certification
    from app.domain.h3c.src.index import H3cExamBatch, H3cMaterialUpload
    from app.domain.order.src.index import Inventory, InventoryRecord
    from app.domain.plan.src.index import Plan
    from app.domain.user.src.index import AdminUser, User, UserRealname

    code = f"{context.prefix}_H3C"
    now = datetime.now(timezone.utc)
    async with context.factory() as db:
        admin = AdminUser(
            username=f"{context.prefix}_admin",
            password_hash="integration-only-hash",
            role="h3c_admin",
            must_change_password=False,
        )
        db.add(admin)
        await db.flush()
        user = User(openid=f"{context.prefix}_user", phone="13800000001")
        db.add(user)
        await db.flush()
        db.add(
            UserRealname(
                user_id=user.id,
                user_type="normal",
                real_name="王小明",
                id_card_number="510101200001010123",
                status="verified",
            )
        )
        db.add(
            Certification(
                name=code,
                chinese_name=code,
                code=code,
                vendor="H3C",
                is_active=True,
            )
        )
        plan = Plan(
            product_type=code,
            name=f"{context.prefix} batch",
            apply_start=now - timedelta(hours=1),
            apply_end=now + timedelta(days=7),
            exam_date=now + timedelta(days=30),
            capacity=1,
            price_cents=20000,
            exam_location="成都",
            status="published",
            published_at=now,
        )
        db.add(plan)
        await db.flush()
        batch = H3cExamBatch(
            plan_id=plan.id,
            exam_code=f"{context.prefix}_GB0",
            country="CHN",
            language="CHS",
            identity_tag="内部员工",
            coupon_price_cents=0,
            student_price_cents=10000,
            full_price_cents=20000,
            payment_timeout_minutes=30,
            resubmission_window_hours=72,
            max_resubmissions=2,
            max_material_bytes=1024 * 1024,
        )
        db.add(batch)
        await db.flush()
        inventory = Inventory(
            inventory_type="h3c_batch",
            ref_code=f"h3c-batch-{batch.id}",
            total_quota=1,
            available_quota=1,
            locked_quota=0,
            sold_quota=0,
            is_active=True,
        )
        db.add(inventory)
        await db.flush()
        db.add(
            InventoryRecord(
                inventory_id=inventory.id,
                action="initialize",
                quantity=1,
                before_total_quota=0,
                before_available_quota=0,
                before_locked_quota=0,
                before_sold_quota=0,
                after_total_quota=1,
                after_available_quota=1,
                after_locked_quota=0,
                after_sold_quota=0,
                reason="test_seed",
            )
        )
        material_key = f"h3c/materials/{user.id}/{context.prefix}-coupon.jpg"
        db.add(
            H3cMaterialUpload(
                user_id=user.id,
                material_type="coupon_proof",
                storage_key=material_key,
                original_filename="coupon.jpg",
                size_bytes=128,
                sha256="a" * 64,
            )
        )
        await db.commit()
        return SimpleNamespace(
            admin_id=admin.id,
            user_id=user.id,
            batch_id=batch.id,
            plan_id=plan.id,
            code=code,
            material_key=material_key,
            inventory_id=inventory.id,
        )


def _request(context, seed, *, registration_type="coupon"):
    from app.schemas.h3c_registration import H3cOrderCreate

    return H3cOrderCreate(
        batch_id=seed.batch_id,
        registration_type=registration_type,
        candidate_name="王小明",
        gender="男",
        candidate_idcard="510101200001010123",
        school="智天远",
        address="成都市",
        phone="13800000001",
        email="user@example.com",
        education="大学本科",
        first_name_en="Xiaoming",
        last_name_en="Wang",
        coupon_code="COUPON-001" if registration_type == "coupon" else None,
        verify_code=None,
        coupon_proof_key=seed.material_key if registration_type == "coupon" else None,
        student_proof_key=None,
    )


async def test_h3c_zero_price_review_and_resubmission_refund_flow(context):
    from app.port.exceptions import ConflictException
    from app.services.h3c_registration import H3cRegistrationService

    seed = await _seed(context)
    service = H3cRegistrationService()
    created = await service.create_order(seed.user_id, _request(context, seed))

    assert created.status == "pending_review"
    assert created.order_status == "completed"
    assert created.price_cents == 0
    assert created.registration_no == f"H3C-COUPON-{created.id:08d}"
    assert [item.material_type for item in created.materials] == ["coupon_proof"]
    assert created.candidate_snapshot["birth_date"] == "2000/01/01"

    with pytest.raises(ConflictException, match="该身份证号已报名此考试批次"):
        await service.create_order(seed.user_id, _request(context, seed))

    rejected = await service.review(
        admin_id=seed.admin_id,
        registration_id=created.id,
        decision_data={
            "decision": "rejected",
            "reason_code": "image_unclear",
            "reason_detail": "图片模糊",
            "rejected_material_types": ["coupon_proof"],
        },
    )
    assert rejected.status == "rejected_awaiting_resubmission"
    assert rejected.resubmission_due_at is not None

    from app.domain.h3c.src.index import H3cMaterialUpload

    replacement_key = f"h3c/materials/{seed.user_id}/{context.prefix}-replacement.jpg"
    async with context.factory() as db:
        db.add(
            H3cMaterialUpload(
                user_id=seed.user_id,
                material_type="coupon_proof",
                storage_key=replacement_key,
                original_filename="replacement.jpg",
                size_bytes=256,
                sha256="b" * 64,
            )
        )
        await db.commit()
    resubmitted = await service.resubmit_materials(
        seed.user_id,
        created.id,
        {"coupon_proof_key": replacement_key, "student_proof_key": None},
    )
    assert resubmitted.status == "pending_review"
    assert resubmitted.resubmission_count == 1
    assert len(resubmitted.materials) == 2
    assert [item.is_current for item in resubmitted.materials] == [False, True]

    second_reject = await service.review(
        admin_id=seed.admin_id,
        registration_id=created.id,
        decision_data={
            "decision": "rejected",
            "reason_code": "suspected_forged_material",
            "reason_detail": "材料存疑",
            "rejected_material_types": ["coupon_proof"],
        },
    )
    assert second_reject.status == "rejected_awaiting_resubmission"

    second_replacement_key = f"h3c/materials/{seed.user_id}/{context.prefix}-replacement-2.jpg"
    async with context.factory() as db:
        db.add(
            H3cMaterialUpload(
                user_id=seed.user_id,
                material_type="coupon_proof",
                storage_key=second_replacement_key,
                original_filename="replacement-2.jpg",
                size_bytes=256,
                sha256="c" * 64,
            )
        )
        await db.commit()
    second_resubmission = await service.resubmit_materials(
        seed.user_id,
        created.id,
        {"coupon_proof_key": second_replacement_key, "student_proof_key": None},
    )
    assert second_resubmission.status == "pending_review"
    assert second_resubmission.resubmission_count == 2

    final_reject = await service.review(
        admin_id=seed.admin_id,
        registration_id=created.id,
        decision_data={
            "decision": "rejected",
            "reason_code": "suspected_forged_material",
            "reason_detail": "第二次补交后仍存疑",
            "rejected_material_types": ["coupon_proof"],
        },
    )
    assert final_reject.status == "pending_refund_confirmation"

    from app.domain.h3c.src.index import H3cRefundRequest

    async with context.factory() as db:
        refund = await db.scalar(
            select(H3cRefundRequest).where(
                H3cRefundRequest.registration_id == created.id
            )
        )
        assert refund is not None
        assert refund.request_kind == "review_failed"
        assert refund.amount_cents == 0
