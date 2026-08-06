from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AdminLoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class AdminInfo(BaseModel):
    id: int
    username: str
    role: str

    model_config = {"from_attributes": True}

class DashboardResponse(BaseModel):
    """管理后台数据看板响应"""
    total_users: int
    total_orders: int
    recent_orders_30d: int
    paid_orders: int
    revenue_fen: int
    recent_revenue_30d_fen: int
    conversion_rate: float

    model_config = {"from_attributes": True}


ALL_PERMISSIONS = [
    "dashboard:view",
    "user:list",
    "user:write",
    "user:delete",
    "order:list",
    "order:write",
    "quiz:list",
    "quiz:write",
    "quiz:import",
    "content:list",
    "content:write",
    "content:banner",
    "course:list",
    "course:write",
]


class AdminLoginResponse(BaseModel):
    access_token: str
    expires_in: int
    admin: AdminInfo
    permissions: list[str] = ALL_PERMISSIONS


class AdminMeResponse(BaseModel):
    admin: AdminInfo
    permissions: list[str]


# ── User management ──


class AdminUserFilter(BaseModel):
    openid: str | None = None
    phone: str | None = None
    created_at_start: datetime | None = None
    created_at_end: datetime | None = None
    identity_status: str | None = None
    student_status: str | None = None


class AdminUserListItem(BaseModel):
    id: int
    openid: str
    phone: str | None = None
    is_active: bool
    created_at: datetime
    identity_status: str | None = None
    student_status: str | None = None

    model_config = {"from_attributes": True}

class AdminUserStatusToggle(BaseModel):
    is_active: bool


class AdminProfileUpdate(BaseModel):
    """管理端只可编辑非认证基础资料。

    实名和学生资料必须由用户提交，再通过专用人工审核接口处理；这里显式
    禁止额外字段，避免旧客户端绕过资料锁和审核状态机。
    """

    nickname: str | None = Field(None, max_length=64, description="昵称")
    email: str | None = Field(None, max_length=128, description="邮箱")
    phone: str | None = Field(None, min_length=11, max_length=11, description="手机号")
    province: str | None = Field(None, max_length=32, description="省份")
    city: str | None = Field(None, max_length=32, description="城市")
    address: str | None = Field(None, max_length=256, description="详细地址")

    model_config = {"extra": "forbid"}


class AdminIdentityReview(BaseModel):
    status: Literal["verified", "rejected"] = Field(
        ..., description="审核结果: verified / rejected"
    )
    comment: str | None = Field(None, max_length=256, description="审核备注")


class AdminOrderReview(BaseModel):
    action: Literal["approve", "reject_registration", "reject_and_refund"] = Field(
        ..., description="审核动作: approve / reject_registration / reject_and_refund"
    )
    comment: str | None = Field(None, max_length=256, description="审核备注（驳回时必填）")


class AdminUserOrderBrief(BaseModel):
    """管理端用户订单摘要（用户详情页子资源）"""
    id: int
    cert_type: str
    candidate_name: str
    price: int
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminUserConversationBrief(BaseModel):
    """管理端用户对话摘要（用户详情页子资源）"""
    id: int
    session_id: str
    title: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminBatchDeleteRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1)
