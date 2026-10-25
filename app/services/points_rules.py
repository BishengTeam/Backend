"""积分规则 v2（2026-10-24 起）的单一事实来源。

- 每日答题打卡 +1 积分（金额沿用 CLAIM_SCENE_POINTS["daily_checkin"]）
- 累计签到满 7/15/30 天，各一次性 +5 积分（只统计生效日之后的新增打卡）
- 练习作答每满 100 次 +1 积分，不设上限，不满 100 次不计（只统计生效日之后的新增作答）
- 新用户注册不再赠送积分

「只算新增长」通过按生效时间过滤既有行数实现（打卡按业务时区自然日、
作答按 submitted_at），不建基线表，里程碑发放靠 points_history 幂等键去重。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

# 规则 v2 生效时间（业务时区）。此前的打卡/作答不计入里程碑与刷题积分。
POINTS_RULES_V2_STARTED_AT = datetime(2026, 10, 24, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

CHECKIN_MILESTONE_DAYS: tuple[int, ...] = (7, 15, 30)
CHECKIN_MILESTONE_POINTS = 5
QUIZ_ATTEMPTS_PER_POINT = 100
QUIZ_ATTEMPTS_MILESTONE_POINTS = 1


def checkin_milestone_hits(checkin_days: int) -> tuple[int, ...]:
    """返回已达标的签到里程碑档位；发放侧按 source_id 幂等去重，可重复结算。"""
    return tuple(threshold for threshold in CHECKIN_MILESTONE_DAYS if checkin_days >= threshold)


def quiz_attempt_milestone_hits(attempt_count: int) -> tuple[int, ...]:
    """返回已达标的刷题里程碑序号（每满 100 次、1 起连续编号）。"""
    return tuple(range(1, attempt_count // QUIZ_ATTEMPTS_PER_POINT + 1))
