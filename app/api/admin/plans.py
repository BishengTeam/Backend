from typing import Literal

from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import require_permission, require_super_admin
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.order import OrderResponse, OrderStatus
from app.schemas.plan import (
    PlanCreate,
    PlanImpactAction,
    PlanImpactResponse,
    PlanResponse,
    PlanUpdate,
)
from app.schemas.review import ReviewResponse
from app.services.plan import PlanService
from app.services.plan_order_management import PlanOrderManagementService

router = APIRouter(tags=["管理后台-批次管理"])


@router.get("/{code}/plans",
    response_model=APIResponse[list[PlanResponse]],
    summary="某认证下的批次列表",
)
async def list_plans(
    code: str = Path(..., description="认证产品代码，如 H3C-RE"),
    _admin=Depends(require_permission("user:list")),
):
    """获取指定认证产品下的所有批次（含 enrolled 计数）"""
    result = await PlanService().list_plans(code)
    return success(data=result)


@router.get(
    "/{code}/plans/{plan_id}/orders",
    response_model=APIResponse[PaginatedData[OrderResponse]],
    summary="批次订单列表",
)
async def list_plan_orders(
    code: str = Path(..., description="认证产品代码"),
    plan_id: int = Path(..., ge=1, description="批次 ID"),
    status: OrderStatus | None = Query(None, description="按订单状态筛选"),
    phone: str | None = Query(None, description="按考生手机号筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("order:list")),
) -> APIResponse[PaginatedData[OrderResponse]]:
    result = await PlanOrderManagementService().list_orders(
        product_type=code,
        plan_id=plan_id,
        status=status,
        phone=phone,
        page=page,
        page_size=page_size,
    )
    return success(data=result)


@router.get(
    "/{code}/plans/{plan_id}/approvals",
    response_model=APIResponse[PaginatedData[ReviewResponse]],
    summary="批次审核记录",
)
async def list_plan_approvals(
    code: str = Path(..., description="认证产品代码"),
    plan_id: int = Path(..., ge=1, description="批次 ID"),
    action: Literal["approve", "reject"] | None = Query(
        None,
        description="按审核动作筛选",
    ),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    _admin=Depends(require_permission("order:list")),
) -> APIResponse[PaginatedData[ReviewResponse]]:
    result = await PlanOrderManagementService().list_approvals(
        product_type=code,
        plan_id=plan_id,
        action=action,
        page=page,
        page_size=page_size,
    )
    return success(data=result)


@router.post("/{code}/plans",
    response_model=APIResponse[PlanResponse],
    summary="创建批次",
)
async def create_plan(
    body: PlanCreate,
    code: str = Path(..., description="认证产品代码"),
    admin=Depends(require_permission("user:write")),
):
    result = await PlanService().create_plan(code, body, admin_id=admin.id)
    return success(data=result)


@router.put("/{code}/plans/{plan_id}",
    response_model=APIResponse[PlanResponse],
    summary="编辑批次",
)
async def update_plan(
    body: PlanUpdate,
    code: str = Path(..., description="认证产品代码"),
    plan_id: int = Path(..., description="批次 ID"),
    admin=Depends(require_permission("user:write")),
):
    result = await PlanService().update_plan(
        plan_id, body, product_type=code, admin_id=admin.id
    )
    return success(data=result)


@router.put("/{code}/plans/{plan_id}/publish",
    response_model=APIResponse[PlanResponse],
    summary="发布批次",
)
async def publish_plan(
    code: str = Path(..., description="认证产品代码"),
    plan_id: int = Path(..., description="批次 ID"),
    admin=Depends(require_permission("user:write")),
):
    result = await PlanService().publish_plan(
        plan_id, product_type=code, admin_id=admin.id
    )
    return success(data=result, message="批次已发布")


@router.put("/{code}/plans/{plan_id}/archive",
    response_model=APIResponse[PlanResponse],
    summary="归档批次",
)
async def archive_plan(
    code: str = Path(..., description="认证产品代码"),
    plan_id: int = Path(..., description="批次 ID"),
    admin=Depends(require_permission("user:write")),
):
    result = await PlanService().archive_plan(
        plan_id, product_type=code, admin_id=admin.id
    )
    return success(data=result, message="批次已归档")


@router.put("/{code}/plans/{plan_id}/cancel",
    response_model=APIResponse[PlanResponse],
    summary="取消批次",
)
async def cancel_plan(
    code: str = Path(..., description="认证产品代码"),
    plan_id: int = Path(..., description="批次 ID"),
    admin=Depends(require_permission("user:write")),
):
    result = await PlanService().cancel_plan(
        plan_id, product_type=code, admin_id=admin.id
    )
    return success(data=result, message="批次已取消")


@router.put(
    "/{code}/plans/{plan_id}/close-registration",
    response_model=APIResponse[PlanResponse],
    summary="关闭批次报名",
)
async def close_registration(
    code: str = Path(..., description="认证产品代码"),
    plan_id: int = Path(..., ge=1),
    admin=Depends(require_permission("user:write")),
):
    result = await PlanService().close_registration(
        plan_id, product_type=code, admin_id=admin.id
    )
    return success(data=result, message="批次报名已关闭")


@router.get(
    "/{code}/plans/{plan_id}/impact",
    response_model=APIResponse[PlanImpactResponse],
    summary="预览取消或终结人社批次的影响",
    description=(
        "只读统计，不锁定资源，也不替代执行取消或终结时的权限、状态和并发重校验；"
        "终结影响仅超级管理员可查看。"
    ),
)
async def preview_plan_impact(
    code: str = Path(..., description="认证产品代码，当前仅支持 RS-ZY"),
    plan_id: int = Path(..., ge=1, description="批次 ID"),
    action: PlanImpactAction = Query(..., description="危险操作：cancel 或 finalize"),
    admin=Depends(require_permission("user:write")),
) -> APIResponse[PlanImpactResponse]:
    if action == "finalize":
        await require_super_admin(admin)
    result = await PlanService().preview_impact(
        plan_id,
        product_type=code,
        action=action,
    )
    return success(data=result)


@router.put(
    "/{code}/plans/{plan_id}/finalize",
    response_model=APIResponse[PlanResponse],
    summary="终结人社批次（仅超级管理员）",
    description="待审核记录会阻止终结；驳回且已付款报名将逐名建立自动退款任务。",
)
async def finalize_plan(
    code: str = Path(..., description="认证产品代码"),
    plan_id: int = Path(..., ge=1),
    admin=Depends(require_super_admin),
):
    result = await PlanService().finalize_plan(
        plan_id, product_type=code, admin_id=admin.id
    )
    return success(data=result, message="批次已终结")


@router.delete("/{code}/plans/{plan_id}",
    response_model=APIResponse,
    summary="删除批次",
)
async def delete_plan(
    code: str = Path(..., description="认证产品代码"),
    plan_id: int = Path(..., description="批次 ID"),
    _admin=Depends(require_permission("user:write")),
):
    await PlanService().delete_plan(plan_id, product_type=code)
    return success(message="批次已删除")
