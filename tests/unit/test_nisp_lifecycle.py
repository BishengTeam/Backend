import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from app.domain.nisp.src import NispRegistration, NispRefundRequest
from app.domain.order.src.index import Order
from app.port.exceptions import BusinessException, ConflictException
from app.schemas.nisp import NispOrderCreate, NispResubmitRequest, NispReviewDecisionRequest
from app.services.nisp_registration import NispRegistrationService
from app.services.nisp_admin import NispAdminService
from app.services.nisp_refund import NispRefundService, _PreparedRefund
from app.services.nisp_lifecycle import request_refund
from app.integrations.wechat_pay import WechatPayAPIError, WechatPayResultUnknownError

NOW = datetime.now(timezone.utc)


def session(monkeypatch, module, *, scalars=(), execute=()):
    db = NS(scalar=AsyncMock(side_effect=list(scalars)), execute=AsyncMock(side_effect=list(execute)),
            scalars=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock(), flush=AsyncMock(), add=Mock())

    @asynccontextmanager
    async def ctx():
        yield db

    monkeypatch.setattr(module + '.get_db_ctx', ctx)
    return db


def result(value):
    return NS(scalar_one_or_none=lambda: value)


def payload():
    return NispOrderCreate(batch_id=1, level='1', name='张三', pinyin='ZHANG SAN',
                          major='计算机', school='测试学校', id_card='110101199001010011',
                          phone='13800000000', email='test@example.test', province='四川',
                          id_card_both_sides_key='id.jpg', portrait_photo_key='photo.jpg')


def batch(**kwargs):
    return NS(id=1, plan_id=2, level='1', level1_price_cents=100,
              level2_price_cents=200, payment_timeout_minutes=30,
              max_resubmissions=2, resubmission_window_hours=72, training_org=None, **kwargs)


def plan(**kwargs):
    fields = dict(id=2, product_type='NISP-1', status='published', capacity=10,
                  apply_start=NOW-timedelta(days=1),
                  apply_end=NOW+timedelta(days=1), exam_date=None, exam_location=None)
    return NS(**(fields | kwargs))


def identity(**kwargs):
    fields = dict(
        user_id=10,
        real_name='张三',
        id_card_number='110101199001010011',
        status='verified',
        verified_at='2026-01-01T00:00:00Z',
        id_card_hash='hash',
        last_name_zh=None,
        first_name_zh=None,
        last_name_en=None,
        first_name_en=None,
        gender='男',
        age=26,
        birth_date='1990-01-01',
        zip_code='110101',
    )
    return NS(**(fields | kwargs))


def cert_product(**kwargs):
    fields = dict(id=1, type='nisp', code='NISP-1', is_active=True)
    return NS(**(fields | kwargs))


