"""管理端 — 考场开放计划 + 假期管理 API。"""
from datetime import date, datetime, time as dt_time
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db
from app.middleware.auth import require_any_permission
from app.schemas.common import APIResponse, success
from app.domain.exam.src.index import (
    ExamSchedule, ExamScheduleSubject, ExamTimeSlot, ExamSeatQuota, Holiday,
    ExamBooking,
)
from app.port.exceptions import BusinessException, NotFoundException
from app.models.cert_product import CertProduct
from app.policy.vendor_scope import (
    manageable_vendors,
    require_vendor_permission,
)

router = APIRouter(prefix="/h3c/exam-schedules", tags=["管理后台-H3C考场管理"])

# H3C 与深信服共用考场管理，任一权限即可维护。
schedule_guard = require_any_permission("h3c:batch_manage", "content:write")


DEFAULT_SLOT_TIMES = ("09:30", "11:30", "14:30", "16:30")


def _parse_time(value: str) -> dt_time:
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise BusinessException("时段格式必须为 HH:MM") from exc
    return parsed.time()


# ============================================================
# Schema
# ============================================================


class ScheduleUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    open_date_start: date | None = None
    open_date_end: date | None = None
    allowed_weekdays: list[int] | None = None
    exam_location: str | None = Field(None, min_length=1, max_length=256)
    pre_exam_notice: str | None = None


class TimeSlotCreate(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=32)
    start_time: str = Field(..., description="HH:MM 格式")


class SeatQuotaCreate(BaseModel):
    product_id: int
    slot_start: str = Field(..., description="HH:MM 格式")
    total_seats: int = Field(..., ge=0)


class SharedQuotaCreate(BaseModel):
    slot_start: str = Field(..., description="HH:MM 格式")
    total_seats: int = Field(..., ge=0)


class ScheduleCreate(BaseModel):
    vendor: str = Field("h3c", pattern="^(h3c|sangfor)$", description="认证厂商")
    name: str = Field(..., min_length=1, max_length=128)
    open_date_start: date
    open_date_end: date
    allowed_weekdays: list[int] = Field(default=[4], description="Python weekday()，4=周五")
    exam_location: str | None = Field(None, max_length=256)
    pre_exam_notice: str | None = Field(None, max_length=5000)
    product_ids: list[int] = Field(default_factory=list)
    time_slots: list[TimeSlotCreate] | None = None
    default_quota: int = Field(5, ge=0)
    seat_quotas: list[SeatQuotaCreate] | None = None
    shared_quotas: list[SharedQuotaCreate] | None = None
    publish: bool = False


class SeatQuotaItem(BaseModel):
    subject_id: int
    slot_id: int
    total_seats: int = Field(..., ge=0)


class SharedSeatQuotaItem(BaseModel):
    slot_id: int
    total_seats: int = Field(..., ge=0)


class SeatQuotaBatch(BaseModel):
    quotas: list[SeatQuotaItem] = Field(default_factory=list)
    shared_quotas: list[SharedSeatQuotaItem] = Field(default_factory=list)


class HolidayCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    holiday_date: date
    is_annual: bool = False


# ============================================================
# 考场计划 CRUD
# ============================================================

@router.get("", response_model=APIResponse[list], summary="计划列表")
async def list_schedules(
    vendor: Annotated[str | None, Query(pattern="^(h3c|sangfor)$")] = None,
    page: Annotated[int | None, Query(ge=1)] = None,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    _admin=Depends(schedule_guard),
    db: AsyncSession = Depends(get_db),
):
    allowed_vendors = manageable_vendors(_admin)
    if vendor:
        require_vendor_permission(_admin, vendor)
    conditions = [ExamSchedule.vendor == vendor] if vendor else [
        ExamSchedule.vendor.in_(allowed_vendors)
    ]
    stmt = select(ExamSchedule).where(*conditions)
    stmt = stmt.order_by(ExamSchedule.id.desc())
    total = None
    if page is not None:
        # 直接对 id 计数，避免对带 ORDER BY / JSON 列的子查询再聚合。
        total = await db.scalar(
            select(func.count(ExamSchedule.id)).where(*conditions)
        )
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()
    items = [{
        "id": s.id, "name": s.name, "vendor": s.vendor,
        "open_date_start": s.open_date_start.isoformat(),
        "open_date_end": s.open_date_end.isoformat(),
        "allowed_weekdays": s.allowed_weekdays,
        "exam_location": s.exam_location,
        "status": s.status, "published_at": s.published_at,
    } for s in rows]
    if total is None:
        return success(data=items)
    return success(data={
        "items": items, "total": total, "page": page, "page_size": page_size,
    })


