"""管理端 — 考场开放计划 + 假期管理 API。"""
from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db
from app.middleware.auth import require_permission
from app.schemas.common import APIResponse, success
from app.domain.exam.src.index import (
    ExamSchedule, ExamScheduleSubject, ExamTimeSlot, ExamSeatQuota, Holiday,
)
from app.port.exceptions import BusinessException, NotFoundException
from app.models.cert_product import CertProduct

router = APIRouter(prefix="/h3c/exam-schedules", tags=["管理后台-H3C考场管理"])


# ============================================================
# Schema
# ============================================================

class ScheduleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    open_date_start: date
    open_date_end: date
    allowed_weekdays: list[int] = Field(default=[4], description="Python weekday()，4=周五")
    pre_exam_notice: str | None = Field(None, max_length=5000)


class ScheduleUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    open_date_start: date | None = None
    open_date_end: date | None = None
    allowed_weekdays: list[int] | None = None
    pre_exam_notice: str | None = None


class TimeSlotCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=32)
    start_time: str = Field(..., description="HH:MM 格式")


class SeatQuotaItem(BaseModel):
    subject_id: int
    slot_id: int
    total_seats: int = Field(..., ge=0)


class SeatQuotaBatch(BaseModel):
    quotas: list[SeatQuotaItem]


class HolidayCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    holiday_date: date
    is_annual: bool = False


# ============================================================
# 考场计划 CRUD
# ============================================================

@router.get("", response_model=APIResponse[list], summary="计划列表")
async def list_schedules(
    _admin=Depends(require_permission("h3c:batch_manage")),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(ExamSchedule).order_by(ExamSchedule.id.desc())
    )).scalars().all()
    return success(data=[{
        "id": s.id, "name": s.name, "vendor": s.vendor,
        "open_date_start": s.open_date_start.isoformat(),
        "open_date_end": s.open_date_end.isoformat(),
        "allowed_weekdays": s.allowed_weekdays,
        "status": s.status, "published_at": s.published_at,
    } for s in rows])


@router.post("", response_model=APIResponse[dict], summary="创建计划")
async def create_schedule(
    data: ScheduleCreate,
    _admin=Depends(require_permission("h3c:batch_manage")),
    db: AsyncSession = Depends(get_db),
):
    if data.open_date_start > data.open_date_end:
        raise BusinessException("开始日期不能晚于结束日期")
    schedule = ExamSchedule(
        name=data.name,
        open_date_start=data.open_date_start,
        open_date_end=data.open_date_end,
        allowed_weekdays=data.allowed_weekdays,
        pre_exam_notice=data.pre_exam_notice,
    )
    db.add(schedule)
    await db.commit()
    return success(data={"id": schedule.id}, message="考场计划已创建")