@pytest.mark.asyncio
@pytest.mark.parametrize('changes,occupied', [
    ({'apply_start': NOW+timedelta(days=1)}, 0),
    ({'apply_end': NOW-timedelta(days=1)}, 0),
    ({'status': 'cancelled'}, 0),
    ({'capacity': 1}, 1),
])
async def test_invalid_batch_never_creates_order(monkeypatch, changes, occupied):
    db = session(monkeypatch, 'app.services.nisp_registration',
                 scalars=[identity(), 2, plan(**changes), cert_product(), occupied],
                 execute=[result(batch())])
    db.get = AsyncMock(return_value=NS(is_active=True))
    monkeypatch.setattr('app.services.agreement_template.ensure_accepted', AsyncMock())
    with pytest.raises((BusinessException, ConflictException)):
        await NispRegistrationService().create_order(10, payload())
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('price', [0, 100])
async def test_new_order_has_plan_and_database_id_registration_number(monkeypatch, price):
    b = batch()
    b.level1_price_cents = price
    db = session(monkeypatch, 'app.services.nisp_registration',
                 scalars=[identity(), 2, plan(), cert_product(), 0, None],
                 execute=[result(b), result(None)])
    db.get = AsyncMock(return_value=NS(is_active=True))
    monkeypatch.setattr('app.services.agreement_template.ensure_accepted', AsyncMock())
    service = NispRegistrationService()
    service._bind_material = AsyncMock()
    service._response = AsyncMock(return_value='ok')

    async def flush():
        for call in db.add.call_args_list:
            obj = call.args[0]
            if obj.id is None:
                obj.id = 321 if isinstance(obj, Order) else 765

    db.flush.side_effect = flush
    await service.create_order(10, payload())
    order, reg = [c.args[0] for c in db.add.call_args_list]
    assert order.plan_id == 2
    assert reg.registration_no == 'NISP-1-00000765'
    assert reg.status == ('pending_review' if price == 0 else 'pending_payment')
    assert order.status == ('completed' if price == 0 else 'pending')
    if price == 0:
        assert order.paid_at is not None and order.expires_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize('updates,count,expired', [({}, 0, False),
    ({'portrait_photo_key': 'new.jpg'}, 0, False),
    ({'id_card_both_sides_key': 'new.jpg'}, 2, False),
    ({'id_card_both_sides_key': 'new.jpg'}, 0, True)])
async def test_resubmission_rejects_empty_unrequested_exhausted_or_expired(monkeypatch, updates, count, expired):
    reg = NS(id=1, batch_id=1, status='rejected_awaiting_resubmission',
             user_id=10, order_id=2,
             resubmission_count=count, resubmission_due_at=NOW+timedelta(hours=-1 if expired else 1),
             material_keys={'id_card_both_sides': 'old-id', 'portrait_photo': 'old-photo'})
    review = NS(rejected_material_types=['id_card_both_sides'])
    order = NS(id=2)
    db = session(monkeypatch, 'app.services.nisp_registration',
                 execute=[result(reg)], scalars=[reg, batch(), order, review])
    with pytest.raises((BusinessException, ConflictException)):
        await NispRegistrationService().resubmit_materials(10, 1, NispResubmitRequest(**updates))
    assert reg.status == 'rejected_awaiting_resubmission'
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_resubmission_preserves_old_json_and_replaces_only_rejected_material(monkeypatch):
    old_keys = {'id_card_both_sides': 'old-id', 'portrait_photo': 'old-photo'}
    reg = NS(id=1, batch_id=1, status='rejected_awaiting_resubmission', resubmission_count=0,
             user_id=10, order_id=2,
             resubmission_due_at=NOW+timedelta(hours=1), material_keys=old_keys)
    review = NS(rejected_material_types=['id_card_both_sides'])
    order = NS(id=2)
    db = session(monkeypatch, 'app.services.nisp_registration', execute=[result(reg)],
                 scalars=[reg, batch(), order, review])
    service = NispRegistrationService()
    service._bind_material = AsyncMock()
    service._response = AsyncMock()
    await service.resubmit_materials(10, 1, NispResubmitRequest(id_card_both_sides_key='new-id'))
    assert old_keys['id_card_both_sides'] == 'old-id'
    assert reg.material_keys == {'id_card_both_sides': 'new-id', 'portrait_photo': 'old-photo'}
    assert reg.resubmission_count == 1 and reg.status == 'pending_review'
    assert reg.resubmission_due_at is None


@pytest.mark.asyncio
async def test_timeout_requests_refund_once(monkeypatch):
    reg = NS(id=1, order_id=2, user_id=3, status='rejected_awaiting_resubmission',
             resubmission_due_at=NOW - timedelta(minutes=1))
    order = NS(id=2, status='paid', price=100)
    db = session(monkeypatch, 'app.services.nisp_registration',
                 scalars=[order, reg, None], execute=[NS(all=lambda: [(2, 1)])])
    assert await NispRegistrationService().process_resubmission_timeouts() == 1
    refund = db.add.call_args.args[0]
    assert reg.status == 'pending_refund_confirmation'
    assert refund.reason_code == 'resubmission_timeout' and refund.amount_cents == 100
    db.scalar.side_effect = [refund]
    assert await request_refund(db, reg, order, reason_code='resubmission_timeout') is refund
    assert db.add.call_count == 1


