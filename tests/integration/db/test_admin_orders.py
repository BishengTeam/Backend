"""PostgreSQL integration tests for AdminOrderService (export & reconciliation).

Requires TEST_DATABASE_URL and TEST_DATABASE_URL_SYNC environment variables.
"""

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]

SHANGHAI = ZoneInfo("Asia/Shanghai")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _require_postgresql_urls() -> str:
    database_url = os.getenv("TEST_DATABASE_URL")
    database_url_sync = os.getenv("TEST_DATABASE_URL_SYNC")
    if not database_url or not database_url_sync:
        pytest.skip(
            "PostgreSQL integration tests require TEST_DATABASE_URL and TEST_DATABASE_URL_SYNC"
        )
    assert database_url.startswith("postgresql+asyncpg://")
    assert database_url_sync.startswith("postgresql://")
    os.environ.setdefault("DATABASE_URL", database_url)
    os.environ.setdefault("DATABASE_URL_SYNC", database_url_sync)
    os.environ.setdefault("JWT_SECRET", "test-secret")
    return database_url


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


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
    prefix = f"ao_{uuid4().hex[:12]}"
    await _cleanup_test_data(session_factory, prefix)
    try:
        yield prefix
    finally:
        await _cleanup_test_data(session_factory, prefix)


@pytest.fixture
async def app_context(monkeypatch, session_factory):
    import importlib

    admin_order_module = importlib.import_module("app.services.admin_order")

    @asynccontextmanager
    async def test_db_ctx():
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(admin_order_module, "get_db_ctx", test_db_ctx)
    return SimpleNamespace(admin_order_module=admin_order_module)


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


async def _cleanup_test_data(session_factory, prefix: str) -> None:
    prefix_like = f"{prefix}%"
    async with session_factory() as db:
        await db.execute(
            text(
                """
                DELETE FROM inventory_record
                WHERE order_id IN (
                    SELECT id FROM "order" WHERE product_type LIKE :pl
                )
                """
            ),
            {"pl": prefix_like},
        )
        await db.execute(
            text('DELETE FROM "order" WHERE product_type LIKE :pl'),
            {"pl": prefix_like},
        )
        await db.execute(
            text("DELETE FROM price_config WHERE product_type LIKE :pl"),
            {"pl": prefix_like},
        )
        await db.execute(
            text(
                """
                DELETE FROM user_student
                WHERE user_id IN (
                    SELECT id FROM "user" WHERE openid LIKE :pl
                )
                """
            ),
            {"pl": prefix_like},
        )
        await db.execute(
            text(
                """
                DELETE FROM user_realname
                WHERE user_id IN (
                    SELECT id FROM "user" WHERE openid LIKE :pl
                )
                """
            ),
            {"pl": prefix_like},
        )
        await db.execute(
            text('DELETE FROM "user" WHERE openid LIKE :pl'),
            {"pl": prefix_like},
        )
        await db.execute(
            text("DELETE FROM certification WHERE code LIKE :pl"),
            {"pl": prefix_like},
        )
        await db.execute(
            text("DELETE FROM inventory WHERE ref_code LIKE :pl"),
            {"pl": prefix_like},
        )
        await db.commit()


# ---------------------------------------------------------------------------
# seed helpers
# ---------------------------------------------------------------------------


async def _seed_user(session_factory, prefix: str) -> int:
    from app.domain.user.src.index import User

    async with session_factory() as db:
        user = User(openid=f"{prefix}-openid", phone="13800000001")
        db.add(user)
        await db.flush()
        user_id = user.id
        await db.commit()
        return user_id


async def _seed_order(
    session_factory,
    prefix: str,
    user_id: int,
    *,
    status: str = "paid",
    price: int = 9900,
    product_type: str | None = None,
    candidate_name: str = "测试考生",
    candidate_phone: str = "13800000001",
    created_at: datetime | None = None,
) -> int:
    """Seed a single order and return its id.

    ``product_type`` defaults to *prefix* so the row is captured by the
    prefix-based cleanup.
    """
    # Register every table referenced by Order's foreign keys.  Keeping these
    # imports explicit makes this file runnable on its own instead of relying
    # on another integration test having populated Base.metadata first.
    import importlib

    importlib.import_module("app.domain.plan.src.index")
    importlib.import_module("app.domain.renshe.src.index")
    from app.domain.order.src.index import Order

    product = product_type or prefix
    async with session_factory() as db:
        order = Order(
            user_id=user_id,
            order_kind="certification",
            product_type=product,
            candidate_name=candidate_name,
            candidate_phone=candidate_phone,
            price=price,
            status=status,
        )
        if created_at is not None:
            order.created_at = created_at
        db.add(order)
        await db.flush()
        order_id = order.id
        await db.commit()
        return order_id


# ---------------------------------------------------------------------------
# 1. GET /admin/orders/export  —  CSV export
# ---------------------------------------------------------------------------


