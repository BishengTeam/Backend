"""管理后台-认证批次管理 API v2。

挂载前缀: /admin/certifications
核心路由: /batches（全局批次 CRUD + 生命周期 + 产品/时段管理）
兼容路由: 旧 /{code}/plans 返回 410 Gone
"""
from fastapi import APIRouter, Depends, Path, Query, Response, status
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
# F1: 批次整批导出（轻量只读汇总 XLSX）
# ============================================================

@router.get(
    "/batches/{batch_id}/export",
    summary="导出整批报名表（多 Sheet XLSX）",
    response_class=Response,
)
async def export_batch_registrations(
    batch_id: int = Path(..., ge=1),
    _admin=Depends(require_permission("certification:manage")),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """按厂商分 Sheet 导出整批所有产品的有效订单。"""
    from io import BytesIO
    from openpyxl import Workbook
    from app.domain.plan.src.index import Plan, PlanProduct
    from app.models.cert_product import CertProduct
    from app.domain.order.src.index import Order
    from sqlalchemy import select

    plan = await db.get(Plan, batch_id)
    if plan is None:
        from app.port.exceptions import NotFoundException
        raise NotFoundException("批次")

    rows = (await db.execute(
        select(Order, CertProduct)
        .join(PlanProduct, PlanProduct.plan_id == Order.plan_id)
        .join(CertProduct, CertProduct.id == PlanProduct.product_id)
        .where(
            Order.plan_id == batch_id,
            Order.product_type == CertProduct.code,
            Order.status.in_(("paid", "completed")),
        )
        .order_by(CertProduct.type, Order.id)
    )).all()

    if not rows:
        from app.port.exceptions import BusinessException
        raise BusinessException("该批次暂无有效订单可导出")

    # 按厂商分组
    by_vendor: dict[str, list] = {}
    for order, cert in rows:
        by_vendor.setdefault(cert.type, []).append((order, cert))

    vendor_labels = {"h3c": "H3C", "nisp": "NISP", "renshe": "人社", "sangfor": "深信服", "generic": "通用"}

    wb = Workbook()

    # Sheet 1: 汇总
    ws = wb.active
    ws.title = "汇总"
    ws.append(["厂商", "产品", "报名数", "总收入（分）"])
    revenue = {}
    for vendor_type, items in sorted(by_vendor.items()):
        for order, cert in items:
            key = (vendor_labels.get(vendor_type, vendor_type), cert.name)
            if key not in revenue:
                revenue[key] = {"count": 0, "total": 0}
            revenue[key]["count"] += 1
            revenue[key]["total"] += order.price
    for (vendor, product), data in sorted(revenue.items()):
        ws.append([vendor, product, data["count"], data["total"]])

    # Sheet 2+: 各厂商明细
    detail_headers = {
        "h3c": ["考生姓名", "手机号", "身份证号", "产品", "报名方式", "价格（分）", "订单状态", "下单时间"],
        "nisp": ["考生姓名", "手机号", "身份证号", "产品", "等级", "价格（分）", "订单状态", "下单时间"],
        "renshe": ["考生姓名", "手机号", "身份证号", "产品", "职业", "技能等级", "价格（分）", "订单状态", "下单时间"],
        "sangfor": ["考生姓名", "手机号", "身份证号", "产品", "报考方向", "价格（分）", "订单状态", "下单时间"],
        "generic": ["考生姓名", "手机号", "身份证号", "产品", "价格（分）", "订单状态", "下单时间"],
    }

    for vendor_type, items in sorted(by_vendor.items()):
        label = vendor_labels.get(vendor_type, vendor_type)
        ws_detail = wb.create_sheet(title=label)
        ws_detail.append(detail_headers.get(vendor_type, detail_headers["generic"]))

        for order, cert in items:
            extra = order.extra_data or {}
            base = [
                order.candidate_name or "",
                order.candidate_phone or "",
                order.candidate_idcard or "",
                cert.name,
 ]
            if vendor_type == "h3c":
                base.append(extra.get("registration_type", ""))
            elif vendor_type == "nisp":
                base.append(extra.get("level", ""))
            elif vendor_type == "renshe":
                _pp = None
                for _p in (await db.execute(
                    select(PlanProduct).where(
                        PlanProduct.plan_id == batch_id,
                        PlanProduct.product_id == cert.id,
                    ).limit(1)
                )).scalars():
                    _pp = _p
                base.append(getattr(_pp, "occupation_name", "") or "")
                base.append(getattr(_pp, "skill_level", "") or "")
            elif vendor_type == "sangfor":
                base.append(extra.get("exam_direction", "") or (extra.get("candidate_snapshot", {}) or {}).get("exam_direction", ""))

            base.extend([
                order.price,
                order.status,
                order.created_at.strftime("%Y-%m-%d %H:%M:%S") if order.created_at else "",
            ])
            ws_detail.append(base)

    # 自适应列宽
    for ws_x in wb.worksheets:
        for column in ws_x.columns:
            max_len = max(len(str(cell.value or "")) for cell in column)
            ws_x.column_dimensions[column[0].column_letter].width = min(max_len + 2, 50)

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    return Response(
        content=buffer.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="batch_{batch_id}_registrations.xlsx"'
        },
    )


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