@router.post("", response_model=APIResponse[dict], summary="创建计划")
async def create_schedule(
    data: ScheduleCreate,
    _admin=Depends(schedule_guard),
    db: AsyncSession = Depends(get_db),
):
    if data.open_date_start > data.open_date_end:
        raise BusinessException("开始日期不能晚于结束日期")
    require_vendor_permission(_admin, data.vendor)

    allowed_weekdays = sorted(set(data.allowed_weekdays))
    if not allowed_weekdays or any(day not in range(0, 7) for day in allowed_weekdays):
        raise BusinessException("可用星期必须为 0-6 中的至少一天")

    # Keep the original minimal create request valid for rolling deployments.
    # The new Admin always sends the complete composite payload.
    legacy_basic_create = (
        not data.product_ids
        and data.time_slots is None
        and data.seat_quotas is None
        and data.shared_quotas is None
        and not data.publish
    )
    product_ids = list(dict.fromkeys(data.product_ids))
    if not product_ids and not legacy_basic_create:
        raise BusinessException("请至少选择一个考试科目")
    products_by_id: dict[int, CertProduct] = {}
    if product_ids:
        products = (await db.execute(
            select(CertProduct).where(
                CertProduct.id.in_(product_ids),
                CertProduct.type == data.vendor,
                CertProduct.is_active.is_(True),
            )
        )).scalars().all()
        products_by_id.update({product.id: product for product in products})
        missing_products = [
            product_id for product_id in product_ids if product_id not in products_by_id
        ]
        if missing_products:
            raise BusinessException(
                f"认证产品不存在或未上架：{', '.join(str(item) for item in missing_products)}"
            )

    slot_inputs = data.time_slots
    if slot_inputs is None and not legacy_basic_create:
        slot_inputs = [TimeSlotCreate(start_time=item) for item in DEFAULT_SLOT_TIMES]
    if not slot_inputs and not legacy_basic_create:
        raise BusinessException("请至少添加一个考试时段")

    parsed_slots: list[tuple[dt_time, str]] = []
    seen_slots: set[dt_time] = set()
    for item in slot_inputs or []:
        start_time = _parse_time(item.start_time)
        if start_time in seen_slots:
            raise BusinessException(f"考试时段重复：{item.start_time}")
        seen_slots.add(start_time)
        label = item.label or f"{start_time.strftime('%H:%M')} 场"
        parsed_slots.append((start_time, label))

    explicit_quotas: dict[tuple[int, dt_time], int] = {}
    for item in data.seat_quotas or []:
        if item.product_id not in products_by_id:
            raise BusinessException(f"配额科目不属于该考场计划：{item.product_id}")
        start_time = _parse_time(item.slot_start)
        if start_time not in seen_slots:
            raise BusinessException(f"配额时段不属于该考场计划：{item.slot_start}")
        key = (item.product_id, start_time)
        if key in explicit_quotas:
            raise BusinessException(f"配额重复：{item.product_id} {item.slot_start}")
        explicit_quotas[key] = item.total_seats

    explicit_shared_quotas: dict[dt_time, int] = {}
    for item in data.shared_quotas or []:
        start_time = _parse_time(item.slot_start)
        if start_time not in seen_slots:
            raise BusinessException(f"共享配额时段不属于该考场计划：{item.slot_start}")
        if start_time in explicit_shared_quotas:
            raise BusinessException(f"共享配额重复：{item.slot_start}")
        explicit_shared_quotas[start_time] = item.total_seats

    # Old product-matrix payloads remain accepted, but their independent
    # capacities are normalized to one conservative shared pool per time slot.
    if data.shared_quotas is None and explicit_quotas:
        for product_id in product_ids:
            for start_time in seen_slots:
                value = explicit_quotas.get(
                    (product_id, start_time), data.default_quota
                )
                previous = explicit_shared_quotas.get(start_time)
                explicit_shared_quotas[start_time] = (
                    value if previous is None else max(previous, value)
                )

    schedule = ExamSchedule(
        vendor=data.vendor,
        name=data.name,
        open_date_start=data.open_date_start,
        open_date_end=data.open_date_end,
        allowed_weekdays=allowed_weekdays,
        exam_location=data.exam_location,
        pre_exam_notice=data.pre_exam_notice,
        status="published" if data.publish else "draft",
        published_at=date.today() if data.publish else None,
    )
    db.add(schedule)
    await db.flush()

    if not legacy_basic_create:
        subjects_by_product: dict[int, ExamScheduleSubject] = {}
        for product_id in product_ids:
            subject = ExamScheduleSubject(schedule_id=schedule.id, product_id=product_id)
            db.add(subject)
            subjects_by_product[product_id] = subject
        await db.flush()

        slots_by_time: dict[dt_time, ExamTimeSlot] = {}
        for sort_order, (start_time, label) in enumerate(parsed_slots):
            slot = ExamTimeSlot(
                schedule_id=schedule.id,
                label=label,
                start_time=start_time,
                sort_order=sort_order,
            )
            db.add(slot)
            slots_by_time[start_time] = slot
        await db.flush()

        # 深信服不设机位配额：考生可自由选择时段。
        if data.vendor != "sangfor":
            for start_time, _ in parsed_slots:
                db.add(ExamSeatQuota(
                    schedule_id=schedule.id,
                    subject_id=None,
                    slot_id=slots_by_time[start_time].id,
                    total_seats=explicit_shared_quotas.get(
                        start_time, data.default_quota
                    ),
                ))

    await db.commit()

    return success(
        data={"id": schedule.id, "status": schedule.status},
        message="考场计划已创建并发布" if data.publish else "考场计划已创建",
    )


