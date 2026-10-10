"""NISP transactions and concurrency against an isolated PostgreSQL schema."""

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

import app.models  # Register all foreign-key targets before create_all.
from app.adapter.database import Base
from app.domain.nisp.src import NispExamBatch, NispRegistration, NispMaterialFile, NispRefundRequest
from app.domain.order.src.index import Order
from app.domain.plan.src.index import Plan
from app.domain.user.src.index import User, UserRealname, AdminUser
from app.models.cert_product import CertProduct
from app.port.exceptions import ConflictException
from app.schemas.nisp import NispOrderCreate, NispReviewDecisionRequest, NispResubmitRequest
from app.services.nisp_admin import NispAdminService
from app.services.nisp_registration import NispRegistrationService
from app.services.nisp_refund import NispRefundService
from app.services.nisp_lifecycle import occupied_count

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def context(monkeypatch):
    url = os.environ['TEST_DATABASE_URL']
    schema = 'nisp_test_' + uuid4().hex
    owner = create_async_engine(url)
    async with owner.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={'server_settings': {'search_path': schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("""
                CREATE FUNCTION quiz_library_code() RETURNS text AS $$
                BEGIN
                    RETURN 'QL-' || upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 12));
                END;
                $$ LANGUAGE plpgsql;
            """))
            await conn.run_sync(Base.metadata.create_all)

        @asynccontextmanager
        async def db_ctx():
            async with factory() as db:
                yield db

        for module in ['nisp_registration', 'nisp_admin', 'nisp_refund']:
            monkeypatch.setattr(f'app.services.{module}.get_db_ctx', db_ctx)
        monkeypatch.setattr('app.services.order.get_db_ctx', db_ctx)
        monkeypatch.setattr('app.services.agreement_template.ensure_accepted', AsyncMock())
        yield NS(factory=factory)
    finally:
        await engine.dispose()
        async with owner.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await owner.dispose()


async def seed(context, *, capacity=10, price=0):
    async with context.factory() as db:
        now = datetime.now(timezone.utc)
        product = await db.scalar(
            select(CertProduct).where(CertProduct.code == 'NISP-1')
        )
        if product is None:
            product = CertProduct(
                type='nisp',
                code='NISP-1',
                name='NISP',
                chinese_name='NISP 一级',
                is_active=True,
            )
            db.add(product)
        plan = Plan(product_type='NISP-1', name=uuid4().hex,
                    status='published', capacity=capacity,
                    apply_start=now-timedelta(days=1), apply_end=now+timedelta(days=1))
        admin = AdminUser(username=uuid4().hex, password_hash='test-only', role='cert_admin')
        db.add_all([product, plan, admin])
        db.add(admin)
        await db.flush()
        batch = NispExamBatch(plan_id=plan.id, level='1',
                              level1_price_cents=price, level2_price_cents=price)
        db.add(batch)
        await db.commit()
        return batch, admin.id


async def applicant(context, batch):
    async with context.factory() as db:
        user = User(openid=uuid4().hex)
        db.add(user)
        await db.flush()
        id_card = f'110101199001{user.id:06d}'
        db.add(UserRealname(
            user_id=user.id,
            user_type='student',
            real_name='测试',
            id_card_number=id_card,
            status='verified',
        ))
        keys = {}
        for kind in ['id_card_both_sides', 'portrait_photo']:
            key = f'nisp/materials/{user.id}/{uuid4().hex}.jpg'
            db.add(NispMaterialFile(user_id=user.id, material_type=kind, storage_key=key))
            keys[kind] = key
        await db.commit()
        return user.id, NispOrderCreate(
            batch_id=batch.id, level='1', name='测试', pinyin='CE SHI', major='计算机',
            school='测试学校', id_card=id_card, phone='13800000000',
            email='test@example.test', province='四川',
            id_card_both_sides_key=keys['id_card_both_sides'], portrait_photo_key=keys['portrait_photo'])


async def test_nisp_rejects_proxy_registration_and_freezes_verified_identity(context):
    from app.domain.user.src.index import User
    from app.port.exceptions import BusinessException, ConflictException

    batch, _ = await seed(context)
    user_id, payload = await applicant(context, batch)
    proxy_payload = payload.model_copy(update={'name': '代报名'})
    with pytest.raises(ConflictException, match='本人不一致'):
        await NispRegistrationService().create_order(user_id, proxy_payload)

    async with context.factory() as db:
        unverified = User(openid=uuid4().hex)
        db.add(unverified)
        await db.commit()
        unverified_id = unverified.id
    with pytest.raises(BusinessException, match='请先完成实名认证'):
        await NispRegistrationService().create_order(unverified_id, payload)

    created = await NispRegistrationService().create_order(user_id, payload)
    identity_basis = created.candidate_snapshot['identity_verification']
    assert created.candidate_snapshot['name'] == '测试'
    assert created.candidate_snapshot['id_card'] == payload.id_card
    assert identity_basis['source_table'] == 'user_realname'
    assert identity_basis['status'] == 'verified'
    assert identity_basis['real_name'] == '测试'
    assert identity_basis['id_card_number'] == payload.id_card


async def test_nisp_rejects_registration_when_product_is_inactive(context):
    from app.port.exceptions import BusinessException

    batch, _ = await seed(context)
    user_id, payload = await applicant(context, batch)
    async with context.factory() as db:
        product = await db.scalar(
            select(CertProduct).where(CertProduct.code == 'NISP-1')
        )
        assert product is not None
        product.is_active = False
        await db.commit()
    try:
        with pytest.raises(BusinessException, match='已下架'):
            await NispRegistrationService().create_order(user_id, payload)
    finally:
        async with context.factory() as db:
            product = await db.scalar(
                select(CertProduct).where(CertProduct.code == 'NISP-1')
            )
            product.is_active = True
            await db.commit()


async def test_nisp_admin_rejects_wrong_level_product_binding(context):
    from app.port.exceptions import BusinessException
    from app.schemas.nisp import NispExamBatchCreate

    async with context.factory() as db:
        plan = Plan(
            product_type='NISP-2',
            name=uuid4().hex,
            capacity=10,
            status='draft',
        )
        db.add(plan)
        await db.commit()
        plan_id = plan.id

    with pytest.raises(BusinessException, match='对应级别'):
        await NispAdminService().create_batch(NispExamBatchCreate(
            plan_id=plan_id,
            level='1',
            level1_price_cents=100,
            level2_price_cents=200,
        ))


async def test_generic_order_creation_rejects_nisp(context):
    from app.port.exceptions import DedicatedCertificationOrderRequiredException
    from app.schemas.order import OrderCreate
    from app.services.order import OrderService

    batch, _ = await seed(context)
    user_id, _ = await applicant(context, batch)
    payload = OrderCreate(
        order_kind='certification',
        product_type='NISP-1',
        candidate_name='测试',
        candidate_phone='13800000000',
    )
    with pytest.raises(
        DedicatedCertificationOrderRequiredException, match='NISP'
    ) as exc_info:
        await OrderService().create_order(user_id, payload)
    assert exc_info.value.code == 40210
    assert exc_info.value.detail == {
        'reason': 'dedicated_certification_order_required',
        'vendor': 'NISP',
    }


async def test_last_seat_is_not_oversold(context):
    batch, _ = await seed(context, capacity=1)
    applicants = [await applicant(context, batch) for _ in range(2)]
    results = await asyncio.gather(*[
        NispRegistrationService().create_order(user_id, payload)
        for user_id, payload in applicants
    ], return_exceptions=True)
    assert sum(isinstance(value, ConflictException) for value in results) == 1
    assert sum(not isinstance(value, Exception) for value in results) == 1
    async with context.factory() as db:
        assert await occupied_count(db, batch.plan_id) == 1


async def test_parallel_batches_have_distinct_registration_numbers(context):
    batches = [await seed(context) for _ in range(2)]
    applicants = [await applicant(context, batch) for batch, _ in batches]
    rows = await asyncio.gather(*[
        NispRegistrationService().create_order(user_id, payload) for user_id, payload in applicants
    ])
    assert len({row.registration_no for row in rows}) == 2


async def test_legacy_order_without_plan_still_occupies_last_seat(context):
    batch, _ = await seed(context, capacity=1)
    user_id, payload = await applicant(context, batch)
    first = await NispRegistrationService().create_order(user_id, payload)
    async with context.factory() as db:
        order = await db.get(Order, first.order_id)
        order.plan_id = None
        await db.commit()
        assert await occupied_count(db, batch.plan_id) == 1
    with pytest.raises(ConflictException):
        await NispRegistrationService().create_order(*await applicant(context, batch))


async def test_resubmission_persists_json_and_material_history(context):
    batch, admin_id = await seed(context)
    user_id, payload = await applicant(context, batch)
    service = NispRegistrationService()
    reg = await service.create_order(user_id, payload)
    await service.review(admin_id=admin_id, registration_id=reg.id, data=NispReviewDecisionRequest(
        decision='rejected', reason_detail='身份证不清晰', rejected_material_types=['id_card_both_sides']))
    new_key = f'nisp/materials/{user_id}/new.jpg'
    async with context.factory() as db:
        db.add(NispMaterialFile(user_id=user_id, material_type='id_card_both_sides', storage_key=new_key))
        await db.commit()
    await service.resubmit_materials(user_id, reg.id, NispResubmitRequest(id_card_both_sides_key=new_key))
    async with context.factory() as db:
        stored = await db.get(NispRegistration, reg.id)
        materials = (await db.scalars(select(NispMaterialFile).where(
            NispMaterialFile.registration_id == reg.id,
            NispMaterialFile.material_type == 'id_card_both_sides').order_by(NispMaterialFile.version_no))).all()
        assert stored.material_keys['id_card_both_sides'] == new_key
        assert stored.material_keys['portrait_photo'] == payload.portrait_photo_key
        assert [(m.version_no, m.is_current) for m in materials] == [(1, False), (2, True)]


async def test_timeout_and_batch_cancel_do_not_duplicate_refund(context):
    batch, admin_id = await seed(context)
    user_id, payload = await applicant(context, batch)
    service = NispRegistrationService()
    reg = await service.create_order(user_id, payload)
    await service.review(admin_id=admin_id, registration_id=reg.id, data=NispReviewDecisionRequest(
        decision='rejected', reason_detail='照片不清晰', rejected_material_types=['portrait_photo']))
    async with context.factory() as db:
        stored = await db.get(NispRegistration, reg.id)
        stored.resubmission_due_at = datetime.now(timezone.utc)-timedelta(seconds=1)
        await db.commit()
    assert await service.process_resubmission_timeouts() == 1
    assert await service.process_resubmission_timeouts() == 0
    await NispAdminService().cancel_batch(batch.id)
    await NispAdminService().cancel_batch(batch.id)
    async with context.factory() as db:
        refunds = (await db.scalars(select(NispRefundRequest))).all()
        assert len(refunds) == 1 and refunds[0].status == 'requested'
        refund_id = refunds[0].id
    refund = await NispRefundService().confirm(admin_id=admin_id, refund_id=refund_id)
    assert refund.status == 'succeeded'
    assert (await NispRefundService().confirm(admin_id=admin_id, refund_id=refund_id)).status == 'succeeded'
    async with context.factory() as db:
        assert (await db.get(Order, reg.order_id)).status == 'refunded'
        assert (await db.get(NispRegistration, reg.id)).status == 'refunded_closed'


async def test_batch_cancellation_closes_unpaid_order(context):
    batch, _ = await seed(context, price=100)
    reg = await NispRegistrationService().create_order(*await applicant(context, batch))
    await NispAdminService().cancel_batch(batch.id)
    async with context.factory() as db:
        assert (await db.get(Order, reg.order_id)).status == 'closed'
        assert (await db.get(NispRegistration, reg.id)).status == 'cancelled'
        assert await occupied_count(db, batch.plan_id) == 0
