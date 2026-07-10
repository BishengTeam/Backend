from fastapi import APIRouter, Depends, Path

from app.middleware.auth import require_permission
from app.schemas.common import APIResponse, success
from app.schemas.plan import PlanCreate, PlanUpdate, PlanResponse
from app.services.plan import PlanService

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


@router.post("/{code}/plans",
    response_model=APIResponse[PlanResponse],
    summary="创建批次",
)
async def create_plan(
    body: PlanCreate,
    code: str = Path(..., description="认证产品代码"),
    _admin=Depends(require_permission("user:write")),
):
    result = await PlanService().create_plan(code, body)
    return success(data=result)


@router.put("/{code}/plans/{plan_id}",
    response_model=APIResponse[PlanResponse],
    summary="编辑批次",
)
async def update_plan(
    body: PlanUpdate,
    code: str = Path(..., description="认证产品代码"),
    plan_id: int = Path(..., description="批次 ID"),
    _admin=Depends(require_permission("user:write")),
):
    result = await PlanService().update_plan(plan_id, body)
    return success(data=result)


@router.put("/{code}/plans/{plan_id}/publish",
    response_model=APIResponse[PlanResponse],
    summary="发布批次",
)
async def publish_plan(
    code: str = Path(..., description="认证产品代码"),
    plan_id: int = Path(..., description="批次 ID"),
    _admin=Depends(require_permission("user:write")),
):
    result = await PlanService().publish_plan(plan_id)
    return success(data=result, message="批次已发布")


@router.put("/{code}/plans/{plan_id}/archive",
    response_model=APIResponse[PlanResponse],
    summary="归档批次",
)
async def archive_plan(
    code: str = Path(..., description="认证产品代码"),
    plan_id: int = Path(..., description="批次 ID"),
    _admin=Depends(require_permission("user:write")),
):
    result = await PlanService().archive_plan(plan_id)
    return success(data=result, message="批次已归档")


@router.put("/{code}/plans/{plan_id}/cancel",
    response_model=APIResponse[PlanResponse],
    summary="取消批次",
)
async def cancel_plan(
    code: str = Path(..., description="认证产品代码"),
    plan_id: int = Path(..., description="批次 ID"),
    _admin=Depends(require_permission("user:write")),
):
    result = await PlanService().cancel_plan(plan_id)
    return success(data=result, message="批次已取消")


@router.delete("/{code}/plans/{plan_id}",
    response_model=APIResponse,
    summary="删除批次",
)
async def delete_plan(
    code: str = Path(..., description="认证产品代码"),
    plan_id: int = Path(..., description="批次 ID"),
    _admin=Depends(require_permission("user:write")),
):
    await PlanService().delete_plan(plan_id)
    return success(message="批次已删除")
