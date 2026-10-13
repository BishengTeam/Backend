import asyncio
import os
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def session_factory():
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_async_engine(url, pool_size=5, max_overflow=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def app_context(monkeypatch, session_factory):
    import app.services.points as points_module
    import app.services.points_virtual as virtual_module

    @asynccontextmanager
    async def db_ctx():
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(points_module, "get_db_ctx", db_ctx)
    monkeypatch.setattr(virtual_module, "get_db_ctx", db_ctx)
    return SimpleNamespace(db_ctx=db_ctx)


@pytest.fixture
async def test_prefix(session_factory):
    prefix = "pv" + uuid4().hex[:16]
    try:
        yield prefix
    finally:
        from sqlalchemy import delete

        from app.domain.certification.src.index import Course, CourseEnrollment
        from app.domain.community.src.index import (
            QuizCheckin,
            QuizLibrary,
            QuizLibraryEntitlement,
            QuizUserStats,
        )
        from app.domain.points_mall.src import (
            PointsVirtualOrder,
            PointsVirtualProduct,
            PointsVirtualRefund,
        )
        from app.domain.user.src.index import (
            AdminUser,
            PointsHistory,
            User,
            UserPoints,
        )

        async with session_factory() as db:
            users = select(User.id).where(User.openid.like(f"{prefix}%"))
            admins = select(AdminUser.id).where(AdminUser.username.like(f"{prefix}%"))
            courses = select(Course.id).where(Course.title.like(f"{prefix}%"))
            libraries = select(QuizLibrary.id).where(
                QuizLibrary.name.like(f"{prefix}%")
            )
            products = select(PointsVirtualProduct.id).where(
                or_(
                    PointsVirtualProduct.course_id.in_(courses),
                    PointsVirtualProduct.library_id.in_(libraries),
                )
            )
            orders = select(PointsVirtualOrder.id).where(
                PointsVirtualOrder.user_id.in_(users)
            )
            await db.execute(
                delete(PointsVirtualRefund).where(
                    PointsVirtualRefund.order_id.in_(orders)
                )
            )
            await db.execute(
                delete(CourseEnrollment).where(CourseEnrollment.user_id.in_(users))
            )
            await db.execute(
                delete(QuizLibraryEntitlement).where(
                    QuizLibraryEntitlement.user_id.in_(users)
                )
            )
            await db.execute(
                delete(PointsVirtualOrder).where(PointsVirtualOrder.user_id.in_(users))
            )
            await db.execute(
                delete(PointsVirtualProduct).where(PointsVirtualProduct.id.in_(products))
            )
            await db.execute(
                delete(PointsHistory).where(PointsHistory.user_id.in_(users))
            )
            await db.execute(
                delete(UserPoints).where(UserPoints.user_id.in_(users))
            )
            await db.execute(
                delete(QuizCheckin).where(QuizCheckin.user_id.in_(users))
            )
            await db.execute(
                delete(QuizUserStats).where(QuizUserStats.user_id.in_(users))
            )
            await db.execute(delete(Course).where(Course.id.in_(courses)))
            await db.execute(delete(QuizLibrary).where(QuizLibrary.id.in_(libraries)))
            await db.execute(delete(AdminUser).where(AdminUser.id.in_(admins)))
            await db.execute(delete(User).where(User.id.in_(users)))
            await db.commit()


async def seed_user(session_factory, prefix: str, index: int = 0) -> int:
    from app.domain.user.src.index import User

    async with session_factory() as db:
        user = User(openid=f"{prefix}-user-{index}", phone=f"139{index:08d}")
        db.add(user)
        await db.commit()
        return user.id


async def seed_admin(session_factory, prefix: str) -> int:
    from app.domain.user.src.index import AdminUser

    async with session_factory() as db:
        admin = AdminUser(
            username=f"{prefix}-admin",
            password_hash="test-only",
            role="cert_admin",
            display_name=f"{prefix}-admin",
            must_change_password=False,
        )
        db.add(admin)
        await db.commit()
        return admin.id


async def seed_course(session_factory, prefix: str, index: int = 0) -> int:
    from app.domain.certification.src.index import Course

    async with session_factory() as db:
        course = Course(
            title=f"{prefix}-course-{index}",
            category="测试",
            cover_storage_key=f"test/{prefix}/course-{index}.jpg",
            price=9900,
            status="published",
            is_active=True,
        )
        db.add(course)
        await db.commit()
        return course.id


async def seed_quiz_library(session_factory, prefix: str, index: int = 0) -> int:
    from app.domain.community.src.index import QuizLibrary

    async with session_factory() as db:
        library = QuizLibrary(
            name=f"{prefix}-library-{index}",
            normalized_name=f"{prefix}-library-{index}",
            access_mode="paid",
            migration_state="ready",
            status="published",
            published_at=datetime.now(timezone.utc),
            v2_enabled=True,
            price_cents=6900,
        )
        db.add(library)
        await db.commit()
        return library.id


async def test_course_points_redemption_refund_and_redeem(
    session_factory, app_context, test_prefix
):
    from app.domain.certification.src.index import CourseEnrollment
    from app.domain.community.src.index import QuizLibraryEntitlement
    from app.domain.points_mall.src import PointsVirtualProduct
    from app.domain.points_mall.src import PointsVirtualOrder, PointsVirtualProduct
    from app.domain.user.src.index import PointsHistory, UserPoints
    from app.schemas.points_virtual import (
        PointsVirtualProductCreate,
        PointsVirtualRedeemRequest,
        PointsVirtualRefundRequest,
    )
    from app.services.points import PointsService
    from app.services.points_virtual import PointsVirtualService

    prefix = test_prefix
    user_id = await seed_user(session_factory, prefix)
    admin_id = await seed_admin(session_factory, prefix)
    course_id = await seed_course(session_factory, prefix)
    service = PointsVirtualService()

    product = await service.admin_create_product(
        PointsVirtualProductCreate(
            product_type="course",
            course_id=course_id,
            name="积分课程",
            points_price=30,
            stock_limit=1,
            entitlement_days=30,
            status="published",
        )
    )
    await PointsService().grant_points(
        user_id,
        amount=30,
        action_type="test_seed",
        source_type="test",
        source_id=f"{prefix}-seed",
    )

    request = PointsVirtualRedeemRequest(
        product_id=product.id,
        product_version=product.version,
        idempotency_key=f"{prefix}-idem",
    )
    first = await service.redeem(user_id, request)
    second = await service.redeem(user_id, request)
    assert second.id == first.id
    assert second.idempotent is True
    assert first.balance == 0

    async with session_factory() as db:
        order_count = await db.scalar(
            select(func.count()).select_from(PointsVirtualOrder)
        )
        enrollment = (
            await db.execute(
                select(CourseEnrollment).where(
                    CourseEnrollment.points_virtual_order_id == first.id
                )
            )
        ).scalar_one()
        history = (
            await db.execute(
                select(PointsHistory).where(PointsHistory.id == first.points_history_id)
            )
        ).scalar_one()
    assert order_count >= 1
    assert enrollment.learning_access is True
    assert enrollment.access_expires_at is not None
    assert history.amount == -30

    refund = await service.refund(
        admin_id=admin_id,
        order_id=first.id,
        data=PointsVirtualRefundRequest(
            reason_code="test_refund",
            reason_detail="integration refund",
        ),
    )
    assert refund.points_returned == 30
    async with session_factory() as db:
        account = await db.scalar(
            select(UserPoints).where(UserPoints.user_id == user_id)
        )
        stored_enrollment = await db.get(CourseEnrollment, enrollment.id)
        stored_product = await db.get(PointsVirtualProduct, product.id)
        active_libraries = (
            await db.execute(
                select(func.count()).select_from(QuizLibraryEntitlement).where(
                    QuizLibraryEntitlement.user_id == user_id,
                    QuizLibraryEntitlement.status == "active",
                )
            )
        ).scalar_one()
    assert account.balance == 30
    assert stored_enrollment.status == "refunded"
    assert stored_enrollment.learning_access is False
    assert stored_product.total_redeemed == 0
    assert active_libraries == 0

    admin_product = await service.admin_get_product(product.id)
    assert admin_product.content_name == f"{prefix}-course-0"
    assert admin_product.refunded_count == 1
    assert admin_product.remaining_stock == 1
    assert admin_product.redeemable is True

    admin_products = await service.admin_list_products(
        keyword="积分课程",
        status="published",
        product_type="course",
        page=1,
        page_size=10,
    )
    assert [row.id for row in admin_products.items] == [product.id]

    admin_orders = await service.admin_list_orders(
        keyword="13900000000",
        product_id=product.id,
        status="refunded",
        page=1,
        page_size=10,
    )
    assert [row.id for row in admin_orders.items] == [first.id]
    assert admin_orders.items[0].user_id == user_id
    assert admin_orders.items[0].user_phone == "139****0000"

    order_detail = await service.admin_get_order(first.id)
    assert order_detail.spend_history is not None
    assert order_detail.spend_history.amount == -30
    assert order_detail.refund is not None
    assert order_detail.refund_history is not None
    assert order_detail.refund_history.amount == 30
    assert order_detail.refunded_by_admin_name == f"{prefix}-admin"

    await PointsService().grant_points(
        user_id,
        amount=30,
        action_type="test_seed",
        source_type="test",
        source_id=f"{prefix}-seed-2",
    )
    again = await service.redeem(
        user_id,
        PointsVirtualRedeemRequest(
            product_id=product.id,
            product_version=product.version,
            idempotency_key=f"{prefix}-idem-2",
        ),
    )
    assert again.id != first.id
    assert again.balance == 30


async def test_quiz_points_redemption_and_last_stock_concurrency(
    session_factory, app_context, test_prefix
):
    from app.domain.community.src.index import QuizLibraryEntitlement
    from app.domain.points_mall.src import PointsVirtualProduct
    from app.schemas.points_virtual import (
        PointsVirtualProductCreate,
        PointsVirtualRedeemRequest,
    )
    from app.services.points import PointsService
    from app.services.points_virtual import PointsVirtualService

    prefix = test_prefix
    library_id = await seed_quiz_library(session_factory, prefix)
    service = PointsVirtualService()
    product = await service.admin_create_product(
        PointsVirtualProductCreate(
            product_type="quiz_library",
            library_id=library_id,
            name="积分题库",
            points_price=10,
            stock_limit=1,
            status="published",
        )
    )
    first_user = await seed_user(session_factory, prefix, 1)
    second_user = await seed_user(session_factory, prefix, 2)
    for index, user_id in enumerate((first_user, second_user), start=1):
        await PointsService().grant_points(
            user_id,
            amount=10,
            action_type="test_seed",
            source_type="test",
            source_id=f"{prefix}-seed-{index}",
        )

    results = await asyncio.gather(
        service.redeem(
            first_user,
            PointsVirtualRedeemRequest(
                product_id=product.id,
                product_version=product.version,
                idempotency_key=f"{prefix}-user-1",
            ),
        ),
        service.redeem(
            second_user,
            PointsVirtualRedeemRequest(
                product_id=product.id,
                product_version=product.version,
                idempotency_key=f"{prefix}-user-2",
            ),
        ),
        return_exceptions=True,
    )
    successes = [row for row in results if not isinstance(row, Exception)]
    failures = [row for row in results if isinstance(row, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert "库存不足" in str(failures[0])

    async with session_factory() as db:
        entitlement_count = (
            await db.execute(
                select(func.count()).select_from(QuizLibraryEntitlement).where(
                    QuizLibraryEntitlement.library_id == library_id
                )
            )
        ).scalar_one()
        stored_product = await db.get(PointsVirtualProduct, product.id)
    assert entitlement_count == 1
    assert stored_product.total_redeemed == 1


async def test_manual_checkin_repairs_missing_points_for_existing_checkin(
    session_factory, app_context, monkeypatch, test_prefix
):
    from app.domain.community.src.index import QuizCheckin
    from app.domain.user.src.index import PointsHistory, UserPoints
    from app.services.quiz_practice import QuizPracticeService
    from app.utils.quiz_helpers import today

    @asynccontextmanager
    async def quiz_db_ctx():
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(
        "app.services.quiz_practice.get_db_ctx",
        quiz_db_ctx,
    )
    prefix = test_prefix
    user_id = await seed_user(session_factory, prefix, index=9)
    local_day = today()
    async with session_factory() as db:
        db.add(
            QuizCheckin(
                user_id=user_id,
                checkin_date=local_day,
                questions_completed=2,
                consecutive_days=1,
                first_attempt_id=None,
            )
        )
        await db.commit()

    response = await QuizPracticeService().manual_checkin(user_id)

    assert response.checked_in is True
    async with session_factory() as db:
        account = await db.scalar(
            select(UserPoints).where(UserPoints.user_id == user_id)
        )
        history = await db.scalar(
            select(PointsHistory).where(PointsHistory.user_id == user_id)
        )
    assert account.balance == 5
    assert history is not None
    assert history.source_id == f"daily_checkin:{user_id}:{local_day.isoformat()}"
