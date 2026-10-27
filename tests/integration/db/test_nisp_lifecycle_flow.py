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
from app.models.offline_payment import OfflinePaymentSettings
from app.port.exceptions import BusinessException, ConflictException
from app.schemas.nisp import (
    NispExamBatchFullCreate,
    NispExamBatchUpdate,
    NispOrderCreate,
    NispReviewDecisionRequest,
    NispResubmitRequest,
)
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

        for module in ['nisp_registration', 'nisp_admin', 'nisp_refund',
                       'nisp_export', 'certification_final_review']:
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
        if price:
            # Paid NISP orders now use the private bank-transfer workflow.
            # Keep this schema-local fixture self-contained instead of depending
            # on another integration module having configured the account.
            if await db.get(OfflinePaymentSettings, 1) is None:
                db.add(OfflinePaymentSettings(id=1, version=1, data={
                    'account_name': '测试收款账户',
                    'bank_name': '测试银行',
                    'account_number': '6222000000000000',
                    'contact_name': '测试管理员',
                    'contact_phone': '13800000000',
                }))
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


async def test_review_batch_options_include_history_and_search_every_page(context, monkeypatch):
    statuses = ['draft', 'published', 'registration_closed', 'cancelled', 'finalized', 'archived']
    async with context.factory() as db:
        for index in range(125):
            level = '2' if index == 1 else '1'
            plan = Plan(
                id=1000 + index,
                product_type=f'NISP-{level}',
                name='同名历史批次' if index < 2 else f'历史批次 {index}',
                status=statuses[index % len(statuses)],
            )
            db.add(plan)
            await db.flush()
            db.add(NispExamBatch(id=18 + index, plan_id=plan.id, level=level))
        # A non-NISP plan without a batch never becomes a review option.
        db.add(Plan(product_type='other', name='不应出现', status='published'))
        await db.commit()

    full_response = AsyncMock(side_effect=AssertionError('options must use a lightweight projection'))
    monkeypatch.setattr(NispAdminService, '_response', full_response)
    service = NispAdminService()
    first, total = await service.list_review_batch_options()
    assert total == 125
    assert [item.id for item in first] == list(range(142, 122, -1))
    assert set(first[0].model_dump()) == {'id', 'name', 'level', 'status'}
    assert {item.status for item in first} == set(statuses)

    first_page, first_total = await service.list_review_batch_options(page_size=100)
    second_page, second_total = await service.list_review_batch_options(page=2, page_size=100)
    assert first_total == second_total == 125
    assert [item.id for item in first_page + second_page] == list(range(142, 17, -1))
    empty_page, empty_total = await service.list_review_batch_options(page=3, page_size=100)
    assert empty_page == [] and empty_total == 125

    same_name, count = await service.list_review_batch_options(keyword='  同名历史  ')
    assert count == 2
    assert [(item.id, item.name, item.level) for item in same_name] == [
        (19, '同名历史批次', '2'), (18, '同名历史批次', '1'),
    ]
    for keyword in ['18', '#18', '  #18  ']:
        matches, count = await service.list_review_batch_options(keyword=keyword)
        # Numeric searches also retain ordinary name matching.
        expected = [136, 36, 18] if keyword == '18' else [18]
        assert [item.id for item in matches] == expected
        assert count == len(expected)
    for keyword in ['1000', '#1000', '不存在的批次', '2147483648', '9' * 64]:
        matches, count = await service.list_review_batch_options(keyword=keyword)
        assert matches == [] and count == 0
    blank, count = await service.list_review_batch_options(keyword='   ')
    assert count == total and blank == first
    full_response.assert_not_awaited()