@pytest.mark.asyncio
async def test_cancel_batch_closes_pending_and_requests_paid_refund(monkeypatch):
    pending = NS(id=10, status='pending', user_id=4, coupon_code=None)
    paid = NS(id=20, status='completed', user_id=5, price=100)
    first = NS(id=1, status='pending_payment')
    second = NS(id=2, status='approved', user_id=5)
    p = plan()
    db = session(monkeypatch, 'app.services.nisp_admin',
                 scalars=[2, p, batch(), pending, first, paid, second, None],
                 execute=[NS(all=lambda: [(1, 10), (2, 20)])])
    monkeypatch.setattr('app.services.nisp_admin.release_order_coupon', AsyncMock())
    service = NispAdminService()
    service._response = AsyncMock()
    await service.cancel_batch(1)
    assert p.status == 'cancelled' and pending.status == 'closed'
    assert first.status == 'cancelled' and second.status == 'pending_refund_confirmation'
    assert db.add.call_args.args[0].request_kind == 'batch_cancelled'


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['approved', 'processing', 'failed'])
async def test_reconcile_recovers_missing_remote_refund_with_same_number(monkeypatch, status):
    refund = NS(id=1, order_id=2, status=status, out_refund_no='NISP-RF-00000001',
                amount_cents=100, approved_by_admin_id=9)
    db = session(monkeypatch, 'app.services.nisp_refund', scalars=[refund])
    db.get = AsyncMock(return_value=NS(out_trade_no='order-2', price=100))
    provider = NS(query_refund=AsyncMock(side_effect=WechatPayAPIError('RESOURCE_NOT_EXISTS', 404)))
    service = NispRefundService(provider)
    service._submit = AsyncMock(return_value='submitted')
    assert await service.reconcile(1) == 'submitted'
    assert service._submit.call_args.args[0].out_refund_no == 'NISP-RF-00000001'


@pytest.mark.asyncio
async def test_unconfirmed_refund_is_not_automatically_submitted(monkeypatch):
    db = session(monkeypatch, 'app.services.nisp_refund', scalars=[NS(status='requested')])
    service = NispRefundService(NS(query_refund=AsyncMock()))
    service._response = Mock(return_value='requested')
    assert await service.reconcile(1) == 'requested'
    service.wechat_pay.query_refund.assert_not_awaited()


@pytest.mark.asyncio
async def test_processing_result_updates_registration(monkeypatch):
    refund = NS(status='approved', registration_id=3)
    reg = NS(status='pending_refund_confirmation')
    db = session(monkeypatch, 'app.services.nisp_refund', scalars=[refund, reg])
    service = NispRefundService()
    service._response = Mock()
    service._lock_refund_chain = AsyncMock(return_value=(refund, NS(), reg))
    await service._apply_provider_result(refund_id=1, status='PROCESSING', refund_id_wechat='wx-1')
    assert refund.status == 'processing' and reg.status == 'refund_processing'


@pytest.mark.asyncio
async def test_zero_refund_updates_completed_order(monkeypatch):
    refund, order, reg = NS(), NS(status='completed'), NS()
    refund.order_id, refund.registration_id = 2, 3
    session(monkeypatch, 'app.services.nisp_refund', scalars=[refund, order, reg])
    service = NispRefundService()
    service._response = Mock()
    service._lock_refund_chain = AsyncMock(return_value=(refund, order, reg))
    await service._complete_zero_refund(1)
    assert order.status == 'refunded' and reg.status == 'refunded_closed'