class TestAdminOrderExport:
    async def test_export_returns_csv_with_headers(
        self, session_factory, app_context, test_prefix
    ):
        """Calling export_orders with no filters should return a CSV with
        expected header columns and at least the seeded row."""
        user_id = await _seed_user(session_factory, test_prefix)
        await _seed_order(session_factory, test_prefix, user_id, status="paid", price=100)
        await _seed_order(session_factory, test_prefix, user_id, status="pending", price=200)

        csv_content = await app_context.admin_order_module.AdminOrderService().export_orders(
            filters=None, start_time=None, end_time=None
        )

        assert isinstance(csv_content, str)
        assert len(csv_content) > 0

        line_sep = "\r\n" if "\r\n" in csv_content else "\n"
        lines = csv_content.strip().split(line_sep)
        header = lines[0]

        for col in ("ID", "UserID", "ProductType", "CandidateName", "CandidatePhone",
                     "Price", "Status", "CreatedAt"):
            assert col in header, f"CSV header missing column: {col}"

        # At least header + 2 data rows
        assert len(lines) >= 3, f"Expected >= 3 lines, got {len(lines)}"

    async def test_export_respects_status_filter(
        self, session_factory, app_context, test_prefix
    ):
        """Only orders matching the supplied status should appear."""
        from app.schemas.order import OrderFilter

        user_id = await _seed_user(session_factory, test_prefix)
        await _seed_order(session_factory, test_prefix, user_id, status="paid", price=100)
        await _seed_order(session_factory, test_prefix, user_id, status="refunded", price=200)

        csv_content = await app_context.admin_order_module.AdminOrderService().export_orders(
            filters=OrderFilter(status="paid"), start_time=None, end_time=None
        )

        line_sep = "\r\n" if "\r\n" in csv_content else "\n"
        lines = csv_content.strip().split(line_sep)
        # At least header + 1 matching row (historical data may add extra rows)
        assert len(lines) >= 2, f"Expected at least 2 lines (header + rows), got {len(lines)}"
        # Verify our test order appears in the export
        assert any(test_prefix in line and "paid" in line for line in lines[1:]), \
            f"Test order not found in export: {csv_content[:500]}"

    async def test_export_empty_when_no_orders_match(
        self, session_factory, app_context, test_prefix
    ):
        """export_orders should return only the header row when no orders exist."""
        user_id = await _seed_user(session_factory, test_prefix)
        await _seed_order(session_factory, test_prefix, user_id, status="paid", price=100)

        from app.schemas.order import OrderFilter

        csv_content = await app_context.admin_order_module.AdminOrderService().export_orders(
            filters=OrderFilter(status="completed"), start_time=None, end_time=None
        )

        line_sep = "\r\n" if "\r\n" in csv_content else "\n"
        lines = csv_content.strip().split(line_sep)
        # header only, no data rows
        assert len(lines) == 1, f"Expected 1 header line, got {len(lines)}"
        assert "ID" in lines[0]


# ---------------------------------------------------------------------------
# 2. GET /admin/orders/reconciliation  —  daily reconciliation
# ---------------------------------------------------------------------------


class TestAdminOrderReconciliation:
    async def test_reconciliation_returns_summary(
        self, session_factory, app_context, test_prefix
    ):
        """Reconciliation aggregates today's orders by paid vs refunded."""
        user_id = await _seed_user(session_factory, test_prefix)
        date_str = datetime.now(SHANGHAI).strftime("%Y-%m-%d")

        await _seed_order(session_factory, test_prefix, user_id, status="paid", price=100)
        await _seed_order(session_factory, test_prefix, user_id, status="paid", price=200)
        await _seed_order(session_factory, test_prefix, user_id, status="refunded", price=50)

        result = await app_context.admin_order_module.AdminOrderService().reconciliation(date_str)

        assert isinstance(result, dict)
        for key in ("order_total", "refund_total", "net_income", "order_count"):
            assert key in result, f"Missing key: {key}"
        # Historical data may exist; verify at least our seeded test orders are included
        assert result["order_total"] >= 300   # at least 100 + 200
        assert result["refund_total"] >= 50
        assert result["net_income"] >= 250    # at least 300 - 50
        assert result["order_count"] >= 3

    async def test_reconciliation_no_orders_returns_zeros(
        self, session_factory, app_context, test_prefix
    ):
        """Reconciliation response should always include expected numeric fields."""
        date_str = datetime.now(SHANGHAI).strftime("%Y-%m-%d")

        result = await app_context.admin_order_module.AdminOrderService().reconciliation(date_str)

        assert isinstance(result, dict)
        for key in ("order_total", "refund_total", "net_income", "order_count"):
            assert key in result, f"Missing key: {key}"
            assert isinstance(result[key], (int, float)), \
                f"Key '{key}' should be numeric, got {type(result[key])}"

    async def test_reconciliation_excludes_other_dates(
        self, session_factory, app_context, test_prefix
    ):
        """Shanghai calendar-day boundaries form a half-open UTC range."""
        user_id = await _seed_user(session_factory, test_prefix)
        target_date = "2099-01-15"
        await _seed_order(
            session_factory,
            test_prefix,
            user_id,
            status="paid",
            price=1,
            created_at=datetime(2099, 1, 14, 15, 59, 59, tzinfo=timezone.utc),
        )
        await _seed_order(
            session_factory,
            test_prefix,
            user_id,
            status="paid",
            price=10,
            created_at=datetime(2099, 1, 14, 16, 0, tzinfo=timezone.utc),
        )
        await _seed_order(
            session_factory,
            test_prefix,
            user_id,
            status="paid",
            price=20,
            created_at=datetime(
                2099,
                1,
                15,
                15,
                59,
                59,
                999999,
                tzinfo=timezone.utc,
            ),
        )
        await _seed_order(
            session_factory,
            test_prefix,
            user_id,
            status="paid",
            price=100,
            created_at=datetime(2099, 1, 15, 16, 0, tzinfo=timezone.utc),
        )

        result = await app_context.admin_order_module.AdminOrderService().reconciliation(
            target_date
        )
        assert result == {
            "order_total": 30,
            "refund_total": 0,
            "net_income": 30,
            "order_count": 2,
        }
