"""积分规则 v2（points_rules.py）纯函数单测。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.points_rules import (
    CHECKIN_MILESTONE_DAYS,
    CHECKIN_MILESTONE_POINTS,
    POINTS_RULES_V2_STARTED_AT,
    QUIZ_ATTEMPTS_MILESTONE_POINTS,
    QUIZ_ATTEMPTS_PER_POINT,
    checkin_milestone_hits,
    quiz_attempt_milestone_hits,
)


class TestPointsRulesUnit:
    def test_release_marker_is_timezone_aware_shanghai_midnight(self):
        assert POINTS_RULES_V2_STARTED_AT.tzinfo is not None
        assert POINTS_RULES_V2_STARTED_AT == datetime(
            2026, 10, 24, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")
        )

    def test_checkin_milestone_thresholds_and_amounts(self):
        assert CHECKIN_MILESTONE_DAYS == (7, 15, 30)
        assert CHECKIN_MILESTONE_POINTS == 5

    def test_checkin_milestone_hits_only_after_threshold(self):
        assert checkin_milestone_hits(0) == ()
        assert checkin_milestone_hits(6) == ()
        assert checkin_milestone_hits(7) == (7,)
        assert checkin_milestone_hits(14) == (7,)
        assert checkin_milestone_hits(15) == (7, 15)
        assert checkin_milestone_hits(29) == (7, 15)
        assert checkin_milestone_hits(30) == (7, 15, 30)
        assert checkin_milestone_hits(100) == (7, 15, 30)

    def test_quiz_attempt_milestones_per_hundred(self):
        assert QUIZ_ATTEMPTS_PER_POINT == 100
        assert QUIZ_ATTEMPTS_MILESTONE_POINTS == 1
        assert quiz_attempt_milestone_hits(0) == ()
        assert quiz_attempt_milestone_hits(99) == ()
        assert quiz_attempt_milestone_hits(100) == (1,)
        assert quiz_attempt_milestone_hits(199) == (1,)
        assert quiz_attempt_milestone_hits(250) == (1, 2)
