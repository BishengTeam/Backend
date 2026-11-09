"""管理后台-认证批次管理 API v2。

挂载前缀: /admin/certifications
核心路由: /batches（全局批次 CRUD + 生命周期 + 产品/时段管理）
兼容路由: 旧 /{code}/plans 返回 410 Gone
"""
from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.database import get_db
from app.middleware.auth import require_permission
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.order import OrderResponse, OrderStatus
from app.schemas.plan import (
    BatchCreate,
    BatchListResponse,
    BatchResponse,
    BatchUpdate,
    PlanProductCreate,
    PlanProductUpdate,
    TimeSegmentCreate,
    TimeSegmentUpdate,
)
from app.services.plan import PlanService
from app.services.plan_order_management import PlanOrderManagementService

router = APIRouter(tags=["管理后台-认证批次管理"])


# ============================================================
# 批次 CRUD（全局）
# ============================================================

@router.get(
    "/batches",
    response_model=APIResponse[PaginatedData[BatchListResponse]],
    summary="批次列表",
)
async def list_batches(
    status_filter: str | None = Query(None, alias="status", description="按状态筛选"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _admin=Depends(require_permission("certification:list")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[PaginatedData[BatchListResponse]]:
    items, total = await PlanService.list_batches(
        db, status=status_filter, page=page, page_size=page_size
    )
    return success(data=PaginatedData(items=items, total=total, page=page, page_size=page_size))


@router.post(
    "/batches",
    response_model=APIResponse[BatchResponse],
    status_code=status.HTTP_201_CREATED,
    summary="创建批次",
)
async def create_batch(
    data: BatchCreate,
    _admin=Depends(require_permission("certification:manage")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[BatchResponse]:
    result = await PlanService.create_batch(db, data)
    return success(data=result)


@router.get(
    "/batches/{batch_id}",
    response_model=APIResponse[BatchResponse],
    summary="批次详情",
)
async def get_batch(
    batch_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("certification:list")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[BatchResponse]:
    result = await PlanService.get_batch(db, batch_id)
    return success(data=result)


@router.put(
    "/batches/{batch_id}",
    response_model=APIResponse[BatchResponse],
    summary="更新批次（仅草稿）",
)
async def update_batch(
    data: BatchUpdate,
    batch_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("certification:manage")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[BatchResponse]:
    result = await PlanService.update_batch(db, batch_id, data)
    return success(data=result)


@router.delete(
    "/batches/{batch_id}",
    response_model=APIResponse[None],
    summary="删除批次（仅草稿且无订单）",
)
async def delete_batch(
    batch_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("certification:manage")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[None]:
    await PlanService.delete_batch(db, batch_id)
    return success(message="批次已删除")


# ============================================================
# 生命周期
# ============================================================

@router.post("/batches/{batch_id}/publish", response_model=APIResponse[BatchResponse], summary="发布批次")
async def publish_batch(
    batch_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("certification:manage")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[BatchResponse]:
    result = await PlanService.publish(db, batch_id)
    return success(data=result, message="批次已发布")


@router.post("/batches/{batch_id}/close-registration", response_model=APIResponse[BatchResponse], summary="关闭报名")
async def close_registration(
    batch_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("certification:manage")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[BatchResponse]:
    result = await PlanService.close_registration(db, batch_id)
    return success(data=result, message="报名已关闭")


@router.post("/batches/{batch_id}/finalize", response_model=APIResponse[BatchResponse], summary="终审归档")
async def finalize_batch(
    batch_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("certification:manage")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[BatchResponse]:
    result = await PlanService.finalize(db, batch_id)
    return success(data=result, message="批次已终审")


@router.post("/batches/{batch_id}/cancel", response_model=APIResponse[BatchResponse], summary="取消批次")
async def cancel_batch(
    batch_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("certification:manage")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[BatchResponse]:
    result = await PlanService.cancel(db, batch_id)
    return success(data=result, message="批次已取消")


@router.post("/batches/{batch_id}/archive", response_model=APIResponse[BatchResponse], summary="归档批次")
async def archive_batch(
    batch_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("certification:manage")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[BatchResponse]:
    result = await PlanService.archive(db, batch_id)
    return success(data=result, message="批次已归档")


# ============================================================
# 批次内产品管理
# ============================================================

@router.post(
    "/batches/{batch_id}/products",
    response_model=APIResponse[BatchResponse],
    summary="添加产品到批次（仅草稿）",
)
async def add_product(
    data: PlanProductCreate,
    batch_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("certification:manage")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[BatchResponse]:
    result = await PlanService.add_product(db, batch_id, data)
    return success(data=result, message="产品已添加")


@router.put(
    "/batches/{batch_id}/products/{pp_id}",
    response_model=APIResponse[BatchResponse],
    summary="更新批次产品（仅草稿）",
)
async def update_product(
    data: PlanProductUpdate,
    batch_id: int = Path(..., ge=1),
    pp_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("certification:manage")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[BatchResponse]:
    result = await PlanService.update_product(db, batch_id, pp_id, data)
    return success(data=result, message="产品已更新")


@router.delete(
    "/batches/{batch_id}/products/{pp_id}",
    response_model=APIResponse[BatchResponse],
    summary="移除批次产品（仅草稿且无订单）",
)
async def remove_product(
    batch_id: int = Path(..., ge=1),
    pp_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("certification:manage")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[BatchResponse]:
    result = await PlanService.remove_product(db, batch_id, pp_id)
    return success(data=result, message="产品已移除")


# ============================================================
# 批次内时段管理
# ============================================================

@router.post(
    "/batches/{batch_id}/time-segments",
    response_model=APIResponse[BatchResponse],
    summary="添加考试时段（仅草稿）",
)
async def add_time_segment(
    data: TimeSegmentCreate,
    batch_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("certification:manage")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[BatchResponse]:
    result = await PlanService.add_time_segment(db, batch_id, data)
    return success(data=result, message="时段已添加")


@router.put(
    "/batches/{batch_id}/time-segments/{ts_id}",
    response_model=APIResponse[BatchResponse],
    summary="更新考试时段（仅草稿）",
)
async def update_time_segment(
    data: TimeSegmentUpdate,
    batch_id: int = Path(..., ge=1),
    ts_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("certification:manage")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[BatchResponse]:
    result = await PlanService.update_time_segment(db, batch_id, ts_id, data)
    return success(data=result, message="时段已更新")


@router.delete(
    "/batches/{batch_id}/time-segments/{ts_id}",
    response_model=APIResponse[BatchResponse],
    summary="删除考试时段（仅草稿）",
)
async def remove_time_segment(
    batch_id: int = Path(..., ge=1),
    ts_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("certification:manage")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[BatchResponse]:
    result = await PlanService.remove_time_segment(db, batch_id, ts_id)
    return success(data=result, message="时段已删除")


# ============================================================
# 批次订单查询（保留）
# ============================================================

@router.get(
    "/batches/{batch_id}/orders",
    response_model=APIResponse[PaginatedData[OrderResponse]],
    summary="批次订单列表",
)
async def list_batch_orders(
    batch_id: int = Path(..., ge=1),
    status_filter: OrderStatus | None = Query(None, alias="status"),
    phone: str | None = Query(None, description="按考生手机号筛选"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin=Depends(require_permission("order:list")),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[PaginatedData[OrderResponse]]:
    result = await PlanOrderManagementService().list_orders(
        plan_id=batch_id,
        status=status_filter,
        phone=phone,
        page=page,
        page_size=page_size,
    )
    return success(data=result)


# ============================================================
# 旧接口：410 Gone
# ============================================================

@router.get(
    "/{code}/plans",
    status_code=status.HTTP_410_GONE,
    summary="[已废弃] 按认证产品查批次列表",
)
async def list_plans_deprecated(
    code: str = Path(...),
    _admin=Depends(require_permission("certification:list")),
):
    from fastapi import HTTPException
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="此接口已废弃，请使用 GET /admin/certifications/batches",
    )