async def test_review_batch_name_and_existing_registration_filters_intersect(context):
    from app.domain.nisp.src import NispExportItem, NispExportJob
    from app.schemas.nisp import NispRegistrationResponse

    batch, admin_id = await seed(context)
    other_batch, _ = await seed(context)
    now = datetime.now(timezone.utc)
    examples = [
        ('target', batch, 'approved', '张三', 'confirmed', True),
        ('other_batch', other_batch, 'approved', '张三', 'confirmed', True),
        ('other_status', batch, 'pending_review', '张三', 'confirmed', True),
        ('other_name', batch, 'approved', '李四', 'confirmed', True),
        ('other_payment', batch, 'approved', '张三', 'under_review', True),
        ('no_export', batch, 'approved', '张三', 'confirmed', False),
    ]
    ids = {}
    async with context.factory() as db:
        plan = await db.get(Plan, batch.plan_id)
        plan.name = '审核所属批次'
        plan.status = 'archived'
        for key, selected_batch, status, name, payment_state, exported in examples:
            user = User(openid=uuid4().hex)
            db.add(user)
            await db.flush()
            order = Order(
                user_id=user.id, plan_id=selected_batch.plan_id,
                product_type='NISP-1', price=0,
                payment_channel='free' if payment_state == 'confirmed' else 'bank_transfer',
                status='paid' if payment_state == 'confirmed' else 'pending',
                offline_status=None if payment_state == 'confirmed' else payment_state,
                paid_at=now if payment_state == 'confirmed' else None,
            )
            db.add(order)
            await db.flush()
            registration = NispRegistration(
                batch_id=selected_batch.id, plan_id=selected_batch.plan_id,
                user_id=user.id, order_id=order.id, registration_no=uuid4().hex,
                level='1', status=status, candidate_idcard=f'{user.id:018d}',
                candidate_snapshot={'name': name},
            )
            db.add(registration)
            await db.flush()
            ids[key] = registration.id
            if key == 'target':
                target_user_id = user.id
            if exported:
                job = NispExportJob(
                    batch_id=selected_batch.id, level='1', requested_by_admin_id=admin_id,
                    include_statuses=['approved'], status='succeeded', registration_count=1,
                )
                db.add(job)
                await db.flush()
                db.add(NispExportItem(job_id=job.id, registration_id=registration.id))
        await db.commit()

    filters = dict(batch_id=batch.id, level='1', status='approved', keyword='张',
                   payment_state='confirmed', has_valid_export=True)
    service = NispAdminService()
    rows, total = await service.list_registrations(**filters)
    assert total == 1 and [row.id for row in rows] == [ids['target']]
    assert rows[0].batch_name == '审核所属批次'
    assert rows[0].batch_id == batch.id
    assert rows[0].plan_id == batch.plan_id

    for key, changed_filter in [
        ('other_batch', {'batch_id': other_batch.id}),
        ('other_status', {'status': 'pending_review'}),
        ('other_name', {'keyword': '李'}),
        ('other_payment', {'payment_state': 'under_review'}),
        ('no_export', {'has_valid_export': False}),
    ]:
        rows, total = await service.list_registrations(**(filters | changed_filter))
        assert total == 1 and [row.id for row in rows] == [ids[key]]
    rows, total = await service.list_registrations(**(filters | {'level': '2'}))
    assert total == 0 and rows == []
    rows, total = await service.list_registrations(batch_id=batch.id, page=2, page_size=2)
    assert total == 5 and len(rows) == 2
    assert all(row.batch_id == batch.id and row.batch_name == '审核所属批次' for row in rows)

    # The shared response remains usable by the user list/detail and older DTO inputs.
    user_service = NispRegistrationService()
    detail = await user_service.get_registration(target_user_id, ids['target'])
    listing = await user_service.list_user_registrations(target_user_id)
    assert detail.batch_name == listing['items'][0].batch_name == '审核所属批次'
    old_payload = detail.model_dump(exclude={'batch_name'})
    assert NispRegistrationResponse.model_validate(old_payload).batch_name is None
    async with context.factory() as db:
        assert (await db.get(Plan, batch.plan_id)).status == 'archived'
        for key, _, status, _, _, _ in examples:
            assert (await db.get(NispRegistration, ids[key])).status == status


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


