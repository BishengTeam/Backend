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


@pytest.fixture
async def management_context(monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    database_url = _require_test_db_url()
    engine = create_async_engine(database_url, pool_size=5, max_overflow=5)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"pm_{uuid4().hex[:12]}"

    import app.services.plan_order_management as service_module

    @asynccontextmanager
    async def test_db_ctx():
        async with factory() as session:
            yield session

    monkeypatch.setattr(service_module, "get_db_ctx", test_db_ctx)

    try:
        yield SimpleNamespace(
            factory=factory,
            prefix=prefix,
            service=service_module.PlanOrderManagementService(),
        )
    finally:
        prefix_like = f"{prefix}%"
        async with factory() as db:
            await db.execute(
                text(
                    """
                    DELETE FROM review
                    WHERE target_id IN (
                        SELECT id FROM "order" WHERE product_type LIKE :prefix_like
                    )
                    OR reviewer_id IN (
                        SELECT id FROM admin_user WHERE username LIKE :prefix_like
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
                text('DELETE FROM "user" WHERE openid LIKE :prefix_like'),
                {"prefix_like": prefix_like},
            )
            await db.execute(
                text("DELETE FROM admin_user WHERE username LIKE :prefix_like"),
                {"prefix_like": prefix_like},
            )
            await db.commit()
        await engine.dispose()


async def _seed_management_data(context):
    from app.domain.order.src.index import Order
    from app.domain.plan.src.index import Plan
    from app.domain.review.src.index import Review
    from app.domain.user.src.index import AdminUser, User

    now = datetime.now(timezone.utc)
    product_a = f"{context.prefix}_A"
    product_b = f"{context.prefix}_B"

    async with context.factory() as db:
        paid_user = User(
            openid=f"{context.prefix}_paid_openid",
            phone="13800000000",
        )
        pending_user = User(
            openid=f"{context.prefix}_pending_openid",
            phone="13800000009",
        )
        admin = AdminUser(
            username=f"{context.prefix}_admin",
            password_hash="test-hash",
            role="super_admin",
            is_active=True,
        )
        db.add_all([paid_user, pending_user, admin])
        await db.flush()

        plan_a = Plan(
            product_type=product_a,
            name="Batch A",
            apply_start=now - timedelta(days=1),
            apply_end=now + timedelta(days=1),
            capacity=10,
            status="published",
        )
        plan_b = Plan(
            product_type=product_b,
            name="Batch B",
            apply_start=now - timedelta(days=1),
            apply_end=now + timedelta(days=1),
            capacity=10,
            status="published",
        )
        db.add_all([plan_a, plan_b])
        await db.flush()

        paid_a = Order(
            user_id=paid_user.id,
            order_kind="certification",
            product_type=product_a,
            plan_id=plan_a.id,
            candidate_name="Paid A",
            candidate_phone="13800000001",
            price=100,
            status="paid",
            out_trade_no=f"{context.prefix}_paid_a",
        )
        pending_a = Order(
            user_id=pending_user.id,
            order_kind="certification",
            product_type=product_a,
            plan_id=plan_a.id,
            candidate_name="Pending A",
            candidate_phone="13800000002",
            price=100,
            status="pending",
            out_trade_no=f"{context.prefix}_pending_a",
        )
        paid_b = Order(
            user_id=paid_user.id,
            order_kind="certification",
            product_type=product_b,
            plan_id=plan_b.id,
            candidate_name="Paid B",
            candidate_phone="13800000003",
            price=100,
            status="paid",
            out_trade_no=f"{context.prefix}_paid_b",
        )
        db.add_all([paid_a, pending_a, paid_b])
        await db.flush()

        db.add_all(
            [
                Review(
                    target_type="order",
                    target_id=paid_a.id,
                    reviewer_id=admin.id,
                    action="approve",
                    comment="approved A",
                ),
                Review(
                    target_type="order",
                    target_id=paid_b.id,
                    reviewer_id=admin.id,
                    action="reject",
                    comment="rejected B",
                ),
                Review(
                    target_type="identity",
                    target_id=paid_a.id,
                    reviewer_id=admin.id,
                    action="approve",
                    comment="same target id but not an order review",
                ),
            ]
        )
        result = SimpleNamespace(
            product_a=product_a,
            product_b=product_b,
            plan_a_id=plan_a.id,
            paid_a_id=paid_a.id,
        )
        await db.commit()
        return result


async def test_plan_management_queries_are_scoped_by_plan(management_context):
    from app.port.exceptions import NotFoundException

    data = await _seed_management_data(management_context)

    orders = await management_context.service.list_orders(
        product_type=data.product_a,
        plan_id=data.plan_a_id,
        status="paid",
        phone=None,
        page=1,
        page_size=20,
    )
    assert orders.total == 1
    assert [item.id for item in orders.items] == [data.paid_a_id]
    assert orders.items[0].plan_id == data.plan_a_id

    approvals = await management_context.service.list_approvals(
        product_type=data.product_a,
        plan_id=data.plan_a_id,
        action=None,
        page=1,
        page_size=20,
    )
    assert approvals.total == 1
    assert approvals.items[0].target_type == "order"
    assert approvals.items[0].target_id == data.paid_a_id

    with pytest.raises(NotFoundException):
        await management_context.service.list_orders(
            product_type=data.product_b,
            plan_id=data.plan_a_id,
            status=None,
            phone=None,
            page=1,
            page_size=20,
        )
