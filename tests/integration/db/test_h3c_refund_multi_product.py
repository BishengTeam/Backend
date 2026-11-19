import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.h3c.src.index import (
    H3cBatchProduct,
    H3cExamBatch,
    H3cRefundRequest,
    H3cRegistration,
)
from app.api.admin.exam_schedule import ScheduleCreate, create_schedule
from app.api.exam_booking import get_slots, my_bookings
from app.domain.exam.src.index import (
    ExamBooking,
    ExamSchedule,
    ExamScheduleSubject,
    ExamSeatQuota,
    ExamTimeSlot,
)
from app.domain.order.src.index import Order
from app.domain.plan.src.index import Plan
from app.domain.user.src.index import User
from app.models.cert_product import CertProduct
from app.port.exceptions import BusinessException
from app.services.admin_cert_product import AdminCertProductService
from app.services.certification_journey import CertificationJourneyService, user_journey
from app.services.consultation_group import ConsultationGroupService
from app.services.h3c_admin import H3cAdminBatchService
from app.services.h3c_refund import H3cRefundService
from app.services.exam_booking import ExamBookingService


SUPER_ADMIN = SimpleNamespace(role="super_admin", permissions=None)

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def context(monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"h3crf_{uuid4().hex[:10]}"

    @asynccontextmanager
    async def db_ctx():
        async with factory() as session:
            yield session

    monkeypatch.setattr(
        "app.services.h3c_refund.get_db_ctx",
        db_ctx,
    )
    monkeypatch.setattr(
        "app.services.certification_journey.get_db_ctx",
        db_ctx,
    )
    monkeypatch.setattr(
        "app.services.admin_cert_product.get_db_ctx",
        db_ctx,
    )
    context = SimpleNamespace(factory=factory, prefix=prefix)
    yield context

    async with factory() as db, db.begin():
        product_codes = select(CertProduct.id).where(
            CertProduct.code.like(f"{prefix}%")
        )
        plan_ids = select(Plan.id).where(Plan.name.like(f"{prefix}%"))
        batch_ids = select(H3cExamBatch.id).where(
            H3cExamBatch.plan_id.in_(plan_ids)
        )
        order_ids = select(Order.id).where(Order.out_trade_no.like(f"{prefix}%"))
        registration_ids = select(H3cRegistration.id).where(
            H3cRegistration.registration_no.like(f"{prefix}%")
        )
        schedule_ids = select(ExamSchedule.id).where(
            ExamSchedule.name.like(f"{prefix}%")
        )
        await db.execute(delete(ExamBooking).where(
            ExamBooking.registration_id.in_(registration_ids)
        ))
        await db.execute(delete(ExamSeatQuota).where(
            ExamSeatQuota.schedule_id.in_(schedule_ids)
        ))
        await db.execute(delete(ExamScheduleSubject).where(
            ExamScheduleSubject.schedule_id.in_(schedule_ids)
        ))
        await db.execute(delete(ExamTimeSlot).where(
            ExamTimeSlot.schedule_id.in_(schedule_ids)
        ))
        await db.execute(delete(ExamSchedule).where(
            ExamSchedule.id.in_(schedule_ids)
        ))
        await db.execute(delete(H3cRefundRequest).where(
            H3cRefundRequest.out_refund_no.like(f"{prefix}%")
        ))
        await db.execute(delete(H3cRegistration).where(
            H3cRegistration.registration_no.like(f"{prefix}%")
        ))
        await db.execute(delete(H3cBatchProduct).where(
            H3cBatchProduct.product_id.in_(product_codes)
        ))
        await db.execute(delete(Order).where(Order.id.in_(order_ids)))
        await db.execute(delete(H3cExamBatch).where(H3cExamBatch.id.in_(batch_ids)))
        await db.execute(delete(Plan).where(Plan.id.in_(plan_ids)))
        await db.execute(delete(CertProduct).where(CertProduct.id.in_(product_codes)))
        await db.execute(delete(User).where(User.openid.like(f"{prefix}%")))

    await engine.dispose()


async def test_multi_product_refund_list_resolves_selected_subject(context):
    prefix = context.prefix
    now = datetime.now(timezone.utc)
    stats_before = await AdminCertProductService().get_stats()
    h3c_stats_before = next(
        item for item in stats_before if item.type == "h3c"
    )

    seed = SimpleNamespace()
    async with context.factory() as db, db.begin():
        user = User(openid=f"{prefix}_user")
        product = CertProduct(
            type="h3c",
            code=f"{prefix}_NE",
            name=f"{prefix} H3CNE",
            chinese_name="H3CNE 认证网络工程师",
            is_active=True,
            coupon_price_cents=30000,
            student_price_cents=20000,
            full_price_cents=50000,
        )
        second_product = CertProduct(
            type="h3c",
            code=f"{prefix}_SE",
            name=f"{prefix} H3CSE",
            chinese_name="H3CSE 认证路由交换高级工程师",
            is_active=True,
            coupon_price_cents=50000,
            student_price_cents=40000,
            full_price_cents=80000,
        )
        plan = Plan(
            product_type=None,
            name=f"{prefix} multi batch",
            status="published",
            apply_start=now - timedelta(days=1),
            apply_end=now + timedelta(days=1),
            capacity=10,
            price_cents=0,
        )
        db.add_all((user, product, second_product, plan))
        await db.flush()

        batch = H3cExamBatch(
            plan_id=plan.id,
            exam_code=f"{prefix}_EXAM",
            identity_tag="INTERNAL",
            coupon_price_cents=0,
            student_price_cents=0,
            full_price_cents=0,
        )
        db.add(batch)
        await db.flush()
        first_batch_product = H3cBatchProduct(
            batch_id=batch.id,
            product_id=product.id,
        )
        second_batch_product = H3cBatchProduct(
            batch_id=batch.id,
            product_id=second_product.id,
        )
        db.add_all((first_batch_product, second_batch_product))
        await db.flush()
        order = Order(
            user_id=user.id,
            order_kind="certification",
            product_type=product.code,
            plan_id=plan.id,
            candidate_name="多产品退款",
            candidate_idcard="510101200001010199",
            price=50000,
            status="paid",
            payment_channel="bank_transfer",
            out_trade_no=f"{prefix}_order",
        )
        db.add(order)
        await db.flush()
        second_order = Order(
            user_id=user.id,
            order_kind="certification",
            product_type=second_product.code,
            plan_id=plan.id,
            candidate_name="多产品退款第二科目",
            candidate_idcard="510101200001010199",
            price=80000,
            status="pending",
            payment_channel="unknown",
            out_trade_no=f"{prefix}_order_2",
        )
        db.add(second_order)
        await db.flush()
        assert second_order.id != order.id
        registration = H3cRegistration(
            batch_id=batch.id,
            batch_product_id=first_batch_product.id,
            plan_id=plan.id,
            user_id=user.id,
            order_id=order.id,
            registration_no=f"{prefix}_reg",
            registration_type="full",
            status="pending_refund_confirmation",
            candidate_snapshot={"candidate_name": "多产品退款"},
            candidate_idcard="510101200001010199",
        )
        db.add(registration)
        await db.flush()
        second_registration = H3cRegistration(
            batch_id=batch.id,
            batch_product_id=second_batch_product.id,
            plan_id=plan.id,
            user_id=user.id,
            order_id=second_order.id,
            registration_no=f"{prefix}_reg_2",
            registration_type="full",
            status="pending_refund_confirmation",
            candidate_snapshot={"candidate_name": "多产品退款第二科目"},
            candidate_idcard="510101200001010198",
        )
        db.add(second_registration)
        await db.flush()
        db.add(H3cRefundRequest(
            registration_id=registration.id,
            order_id=order.id,
            user_id=user.id,
            request_kind="review_failed",
            reason_code="candidate_request",
            amount_cents=50000,
            status="requested",
            requested_at=now,
            out_refund_no=f"{prefix}_refund",
        ))
        seed.plan_id = plan.id
        seed.registration_id = registration.id
        seed.second_registration_id = second_registration.id
        seed.user_id = user.id

    async with context.factory() as db:
        await CertificationJourneyService._check_plan(db, "h3c", seed.plan_id)
        permission = await ConsultationGroupService.required_permission(
            db, seed.plan_id
        )
    assert permission == "h3c:batch_manage"

    journey = await user_journey(
        "h3c", seed.registration_id, seed.user_id
    )
    assert journey["subject"] == "H3CNE 认证网络工程师"

    admin_batches = await H3cAdminBatchService().list_batches(
        certification_code=second_product.code
    )
    assert [batch.id for batch in admin_batches.items] == [batch.id]
    assert admin_batches.items[0].certification_code == (
        f"{product.code}, {second_product.code}"
    )
    assert set(admin_batches.items[0].product_codes) == {
        product.code,
        second_product.code,
    }

    result = await H3cRefundService().list_refunds(
        keyword=prefix,
        page=1,
        page_size=20,
    )
    item = next(
        row for row in result.items if row.out_refund_no == f"{prefix}_refund"
    )

    assert item.product_label == "H3CNE 认证网络工程师"

    stats_after = await AdminCertProductService().get_stats()
    h3c_stats_after = next(item for item in stats_after if item.type == "h3c")
    assert h3c_stats_after.active_batch_count == (
        h3c_stats_before.active_batch_count + 1
    )

    async with context.factory() as db:
        registration = await db.get(H3cRegistration, seed.registration_id)
        second_registration = await db.get(
            H3cRegistration, seed.second_registration_id
        )
        assert registration is not None
        assert second_registration is not None
        registration.status = "approved"
        second_registration.status = "approved"
        await db.commit()

        days_until_friday = (4 - now.weekday()) % 7 or 7
        exam_day = now.date() + timedelta(days=days_until_friday + 7)
        schedule_result = await create_schedule(
            data=ScheduleCreate(
                name=f"{prefix} booking schedule",
                open_date_start=exam_day - timedelta(days=7),
                open_date_end=exam_day + timedelta(days=7),
                allowed_weekdays=[4],
                exam_location="成都市高新区天府软件园B区3楼",
                product_ids=[product.id, second_product.id],
                time_slots=[
                    {"start_time": "09:30"},
                    {"start_time": "14:30"},
                ],
                default_quota=5,
                shared_quotas=[
                    {"slot_start": "09:30", "total_seats": 1},
                    {"slot_start": "14:30", "total_seats": 5},
                ],
                publish=True,
            ),
            _admin=SUPER_ADMIN,
            db=db,
        )
        schedule_id = schedule_result.data["id"]
        async with context.factory() as check_db:
            schedule = await check_db.get(ExamSchedule, schedule_id)
            assert schedule is not None
            assert schedule.exam_location == "成都市高新区天府软件园B区3楼"

        slot_response = await get_slots(
            schedule_id=schedule_id,
            exam_date=exam_day,
            registration_id=registration.id,
            current_user=SimpleNamespace(id=seed.user_id),
            db=db,
        )
        assert [slot["start_time"][-8:-3] for slot in slot_response.data["slots"]] == [
            "09:30",
            "14:30",
        ]

        first_slot_id = slot_response.data["slots"][0]["slot_id"]
        second_slot_id = slot_response.data["slots"][1]["slot_id"]
        first_booking = await ExamBookingService.book(
            db,
            registration_id=registration.id,
            schedule_id=schedule_id,
            slot_id=first_slot_id,
            exam_date=exam_day,
        )
        await db.commit()

        booked_slots = await get_slots(
            schedule_id=schedule_id,
            exam_date=exam_day,
            registration_id=registration.id,
            current_user=SimpleNamespace(id=seed.user_id),
            db=db,
        )
        assert [slot["remaining"] for slot in booked_slots.data["slots"]] == [0, 5]

        with pytest.raises(BusinessException, match="该时段机位已满"):
            await ExamBookingService.book(
                db,
                registration_id=second_registration.id,
                schedule_id=schedule_id,
                slot_id=first_slot_id,
                exam_date=exam_day,
            )

        cancelled = await ExamBookingService.cancel(
            db, first_booking.id, registration.id
        )
        assert cancelled.status == "cancelled"
        await db.commit()

        second_subject_booking = await ExamBookingService.book(
            db,
            registration_id=second_registration.id,
            schedule_id=schedule_id,
            slot_id=first_slot_id,
            exam_date=exam_day,
        )
        await db.commit()

        rebooked = await ExamBookingService.book(
            db,
            registration_id=registration.id,
            schedule_id=schedule_id,
            slot_id=second_slot_id,
            exam_date=exam_day,
        )
        await db.commit()

        assert rebooked.id == first_booking.id
        assert rebooked.status == "booked"
        assert rebooked.slot_id == second_slot_id
        assert second_subject_booking.subject_id != rebooked.subject_id

        booking_rows = await my_bookings(
            current_user=SimpleNamespace(id=seed.user_id),
            db=db,
        )
        active_row = next(
            row for row in booking_rows.data
            if row["registration_id"] == registration.id and row["status"] == "booked"
        )
        assert active_row["schedule_id"] == schedule_id
        assert active_row["exam_location"] == "成都市高新区天府软件园B区3楼"
