import os
from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.admin.exam_schedule import (
    ScheduleCreate,
    SeatQuotaBatch,
    SeatQuotaItem,
    SharedSeatQuotaItem,
    create_schedule,
    get_schedule,
    publish_schedule,
    set_seat_quotas,
)
from app.domain.exam.src.index import (
    ExamSchedule,
    ExamScheduleSubject,
    ExamSeatQuota,
    ExamTimeSlot,
)
from app.models.cert_product import CertProduct
from app.port.exceptions import BusinessException


pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def context():
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"examsched_{uuid4().hex[:10]}"

    async with factory() as db, db.begin():
        products = [
            CertProduct(
                type="h3c",
                code=f"{prefix}_ne",
                name=f"{prefix} H3CNE",
                chinese_name=f"{prefix} H3CNE",
                is_active=True,
                coupon_price_cents=30000,
                student_price_cents=20000,
                full_price_cents=50000,
            ),
            CertProduct(
                type="h3c",
                code=f"{prefix}_se",
                name=f"{prefix} H3CSE",
                chinese_name=f"{prefix} H3CSE",
                is_active=True,
                coupon_price_cents=50000,
                student_price_cents=40000,
                full_price_cents=80000,
            ),
        ]
        db.add_all(products)

    async with factory() as db:
        products = list(await db.scalars(select(CertProduct).where(
            CertProduct.code.like(f"{prefix}%")
        )))

    yield type("Context", (), {"factory": factory, "prefix": prefix, "products": products})()

    async with factory() as db, db.begin():
        schedule_ids = select(ExamSchedule.id).where(ExamSchedule.name.like(f"{prefix}%"))
        await db.execute(delete(ExamSeatQuota).where(ExamSeatQuota.schedule_id.in_(schedule_ids)))
        await db.execute(delete(ExamScheduleSubject).where(
            ExamScheduleSubject.schedule_id.in_(schedule_ids)
        ))
        await db.execute(delete(ExamTimeSlot).where(ExamTimeSlot.schedule_id.in_(schedule_ids)))
        await db.execute(delete(ExamSchedule).where(ExamSchedule.id.in_(schedule_ids)))
        await db.execute(delete(CertProduct).where(CertProduct.code.like(f"{prefix}%")))

    await engine.dispose()


async def test_composite_create_publishes_full_schedule_and_overrides_quota(context):
    factory, prefix, products = context.factory, context.prefix, context.products
    today = date.today()
    payload = ScheduleCreate(
        name=f"{prefix} December schedule",
        open_date_start=today,
        open_date_end=today + timedelta(days=90),
        allowed_weekdays=[4, 4],
        product_ids=[products[0].id, products[1].id],
        time_slots=[
            {"start_time": "09:30", "label": "上午第一场"},
            {"start_time": "14:30"},
        ],
        default_quota=5,
        seat_quotas=[
            {"product_id": products[1].id, "slot_start": "09:30", "total_seats": 3},
        ],
        shared_quotas=[
            {"slot_start": "09:30", "total_seats": 3},
        ],
        publish=True,
    )

    async with factory() as db:
        result = await create_schedule(data=payload, _admin=None, db=db)

    assert result.data["status"] == "published"
    schedule_id = result.data["id"]

    async with factory() as db:
        schedule = await db.get(ExamSchedule, schedule_id)
        assert schedule is not None
        assert schedule.status == "published"
        assert schedule.allowed_weekdays == [4]

        subjects = list(await db.scalars(select(ExamScheduleSubject).where(
            ExamScheduleSubject.schedule_id == schedule_id
        )))
        slots = list(await db.scalars(select(ExamTimeSlot).where(
            ExamTimeSlot.schedule_id == schedule_id
        ).order_by(ExamTimeSlot.sort_order)))
        quotas = list(await db.scalars(select(ExamSeatQuota).where(
            ExamSeatQuota.schedule_id == schedule_id
        )))

        assert len(subjects) == 2
        assert [(slot.start_time.strftime("%H:%M"), slot.label) for slot in slots] == [
            ("09:30", "上午第一场"),
            ("14:30", "14:30 场"),
        ]
        assert len(quotas) == 2
        slot_by_time = {slot.start_time.strftime("%H:%M"): slot.id for slot in slots}
        quota_values = {
            quota.slot_id: quota.total_seats for quota in quotas
        }
        assert {quota.subject_id for quota in quotas} == {None}
        assert quota_values[slot_by_time["09:30"]] == 3
        assert quota_values[slot_by_time["14:30"]] == 5

        detail = await get_schedule(schedule_id=schedule_id, _admin=None, db=db)
        assert detail.data["seat_quotas"] == [
            {
                "slot_id": slot_by_time["09:30"],
                "slot_start": "09:30",
                "total_seats": 3,
                "booked": 0,
                "remaining": 3,
            },
            {
                "slot_id": slot_by_time["14:30"],
                "slot_start": "14:30",
                "total_seats": 5,
                "booked": 0,
                "remaining": 5,
            },
        ]