@router.get("/{schedule_id}", response_model=APIResponse[dict], summary="计划详情")
async def get_schedule(
    schedule_id: int,
    _admin=Depends(require_permission("h3c:batch_manage")),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(ExamSchedule, schedule_id)
    if s is None:
        raise NotFoundException("考场计划")
        subjects = (await db.execute(
        select(ExamScheduleSubject, CertProduct)
        .join(CertProduct, CertProduct.id == ExamScheduleSubject.product_id)
        .where(ExamScheduleSubject.schedule_id == schedule_id)
    )).all()
    slots = (await db.execute(
        select(ExamTimeSlot).where(ExamTimeSlot.schedule_id == schedule_id)
        .order_by(ExamTimeSlot.sort_order)
    )).scalars().all()
    quotas = (await db.execute(
        select(ExamSeatQuota).where(ExamSeatQuota.schedule_id == schedule_id)
    )).scalars().all()
    # P2 #8: 计算各配额的已约人数
    from app.domain.exam.src.index import ExamBooking
    from sqlalchemy import func
    booking_counts = dict((await db.execute(
        select(ExamBooking.subject_id, ExamBooking.slot_id, func.count())
        .where(
            ExamBooking.schedule_id == schedule_id,
            ExamBooking.status == "booked",
        )
        .group_by(ExamBooking.subject_id, ExamBooking.slot_id)
    )).all())

    return success(data={
        "id": s.id, "name": s.name, "vendor": s.vendor,
        "open_date_start": s.open_date_start.isoformat(),
        "open_date_end": s.open_date_end.isoformat(),
        "allowed_weekdays": s.allowed_weekdays,
        "pre_exam_notice": s.pre_exam_notice,
        "status": s.status,
        "subjects": [{"id": ss.id, "product_id": ss.product_id, "product_name": p.name} for ss, p in subjects],
        "time_slots": [{"id": t.id, "label": t.label, "start_time": t.start_time.isoformat()} for t in slots],
        "seat_quotas": [{
            "subject_id": q.subject_id,
            "slot_id": q.slot_id,
            "total_seats": q.total_seats,
            "booked": booking_counts.get((q.subject_id, q.slot_id), 0),
            "remaining": -1 if q.total_seats == 0 else max(0, q.total_seats - booking_counts.get((q.subject_id, q.slot_id), 0)),
        } for q in quotas],
    })


@router.put("/{schedule_id}", response_model=APIResponse[dict], summary="更新计划（draft）")
async def update_schedule(
    schedule_id: int,
    data: ScheduleUpdate,
    _admin=Depends(require_permission("h3c:batch_manage")),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(ExamSchedule, schedule_id, with_for_update=True)
    if s is None:
        raise NotFoundException("考场计划")
    if s.status != "draft":
        raise BusinessException("仅草稿状态可编辑")
    for field in ("name", "open_date_start", "open_date_end", "allowed_weekdays", "pre_exam_notice"):
        val = getattr(data, field)
        if val is not None:
            setattr(s, field, val)
    await db.commit()
    return success(message="已更新")


@router.post("/{schedule_id}/publish", response_model=APIResponse[dict], summary="发布计划")
async def publish_schedule(
    schedule_id: int,
    _admin=Depends(require_permission("h3c:batch_manage")),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(ExamSchedule, schedule_id, with_for_update=True)
    if s is None or s.status != "draft":
        raise BusinessException("仅草稿可发布")
    s.status = "published"
    s.published_at = date.today()
    await db.commit()
    return success(message="考场计划已发布")


@router.post("/{schedule_id}/close", response_model=APIResponse[dict], summary="关闭计划")
async def close_schedule(
    schedule_id: int,
    _admin=Depends(require_permission("h3c:batch_manage")),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(ExamSchedule, schedule_id, with_for_update=True)
    if s is None or s.status != "published":
        raise BusinessException("仅已发布可关闭")
    s.status = "closed"
    await db.commit()
    return success(message="考场计划已关闭")


# ============================================================
# 计划内管理
# ============================================================

@router.post("/{schedule_id}/subjects", response_model=APIResponse[dict], summary="添加科目")
async def add_subject(
    schedule_id: int,
    product_id: int = Query(..., ge=1),
    _admin=Depends(require_permission("h3c:batch_manage")),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(ExamSchedule, schedule_id)
    if s is None or s.status != "draft":
        raise BusinessException("仅草稿可添加科目")
    product = await db.get(CertProduct, product_id)
    if product is None or product.type != "h3c":
        raise BusinessException("仅可添加 H3C 产品")
    existing = await db.scalar(
        select(ExamScheduleSubject.id).where(
            ExamScheduleSubject.schedule_id == schedule_id,
            ExamScheduleSubject.product_id == product_id,
        )
    )
    if existing:
        raise BusinessException("该科目已在计划中")
    db.add(ExamScheduleSubject(schedule_id=schedule_id, product_id=product_id))
    await db.commit()
    return success(message="科目已添加")


@router.post("/{schedule_id}/time-slots", response_model=APIResponse[dict], summary="添加时段")
async def add_time_slot(
    schedule_id: int,
    data: TimeSlotCreate,
    _admin=Depends(require_permission("h3c:batch_manage")),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(ExamSchedule, schedule_id)
    if s is None or s.status != "draft":
        raise BusinessException("仅草稿可添加时段")
    from datetime import time as dt_time
    parts = data.start_time.split(":")
    t = dt_time(int(parts[0]), int(parts[1]))
    existing = await db.scalar(
        select(ExamTimeSlot.id).where(
            ExamTimeSlot.schedule_id == schedule_id,
            ExamTimeSlot.start_time == t,
        )
    )
    if existing:
        raise BusinessException("该时段已存在")
    count = (await db.execute(
        select(ExamTimeSlot).where(ExamTimeSlot.schedule_id == schedule_id)
    )).scalars().all()
    db.add(ExamTimeSlot(schedule_id=schedule_id, label=data.label, start_time=t, sort_order=len(count)))
    await db.commit()
    return success(message="时段已添加")


@router.put("/{schedule_id}/seat-quotas", response_model=APIResponse[dict], summary="批量设置机位配额")
async def set_seat_quotas(
    schedule_id: int,
    data: SeatQuotaBatch,
    _admin=Depends(require_permission("h3c:batch_manage")),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(ExamSchedule, schedule_id)
    if s is None:
        raise NotFoundException("考场计划")
    for item in data.quotas:
        existing = await db.scalar(
            select(ExamSeatQuota).where(
                ExamSeatQuota.schedule_id == schedule_id,
                ExamSeatQuota.subject_id == item.subject_id,
                ExamSeatQuota.slot_id == item.slot_id,
            )
        )
        if existing:
            existing.total_seats = item.total_seats
        else:
            db.add(ExamSeatQuota(
                schedule_id=schedule_id,
                subject_id=item.subject_id,
                slot_id=item.slot_id,
                total_seats=item.total_seats,
            ))
    await db.commit()
    return success(message="机位配额已保存")


# ============================================================
# 假期管理（全局）
# ============================================================

holiday_router = APIRouter(prefix="/holidays", tags=["管理后台-假期管理"])


@holiday_router.get("", response_model=APIResponse[list], summary="假期列表")
async def list_holidays(
    _admin=Depends(require_permission("h3c:batch_manage")),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(select(Holiday).order_by(Holiday.holiday_date))).scalars().all()
    return success(data=[{
        "id": h.id, "name": h.name,
        "holiday_date": h.holiday_date.isoformat(),
        "is_annual": h.is_annual,
    } for h in rows])


@holiday_router.post("", response_model=APIResponse[dict], summary="新增假期")
async def create_holiday(
    data: HolidayCreate,
    _admin=Depends(require_permission("h3c:batch_manage")),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.scalar(
        select(Holiday.id).where(Holiday.holiday_date == data.holiday_date)
    )
    if existing:
        raise BusinessException("该日期已存在假期记录")
    h = Holiday(name=data.name, holiday_date=data.holiday_date, is_annual=data.is_annual)
    db.add(h)
    await db.commit()
    return success(data={"id": h.id}, message="假期已添加")


@holiday_router.delete("/{holiday_id}", response_model=APIResponse[dict], summary="删除假期")
async def delete_holiday(
    holiday_id: int,
    _admin=Depends(require_permission("h3c:batch_manage")),
    db: AsyncSession = Depends(get_db),
):
    h = await db.get(Holiday, holiday_id)
    if h is None:
        raise NotFoundException("假期记录")
    await db.delete(h)
    await db.commit()
    return success(message="假期已删除")


# ============================================================
# 预约管理
# ============================================================

@router.get("/{schedule_id}/bookings", response_model=APIResponse[list], summary="预约总览")
async def list_bookings(
    schedule_id: int,
    exam_date: date | None = Query(None, alias="date"),
    slot_id: int | None = Query(None, ge=1),
    _admin=Depends(require_permission("h3c:batch_manage")),
    db: AsyncSession = Depends(get_db),
):
    from app.domain.exam.src.index import ExamBooking, ExamTimeSlot, ExamScheduleSubject
    from app.models.cert_product import CertProduct
    from app.domain.h3c.src.index import H3cRegistration

    stmt = (
        select(ExamBooking, ExamTimeSlot, CertProduct, H3cRegistration)
        .join(ExamTimeSlot, ExamTimeSlot.id == ExamBooking.slot_id)
        .outerjoin(ExamScheduleSubject, ExamScheduleSubject.id == ExamBooking.subject_id)
        .outerjoin(CertProduct, CertProduct.id == ExamScheduleSubject.product_id)
        .join(H3cRegistration, H3cRegistration.id == ExamBooking.registration_id)
        .where(ExamBooking.schedule_id == schedule_id)
        .order_by(ExamBooking.exam_date, ExamTimeSlot.start_time)
    )
    if exam_date:
        stmt = stmt.where(ExamBooking.exam_date == exam_date)
    if slot_id:
        stmt = stmt.where(ExamBooking.slot_id == slot_id)

    rows = (await db.execute(stmt)).all()
    return success(data=[{
        "id": b.id,
        "registration_id": b.registration_id,
        "candidate_name": reg.candidate_snapshot.get("name", "") if reg.candidate_snapshot else "",
        "subject": p.name if p else "",
        "slot_label": slot.label,
        "slot_time": slot.start_time.isoformat(),
        "exam_date": b.exam_date.isoformat(),
        "status": b.status,
    } for b, slot, p, reg in rows])


@router.post("/{schedule_id}/bookings/{booking_id}/no-show", response_model=APIResponse[dict], summary="标记缺考")
async def mark_no_show(
    schedule_id: int,
    booking_id: int,
    _admin=Depends(require_permission("h3c:batch_manage")),
    db: AsyncSession = Depends(get_db),
):
    from app.domain.exam.src.index import ExamBooking
    booking = await db.get(ExamBooking, booking_id)
    if booking is None or booking.schedule_id != schedule_id:
        raise NotFoundException("预约记录")
    if booking.status != "booked":
        raise BusinessException("仅已预约的记录可标记缺考")
    booking.status = "no_show"
    await db.commit()
    return success(message="已标记缺考")
