import asyncio
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
async def h3c_context(monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    database_url = _require_test_db_url()
    engine = create_async_engine(database_url, pool_size=5, max_overflow=5)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"hp_{uuid4().hex[:12]}"

    import app.services.h3c_order as h3c_order_module

    @asynccontextmanager
    async def test_db_ctx():
        async with factory() as session:
            yield session

    monkeypatch.setattr(h3c_order_module, "get_db_ctx", test_db_ctx)

    try:
        yield SimpleNamespace(
            factory=factory,
            prefix=prefix,
            service=h3c_order_module.H3cOrderService(),
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
                text("DELETE FROM price_config WHERE product_type LIKE :prefix_like"),
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
        await engine.dispose()


async def _seed_h3c_plan(context, *, user_count: int = 2, capacity: int = 1):
    from app.domain.certification.src.index import Certification
    from app.domain.order.src.index import Inventory, PriceConfig
    from app.domain.plan.src.index import Plan
    from app.domain.user.src.index import User, UserRealname

    product_type = f"{context.prefix}_H3C"
    now = datetime.now(timezone.utc)

    async with context.factory() as db:
        users = []
        for index in range(user_count):
            user = User(
                openid=f"{context.prefix}_openid_{index}",
                phone=f"1380000000{index}",
            )
            db.add(user)
            await db.flush()
            db.add(
                UserRealname(
                    user_id=user.id,
                    user_type="student",
                    real_name=f"Test User {index}",
                    id_card_number=f"1101011990010100{index}X"[:18],
                    status="verified",
                )
            )
            users.append(user)

        db.add(
            Certification(
                name=product_type,
                chinese_name=product_type,
                code=product_type,
                vendor="H3C",
                is_active=True,
            )
        )
        db.add(
            PriceConfig(
                product_type=product_type,
                user_type="student",
                price=12800,
                is_active=True,
            )
        )
        db.add(
            Inventory(
                inventory_type="certification",
                ref_code=product_type,
                total_quota=user_count,
                available_quota=user_count,
                locked_quota=0,
                sold_quota=0,
                is_active=True,
            )
        )
        plan = Plan(
            product_type=product_type,
            name="2026 H3C 第一批",
            apply_start=now - timedelta(days=1),
            apply_end=now + timedelta(days=1),
            exam_date=now + timedelta(days=7),
            capacity=capacity,
            status="published",
        )
        db.add(plan)
        await db.flush()
        result = SimpleNamespace(
            product_type=product_type,
            plan_id=plan.id,
            user_ids=[user.id for user in users],
        )
        await db.commit()
        return result


def _h3c_request(plan_id: int):
    from app.schemas.h3c import H3cOrderCreate

    return H3cOrderCreate(
        plan_id=plan_id,
        candidate_name="张三",
        gender="男",
        candidate_idcard="11010119900101001X",
        school="测试大学",
        address="成都市测试路 1 号",
        phone="13800000000",
        email="test@example.com",
        education="本科",
        first_name_en="SAN",
        last_name_en="ZHANG",
        coupon_code="COUPON-001",
        verify_code="123456",
        identity_tag="student",
        exam_datetime="2026-07-21 09:00:00",
        coupon_proof_oss="tests/coupon.jpg",
        degree_cert_oss="tests/degree.jpg",
    )


async def test_h3c_concurrent_orders_share_plan_capacity(h3c_context):
    from sqlalchemy import func, select

    from app.domain.order.src.index import Inventory, InventoryRecord, Order
    from app.port.exceptions import BusinessException

    data = await _seed_h3c_plan(h3c_context, user_count=2, capacity=1)
    results = await asyncio.gather(
        *[
            h3c_context.service.create_order(user_id, _h3c_request(data.plan_id))
            for user_id in data.user_ids
        ],
        return_exceptions=True,
    )

    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(successes) == 1, results
    assert len(failures) == 1, results
    assert isinstance(failures[0], BusinessException)
    assert failures[0].message == "该批次名额已满"

    response = successes[0]
    assert response.plan_id == data.plan_id
    assert response.product_type == data.product_type
    assert response.price == 12800
    assert response.out_trade_no
    assert response.extra_data["exam_code"] == data.product_type
    assert response.extra_data["plan_name"] == "2026 H3C 第一批"

    async with h3c_context.factory() as db:
        orders = (
            await db.execute(select(Order).where(Order.plan_id == data.plan_id))
        ).scalars().all()
        assert len(orders) == 1
        order = orders[0]
        inventory = (
            await db.execute(
                select(Inventory).where(Inventory.ref_code == data.product_type)
            )
        ).scalar_one()
        lock_records = await db.scalar(
            select(func.count())
            .select_from(InventoryRecord)
            .where(
                InventoryRecord.order_id == order.id,
                InventoryRecord.action == "lock",
            )
        )

    assert order.plan_id == data.plan_id
    assert order.product_type == data.product_type
    assert order.status == "pending"
    assert inventory.available_quota == 1
    assert inventory.locked_quota == 1
    assert lock_records == 1
