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


async def _cleanup_test_data(session_factory, prefix: str) -> None:
    from sqlalchemy import text

    prefix_like = f"{prefix}%"
    async with session_factory() as db:
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
            text("DELETE FROM price_config WHERE product_type LIKE :prefix_like"),
            {"prefix_like": prefix_like},
        )
        await db.execute(
            text(
                """
                DELETE FROM user_student
                WHERE user_id IN (
                    SELECT id FROM "user" WHERE openid LIKE :prefix_like
                )
                """
            ),
            {"prefix_like": prefix_like},
        )
        await db.execute(
            text(
                """
                DELETE FROM user_realname
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
    from app.domain.certification.src.index import Certification
    from app.domain.order.src.index import Inventory, PriceConfig
    from app.domain.user.src.index import User, UserRealname

    product_type = prefix
    total_quota = available_quota + locked_quota + sold_quota

    async with session_factory() as db:
        users = []
        for index in range(user_count):
            user = User(openid=f"{prefix}-openid-{index}", phone=f"138{index:08d}")
            db.add(user)
            await db.flush()
            db.add(
                UserRealname(
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
                name=product_type,
                chinese_name=product_type,
                code=product_type,
                vendor="test",
                is_active=True,
            )
        )
        db.add(
            PriceConfig(
                product_type=product_type,
                user_type="student",
                price=100,
                is_active=True,
            )
        )
        inventory = Inventory(
            inventory_type="certification",
            ref_code=product_type,
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

    return SimpleNamespace(
        product_type=product_type,
        user_ids=user_ids,
        inventory_id=inventory_id,
    )


async def _seed_pending_order(
    session_factory,
    prefix: str,
    *,
    expires_at: datetime,
    available_quota: int = 0,
    locked_quota: int = 1,
) -> SimpleNamespace:
    from app.domain.order.src.index import Order

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
            order_kind="certification",
            product_type=data.product_type,
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
        product_type=data.product_type,
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

    from app.port.exceptions import BusinessException
    from app.domain.order.src.index import Inventory, InventoryRecord, Order
    from app.schemas.order import OrderCreate

    data = await _seed_base_data(session_factory, test_prefix, user_count=2, available_quota=1)
    service = app_context.order_module.OrderService()

    tasks = [
        service.create_order(
            user_id,
            OrderCreate(
                order_kind="certification",
                product_type=data.product_type,
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
            await db.execute(
                select(Inventory).where(Inventory.ref_code == data.product_type)
            )
        ).scalar_one()
        order_count = await db.scalar(
            select(func.count())
            .select_from(Order)
            .where(Order.product_type == data.product_type)
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

    from app.domain.order.src.index import Inventory, InventoryRecord, Order
    from app.integrations.wechat_pay import WechatPayTransaction

    data = await _seed_pending_order(
        session_factory,
        test_prefix,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    service = app_context.payment_module.PaymentService()

    callback = WechatPayTransaction(
        appid="integration-test",
        mchid="integration-test",
        out_trade_no=data.out_trade_no,
        transaction_id=f"{test_prefix}-tx",
        trade_state="SUCCESS",
        amount_total=100,
        currency="CNY",
        attach="",
        success_time=datetime.now(timezone.utc),
    )

    first = await service._apply_transaction(
        callback, source="integration_test", verify_provider_fields=False
    )
    second = await service._apply_transaction(
        callback, source="integration_test", verify_provider_fields=False
    )

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
    from app.domain.order.src.index import (
        Inventory,
        InventoryRecord,
        Order,
        apply_order_status_transition,
        release_inventory_lock,
    )
    from app.schemas.order import OrderCreate

    now = datetime.now(timezone.utc)
    data = await _seed_base_data(
        session_factory,
        test_prefix,
        user_count=1,
        available_quota=1,
    )

    # 通过正规通道创建订单（自动走 lock_certification_inventory 原子锁库存）
    order_svc = app_context.order_module.OrderService()
    order_resp = await order_svc.create_order(
        data.user_ids[0],
        OrderCreate(
            order_kind="certification",
            product_type=data.product_type,
            candidate_name="Test Candidate",
            candidate_phone="13800000000",
        ),
    )

    # 手动将 expires_at 改为过去，模拟支付超时
    async with session_factory() as db:
        order = await db.get(Order, order_resp.id)
        order.expires_at = now - timedelta(minutes=1)
        await db.commit()

    # 两阶段测试：
    # 阶段1：关闭超时订单（OrderTimeoutCloseService.close_expired_pending_orders）
    # 阶段2：释放库存锁（新会话的 release_inventory_lock）
    # 分开验证以隔离 close_expired_pending_orders 内部的 raw-UPDATE 会话问题

    close_reason = f"{test_prefix}-timeout"

    # 阶段1：关闭订单（手动模拟 close_expired_pending_order + 提交）
    async with session_factory() as db:
        order = await db.get(Order, order_resp.id)
        changed = apply_order_status_transition(order, "closed")
        assert changed is True
        order.closed_at = now
        order.close_reason = close_reason
        await db.commit()

    # 阶段2：在新会话中释放库存锁
    async with session_factory() as db:
        order = await db.get(Order, order_resp.id)
        released = await release_inventory_lock(db, order, reason=close_reason)
        assert released is True
        await db.commit()

    # 验证最终状态
    async with session_factory() as db:
        order = await db.get(Order, order_resp.id)
        inventory = (await db.execute(
            select(Inventory).where(Inventory.ref_code == data.product_type)
        )).scalar_one()
        lock_records = await db.scalar(
            select(func.count()).select_from(InventoryRecord)
            .where(InventoryRecord.order_id == order_resp.id, InventoryRecord.action == "lock")
        )
        release_records = await db.scalar(
            select(func.count()).select_from(InventoryRecord)
            .where(InventoryRecord.order_id == order_resp.id, InventoryRecord.action == "release")
        )

    assert order.status == "closed"
    assert order.closed_at is not None
    assert order.close_reason == close_reason
    assert inventory.available_quota == 1  # 释放回 available
    assert inventory.locked_quota == 0     # 锁已清除
    assert inventory.sold_quota == 0
    assert lock_records == 1       # create_order 产生的 lock 记录
    assert release_records == 1    # 释放产生的 release 记录
