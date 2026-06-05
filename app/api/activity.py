from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_user
from app.domain.user.src.index import User
from app.schemas.activity import (
    ActivityRegisterRequest,
    ActivityRegistrationResponse,
    ActivityReminderResponse,
    ActivityResponse,
)
from app.schemas.common import APIResponse, PaginatedData, success
from app.services.activity import ActivityService

router = APIRouter(prefix="/activities", tags=["活动"])


@router.get("", response_model=APIResponse[PaginatedData[ActivityResponse]])
async def list_activities(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
) -> APIResponse[PaginatedData[ActivityResponse]]:
    """活动列表（仅展示进行中且未结束的活动）"""
    result = await ActivityService().list_activities(
        page=page,
        page_size=page_size,
    )
    return success(data=result)


@router.post("/register", response_model=APIResponse[ActivityRegistrationResponse])
async def register_activity(
    body: ActivityRegisterRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[ActivityRegistrationResponse]:
    """活动报名"""
    result = await ActivityService().register(
        activity_id=body.activity_id,
        user_id=current_user.id,
        name=body.name,
        phone=body.phone,
        remark=body.remark,
    )
    return success(data=result)


@router.post("/{activity_id}/enroll", response_model=APIResponse[ActivityRegistrationResponse])
async def enroll_activity(
    activity_id: int = Path(..., ge=1, description="活动 ID"),
    body: ActivityRegisterRequest | None = None,
    current_user: User = Depends(get_current_user),
) -> APIResponse[ActivityRegistrationResponse]:
    """活动报名（enroll 别名，兼容旧前端路径 {id}/enroll）"""
    name = body.name if body else ""
    phone = body.phone if body else ""
    remark = body.remark if body else None
    result = await ActivityService().register(
        activity_id=activity_id,
        user_id=current_user.id,
        name=name,
        phone=phone,
        remark=remark,
    )
    return success(data=ActivityRegistrationResponse.model_validate(result))


@router.post("/{activity_id}/remind", response_model=APIResponse[ActivityReminderResponse])
async def remind_activity(
    activity_id: int = Path(..., ge=1, description="活动 ID"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[ActivityReminderResponse]:
    """设置活动提醒"""
    result = await ActivityService().set_reminder(current_user.id, activity_id)
    return success(data=ActivityReminderResponse.model_validate(result))