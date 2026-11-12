import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.integrations.wechat_pay import (
    WechatPayAPIError,
    WechatPayRefund,
    WechatPayResultUnknownError,
    WechatPayTransaction,
)
from app.port.config import settings
from app.port.exceptions import BusinessException


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
async def context(monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_async_engine(url, pool_size=8, max_overflow=8)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"r1_{uuid4().hex[:12]}"
    monkeypatch.setattr(settings, "WECHAT_PAY_MCHID", "1900000001")

    @asynccontextmanager
    async def db_ctx():
        async with factory() as session:
            yield session

    modules = [
        "app.services.payment",
        "app.services.payment_refund",
        "app.services.h3c_refund",
        "app.services.h3c_admin",
        "app.services.admin_order",
        "app.services.plan",
    ]
    for module_name in modules:
        module = __import__(module_name, fromlist=["get_db_ctx"])
        monkeypatch.setattr(module, "get_db_ctx", db_ctx)

    yield SimpleNamespace(factory=factory, prefix=prefix, db_ctx=db_ctx)

    async with factory() as db:
        like = f"{prefix}%"
        await db.execute(
            text('DELETE FROM "order" WHERE product_type LIKE :nisp_like'),
            {"nisp_like": f"NISP-{like}"},
        )
        await db.execute(
            text(
                """
                DELETE FROM payment_refund_task
                WHERE order_id IN (
                    SELECT id FROM "order" WHERE product_type LIKE :like
                )
                """
            ),
            {"like": like},
        )
        await db.execute(
            text(
                """
                DELETE FROM h3c_refund_request
                WHERE order_id IN (
                    SELECT id FROM "order" WHERE product_type LIKE :like
                )
                """
            ),
            {"like": like},
        )
        await db.execute(
            text(
                """
                DELETE FROM h3c_registration
                WHERE order_id IN (
                    SELECT id FROM "order" WHERE product_type LIKE :like
                )
                """
            ),
            {"like": like},
        )
        await db.execute(
            text("DELETE FROM h3c_exam_batch WHERE plan_id IN (SELECT id FROM plan WHERE product_type LIKE :like)"),
            {"like": like},
        )
        await db.execute(
            text(
                """
                DELETE FROM inventory_record
                WHERE order_id IN (SELECT id FROM "order" WHERE product_type LIKE :like)
                   OR inventory_id IN (SELECT id FROM inventory WHERE ref_code LIKE :like)
                """
            ),
            {"like": like},
        )
        await db.execute(text('DELETE FROM "order" WHERE product_type LIKE :like'), {"like": like})
        await db.execute(text("DELETE FROM inventory WHERE ref_code LIKE :like"), {"like": like})
        await db.execute(text("DELETE FROM plan WHERE product_type LIKE :like"), {"like": like})
        await db.execute(text("DELETE FROM \"user\" WHERE openid LIKE :like"), {"like": like})
        await db.commit()

    await engine.dispose()


async def _seed_h3c(context, *, refund_status="requested"):
    from app.domain.h3c.src.index import (
        H3cExamBatch,
        H3cRefundRequest,
        H3cRegistration,
    )
    from app.domain.order.src.index import (
        Inventory,
        InventoryRecord,
        Order,
    )
    from app.domain.plan.src.index import Plan
    from app.domain.user.src.index import AdminUser, User

    prefix = context.prefix
    now = _now()
    async with context.factory() as db:
        admin = AdminUser(
            username=f"{prefix}-admin",
            password_hash="test-hash",
            role="cert_admin",
            must_change_password=False,
        )
        user = User(openid=f"{prefix}-openid", phone="13800000000")
        plan = Plan(
            product_type=prefix,
            name=f"{prefix} batch",
            apply_start=now - timedelta(days=1),
            apply_end=now + timedelta(days=1),
            exam_date=now + timedelta(days=2),
            capacity=1,
            price_cents=100,
            status="published",
        )
        db.add_all([admin, user, plan])
        await db.flush()

        batch = H3cExamBatch(
            plan_id=plan.id,
            exam_code=f"{prefix}-exam",
            identity_tag=f"{prefix}-tag",
            coupon_price_cents=100,
            student_price_cents=100,
            full_price_cents=100,
        )
        inventory = Inventory(
            inventory_type="h3c_batch",
            ref_code=f"{prefix}-inventory",
            total_quota=1,
            available_quota=0,
            locked_quota=0,
            sold_quota=1,
        )
        db.add_all([batch, inventory])
        await db.flush()

        order = Order(
            user_id=user.id,
            order_kind="certification",
            product_type=prefix,
            plan_id=plan.id,
            inventory_id=inventory.id,
            price=100,
            status="paid",
            out_trade_no=f"{prefix}-trade",
            transaction_id=f"{prefix}-transaction",
            paid_at=now - timedelta(minutes=2),
        )
        db.add(order)
        await db.flush()
        db.add(
            InventoryRecord(
                inventory_id=inventory.id,
                order_id=order.id,
                action="confirm",
                quantity=1,
                before_total_quota=1,
                before_available_quota=1,
                before_locked_quota=0,
                before_sold_quota=0,
                after_total_quota=1,
                after_available_quota=0,
                after_locked_quota=0,
                after_sold_quota=1,
                reason="test_seed",
            )
        )
        registration = H3cRegistration(
            batch_id=batch.id,
            plan_id=plan.id,
            user_id=user.id,
            order_id=order.id,
            registration_no=f"{prefix}-reg",
            registration_type="full",
            status="pending_refund_confirmation",
            candidate_snapshot={},
            candidate_idcard="110101199001010011",
        )
        db.add(registration)
        await db.flush()
        refund = H3cRefundRequest(
            registration_id=registration.id,
            order_id=order.id,
            user_id=user.id,
            request_kind="review_failed",
            reason_code="review_failed",
            amount_cents=100,
            status=refund_status,
            requested_at=now,
        )
        db.add(refund)
        await db.flush()
        if refund.out_refund_no is None:
            refund.out_refund_no = f"H3RF{refund.id:028d}"
        await db.commit()
        return SimpleNamespace(
            admin_id=admin.id,
            user_id=user.id,
            plan_id=plan.id,
            batch_id=batch.id,
            order_id=order.id,
            registration_id=registration.id,
            refund_id=refund.id,
            inventory_id=inventory.id,
            out_trade_no=order.out_trade_no,
            transaction_id=order.transaction_id,
            out_refund_no=refund.out_refund_no,
        )


def _refund_payload(seed, *, status="SUCCESS"):
    return {
        "out_trade_no": seed.out_trade_no,
        "transaction_id": seed.transaction_id,
        "out_refund_no": seed.out_refund_no,
        "refund_id": f"{seed.refund_id}-wechat",
        "status": status,
        "success_time": _now().isoformat(),
        "amount": {"total": 100, "refund": 100, "currency": "CNY"},
        "mchid": settings.WECHAT_PAY_MCHID,
    }


async def test_h3c_success_callback_before_unknown_submit_keeps_final_state(context):
    from app.services.h3c_refund import H3cRefundService

    seed = await _seed_h3c(context)

    class Provider:
        mch_id = settings.WECHAT_PAY_MCHID
        refund_notify_url = settings.WECHAT_PAY_REFUND_NOTIFY_URL

        async def refund(self, **kwargs):
            payload = _refund_payload(seed)
            payload["out_refund_no"] = kwargs["out_refund_no"]
            await H3cRefundService(self)._apply_provider_result(
                WechatPayRefund.from_payload(payload, require_mchid=True),
            )
            raise WechatPayResultUnknownError("callback won the race")

    provider = Provider()
    service = H3cRefundService(provider)
    with pytest.raises(WechatPayResultUnknownError):
        await service.confirm(admin_id=seed.admin_id, refund_id=seed.refund_id)

    from app.domain.h3c.src.index import H3cRefundRequest, H3cRegistration
    from app.domain.order.src.index import Inventory, Order

    async with context.factory() as db:
        refund = await db.get(H3cRefundRequest, seed.refund_id)
        registration = await db.get(H3cRegistration, seed.registration_id)
        order = await db.get(Order, seed.order_id)
        inventory = await db.get(Inventory, seed.inventory_id)
        assert refund.status == "succeeded"
        assert registration.status == "refunded_closed"
        assert order.status == "refunded"
        assert (inventory.available_quota, inventory.sold_quota) == (1, 0)


async def test_h3c_recovery_reuses_provider_refund_number(context):
    from app.services.h3c_refund import H3cRefundService

    seed = await _seed_h3c(context, refund_status="approved")
    submitted = []

    class Provider:
        mch_id = settings.WECHAT_PAY_MCHID
        refund_notify_url = settings.WECHAT_PAY_REFUND_NOTIFY_URL

        async def query_refund(self, *, out_refund_no):
            raise WechatPayAPIError("RESOURCE_NOT_EXISTS", 404)

        async def refund(self, **kwargs):
            submitted.append(kwargs["out_refund_no"])
            return _refund_payload(seed)

    service = H3cRefundService(Provider())
    result = await service.recover(seed.refund_id)
    assert result.status == "succeeded"
    assert submitted == [seed.out_refund_no]


async def _seed_pending_closed_race(context):
    from datetime import datetime, timezone

    from app.domain.h3c.src.index import H3cExamBatch, H3cRegistration
    from app.domain.order.src.index import Inventory, InventoryRecord, Order
    from app.domain.plan.src.index import Plan
    from app.domain.user.src.index import AdminUser, User

    prefix = context.prefix
    now = datetime.now(timezone.utc)
    async with context.factory() as db:
        admin = AdminUser(
            username=f"{prefix}-admin", password_hash="x", role="cert_admin"
        )
        user = User(openid=f"{prefix}-openid")
        plan = Plan(
            product_type=prefix,
            name=f"{prefix}-race",
            apply_start=now - timedelta(days=1),
            apply_end=now + timedelta(days=1),
            exam_date=now + timedelta(days=2),
            capacity=1,
            price_cents=100,
            status="published",
        )
        db.add_all([admin, user, plan])
        await db.flush()
        batch = H3cExamBatch(
            plan_id=plan.id,
            exam_code=f"{prefix}-exam",
            identity_tag="test",
            coupon_price_cents=100,
            student_price_cents=100,
            full_price_cents=100,
        )
        inventory = Inventory(
            inventory_type="h3c_batch",
            ref_code=f"{prefix}-inventory",
            total_quota=1,
            available_quota=0,
            locked_quota=1,
            sold_quota=0,
        )
        db.add_all([batch, inventory])
        await db.flush()
        order = Order(
            user_id=user.id,
            order_kind="certification",
            product_type=prefix,
            plan_id=plan.id,
            inventory_id=inventory.id,
            price=100,
            status="pending",
            out_trade_no=f"{prefix}-trade",
            expires_at=now - timedelta(seconds=30),
        )
        db.add(order)
        await db.flush()
        db.add(
            InventoryRecord(
                inventory_id=inventory.id,
                order_id=order.id,
                action="lock",
                quantity=1,
                before_total_quota=1,
                before_available_quota=1,
                before_locked_quota=0,
                before_sold_quota=0,
                after_total_quota=1,
                after_available_quota=0,
                after_locked_quota=1,
                after_sold_quota=0,
                reason="test",
            )
        )
        registration = H3cRegistration(
            batch_id=batch.id,
            plan_id=plan.id,
            user_id=user.id,
            order_id=order.id,
            registration_no=f"{prefix}-reg",
            registration_type="full",
            status="pending_payment",
            candidate_snapshot={},
            candidate_idcard="110101199001010011",
        )
        db.add(registration)
        await db.commit()
        return SimpleNamespace(
            admin_id=admin.id,
            user_id=user.id,
            batch_id=batch.id,
            order_id=order.id,
            registration_id=registration.id,
            inventory_id=inventory.id,
            out_trade_no=order.out_trade_no,
            openid=user.openid,
        )


async def test_batch_cancel_and_late_payment_are_consistent_without_deadlock(context):
    from app.services.h3c_admin import H3cAdminBatchService
    from app.services.payment import PaymentService

    seed = await _seed_pending_closed_race(context)

    class PaymentProvider:
        appid = "test-appid"
        mch_id = "test-mchid"

    payment = PaymentService()
    payment.wechat_pay = PaymentProvider()
    transaction = WechatPayTransaction(
        appid=PaymentProvider.appid,
        mchid=PaymentProvider.mch_id,
        out_trade_no=seed.out_trade_no,
        trade_state="SUCCESS",
        amount_total=100,
        currency="CNY",
        attach=f"order:{seed.order_id}",
        transaction_id=f"{context.prefix}-late-transaction",
        success_time=_now() - timedelta(seconds=1),
        payer_openid=seed.openid,
    )

    await asyncio.gather(
        payment._apply_transaction(
            transaction,
            source="integration-test",
            verify_provider_fields=True,
        ),
        H3cAdminBatchService().cancel_batch(
            admin_id=seed.admin_id, batch_id=seed.batch_id
        ),
    )

    from app.domain.h3c.src.index import H3cRegistration
    from app.domain.order.src.index import Inventory, Order, PaymentRefundTask

    async with context.factory() as db:
        order = await db.get(Order, seed.order_id)
        registration = await db.get(H3cRegistration, seed.registration_id)
        inventory = await db.get(Inventory, seed.inventory_id)
        tasks = (
            await db.execute(
                select(PaymentRefundTask).where(PaymentRefundTask.order_id == order.id)
            )
        ).scalars().all()
        assert order.status == "closed"
        assert registration.status == "cancelled"
        assert (inventory.available_quota, inventory.locked_quota, inventory.sold_quota) == (1, 0, 0)
        assert len(tasks) == 1
        assert tasks[0].status in {"queued", "processing"}
        assert tasks[0].amount_cents == 100
        assert tasks[0].out_refund_no.startswith("LPRF")


async def test_late_payment_task_submits_once_and_callback_is_idempotent(
    context, monkeypatch
):
    from app.domain.order.src.index import Order, PaymentRefundTask
    from app.domain.user.src.index import User
    from app.services.payment_refund import PaymentRefundService

    merchant_id = "1900000001"
    monkeypatch.setattr(settings, "WECHAT_PAY_MCHID", merchant_id)
    prefix = context.prefix
    async with context.factory() as db:
        user = User(openid=f"{prefix}-openid")
        db.add(user)
        await db.flush()
        order = Order(
            user_id=user.id,
            order_kind="certification",
            product_type=prefix,
            price=100,
            status="closed",
            out_trade_no=f"{prefix}-trade",
            transaction_id=f"{prefix}-transaction",
            paid_at=_now(),
        )
        db.add(order)
        await db.flush()
        task = await PaymentRefundService.schedule_late_payment_refund(db, order)
        await db.commit()
        task_id = task.id
        order_id = order.id

    submitted = []

    class Provider:
        out_trade_no = f"{prefix}-trade"
        transaction_id = f"{prefix}-transaction"

        async def query_refund(self, *, out_refund_no):
            raise WechatPayAPIError("RESOURCE_NOT_EXISTS", 404)

        async def refund(self, **kwargs):
            submitted.append(kwargs["out_refund_no"])
            return {
                "out_trade_no": self.out_trade_no,
                "transaction_id": self.transaction_id,
                "out_refund_no": kwargs["out_refund_no"],
                "refund_id": f"{task_id}-refund",
                "status": "SUCCESS",
                "success_time": _now().isoformat(),
                "amount": {"total": 100, "refund": 100, "currency": "CNY"},
                "mchid": merchant_id,
            }

    service = PaymentRefundService(Provider())
    result = await service.process(task_id)
    assert result.status == "succeeded"
    assert submitted == [result.out_refund_no]

    # Re-running the worker and applying the same signed callback are no-ops.
    assert (await service.process(task_id)).status == "succeeded"
    await service._apply_provider_result(
        task_id,
        WechatPayRefund(
            out_trade_no=result.out_trade_no,
            transaction_id=result.transaction_id,
            out_refund_no=result.out_refund_no,
            refund_id=f"{task_id}-refund",
            status="SUCCESS",
            amount_total=100,
            amount_refund=100,
            currency="CNY",
            mchid=merchant_id,
        ),
    )
    assert submitted == [result.out_refund_no]

    async with context.factory() as db:
        task = await db.get(PaymentRefundTask, task_id)
        order = await db.get(Order, order_id)
        assert task.status == "succeeded"
        assert task.submit_attempts == 1
        assert order.status == "closed"
        assert len(
            (
                await db.execute(
                    select(PaymentRefundTask).where(
                        PaymentRefundTask.order_id == order_id
                    )
                )
            ).scalars().all()
        ) == 1


async def test_generic_admin_and_plan_entry_points_reject_h3c(context):
    from app.schemas.admin import AdminOrderReview
    from app.services.admin_order import AdminOrderService
    from app.services.plan import PlanService

    seed = await _seed_h3c(context)
    admin_service = AdminOrderService()
    with pytest.raises(BusinessException, match="H3C 订单必须使用专用"):
        await admin_service.review_order(
            seed.order_id, AdminOrderReview(action="approve")
        )
    with pytest.raises(BusinessException, match="H3C 订单必须使用专用"):
        await admin_service.refund_order(seed.order_id)
    with pytest.raises(BusinessException, match="H3C 批次必须使用专用"):
        await PlanService().cancel_plan(seed.plan_id, context.prefix)
    with pytest.raises(BusinessException, match="H3C 批次必须使用专用"):
        await PlanService().archive_plan(seed.plan_id, context.prefix)


async def test_generic_admin_rejects_nisp_by_product_prefix(context):
    from app.domain.order.src.index import Order
    from app.domain.user.src.index import User
    from app.schemas.admin import AdminOrderReview
    from app.services.admin_order import AdminOrderService

    prefix = context.prefix
    async with context.factory() as db:
        user = User(openid=f"{prefix}-openid")
        db.add(user)
        await db.flush()
        order = Order(
            user_id=user.id,
            order_kind="certification",
            product_type=f"NISP-{prefix}",
            price=100,
            status="paid",
            out_trade_no=f"{prefix}-trade",
        )
        db.add(order)
        await db.commit()
        order_id = order.id

    with pytest.raises(BusinessException, match="NISP 订单必须使用专用"):
        await AdminOrderService().review_order(
            order_id, AdminOrderReview(action="approve")
        )
    with pytest.raises(BusinessException, match="NISP 订单必须使用专用"):
        await AdminOrderService().refund_order(order_id)
