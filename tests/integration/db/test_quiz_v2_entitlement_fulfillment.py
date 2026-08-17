"""Course settlement grants and revokes quiz library entitlements."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.certification.src.index import Course, CourseEnrollment
from app.domain.community.src.index import (
    QuizCourseLibraryBinding,
    QuizLibrary,
    QuizLibraryEntitlement,
)
from app.domain.order.src.index import Order
from app.domain.plan.src.index import Plan  # noqa: F401 - resolve Order.plan_id FK
from app.domain.user.src.index import User
from app.services.order_fulfillment import OrderFulfillmentService


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


async def test_course_settlement_entitlement_is_idempotent_and_refund_scoped() -> None:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(url, pool_size=2, max_overflow=1)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"qful_{uuid4().hex[:10]}"
    async with factory() as db:
        user = User(openid=f"{prefix}_user")
        course = Course(
            title=f"{prefix}课程",
            category="test",
            price=100,
            status="published",
            is_active=True,
        )
        db.add_all([user, course])
        await db.flush()
        order = Order(
            user_id=user.id,
            order_kind="course",
            product_type="course",
            price=100,
            status="paid",
            paid_at=datetime.now(timezone.utc),
        )
        db.add(order)
        await db.flush()
        enrollment = CourseEnrollment(
            user_id=user.id,
            course_id=course.id,
            order_id=order.id,
            status="pending_payment",
            learning_access=False,
        )
        library = QuizLibrary(
            name=f"{prefix}题库",
            normalized_name=f"{prefix}题库",
            description="test",
            cover_url="https://example.invalid/cover.png",
            access_mode="course_entitlement",
            system_kind="none",
            migration_state="ready",
            status="draft",
            v2_enabled=False,
            created_by=None,
            updated_by=None,
        )
        db.add_all([enrollment, library])
        await db.flush()
        binding = QuizCourseLibraryBinding(
            course_id=course.id,
            library_id=library.id,
            status="active",
            lock_version=1,
        )
        db.add(binding)
        await db.commit()

        service = OrderFulfillmentService()
        assert await service.on_paid(db, order) is True
        await db.commit()
        entitlement = (
            await db.execute(
                select(QuizLibraryEntitlement).where(
                    QuizLibraryEntitlement.order_id == order.id,
                    QuizLibraryEntitlement.library_id == library.id,
                )
            )
        ).scalar_one()
        assert entitlement.status == "active"
        assert entitlement.snapshot["library_code"] == library.library_code

        assert await service.on_paid(db, order) is False
        await db.commit()
        assert len(
            list(
                (
                    await db.execute(
                        select(QuizLibraryEntitlement).where(
                            QuizLibraryEntitlement.order_id == order.id
                        )
                    )
                ).scalars()
            )
        ) == 1

        order.status = "refunded"
        assert await service.on_refunded(db, order) is True
        await db.commit()
        await db.refresh(entitlement)
        assert entitlement.status == "revoked"
        assert entitlement.revoked_at is not None

        await db.execute(
            delete(QuizLibraryEntitlement).where(
                QuizLibraryEntitlement.order_id == order.id
            )
        )
        await db.execute(
            delete(QuizCourseLibraryBinding).where(
                QuizCourseLibraryBinding.id == binding.id
            )
        )
        await db.execute(delete(QuizLibrary).where(QuizLibrary.id == library.id))
        await db.execute(
            delete(CourseEnrollment).where(CourseEnrollment.id == enrollment.id)
        )
        await db.execute(delete(Order).where(Order.id == order.id))
        await db.execute(delete(Course).where(Course.id == course.id))
        await db.execute(delete(User).where(User.id == user.id))
        await db.commit()
    await engine.dispose()
