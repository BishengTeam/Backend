"""认证附件三级作用域：批次级 / 考场级 / 厂商级合并与去重。"""
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.admin.cert_attachment import AttachmentCreate, create_attachment
from app.api.admin.exam_schedule import ScheduleCreate, create_schedule
from app.api.certification_journey import batch_attachments
from app.domain.exam.src.index import (
    CertAttachment,
    ExamSchedule,
    ExamScheduleSubject,
    ExamSeatQuota,
    ExamTimeSlot,
)
from app.domain.plan.src.index import Plan
from app.domain.user.src.index import User
from app.models.cert_product import CertProduct
from app.port.exceptions import NotFoundException
from app.port.exceptions import BusinessException
from app.services.cert_attachment import (
    list_plan_attachments,
    list_schedule_attachments,
)
from app.services.certification_journey import CertificationJourneyService


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]

SUPER_ADMIN = SimpleNamespace(role="super_admin", permissions=None)


@pytest.fixture
async def context(monkeypatch, tmp_path):
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"attach_scope_{uuid4().hex[:8]}"
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr("app.services.upload.UPLOAD_DIR", str(upload_dir))

    @asynccontextmanager
    async def db_ctx():
        async with factory() as session:
            yield session

    monkeypatch.setattr(
        "app.api.certification_journey.get_db_ctx", db_ctx
    )
    today = date.today()
    exam_day = today + timedelta(days=((4 - today.weekday()) % 7 or 7) + 7)
    now = datetime.now(timezone.utc)

    async with factory() as db, db.begin():
        # 公开批次目录只接受深信服官方方向编码，复用固定 code 以便走真实 notice 链路。
        product = await db.scalar(
            select(CertProduct).where(CertProduct.code == "SCSA-S")
        )
        if product is None:
            product = CertProduct(
                type="sangfor",
                code="SCSA-S",
                name="scsa_s",
                chinese_name="SCSA-S 安全服务认证工程师",
                is_active=True,
                coupon_price_cents=0,
                student_price_cents=0,
                full_price_cents=100,
            )
            db.add(product)
            await db.flush()
        else:
            product.is_active = True
        plan = Plan(
            product_type=product.code,
            name=f"{prefix} batch",
            status="published",
            apply_start=now - timedelta(days=1),
            apply_end=now + timedelta(days=10),
            exam_date=now + timedelta(days=30),
            capacity=100,
            price_cents=100,
            registration_notice="报名完成后不可退款，请确认后提交。",
        )
        db.add(plan)
        await db.flush()
        seed = SimpleNamespace(
            factory=factory,
            prefix=prefix,
            product_id=product.id,
            plan_id=plan.id,
            exam_day=exam_day,
            upload_dir=upload_dir,
        )

    async with factory() as db:
        schedule = await create_schedule(
            data=ScheduleCreate(
                vendor="sangfor",
                name=f"{prefix} schedule",
                open_date_start=exam_day,
                open_date_end=exam_day,
                allowed_weekdays=[4],
                product_ids=[seed.product_id],
                time_slots=[{"start_time": "09:30"}],
                publish=True,
            ),
            _admin=SUPER_ADMIN,
            db=db,
        )
        seed.schedule_id = schedule.data["id"]

    yield seed

    async with factory() as db, db.begin():
        schedule_ids = select(ExamSchedule.id).where(ExamSchedule.name.like(f"{prefix}%"))
        plan_ids = select(Plan.id).where(Plan.name.like(f"{prefix}%"))
        await db.execute(delete(CertAttachment).where(
            CertAttachment.vendor == "sangfor",
            CertAttachment.plan_id.in_(plan_ids),
        ))
        await db.execute(delete(CertAttachment).where(
            CertAttachment.file_url.like(f"/api/media/cert-attachments/{prefix}%")
        ))
        await db.execute(delete(CertAttachment).where(
            CertAttachment.schedule_id.in_(schedule_ids)
        ))
        await db.execute(delete(ExamSeatQuota).where(
            ExamSeatQuota.schedule_id.in_(schedule_ids)
        ))
        await db.execute(delete(ExamScheduleSubject).where(
            ExamScheduleSubject.schedule_id.in_(schedule_ids)
        ))
        await db.execute(delete(ExamTimeSlot).where(
            ExamTimeSlot.schedule_id.in_(schedule_ids)
        ))
        await db.execute(delete(ExamSchedule).where(ExamSchedule.id.in_(schedule_ids)))
        await db.execute(delete(Plan).where(Plan.id.in_(plan_ids)))

    await engine.dispose()


