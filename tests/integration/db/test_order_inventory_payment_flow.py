import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest


pytestmark = pytest.mark.integration_db


def _require_postgresql_urls() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    database_url_sync = os.getenv("TEST_DATABASE_URL_SYNC")
    if not database_url or not database_url_sync:
        pytest.skip("PostgreSQL integration tests require TEST_DATABASE_URL and TEST_DATABASE_URL_SYNC")

    assert database_url.startswith("postgresql+asyncpg://")
    assert database_url_sync.startswith("postgresql://")

    os.environ.setdefault("DATABASE_URL", database_url)
    os.environ.setdefault("DATABASE_URL_SYNC", database_url_sync)
    os.environ.setdefault("JWT_SECRET", "test-secret")
    return database_url


@pytest.fixture
async def session_factory():
    database_url = _require_postgresql_urls()

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(database_url, pool_size=5, max_overflow=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def test_prefix(session_factory):
    prefix = f"it{uuid4().hex[:18]}"
    await _cleanup_test_data(session_factory, prefix)
    try:
        yield prefix
    finally:
        await _cleanup_test_data(session_factory, prefix)


@pytest.fixture
async def app_context(monkeypatch, session_factory):
    import importlib

    order_module = importlib.import_module("app.services.order")
    payment_module = importlib.import_module("app.services.payment")
    timeout_module = importlib.import_module("app.services.order_timeout")

    @asynccontextmanager
    async def test_db_ctx():
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(order_module, "get_db_ctx", test_db_ctx)
    monkeypatch.setattr(payment_module, "get_db_ctx", test_db_ctx)
    monkeypatch.setattr(timeout_module, "get_db_ctx", test_db_ctx)

    return SimpleNamespace(
        order_module=order_module,
        payment_module=payment_module,
        timeout_module=timeout_module,
    )


class ValidWechatPay:
    def verify_signature(self, payload: dict) -> bool:
        return True


async def _cleanup_test_data(session_factory, prefix: str) -> None:
    from sqlalchemy import text

    prefix_like = f"{prefix}%"
    async with session_factory() as db:
        await db.execute(
            text(
                """
                DELETE FROM inventory_record
                WHERE order_id IN (
                    SELECT id FROM "order" WHERE cert_type LIKE :prefix_like
                )
                OR inventory_id IN (
                    SELECT id FROM inventory WHERE ref_code LIKE :prefix_like
                )
                """
            ),
            {"prefix_like": prefix_like},
        )
        await db.execute(
            text('DELETE FROM "order" WHERE cert_type LIKE :prefix_like'),
            {"prefix_like": prefix_like},
        )
        await db.execute(
            text("DELETE FROM price_config WHERE cert_type LIKE :prefix_like"),
            {"prefix_like": prefix_like},
        )
        await db.execute(
            text(
                """
                DELETE FROM user_identity
                WHERE user_id IN (
                    SELECT id FROM "user" WHERE openid LIKE :prefix_like
                )
                """
            ),
            {"prefix_like": prefix_like},
        )
        await db.execute(
            text('DELETE FROM "user" WHERE openid LIKE :prefix_like'),
            {"prefix_like": prefix_like},
        )
        await db.execute(
            text("DELETE FROM inventory WHERE ref_code LIKE :prefix_like"),
            {"prefix_like": prefix_like},
        )
        await db.execute(
            text("DELETE FROM certification WHERE code LIKE :prefix_like"),
            {"prefix_like": prefix_like},
        )
        await db.commit()


async def _seed_base_data(
    session_factory,
    prefix: str,
    *,
    user_count: int = 1,
    available_quota: int = 1,
    locked_quota: int = 0,
    sold_quota: int = 0,
) -> SimpleNamespace:
    from app.models.certification import Certification
    from app.models.inventory import Inventory
    from app.models.price_config import PriceConfig
    from app.models.user import User
    from app.models.user_identity import UserIdentity

    cert_type = prefix
    total_quota = available_quota + locked_quota + sold_quota

    async with session_factory() as db:
        users = []
        for index in range(user_count):
            user = User(openid=f"{prefix}-openid-{index}", phone=f"138{index:08d}")
            db.add(user)
            await db.flush()
            db.add(
                UserIdentity(
                    user_id=user.id,
                    user_type="student",
                    real_name=f"Test User {index}",
                    id_card_number=f"{prefix[:8]}{index:010d}"[:18],
                    status="verified",
                )
            )
            users.append(user)

        db.add(
            Certification(
                name=cert_type,
                chinese_name=cert_type,
                code=cert_type,
                vendor="test",
                is_active=True,
            )
        )
        db.add(PriceConfig(cert_type=cert_type, user_type="student", price=100, is_active=True))
        inventory = Inventory(
            inventory_type="certification",
            ref_code=cert_type,
            total_quota=total_quota,
            available_quota=available_quota,
            locked_quota=locked_quota,
            sold_quota=sold_quota,
            is_active=True,
        )
        db.add(inventory)
        await db.flush()
        user_ids = [user.id for user in users]
        inventory_id = inventory.id
        await db.commit()

    return SimpleNamespace(cert_type=cert_type, user_ids=user_ids, inventory_id=inventory_id)


async def _seed_pending_order(
    session_factory,
    prefix: str,
    *,
    expires_at: datetime,
    available_quota: int = 0,
    locked_quota: int = 1,
) -> SimpleNamespace:
    from app.models.order import Order

    data = await _seed_base_data(
        session_factory,
        prefix,
        user_count=1,
        available_quota=available_quota,
        locked_quota=locked_quota,
    )
    async with session_factory() as db:
        order = Order(
            user_id=data.user_ids[0],
            inventory_id=data.inventory_id,
            cert_type=data.cert_type,
            candidate_name="Test Candidate",
            candidate_phone="13800000000",
            candidate_idcard=None,
            price=100,
            status="pending",
            out_trade_no=f"{prefix}-trade",
            expires_at=expires_at,
        )
        db.add(order)
        await db.flush()
        order_id = order.id
        out_trade_no = order.out_trade_no
        await db.commit()

    return SimpleNamespace(
        cert_type=data.cert_type,
        user_id=data.user_ids[0],
        inventory_id=data.inventory_id,
        order_id=order_id,
        out_trade_no=out_trade_no,
    )


async def test_concurrent_order_creation_does_not_oversell(
    session_factory,
    app_context,
    test_prefix,
):
    from sqlalchemy import func, select

    from app.core.exceptions import BusinessException
    from app.models.inventory import Inventory, InventoryRecord
    from app.models.order import Order
    from app.schemas.order import OrderCreate

    data = await _seed_base_data(session_factory, test_prefix, user_count=2, available_quota=1)
    service = app_context.order_module.OrderService()

    tasks = [
        service.create_order(
            user_id,
            OrderCreate(
                cert_type=data.cert_type,
                candidate_name=f"Candidate {index}",
                candidate_phone=f"1380000000{index}",
                candidate_idcard=None,
            ),
        )
        for index, user_id in enumerate(data.user_ids)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(successes) == 1, results
    assert len(failures) == 1, [repr(result) for result in results]
    assert isinstance(failures[0], BusinessException)

    async with session_factory() as db:
        inventory = (
            await db.execute(select(Inventory).where(Inventory.ref_code == data.cert_type))
        ).scalar_one()
        order_count = await db.scalar(
            select(func.count()).select_from(Order).where(Order.cert_type == data.cert_type)
        )
        lock_record_count = await db.scalar(
            select(func.count())
            .select_from(InventoryRecord)
            .where(InventoryRecord.inventory_id == inventory.id, InventoryRecord.action == "lock")
        )

    assert inventory.available_quota == 0
    assert inventory.locked_quota == 1
    assert inventory.sold_quota == 0
    assert order_count == 1
    assert lock_record_count == 1


async def test_success_callback_is_idempotent_and_confirms_inventory_once(
    session_factory,
    app_context,
    test_prefix,
):
    from sqlalchemy import func, select

    from app.models.inventory import Inventory, InventoryRecord
    from app.models.order import Order
    from app.schemas.payment import PaymentCallbackRequest

    data = await _seed_pending_order(
        session_factory,
        test_prefix,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    service = app_context.payment_module.PaymentService()
    service.wechat_pay = ValidWechatPay()

    callback = PaymentCallbackRequest(
        out_trade_no=data.out_trade_no,
        transaction_id=f"{test_prefix}-tx",
        trade_state="SUCCESS",
        total_fee=100,
        sign="valid",
    )

    first = await service.handle_callback(callback)
    second = await service.handle_callback(callback)

    async with session_factory() as db:
        order = (await db.execute(select(Order).where(Order.id == data.order_id))).scalar_one()
        inventory = (
            await db.execute(select(Inventory).where(Inventory.id == data.inventory_id))
        ).scalar_one()
        confirm_record_count = await db.scalar(
            select(func.count())
            .select_from(InventoryRecord)
            .where(InventoryRecord.order_id == data.order_id, InventoryRecord.action == "confirm")
        )

    assert first.processed is True
    assert second.processed is False
    assert order.status == "paid"
    assert order.transaction_id == callback.transaction_id
    assert inventory.available_quota == 0
    assert inventory.locked_quota == 0
    assert inventory.sold_quota == 1
    assert confirm_record_count == 1


async def test_timeout_close_releases_locked_inventory(
    session_factory,
    app_context,
    test_prefix,
):
    from sqlalchemy import func, select

    from app.models.inventory import Inventory, InventoryRecord
    from app.models.order import Order

    now = datetime.now(timezone.utc)
    data = await _seed_pending_order(
        session_factory,
        test_prefix,
        expires_at=now - timedelta(minutes=1),
    )
    service = app_context.timeout_module.OrderTimeoutCloseService()

    result = await service.close_expired_pending_orders(
        now=now,
        limit=10,
        close_reason=f"{test_prefix}-timeout",
    )

    async with session_factory() as db:
        order = (await db.execute(select(Order).where(Order.id == data.order_id))).scalar_one()
        inventory = (
            await db.execute(select(Inventory).where(Inventory.id == data.inventory_id))
        ).scalar_one()
        release_record_count = await db.scalar(
            select(func.count())
            .select_from(InventoryRecord)
            .where(InventoryRecord.order_id == data.order_id, InventoryRecord.action == "release")
        )

    assert result.closed == 1
    assert result.order_ids == [data.order_id]
    assert order.status == "closed"
    assert order.closed_at is not None
    assert order.close_reason == f"{test_prefix}-timeout"
    assert inventory.available_quota == 1
    assert inventory.locked_quota == 0
    assert inventory.sold_quota == 0
    assert release_record_count == 1