@pytest.mark.asyncio
async def test_uncertain_refund_preserves_processing_for_reconciliation(monkeypatch):
    refund = NS(status='approved', registration_id=3, retry_count=0)
    reg = NS(status='pending_refund_confirmation')
    session(monkeypatch, 'app.services.nisp_refund', scalars=[refund, reg, refund])
    provider = NS(refund=AsyncMock(side_effect=WechatPayResultUnknownError('timeout')))
    service = NispRefundService(provider)
    service._lock_refund_chain = AsyncMock(return_value=(refund, NS(), reg))
    with pytest.raises(ConflictException):
        await service._submit(_PreparedRefund(1, 2, 'trade', 'refund', 100, 100))
    assert refund.status == 'processing' and reg.status == 'refund_processing'
    assert refund.retry_count == 1


@pytest.mark.asyncio
async def test_callback_success_is_not_overwritten_by_submission_error(monkeypatch):
    refund = NS(status='approved', registration_id=3, retry_count=0)
    reg = NS(status='pending_refund_confirmation')
    session(monkeypatch, 'app.services.nisp_refund', scalars=[refund, reg, refund])

    async def provider_refund(**kwargs):
        refund.status = 'succeeded'
        reg.status = 'refunded_closed'
        raise WechatPayResultUnknownError('timeout after callback')

    service = NispRefundService(NS(refund=provider_refund))
    service._lock_refund_chain = AsyncMock(return_value=(refund, NS(), reg))

    with pytest.raises(ConflictException):
        await service._submit(
            _PreparedRefund(1, 2, 'trade', 'refund', 100, 100))
    assert refund.status == 'succeeded' and reg.status == 'refunded_closed'
    assert refund.retry_count == 0


@pytest.mark.asyncio
async def test_rejection_requires_material_selection(monkeypatch):
    reg = NS(id=1, batch_id=1, order_id=2, status='pending_review',
             material_keys={'portrait_photo': 'old'})
    db = session(monkeypatch, 'app.services.nisp_registration', execute=[result(reg)],
                 scalars=[reg, batch(), NS(id=2), None])
    with pytest.raises(BusinessException):
        await NispRegistrationService().review(admin_id=9, registration_id=1,
            data=NispReviewDecisionRequest(decision='rejected', reason_detail='照片不清晰'))
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_success_callback_rejects_wrong_amount(monkeypatch):
    refund = NS(id=1, order_id=2, amount_cents=100)
    db = session(monkeypatch, 'app.services.nisp_refund', scalars=[refund])
    db.get = AsyncMock(return_value=NS(out_trade_no='trade', price=100))
    provider = NS(parse_refund_notification=Mock(return_value=NS(
        out_trade_no='trade', out_refund_no='refund', amount_total=100, amount_refund=1)))
    service = NispRefundService(provider)
    service._apply_provider_result = AsyncMock()
    with pytest.raises(ConflictException):
        await service.handle_callback_raw(raw_body=b'{}', headers={})
    service._apply_provider_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_recovers_zero_refunds_when_wechat_is_disabled(monkeypatch):
    from app.services import nisp_worker

    db = session(monkeypatch, 'app.services.nisp_worker')
    db.scalars.return_value = NS(all=lambda: [12])
    deadlines = AsyncMock()
    reconcile = AsyncMock()
    monkeypatch.setattr(nisp_worker.settings, 'WECHAT_PAY_ENABLED', False)
    monkeypatch.setattr(nisp_worker.NispRegistrationService, 'process_resubmission_timeouts', deadlines)
    monkeypatch.setattr(nisp_worker.NispRefundService, 'reconcile', reconcile)
    monkeypatch.setattr(nisp_worker.asyncio, 'sleep', AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await nisp_worker.nisp_lifecycle_worker_loop()
    deadlines.assert_awaited_once()
    reconcile.assert_awaited_once_with(12)
    statement = db.scalars.call_args.args[0].compile(compile_kwargs={'literal_binds': True})
    assert 'amount_cents = 0' in str(statement)
