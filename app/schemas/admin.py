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
    enterprise_status: str | None = None


class AdminUserListItem(BaseModel):
    id: int
    openid: str
    phone: str | None = None
    is_active: bool
    created_at: datetime
    identity_status: str | None = None
    student_status: str | None = None
    enterprise_status: str | None = None

    model_config = {"from_attributes": True}

class AdminUserStatusToggle(BaseModel):
    is_active: bool


class AdminProfileUpdate(BaseModel):
    """管理端编辑用户资料 — 所有字段可选，管理员可直接修改任意数据，不触发审核"""
    nickname: str | None = Field(None, max_length=64, description="昵称")
    email: str | None = Field(None, max_length=128, description="邮箱")
    phone: str | None = Field(None, min_length=11, max_length=11, description="手机号")
    user_type: Literal["student", "enterprise"] | None = Field(None, description="用户类型")
    real_name: str | None = Field(None, max_length=64, description="真实姓名")
    id_card_number: str | None = Field(None, min_length=18, max_length=18, description="18 位身份证号")
    id_card_front_oss: str | None = Field(None, max_length=512, description="身份证人像面 OSS key")
    id_card_back_oss: str | None = Field(None, max_length=512, description="身份证国徽面 OSS key")
    education: str | None = Field(None, max_length=32, description="学历")
    school: str | None = Field(None, max_length=128, description="学校")
    major: str | None = Field(None, max_length=128, description="专业")
    student_card_oss: str | None = Field(None, max_length=512, description="学生证 OSS key")
    organization: str | None = Field(None, max_length=256, description="单位")
    # 审核状态（可直接设为 pending / verified / rejected）
    identity_status: Literal["pending", "verified", "rejected"] | None = Field(None, description="实名审核状态")
    student_status: Literal["pending", "verified", "rejected"] | None = Field(None, description="学生信息审核状态")
    enterprise_status: Literal["pending", "verified", "rejected"] | None = Field(None, description="企业信息审核状态")


class AdminIdentityReview(BaseModel):
    status: str = Field(..., description="审核结果: verified / rejected")
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
