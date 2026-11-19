"""用户端 — 多厂商考场预约 API（H3C / 深信服）。"""
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db
from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, success
from app.services.exam_booking import ExamBookingService
from app.services.vendor_registration import (
    approved_registration_ids,
    normalize_vendor,
    resolve_registration,
    user_registration_ids,
)
from app.domain.exam.src.index import (
    CertAttachment,
    ExamBooking,
    ExamSchedule,
    ExamScheduleSubject,
)
from app.port.exceptions import NotFoundException

router = APIRouter(tags=["考场预约"])

VendorQuery = Annotated[str, Query(description="认证厂商：h3c / sangfor")]


class BookRequest(BaseModel):
    slot_id: int
    exam_date: date


@router.get("/schedules", response_model=APIResponse[list], summary="我的可预约计划")
async def my_schedules(
    vendor: VendorQuery = "h3c",
    registration_id: int = Query(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """只返回 published 且我已审核通过的。"""
    vendor = normalize_vendor(vendor)
    registration = await resolve_registration(
        db, vendor, registration_id, current_user.id
    )
    if registration is None:
        raise NotFoundException("报名记录")
    if not registration.approved:
        return success(data=[])

    rows = (await db.execute(
        select(ExamSchedule).where(
            ExamSchedule.status == "published",
            ExamSchedule.vendor == vendor,
        ).order_by(ExamSchedule.open_date_start)
    )).scalars().all()
    return success(data=[{
        "id": s.id, "name": s.name, "vendor": s.vendor,
        "open_date_start": s.open_date_start.isoformat(),
        "open_date_end": s.open_date_end.isoformat(),
        "exam_location": s.exam_location,
    } for s in rows])


@router.get("/schedules/{schedule_id}/dates", response_model=APIResponse[dict], summary="可预约日期列表")
async def get_dates(
    schedule_id: int = Path(..., ge=1),
    vendor: VendorQuery = "h3c",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 校验用户有该厂商已通过审核的报名
    registration_ids = await approved_registration_ids(db, vendor, current_user.id)
    if not registration_ids:
        return success(data={"dates": []})

    dates = await ExamBookingService.get_bookable_dates(db, schedule_id)
    return success(data={"dates": [d.isoformat() for d in dates]})


@router.get("/schedules/{schedule_id}/slots", response_model=APIResponse[dict], summary="某日期的时段及剩余机位")
async def get_slots(
    schedule_id: int = Path(..., ge=1),
    exam_date: date = Query(..., alias="date"),
    vendor: VendorQuery = "h3c",
    registration_id: int | None = Query(None, ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """自动从用户报名记录解析科目，无需前端传 subject_id。"""
    vendor = normalize_vendor(vendor)
    if registration_id is not None:
        registration = await resolve_registration(
            db, vendor, registration_id, current_user.id
        )
        if registration is None or not registration.approved:
            raise NotFoundException("报名记录")
        product = registration.product
    else:
        ids = await approved_registration_ids(db, vendor, current_user.id)
        if not ids:
            raise NotFoundException("报名记录")
        product = None
        for current_id in sorted(ids, reverse=True):
            info = await resolve_registration(db, vendor, current_id)
            if info is not None and info.product is not None:
                product = info.product
                break

    subject_id = await db.scalar(
        select(ExamScheduleSubject.id).where(
            ExamScheduleSubject.schedule_id == schedule_id,
            ExamScheduleSubject.product_id == product.id,
        )
    ) if product else None
    if subject_id is None:
        return success(data={"slots": []})

    slots = await ExamBookingService.get_slots_with_remaining(
        db, schedule_id, subject_id, exam_date, vendor=vendor
    )
    return success(data={"slots": slots})


@router.post("/schedules/{schedule_id}/book", response_model=APIResponse[dict], summary="预约机位")
async def book_seat(
    schedule_id: int = Path(..., ge=1),
    data: BookRequest = ...,
    vendor: VendorQuery = "h3c",
    registration_id: int = Query(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    vendor = normalize_vendor(vendor)
    registration = await resolve_registration(
        db, vendor, registration_id, current_user.id
    )
    if registration is None or not registration.approved:
        raise NotFoundException("报名记录")
    booking = await ExamBookingService.book(
        db,
        vendor=vendor,
        registration_id=registration_id,
        schedule_id=schedule_id,
        slot_id=data.slot_id,
        exam_date=data.exam_date,
    )
    await db.commit()
    return success(data={
        "booking_id": booking.id,
        "exam_date": booking.exam_date.isoformat(),
    }, message="预约成功")


@router.get("/bookings", response_model=APIResponse[list], summary="我的预约列表")
async def my_bookings(
    vendor: VendorQuery = "h3c",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.domain.exam.src.index import ExamTimeSlot
    from app.models.cert_product import CertProduct

    vendor = normalize_vendor(vendor)
    registration_ids = await user_registration_ids(db, vendor, current_user.id)
    if not registration_ids:
        return success(data=[])

    rows = (await db.execute(
        select(
            ExamBooking,
            ExamTimeSlot,
            ExamSchedule.name,
            ExamSchedule.exam_location,
            CertProduct.name,
        )
        .join(ExamTimeSlot, ExamTimeSlot.id == ExamBooking.slot_id)
        .join(ExamSchedule, ExamSchedule.id == ExamBooking.schedule_id)
        .outerjoin(ExamScheduleSubject, ExamScheduleSubject.id == ExamBooking.subject_id)
        .outerjoin(CertProduct, CertProduct.id == ExamScheduleSubject.product_id)
        .where(
            ExamBooking.vendor == vendor,
            ExamBooking.registration_id.in_(registration_ids),
        )
        .order_by(ExamBooking.exam_date.desc())
    )).all()
    return success(data=[{
        "id": b.id,
        "registration_id": b.registration_id,
        "schedule_id": b.schedule_id,
        "schedule_name": sname,
        "exam_location": slocation,
        "subject_name": pname or "",
        "slot_label": slot.label,
        "slot_time": slot.start_time.isoformat(),
        "exam_date": b.exam_date.isoformat(),
        "status": b.status,
    } for b, slot, sname, slocation, pname in rows])


@router.delete("/bookings/{booking_id}", response_model=APIResponse[dict], summary="取消预约")
async def cancel_booking(
    booking_id: int = Path(..., ge=1),
    vendor: VendorQuery = "h3c",
    registration_id: int = Query(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    vendor = normalize_vendor(vendor)
    registration = await resolve_registration(
        db, vendor, registration_id, current_user.id
    )
    if registration is None:
        raise NotFoundException("报名记录")
    await ExamBookingService.cancel(db, booking_id, registration_id)
    await db.commit()
    return success(message="预约已取消")


@router.get("/schedules/{schedule_id}/notice", response_model=APIResponse[dict], summary="考前注意事项")
async def get_notice(
    schedule_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    schedule = await db.get(ExamSchedule, schedule_id)
    if schedule is None:
        raise NotFoundException("考场计划")
    return success(data={"notice": schedule.pre_exam_notice or ""})


@router.get("/schedules/{schedule_id}/attachments", response_model=APIResponse[list], summary="考场配套附件")
async def get_attachments(
    schedule_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    schedule = await db.get(ExamSchedule, schedule_id)
    if schedule is None:
        raise NotFoundException("考场计划")
    rows = (await db.execute(
        select(CertAttachment).where(
            CertAttachment.schedule_id == schedule_id,
            CertAttachment.is_active.is_(True),
        ).order_by(CertAttachment.sort_order, CertAttachment.id)
    )).scalars().all()
    return success(data=[{
        "id": item.id,
        "title": item.title,
        "file_url": item.file_url,
        "file_size": item.file_size,
    } for item in rows])
