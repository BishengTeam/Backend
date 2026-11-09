import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.plan.src.index import Plan
from app.domain.h3c.src.index import H3cExamBatch
from app.domain.nisp.src.index import NispExamBatch
from app.models.cert_product import CertProduct
from app.models.certification_journey import CertificationJourneyContent
from app.port.exceptions import BusinessException, ConflictException, NotFoundException
from app.schemas.certification_journey import JourneyContentUpdate
from app.services.certification_journey import CertificationJourneyService, confirmed_notice

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def context(monkeypatch):
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    @asynccontextmanager
    async def ctx():
        async with factory() as db:
            yield db
    monkeypatch.setattr("app.services.certification_journey.get_db_ctx", ctx)
    prefix = "journey_" + uuid4().hex[:10]
    async with factory() as db:
        products, plans, batches = [], [], []
        for vendor in ("h3c", "nisp", "renshe"):
            product = CertProduct(type=vendor, code=f"{prefix}_{vendor}", name=vendor, chinese_name=f"测试{vendor}", is_active=True, renshe_status="available" if vendor == "renshe" else None)
            db.add(product); await db.flush(); products.append(product)
            plan = Plan(product_type=product.code, name=f"{prefix}_{vendor}", status="published", capacity=20, price_cents=5000,
                apply_start=datetime.now(timezone.utc) - timedelta(days=1), apply_end=datetime.now(timezone.utc) + timedelta(days=1),
                exam_date=datetime.now(timezone.utc) + timedelta(days=5), renshe_config={"qualification_rule": "level_five_employment_v1", "reporting_policy": {"contract_version": 2}} if vendor == "renshe" else None)
            db.add(plan); await db.flush(); plans.append(plan)
            batch = H3cExamBatch(plan_id=plan.id, exam_code="TEST", identity_tag="TEST", coupon_price_cents=1000, student_price_cents=2000, full_price_cents=3000) if vendor == "h3c" else NispExamBatch(plan_product_id=_pp_plan.id, level="1", level1_price_cents=1000, level2_price_cents=2000) if vendor == "nisp" else None
            if batch: db.add(batch); await db.flush()
            batches.append(batch)
        await db.commit()
    yield SimpleNamespace(db=factory, service=CertificationJourneyService(), products=products, plans=plans, batches=batches)
    async with factory() as db, db.begin():
        await db.execute(delete(CertificationJourneyContent).where(CertificationJourneyContent.plan_id.in_([plan.id for plan in plans])))
        await db.execute(delete(H3cExamBatch).where(H3cExamBatch.plan_id.in_([plan.id for plan in plans])))
        await db.execute(delete(NispExamBatch).where(NispExamBatch.plan_id.in_([plan.id for plan in plans])))
        await db.execute(delete(Plan).where(Plan.id.in_([plan.id for plan in plans])))
        await db.execute(delete(CertProduct).where(CertProduct.id.in_([product.id for product in products])))
    await engine.dispose()


async def test_catalog_uses_domain_batch_ids_filters_products_and_keeps_real_prices(context):
    env = context
    for index, vendor in enumerate(("h3c", "nisp", "renshe")):
        catalog = await env.service.catalog(vendor)
        batch = next(item for item in catalog["batches"] if item["plan_id"] == env.plans[index].id)
        assert batch["id"] == (env.batches[index].id if index != 2 else env.plans[index].id)
        assert batch["can_apply"]
        assert batch["prices"][0]["price_cents"] == (1000 if index != 2 else 5000)
        assert not {"contact_phone", "candidate_snapshot", "renshe_config"} & batch.keys()
    with pytest.raises(BusinessException, match='报名方式'):
        await env.service.notice('nisp', env.batches[1].id, '2')
    async with env.db() as db, db.begin():
        product = await db.get(CertProduct, env.products[0].id); product.is_active = False
    assert not any(item["plan_id"] == env.plans[0].id for item in (await env.service.catalog("h3c"))["batches"])


async def test_notice_confirmation_binds_batch_type_price_and_preserves_snapshot(context):
    env = context
    batch_id = env.batches[0].id
    first = await env.service.notice("h3c", batch_id, "student")
    assert first["price"]["price_cents"] == 2000
    assert not {"contact_phone", "contact_wechat"} & first["notice"].keys()
    async with env.db() as db:
        snapshot = await confirmed_notice(db, vendor="h3c", batch_id=batch_id, registration_type="student", notice_hash=first["notice_hash"])
        assert snapshot["confirmed_at"] and snapshot["batch"]["plan_id"] == env.plans[0].id
        with pytest.raises(ConflictException):
            await confirmed_notice(db, vendor="h3c", batch_id=batch_id, registration_type="full", notice_hash=first["notice_hash"])
    async with env.db() as db, db.begin():
        batch = await db.get(H3cExamBatch, batch_id); batch.student_price_cents = 2500
    async with env.db() as db:
        with pytest.raises(ConflictException):
            await confirmed_notice(db, vendor="h3c", batch_id=batch_id, registration_type="student", notice_hash=first["notice_hash"])
    assert snapshot["price"]["price_cents"] == 2000


async def test_batch_supplement_conflicts_and_closed_batches_are_not_confirmable(context):
    env = context
    plan_id = env.plans[0].id
    original = await env.service.notice("h3c", env.batches[0].id, "full")
    update = JourneyContentUpdate(version=0, title="批次补充说明", blocks=[{"type": "text", "text": "请携带本人证件"}], contact_phone="13800138000")
    result = await env.service.update_content("h3c", update, 101, plan_id)
    assert result["version"] == 1
    with pytest.raises(ConflictException):
        await env.service.update_content("h3c", update, 101, plan_id)
    with pytest.raises(NotFoundException):
        await env.service.get_content("nisp", plan_id)
    latest = await env.service.notice("h3c", env.batches[0].id, "full")
    assert latest["notice_hash"] != original["notice_hash"]
    assert latest["supplement"]["blocks"][0]["text"] == "请携带本人证件"
    assert "contact_phone" not in latest["supplement"]
    async with env.db() as db, db.begin():
        plan = await db.get(Plan, plan_id); plan.apply_end = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(BusinessException):
        await env.service.notice("h3c", env.batches[0].id, "full")


async def test_notice_images_validate_actual_content_without_fetching_external_urls(context, monkeypatch, tmp_path):
    from PIL import Image
    monkeypatch.setattr('app.services.upload.UPLOAD_DIR', str(tmp_path))
    (tmp_path / 'wrong.png').write_bytes(b'%PDF-1.4 not a picture')
    values = dict(title='图片须知', blocks=[{'type': 'image', 'url': '/api/media/wrong.png'}])
    with pytest.raises(BusinessException, match='损坏'):
        await context.service.update_content('h3c', JourneyContentUpdate(version=0, **values), 101, context.plans[0].id)
    assert (await context.service.get_content('h3c', context.plans[0].id))['version'] == 0
    Image.new('RGB', (10, 10), 'white').save(tmp_path / 'valid.png')
    values['blocks'][0]['url'] = '/api/media/valid.png'
    result = await context.service.update_content('h3c', JourneyContentUpdate(version=0, **values), 101, context.plans[0].id)
    assert result['version'] == 1
    values['blocks'][0]['url'] = 'https://example.com/test.png'
    with pytest.raises(BusinessException, match='上传入口'):
        await context.service.update_content('h3c', JourneyContentUpdate(version=1, **values), 101, context.plans[0].id)
