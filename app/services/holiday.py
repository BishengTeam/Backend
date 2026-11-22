"""假期匹配规则：单次按完整日期，每年重复按月和日。"""
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.exam.src.index import Holiday


def holiday_matches(holiday: Holiday, target: date) -> bool:
    if holiday.holiday_date == target:
        return True
    return bool(holiday.is_annual) and (
        holiday.holiday_date.month,
        holiday.holiday_date.day,
    ) == (target.month, target.day)


async def holiday_for_date(
    db: AsyncSession, target: date
) -> Holiday | None:
    rows = (await db.execute(select(Holiday))).scalars().all()
    return next((item for item in rows if holiday_matches(item, target)), None)
