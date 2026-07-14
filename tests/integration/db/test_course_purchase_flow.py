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
async def course_context(monkeypatch, tmp_path):
    from sqlalchemy import delete, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    database_url = _require_test_db_url()
    engine = create_async_engine(database_url, pool_size=5, max_overflow=5)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"course_{uuid4().hex[:12]}"

    import app.services.admin_course as admin_course_module
    import app.services.course as course_module
    import app.services.course_asset as course_asset_module
    import app.services.course_purchase as purchase_module
    import app.services.order_timeout as timeout_module
    import app.services.payment as payment_module

    @asynccontextmanager
    async def test_db_ctx():
        async with factory() as session:
            yield session

    for module in (
        admin_course_module,
        course_module,
        course_asset_module,
        purchase_module,
        timeout_module,
        payment_module,
    ):
        monkeypatch.setattr(module, "get_db_ctx", test_db_ctx)
    monkeypatch.setattr(
        course_asset_module,
        "PRIVATE_COURSE_ASSET_ROOT",
        tmp_path / "private" / "course-assets",
    )

    context = SimpleNamespace(
        factory=factory,
        prefix=prefix,
        purchase_service=purchase_module.CoursePurchaseService(),
        payment_service=payment_module.PaymentService(),
        timeout_service=timeout_module.OrderTimeoutCloseService(),
        course_service=course_module.CourseService(),
        asset_service=course_asset_module.CourseAssetService(),
        admin_service=admin_course_module.AdminCourseService(),
        asset_storage=course_asset_module.CourseAssetStorage,
    )
    try:
        yield context
    finally:
        from app.domain.certification.src.index import Course, CourseAsset, CourseEnrollment
        from app.domain.order.src.index import Order
        from app.domain.user.src.index import User

        async with factory() as db:
            course_ids = select(Course.id).where(Course.title.like(f"{prefix}%"))
            user_ids = select(User.id).where(User.openid.like(f"{prefix}%"))
            await db.execute(delete(CourseAsset).where(CourseAsset.course_id.in_(course_ids)))
            await db.execute(
                delete(CourseEnrollment).where(CourseEnrollment.course_id.in_(course_ids))
            )
            await db.execute(
                delete(Order).where(
                    Order.user_id.in_(user_ids),
                    Order.order_kind == "course",
                )
            )
            await db.execute(delete(Course).where(Course.id.in_(course_ids)))
            await db.execute(delete(User).where(User.id.in_(user_ids)))
            await db.commit()
        await engine.dispose()


async def _seed_courses(context):
    from app.domain.certification.src.index import Course, CourseAsset
    from app.domain.user.src.index import User

    async with context.factory() as db:
        paid_user = User(openid=f"{context.prefix}_paid")
        other_user = User(openid=f"{context.prefix}_other")
        db.add_all([paid_user, other_user])
        await db.flush()

        free_course = Course(
            title=f"{context.prefix}_free",
            category="integration",
            price=0,
        )
        paid_course = Course(
            title=f"{context.prefix}_paid",
            category="integration",
            price=12345,
        )
        timeout_course = Course(
            title=f"{context.prefix}_timeout",
            category="integration",
            price=6789,
        )
        db.add_all([free_course, paid_course, timeout_course])
        await db.flush()

        preview = CourseAsset(
            course_id=paid_course.id,
            title="Preview",
            storage_key=f"{paid_course.id}/preview.mp4",
            asset_type="video",
            sort_order=0,
            is_preview=True,
        )
        private = CourseAsset(
            course_id=paid_course.id,
            title="Private",
            storage_key=f"{paid_course.id}/private.mp4",
            asset_type="video",
            sort_order=1,
            is_preview=False,
        )
        db.add_all([preview, private])
        await db.flush()
        result = SimpleNamespace(
            paid_user_id=paid_user.id,
            other_user_id=other_user.id,
            free_course_id=free_course.id,
            paid_course_id=paid_course.id,
            timeout_course_id=timeout_course.id,
            preview_asset_id=preview.id,
            private_asset_id=private.id,
        )
        await db.commit()

    for storage_key in (preview.storage_key, private.storage_key):
        path = context.asset_storage.resolve(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"private-course-content")
    return result


async def test_free_and_concurrent_paid_purchase_use_backend_price(course_context):
    from sqlalchemy import func, select

    from app.domain.certification.src.index import CourseEnrollment
    from app.domain.order.src.index import Order
    from app.port.exceptions import BusinessException
    from app.schemas.course import CourseEnrollRequest

    data = await _seed_courses(course_context)

    free = await course_context.purchase_service.purchase(
        data.paid_user_id,
        data.free_course_id,
    )
    assert free.payment_required is False
    assert free.order_id is None
    assert free.status == "enrolled"
    assert free.learning_access is True

    with pytest.raises(BusinessException, match="付费课程请使用课程购买接口"):
        await course_context.course_service.enroll(
            data.paid_user_id,
            CourseEnrollRequest(course_id=data.paid_course_id),
        )

    first, second = await asyncio.gather(
        course_context.purchase_service.purchase(
            data.paid_user_id,
            data.paid_course_id,
        ),
        course_context.purchase_service.purchase(
            data.paid_user_id,
            data.paid_course_id,
        ),
    )
    assert first.enrollment_id == second.enrollment_id
    assert first.order_id == second.order_id
    assert first.payment_required is True
    assert first.learning_access is False

    async with course_context.factory() as db:
        order = await db.get(Order, first.order_id)
        active_count = await db.scalar(
            select(func.count())
            .select_from(CourseEnrollment)
            .where(
                CourseEnrollment.user_id == data.paid_user_id,
                CourseEnrollment.course_id == data.paid_course_id,
                CourseEnrollment.status.in_(("pending_payment", "enrolled", "completed")),
            )
        )
    assert order.price == 12345
    assert active_count == 1


