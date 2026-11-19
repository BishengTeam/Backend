"""跨厂商 ID 碰撞时，取消预约不得越权。"""
import os
import random
from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.admin.exam_schedule import ScheduleCreate, create_schedule
from app.api.exam_booking import cancel_booking
from app.domain.exam.src.index import ExamBooking, ExamSchedule, ExamTimeSlot
from app.domain.h3c.src.index import H3cBatchProduct, H3cExamBatch, H3cRegistration
from app.domain.order.src.index import Order
from app.domain.plan.src.index import Plan
from app.domain.user.src.index import User
from app.models.cert_product import CertProduct
from app.port.exceptions import NotFoundException
from app.services.exam_booking import ExamBookingService


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]

SUPER_ADMIN = SimpleNamespace(role="super_admin", permissions=None)


@pytest.fixture
async def context():
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"idor_{uuid4().hex[:8]}"
    collide_id = 1_500_000_000 + random.randint(0, 400_000_000)
    today = date.today()
    exam_day = today + timedelta(days=((4 - today.weekday()) % 7 or 7) + 7)

    async with factory() as db, db.begin():
        attacker = User(openid=f"{prefix}_attacker")
        victim = User(openid=f"{prefix}_victim")
        h3c_product = CertProduct(
            type="h3c",
            code=f"{prefix}_H3C",
            name=f"{prefix} H3C",
            chinese_name=f"{prefix} H3C",
            is_active=True,
            coupon_price_cents=100,
            student_price_cents=100,
            full_price_cents=100,
        )
        sangfor_product = CertProduct(
            type="sangfor",
            code=f"{prefix}_SF",
            name=f"{prefix} SF",
            chinese_name=f"{prefix} SF",
            is_active=True,
            coupon_price_cents=0,
            student_price_cents=0,
            full_price_cents=100,
        )
        plan = Plan(
            product_type=None,
            name=f"{prefix} h3c plan",
            status="draft",
            capacity=100,
            price_cents=0,
        )
        db.add_all((attacker, victim, h3c_product, sangfor_product, plan))
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
        batch_product = H3cBatchProduct(batch_id=batch.id, product_id=h3c_product.id)
        db.add(batch_product)
        await db.flush()
        victim_order = Order(
            user_id=victim.id,
            order_kind="certification",
            product_type=h3c_product.code,
            plan_id=plan.id,
            candidate_name="受害者",
            price=100,
            status="paid",
            out_trade_no=f"{prefix}_victim_order",
        )
        db.add(victim_order)
        await db.flush()
        victim_registration = H3cRegistration(
            id=collide_id,
            batch_id=batch.id,
            batch_product_id=batch_product.id,
            plan_id=plan.id,
            user_id=victim.id,
            order_id=victim_order.id,
            registration_no=f"{prefix}_victim_reg",
            registration_type="full",
            status="approved",
            candidate_snapshot={"name": "受害者"},
            candidate_idcard="510101200001010111",
        )
        db.add(victim_registration)
        attacker_order = Order(
            id=collide_id,
            user_id=attacker.id,
            order_kind="certification",
            product_type=sangfor_product.code,
            candidate_name="攻击者",
            price=100,
            status="paid",
            out_trade_no=f"{prefix}_attacker_order",
            extra_data={
                "business_type": "sangfor",
                "registration_state": "approved",
                "candidate_snapshot": {"name": "攻击者"},
            },
        )
        db.add(attacker_order)
        await db.flush()
        seed = SimpleNamespace(
            factory=factory,
            prefix=prefix,
            collide_id=collide_id,
            attacker_id=attacker.id,
            victim_id=victim.id,
            h3c_product_id=h3c_product.id,
            attacker_order_id=attacker_order.id,
            exam_day=exam_day,
        )

    async with factory() as db:
        created = await create_schedule(
            data=ScheduleCreate(
                vendor="h3c",
                name=f"{prefix} schedule",
                open_date_start=exam_day,
                open_date_end=exam_day,
                allowed_weekdays=[4],
                product_ids=[seed.h3c_product_id],
                time_slots=[{"start_time": "09:30"}],
                shared_quotas=[{"slot_start": "09:30", "total_seats": 5}],
                publish=True,
            ),
            _admin=SUPER_ADMIN,
            db=db,
        )
        seed.schedule_id = created.data["id"]
        seed.slot_id = await db.scalar(select(ExamTimeSlot.id).where(
            ExamTimeSlot.schedule_id == seed.schedule_id
        ))
        booking = await ExamBookingService.book(
            db,
            vendor="h3c",
            registration_id=collide_id,
            schedule_id=seed.schedule_id,
            slot_id=seed.slot_id,
            exam_date=exam_day,
        )
        await db.commit()
        seed.booking_id = booking.id

    yield seed

    async with factory() as db, db.begin():
        schedule_ids = select(ExamSchedule.id).where(ExamSchedule.name.like(f"{prefix}%"))
        await db.execute(delete(ExamBooking).where(ExamBooking.schedule_id.in_(schedule_ids)))
        await db.execute(delete(ExamTimeSlot).where(ExamTimeSlot.schedule_id.in_(schedule_ids)))
        await db.execute(delete(ExamSchedule).where(ExamSchedule.id.in_(schedule_ids)))
        await db.execute(delete(H3cRegistration).where(
            H3cRegistration.registration_no.like(f"{prefix}%")
        ))
        await db.execute(delete(Order).where(Order.out_trade_no.like(f"{prefix}%")))
        await db.execute(delete(H3cBatchProduct).where(
            H3cBatchProduct.batch_id.in_(
                select(H3cExamBatch.id).where(H3cExamBatch.exam_code.like(f"{prefix}%"))
            )
        ))
        await db.execute(delete(H3cExamBatch).where(H3cExamBatch.exam_code.like(f"{prefix}%")))
        await db.execute(delete(Plan).where(Plan.name.like(f"{prefix}%")))
        await db.execute(delete(CertProduct).where(CertProduct.code.like(f"{prefix}%")))
        await db.execute(delete(User).where(User.openid.like(f"{prefix}%")))

    await engine.dispose()


async def test_sangfor_order_cannot_cancel_colliding_h3c_booking(context):
    async with context.factory() as db:
        with pytest.raises(NotFoundException):
            await cancel_booking(
                booking_id=context.booking_id,
                vendor="sangfor",
                registration_id=context.collide_id,
                current_user=SimpleNamespace(id=context.attacker_id),
                db=db,
            )

    async with context.factory() as db:
        booking = await db.get(ExamBooking, context.booking_id)
        assert booking is not None
        assert booking.status == "booked"
        assert booking.vendor == "h3c"
        assert booking.registration_id == context.collide_id