async def test_nisp_full_batch_create_publish_and_edit_flow(context):
    now = datetime.now(timezone.utc)
    async with context.factory() as db:
        db.add(CertProduct(
            type='nisp',
            code='NISP-2',
            name='NISP',
            chinese_name='NISP 二级',
            is_active=True,
        ))
        await db.commit()

    draft = await NispAdminService().create_full_batch(NispExamBatchFullCreate(
        level='2',
        name='NISP二级完整创建批次',
        apply_start=now + timedelta(days=1),
        apply_end=now + timedelta(days=10),
        capacity=30,
        price_cents=48000,
        training_org='测试培训机构',
    ))
    assert draft.plan_status == 'draft'
    assert draft.level == '2'
    assert draft.level2_price_cents == 48000
    assert draft.level1_price_cents == 0

    updated = await NispAdminService().update_batch(draft.id, NispExamBatchUpdate(
        name='NISP二级完整创建批次（更新）',
        exam_date=now + timedelta(days=20),
        exam_location='成都市测试考点',
        capacity=35,
        price_cents=50000,
    ))
    assert updated.plan_name == 'NISP二级完整创建批次（更新）'
    assert updated.exam_location == '成都市测试考点'
    assert updated.capacity == 35
    assert updated.level2_price_cents == 50000

    with pytest.raises(BusinessException, match='考试时间'):
        await NispAdminService().publish_batch(
            (await NispAdminService().create_full_batch(NispExamBatchFullCreate(
                level='2',
                name='NISP二级缺少考试时间批次',
                apply_start=now + timedelta(days=1),
                apply_end=now + timedelta(days=10),
                capacity=20,
                price_cents=38000,
            ))).id
        )

    published = await NispAdminService().publish_batch(updated.id)
    assert published.plan_status == 'published'

    items, total = await NispAdminService().list_batches(
        keyword='完整创建批次（更新）',
        level='2',
        status='published',
    )
    assert total == 1
    assert [item.id for item in items] == [published.id]

    with pytest.raises(ConflictException, match='仅草稿批次'):
        await NispAdminService().update_batch(
            published.id, NispExamBatchUpdate(capacity=40)
        )


async def test_legacy_nisp_plan_still_lists_and_supports_order(context, monkeypatch):
    """Legacy plan.product_type='nisp' resolves through NispExamBatch.level."""
    from app.api.nisp import list_batches

    @asynccontextmanager
    async def api_db_ctx():
        async with context.factory() as db:
            yield db

    # The endpoint imports get_db_ctx lazily from the database adapter.
    monkeypatch.setattr('app.adapter.database.get_db_ctx', api_db_ctx)

    async with context.factory() as db:
        now = datetime.now(timezone.utc)
        product = CertProduct(
            type='nisp',
            code='NISP-2',
            name='NISP',
            chinese_name='NISP 二级',
            is_active=True,
        )
        plan = Plan(
            product_type='nisp',
            name=uuid4().hex,
            status='published',
            capacity=10,
            apply_start=now - timedelta(days=1),
            apply_end=now + timedelta(days=1),
        )
        db.add_all([product, plan])
        await db.flush()
        batch = NispExamBatch(
            plan_id=plan.id,
            level='2',
            level1_price_cents=0,
            level2_price_cents=0,
        )
        db.add(batch)
        await db.commit()

    response = await list_batches(None)
    assert response.code == 0
    assert [item.id for item in response.data] == [batch.id]
    assert response.data[0].level == '2'

    user_id, payload = await applicant(context, batch)
    level2_materials = {}
    async with context.factory() as db:
        for material_type in ('xuexin_report', 'application_form'):
            storage_key = f'nisp/materials/{user_id}/{material_type}-{uuid4().hex}.pdf'
            db.add(
                NispMaterialFile(
                    user_id=user_id,
                    material_type=material_type,
                    storage_key=storage_key,
                )
            )
            level2_materials[material_type] = storage_key
        await db.commit()
    payload = payload.model_copy(update={
        'level': '2',
        'gender': '男',
        'age': 20,
        'education': '本科',
        'address': '四川省成都市',
        'zip_code': '610000',
        'xuexin_report_key': level2_materials['xuexin_report'],
        'application_form_key': level2_materials['application_form'],
    })
    registration = await NispRegistrationService().create_order(user_id, payload)
    assert registration.level == '2'
    async with context.factory() as db:
        order = await db.get(Order, registration.order_id)
    assert order.product_type == 'NISP-2'


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


