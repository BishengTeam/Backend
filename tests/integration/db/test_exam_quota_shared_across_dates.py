"""H3C 时段共享池跨日期生效：2 个机位不能因日期不同而放大到 4 个。"""
import os
import asyncio
from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.admin.exam_schedule import (
    ScheduleCreate,
    close_schedule,
    create_schedule,
)
from app.domain.exam.src.index import (
    ExamBooking,
    ExamSchedule,
    ExamScheduleSubject,
    ExamSeatQuota,
    ExamTimeSlot,
)
from app.domain.h3c.src.index import H3cBatchProduct, H3cExamBatch, H3cRegistration
from app.domain.order.src.index import Order
from app.domain.plan.src.index import Plan
from app.domain.user.src.index import User
from app.models.cert_product import CertProduct
from app.port.exceptions import BusinessException
from app.services.exam_booking import ExamBookingService


SUPER_ADMIN = SimpleNamespace(role="super_admin", permissions=None)

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def context():
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"quota_dates_{uuid4().hex[:8]}"
    today = date.today()
    first_friday = today + timedelta(days=((4 - today.weekday()) % 7 or 7) + 7)
    second_friday = first_friday + timedelta(days=7)

    async with factory() as db, db.begin():
        product = CertProduct(
            type="h3c",
            code=f"{prefix}_NE",
            name=f"{prefix} H3CNE",
            chinese_name=f"{prefix} H3CNE",
            is_active=True,
            coupon_price_cents=30000,
            student_price_cents=20000,
            full_price_cents=50000,
        )
        plan = Plan(
            product_type=None,
            name=f"{prefix} batch",
            status="draft",
            capacity=100,
            price_cents=0,
        )
        db.add_all((product, plan))
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
        batch_product = H3cBatchProduct(batch_id=batch.id, product_id=product.id)
        db.add(batch_product)
        await db.flush()

        registrations = []
        for index in range(3):
            user = User(openid=f"{prefix}_u{index}")
            db.add(user)
            await db.flush()
            order = Order(
                user_id=user.id,
                order_kind="certification",
                product_type=product.code,
                plan_id=plan.id,
                candidate_name=f"考生{index}",
                candidate_idcard=f"51010120000101010{index}",
                price=50000,
                status="paid",
                out_trade_no=f"{prefix}_order{index}",
            )
            db.add(order)
            await db.flush()
            registration = H3cRegistration(
                batch_id=batch.id,
                batch_product_id=batch_product.id,
                plan_id=plan.id,
                user_id=user.id,
                order_id=order.id,
                registration_no=f"{prefix}_reg{index}",
                registration_type="full",
                status="approved",
                candidate_snapshot={"name": f"考生{index}"},
                candidate_idcard=f"51010120000101010{index}",
            )
            db.add(registration)
            registrations.append(registration)
        await db.flush()
        registration_ids = [item.id for item in registrations]
        seed = SimpleNamespace(
            factory=factory,
            product_id=product.id,
            registration_ids=registration_ids,
            first_friday=first_friday,
            second_friday=second_friday,
        )

    async with factory() as db:
        result = await create_schedule(
            data=ScheduleCreate(
                vendor="h3c",
                name=f"{prefix} schedule",
                open_date_start=first_friday,
                open_date_end=second_friday,
                allowed_weekdays=[4],
                product_ids=[seed.product_id],
                time_slots=[{"start_time": "09:30"}],
                shared_quotas=[{"slot_start": "09:30", "total_seats": 2}],
                publish=True,
            ),
            _admin=SUPER_ADMIN,
            db=db,
        )
        seed.schedule_id = result.data["id"]
        seed.slot_id = await db.scalar(select(ExamTimeSlot.id).where(
            ExamTimeSlot.schedule_id == seed.schedule_id
        ))

    yield seed

    async with factory() as db, db.begin():
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
        await db.execute(delete(H3cRegistration).where(
            H3cRegistration.registration_no.like(f"{prefix}%")
        ))
        await db.execute(delete(Order).where(Order.out_trade_no.like(f"{prefix}%")))
        await db.execute(delete(H3cBatchProduct).where(
            H3cBatchProduct.product_id == select(CertProduct.id).where(
                CertProduct.code.like(f"{prefix}%")
            ).scalar_subquery()
        ))
        await db.execute(delete(H3cExamBatch).where(
            H3cExamBatch.exam_code.like(f"{prefix}%")
        ))
        await db.execute(delete(Plan).where(Plan.name.like(f"{prefix}%")))
        await db.execute(delete(CertProduct).where(CertProduct.code.like(f"{prefix}%")))
        await db.execute(delete(User).where(User.openid.like(f"{prefix}%")))

    await engine.dispose()


async def test_shared_seat_pool_counts_all_dates(context):
    first, second, third = context.registration_ids

    async with context.factory() as db:
        await ExamBookingService.book(
            db,
            registration_id=first,
            schedule_id=context.schedule_id,
            slot_id=context.slot_id,
            exam_date=context.first_friday,
        )
        await ExamBookingService.book(
            db,
            registration_id=second,
            schedule_id=context.schedule_id,
            slot_id=context.slot_id,
            exam_date=context.second_friday,
        )
        await db.commit()

    async with context.factory() as db:
        slots = await ExamBookingService.get_slots_with_remaining(
            db,
            context.schedule_id,
            await db.scalar(select(ExamScheduleSubject.id).where(
                ExamScheduleSubject.schedule_id == context.schedule_id
            )),
            context.first_friday,
        )
        assert [slot["remaining"] for slot in slots] == [0]

        with pytest.raises(BusinessException, match="该时段机位已满"):
            await ExamBookingService.book(
                db,
                registration_id=third,
                schedule_id=context.schedule_id,
                slot_id=context.slot_id,
                exam_date=context.first_friday,
            )


async def _close_in_new_session(factory, schedule_id: int):
    async with factory() as db:
        return await close_schedule(schedule_id=schedule_id, _admin=SUPER_ADMIN, db=db)


async def test_admin_close_waits_for_inflight_booking(context):
    """预约持有计划共享锁期间，管理员关闭计划必须等待，避免关闭后仍产生预约。"""
    async with context.factory() as booking_db:
        await ExamBookingService.book(
            booking_db,
            registration_id=context.registration_ids[0],
            schedule_id=context.schedule_id,
            slot_id=context.slot_id,
            exam_date=context.first_friday,
        )
        closing = asyncio.create_task(
            _close_in_new_session(context.factory, context.schedule_id)
        )
        await asyncio.sleep(0.5)
        assert not closing.done(), "管理员关闭计划未被预约共享锁阻塞"
        await booking_db.commit()

    await asyncio.wait_for(closing, timeout=5)
    async with context.factory() as db:
        schedule = await db.get(ExamSchedule, context.schedule_id)
        assert schedule is not None and schedule.status == "closed"