async def test_legacy_minimal_create_stays_draft_and_cannot_publish_empty(context):
    factory, prefix = context.factory, context.prefix
    payload = ScheduleCreate(
        name=f"{prefix} legacy schedule",
        open_date_start=date.today(),
        open_date_end=date.today() + timedelta(days=30),
    )

    async with factory() as db:
        result = await create_schedule(data=payload, _admin=None, db=db)
        assert result.data["status"] == "draft"

        schedule_id = result.data["id"]
        with pytest.raises(BusinessException, match="至少一个科目和时段"):
            await publish_schedule(schedule_id=schedule_id, _admin=None, db=db)


async def test_legacy_product_quota_payload_normalizes_to_shared_pool(context):
    factory, prefix, products = context.factory, context.prefix, context.products
    payload = ScheduleCreate(
        name=f"{prefix} legacy quota schedule",
        open_date_start=date.today(),
        open_date_end=date.today() + timedelta(days=30),
        allowed_weekdays=[4],
        product_ids=[products[0].id, products[1].id],
        time_slots=[{"start_time": "09:30"}, {"start_time": "14:30"}],
        default_quota=5,
        seat_quotas=[
            {"product_id": products[0].id, "slot_start": "09:30", "total_seats": 3},
            {"product_id": products[1].id, "slot_start": "09:30", "total_seats": 7},
        ],
        publish=True,
    )

    async with factory() as db:
        result = await create_schedule(data=payload, _admin=None, db=db)
        quotas = list(await db.scalars(select(ExamSeatQuota).where(
            ExamSeatQuota.schedule_id == result.data["id"]
        ).order_by(ExamSeatQuota.slot_id)))

    assert [(quota.subject_id, quota.total_seats) for quota in quotas] == [
        (None, 7),
        (None, 5),
    ]


async def test_published_schedule_quota_can_increase_and_closed_schedule_cannot_change(context):
    factory, prefix, products = context.factory, context.prefix, context.products
    payload = ScheduleCreate(
        name=f"{prefix} quota schedule",
        open_date_start=date.today(),
        open_date_end=date.today() + timedelta(days=30),
        allowed_weekdays=[4],
        product_ids=[products[0].id],
        time_slots=[{"start_time": "09:30"}],
        default_quota=5,
        publish=True,
    )

    async with factory() as db:
        result = await create_schedule(data=payload, _admin=None, db=db)
        schedule_id = result.data["id"]
        subject_id = await db.scalar(select(ExamScheduleSubject.id).where(
            ExamScheduleSubject.schedule_id == schedule_id
        ))
        slot_id = await db.scalar(select(ExamTimeSlot.id).where(
            ExamTimeSlot.schedule_id == schedule_id
        ))

        await set_seat_quotas(
            schedule_id=schedule_id,
            data=SeatQuotaBatch(shared_quotas=[
                SharedSeatQuotaItem(slot_id=slot_id, total_seats=8)
            ]),
            _admin=None,
            db=db,
        )
        quota = await db.scalar(select(ExamSeatQuota).where(
            ExamSeatQuota.schedule_id == schedule_id,
            ExamSeatQuota.subject_id.is_(None),
            ExamSeatQuota.slot_id == slot_id,
        ))
        assert quota is not None and quota.total_seats == 8

        schedule = await db.get(ExamSchedule, schedule_id)
        assert schedule is not None
        schedule.status = "closed"
        await db.commit()

        with pytest.raises(BusinessException, match="已关闭计划不可设置配额"):
            await set_seat_quotas(
                schedule_id=schedule_id,
                data=SeatQuotaBatch(quotas=[
                    SeatQuotaItem(subject_id=subject_id, slot_id=slot_id, total_seats=9)
                ]),
                _admin=None,
                db=db,
            )