@router.get("/{schedule_id}", response_model=APIResponse[dict], summary="计划详情")
async def get_schedule(
    schedule_id: int,
    _admin=Depends(schedule_guard),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(ExamSchedule, schedule_id)
    if s is None:
        raise NotFoundException("考场计划")
    require_vendor_permission(_admin, s.vendor)

    subjects = (await db.execute(
        select(ExamScheduleSubject, CertProduct)
        .join(CertProduct, CertProduct.id == ExamScheduleSubject.product_id)
        .where(ExamScheduleSubject.schedule_id == schedule_id)
    )).all()
    slots = (await db.execute(
        select(ExamTimeSlot).where(ExamTimeSlot.schedule_id == schedule_id)
        .order_by(ExamTimeSlot.sort_order)
    )).scalars().all()
    slots_by_id = {slot.id: slot for slot in slots}
    quotas = (await db.execute(
        select(ExamSeatQuota).where(
            ExamSeatQuota.schedule_id == schedule_id,
            ExamSeatQuota.subject_id.is_(None),
        ).order_by(ExamSeatQuota.slot_id)
    )).scalars().all()
    # P2 #8: 计算各配额的已约人数
    booking_counts = dict((await db.execute(
        select(ExamBooking.slot_id, func.count())
        .where(
            ExamBooking.schedule_id == schedule_id,
            ExamBooking.status == "booked",
        )
        .group_by(ExamBooking.slot_id)
    )).all())

    return success(data={
        "id": s.id, "name": s.name, "vendor": s.vendor,
        "open_date_start": s.open_date_start.isoformat(),
        "open_date_end": s.open_date_end.isoformat(),
        "allowed_weekdays": s.allowed_weekdays,
        "exam_location": s.exam_location,
        "pre_exam_notice": s.pre_exam_notice,
        "status": s.status,
        "subjects": [{"id": ss.id, "product_id": ss.product_id, "product_name": p.chinese_name or p.name} for ss, p in subjects],
        "time_slots": [{"id": t.id, "label": t.label, "start_time": t.start_time.isoformat()} for t in slots],
        "seat_quotas": [{
            "slot_id": q.slot_id,
            "slot_start": slots_by_id[q.slot_id].start_time.strftime("%H:%M"),
            "total_seats": q.total_seats,
            "booked": booking_counts.get(q.slot_id, 0),
            "remaining": -1 if q.total_seats == 0 else max(0, q.total_seats - booking_counts.get(q.slot_id, 0)),
        } for q in quotas],
    })


@router.put("/{schedule_id}", response_model=APIResponse[dict], summary="更新计划（draft）")
async def update_schedule(
    schedule_id: int,
    data: ScheduleUpdate,
    _admin=Depends(schedule_guard),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(ExamSchedule, schedule_id, with_for_update=True)
    if s is None:
        raise NotFoundException("考场计划")
    require_vendor_permission(_admin, s.vendor)
    if s.status != "draft":
        raise BusinessException("仅草稿状态可编辑")
    for field in ("name", "open_date_start", "open_date_end", "allowed_weekdays", "exam_location", "pre_exam_notice"):
        val = getattr(data, field)
        if val is not None:
            setattr(s, field, val)
    await db.commit()
    return success(message="已更新")


@router.post("/{schedule_id}/publish", response_model=APIResponse[dict], summary="发布计划")
async def publish_schedule(
    schedule_id: int,
    _admin=Depends(schedule_guard),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(ExamSchedule, schedule_id, with_for_update=True)
    if s is None:
        raise NotFoundException("考场计划")
    require_vendor_permission(_admin, s.vendor)
    if s.status != "draft":
        raise BusinessException("仅草稿可发布")
    subject_count = await db.scalar(
        select(func.count()).select_from(ExamScheduleSubject).where(
            ExamScheduleSubject.schedule_id == schedule_id
        )
    )
    slot_count = await db.scalar(
        select(func.count()).select_from(ExamTimeSlot).where(
            ExamTimeSlot.schedule_id == schedule_id
        )
    )
    if not subject_count or not slot_count:
        raise BusinessException("发布前必须配置至少一个科目和时段")
    s.status = "published"
    s.published_at = date.today()
    await db.commit()
    return success(message="考场计划已发布")


@router.post("/{schedule_id}/close", response_model=APIResponse[dict], summary="关闭计划")
async def close_schedule(
    schedule_id: int,
    _admin=Depends(schedule_guard),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(ExamSchedule, schedule_id, with_for_update=True)
    if s is None:
        raise NotFoundException("考场计划")
    require_vendor_permission(_admin, s.vendor)
    if s.status != "published":
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
    _admin=Depends(schedule_guard),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(ExamSchedule, schedule_id)
    if s is None:
        raise NotFoundException("考场计划")
    require_vendor_permission(_admin, s.vendor)
    if s.status != "draft":
        raise BusinessException("仅草稿可添加科目")
    product = await db.get(CertProduct, product_id)
    if product is None or product.type != s.vendor or not product.is_active:
        raise BusinessException(f"仅可添加已上架的 {s.vendor} 认证产品")
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
    _admin=Depends(schedule_guard),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(ExamSchedule, schedule_id)
    if s is None:
        raise NotFoundException("考场计划")
    require_vendor_permission(_admin, s.vendor)
    if s.status != "draft":
        raise BusinessException("仅草稿可添加时段")
    t = _parse_time(data.start_time)
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
    db.add(ExamTimeSlot(
        schedule_id=schedule_id,
        label=data.label or f"{data.start_time} 场",
        start_time=t,
        sort_order=len(count),
    ))
    await db.commit()
    return success(message="时段已添加")


@router.put("/{schedule_id}/seat-quotas", response_model=APIResponse[dict], summary="批量设置机位配额")
async def set_seat_quotas(
    schedule_id: int,
    data: SeatQuotaBatch,
    _admin=Depends(schedule_guard),
    db: AsyncSession = Depends(get_db),
):
    s = await db.get(ExamSchedule, schedule_id, with_for_update=True)
    if s is None:
        raise NotFoundException("考场计划")
    require_vendor_permission(_admin, s.vendor)
    if s.status == "closed":
        raise BusinessException("已关闭计划不可设置配额")
    if s.vendor == "sangfor":
        raise BusinessException("深信服考场不设机位配额，考生可自由选择时段")

    slot_ids = set((await db.scalars(
        select(ExamTimeSlot.id).where(ExamTimeSlot.schedule_id == schedule_id)
    )).all())
    booking_counts = dict((await db.execute(
        select(ExamBooking.slot_id, func.count())
        .where(
            ExamBooking.schedule_id == schedule_id,
            ExamBooking.status == "booked",
        )
        .group_by(ExamBooking.slot_id)
    )).all())

    if data.shared_quotas:
        shared_values: dict[int, int] = {}
        for item in data.shared_quotas:
            if item.slot_id not in slot_ids:
                raise BusinessException("配额时段不属于该考场计划")
            if item.slot_id in shared_values:
                raise BusinessException("配额重复，同一时段只能设置一个共享池")
            shared_values[item.slot_id] = item.total_seats
    elif data.quotas:
        # Legacy clients still submit a subject matrix. Normalize it to the
        # maximum requested capacity for each slot, matching creation behavior.
        legacy_values: dict[int, list[int]] = {}
        for item in data.quotas:
            if item.slot_id not in slot_ids:
                raise BusinessException("配额时段不属于该考场计划")
            legacy_values.setdefault(item.slot_id, []).append(item.total_seats)
        shared_values = {
            slot_id: max(values) for slot_id, values in legacy_values.items()
        }
    else:
        raise BusinessException("请至少设置一个时段的共享机位")

    for slot_id, total_seats in shared_values.items():
        booked = booking_counts.get(slot_id, 0)
        if s.status == "published" and total_seats != 0 and total_seats < booked:
            raise BusinessException(
                f"配额不能小于该时段已预约人数（已约 {booked} 人）；如需不限量请设为 0"
            )
        existing = await db.scalar(
            select(ExamSeatQuota).where(
                ExamSeatQuota.schedule_id == schedule_id,
                ExamSeatQuota.slot_id == slot_id,
                ExamSeatQuota.subject_id.is_(None),
            )
        )
        if existing:
            existing.total_seats = total_seats
        else:
            db.add(ExamSeatQuota(
                schedule_id=schedule_id,
                subject_id=None,
                slot_id=slot_id,
                total_seats=total_seats,
            ))
    await db.commit()
    return success(message="机位配额已保存")


# ============================================================
# 假期管理（全局）
# ============================================================

holiday_router = APIRouter(prefix="/holidays", tags=["管理后台-假期管理"])


@holiday_router.get("", response_model=APIResponse[list], summary="假期列表")
async def list_holidays(
    _admin=Depends(schedule_guard),
    db: AsyncSession = Depends(get_db),
):
    require_vendor_permission(_admin, "h3c")
    rows = (await db.execute(select(Holiday).order_by(Holiday.holiday_date))).scalars().all()
    return success(data=[{
        "id": h.id, "name": h.name,
        "holiday_date": h.holiday_date.isoformat(),
        "is_annual": h.is_annual,
    } for h in rows])


@holiday_router.post("", response_model=APIResponse[dict], summary="新增假期")
async def create_holiday(
    data: HolidayCreate,
    _admin=Depends(schedule_guard),
    db: AsyncSession = Depends(get_db),
):
    require_vendor_permission(_admin, "h3c")
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
    _admin=Depends(schedule_guard),
    db: AsyncSession = Depends(get_db),
):
    require_vendor_permission(_admin, "h3c")
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
    _admin=Depends(schedule_guard),
    db: AsyncSession = Depends(get_db),
):
    schedule = await db.get(ExamSchedule, schedule_id)
    if schedule is None:
        raise NotFoundException("考场计划")
    require_vendor_permission(_admin, schedule.vendor)
    from app.domain.exam.src.index import ExamBooking, ExamTimeSlot, ExamScheduleSubject
    from app.models.cert_product import CertProduct
    from app.domain.h3c.src.index import H3cRegistration
    from app.domain.order.src.index import Order
    from sqlalchemy import and_

    stmt = (
        select(ExamBooking, ExamTimeSlot, CertProduct, H3cRegistration, Order)
        .join(ExamTimeSlot, ExamTimeSlot.id == ExamBooking.slot_id)
        .outerjoin(ExamScheduleSubject, ExamScheduleSubject.id == ExamBooking.subject_id)
        .outerjoin(CertProduct, CertProduct.id == ExamScheduleSubject.product_id)
        .outerjoin(H3cRegistration, and_(
            ExamBooking.vendor == "h3c",
            H3cRegistration.id == ExamBooking.registration_id,
        ))
        .outerjoin(Order, and_(
            ExamBooking.vendor == "sangfor",
            Order.id == ExamBooking.registration_id,
        ))
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
        "vendor": b.vendor,
        "candidate_name": (
            (reg.candidate_snapshot or {}).get("name", "")
            if reg is not None
            else (order.candidate_name or "")
        ),
        "subject": (p.chinese_name or p.name) if p else "",
        "slot_label": slot.label,
        "slot_time": slot.start_time.isoformat(),
        "exam_date": b.exam_date.isoformat(),
        "status": b.status,
    } for b, slot, p, reg, order in rows])


@router.post("/{schedule_id}/bookings/{booking_id}/no-show", response_model=APIResponse[dict], summary="标记缺考")
async def mark_no_show(
    schedule_id: int,
    booking_id: int,
    _admin=Depends(schedule_guard),
    db: AsyncSession = Depends(get_db),
):
    from app.domain.exam.src.index import ExamBooking
    schedule = await db.get(ExamSchedule, schedule_id)
    if schedule is None:
        raise NotFoundException("考场计划")
    require_vendor_permission(_admin, schedule.vendor)
    booking = await db.get(ExamBooking, booking_id)
    if booking is None or booking.schedule_id != schedule_id:
        raise NotFoundException("预约记录")
    if booking.status != "booked":
        raise BusinessException("仅已预约的记录可标记缺考")
    booking.status = "no_show"
    await db.commit()
    return success(message="已标记缺考")