async def test_nisp_correction_versions_and_return_after_approval(context):
    from app.domain.nisp.src import NispCorrectionRequest, NispRegistrationVersion

    batch, admin_id = await seed(context)
    user_id, payload = await applicant(context, batch)
    service = NispRegistrationService()
    reg = await service.create_order(user_id, payload)
    approved = await service.review(
        admin_id=admin_id,
        registration_id=reg.id,
        data=NispReviewDecisionRequest(decision='approved'),
    )
    assert approved.status == 'approved'
    returned = await service.review(
        admin_id=admin_id,
        registration_id=reg.id,
        data=NispReviewDecisionRequest(
            decision='rejected',
            reason_detail='联系信息需要补正',
            allowed_fields=['phone', 'email'],
        ),
    )
    assert returned.status == 'rejected_awaiting_resubmission'
    assert set(returned.pending_correction.allowed_fields) == {'phone', 'email'}
    corrected = await service.resubmit_materials(
        user_id,
        reg.id,
        NispResubmitRequest(phone='13900000000', email='corrected@example.test'),
    )
    assert corrected.status == 'pending_review'
    assert corrected.candidate_snapshot['phone'] == '13900000000'
    assert corrected.candidate_snapshot['email'] == 'corrected@example.test'
    async with context.factory() as db:
        versions = (await db.scalars(select(NispRegistrationVersion).where(
            NispRegistrationVersion.registration_id == reg.id
        ).order_by(NispRegistrationVersion.version_no))).all()
        correction = await db.scalar(select(NispCorrectionRequest).where(
            NispCorrectionRequest.registration_id == reg.id
        ))
    assert [row.version_no for row in versions] == [1, 2]
    assert [row.is_current for row in versions] == [False, True]
    assert versions[0].candidate_snapshot['email'] == 'test@example.test'
    assert correction.status == 'submitted'


async def test_nisp_direct_reject_refund_authorizes_worker(context):
    from app.schemas.nisp import NispRejectRefundRequest

    batch, admin_id = await seed(context)
    user_id, payload = await applicant(context, batch)
    service = NispRegistrationService()
    reg = await service.create_order(user_id, payload)
    rejected = await service.reject_and_refund(
        admin_id=admin_id,
        registration_id=reg.id,
        data=NispRejectRefundRequest(
            reason_code='review_rejected',
            reason_detail='资料不符合报名要求',
        ),
    )
    assert rejected.status == 'refund_processing'
    async with context.factory() as db:
        refund = await db.scalar(select(NispRefundRequest).where(
            NispRefundRequest.registration_id == reg.id
        ))
    assert refund.status == 'approved'
    assert refund.approved_by_admin_id == admin_id
    assert refund.out_refund_no == f'NISP-RF-{refund.id:08d}'


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