async def test_payment_refund_and_private_content_authorization(
    course_context,
    monkeypatch,
):
    from sqlalchemy import select

    from app.domain.certification.src.index import Course, CourseEnrollment
    from app.domain.order.src.index import Order
    from app.port.exceptions import ForbiddenException, NotFoundException
    from app.schemas.payment import PaymentCallbackRequest

    data = await _seed_courses(course_context)
    purchase = await course_context.purchase_service.purchase(
        data.paid_user_id,
        data.paid_course_id,
    )
    callback = PaymentCallbackRequest(
        out_trade_no=(
            await _get_order(course_context, purchase.order_id)
        ).out_trade_no,
        transaction_id=f"{course_context.prefix}_transaction",
        trade_state="SUCCESS",
        total_fee=12345,
    )

    original_on_paid = course_context.payment_service.fulfillment.on_paid

    async def fail_fulfillment(db, order):
        raise RuntimeError("simulated fulfillment failure")

    monkeypatch.setattr(
        course_context.payment_service.fulfillment,
        "on_paid",
        fail_fulfillment,
    )
    with pytest.raises(RuntimeError, match="simulated fulfillment failure"):
        await course_context.payment_service.handle_callback(callback)

    async with course_context.factory() as db:
        order = await db.get(Order, purchase.order_id)
        enrollment = await db.get(CourseEnrollment, purchase.enrollment_id)
        assert order.status == "pending"
        assert order.transaction_id is None
        assert enrollment.status == "pending_payment"
        assert enrollment.learning_access is False

    monkeypatch.setattr(
        course_context.payment_service.fulfillment,
        "on_paid",
        original_on_paid,
    )
    paid = await course_context.payment_service.handle_callback(callback)
    assert paid.status == "completed"
    assert paid.processed is True

    async with course_context.factory() as db:
        course = await db.get(Course, data.paid_course_id)
        course.is_active = False
        await db.commit()

    content = await course_context.course_service.get_content(
        data.paid_user_id,
        data.paid_course_id,
    )
    assert content.learning_access is True
    assert [asset.id for asset in content.assets] == [
        data.preview_asset_id,
        data.private_asset_id,
    ]
    with pytest.raises(NotFoundException):
        await course_context.course_service.get_content(
            data.other_user_id,
            data.paid_course_id,
        )

    revoked = await course_context.admin_service.revoke_enrollment(
        purchase.enrollment_id
    )
    assert revoked.status == "cancelled"
    assert revoked.learning_access is False
    duplicate_payment = await course_context.payment_service.handle_callback(callback)
    assert duplicate_payment.processed is False
    async with course_context.factory() as db:
        enrollment = await db.get(CourseEnrollment, purchase.enrollment_id)
        assert enrollment.status == "cancelled"
        assert enrollment.learning_access is False

    async with course_context.factory() as db:
        course = await db.get(Course, data.paid_course_id)
        course.is_active = True
        await db.commit()

    refund = await course_context.payment_service.handle_callback(
        PaymentCallbackRequest(
            out_trade_no=callback.out_trade_no,
            trade_state="REFUND",
        )
    )
    assert refund.status == "refunded"

    async with course_context.factory() as db:
        enrollment = await db.get(CourseEnrollment, purchase.enrollment_id)
        assert enrollment.status == "refunded"
        assert enrollment.learning_access is False
        assert enrollment.access_revoked_at is not None

    preview_file = await course_context.asset_service.get_content(
        data.other_user_id,
        data.preview_asset_id,
    )
    assert preview_file.path.is_file()
    with pytest.raises(ForbiddenException):
        await course_context.asset_service.get_content(
            data.paid_user_id,
            data.private_asset_id,
        )

    preview_content = await course_context.course_service.get_content(
        data.other_user_id,
        data.paid_course_id,
    )
    assert preview_content.learning_access is False
    assert [asset.id for asset in preview_content.assets] == [data.preview_asset_id]


async def test_expired_course_order_cancels_pending_enrollment(course_context):
    from app.domain.certification.src.index import CourseEnrollment
    from app.domain.order.src.index import Order

    data = await _seed_courses(course_context)
    purchase = await course_context.purchase_service.purchase(
        data.paid_user_id,
        data.timeout_course_id,
    )
    async with course_context.factory() as db:
        order = await db.get(Order, purchase.order_id)
        order.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()

    result = await course_context.timeout_service.close_expired_pending_orders(limit=10)
    assert purchase.order_id in result.order_ids

    async with course_context.factory() as db:
        order = await db.get(Order, purchase.order_id)
        enrollment = await db.get(CourseEnrollment, purchase.enrollment_id)
        assert order.status == "closed"
        assert enrollment.status == "cancelled"
        assert enrollment.learning_access is False


async def _get_order(context, order_id):
    from app.domain.order.src.index import Order

    async with context.factory() as db:
        return await db.get(Order, order_id)
