"""深信服接入分时预约考场 — 预约/配额/报名码/附件。"""
import os
from io import BytesIO
from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from fastapi import UploadFile

from app.api.admin.cert_attachment import (
    AttachmentCreate,
    create_attachment,
    delete_attachment,
    list_attachments,
    upload_attachment,
)
from app.api.upload import get_file
from app.api.admin.exam_schedule import (
    ScheduleCreate,
    SeatQuotaBatch,
    SharedSeatQuotaItem,
    create_schedule,
    list_bookings,
    set_seat_quotas,
)
from app.api.exam_booking import (
    cancel_booking,
    get_attachments,
    get_notice,
    get_slots,
    my_bookings,
    my_schedules,
)
from app.domain.exam.src.index import (
    CertAttachment,
    ExamBooking,
    ExamSchedule,
    ExamScheduleSubject,
    ExamSeatQuota,
    ExamTimeSlot,
)
from app.domain.order.src.index import Order
from app.models.offline_payment import OfflinePaymentAudit
from app.domain.plan.src.index import Plan
from app.domain.user.src.index import User
from app.models.cert_product import CertProduct
from app.port.exceptions import BusinessException
from app.services.exam_booking import ExamBookingService
from app.services.sangfor_registration_code import (
    CODE_FIELD,
    ensure_registration_code,
)
from app.services.vendor_registration import resolve_registration


SUPER_ADMIN = SimpleNamespace(role="super_admin", permissions=None)

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