async def test_nisp_final_review_binds_effective_export_version(context):
    from app.domain.nisp.src import (
        NispExportItem,
        NispExportJob,
        NispFinalReview,
        NispRegistrationVersion,
    )
    from app.schemas.nisp import NispFinalReviewRequest
    from app.services.certification_final_review import NispFinalReviewService

    batch, admin_id = await seed(context)
    user_id, payload = await applicant(context, batch)
    service = NispRegistrationService()
    async with context.factory() as db:
        stored_batch = await db.get(NispExamBatch, batch.id)
        assert stored_batch is not None
        stored_batch.consultation_group_qrcode_url = "/api/media/nisp-consultation-group.png"
        await db.commit()
    reg = await service.create_order(user_id, payload)
    await service.review(
        admin_id=admin_id,
        registration_id=reg.id,
        data=NispReviewDecisionRequest(decision='approved'),
    )
    async with context.factory() as db:
        version = await db.scalar(select(NispRegistrationVersion).where(
            NispRegistrationVersion.registration_id == reg.id,
            NispRegistrationVersion.is_current.is_(True),
        ))
        job = NispExportJob(
            batch_id=batch.id,
            level='1',
            requested_by_admin_id=admin_id,
            include_statuses=['approved'],
            status='succeeded',
            artifact_type='full_package',
            registration_count=1,
            storage_key='test-export.zip',
        )
        db.add(job)
        await db.flush()
        item = NispExportItem(
            job_id=job.id,
            registration_id=reg.id,
            registration_version_id=version.id,
            candidate_snapshot=version.candidate_snapshot,
            material_versions=version.material_versions,
            is_valid=True,
        )
        db.add(item)
        await db.commit()
        item_id = item.id

    finalized = await NispFinalReviewService().review(
        admin_id=admin_id,
        registration_id=reg.id,
        data=NispFinalReviewRequest(export_item_id=item_id),
    )
    assert finalized.status == 'final_approved'
    assert finalized.consultation_group_qrcode_url == "/api/media/nisp-consultation-group.png"
    assert finalized.candidate_snapshot["phone"] == payload.phone
    async with context.factory() as db:
        stored_review = await db.scalar(select(NispFinalReview).where(
            NispFinalReview.registration_id == reg.id
        ))
        stored_item = await db.get(NispExportItem, item_id)
    assert stored_review.export_item_id == item_id
    assert stored_item.is_valid is True


async def test_nisp_batch_export_precheck_selection_and_cross_job_final_review(context):
    from app.domain.nisp.src import NispExportItem, NispExportJob
    from app.port.exceptions import BusinessException, ConflictException
    from app.schemas.nisp import (
        NispBatchFinalReviewRequest,
        NispBatchFinalReviewTarget,
        NispExportPrecheckRequest,
    )
    from app.services.certification_final_review import NispFinalReviewService
    from app.services.nisp_export import NispExportService

    batch, admin_id = await seed(context)
    applicants = [await applicant(context, batch) for _ in range(2)]
    registration_service = NispRegistrationService()
    registrations = []
    for user_id, payload in applicants:
        row = await registration_service.create_order(user_id, payload)
        registrations.append(await registration_service.review(
            admin_id=admin_id,
            registration_id=row.id,
            data=NispReviewDecisionRequest(decision='approved'),
        ))

    export_service = NispExportService()
    precheck = await export_service.precheck(NispExportPrecheckRequest(
        batch_id=batch.id,
        registration_ids=[row.id for row in registrations],
    ))
    assert precheck.selected_count == 2
    assert precheck.exportable_count == 2
    assert precheck.blocked_count == 0
    assert precheck.level == batch.level
    assert precheck.exportable_registration_ids == [row.id for row in registrations]

    with pytest.raises(BusinessException, match='级别与批次级别不一致'):
        await export_service.create_job(
            admin_id=admin_id,
            batch_id=batch.id,
            level='2' if batch.level == '1' else '1',
        )

    first_job = await export_service.create_job(
        admin_id=admin_id,
        batch_id=batch.id,
        level=None,
        registration_ids=[registrations[0].id],
    )
    second_job = await export_service.create_job(
        admin_id=admin_id,
        batch_id=batch.id,
        level=None,
        registration_ids=[registrations[1].id],
    )
    assert first_job.level == batch.level
    assert first_job.registration_count == 1
    assert second_job.registration_count == 1

    jobs, total = await export_service.list_jobs(batch_id=batch.id)
    assert total == 2
    assert {job.id for job in jobs} == {first_job.id, second_job.id}
    assert all(job.registration_ids for job in jobs)

    async with context.factory() as db:
        for job_id in [first_job.id, second_job.id]:
            stored_job = await db.get(NispExportJob, job_id)
            stored_job.status = 'succeeded'
            stored_job.storage_key = f'test-export-{job_id}.zip'
        items = (await db.scalars(select(NispExportItem).where(
            NispExportItem.job_id.in_([first_job.id, second_job.id])
        ).order_by(NispExportItem.job_id))).all()
        await db.commit()
    assert len(items) == 2

    finalized = await NispFinalReviewService().review_batch(
        admin_id=admin_id,
        data=NispBatchFinalReviewRequest(targets=[
            NispBatchFinalReviewTarget(
                registration_id=registrations[0].id,
                export_item_id=items[0].id,
            ),
            NispBatchFinalReviewTarget(
                registration_id=registrations[1].id,
                export_item_id=items[1].id,
            ),
        ]),
    )
    assert [row.status for row in finalized.items] == ['success', 'success']
    assert all(row.success for row in finalized.items)

    listed = await NispAdminService().list_registrations(
        batch_id=batch.id,
        status='final_approved',
    )
    assert listed[1] == 2


