from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ActivityResponse(BaseModel):
    id: int
    title: str
    description: str | None = None
    cover_url: str | None = None
    location: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    max_participants: int = 0
    is_active: bool = True
    related_cert_id: int | None = None
    related_course_id: int | None = None
    live_url: str | None = None
    group_qrcode_url: str | None = None
    registration_deadline: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ActivityRegisterRequest(BaseModel):
    activity_id: int = Field(..., ge=1, description="活动 ID")
    name: str = Field(..., min_length=1, max_length=64, description="报名姓名")
    phone: str = Field(..., min_length=1, max_length=20, description="联系电话")
    remark: str | None = Field(None, max_length=500, description="备注")


class ActivityRegistrationResponse(BaseModel):
    id: int
    activity_id: int
    user_id: int
    name: str
    phone: str
    remark: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ActivityReminderResponse(BaseModel):
    id: int
    activity_id: int
    user_id: int
    reminded: bool
    created_at: datetime

    model_config = {"from_attributes": True}
