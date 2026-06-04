"""Admin user management PostgreSQL integration tests.

Requires TEST_DATABASE_URL environment variable.
"""

import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


def _require_test_db_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip(
            "PostgreSQL integration tests require TEST_DATABASE_URL"
        )
    assert database_url.startswith(
        "postgresql+asyncpg://"
    ), f"TEST_DATABASE_URL must use asyncpg driver: {database_url}"

    # Ensure the application-level settings also resolve to the test DB
    # in case any code path still reaches the real get_db_ctx.
    os.environ.setdefault("DATABASE_URL", database_url)
    os.environ.setdefault("JWT_SECRET", "test-secret")
    return database_url


@pytest.fixture
async def test_context(monkeypatch):
    """Provide a test session factory, prefix, and patched get_db_ctx.

    Yields ``(factory, prefix)``.  The ``get_db_ctx`` inside
    ``app.services.admin_user`` is monkeypatched so that
    ``AdminUserService`` methods use the same test database.
    """
    database_url = _require_test_db_url()

    engine = create_async_engine(database_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"au_{uuid4().hex[:12]}"

    # Patch get_db_ctx so AdminUserService uses the test factory
    import app.services.admin_user as admin_user_module

    @asynccontextmanager
    async def _test_db_ctx():
        async with factory() as session:
            yield session

    monkeypatch.setattr(admin_user_module, "get_db_ctx", _test_db_ctx)

    try:
        yield factory, prefix
    finally:
        # Clean up all test data created with this prefix
        async with factory() as db:
            pl = f"{prefix}%"
            await db.execute(
                text(
                    'DELETE FROM conversation WHERE user_id IN '
                    '(SELECT id FROM "user" WHERE openid LIKE :pl)'
                ),
                {"pl": pl},
            )
            await db.execute(
                text('DELETE FROM "order" WHERE cert_type LIKE :pl'),
                {"pl": pl},
            )
            await db.execute(
                text(
                    'DELETE FROM user_identity WHERE user_id IN '
                    '(SELECT id FROM "user" WHERE openid LIKE :pl)'
                ),
                {"pl": pl},
            )
            await db.execute(
                text('DELETE FROM "user" WHERE openid LIKE :pl'),
                {"pl": pl},
            )
            await db.commit()
        await engine.dispose()


# ---------------------------------------------------------------------------
# 1. POST /admin/users/batch-delete — 批量软删除
# ---------------------------------------------------------------------------


async def test_batch_delete_soft_deletes_users(test_context):
    """Create 3 users, batch-delete them, verify is_deleted=True."""
    from app.models.user import User
    from app.services.admin_user import AdminUserService

    factory, prefix = test_context

    # Seed 3 test users
    user_ids: list[int] = []
    async with factory() as db:
        for i in range(3):
            user = User(
                openid=f"{prefix}_del_{i}",
                phone=f"138{i:08d}",
            )
            db.add(user)
            await db.flush()
            user_ids.append(user.id)
        await db.commit()

    # Batch soft-delete
    count = await AdminUserService().batch_delete(user_ids)
    assert count == 3, f"Expected 3 deleted, got {count}"

    # Verify each user is now soft-deleted
    async with factory() as db:
        for uid in user_ids:
            user = (await db.execute(select(User).where(User.id == uid))).scalar_one()
            assert user.is_deleted is True, f"User {uid} should be soft-deleted"


# ---------------------------------------------------------------------------
# 2. GET /admin/users/export — 用户 CSV 导出
# ---------------------------------------------------------------------------


async def test_export_users_returns_csv_with_header_and_data(test_context):
    """Create 2 users, export CSV, verify header and data rows."""
    from app.models.user import User
    from app.services.admin_user import AdminUserService

    factory, prefix = test_context

    # Seed 2 test users
    openids: list[str] = []
    async with factory() as db:
        for i in range(2):
            openid = f"{prefix}_exp_{i}"
            user = User(openid=openid, phone=f"139{i:08d}")
            db.add(user)
            openids.append(openid)
        await db.commit()

    # Export as CSV
    csv_content = await AdminUserService().export_users(None)

    # Verify non-empty and header present
    assert csv_content, "CSV content should not be empty"
    assert "ID,OpenID,Phone,IsActive,CreatedAt" in csv_content, (
        "CSV must contain the expected header row"
    )

    # Verify each seeded user appears in the output
    for openid in openids:
        assert openid in csv_content, (
            f"CSV must contain user openid '{openid}'"
        )


# ---------------------------------------------------------------------------
# 3. GET /admin/users/{id}/orders — 用户订单记录
# ---------------------------------------------------------------------------


async def test_get_user_orders_returns_order_list(test_context):
    """Create a user + order, then fetch orders via the admin service."""
    from app.domain.order.src.index import Order
    from app.models.user import User
    from app.services.admin_user import AdminUserService

    factory, prefix = test_context

    user_id: int
    async with factory() as db:
        # Create user
        user = User(openid=f"{prefix}_ord", phone="13800000000")
        db.add(user)
        await db.flush()
        user_id = user.id

        # Create an order associated with the user
        order = Order(
            user_id=user_id,
            cert_type=f"{prefix}_cert",
            candidate_name="Test Candidate",
            candidate_phone="13800000000",
            price=9900,
            status="pending",
        )
        db.add(order)
        await db.commit()

    # Fetch via admin service
    orders = await AdminUserService().get_user_orders(user_id)

    assert isinstance(orders, list), "Result must be a list"
    assert len(orders) > 0, "Should return at least one order"
    order = orders[0]
    for field in ("id", "status", "cert_type"):
        assert field in order, f"Order dict must contain '{field}'"


# ---------------------------------------------------------------------------
# 4. GET /admin/users/{id}/conversations — 用户对话记录
# ---------------------------------------------------------------------------


async def test_get_user_conversations_returns_session_list(test_context):
    """Create a user + conversation, then verify conversation fields."""
    from app.models.conversation import Conversation
    from app.models.user import User
    from app.services.admin_user import AdminUserService

    factory, prefix = test_context

    user_id: int
    async with factory() as db:
        # Create user
        user = User(openid=f"{prefix}_conv", phone="13800000000")
        db.add(user)
        await db.flush()
        user_id = user.id

        # Create a conversation associated with the user
        conv = Conversation(
            user_id=user_id,
            session_id=f"{prefix}_session",
            backend_type="manual",
        )
        db.add(conv)
        await db.commit()

    # Fetch via admin service
    conversations = await AdminUserService().get_user_conversations(user_id)

    assert isinstance(conversations, list), "Result must be a list"
    assert len(conversations) > 0, "Should return at least one conversation"
    conv = conversations[0]
    for field in ("session_id", "backend_type"):
        assert field in conv, f"Conversation dict must contain '{field}'"
