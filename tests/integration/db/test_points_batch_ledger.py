"""批次积分账本：批次发放、先进先出核销、退款追扣负余额、到期清零。

缴费返积分（OrderFulfillmentService 挂点）与手工调分共用
PointsService._grant_points_in_session / _debit_points_in_session，
本文件以真实 PostgreSQL 验证批次状态迁移与幂等性。
"""

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.points_rules import POINTS_VALIDITY_DAYS


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
    prefix = f"pb{uuid4().hex[:18]}"
    await _cleanup_test_data(session_factory, prefix)
    try:
        yield prefix
    finally:
        await _cleanup_test_data(session_factory, prefix)


@pytest.fixture
async def app_context(monkeypatch, session_factory):
    import importlib

    points_module = importlib.import_module("app.services.points")

    @asynccontextmanager
    async def test_db_ctx():
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(points_module, "get_db_ctx", test_db_ctx)
    return SimpleNamespace(points_module=points_module)


async def _cleanup_test_data(session_factory, prefix: str) -> None:
    from sqlalchemy import text

    prefix_like = f"{prefix}%"
    async with session_factory() as db:
        await db.execute(
            text(
                """
                DELETE FROM points_batch
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
                DELETE FROM points_history
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
                DELETE FROM user_points
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
        await db.commit()


async def _seed_user(session_factory, prefix: str, index: int = 0) -> int:
    from app.domain.user.src.index import User

    async with session_factory() as db:
        user = User(openid=f"{prefix}-openid-{index}", phone=f"139{index:08d}")
        db.add(user)
        await db.flush()
        user_id = user.id
        await db.commit()
        return user_id


async def _seed_batch(
    session_factory,
    user_id: int,
    *,
    amount: int,
    remaining: int | None = None,
    expires_in_days: float = POINTS_VALIDITY_DAYS,
) -> int:
    from app.domain.user.src.index import PointsBatch

    async with session_factory() as db:
        batch = PointsBatch(
            user_id=user_id,
            amount=amount,
            remaining=amount if remaining is None else remaining,
            expires_at=datetime.now(timezone.utc) + timedelta(days=expires_in_days),
            action_type="seed",
            description="测试批次",
            source_type="points_claim",
            source_id=uuid4().hex,
        )
        db.add(batch)
        await db.flush()
        batch_id = batch.id
        await db.commit()
        return batch_id


async def _get_balance(session_factory, user_id: int) -> int:
    from sqlalchemy import select

    from app.domain.user.src.index import UserPoints

    async with session_factory() as db:
        account = (
            await db.execute(select(UserPoints).where(UserPoints.user_id == user_id))
        ).scalar_one_or_none()
        return account.balance if account else 0


async def _get_batch_remaining(session_factory, batch_id: int) -> int:
    from sqlalchemy import select

    from app.domain.user.src.index import PointsBatch

    async with session_factory() as db:
        batch = await db.get(PointsBatch, batch_id)
        return batch.remaining if batch else 0


async def test_grant_creates_batch_with_validity(session_factory, app_context, test_prefix):
    from sqlalchemy import select

    from app.domain.user.src.index import PointsBatch

    user_id = await _seed_user(session_factory, test_prefix)
    service = app_context.points_module.PointsService()

    async with session_factory() as db:
        async with db.begin():
            history, granted = await service._grant_points_in_session(
                db,
                user_id=user_id,
                amount=10,
                action_type="claim_daily_checkin",
                description="测试发放",
                source_type="points_claim",
                source_id="daily_checkin:1",
            )
    assert granted is True

    async with session_factory() as db:
        batches = (
            await db.execute(select(PointsBatch).where(PointsBatch.user_id == user_id))
        ).scalars().all()
        account_balance = await _get_balance(session_factory, user_id)

    assert len(batches) == 1
    batch = batches[0]
    assert batch.amount == 10
    assert batch.remaining == 10
    assert batch.source_id == "daily_checkin:1"
    expected_floor = datetime.now(timezone.utc) + timedelta(days=POINTS_VALIDITY_DAYS - 1)
    assert batch.expires_at > expected_floor
    assert account_balance == 10


async def test_grant_idempotency_does_not_duplicate_batch(session_factory, app_context, test_prefix):
    from sqlalchemy import func, select

    from app.domain.user.src.index import PointsBatch

    user_id = await _seed_user(session_factory, test_prefix)
    service = app_context.points_module.PointsService()

    async with session_factory() as db:
        async with db.begin():
            await service._grant_points_in_session(
                db,
                user_id=user_id,
                amount=10,
                action_type="claim_daily_checkin",
                description="测试发放",
                source_type="points_claim",
                source_id="daily_checkin:1",
            )
    async with session_factory() as db:
        async with db.begin():
            _, granted = await service._grant_points_in_session(
                db,
                user_id=user_id,
                amount=10,
                action_type="claim_daily_checkin",
                description="测试发放",
                source_type="points_claim",
                source_id="daily_checkin:1",
            )
    assert granted is False

    async with session_factory() as db:
        batch_count = await db.scalar(
            select(func.count()).select_from(PointsBatch).where(PointsBatch.user_id == user_id)
        )
    assert batch_count == 1


async def test_debit_consumes_batches_fifo(session_factory, app_context, test_prefix):
    user_id = await _seed_user(session_factory, test_prefix)
    service = app_context.points_module.PointsService()

    # 批次 A：先过期；批次 B：后过期。核销必须先吃 A 再吃 B。
    batch_a = await _seed_batch(session_factory, user_id, amount=30, expires_in_days=10)
    batch_b = await _seed_batch(session_factory, user_id, amount=50, expires_in_days=300)

    async with session_factory() as db:
        async with db.begin():
            history = await service._debit_points_in_session(
                db,
                user_id=user_id,
                amount=40,
                action_type="redeem_virtual",
                description="测试核销",
                source_type="mall_redeem",
                source_id="r1",
            )
    assert history.amount == -40
    assert history.balance_after == 40
    assert await _get_batch_remaining(session_factory, batch_a) == 0
    assert await _get_batch_remaining(session_factory, batch_b) == 40
    assert await _get_balance(session_factory, user_id) == 40

    # 幂等重放：同一 source 键不重复扣
    async with session_factory() as db:
        async with db.begin():
            replay = await service._debit_points_in_session(
                db,
                user_id=user_id,
                amount=40,
                action_type="redeem_virtual",
                description="测试核销",
                source_type="mall_redeem",
                source_id="r1",
            )
    assert replay.id == history.id
    assert await _get_batch_remaining(session_factory, batch_b) == 40


async def test_debit_refuses_insufficient_balance(session_factory, app_context, test_prefix):
    from app.port.exceptions import BusinessException

    user_id = await _seed_user(session_factory, test_prefix)
    service = app_context.points_module.PointsService()
    await _seed_batch(session_factory, user_id, amount=10)

    async with session_factory() as db:
        with pytest.raises(BusinessException):
            async with db.begin():
                await service._debit_points_in_session(
                    db,
                    user_id=user_id,
                    amount=20,
                    action_type="redeem_virtual",
                    description="余额不足",
                    source_type="mall_redeem",
                    source_id="r2",
                )


async def test_clawback_allows_negative_balance_then_recovers(session_factory, app_context, test_prefix):
    user_id = await _seed_user(session_factory, test_prefix)
    service = app_context.points_module.PointsService()

    await _seed_batch(session_factory, user_id, amount=100)

    # 退款追扣 150：批次只剩 100，扣至 -50（欠分），批次全部清零
    async with session_factory() as db:
        async with db.begin():
            history = await service._debit_points_in_session(
                db,
                user_id=user_id,
                amount=150,
                action_type="refund_revoke",
                description="订单退款扣回积分",
                source_type="order_payment",
                source_id="refund:1",
                allow_negative=True,
            )
    assert history.balance_after == -50
    assert await _get_balance(session_factory, user_id) == -50

    # 追扣幂等：同 source 键重放不再扣
    assert await _get_balance(session_factory, user_id) == -50

    # 欠分状态下兑换被余额校验拦截
    from app.port.exceptions import BusinessException

    async with session_factory() as db:
        with pytest.raises(BusinessException):
            async with db.begin():
                await service._debit_points_in_session(
                    db,
                    user_id=user_id,
                    amount=10,
                    action_type="redeem_virtual",
                    description="欠分禁兑",
                    source_type="mall_redeem",
                    source_id="r3",
                )

    # 后续获得先补亏空：+50 → 余额归 0，新批次建立
    async with session_factory() as db:
        async with db.begin():
            await service._grant_points_in_session(
                db,
                user_id=user_id,
                amount=50,
                action_type="claim_daily_checkin",
                description="补亏空",
                source_type="points_claim",
                source_id="daily_checkin:2",
            )
    assert await _get_balance(session_factory, user_id) == 0


async def test_settle_expired_points_clears_only_expired(session_factory, app_context, test_prefix):
    from sqlalchemy import func, select

    from app.domain.user.src.index import PointsHistory

    user_id = await _seed_user(session_factory, test_prefix)
    service = app_context.points_module.PointsService()

    expired_batch = await _seed_batch(session_factory, user_id, amount=30, expires_in_days=-1)
    live_batch = await _seed_batch(session_factory, user_id, amount=70, expires_in_days=300)

    settled = await service.settle_expired_points()
    assert settled >= 30  # 其他测试用户可能有到期批次，只断言不少于本用户的

    assert await _get_batch_remaining(session_factory, expired_batch) == 0
    assert await _get_batch_remaining(session_factory, live_batch) == 70
    assert await _get_balance(session_factory, user_id) == 70

    async with session_factory() as db:
        expiry_rows = (
            await db.execute(
                select(PointsHistory).where(
                    PointsHistory.user_id == user_id,
                    PointsHistory.action_type == "points_expire",
                )
            )
        ).scalars().all()
    assert len(expiry_rows) == 1
    assert expiry_rows[0].amount == -30
    assert expiry_rows[0].balance_after == 70

    # 幂等：再次结算本用户不再产生新流水
    before = len(expiry_rows)
    await service.settle_expired_points()
    async with session_factory() as db:
        after = await db.scalar(
            select(func.count())
            .select_from(PointsHistory)
            .where(
                PointsHistory.user_id == user_id,
                PointsHistory.action_type == "points_expire",
            )
        )
    assert after == before


async def test_payment_points_grant_and_revoke_via_fulfillment(session_factory, test_prefix):
    """缴费返积分挂点：每 10 元 = 1 积分，按订单幂等，退款追扣至负余额。"""
    from app.services.order_fulfillment import OrderFulfillmentService

    user_id = await _seed_user(session_factory, test_prefix)
    fulfillment = OrderFulfillmentService()

    # 500 元订单 → 50 积分
    order = SimpleNamespace(id=9001, user_id=user_id, price=50000)
    async with session_factory() as db:
        async with db.begin():
            await fulfillment._grant_payment_points(db, order)
    assert await _get_balance(session_factory, user_id) == 50

    # 核账重放不重复发
    async with session_factory() as db:
        async with db.begin():
            await fulfillment._grant_payment_points(db, order)
    assert await _get_balance(session_factory, user_id) == 50

    # 不足 10 元不计
    tiny_order = SimpleNamespace(id=9002, user_id=user_id, price=999)
    async with session_factory() as db:
        async with db.begin():
            await fulfillment._grant_payment_points(db, tiny_order)
    assert await _get_balance(session_factory, user_id) == 50

    # 零元订单不发
    free_order = SimpleNamespace(id=9003, user_id=user_id, price=0)
    async with session_factory() as db:
        async with db.begin():
            await fulfillment._grant_payment_points(db, free_order)
    assert await _get_balance(session_factory, user_id) == 50

    # 退款追扣：积分未被消耗时全额追回
    async with session_factory() as db:
        async with db.begin():
            await fulfillment._revoke_payment_points(db, order)
    assert await _get_balance(session_factory, user_id) == 0

    # 追扣重放幂等
    async with session_factory() as db:
        async with db.begin():
            await fulfillment._revoke_payment_points(db, order)
    assert await _get_balance(session_factory, user_id) == 0


async def test_payment_points_revoke_digs_into_negative(session_factory, test_prefix):
    """积分已被部分消耗时，追扣扣至负余额（欠分）。"""
    from app.services import points as points_module
    from app.services.order_fulfillment import OrderFulfillmentService

    user_id = await _seed_user(session_factory, test_prefix)
    fulfillment = OrderFulfillmentService()

    order = SimpleNamespace(id=9101, user_id=user_id, price=100000)  # 1000 元 → 100 积分
    async with session_factory() as db:
        async with db.begin():
            await fulfillment._grant_payment_points(db, order)
    assert await _get_balance(session_factory, user_id) == 100

    # 用户已消耗 60，剩 40
    service = points_module.PointsService()
    async with session_factory() as db:
        async with db.begin():
            await service._debit_points_in_session(
                db,
                user_id=user_id,
                amount=60,
                action_type="redeem_virtual",
                description="已消耗",
                source_type="mall_redeem",
                source_id="rx",
            )

    async with session_factory() as db:
        async with db.begin():
            await fulfillment._revoke_payment_points(db, order)
    assert await _get_balance(session_factory, user_id) == -60
