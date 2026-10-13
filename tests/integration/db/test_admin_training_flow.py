"""PostgreSQL integration tests for Admin Training management."""

import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def test_context(monkeypatch):
    database_url = os.environ.get("TEST_DATABASE_URL")
    database_url_sync = os.environ.get("TEST_DATABASE_URL_SYNC")
    if not database_url or not database_url_sync:
        pytest.skip("PostgreSQL integration tests require TEST_DATABASE_URL and TEST_DATABASE_URL_SYNC")

    engine = create_async_engine(database_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"training_{uuid4().hex[:12]}"

    import app.services.admin_training as service_module
    from app.domain.content.src.index import Training

    @asynccontextmanager
    async def test_db_ctx():
        async with factory() as session:
            yield session

    monkeypatch.setattr(service_module, "get_db_ctx", test_db_ctx)
    try:
        yield factory, prefix
    finally:
        async with factory() as db:
            await db.execute(delete(Training).where(Training.title.like(f"{prefix}%")))
            await db.commit()
        await engine.dispose()


async def test_delete_removes_training_from_admin_list(test_context):
    from app.domain.content.src.index import Training
    from app.schemas.admin_training import AdminTrainingCreate
    from app.services.admin_training import AdminTrainingService

    factory, prefix = test_context
    service = AdminTrainingService()
    created = await service.create(
        AdminTrainingCreate(
            title=f"{prefix}-training",
            is_active=True,
        )
    )

    await service.delete(created.id)

    async with factory() as db:
        stored = await db.get(Training, created.id)
    listed = await service.list_trainings(keyword=f"{prefix}-training", page=1, page_size=10)

    assert stored is None
    assert created.id not in [item.id for item in listed.items]