@pytest.fixture
async def context():
    url = os.environ["TEST_DATABASE_URL"]
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"sangfor_exam_{uuid4().hex[:8]}"

    async with factory() as db, db.begin():
        user = User(openid=f"{prefix}_user")
        h3c_product = CertProduct(
            type="h3c",
            code=f"{prefix}_H3C",
            name=f"{prefix} H3C",
            chinese_name=f"{prefix} H3C",
            is_active=True,
            coupon_price_cents=30000,
            student_price_cents=20000,
            full_price_cents=50000,
        )
        product = CertProduct(
            type="sangfor",
            code=f"{prefix}_SCSA",
            # 内部名称与展示名称不同，用于覆盖预约详情显示内部代码的回归。
            name=f"{prefix}_scsa_s",
            chinese_name=f"{prefix} SCSA",
            is_active=True,
            coupon_price_cents=0,
            student_price_cents=0,
            full_price_cents=100000,
        )
        plan = Plan(
            product_type=product.code,
            name=f"{prefix} sangfor batch",
            status="draft",
            price_cents=100000,
            capacity=100,
            apply_start=None,
            apply_end=None,
            exam_date=None,
        )
        db.add_all((user, h3c_product, product, plan))
        await db.flush()

        order = Order(
            user_id=user.id,
            order_kind="certification",
            product_type=product.code,
            plan_id=plan.id,
            candidate_name="深信服考生",
            candidate_idcard="510101200001010177",
            price=100000,
            status="paid",
            payment_channel="bank_transfer",
            out_trade_no=f"{prefix}_order",
            extra_data={
                "business_type": "sangfor",
                "registration_state": "approved",
                "revision": 1,
                "current_version": 1,
                "candidate_snapshot": {"name": "深信服考生"},
                "batch_snapshot": {"name": plan.name},
                "versions": [],
                "reviews": [],
                "exports": [],
            },
        )
        db.add(order)
        await db.flush()
        seed = SimpleNamespace(
            user_id=user.id,
            order_id=order.id,
            product_id=product.id,
            h3c_product_id=h3c_product.id,
            plan_id=plan.id,
        )

    yield SimpleNamespace(factory=factory, prefix=prefix, **vars(seed))

    async with factory() as db, db.begin():
        product_ids = select(CertProduct.id).where(
            CertProduct.code.like(f"{prefix}%")
        )
        schedule_ids = select(ExamSchedule.id).where(
            ExamSchedule.name.like(f"{prefix}%")
        )
        await db.execute(delete(CertAttachment).where(
            CertAttachment.schedule_id.in_(schedule_ids)
        ))
        await db.execute(delete(ExamBooking).where(
            ExamBooking.schedule_id.in_(schedule_ids)
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
        order_ids = select(Order.id).where(Order.out_trade_no.like(f"{prefix}%"))
        await db.execute(delete(OfflinePaymentAudit).where(
            OfflinePaymentAudit.order_id.in_(order_ids)
        ))
        await db.execute(delete(Order).where(
            Order.out_trade_no.like(f"{prefix}%")
        ))
        await db.execute(delete(Plan).where(Plan.name.like(f"{prefix}%")))
        await db.execute(delete(CertProduct).where(CertProduct.id.in_(product_ids)))
        await db.execute(delete(User).where(User.openid.like(f"{prefix}%")))

    await engine.dispose()


async def _create_schedule(context, *, vendor: str, product_id: int, name: str):
    today = date.today()
    # 至少留出一个完整周，保证考前 3 天仍可取消。
    exam_day = today + timedelta(days=((4 - today.weekday()) % 7 or 7) + 7)
    async with context.factory() as db:
        result = await create_schedule(
            data=ScheduleCreate(
                vendor=vendor,
                name=f"{context.prefix} {name}",
                open_date_start=exam_day - timedelta(days=7),
                open_date_end=exam_day + timedelta(days=7),
                allowed_weekdays=[4],
                exam_location="成都市高新区天府软件园B区3楼",
                product_ids=[product_id],
                time_slots=[{"start_time": "09:30"}],
                publish=True,
            ),
            _admin=SUPER_ADMIN,
            db=db,
        )
    return result.data["id"], exam_day


async def test_sangfor_schedule_has_no_quota_rows(context):
    schedule_id, _ = await _create_schedule(
        context, vendor="sangfor", product_id=context.product_id, name="no quota"
    )

    async with context.factory() as db:
        schedule = await db.get(ExamSchedule, schedule_id)
        assert schedule is not None and schedule.vendor == "sangfor"
        quotas = list(await db.scalars(select(ExamSeatQuota).where(
            ExamSeatQuota.schedule_id == schedule_id
        )))
        assert quotas == []

        with pytest.raises(BusinessException, match="不设机位配额"):
            await set_seat_quotas(
                schedule_id=schedule_id,
                data=SeatQuotaBatch(
                    shared_quotas=[SharedSeatQuotaItem(slot_id=1, total_seats=3)]
                ),
                _admin=SUPER_ADMIN,
                db=db,
            )


async def test_sangfor_booking_flow_without_seat_limit(context):
    schedule_id, exam_day = await _create_schedule(
        context, vendor="sangfor", product_id=context.product_id, name="booking"
    )
    user = SimpleNamespace(id=context.user_id)

    async with context.factory() as db:
        schedules = await my_schedules(
            vendor="sangfor", registration_id=context.order_id,
            current_user=user, db=db,
        )
        assert [row["id"] for row in schedules.data] == [schedule_id]

        slots = await get_slots(
            schedule_id=schedule_id, exam_date=exam_day, vendor="sangfor",
            registration_id=context.order_id, current_user=user, db=db,
        )
        assert [slot["remaining"] for slot in slots.data["slots"]] == [-1]

    async with context.factory() as db:
        slot_id = slots.data["slots"][0]["slot_id"]
        booking = await ExamBookingService.book(
            db,
            vendor="sangfor",
            registration_id=context.order_id,
            schedule_id=schedule_id,
            slot_id=slot_id,
            exam_date=exam_day,
        )
        await db.commit()
        assert booking.vendor == "sangfor"
        assert booking.status == "booked"
        booking_id = booking.id

    async with context.factory() as db:
        rows = await my_bookings(vendor="sangfor", current_user=user, db=db)
        assert len(rows.data) == 1
        assert rows.data[0]["exam_location"] == "成都市高新区天府软件园B区3楼"
        assert rows.data[0]["subject_name"] == f"{context.prefix} SCSA"

        admin_rows = await list_bookings(
            schedule_id=schedule_id, exam_date=None, slot_id=None,
            _admin=SUPER_ADMIN, db=db,
        )
        assert admin_rows.data[0]["vendor"] == "sangfor"
        assert admin_rows.data[0]["candidate_name"] == "深信服考生"

    async with context.factory() as db:
        await cancel_booking(
            booking_id=booking_id, vendor="sangfor",
            registration_id=context.order_id, current_user=user, db=db,
        )
        booking = await db.get(ExamBooking, booking_id)
        assert booking is not None and booking.status == "cancelled"


async def test_sangfor_registration_cannot_book_h3c_schedule(context):
    schedule_id, exam_day = await _create_schedule(
        context, vendor="h3c", product_id=context.h3c_product_id, name="h3c schedule"
    )
    async with context.factory() as db:
        slot_id = await db.scalar(select(ExamTimeSlot.id).where(
            ExamTimeSlot.schedule_id == schedule_id
        ))
        with pytest.raises(BusinessException, match="不属于当前认证厂商"):
            await ExamBookingService.book(
                db,
                vendor="sangfor",
                registration_id=context.order_id,
                schedule_id=schedule_id,
                slot_id=slot_id,
                exam_date=exam_day,
            )


async def test_sangfor_registration_code_is_idempotent(context):
    async with context.factory() as db:
        order = await db.get(Order, context.order_id)
        assert order is not None
        first = await ensure_registration_code(db, order)
        await db.commit()

    async with context.factory() as db:
        order = await db.get(Order, context.order_id)
        assert order is not None
        second = await ensure_registration_code(db, order)

    assert first == second
    assert first.startswith(f"SF-{context.order_id:06d}-")
    assert len(first.rsplit("-", 1)[-1]) == 16  # 64 bit 随机后缀
    assert (order.extra_data or {})[CODE_FIELD] == first


async def test_attachment_crud_for_schedule(context):
    schedule_id, _ = await _create_schedule(
        context, vendor="sangfor", product_id=context.product_id, name="attachments"
    )
    async with context.factory() as db:
        created = await create_attachment(
            data=AttachmentCreate(
                vendor="sangfor",
                schedule_id=schedule_id,
                title="考场指引",
                file_url="https://cdn.example.com/guide.pdf",
                file_size=1024,
            ),
            _admin=SUPER_ADMIN,
            db=db,
        )
        attachment_id = created.data["id"]

    async with context.factory() as db:
        admin_rows = await list_attachments(
            vendor="sangfor", schedule_id=schedule_id, _admin=SUPER_ADMIN, db=db
        )
        assert [row["title"] for row in admin_rows.data] == ["考场指引"]

        user_rows = await get_attachments(
            schedule_id=schedule_id,
            vendor="sangfor",
            current_user=SimpleNamespace(id=context.user_id),
            db=db,
        )
        assert user_rows.data[0]["file_url"].endswith("guide.pdf")

        notice = await get_notice(
            schedule_id=schedule_id,
            vendor="sangfor",
            current_user=SimpleNamespace(id=context.user_id),
            db=db,
        )
        assert notice.data["notice"] == ""

    async with context.factory() as db:
        await delete_attachment(attachment_id=attachment_id, _admin=SUPER_ADMIN, db=db)
        remaining = await db.scalar(select(CertAttachment.id).where(
            CertAttachment.schedule_id == schedule_id
        ))
        assert remaining is None


async def test_attachment_upload_is_downloadable(context, tmp_path, monkeypatch):
    import app.services.upload as upload_module

    monkeypatch.setattr(upload_module, "UPLOAD_DIR", str(tmp_path))
    schedule_id, _ = await _create_schedule(
        context, vendor="sangfor", product_id=context.product_id, name="upload"
    )

    async with context.factory() as db:
        uploaded = await upload_attachment(
            vendor="sangfor",
            file=UploadFile(
                filename="guide.pdf",
                file=BytesIO(b"%PDF-1.7\n" + b"z" * 32),
            ),
            _admin=SUPER_ADMIN,
        )
        assert uploaded.data["url"].startswith("/api/media/cert-attachments/")
        created = await create_attachment(
            data=AttachmentCreate(
                vendor="sangfor",
                schedule_id=schedule_id,
                title="考场指引",
                file_url=uploaded.data["url"],
            ),
            _admin=SUPER_ADMIN,
            db=db,
        )
        attachment_id = created.data["id"]

    response = await get_file(
        uploaded.data["url"].removeprefix("/api/media/")
    )
    assert response.path.startswith(str(tmp_path))
    assert response.headers["x-content-type-options"] == "nosniff"

    async with context.factory() as db:
        await delete_attachment(attachment_id=attachment_id, _admin=SUPER_ADMIN, db=db)


async def test_unapproved_user_cannot_read_notice_or_attachments(context):
    schedule_id, _ = await _create_schedule(
        context, vendor="sangfor", product_id=context.product_id, name="private"
    )
    async with context.factory() as db:
        outsider = User(openid=f"{context.prefix}_outsider")
        db.add(outsider)
        await db.commit()
        outsider_id = outsider.id

    async with context.factory() as db:
        with pytest.raises(Exception):
            await get_notice(
                schedule_id=schedule_id,
                vendor="sangfor",
                current_user=SimpleNamespace(id=outsider_id),
                db=db,
            )
        with pytest.raises(Exception):
            await get_attachments(
                schedule_id=schedule_id,
                vendor="sangfor",
                current_user=SimpleNamespace(id=outsider_id),
                db=db,
            )


async def test_draft_schedule_is_hidden_from_candidates(context):
    today = date.today()
    exam_day = today + timedelta(days=((4 - today.weekday()) % 7 or 7) + 7)
    async with context.factory() as db:
        draft = await create_schedule(
            data=ScheduleCreate(
                vendor="sangfor",
                name=f"{context.prefix} draft schedule",
                open_date_start=exam_day,
                open_date_end=exam_day,
                allowed_weekdays=[4],
                product_ids=[context.product_id],
                time_slots=[{"start_time": "09:30"}],
                publish=False,
            ),
            _admin=SUPER_ADMIN,
            db=db,
        )

    async with context.factory() as db:
        with pytest.raises(Exception):
            await get_notice(
                schedule_id=draft.data["id"],
                vendor="sangfor",
                current_user=SimpleNamespace(id=context.user_id),
                db=db,
            )


async def test_refund_approval_blocks_booking_and_releases_active_booking(context):
    schedule_id, exam_day = await _create_schedule(
        context, vendor="sangfor", product_id=context.product_id, name="refund"
    )
    async with context.factory() as db:
        slot_id = await db.scalar(select(ExamTimeSlot.id).where(
            ExamTimeSlot.schedule_id == schedule_id
        ))
        booking = await ExamBookingService.book(
            db,
            vendor="sangfor",
            registration_id=context.order_id,
            schedule_id=schedule_id,
            slot_id=slot_id,
            exam_date=exam_day,
        )
        await db.commit()
        booking_id = booking.id

    async with context.factory() as db:
        db.add(OfflinePaymentAudit(
            order_id=context.order_id,
            actor_id=1,
            action="admin.generic_refund_approved",
            request_key=f"{context.prefix}_refund",
            payload_hash="0" * 64,
            summary={},
        ))
        await db.commit()

    async with context.factory() as db:
        info = await resolve_registration(db, "sangfor", context.order_id)
        assert info is not None
        assert info.status == "refund_processing"
        assert info.approved is False

        schedules = await my_schedules(
            vendor="sangfor",
            registration_id=context.order_id,
            current_user=SimpleNamespace(id=context.user_id),
            db=db,
        )
        assert schedules.data == []

        new_schedule_id, new_exam_day = await _create_schedule(
            context, vendor="sangfor", product_id=context.product_id, name="refund-blocked"
        )
        new_slot_id = await db.scalar(select(ExamTimeSlot.id).where(
            ExamTimeSlot.schedule_id == new_schedule_id
        ))
        with pytest.raises(BusinessException, match="尚未通过审核"):
            await ExamBookingService.book(
                db,
                vendor="sangfor",
                registration_id=context.order_id,
                schedule_id=new_schedule_id,
                slot_id=new_slot_id,
                exam_date=new_exam_day,
            )

        # 退款审批钩子调用的释放逻辑：已有预约被取消，考位可再次分配。
        cancelled = await ExamBookingService.cancel_active_for(
            db, "sangfor", context.order_id
        )
        await db.commit()
        assert cancelled == 1

    async with context.factory() as db:
        active = await db.get(ExamBooking, booking_id)
        assert active is not None and active.status == "cancelled"
