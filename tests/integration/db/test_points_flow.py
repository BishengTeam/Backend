import asyncio
import os
from contextlib import asynccontextmanager
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
    prefix = f"pt{uuid4().hex[:18]}"
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
    from app.models.user import User

    async with session_factory() as db:
        user = User(openid=f"{prefix}-openid-{index}", phone=f"138{index:08d}")
        db.add(user)
        await db.flush()
        user_id = user.id
        await db.commit()
        return user_id


async def test_new_user_balance_defaults_to_zero(session_factory, app_context, test_prefix):
    user_id = await _seed_user(session_factory, test_prefix)
    service = app_context.points_module.PointsService()

    balance = await service.get_balance(user_id)

    assert balance.balance == 0


async def test_claim_points_is_idempotent(session_factory, app_context, test_prefix):
    from sqlalchemy import func, select

    from app.models.points import PointsHistory, UserPoints
    from app.schemas.points import PointsClaimRequest

    user_id = await _seed_user(session_factory, test_prefix)
    service = app_context.points_module.PointsService()

    first = await service.claim_points(
        user_id,
        PointsClaimRequest(scene="activity", source_id=f"{test_prefix}-activity"),
    )
    second = await service.claim_points(
        user_id,
        PointsClaimRequest(scene="activity", source_id=f"{test_prefix}-activity"),
    )

    async with session_factory() as db:
        account = (await db.execute(select(UserPoints).where(UserPoints.user_id == user_id))).scalar_one()
        history_count = await db.scalar(
            select(func.count()).select_from(PointsHistory).where(PointsHistory.user_id == user_id)
        )

    assert first.claimed is True
    assert second.claimed is False
    assert first.history_id == second.history_id
    assert account.balance == first.amount
    assert history_count == 1


async def test_concurrent_claim_points_only_grants_once(session_factory, app_context, test_prefix):
    from sqlalchemy import func, select

    from app.models.points import PointsHistory, UserPoints
    from app.schemas.points import PointsClaimRequest

    user_id = await _seed_user(session_factory, test_prefix)
    service = app_context.points_module.PointsService()
    request = PointsClaimRequest(scene="activity", source_id=f"{test_prefix}-concurrent")

    results = await asyncio.gather(
        service.claim_points(user_id, request),
        service.claim_points(user_id, request),
        service.claim_points(user_id, request),
    )

    async with session_factory() as db:
        account = (await db.execute(select(UserPoints).where(UserPoints.user_id == user_id))).scalar_one()
        history_count = await db.scalar(
            select(func.count()).select_from(PointsHistory).where(PointsHistory.user_id == user_id)
        )

    assert sum(1 for result in results if result.claimed) == 1
    assert len({result.history_id for result in results}) == 1
    assert account.balance == results[0].amount
    assert history_count == 1


async def test_redeem_points_debits_balance_and_writes_history(
    session_factory,
    app_context,
    test_prefix,
):
    from sqlalchemy import select

    from app.models.points import PointsHistory, UserPoints
    from app.schemas.points import PointsRedeemRequest

    user_id = await _seed_user(session_factory, test_prefix)
    service = app_context.points_module.PointsService()
    await service.grant_points(
        user_id,
        amount=30,
        action_type="adjust",
        description="seed points",
        source_type="test",
        source_id=f"{test_prefix}-seed",
    )

    result = await service.redeem_points(
        user_id,
        PointsRedeemRequest(redeem_type="course", amount=10, target_id=1),
    )

    async with session_factory() as db:
        account = (await db.execute(select(UserPoints).where(UserPoints.user_id == user_id))).scalar_one()
        history = (await db.execute(select(PointsHistory).where(PointsHistory.id == result.history_id))).scalar_one()

    assert result.balance == 20
    assert account.balance == 20
    assert history.amount == -10
    assert history.balance_after == 20


async def test_redeem_points_rejects_insufficient_balance(session_factory, app_context, test_prefix):
    from sqlalchemy import func, select

    from app.core.exceptions import BusinessException
    from app.models.points import PointsHistory, UserPoints
    from app.schemas.points import PointsRedeemRequest

    user_id = await _seed_user(session_factory, test_prefix)
    service = app_context.points_module.PointsService()
    await service.get_balance(user_id)

    with pytest.raises(BusinessException):
        await service.redeem_points(
            user_id,
            PointsRedeemRequest(redeem_type="exam_discount", amount=10),
        )

    async with session_factory() as db:
        account = (await db.execute(select(UserPoints).where(UserPoints.user_id == user_id))).scalar_one()
        history_count = await db.scalar(
            select(func.count()).select_from(PointsHistory).where(PointsHistory.user_id == user_id)
        )

    assert account.balance == 0
    assert history_count == 0


async def test_concurrent_redeem_points_does_not_overdraw(session_factory, app_context, test_prefix):
    from sqlalchemy import func, select

    from app.core.exceptions import BusinessException
    from app.models.points import PointsHistory, UserPoints
    from app.schemas.points import PointsRedeemRequest

    user_id = await _seed_user(session_factory, test_prefix)
    service = app_context.points_module.PointsService()
    await service.grant_points(
        user_id,
        amount=10,
        action_type="adjust",
        description="seed points",
        source_type="test",
        source_id=f"{test_prefix}-seed",
    )

    results = await asyncio.gather(
        service.redeem_points(user_id, PointsRedeemRequest(redeem_type="course", amount=10)),
        service.redeem_points(user_id, PointsRedeemRequest(redeem_type="course", amount=10)),
        return_exceptions=True,
    )

    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, Exception)]
    async with session_factory() as db:
        account = (await db.execute(select(UserPoints).where(UserPoints.user_id == user_id))).scalar_one()
        redeem_count = await db.scalar(
            select(func.count())
            .select_from(PointsHistory)
            .where(PointsHistory.user_id == user_id, PointsHistory.amount < 0)
        )

    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], BusinessException)
    assert account.balance == 0
    assert redeem_count == 1
