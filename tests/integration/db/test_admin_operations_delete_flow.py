"""PostgreSQL integration tests for physical deletion in operations modules."""

import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
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
    prefix = f"opsdel_{uuid4().hex[:12]}"

    import app.services.admin_activity as activity_module
    import app.services.admin_job as job_module

    @asynccontextmanager
    async def test_db_ctx():
        async with factory() as session:
            yield session

    monkeypatch.setattr(activity_module, "get_db_ctx", test_db_ctx)
    monkeypatch.setattr(job_module, "get_db_ctx", test_db_ctx)
    try:
        yield factory, prefix
    finally:
        from app.domain.certification.src.index import Job, JobApplication
        from app.domain.content.src.index import (
            Activity,
            ActivityRegistration,
            ActivityReminder,
        )
        from app.domain.user.src.index import User

        async with factory() as db:
            activity_ids = select(Activity.id).where(Activity.title.like(f"{prefix}%"))
            job_ids = select(Job.id).where(Job.title.like(f"{prefix}%"))
            await db.execute(
                delete(ActivityRegistration).where(
                    ActivityRegistration.activity_id.in_(activity_ids)
                )
            )
            await db.execute(
                delete(ActivityReminder).where(
                    ActivityReminder.activity_id.in_(activity_ids)
                )
            )
            await db.execute(delete(Activity).where(Activity.id.in_(activity_ids)))
            await db.execute(
                delete(JobApplication).where(JobApplication.job_id.in_(job_ids))
            )
            await db.execute(delete(Job).where(Job.id.in_(job_ids)))
            await db.execute(delete(User).where(User.openid.like(f"{prefix}%")))
            await db.commit()
        await engine.dispose()


async def seed_user(factory, prefix: str, index: int) -> int:
    from app.domain.user.src.index import User

    async with factory() as db:
        user = User(openid=f"{prefix}-user-{index}")
        db.add(user)
        await db.commit()
        return user.id


async def test_activity_delete_removes_registrations_and_reminders(test_context):
    from app.domain.content.src.index import Activity, ActivityRegistration, ActivityReminder
    from app.schemas.admin_activity import AdminActivityCreate
    from app.services.admin_activity import AdminActivityService

    factory, prefix = test_context
    user_id = await seed_user(factory, prefix, 1)
    service = AdminActivityService()
    activity = await service.create(
        AdminActivityCreate(title=f"{prefix}-activity", max_participants=10)
    )

    async with factory() as db:
        db.add(
            ActivityRegistration(
                activity_id=activity.id,
                user_id=user_id,
                name="测试用户",
                phone="13900000001",
            )
        )
        db.add(ActivityReminder(activity_id=activity.id, user_id=user_id))
        await db.commit()

    await service.delete(activity.id)

    listed = await service.list_activities(
        keyword=f"{prefix}-activity", page=1, page_size=10
    )

    async with factory() as db:
        stored_activity = await db.get(Activity, activity.id)
        registration = (
            await db.execute(
                ActivityRegistration.__table__.select().where(
                    ActivityRegistration.activity_id == activity.id
                )
            )
        ).first()
        reminder = (
            await db.execute(
                ActivityReminder.__table__.select().where(
                    ActivityReminder.activity_id == activity.id
                )
            )
        ).first()

    assert stored_activity is None
    assert activity.id not in [item.id for item in listed.items]
    assert registration is None
    assert reminder is None


async def test_job_delete_removes_applications(test_context):
    from app.domain.certification.src.index import Job, JobApplication
    from app.schemas.admin_job import AdminJobCreate
    from app.services.admin_job import AdminJobService

    factory, prefix = test_context
    user_id = await seed_user(factory, prefix, 2)
    service = AdminJobService()
    job = await service.create(
        AdminJobCreate(title=f"{prefix}-job", company="测试公司")
    )

    async with factory() as db:
        db.add(JobApplication(user_id=user_id, job_id=job.id))
        await db.commit()

    await service.delete(job.id)

    listed = await service.list_jobs(keyword=f"{prefix}-job", page=1, page_size=10)

    async with factory() as db:
        stored_job = await db.get(Job, job.id)
        application = (
            await db.execute(
                JobApplication.__table__.select().where(JobApplication.job_id == job.id)
            )
        ).first()

    assert stored_job is None
    assert job.id not in [item.id for item in listed.items]
    assert application is None
