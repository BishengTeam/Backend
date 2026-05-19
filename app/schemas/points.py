from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


PointsClaimScene = Literal["daily_checkin", "quiz_task", "new_user", "activity"]
PointsRedeemType = Literal["exam_discount", "course"]


class PointsBalanceResponse(BaseModel):
    balance: int = Field(..., ge=0, description="积分余额")


class PointsHistoryResponse(BaseModel):
    id: int = Field(..., description="积分流水 ID")
    action_type: str = Field(..., description="积分动作类型")
    amount: int = Field(..., description="积分变动值，正数为获得，负数为消耗")
    balance_after: int = Field(..., ge=0, description="变动后积分余额")
    description: str | None = Field(None, description="流水说明")
    source_type: str | None = Field(None, description="幂等来源类型")
    source_id: str | None = Field(None, description="幂等来源 ID")
    created_at: datetime = Field(..., description="创建时间")

    model_config = {"from_attributes": True}


class PointsClaimRequest(BaseModel):
    scene: PointsClaimScene = Field(..., description="领取场景")
    source_id: str | None = Field(
        None,
        min_length=1,
        max_length=64,
        description="领取周期或活动 ID；为空时按场景自动生成",
    )
    description: str | None = Field(None, max_length=256, description="领取说明")


class PointsClaimResponse(BaseModel):
    claimed: bool = Field(..., description="本次请求是否实际发放积分")
    scene: PointsClaimScene = Field(..., description="领取场景")
    amount: int = Field(..., gt=0, description="领取积分数")
    balance: int = Field(..., ge=0, description="领取后积分余额")
    history_id: int = Field(..., description="积分流水 ID")


class PointsRedeemRequest(BaseModel):
    redeem_type: PointsRedeemType = Field(..., description="兑换类型")
    amount: int = Field(..., gt=0, description="消耗积分数")
    target_id: int | None = Field(None, ge=1, description="兑换目标 ID")
    description: str | None = Field(None, max_length=256, description="兑换说明")


class PointsRedeemResponse(BaseModel):
    redeem_type: PointsRedeemType = Field(..., description="兑换类型")
    amount: int = Field(..., gt=0, description="消耗积分数")
    balance: int = Field(..., ge=0, description="兑换后积分余额")
    history_id: int = Field(..., description="积分流水 ID")
