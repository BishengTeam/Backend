from datetime import date

from app.schemas.plan import PlanUpdate
from app.services.plan import PlanService
from app.services.holiday import holiday_matches
from app.domain.exam.src.index import Holiday


def test_plan_update_data_excludes_control_fields_and_keeps_explicit_null():
    data = PlanUpdate(confirm_holiday=False, exam_org_name=None, description="x")
    payload = PlanService.plan_update_data(data)

    assert "confirm_holiday" not in payload
    assert payload["exam_org_name"] is None
    assert payload["description"] == "x"


def test_annual_holiday_matches_same_month_day_in_other_years():
    annual = Holiday(name="国庆节", holiday_date=date(2026, 10, 1), is_annual=True)
    once = Holiday(name="单次", holiday_date=date(2026, 10, 1), is_annual=False)

    assert holiday_matches(annual, date(2027, 10, 1))
    assert not holiday_matches(annual, date(2027, 10, 2))
    assert not holiday_matches(once, date(2027, 10, 1))
    assert holiday_matches(once, date(2026, 10, 1))

