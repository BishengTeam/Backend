"""Plan CRUD and state transition PostgreSQL integration tests.

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
        pytest.skip("PostgreSQL integration tests require TEST_DATABASE_URL")
    assert database_url.startswith(
        "postgresql+asyncpg://"
    ), f"TEST_DATABASE_URL must use asyncpg driver: {database_url}"
    os.environ.setdefault("DATABASE_URL", database_url)
    os.environ.setdefault("JWT_SECRET", "test-secret-key-min-32-chars-long")
    return database_url


@pytest.fixture
async def test_context(monkeypatch):
    """Provide test session factory and prefix, patch get_db_ctx."""
    database_url = _require_test_db_url()
    engine = create_async_engine(database_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"pl_{uuid4().hex[:12]}"

    import app.services.plan as plan_module

    @asynccontextmanager
    async def _test_db_ctx():
        async with factory() as session:
            yield session

    monkeypatch.setattr(plan_module, "get_db_ctx", _test_db_ctx)

    try:
        yield factory, prefix
    finally:
        async with factory() as db:
            await db.execute(text("DELETE FROM plan WHERE product_type LIKE :pl"), {"pl": f"{prefix}%"})
            await db.commit()
        await engine.dispose()


# ---------------------------------------------------------------------------
# 1. Create a plan (draft)
# ---------------------------------------------------------------------------


async def test_create_plan_returns_draft(test_context):
    from app.schemas.plan import PlanCreate
    from app.services.plan import PlanService

    factory, prefix = test_context
    product_type = f"{prefix}_cert"

    result = await PlanService().create_plan(
        product_type,
        PlanCreate(name="2026第一期", capacity=30),
    )
    assert result.id > 0
    assert result.product_type == product_type
    assert result.name == "2026第一期"
    assert result.capacity == 30
    assert result.enrolled == 0
    assert result.status == "draft"


# ---------------------------------------------------------------------------
# 2. List plans for a product_type
# ---------------------------------------------------------------------------


async def test_list_plans_returns_only_same_product_type(test_context):
    from app.schemas.plan import PlanCreate
    from app.services.plan import PlanService

    factory, prefix = test_context
    pt_a = f"{prefix}_a"
    pt_b = f"{prefix}_b"

    await PlanService().create_plan(pt_a, PlanCreate(name="Batch A"))
    await PlanService().create_plan(pt_b, PlanCreate(name="Batch B"))

    # List pt_a only
    plans_a = await PlanService().list_plans(pt_a)
    assert len(plans_a) >= 1
    for p in plans_a:
        assert p.product_type == pt_a

    # List pt_b only
    plans_b = await PlanService().list_plans(pt_b)
    assert len(plans_b) >= 1
    for p in plans_b:
        assert p.product_type == pt_b


# ---------------------------------------------------------------------------
# 3. Full state transition: draft → published → archived
# ---------------------------------------------------------------------------


async def test_full_state_transition(test_context):
    from app.schemas.plan import PlanCreate
    from app.services.plan import PlanService

    factory, prefix = test_context
    product_type = f"{prefix}_flow"

    # Create draft
    plan = await PlanService().create_plan(product_type, PlanCreate(name="Flow Test"))
    assert plan.status == "draft"

    # Publish
    result = await PlanService().publish_plan(plan.id)
    assert result.status == "published"

    # Archive
    result = await PlanService().archive_plan(plan.id)
    assert result.status == "archived"


# ---------------------------------------------------------------------------
# 4. Cannot edit after published
# ---------------------------------------------------------------------------


async def test_cannot_edit_published_plan(test_context):
    from app.schemas.plan import PlanCreate, PlanUpdate
    from app.services.plan import PlanService
    from app.port.exceptions import BusinessException

    factory, prefix = test_context
    product_type = f"{prefix}_lock"

    plan = await PlanService().create_plan(product_type, PlanCreate(name="Locked"))
    await PlanService().publish_plan(plan.id)

    with pytest.raises(BusinessException, match="仅草稿"):
        await PlanService().update_plan(plan.id, PlanUpdate(name="New Name"))


# ---------------------------------------------------------------------------
# 5. Cannot delete published plan — only draft
# ---------------------------------------------------------------------------


async def test_cannot_delete_published_plan(test_context):
    from app.schemas.plan import PlanCreate
    from app.services.plan import PlanService
    from app.port.exceptions import BusinessException

    factory, prefix = test_context
    product_type = f"{prefix}_del"

    plan = await PlanService().create_plan(product_type, PlanCreate(name="No Delete"))
    await PlanService().publish_plan(plan.id)

    with pytest.raises(BusinessException, match="仅草稿"):
        await PlanService().delete_plan(plan.id)


# ---------------------------------------------------------------------------
# 6. Duplicate name within same product_type is rejected
# ---------------------------------------------------------------------------


async def test_duplicate_name_rejected(test_context):
    from app.schemas.plan import PlanCreate
    from app.services.plan import PlanService
    from app.port.exceptions import BusinessException

    factory, prefix = test_context
    pt = f"{prefix}_dup"

    await PlanService().create_plan(pt, PlanCreate(name="Same Name"))

    with pytest.raises(BusinessException, match="已存在"):
        await PlanService().create_plan(pt, PlanCreate(name="Same Name"))


# ---------------------------------------------------------------------------
# 7. User endpoint: list_published_plans only returns published
# ---------------------------------------------------------------------------


async def test_list_published_plans_only_published(test_context):
    from app.schemas.plan import PlanCreate
    from app.services.plan import PlanService

    factory, prefix = test_context
    pt = f"{prefix}_pub"

    # Create two: one draft, one published
    d = await PlanService().create_plan(pt, PlanCreate(name="Draft Only"))
    p = await PlanService().create_plan(pt, PlanCreate(name="Published One"))
    await PlanService().publish_plan(p.id)

    results = await PlanService().list_published_plans(pt)
    result_ids = [r.id for r in results]
    assert p.id in result_ids
    assert d.id not in result_ids


# ---------------------------------------------------------------------------
# 8. Validation: apply_start must be before apply_end
# ---------------------------------------------------------------------------


async def test_apply_start_after_apply_end_rejected(test_context):
    from datetime import datetime, timezone
    from app.schemas.plan import PlanCreate
    from app.services.plan import PlanService
    from app.port.exceptions import ValidationException

    factory, prefix = test_context
    pt = f"{prefix}_val"

    with pytest.raises(ValidationException, match="开始时间必须早于"):
        await PlanService().create_plan(
            pt,
            PlanCreate(
                name="Bad Dates",
                apply_start=datetime(2026, 12, 31, tzinfo=timezone.utc),
                apply_end=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
        )


# ---------------------------------------------------------------------------
# 9. Delete draft works
# ---------------------------------------------------------------------------


async def test_delete_draft_plan(test_context):
    from app.schemas.plan import PlanCreate
    from app.services.plan import PlanService

    factory, prefix = test_context
    pt = f"{prefix}_dd"

    plan = await PlanService().create_plan(pt, PlanCreate(name="Delete Me"))
    plan_id = plan.id
    await PlanService().delete_plan(plan_id)

    with pytest.raises(Exception):
        await PlanService().get_plan(plan_id)


# ---------------------------------------------------------------------------
# 10. Get plan detail
# ---------------------------------------------------------------------------


async def test_get_plan_detail(test_context):
    from app.schemas.plan import PlanCreate
    from app.services.plan import PlanService

    factory, prefix = test_context
    pt = f"{prefix}_detail"

    plan = await PlanService().create_plan(pt, PlanCreate(name="Detail Test", capacity=100))
    await PlanService().publish_plan(plan.id)

    detail = await PlanService().get_plan(plan.id)
    assert detail.id == plan.id
    assert detail.name == "Detail Test"
    assert detail.capacity == 100
    assert detail.status == "published"
    assert detail.enrolled == 0
