import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


def _require_test_db_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PostgreSQL integration tests require TEST_DATABASE_URL")
    assert database_url.startswith("postgresql+asyncpg://")
    os.environ.setdefault("DATABASE_URL", database_url)
    os.environ.setdefault("JWT_SECRET", "test-secret-key-min-32-chars-long")
    return database_url


class _UnconfiguredWechatPay:
    def _is_configured(self) -> bool:
        return False


@pytest.fixture
async def lifecycle_context(monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    database_url = _require_test_db_url()
    engine = create_async_engine(database_url, pool_size=5, max_overflow=5)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"il_{uuid4().hex[:12]}"

    import app.services.admin_order as admin_order_module
    import app.services.payment as payment_module
    import app.services.plan as plan_module

    @asynccontextmanager
    async def test_db_ctx():
        async with factory() as session:
            yield session

    monkeypatch.setattr(admin_order_module, "get_db_ctx", test_db_ctx)
    monkeypatch.setattr(admin_order_module, "WechatPayClient", _UnconfiguredWechatPay)
    monkeypatch.setattr(payment_module, "get_db_ctx", test_db_ctx)
    monkeypatch.setattr(plan_module, "get_db_ctx", test_db_ctx)

    try:
        yield SimpleNamespace(
            factory=factory,
            prefix=prefix,
            admin_service=admin_order_module.AdminOrderService(),
            payment_service=payment_module.PaymentService(),
            plan_service=plan_module.PlanService(),
        )
    finally:
        prefix_like = f"{prefix}%"
        async with factory() as db:
            await db.execute(
                text(
                    """
                    DELETE FROM inventory_record
                    WHERE order_id IN (
                        SELECT id FROM "order" WHERE product_type LIKE :prefix_like
                    )
                    OR inventory_id IN (
                        SELECT id FROM inventory WHERE ref_code LIKE :prefix_like
                    )
                    """
                ),
                {"prefix_like": prefix_like},
            )
            await db.execute(
                text('DELETE FROM "order" WHERE product_type LIKE :prefix_like'),
                {"prefix_like": prefix_like},
            )
            await db.execute(
                text("DELETE FROM plan WHERE product_type LIKE :prefix_like"),
                {"prefix_like": prefix_like},
            )
            await db.execute(
                text("DELETE FROM inventory WHERE ref_code LIKE :prefix_like"),
                {"prefix_like": prefix_like},
            )
            await db.execute(
                text('DELETE FROM "user" WHERE openid LIKE :prefix_like'),
                {"prefix_like": prefix_like},
            )
            await db.commit()
        await engine.dispose()


def _lock_record(inventory_id: int, order_id: int):
    from app.domain.order.src.index import InventoryRecord

    return InventoryRecord(
        inventory_id=inventory_id,
        order_id=order_id,
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
        reason="order_created",
    )


async def _seed_lifecycle_orders(context):
    from app.domain.order.src.index import Inventory, Order
    from app.domain.plan.src.index import Plan
    from app.domain.user.src.index import User

    now = datetime.now(timezone.utc)
    paid_product = f"{context.prefix}_paid"
    expired_product = f"{context.prefix}_expired"

    async with context.factory() as db:
        user = User(
            openid=f"{context.prefix}_openid",
            phone="13800000000",
            is_active=True,
        )
        db.add(user)
        await db.flush()

        paid_plan = Plan(
            product_type=paid_product,
            name="Paid Plan",
            apply_start=now - timedelta(days=1),
            apply_end=now + timedelta(days=1),
            capacity=1,
            status="published",
        )
        expired_plan = Plan(
            product_type=expired_product,
            name="Expired Order Plan",
            apply_start=now - timedelta(days=1),
            apply_end=now + timedelta(days=1),
            capacity=1,
            status="published",
        )
        db.add_all([paid_plan, expired_plan])
        await db.flush()

        paid_inventory = Inventory(
            inventory_type="certification",
            ref_code=paid_product,
            total_quota=1,
            available_quota=0,
            locked_quota=1,
            sold_quota=0,
            is_active=True,
        )
        expired_inventory = Inventory(
            inventory_type="certification",
            ref_code=expired_product,
            total_quota=1,
            available_quota=0,
            locked_quota=1,
            sold_quota=0,
            is_active=True,
        )
        db.add_all([paid_inventory, expired_inventory])
        await db.flush()

        paid_order = Order(
            user_id=user.id,
            order_kind="certification",
            product_type=paid_product,
            plan_id=paid_plan.id,
            inventory_id=paid_inventory.id,
            candidate_name="Paid Candidate",
            candidate_phone="13800000001",
            price=12800,
            status="pending",
            out_trade_no=f"{context.prefix}_paid_trade",
            expires_at=now + timedelta(minutes=30),
        )
        expired_order = Order(
            user_id=user.id,
            order_kind="certification",
            product_type=expired_product,
            plan_id=expired_plan.id,
            inventory_id=expired_inventory.id,
            candidate_name="Expired Candidate",
            candidate_phone="13800000002",
            price=12800,
            status="pending",
            out_trade_no=f"{context.prefix}_expired_trade",
            expires_at=now - timedelta(minutes=1),
        )
        db.add_all([paid_order, expired_order])
        await db.flush()
        db.add_all(
            [
                _lock_record(paid_inventory.id, paid_order.id),
                _lock_record(expired_inventory.id, expired_order.id),
            ]
        )
        result = SimpleNamespace(
            user_id=user.id,
            paid_product=paid_product,
            paid_plan_id=paid_plan.id,
            paid_order_id=paid_order.id,
            paid_out_trade_no=paid_order.out_trade_no,
            paid_inventory_id=paid_inventory.id,
            expired_plan_id=expired_plan.id,
            expired_order_id=expired_order.id,
            expired_inventory_id=expired_inventory.id,
        )
        await db.commit()
        return result


async def test_payment_refund_and_expiration_restore_inventory(lifecycle_context):
    from sqlalchemy import func, select
    from sqlalchemy.exc import IntegrityError

    from app.domain.order.src.index import Inventory, InventoryRecord, Order
    from app.port.exceptions import BusinessException
    from app.schemas.payment import PaymentCallbackRequest, PaymentPrepayRequest

    data = await _seed_lifecycle_orders(lifecycle_context)

    async with lifecycle_context.factory() as db:
        duplicate = Order(
            user_id=data.user_id,
            order_kind="certification",
            product_type=data.paid_product,
            plan_id=data.paid_plan_id,
            candidate_name="Duplicate",
            candidate_phone="13800000003",
            price=12800,
            status="pending",
            out_trade_no=f"{lifecycle_context.prefix}_duplicate",
        )
        db.add(duplicate)
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()

    callback = PaymentCallbackRequest(
        out_trade_no=data.paid_out_trade_no,
        transaction_id=f"{lifecycle_context.prefix}_transaction",
        trade_state="SUCCESS",
        total_fee=12800,
        sign="valid",
    )
    first_payment = await lifecycle_context.payment_service.handle_callback(callback)
    duplicate_payment = await lifecycle_context.payment_service.handle_callback(callback)
    assert first_payment.processed is True
    assert duplicate_payment.processed is False

    paid_plan = await lifecycle_context.plan_service.get_plan(data.paid_plan_id)
    assert paid_plan.enrolled == 1

    first_refund = await lifecycle_context.admin_service.refund_order(data.paid_order_id)
    duplicate_refund = await lifecycle_context.admin_service.refund_order(data.paid_order_id)
    assert first_refund.status == "refunded"
    assert duplicate_refund.status == "refunded"

    paid_plan = await lifecycle_context.plan_service.get_plan(data.paid_plan_id)
    assert paid_plan.enrolled == 0

    with pytest.raises(BusinessException, match="订单已过期"):
        await lifecycle_context.payment_service.create_prepay(
            data.user_id,
            PaymentPrepayRequest(order_id=data.expired_order_id),
        )
    expired_plan = await lifecycle_context.plan_service.get_plan(data.expired_plan_id)
    assert expired_plan.enrolled == 0

    async with lifecycle_context.factory() as db:
        paid_inventory = await db.get(Inventory, data.paid_inventory_id)
        expired_inventory = await db.get(Inventory, data.expired_inventory_id)
        paid_order = await db.get(Order, data.paid_order_id)
        expired_order = await db.get(Order, data.expired_order_id)
        confirm_count = await db.scalar(
            select(func.count())
            .select_from(InventoryRecord)
            .where(
                InventoryRecord.order_id == data.paid_order_id,
                InventoryRecord.action == "confirm",
            )
        )
        refund_count = await db.scalar(
            select(func.count())
            .select_from(InventoryRecord)
            .where(
                InventoryRecord.order_id == data.paid_order_id,
                InventoryRecord.action == "refund",
            )
        )
        release_count = await db.scalar(
            select(func.count())
            .select_from(InventoryRecord)
            .where(
                InventoryRecord.order_id == data.expired_order_id,
                InventoryRecord.action == "release",
            )
        )

    assert paid_order.status == "refunded"
    assert paid_inventory.available_quota == 1
    assert paid_inventory.locked_quota == 0
    assert paid_inventory.sold_quota == 0
    assert confirm_count == 1
    assert refund_count == 1

    assert expired_order.status == "closed"
    assert expired_inventory.available_quota == 1
    assert expired_inventory.locked_quota == 0
    assert expired_inventory.sold_quota == 0
    assert release_count == 1

    async with lifecycle_context.factory() as db:
        replacement = Order(
            user_id=data.user_id,
            order_kind="certification",
            product_type=data.paid_product,
            plan_id=data.paid_plan_id,
            candidate_name="Replacement",
            candidate_phone="13800000004",
            price=12800,
            status="pending",
            out_trade_no=f"{lifecycle_context.prefix}_replacement",
        )
        db.add(replacement)
        await db.commit()