async def test_nisp_export_precheck_reports_missing_level_materials(context):
    from app.port.exceptions import BusinessException
    from app.schemas.nisp import NispExportPrecheckRequest
    from app.services.nisp_export import NispExportService

    batch, admin_id = await seed(context)
    user_id, payload = await applicant(context, batch)
    service = NispRegistrationService()
    created = await service.create_order(user_id, payload)
    approved = await service.review(
        admin_id=admin_id,
        registration_id=created.id,
        data=NispReviewDecisionRequest(decision='approved'),
    )
    async with context.factory() as db:
        material = await db.scalar(select(NispMaterialFile).where(
            NispMaterialFile.registration_id == approved.id,
            NispMaterialFile.material_type == 'portrait_photo',
            NispMaterialFile.is_current.is_(True),
        ))
        material.is_current = False
        await db.commit()

    export_service = NispExportService()
    precheck = await export_service.precheck(NispExportPrecheckRequest(
        batch_id=batch.id,
        registration_ids=[approved.id],
    ))
    assert precheck.selected_count == 1
    assert precheck.exportable_count == 0
    assert precheck.blocked_count == 1
    assert precheck.people[0].reason == '缺少：寸照'
    with pytest.raises(BusinessException, match='暂不可导出'):
        await export_service.create_job(
            admin_id=admin_id,
            batch_id=batch.id,
            registration_ids=[approved.id],
        )


async def test_nisp_expired_export_file_remains_reviewable(context):
    from app.domain.nisp.src import NispExportItem, NispExportJob, NispRegistrationVersion
    from app.schemas.nisp import NispBatchFinalReviewRequest, NispBatchFinalReviewTarget
    from app.services.certification_final_review import NispFinalReviewService
    from app.services.nisp_export import NispExportService

    batch, admin_id = await seed(context)
    user_id, payload = await applicant(context, batch)
    service = NispRegistrationService()
    created = await service.create_order(user_id, payload)
    approved = await service.review(
        admin_id=admin_id,
        registration_id=created.id,
        data=NispReviewDecisionRequest(decision='approved'),
    )
    async with context.factory() as db:
        version = await db.scalar(select(NispRegistrationVersion).where(
            NispRegistrationVersion.registration_id == approved.id,
            NispRegistrationVersion.is_current.is_(True),
        ))
        job = NispExportJob(
            batch_id=batch.id,
            level=batch.level,
            requested_by_admin_id=admin_id,
            include_statuses=['approved'],
            status='succeeded',
            artifact_type='full_package',
            registration_count=1,
            storage_key='expired-test-export.zip',
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        db.add(job)
        await db.flush()
        item = NispExportItem(
            job_id=job.id,
            registration_id=approved.id,
            registration_version_id=version.id,
            candidate_snapshot=version.candidate_snapshot,
            material_versions=version.material_versions,
            is_valid=True,
        )
        db.add(item)
        await db.commit()
        item_id = item.id

    # New expiration behavior only removes the artifact and leaves the succeeded
    # export version available for final review.
    assert await NispExportService(storage=NS(delete_object=AsyncMock())).expire_artifacts() == 1
    response = await NispExportService().get_job(job.id)
    assert response.status == 'succeeded'
    assert response.file_expired is True
    assert response.download_available is False

    finalized = await NispFinalReviewService().review_batch(
        admin_id=admin_id,
        data=NispBatchFinalReviewRequest(targets=[
            NispBatchFinalReviewTarget(registration_id=approved.id, export_item_id=item_id),
        ]),
    )
    assert finalized.items[0].success is True
