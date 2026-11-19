"""H3C 分时预约考场 — 机位判定/预约/取消 Service。"""
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.exam.src.index import (
    ExamBooking,
    ExamSchedule,
    ExamScheduleSubject,
    ExamSeatQuota,
    ExamTimeSlot,
    Holiday,
)
from app.services.clock import db_now_utc
from app.services.vendor_registration import normalize_vendor, resolve_registration
from app.port.exceptions import BusinessException, ConflictException, NotFoundException


class ExamBookingService:
    """机位管控核心逻辑。"""

    @staticmethod
    async def _get_shared_quota(
        db: AsyncSession,
        schedule_id: int,
        slot_id: int,
        *,
        for_update: bool = False,
    ) -> ExamSeatQuota | None:
        statement = select(ExamSeatQuota).where(
            ExamSeatQuota.schedule_id == schedule_id,
            ExamSeatQuota.slot_id == slot_id,
            ExamSeatQuota.subject_id.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        return await db.scalar(statement)

    @staticmethod
    async def cancel_active_for(
        db: AsyncSession, vendor: str, registration_id: int
    ) -> int:
        """退款审批/完成时释放该报名的有效预约（同事务内调用）。"""
        rows = (await db.scalars(
            select(ExamBooking)
            .where(
                ExamBooking.vendor == vendor,
                ExamBooking.registration_id == registration_id,
                ExamBooking.status == "booked",
            )
            .with_for_update()
        )).all()
        for booking in rows:
            booking.status = "cancelled"
        return len(rows)

    # ------------------------------------------------------------------
    # 可预约日期
    # ------------------------------------------------------------------

    @staticmethod
    async def get_bookable_dates(
        db: AsyncSession, schedule_id: int
    ) -> list[date]:
        """获取某计划的可预约日期列表（仅 allowed_weekdays，排除假期）。"""
        schedule = await db.get(ExamSchedule, schedule_id)
        if schedule is None:
            raise NotFoundException("考场计划")
        if schedule.status != "published":
            raise BusinessException("该考场计划未开放")

        holidays = set(
            (await db.execute(select(Holiday.holiday_date))).scalars().all()
        )

        dates: list[date] = []
        current = schedule.open_date_start
        end = schedule.open_date_end
        while current <= end:
            if current.weekday() in schedule.allowed_weekdays and current not in holidays:
                dates.append(current)
            current += timedelta(days=1)
        return dates

    # ------------------------------------------------------------------
    # 时段及剩余机位
    # ------------------------------------------------------------------

    @staticmethod
    async def get_slots_with_remaining(
        db: AsyncSession,
        schedule_id: int,
        subject_id: int,
        exam_date: date,
        *,
        vendor: str | None = None,
    ) -> list[dict]:
        """获取某日期的时段及共享池剩余机位。"""
        vendor = normalize_vendor(vendor)
        schedule = await db.get(ExamSchedule, schedule_id)
        if schedule is None or schedule.status != "published":
            raise BusinessException("该考场计划未开放")
        if schedule.vendor != vendor:
            raise BusinessException("该考场计划不属于当前认证厂商")

        # 校验日期合法性
        if exam_date.weekday() not in schedule.allowed_weekdays:
            raise BusinessException("该日期不可预约")

        slots = (await db.execute(
            select(ExamTimeSlot)
            .where(ExamTimeSlot.schedule_id == schedule_id)
            .order_by(ExamTimeSlot.sort_order, ExamTimeSlot.start_time)
        )).scalars().all()

        subject_exists = await db.scalar(
            select(ExamScheduleSubject.id).where(
                ExamScheduleSubject.schedule_id == schedule_id,
                ExamScheduleSubject.id == subject_id,
            )
        )
        if subject_exists is None:
            raise BusinessException("该科目不在此考场计划中")

        booked_by_slot: dict[int, int] = {}
        if vendor != "sangfor":
            # 共享池按「计划 + 时段」计算，不区分考试日期（与管理端配额语义一致）。
            booked_by_slot = dict((await db.execute(
                select(ExamBooking.slot_id, func.count())
                .where(
                    ExamBooking.schedule_id == schedule_id,
                    ExamBooking.status == "booked",
                )
                .group_by(ExamBooking.slot_id)
            )).all())

        result = []
        for slot in slots:
            if vendor == "sangfor":
                # Sangfor 不限机位，时段余量对外始终显示"不限"。
                result.append({
                    "slot_id": slot.id,
                    "label": slot.label,
                    "start_time": slot.start_time.isoformat(),
                    "remaining": -1,
                })
                continue
            quota = await ExamBookingService._get_shared_quota(
                db, schedule_id, slot.id
            )
            if quota is None:
                remaining = -1  # 无配额记录 = 不限
            elif quota.total_seats == 0:
                remaining = -1  # 0 = 不限
            else:
                booked = booked_by_slot.get(slot.id, 0)
                remaining = max(0, quota.total_seats - booked)

            result.append({
                "slot_id": slot.id,
                "label": slot.label,
                "start_time": slot.start_time.isoformat(),
                "remaining": remaining,
            })
        return result

    # ------------------------------------------------------------------
    # 预约
    # ------------------------------------------------------------------

    @classmethod
    async def book(
        cls,
        db: AsyncSession,
        *,
        registration_id: int,
        schedule_id: int,
        slot_id: int,
        exam_date: date,
        vendor: str = "h3c",
    ) -> ExamBooking:
        """预约机位（FOR UPDATE 防超卖）。"""
        vendor = normalize_vendor(vendor)
        # 共享锁读取计划：允许多个预约并发，但阻塞管理员的关闭/改配额（FOR UPDATE）。
        schedule = await db.get(
            ExamSchedule, schedule_id, with_for_update={"read": True}
        )
        if schedule is None:
            raise NotFoundException("考场计划")
        if schedule.status != "published":
            raise BusinessException("该考场计划未开放")
        if schedule.vendor != vendor:
            raise BusinessException("该考场计划不属于当前认证厂商")

        # 校验日期
        if exam_date.weekday() not in schedule.allowed_weekdays:
            raise BusinessException("该日期不可预约")
        holidays = set(
            (await db.execute(select(Holiday.holiday_date))).scalars().all()
        )
        if exam_date in holidays:
            raise BusinessException("该日期为假期，不可预约")
        if exam_date < schedule.open_date_start or exam_date > schedule.open_date_end:
            raise BusinessException("该日期超出开放范围")

        # 检查重复预约（按厂商 + 报名维度）
        existing = await db.scalar(
            select(ExamBooking).where(
                ExamBooking.vendor == vendor,
                ExamBooking.registration_id == registration_id,
                ExamBooking.schedule_id == schedule_id,
            ).with_for_update()
        )
        if existing is not None and existing.status == "booked":
            raise ConflictException("您已预约该考场计划，请先取消再重新预约")
        if existing is not None and existing.status != "cancelled":
            raise ConflictException("该预约已完成或已缺考，不能重新预约")

        # 获取考生的科目（厂商无关解析）
        info = await resolve_registration(db, vendor, registration_id)
        if info is None:
            raise NotFoundException("报名记录")
        if not info.approved:
            raise BusinessException("报名尚未通过审核，暂不可预约")
        product = info.product
        if product is None:
            raise BusinessException("未找到考生的认证产品")

        subject = (await db.execute(
            select(ExamScheduleSubject)
            .where(
                ExamScheduleSubject.schedule_id == schedule_id,
                ExamScheduleSubject.product_id == product.id,
            )
            .limit(1)
        )).scalar_one_or_none()
        if subject is None:
            raise BusinessException(f"该考场计划不包含您的科目（{product.name}）")

        slot = await db.get(ExamTimeSlot, slot_id)
        if slot is None or slot.schedule_id != schedule_id:
            raise BusinessException("该时段不属于当前考场计划")

        if vendor != "sangfor":
            # 锁定时段共享池。预约仍记录实际科目，但容量不按科目拆分。
            quota = await cls._get_shared_quota(
                db, schedule_id, slot_id, for_update=True
            )

            # 检查机位
            if quota is not None and quota.total_seats > 0:
                booked = (await db.execute(
                    select(func.count()).select_from(ExamBooking).where(
                        ExamBooking.schedule_id == schedule_id,
                        ExamBooking.slot_id == slot_id,
                        ExamBooking.status == "booked",
                    )
                )).scalar_one()
                if booked >= quota.total_seats:
                    raise BusinessException("该时段机位已满")

        # 创建预约
        if existing is None:
            booking = ExamBooking(
                vendor=vendor,
                registration_id=registration_id,
                schedule_id=schedule_id,
                subject_id=subject.id,
                slot_id=slot_id,
                exam_date=exam_date,
            )
            db.add(booking)
        else:
            booking = existing
            booking.vendor = vendor
            booking.subject_id = subject.id
            booking.slot_id = slot_id
            booking.exam_date = exam_date
            booking.status = "booked"
        try:
            await db.flush()
        except IntegrityError as exc:
            raise ConflictException("您已预约该考场计划，请刷新后重试") from exc
        return booking

    # ------------------------------------------------------------------
    # 取消预约
    # ------------------------------------------------------------------

    CANCEL_DEADLINE_DAYS = 3  # 考前 3 天可取消

    @classmethod
    async def cancel(
        cls,
        db: AsyncSession,
        booking_id: int,
        registration_id: int,
        vendor: str = "h3c",
    ) -> ExamBooking:
        """取消预约（考前 N 天可取消）。"""
        vendor = normalize_vendor(vendor)
        booking = await db.scalar(
            select(ExamBooking).where(
                ExamBooking.id == booking_id,
                ExamBooking.vendor == vendor,
                ExamBooking.registration_id == registration_id,
            ).with_for_update()
        )
        if booking is None:
            raise NotFoundException("预约记录")
        if booking.status != "booked":
            raise BusinessException("该预约已取消或已完成")

        # 校验取消截止（考前 3 天）。日期边界以数据库时钟为准。
        today = (await db_now_utc(db)).date()
        deadline = booking.exam_date - timedelta(days=cls.CANCEL_DEADLINE_DAYS)
        if today > deadline:
            raise BusinessException(f"考前 {cls.CANCEL_DEADLINE_DAYS} 天内不可取消，请联系管理员")

        booking.status = "cancelled"
        await db.flush()
        return booking
