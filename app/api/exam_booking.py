"""用户端 — H3C 考场预约 API。"""
from datetime import date

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db
from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.common import APIResponse, success
from app.services.exam_booking import ExamBookingService
from app.domain.exam.src.index import ExamBooking, ExamSchedule
from app.domain.h3c.src.index import H3cRegistration
from app.port.exceptions import NotFoundException

router = APIRouter(prefix="/h3c/exam", tags=["H3C考场预约"])


class BookRequest(BaseModel):
    slot_id: int
    exam_date: date


@router.get("/schedules", response_model=APIResponse[list], summary="我的可预约计划")
async def my_schedules(
    registration_id: int = Query(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """只返回 published 且我已审核通过的。"""
    registration = await db.get(H3cRegistration, registration_id)
    if registration is None or registration.user_id != current_user.id:
        raise NotFoundException("报名记录")
    if registration.status not in ("approved", "final_approved"):
        return success(data=[])

    rows = (await db.execute(
        select(ExamSchedule).where(
            ExamSchedule.status == "published",
            ExamSchedule.vendor == "h3c",
        ).order_by(ExamSchedule.open_date_start)
    )).scalars().all()
    return success(data=[{
        "id": s.id, "name": s.name,
        "open_date_start": s.open_date_start.isoformat(),
        "open_date_end": s.open_date_end.isoformat(),
    } for s in rows])


@router.get("/schedules/{schedule_id}/dates", response_model=APIResponse[dict], summary="可预约日期列表")
async def get_dates(
    schedule_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dates = await ExamBookingService.get_bookable_dates(db, schedule_id)
    return success(data={"dates": [d.isoformat() for d in dates]})


@router.get("/schedules/{schedule_id}/slots", response_model=APIResponse[dict], summary="某日期的时段及剩余机位")
async def get_slots(
    schedule_id: int = Path(..., ge=1),
    exam_date: date = Query(..., alias="date"),
    subject_id: int = Query(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    slots = await ExamBookingService.get_slots_with_remaining(db, schedule_id, subject_id, exam_date)
    return success(data={"slots": slots})


@router.post("/schedules/{schedule_id}/book", response_model=APIResponse[dict], summary="预约机位")
async def book_seat(
    schedule_id: int = Path(..., ge=1),
    data: BookRequest = ...,
    registration_id: int = Query(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    registration = await db.get(H3cRegistration, registration_id)
    if registration is None or registration.user_id != current_user.id:
        raise NotFoundException("报名记录")
    booking = await ExamBookingService.book(
        db,
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.domain.exam.src.index import ExamTimeSlot, ExamScheduleSubject
    from app.models.cert_product import CertProduct
    rows = (await db.execute(
        select(ExamBooking, ExamTimeSlot, ExamSchedule.name, CertProduct.name)
        .join(ExamTimeSlot, ExamTimeSlot.id == ExamBooking.slot_id)
        .join(ExamSchedule, ExamSchedule.id == ExamBooking.schedule_id)
        .outerjoin(ExamScheduleSubject, ExamScheduleSubject.id == ExamBooking.subject_id)
        .outerjoin(CertProduct, CertProduct.id == ExamScheduleSubject.product_id)
        .where(ExamBooking.registration_id.in_(
            select(H3cRegistration.id).where(H3cRegistration.user_id == current_user.id)
        ))
        .order_by(ExamBooking.exam_date.desc())
    )).all()
    return success(data=[{
        "id": b.id,
        "schedule_name": sname,
        "subject_name": pname or "",
        "slot_label": slot.label,
        "slot_time": slot.start_time.isoformat(),
        "exam_date": b.exam_date.isoformat(),
        "status": b.status,
    } for b, slot, sname, pname in rows])


@router.delete("/bookings/{booking_id}", response_model=APIResponse[dict], summary="取消预约")
async def cancel_booking(
    booking_id: int = Path(..., ge=1),
    registration_id: int = Query(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    registration = await db.get(H3cRegistration, registration_id)
    if registration is None or registration.user_id != current_user.id:
        raise NotFoundException("报名记录")
    booking = await ExamBookingService.cancel(db, booking_id, registration_id)
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
