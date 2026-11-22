from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.port.exceptions import BusinessException
from app.domain.exam.src.index import Holiday
from app.services.plan import PlanService


class FakeDb:
    def __init__(self, holiday):
        self.holiday = holiday

    async def execute(self, statement):
        holiday = self.holiday

        class Result:
            def scalars(self):
                return self

            def all(self):
                return [holiday] if holiday else []

        return Result()


async def test_renshe_exam_date_on_holiday_requires_confirmation():
    exam_date = datetime(2026, 10, 1, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    with pytest.raises(BusinessException, match="假期"):
        await PlanService._assert_not_holiday(
            FakeDb(Holiday(name="国庆节", holiday_date=exam_date.date(), is_annual=False)),
            exam_date,
        )


async def test_renshe_exam_date_without_holiday_passes():
    exam_date = datetime(2026, 10, 8, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert await PlanService._assert_not_holiday(FakeDb(None), exam_date) is None
