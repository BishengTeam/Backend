"""H3C 分时预约考场 — 机位判定/预约/取消 Service。"""
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.exam.src.index import (
    ExamBooking,
    ExamSchedule,
    ExamScheduleSubject,
    ExamSeatQuota,
    ExamTimeSlot,
    Holiday,
)
from app.port.exceptions import BusinessException, ConflictException, NotFoundException


class ExamBookingService:
    """机位管控核心逻辑。"""

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
    ) -> list[dict]:
        """获取某日期某科目的所有时段及剩余机位。"""
        schedule = await db.get(ExamSchedule, schedule_id)
        if schedule is None or schedule.status != "published":
            raise BusinessException("该考场计划未开放")

        # 校验日期合法性
        if exam_date.weekday() not in schedule.allowed_weekdays:
            raise BusinessException("该日期不可预约")

        slots = (await db.execute(
            select(ExamTimeSlot)
            .where(ExamTimeSlot.schedule_id == schedule_id)
            .order_by(ExamTimeSlot.sort_order, ExamTimeSlot.start_time)
        )).scalars().all()

        result = []
        for slot in slots:
            quota = await db.scalar(
                select(ExamSeatQuota).where(
                    ExamSeatQuota.schedule_id == schedule_id,
                    ExamSeatQuota.subject_id == subject_id,
                    ExamSeatQuota.slot_id == slot.id,
                )
            )
            if quota is None:
                remaining = -1  # 无配额记录 = 不限
            elif quota.total_seats == 0:
                remaining = -1  # 0 = 不限
            else:
                booked = (await db.execute(
                    select(func.count()).select_from(ExamBooking).where(
                        ExamBooking.schedule_id == schedule_id,
                        ExamBooking.subject_id == subject_id,
                        ExamBooking.slot_id == slot.id,
                        ExamBooking.exam_date == exam_date,
                        ExamBooking.status == "booked",
                    )
                )).scalar_one()
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
    ) -> ExamBooking:
        """预约机位（FOR UPDATE 防超卖）。"""
        schedule = await db.get(ExamSchedule, schedule_id, with_for_update=True)
        if schedule is None:
            raise NotFoundException("考场计划")
        if schedule.status != "published":
            raise BusinessException("该考场计划未开放")

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

        # 检查重复预约
        existing = await db.scalar(
            select(ExamBooking).where(
                ExamBooking.registration_id == registration_id,
                ExamBooking.schedule_id == schedule_id,
                ExamBooking.status == "booked",
            )
        )
        if existing is not None:
            raise ConflictException("您已预约该考场计划，请先取消再重新预约")

        # 获取考生的科目（通过 registration 关联）
        from app.domain.h3c.src.index import H3cRegistration
        registration = await db.get(H3cRegistration, registration_id)
        if registration is None:
            raise NotFoundException("报名记录")

        # v2 多产品：通过 batch_product_id 两跳直达产品
        from app.models.cert_product import CertProduct
        from app.domain.h3c.src.index import H3cExamBatch, H3cBatchProduct
        from app.domain.plan.src.index import Plan

        if registration.batch_product_id:
            # 两跳：registration → batch_product → cert_product
            _bp = await db.get(H3cBatchProduct, registration.batch_product_id)
            if _bp is None:
                raise BusinessException("报名记录缺少科目关联")
            product = await db.get(CertProduct, _bp.product_id)
            if product is None:
                raise BusinessException("未找到考生的认证产品")
        else:
            # 兼容旧链路：registration → batch → plan → product_type
            batch = await db.get(H3cExamBatch, registration.batch_id)
            if batch is None:
                raise BusinessException("报名记录缺少批次关联")
            plan = await db.get(Plan, batch.plan_id)
            if plan is None:
                raise BusinessException("批次缺少关联计划")
            product = await db.scalar(
                select(CertProduct).where(CertProduct.code == plan.product_type)
            )
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

        # 锁配额行
        quota = await db.scalar(
            select(ExamSeatQuota).where(
                ExamSeatQuota.schedule_id == schedule_id,
                ExamSeatQuota.subject_id == subject.id,
                ExamSeatQuota.slot_id == slot_id,
            ).with_for_update()
        )

        # 检查机位
        if quota is not None and quota.total_seats > 0:
            booked = (await db.execute(
                select(func.count()).select_from(ExamBooking).where(
                    ExamBooking.schedule_id == schedule_id,
                    ExamBooking.subject_id == subject.id,
                    ExamBooking.slot_id == slot_id,
                    ExamBooking.exam_date == exam_date,
                    ExamBooking.status == "booked",
                )
            )).scalar_one()
            if booked >= quota.total_seats:
                raise BusinessException("该时段机位已满")

        # 创建预约
        booking = ExamBooking(
            registration_id=registration_id,
            schedule_id=schedule_id,
            subject_id=subject.id,
            slot_id=slot_id,
            exam_date=exam_date,
        )
        db.add(booking)
        await db.flush()
        return booking

    # ------------------------------------------------------------------
    # 取消预约
    # ------------------------------------------------------------------

    CANCEL_DEADLINE_DAYS = 3  # 考前 3 天可取消

    @classmethod
    async def cancel(cls, db: AsyncSession, booking_id: int, registration_id: int) -> ExamBooking:
        """取消预约（考前 N 天可取消）。"""
        booking = await db.scalar(
            select(ExamBooking).where(
                ExamBooking.id == booking_id,
                ExamBooking.registration_id == registration_id,
            ).with_for_update()
        )
        if booking is None:
            raise NotFoundException("预约记录")
        if booking.status != "booked":
            raise BusinessException("该预约已取消或已完成")

        # 校验取消截止（考前 3 天）
        from datetime import datetime as dt
        today = dt.now().date()
        deadline = booking.exam_date - timedelta(days=cls.CANCEL_DEADLINE_DAYS)
        if today > deadline:
            raise BusinessException(f"考前 {cls.CANCEL_DEADLINE_DAYS} 天内不可取消，请联系管理员")

        booking.status = "cancelled"
        await db.flush()
        return booking