async def _add(context, db, **kwargs):
    url = kwargs["file_url"]
    key = url.removeprefix("/api/media/")
    path = Path(context.upload_dir) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.7\nfixture")
    await create_attachment(
        data=AttachmentCreate(vendor="sangfor", **kwargs),
        _admin=SUPER_ADMIN,
        db=db,
    )


async def test_batch_and_schedule_attachments_merge_vendor_scope(context):
    async with context.factory() as db:
        await _add(context, db, plan_id=context.plan_id, title="报名表模板", file_url=f"/api/media/cert-attachments/{context.prefix}_a.pdf")
        await _add(context, db, title="官网激活指引", file_url=f"/api/media/cert-attachments/{context.prefix}_vendor.pdf", sort_order=1)
        await _add(context, db, schedule_id=context.schedule_id, title="考场须知", file_url=f"/api/media/cert-attachments/{context.prefix}_s.pdf")
        # 同一文件同时挂批次与厂商级：按 file_url 去重，保留排序靠前的一条。
        await _add(context, db, plan_id=context.plan_id, title="重复引用", file_url=f"/api/media/cert-attachments/{context.prefix}_vendor.pdf", sort_order=9)

    async with context.factory() as db:
        plan_rows = await list_plan_attachments(db, "sangfor", context.plan_id)
        assert [row["title"] for row in plan_rows] == ["报名表模板", "官网激活指引"]
        assert len({row["file_url"] for row in plan_rows}) == 2

        schedule_rows = await list_schedule_attachments(db, "sangfor", context.schedule_id)
        assert [row["title"] for row in schedule_rows] == ["考场须知", "官网激活指引"]


async def test_public_batch_attachments_require_published_plan_and_vendor(context):
    async with context.factory() as db:
        await _add(context, db, plan_id=context.plan_id, title="报名表模板", file_url=f"/api/media/cert-attachments/{context.prefix}_a.pdf")
        await _add(context, db, title="官网激活指引", file_url=f"/api/media/cert-attachments/{context.prefix}_vendor.pdf")

    response = await batch_attachments(vendor="sangfor", batch_id=context.plan_id)
    assert {row["title"] for row in response.data} == {"报名表模板", "官网激活指引"}

    with pytest.raises(NotFoundException):
        await batch_attachments(vendor="h3c", batch_id=context.plan_id)

    async with context.factory() as db, db.begin():
        plan = await db.get(Plan, context.plan_id)
        plan.status = "draft"

    with pytest.raises(NotFoundException):
        await batch_attachments(vendor="sangfor", batch_id=context.plan_id)


async def test_notice_payload_carries_registration_notice_without_description_fallback(context):
    async with context.factory() as db:
        notice = await CertificationJourneyService().notice_in_db(
            db, "sangfor", context.plan_id
        )

    assert notice["registration_notice"] == "报名完成后不可退款，请确认后提交。"

    async with context.factory() as db, db.begin():
        plan = await db.get(Plan, context.plan_id)
        plan.registration_notice = None
        plan.description = "营销介绍文本"

    async with context.factory() as db:
        empty_notice = await CertificationJourneyService().notice_in_db(
            db, "sangfor", context.plan_id
        )

    assert empty_notice["registration_notice"] == ""
    assert empty_notice["batch_description"] == "营销介绍文本"


async def test_attachment_creation_rejects_missing_uploaded_file(context):
    async with context.factory() as db:
        with pytest.raises(BusinessException, match="附件文件不存在"):
            await create_attachment(
                data=AttachmentCreate(
                    vendor="sangfor",
                    plan_id=context.plan_id,
                    title="伪造附件",
                    file_url="/api/media/cert-attachments/not-uploaded.pdf",
                ),
                _admin=SUPER_ADMIN,
                db=db,
            )
