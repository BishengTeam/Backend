import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def context(monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_async_engine(url, pool_size=3, max_overflow=2)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"crsflow_{uuid4().hex[:12]}"

    db_ctx = asynccontextmanager_factory(factory)

    import app.services.course_entitlement as entitlement_module

    monkeypatch.setattr(entitlement_module, "get_db_ctx", db_ctx)
    yield SimpleNamespace(factory=factory, prefix=prefix)

    from app.domain.certification.src.index import (
        Course,
        CourseEnrollment,
        CourseEntitlementJob,
        CourseEntitlementJobItem,
    )
    from app.domain.community.src.index import (
        QuizCourseLibraryBinding,
        QuizLibrary,
        QuizLibraryEntitlement,
    )
    from app.domain.user.src.index import User

    async with factory() as db:
        course_ids = select(Course.id).where(Course.title.like(f"{prefix}%"))
        library_ids = select(QuizLibrary.id).where(
            QuizLibrary.name.like(f"{prefix}%")
        )
        user_ids = select(User.id).where(User.openid.like(f"{prefix}%"))
        await db.execute(
            delete(CourseEntitlementJobItem).where(
                CourseEntitlementJobItem.job_id.in_(
                    select(CourseEntitlementJob.id).where(
                        CourseEntitlementJob.course_id.in_(course_ids)
                    )
                )
            )
        )
        await db.execute(
            delete(CourseEntitlementJob).where(
                CourseEntitlementJob.course_id.in_(course_ids)
            )
        )
        await db.execute(
            delete(QuizLibraryEntitlement).where(
                QuizLibraryEntitlement.user_id.in_(user_ids)
            )
        )
        await db.execute(
            delete(QuizCourseLibraryBinding).where(
                QuizCourseLibraryBinding.course_id.in_(course_ids)
            )
        )
        await db.execute(
            delete(CourseEnrollment).where(
                CourseEnrollment.course_id.in_(course_ids)
            )
        )
        await db.execute(delete(QuizLibrary).where(QuizLibrary.id.in_(library_ids)))
        await db.execute(delete(Course).where(Course.id.in_(course_ids)))
        await db.execute(delete(User).where(User.id.in_(user_ids)))
        await db.commit()
    await engine.dispose()


def asynccontextmanager_factory(factory):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def wrapper():
        async with factory() as session:
            yield session

    return wrapper


async def test_course_binding_backfill_and_multi_source_revocation(context) -> None:
    from app.domain.certification.src.index import (
        Course,
        CourseEnrollment,
        CourseEntitlementJob,
    )
    from app.domain.community.src.index import (
        QuizCourseLibraryBinding,
        QuizLibrary,
        QuizLibraryEntitlement,
    )
    from app.domain.user.src.index import User
    from app.services.course_entitlement import CourseEntitlementService

    now = datetime.now(timezone.utc)
    async with context.factory() as db:
        user = User(openid=f"{context.prefix}_user")
        course_one = Course(
            title=f"{context.prefix}_course_one",
            category="integration",
            cover_storage_key=f"course/{context.prefix}/one.jpg",
            price=0,
            preview_chapter_count=0,
            status="published",
            is_active=True,
        )
        course_two = Course(
            title=f"{context.prefix}_course_two",
            category="integration",
            cover_storage_key=f"course/{context.prefix}/two.jpg",
            price=0,
            preview_chapter_count=0,
            status="published",
            is_active=True,
        )
        library = QuizLibrary(
            name=f"{context.prefix}_library",
            normalized_name=f"{context.prefix}_library",
            access_mode="course_entitlement",
            system_kind="none",
            migration_state="ready",
            status="published",
            v2_enabled=True,
            published_at=now,
        )
        db.add_all([user, course_one, course_two, library])
        await db.flush()
        enrollment_one = CourseEnrollment(
            user_id=user.id,
            course_id=course_one.id,
            status="enrolled",
            learning_access=True,
            access_granted_at=now,
        )
        enrollment_two = CourseEnrollment(
            user_id=user.id,
            course_id=course_two.id,
            status="enrolled",
            learning_access=True,
            access_granted_at=now,
        )
        db.add_all([enrollment_one, enrollment_two])
        await db.commit()
        ids = SimpleNamespace(
            user_id=user.id,
            course_one=course_one.id,
            course_two=course_two.id,
            library=library.id,
        )

    service = CourseEntitlementService()
    impact = await service.preview_binding(ids.course_one, ids.library)
    assert impact.can_execute is True
    assert impact.active_enrollment_count == 1
    assert impact.candidates_to_backfill == 1

    job_one = await service.create_binding(
        ids.course_one, ids.library, admin_id=None
    )
    assert job_one.total_count == 1
    assert await service.process_one_job() is True
    async with context.factory() as db:
        job = await db.get(CourseEntitlementJob, job_one.id)
        assert job.status == "succeeded"
        entitlement = (
            await db.execute(
                select(QuizLibraryEntitlement).where(
                    QuizLibraryEntitlement.user_id == ids.user_id,
                    QuizLibraryEntitlement.library_id == ids.library,
                )
            )
        ).scalar_one()
        assert entitlement.source_type == "course_enrollment"
        assert entitlement.status == "active"

    job_two = await service.create_binding(
        ids.course_two, ids.library, admin_id=None
    )
    assert await service.process_one_job() is True
    async with context.factory() as db:
        active_count = (
            await db.execute(
                select(QuizLibraryEntitlement).where(
                    QuizLibraryEntitlement.user_id == ids.user_id,
                    QuizLibraryEntitlement.library_id == ids.library,
                    QuizLibraryEntitlement.status == "active",
                )
            )
        ).scalars().all()
        assert len(active_count) == 2
        binding_one = (
            await db.execute(
                select(QuizCourseLibraryBinding).where(
                    QuizCourseLibraryBinding.course_id == ids.course_one
                )
            )
        ).scalar_one()
        binding_two = (
            await db.execute(
                select(QuizCourseLibraryBinding).where(
                    QuizCourseLibraryBinding.course_id == ids.course_two
                )
            )
        ).scalar_one()

    revoke_one = await service.set_binding_status(
        int(binding_one.id), "inactive", admin_id=None
    )
    assert await service.process_one_job() is True
    async with context.factory() as db:
        still_active = (
            await db.execute(
                select(QuizLibraryEntitlement).where(
                    QuizLibraryEntitlement.user_id == ids.user_id,
                    QuizLibraryEntitlement.library_id == ids.library,
                    QuizLibraryEntitlement.status == "active",
                )
            )
        ).scalars().all()
        assert len(still_active) == 1
        assert still_active[0].course_id == ids.course_two

    await service.set_binding_status(
        int(binding_two.id), "inactive", admin_id=None
    )
    assert await service.process_one_job() is True
    async with context.factory() as db:
        still_active = (
            await db.execute(
                select(QuizLibraryEntitlement).where(
                    QuizLibraryEntitlement.user_id == ids.user_id,
                    QuizLibraryEntitlement.library_id == ids.library,
                    QuizLibraryEntitlement.status == "active",
                )
            )
        ).scalars().all()
        assert still_active == []
