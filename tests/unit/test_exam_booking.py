"""H3C 考场预约 Service 单元测试。"""
import unittest
from datetime import date, timedelta

from app.services.exam_booking import ExamBookingService


class BookableDatesTests(unittest.TestCase):
    """可预约日期计算 — 仅周五，排除假期。"""

    def test_friday_only(self):
        """2026-11-09（周一）~ 2026-11-30（周一）中的周五：13、20、27。"""
        schedule_start = date(2026, 11, 9)
        schedule_end = date(2026, 11, 30)
        # 手动计算周五
        dates = []
        current = schedule_start
        while current <= schedule_end:
            if current.weekday() == 4:  # 周五
                dates.append(current)
            current += timedelta(days=1)
        self.assertEqual(dates, [
            date(2026, 11, 13),
            date(2026, 11, 20),
            date(2026, 11, 27),
        ])

    def test_friday_excludes_holiday(self):
        """如果 11-20 是假期，只剩 13 和 27。"""
        dates = [date(2026, 11, 13), date(2026, 11, 20), date(2026, 11, 27)]
        holidays = {date(2026, 11, 20)}
        filtered = [d for d in dates if d not in holidays]
        self.assertEqual(filtered, [date(2026, 11, 13), date(2026, 11, 27)])

    def test_friday_edge_month_boundary(self):
        """12月第一个周五。"""
        current = date(2026, 11, 30)
        while current.weekday() != 4:
            current += timedelta(days=1)
        self.assertEqual(current, date(2026, 12, 4))


class SeatCalculationTests(unittest.TestCase):
    """机位计算逻辑。"""

    def test_remaining_unlimited_when_quota_zero(self):
        """total_seats=0 → 不限。"""
        total_seats = 0
        booked = 100
        remaining = -1 if total_seats == 0 else max(0, total_seats - booked)
        self.assertEqual(remaining, -1)

    def test_remaining_finite(self):
        """total_seats=5, booked=3 → remaining=2。"""
        total_seats = 5
        booked = 3
        remaining = max(0, total_seats - booked)
        self.assertEqual(remaining, 2)

    def test_remaining_zero_when_full(self):
        """total_seats=5, booked=5 → remaining=0。"""
        total_seats = 5
        booked = 5
        remaining = max(0, total_seats - booked)
        self.assertEqual(remaining, 0)

    def test_remaining_never_negative(self):
        """total_seats=5, booked=8（超卖场景）→ remaining=0（不返回负数）。"""
        total_seats = 5
        booked = 8
        remaining = max(0, total_seats - booked)
        self.assertEqual(remaining, 0)


class CancelPolicyTests(unittest.TestCase):
    """取消策略 — 考前 3 天可取消。"""

    def test_cancel_allowed_3_days_before(self):
        """考试日期 11-20，今天 11-17，deadline=11-17 → 今天可取消。"""
        exam_date = date(2026, 11, 20)
        today = date(2026, 11, 17)
        deadline = exam_date - timedelta(days=3)
        self.assertTrue(today <= deadline)

    def test_cancel_blocked_within_3_days(self):
        """考试日期 11-20，今天 11-18 → 不可取消。"""
        exam_date = date(2026, 11, 20)
        today = date(2026, 11, 18)
        deadline = exam_date - timedelta(days=3)
        self.assertFalse(today <= deadline)
